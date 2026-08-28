using System.Text.Json;
using System.Text.Json.Serialization;

namespace FoundryLocalWhisper;

internal sealed class AppConfig
{
    [JsonPropertyName("mic_index")]
    public int? MicIndex { get; set; }

    [JsonPropertyName("model_name")]
    public string? ModelName { get; set; }

    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public static AppConfig Load(string path)
    {
        if (!File.Exists(path))
        {
            return new AppConfig();
        }

        try
        {
            var json = File.ReadAllText(path);
            return JsonSerializer.Deserialize<AppConfig>(json) ?? new AppConfig();
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            Log.Warn($"Failed to read config file: {ex.Message}");
            return new AppConfig();
        }
    }

    public void Save(string path)
    {
        try
        {
            File.WriteAllText(path, JsonSerializer.Serialize(this, JsonOptions));
        }
        catch (IOException ex)
        {
            Log.Error($"Failed to write config file: {ex.Message}");
        }
    }
}
