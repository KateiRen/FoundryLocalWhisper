using System.Drawing;
using System.Text.Json;
using System.Windows.Forms;
using Microsoft.AI.Foundry.Local;
using Microsoft.Extensions.Logging.Abstractions;

namespace FoundryLocalWhisper;

/// <summary>
/// C# port of transcribe.py's FoundryTranscribeTrayApp: global push-to-talk hotkey,
/// 16 kHz mono capture, local Foundry inference, clipboard output, tray controls.
/// </summary>
internal sealed class TrayApp : IDisposable
{
    private const int MinHoldMs = 120;
    private const double MaxRecordSeconds = 30.0;

    private static readonly string ScriptDir = AppContext.BaseDirectory;
    private static readonly string HistoryPath = Path.Combine(ScriptDir, "transcription_history.jsonl");
    private static readonly string TranscriptLogPath = Path.Combine(ScriptDir, "transcribe.log");
    private static readonly string AppLogPath = Path.Combine(ScriptDir, "transcribe_app.log");
    private static readonly string ConfigPath = Path.Combine(ScriptDir, "transcribe_config.json");

    private readonly AppConfig _config;
    private readonly AudioRecorder _recorder = new();
    private readonly object _lock = new();
    private readonly NativeMethods.LowLevelKeyboardProc _hookProc;

    private nint _hookHandle;
    private bool _ctrlHeld;
    private bool _winHeld;
    private DateTime _bothHeldSince;
    private bool _hookEnabled = true;
    private bool _recording;
    private bool _shuttingDown;
    private bool _modelSwitchInProgress;
    private string? _lastError;

    private FoundryLocalManager? _manager;
    private ICatalog? _catalog;
    private IModel? _speechModel;
    private OpenAIAudioClient? _audioClient;
    private int? _inputDevice;
    private string _modelName = "whisper-tiny";
    private readonly List<string> _availableModels = new();

    private NotifyIcon? _trayIcon;
    private ContextMenuStrip? _menu;
    private readonly string? _cliModelOverride;
    private readonly int? _cliMicOverride;
    private readonly DeviceType? _executionProviderOverride;
    public bool AutoPaste { get; set; } = true;

    public TrayApp(string? modelOverride, int? micOverride, bool autoPaste, string? executionProvider = null)
    {
        _cliModelOverride = modelOverride;
        _cliMicOverride = micOverride;
        AutoPaste = autoPaste;
        _executionProviderOverride = executionProvider is not null && Enum.TryParse<DeviceType>(executionProvider, true, out var parsed)
            ? parsed
            : null;
        _config = AppConfig.Load(ConfigPath);
        _hookProc = HookCallback;
    }

    private void ResolveModelName()
    {
        if (_cliModelOverride is not null)
        {
            _modelName = _cliModelOverride;
            Log.Info($"Using CLI model override: {_modelName}");
            return;
        }

        if (!string.IsNullOrWhiteSpace(_config.ModelName))
        {
            _modelName = _config.ModelName!.Trim();
            Log.Info($"Using configured model: {_modelName}");
        }
    }

    private void ResolveInputDevice()
    {
        if (_cliMicOverride is not null)
        {
            _inputDevice = _cliMicOverride;
            Log.Info($"Using CLI mic override: {_inputDevice}");
            return;
        }

        if (_config.MicIndex is int configured && configured < AudioRecorder.ListInputDevices().Count)
        {
            _inputDevice = configured;
            Log.Info($"Using configured mic: {_inputDevice}");
            return;
        }

        _inputDevice = 0;
        _config.MicIndex = 0;
        _config.Save(ConfigPath);
    }

