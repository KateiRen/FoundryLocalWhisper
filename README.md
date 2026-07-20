# Foundry Local Whisper

## What this is

This is a project to leverage Microsoft Foundry Local to run optimized AI models on local HW.
The code is inspired by the offical foundry local demo at https://learn.microsoft.com/en-us/azure/foundry-local/tutorials/tutorial-build-voice-to-text-note-taker?tabs=windows&pivots=programming-language-python and the WhisprFlow repo https://github.com/dpraj007/whisprflow that build on qualcomm examples and quantisied models.


On my Gaming PC with a NVIDIA GeForce RTX 2060 SUPER (7 years old) it automatically installs the CUDAExecutionProvider

$ uv run app.py 
  CUDAExecutionProvider            60.7%


## Transcribe Tray App

Run the tray app with:

```
uv run transcribe.py
```

Behavior:

- On first start, the app lists all available input microphones and asks you to choose one.
- The selected microphone is stored in `transcribe_config.json` as `mic_index`.
- On later starts, the saved microphone is reused automatically.
- Whisper model selection is available from the tray menu under `Whisper model`.
- Model changes from the tray are persisted in `transcribe_config.json` as `model_name`.

CLI options:

```
uv run transcribe.py --mic-index 3
```

- Temporarily overrides the configured microphone for this launch.

```
uv run transcribe.py --model-name whisper-small
```

- Temporarily overrides the configured whisper model for this launch.

```
uv run transcribe.py --select-mic
```

- Forces a new microphone selection prompt and saves the new choice.


