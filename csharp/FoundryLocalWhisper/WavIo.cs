using NAudio.Wave;

namespace FoundryLocalWhisper;

internal static class WavIo
{
    /// <summary>Writes 16 kHz mono PCM16 samples to a WAV file for handoff to the Foundry audio client.</summary>
    public static void WritePcm16Mono(string path, short[] samples, int sampleRate = AudioRecorder.SampleRate)
    {
        using var writer = new WaveFileWriter(path, new WaveFormat(sampleRate, 16, 1));
        var bytes = new byte[samples.Length * 2];
        Buffer.BlockCopy(samples, 0, bytes, 0, bytes.Length);
        writer.Write(bytes, 0, bytes.Length);
    }

    /// <summary>Reads any PCM WAV file into 16-bit mono samples resampled to 16 kHz if needed.</summary>
    public static (short[] Samples, int SampleRate) ReadPcm16Mono(string path)
    {
        using var reader = new AudioFileReader(path);
        var targetFormat = new WaveFormat(AudioRecorder.SampleRate, 16, 1);

        using var resampled = new MediaFoundationResampler(reader, targetFormat) { ResamplerQuality = 60 };
        var samples = new List<short>();
        var buffer = new byte[targetFormat.AverageBytesPerSecond];
        int bytesRead;
        while ((bytesRead = resampled.Read(buffer, 0, buffer.Length)) > 0)
        {
            var chunk = new short[bytesRead / 2];
            Buffer.BlockCopy(buffer, 0, chunk, 0, bytesRead - bytesRead % 2);
            samples.AddRange(chunk);
        }

        return (samples.ToArray(), AudioRecorder.SampleRate);
    }
}