    private async Task InitializeFoundryAsync(bool discoverModels = true)
    {
        Log.Info("Initializing Foundry Local SDK");
        Timing.Mark("foundry_init_start");

        var config = new Configuration
        {
            // Matches transcribe.py's app_name so both implementations share the same
            // Foundry Local model cache/EP registrations instead of downloading twice.
            AppName = "foundry_local_whisper_tray",
            LogLevel = Microsoft.AI.Foundry.Local.LogLevel.Information,
        };
        await FoundryLocalManager.CreateAsync(config, NullLogger.Instance);
        _manager = FoundryLocalManager.Instance;

        await _manager.DownloadAndRegisterEpsAsync((epName, percent) => Log.Info($"EP {epName} {percent:F1}%"));
        Timing.Mark("foundry_init_done");

        _catalog = await _manager.GetCatalogAsync();

        if (!discoverModels)
        {
            // Benchmarking showed ListModelsAsync() itself - not how many times or how
            // it's called - is the ~8-14s cost here (a one-time per-process catalog sync
            // in the native service). There's no tray menu to populate in one-shot
            // --transcribe-file mode, so skip it entirely instead of paying for it.
            Timing.Mark("model_discovery_done");
            await LoadSpeechModelAsync(_modelName);
            Log.Info($"Foundry model loaded: {_modelName}");
            return;
        }

        await DiscoverModelsAsync();
        Timing.Mark("model_discovery_done");

        if (!_availableModels.Contains(_modelName) && _availableModels.Count > 0)
        {
            Log.Warn($"Configured model '{_modelName}' not available, falling back to '{_availableModels[0]}'");
            _modelName = _availableModels[0];
        }

        await LoadSpeechModelAsync(_modelName);
        Log.Info($"Foundry model loaded: {_modelName}");
    }

    private async Task DiscoverModelsAsync()
    {
        if (_catalog is null)
        {
            _availableModels.Add(_modelName);
            return;
        }

        // GetModelAsync(alias) re-fetches the *entire* catalog from the native service on
        // every cache miss (mirrors foundry_local_sdk's Catalog._update_models self-heal),
        // so probing several hardcoded aliases one-by-one triggered a full re-fetch per
        // miss - that's what made discovery take 8-14s. Task.WhenAll didn't help, because
        // the catalog serializes those refetches internally. One ListModelsAsync() call
        // avoids the miss storm entirely.
        var models = await _catalog.ListModelsAsync();
        foreach (var model in models)
        {
            if (model.Alias is { } alias && alias.Contains("whisper", StringComparison.OrdinalIgnoreCase))
            {
                _availableModels.Add(alias);
            }
        }

        if (!_availableModels.Contains(_modelName))
        {
            _availableModels.Add(_modelName);
        }

        if (_availableModels.Count == 0)
        {
            _availableModels.Add(_modelName);
        }
    }

    private async Task LoadSpeechModelAsync(string modelName)
    {
        if (_catalog is null)
        {
            throw new InvalidOperationException("Foundry catalog not initialized");
        }

        Timing.Mark("model_load_start");
        var model = await _catalog.GetModelAsync(modelName)
            ?? throw new InvalidOperationException($"Foundry model not found: {modelName}");

        var variant = SelectVariant(model, modelName);
        model.SelectVariant(variant);

        await model.DownloadAsync(progress => Log.Info($"Downloading {modelName} {progress:F1}%"));
        await model.LoadAsync();

        _speechModel = model;
        _audioClient = await model.GetAudioClientAsync();
        _audioClient.Settings.Language = "en";
        _modelName = modelName;
        Timing.Mark("model_load_done");

        var runtime = variant.Info.Runtime;
        if (runtime is not null)
        {
            Log.Info($"Model '{modelName}' running on {runtime.DeviceType} ({runtime.ExecutionProvider})");
        }
    }

    private IModel SelectVariant(IModel model, string modelName)
    {
        if (_executionProviderOverride is { } wanted)
        {
            var match = model.Variants.FirstOrDefault(v => v.Info.Runtime?.DeviceType == wanted);
            if (match is not null)
            {
                return match;
            }
            Log.Warn($"No '{wanted}' variant available for '{modelName}'; using the default variant");
        }

        return model.Variants.FirstOrDefault(v => v.Info.Runtime?.DeviceType is not null)
            ?? model.Variants.First();
    }

    private void StartRecording()
    {
        if (_recording)
        {
            return;
        }

        _recording = true;
        RefreshIcon();
        NativeMethods.BeepAsync(600, 80);

        try
        {
            _recorder.Start(_inputDevice);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException)
        {
            _recording = false;
            NotifyError($"Microphone unavailable: {ex.Message}");
            return;
        }

        Log.Info("Recording started (hold Ctrl+Win)");
    }

