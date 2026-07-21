# Foundry Local Whispr

## What this is

This is [Whisper](https://openai.com/index/whisper/), the speech recignition system from [OpenAI](https://openai.com/) transcribing your voice locally on your machine. 

At first, this is supposed to become my personal dictation helper to gain speed and productivity when instructing my pilots and refining results or simply taking notes.

Secondly, this became the playground to evaluate the power and flexibility offered by Microsoft Foundry Local.


## Learnings

The intention was not only to run any AI model (in this case Whisper) locally, the intention was to run optimized AI models on local HW using whatever acceleration is available.

The project was inspired by this [WhisprFlow](https://github.com/dpraj007/whisprflow) repository that builds on qualcomm examples and quantisied models and the belonging [Qualcomm Documentation](https://github.com/qualcomm/ai-hub-models). But I got stuck in conflicting dependencies between sounddevice, onnxruntime and ai-hub-models.

Then I turned to the [offical Foundry Local documentation and sample code](https://learn.microsoft.com/en-us/azure/foundry-local/tutorials/tutorial-build-voice-to-text-note-taker?tabs=windows&pivots=programming-language-python) and was surprised how versatile Foundry Local handles hardware abstraction while recognizng available accelerators (GPU, NPU) and flexibly load the appropriate execution provider. 

On my Surface Laptop 7 (Snapdragon(R) X 12-core XIE80100) it detects the NPU and loads
- WebGpuExecutionProvider and
- QNNExecutionProvider

On my PC with (NVIDIA GeForce RTX 2060 SUPER) it detects the GPU and automatically installs the 
- CUDAExecutionProvider




## Installing

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and requires Python >=3.11,<3.13 (see `pyproject.toml`).

1. Install `uv` if you don't already have it (see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)).
2. Clone this repository and open a terminal in its root folder.
3. Install the dependencies:

```
uv sync
```

This creates a `.venv` and installs `foundry-local-sdk-winml`, `numpy`, `openai`, `pillow`, `pystray`, and `sounddevice` as declared in `pyproject.toml`. Microsoft Foundry Local itself handles downloading and registering the appropriate execution provider (CUDA, QNN, WebGPU, etc.) for your hardware on first run.


## Usage

### Command-line demo

`app.py` is still the unmodified demo from the [official Foundry Local voice-to-text note-taker tutorial](https://learn.microsoft.com/en-us/azure/foundry-local/tutorials/tutorial-build-voice-to-text-note-taker?tabs=windows&pivots=programming-language-python). It downloads and loads `whisper-tiny` to transcribe `sample-speech.wav`, then downloads and loads `qwen2.5-0.5b` to summarize that transcription into bullet-point notes:

```
uv run app.py
```

### Transcribe Tray App

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

### Starting without a console window

`uv run transcribe.py` keeps a terminal window open for the lifetime of the app. To launch it as a silent background tray app instead, double-click [`start_transcribe.vbs`](start_transcribe.vbs) (or point a shortcut, Start Menu entry, or your `shell:startup` folder at it). It runs `uv run transcribe.py` hidden, with no console window, from the script's own folder.

### Systray menu

Right-click (or left-click, depending on your OS) the tray icon to access:

- **Active (Hold Ctrl+Win) / Paused** — a disabled label showing the current dictation state.
- **Pause Dictation / Resume Dictation** — toggles whether the Ctrl+Win hotkey starts/stops recording.
- **Model: `<name>`** — a disabled label showing the currently loaded Whisper model.
- **Whisper model** — submenu listing every discovered Whisper model (e.g. `whisper-tiny`, `whisper-base`, `whisper-small`, `whisper-medium`, `whisper-large`); pick one to unload the current model and load the selected one on the fly. Disabled while a switch is in progress. The choice is saved to `transcribe_config.json`.
- **Mic: `[index] name`** — a disabled label showing the currently selected microphone.
- **Microphone** — submenu listing every detected input device; pick one to switch the active microphone at runtime. Disabled while recording. The choice is saved to `transcribe_config.json`.
- **Auto-paste at cursor** — toggles whether the transcription is automatically pasted at the current cursor position after dictation.
- **Open History** — opens `transcription_history.jsonl` with the default associated app.
- **Open App Log** — opens `transcribe.log` with the default associated app.
- **Quit** — stops recording if active, unloads the model, and exits the app.

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


