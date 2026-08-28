# Foundry Local Whisper (C#)

A C# prototype of [transcribe.py](../../transcribe.py), built to compare interaction
latency against the Python/PySide implementation using the same push-to-talk workflow:
a global `Ctrl+Win` hotkey, 16 kHz mono microphone capture, local Foundry Local
inference, clipboard output with optional auto-paste, a tray icon with menu, and
JSON config persistence.

## Prerequisites

- Windows 10/11 (x64 or ARM64)
- [.NET 9 SDK](https://dotnet.microsoft.com/download)
- [Foundry Local](https://learn.microsoft.com/azure/foundry-local/) installed and reachable
  on the machine (the `Microsoft.AI.Foundry.Local` NuGet package talks to the local service)

NuGet dependencies are pinned (`Microsoft.AI.Foundry.Local` 1.2.4, `NAudio` 2.2.1,
`Microsoft.Extensions.Logging.Abstractions` 9.0.15) so the published latency numbers stay
reproducible.

## Build

```powershell
cd csharp/FoundryLocalWhisper
dotnet restore
dotnet build -c Release
```

## Run

```powershell
dotnet run -c Release
```

CLI options mirror the Python app:

```powershell
dotnet run -c Release -- --model-name whisper-small
dotnet run -c Release -- --mic-index 3
dotnet run -c Release -- --no-auto-paste
dotnet run -c Release -- --transcribe-file input.wav --output-file output.txt
```

## What's implemented

- Global low-level keyboard hook (`WH_KEYBOARD_LL`) for the `Ctrl+Win` push-to-talk
  gesture and `Ctrl+Alt+Q` quit shortcut, matching [NativeMethods.cs](NativeMethods.cs).
- 16 kHz mono capture via NAudio (`AudioRecorder.cs`).
- Foundry Local model discovery, download, load, and streaming transcription
  (`TrayApp.cs`, using `Microsoft.AI.Foundry.Local`).
- Clipboard write + simulated `Ctrl+V` paste (`NativeMethods.SetClipboardText` /
  `SimulateCtrlV`).
- Tray icon and menu (model switch, mic switch, auto-paste toggle, open history/log, quit)
  via `System.Windows.Forms.NotifyIcon`.
- Config persistence to the same `transcribe_config.json` shape as the Python app.
- Rotating file + console logging (`Log.cs`) and a `transcription_history.jsonl` writer.
- `--transcribe-file` mode that emits `@@TIMING@@{...}` JSON checkpoints
  (`process_start`, `foundry_init_done`, `model_load_done`, `transcription_done`,
  `clipboard_set`, `paste_simulated`) for the latency comparison harness.

## Measured results

[../../benchmark/results/python_vs_csharp.md](../../benchmark/results/python_vs_csharp.md)
carries the current numbers plus the host/audio/model provenance needed to reproduce them.
The current file-mode results show lower C# process wall-clock time, but catalog and model
initialization costs are attributed differently by the two SDK paths. This benchmark does
not measure microphone-stop latency. Its comparable per-request metrics are
`inference_to_text` and `inference_to_paste`; these are broadly similar because both
implementations spend that time inside the same Foundry Local inference call.

The dominant remaining cost is `FoundryLocalManager.GetCatalogAsync()`, measured at ~8.5-10s
per process. SDK 1.2.4 exposes no offline or cached-catalog option (`Configuration` offers
only `AppName`, `AppDataDir`, `ModelCacheDir`, `LogsDir`, `LogLevel`, `Web`, and
`AdditionalSettings`), and `ICatalog.GetCachedModelsAsync` still needs the catalog handle
first. It is a one-time startup cost for the tray app, so it was left as-is rather than
worked around.

## Known gaps vs. the Python implementation

- No BYO Qualcomm QNN direct-ONNX-Runtime path (equivalent of `qnn_whisper.py`) yet -
  Rust/C# QNN-direct bindings are less mature than Foundry Local's own EP handling; this
  prototype only exercises Foundry Local's catalog models.
- No silence-aware chunking for audio longer than the model's 30s window.
- No WER/CER scoring - use `benchmark/compare_python_csharp.py` for latency-only comparison.

For a reproducible comparison, use the same explicit execution provider for both apps,
such as `--execution-provider CPU`, and report the audio, model, provider, hardware,
runtime versions, warmup count, and measured-run count. The current `--warmup` option
launches separate discarded processes; it warms disk/model-download caches but does not
warm the measured process's runtime, catalog, or loaded model session.