    private void StopRecording()
    {
        if (!_recording)
        {
            return;
        }

        _recording = false;
        RefreshIcon();
        NativeMethods.BeepAsync(900, 90);

        var samples = _recorder.Stop();
        Timing.Mark("recording_stopped");

        if (samples.Length == 0)
        {
            Log.Info("No audio captured");
            return;
        }

        if (_shuttingDown)
        {
            Log.Info("Discarding in-progress recording (shutting down)");
            return;
        }

        var durationS = samples.Length / (double)AudioRecorder.SampleRate;
        Log.Info($"Captured {durationS:F2}s audio");

        _ = Task.Run(() => TranscribeAsync(samples, durationS));
    }

    private async Task TranscribeAsync(short[] samples, double durationS)
    {
        var ts = DateTimeOffset.UtcNow.ToString("O");
        var start = DateTime.UtcNow;

        try
        {
            var text = await TranscribeSamplesAsync(samples);
            Timing.Mark("transcription_done");
            var elapsed = (DateTime.UtcNow - start).TotalSeconds;

            if (!string.IsNullOrWhiteSpace(text))
            {
                Log.Info($"Transcription: {text}");
                AppendTranscriptLog(ts, text);
                NativeMethods.SetClipboardText(text);
                Timing.Mark("clipboard_set");
                if (AutoPaste && !_shuttingDown)
                {
                    NativeMethods.SimulateCtrlV();
                    Timing.Mark("paste_simulated");
                }
                NativeMethods.BeepAsync(1200, 80);
                ClearError();
                AppendHistory(ts, text, durationS, elapsed, "ok", null);
            }
            else
            {
                Log.Info("No speech detected");
                NativeMethods.BeepAsync(420, 200);
                AppendHistory(ts, "", durationS, elapsed, "empty", null);
            }
        }
        catch (Exception ex)
        {
            var elapsed = (DateTime.UtcNow - start).TotalSeconds;
            Log.Error($"Transcription failed: {ex.Message}");
            NotifyError($"Transcription failed: {ex.Message}");
            AppendHistory(ts, "", durationS, elapsed, "error", ex.Message);
        }
    }

    private async Task<string> TranscribeSamplesAsync(short[] samples)
    {
        if (_audioClient is null)
        {
            throw new InvalidOperationException("Foundry audio client not initialized");
        }

        var tmpPath = Path.Combine(Path.GetTempPath(), $"fwt_{Guid.NewGuid():N}.wav");
        try
        {
            WavIo.WritePcm16Mono(tmpPath, samples);
            var builder = new System.Text.StringBuilder();
            await foreach (var chunk in _audioClient.TranscribeAudioStreamingAsync(tmpPath, CancellationToken.None))
            {
                builder.Append(chunk.Text);
            }
            return builder.ToString().Trim();
        }
        finally
        {
            try { File.Delete(tmpPath); } catch (IOException) { /* best-effort cleanup */ }
        }
    }

    private static void AppendTranscriptLog(string ts, string text)
    {
        try
        {
            File.AppendAllText(TranscriptLogPath, $"{ts}\t{text}\n");
        }
        catch (IOException ex)
        {
            Log.Error($"Failed writing transcript log: {ex.Message}");
        }
    }

    private static void AppendHistory(string ts, string text, double audioS, double latencyS, string status, string? error)
    {
        try
        {
            var entry = new Dictionary<string, object?>
            {
                ["ts"] = ts,
                ["text"] = text,
                ["audio_s"] = Math.Round(audioS, 2),
                ["latency_s"] = Math.Round(latencyS, 2),
                ["status"] = status,
            };
            if (error is not null)
            {
                entry["error"] = error;
            }
            File.AppendAllText(HistoryPath, JsonSerializer.Serialize(entry) + "\n");
        }
        catch (IOException ex)
        {
            Log.Error($"Failed writing transcription history: {ex.Message}");
        }
    }

    private void RefreshIcon()
    {
        if (_trayIcon is null)
        {
            return;
        }

        _trayIcon.Icon = LoadIcon(_recording);
        _trayIcon.Text = _recording
            ? "Foundry Transcribe | Recording..."
            : _lastError is not null
                ? $"Foundry Transcribe | Error: {_lastError}"[..Math.Min(127, 22 + (_lastError?.Length ?? 0))]
                : !_hookEnabled
                    ? "Foundry Transcribe | Paused"
                    : "Foundry Transcribe | Hold Ctrl+Win to dictate";
    }

