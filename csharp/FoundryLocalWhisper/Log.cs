namespace FoundryLocalWhisper;

/// <summary>Minimal rotating-ish file + console logger, mirroring transcribe_app.log behavior.</summary>
internal static class Log
{
    private static readonly object Gate = new();
    private static string? _logPath;
    private const long MaxBytes = 1_000_000;

    public static void Init(string logPath)
    {
        _logPath = logPath;
    }

    public static void Info(string message) => Write("INFO", message);
    public static void Warn(string message) => Write("WARNING", message);
    public static void Error(string message) => Write("ERROR", message);

    private static void Write(string level, string message)
    {
        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss,fff} {level} {message}";
        Console.WriteLine(line);

        if (_logPath is null)
        {
            return;
        }

        lock (Gate)
        {
            try
            {
                RotateIfNeeded();
                File.AppendAllLines(_logPath, new[] { line });
            }
            catch (IOException)
            {
                // Best-effort logging: don't crash the app over a log write failure.
            }
        }
    }

    private static void RotateIfNeeded()
    {
        if (_logPath is null || !File.Exists(_logPath))
        {
            return;
        }

        var info = new FileInfo(_logPath);
        if (info.Length < MaxBytes)
        {
            return;
        }

        var backup = _logPath + ".1";
        File.Delete(backup);
        File.Move(_logPath, backup);
    }
}
