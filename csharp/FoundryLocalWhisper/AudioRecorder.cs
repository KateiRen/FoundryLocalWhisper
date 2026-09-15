using NAudio.Wave;

namespace FoundryLocalWhisper;

/// <summary>Captures 16 kHz mono PCM16 audio from a WaveIn device, mirroring sounddevice's InputStream role.</summary>
internal sealed class AudioRecorder : IDisposable
{
    public const int SampleRate = 16000;
    private const int Channels = 1;

    private WaveInEvent? _waveIn;
    private readonly List<byte> _buffer = new();
    private readonly object _lock = new();

    public static IReadOnlyList<(int Index, string Name)> ListInputDevices()
    {
        var devices = new List<(int, string)>();
        for (var i = 0; i < WaveInEvent.DeviceCount; i++)
        {
            devices.Add((i, WaveInEvent.GetCapabilities(i).ProductName));
        }
        return devices;
    }

    public void Start(int? deviceIndex)
    {
        lock (_lock)
        {
            _buffer.Clear();
        }

        _waveIn = new WaveInEvent
        {
            DeviceNumber = deviceIndex ?? 0,
            WaveFormat = new WaveFormat(SampleRate, 16, Channels),
            BufferMilliseconds = 50,
        };
        _waveIn.DataAvailable += OnDataAvailable;
        _waveIn.StartRecording();
    }

    private void OnDataAvailable(object? sender, WaveInEventArgs e)
    {
        lock (_lock)
        {
            _buffer.AddRange(e.Buffer.AsSpan(0, e.BytesRecorded).ToArray());
        }
    }

    /// <summary>Stops capture and returns the recorded PCM16 mono samples.</summary>
    public short[] Stop()
    {
        if (_waveIn is not null)
        {
            _waveIn.StopRecording();
            _waveIn.DataAvailable -= OnDataAvailable;
            _waveIn.Dispose();
            _waveIn = null;
        }

        lock (_lock)
        {
            var bytes = _buffer.ToArray();
            var samples = new short[bytes.Length / 2];
            Buffer.BlockCopy(bytes, 0, samples, 0, samples.Length * 2);
            return samples;
        }
    }

    public void Dispose()
    {
        _waveIn?.Dispose();
    }
}