    private static Icon LoadIcon(bool recording)
    {
        var name = recording ? "mic_rec.png" : "mic.png";
        var path = Path.Combine(ScriptDir, "assets", name);
        if (File.Exists(path))
        {
            using var bitmap = new Bitmap(path);
            return Icon.FromHandle(bitmap.GetHicon());
        }
        return SystemIcons.Application;
    }

    private void NotifyError(string message)
    {
        Log.Error(message);
        _lastError = message;
        NativeMethods.BeepAsync(300, 250);
        RefreshIcon();
        RebuildMenu();
    }

    private void ClearError()
    {
        if (_lastError is null)
        {
            return;
        }
        _lastError = null;
        RefreshIcon();
        RebuildMenu();
    }

    private bool IsCtrl(uint vk) => vk is NativeMethods.VK_LCONTROL or NativeMethods.VK_RCONTROL;
    private bool IsWin(uint vk) => vk is NativeMethods.VK_LWIN or NativeMethods.VK_RWIN;

    private bool OnKeyEvent(uint vk, bool isDown)
    {
        if (vk == NativeMethods.VK_Q)
        {
            var ctrlDown = NativeMethods.IsKeyDown(NativeMethods.VK_LCONTROL) || NativeMethods.IsKeyDown(NativeMethods.VK_RCONTROL);
            var altDown = NativeMethods.IsKeyDown(NativeMethods.VK_LMENU) || NativeMethods.IsKeyDown(NativeMethods.VK_RMENU);
            if (isDown && ctrlDown && altDown)
            {
                RequestQuit();
                return true;
            }
            return false;
        }

        var previousCtrl = _ctrlHeld;
        var previousWin = _winHeld;

        if (IsCtrl(vk))
        {
            _ctrlHeld = isDown;
            _winHeld = NativeMethods.IsKeyDown(NativeMethods.VK_LWIN) || NativeMethods.IsKeyDown(NativeMethods.VK_RWIN);
        }

        if (IsWin(vk))
        {
            _winHeld = isDown;
            _ctrlHeld = NativeMethods.IsKeyDown(NativeMethods.VK_LCONTROL) || NativeMethods.IsKeyDown(NativeMethods.VK_RCONTROL);
        }

        var changed = _ctrlHeld != previousCtrl || _winHeld != previousWin;
        if (!changed)
        {
            return false;
        }

        var bothHeld = _ctrlHeld && _winHeld;

        if (_hookEnabled)
        {
            lock (_lock)
            {
                if (bothHeld && !_recording)
                {
                    _bothHeldSince = DateTime.UtcNow;
                    StartRecording();
                }
                else if (!bothHeld && _recording)
                {
                    var holdMs = (DateTime.UtcNow - _bothHeldSince).TotalMilliseconds;
                    if (holdMs < MinHoldMs)
                    {
                        _recording = false;
                        _recorder.Stop();
                        RefreshIcon();
                        Log.Info($"Tap too short ({holdMs:F0}ms), discarded");
                    }
                    else
                    {
                        StopRecording();
                    }
                }
            }
        }

        return IsWin(vk) && isDown && _ctrlHeld;
    }

    private nint HookCallback(int nCode, nint wParam, nint lParam)
    {
        if (nCode >= 0)
        {
            var kb = System.Runtime.InteropServices.Marshal.PtrToStructure<NativeMethods.KBDLLHOOKSTRUCT>(lParam);
            var vk = kb.vkCode;
            var isDown = wParam == NativeMethods.WM_KEYDOWN || wParam == NativeMethods.WM_SYSKEYDOWN;
            if (IsCtrl(vk) || IsWin(vk) || vk == NativeMethods.VK_Q)
            {
                if (OnKeyEvent(vk, isDown))
                {
                    return 1;
                }
            }
        }

        return NativeMethods.CallNextHookEx(_hookHandle, nCode, wParam, lParam);
    }

    private void InstallHook()
    {
        using var curProcess = System.Diagnostics.Process.GetCurrentProcess();
        using var curModule = curProcess.MainModule!;
        _hookHandle = NativeMethods.SetWindowsHookEx(
            NativeMethods.WH_KEYBOARD_LL, _hookProc,
            System.Runtime.InteropServices.Marshal.GetHINSTANCE(typeof(TrayApp).Module),
            0);

        if (_hookHandle == 0)
        {
            NotifyError("Keyboard hook could not be installed - the Ctrl+Win hotkey is unavailable");
            return;
        }

        Log.Info("Keyboard hook installed");
    }

