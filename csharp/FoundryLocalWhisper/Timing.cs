using System.Diagnostics;
using System.Text.Json;

namespace FoundryLocalWhisper;

/// <summary>
/// Emits machine-readable timing checkpoints (JSON lines on stdout) so the comparison
/// harness can measure startup, model-load, stop-to-text, and stop-to-paste latency
/// identically for the Python and C# implementations.
/// </summary>
internal static class Timing
{
    private static readonly Stopwatch Stopwatch = Stopwatch.StartNew();

    public static void Mark(string eventName)
    {
        var payload = JsonSerializer.Serialize(new
        {
            @event = eventName,
            elapsed_ms = Stopwatch.Elapsed.TotalMilliseconds,
            wall_clock = DateTimeOffset.UtcNow.ToString("O"),
        });
        Console.WriteLine($"@@TIMING@@{payload}");
    }
}
