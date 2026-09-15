namespace FoundryLocalWhisper;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Log.Init(Path.Combine(AppContext.BaseDirectory, "transcribe_app.log"));
        ApplicationConfiguration.Initialize();

        string? modelName = null;
        int? micIndex = null;
        var autoPaste = true;
        string? transcribeFile = null;
        string? outputFile = null;
        string? executionProvider = null;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--model-name":
                    modelName = args[++i];
                    break;
                case "--mic-index":
                    micIndex = int.Parse(args[++i]);
                    break;
                case "--no-auto-paste":
                    autoPaste = false;
                    break;
                case "--transcribe-file":
                    transcribeFile = args[++i];
                    break;
                case "--output-file":
                    outputFile = args[++i];
                    break;
                case "--execution-provider":
                    executionProvider = args[++i];
                    break;
            }
        }

        var app = new TrayApp(modelName, micIndex, autoPaste, executionProvider);
        try
        {
            if (transcribeFile is not null)
            {
                outputFile ??= Path.ChangeExtension(transcribeFile, ".txt");
                app.TranscribeFileAsync(transcribeFile, outputFile).GetAwaiter().GetResult();
            }
            else
            {
                app.RunAsync().GetAwaiter().GetResult();
            }
        }
        catch (Exception ex)
        {
            Log.Error($"Fatal startup error: {ex}");
            NativeMethods.MessageBox(0, $"{ex.Message}\n\nSee the log for details.", "Foundry Transcribe - startup failed", 0x10);
        }
        finally
        {
            app.Dispose();
        }
    }
}