    private void RebuildMenu()
    {
        if (_trayIcon is null)
        {
            return;
        }
        _menu = BuildMenu();
        _trayIcon.ContextMenuStrip = _menu;
    }

    private ContextMenuStrip BuildMenu()
    {
        var menu = new ContextMenuStrip();

        menu.Items.Add(new ToolStripMenuItem(_hookEnabled ? "Active (Hold Ctrl+Win)" : "Paused") { Enabled = false });
        var toggle = new ToolStripMenuItem(_hookEnabled ? "Pause Dictation" : "Resume Dictation");
        toggle.Click += (_, _) => ToggleHook();
        menu.Items.Add(toggle);
        menu.Items.Add(new ToolStripSeparator());

        if (_lastError is not null)
        {
            var errItem = new ToolStripMenuItem($"Last error: {_lastError}"[..Math.Min(80, 12 + _lastError.Length)]);
            errItem.Click += (_, _) => OpenAppLog();
            menu.Items.Add(errItem);
            var dismiss = new ToolStripMenuItem("Dismiss error");
            dismiss.Click += (_, _) => ClearError();
            menu.Items.Add(dismiss);
            menu.Items.Add(new ToolStripSeparator());
        }

        menu.Items.Add(new ToolStripMenuItem($"Model: {_modelName}") { Enabled = false });
        var modelMenu = new ToolStripMenuItem("Whisper model");
        foreach (var name in _availableModels)
        {
            var item = new ToolStripMenuItem(name) { Checked = name == _modelName, Enabled = !_modelSwitchInProgress };
            item.Click += (_, _) => _ = SwitchModelAsync(name);
            modelMenu.DropDownItems.Add(item);
        }
        menu.Items.Add(modelMenu);
        menu.Items.Add(new ToolStripSeparator());

        var micLabel = _inputDevice is null ? "Mic: system default" : $"Mic: [{_inputDevice}]";
        menu.Items.Add(new ToolStripMenuItem(micLabel) { Enabled = false });
        var micMenu = new ToolStripMenuItem("Microphone");
        foreach (var (index, name) in AudioRecorder.ListInputDevices())
        {
            var item = new ToolStripMenuItem($"[{index}] {name}") { Checked = _inputDevice == index, Enabled = !_recording };
            item.Click += (_, _) => SelectMic(index);
            micMenu.DropDownItems.Add(item);
        }
        menu.Items.Add(micMenu);
        menu.Items.Add(new ToolStripSeparator());

        var autoPasteItem = new ToolStripMenuItem("Auto-paste at cursor") { Checked = AutoPaste };
        autoPasteItem.Click += (_, _) => { AutoPaste = !AutoPaste; Log.Info($"Auto-paste {(AutoPaste ? "on" : "off")}"); RebuildMenu(); };
        menu.Items.Add(autoPasteItem);

        var historyItem = new ToolStripMenuItem("Open History");
        historyItem.Click += (_, _) => OpenIfExists(HistoryPath);
        menu.Items.Add(historyItem);

        var logItem = new ToolStripMenuItem("Open App Log");
        logItem.Click += (_, _) => OpenAppLog();
        menu.Items.Add(logItem);

        menu.Items.Add(new ToolStripSeparator());
        var quitItem = new ToolStripMenuItem("Quit (Ctrl+Alt+Q)");
        quitItem.Click += (_, _) => RequestQuit();
        menu.Items.Add(quitItem);

        return menu;
    }

