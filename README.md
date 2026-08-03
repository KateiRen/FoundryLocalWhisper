# Foundry Local Whispr

## What this is

This is [Whisper](https://openai.com/index/whisper/), the speech recignition system from [OpenAI](https://openai.com/) transcribing your voice locally on your machine. 

At first, this is supposed to become my personal dictation helper to gain speed and productivity when instructing my pilots and refining results or simply taking notes.

Secondly, this became the playground to evaluate the power and flexibility offered by **Microsoft Foundry Local**.


## Learnings

The intention was not only to run any AI model (in this case Whisper) locally, the intention was to run optimized AI models on local HW using whatever acceleration is available.

The project was inspired by this [WhisprFlow](https://github.com/dpraj007/whisprflow) repository that builds on qualcomm examples and quantisied models and the belonging [Qualcomm Documentation](https://github.com/qualcomm/ai-hub-models). But I got stuck in conflicting dependencies between sounddevice, onnxruntime and ai-hub-models.

Then I turned to the [offical Foundry Local documentation and sample code](https://learn.microsoft.com/en-us/azure/foundry-local/tutorials/tutorial-build-voice-to-text-note-taker?tabs=windows&pivots=programming-language-python) and was surprised how versatile Foundry Local handles hardware abstraction while recognizng available accelerators (GPU, NPU) and flexibly load the appropriate execution provider. 

On my Surface Laptop 7 (Snapdragon(R) X 12-core XIE80100) it detects the GPU and NPU and loads execution providers
- WebGpuExecutionProvider and
- QNNExecutionProvider

On my PC (with a NVIDIA GeForce RTX 2060 SUPER) it detects the GPU and automatically installs the 
- CUDAExecutionProvider

Sadly, Foundry Local does not come with a NPU whisper model in it's catalogue. That's why I added qnn_whisper.py as an alternate path to run a qualcomm optimized for NPU model (whisper_large_v3_turbo).

## Installing

### Foundry Local Whisper

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and requires Python >=3.11,<3.13 (see `pyproject.toml`).

1. Install `uv` if you don't already have it (see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)).
2. Clone this repository and open a terminal in its root folder.
3. Install the dependencies:

```
uv sync
```

This creates a `.venv` and installs `foundry-local-sdk-winml`, `numpy`, `openai`, `pillow`, `pystray`, and `sounddevice` as declared in `pyproject.toml`. Microsoft Foundry Local itself handles downloading and registering the appropriate execution provider (CUDA, QNN, WebGPU, etc.) for your hardware on first run.

### QNN-Whisper

The optional QNN backend runs Whisper Large V3 Turbo directly on the Qualcomm
NPU through `onnxruntime-qnn`. The model is supplied by **Qualcomm AI Hub**. A Qualcomm account is required to download the precompiled model.

1. Open the [Whisper-Large-V3-Turbo model page on Qualcomm AI Hub](https://aihub.qualcomm.com/models/whisper_large_v3_turbo) and sign in or create a Qualcomm account.
2. Select **Snapdragon X Elite CRD** as the target device and **ONNX Runtime** as the runtime.
3. Choose the **float** model and click **Download Model**. Download the precompiled QNN ONNX package for Snapdragon X Elite.
4. Extract the downloaded archive into the following directory:

```text
BYO-Models/
└── whisper_large_v3_turbo/
	└── whisper_large_v3_turbo-precompiled_qnn_onnx-float-qualcomm_snapdragon_x_elite/
		├── decoder.onnx
		├── decoder_qairt_context.bin
		├── encoder.onnx
		├── encoder_qairt_context.bin
		└── metadata.json
```

The directory and filenames must match this layout because `qnn_whisper.py`
loads them from that fixed location. The `.bin` files are larger than GitHub's
file-size limit and are excluded from this repository, so each installation
must obtain them from Qualcomm AI Hub.

After the files are present, run the normal tray application:

```text
uv run transcribe.py
```

Select `whisper-large-v3-turbo-qnn` from the **Whisper model** tray submenu.
The QNN option is added only when the model directory is present. The Foundry
Local Whisper models remain usable without this download.



## Usage

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

For the first start, use `uv run transcribe.py` in a terminal instead of the hidden VBS launcher. Downloading and loading the execution providers and model can take some time, and the terminal keeps that setup progress and any prompts visible. Once the first startup has completed successfully, the VBS launcher is suitable for normal use.

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

### CLI options:

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