    private static void OpenIfExists(string path)
    {
        if (File.Exists(path))
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
        }
    }

    private void OpenAppLog() => OpenIfExists(AppLogPath);

    private void ToggleHook()
    {
        _hookEnabled = !_hookEnabled;
        if (!_hookEnabled && _recording)
        {
            lock (_lock) { StopRecording(); }
        }
        Log.Info($"Dictation {(_hookEnabled ? "enabled" : "disabled")}");
        RefreshIcon();
        RebuildMenu();
    }

    private void SelectMic(int deviceIndex)
    {
        if (deviceIndex == _inputDevice || _recording)
        {
            return;
        }

        _inputDevice = deviceIndex;
        _config.MicIndex = deviceIndex;
        _config.Save(ConfigPath);
        Log.Info($"Switched microphone to: {deviceIndex}");
        RebuildMenu();
    }

    private async Task SwitchModelAsync(string modelName)
    {
        if (modelName == _modelName || _modelSwitchInProgress)
        {
            return;
        }

        _modelSwitchInProgress = true;
        RebuildMenu();

        var oldModel = _modelName;
        try
        {
            lock (_lock)
            {
                if (_recording)
                {
                    Log.Info("Cannot switch models while recording");
                    return;
                }
            }

            if (_speechModel is not null)
            {
                await _speechModel.UnloadAsync();
            }

            await LoadSpeechModelAsync(modelName);
            _config.ModelName = _modelName;
            _config.Save(ConfigPath);
            Log.Info($"Switched to model: {_modelName}");
            NativeMethods.BeepAsync(1100, 80);
            ClearError();
        }
        catch (Exception ex)
        {
            Log.Error($"Model switch failed: {ex.Message}");
            try
            {
                await LoadSpeechModelAsync(oldModel);
                NotifyError($"Could not load '{modelName}', kept '{oldModel}': {ex.Message}");
            }
            catch (Exception restoreEx)
            {
                Log.Error($"Failed to reload previous model: {restoreEx.Message}");
                NotifyError($"Model switch failed and '{oldModel}' could not be restored - restart the app");
            }
        }
        finally
        {
            _modelSwitchInProgress = false;
            RebuildMenu();
        }
    }

    public void RequestQuit()
    {
        if (_shuttingDown)
        {
            return;
        }
        Task.Run(Quit);
    }

    private void Quit()
    {
        if (_shuttingDown)
        {
            return;
        }
        Log.Info("Shutting down");
        _hookEnabled = false;
        _shuttingDown = true;

        lock (_lock)
        {
            if (_recording)
            {
                StopRecording();
            }
        }

        if (_hookHandle != 0)
        {
            NativeMethods.UnhookWindowsHookEx(_hookHandle);
            _hookHandle = 0;
        }

        _speechModel?.UnloadAsync().GetAwaiter().GetResult();

        if (_trayIcon is not null)
        {
            _trayIcon.Visible = false;
        }
        Application.Exit();
    }

    public async Task RunAsync()
    {
        Timing.Mark("process_start");
        ResolveModelName();
        ResolveInputDevice();
        await InitializeFoundryAsync();

        InstallHook();

        _trayIcon = new NotifyIcon
        {
            Icon = LoadIcon(false),
            Text = "Foundry Transcribe | Hold Ctrl+Win to dictate",
            Visible = true,
        };
        RebuildMenu();

        Log.Info("Ready. Hold Ctrl+Win to dictate. Hold Ctrl+Alt+Q to quit.");
        Timing.Mark("ready");
        Application.Run();
    }

    /// <summary>Non-interactive mode used both for CLI file transcription and the benchmark harness.</summary>
    public async Task<string> TranscribeFileAsync(string inputPath, string outputPath)
    {
        if (!File.Exists(inputPath))
        {
            throw new FileNotFoundException("Input audio file not found", inputPath);
        }

        Timing.Mark("process_start");
        ResolveModelName();
        await InitializeFoundryAsync(discoverModels: false);

        Log.Info($"Transcribing file: {inputPath}");
        var (samples, _) = WavIo.ReadPcm16Mono(inputPath);
        var durationS = samples.Length / (double)AudioRecorder.SampleRate;

        Timing.Mark("transcription_start");
        var text = await TranscribeSamplesAsync(samples);
        Timing.Mark("transcription_done");

        File.WriteAllText(outputPath, text);
        Console.WriteLine(text);

        // Mirror the interactive pipeline's post-processing cost even in file mode,
        // so stop-to-paste latency is comparable across implementations.
        NativeMethods.SetClipboardText(text);
        Timing.Mark("clipboard_set");
        NativeMethods.SimulateCtrlV();
        Timing.Mark("paste_simulated");

        var wordCount = text.Split(' ', StringSplitOptions.RemoveEmptyEntries).Length;
        Log.Info($"Transcribed {wordCount} words for {durationS:F1}s of audio");

        return text;
    }

    public void Dispose()
    {
        _recorder.Dispose();
        _trayIcon?.Dispose();
        _menu?.Dispose();
    }
}
