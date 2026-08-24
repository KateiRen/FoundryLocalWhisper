# Contributing to Foundry Local Whisper

Thanks for helping improve Foundry Local Whisper. Contributions of code,
documentation, testing, benchmarks, and demo material are welcome.

## Getting started

1. Install Python 3.11 or 3.12 and
   [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Fork and clone the repository.
3. Install the project dependencies:

   ```powershell
   uv sync
   ```

4. Start the application from a terminal so that setup messages and errors are
   visible:

   ```powershell
   uv run transcribe.py
   ```

The application is Windows-specific. Its global keyboard hook, clipboard
integration, tray application, and hardware acceleration should be tested on
Windows before opening a pull request. The optional QNN path also requires the
model files and Qualcomm hardware described in the README.

## How to contribute

For a bug report, include the selected model, relevant hardware and execution
provider, steps to reproduce, expected behavior, and the relevant entries from
`transcribe.log`. Do not include private transcription content in an issue.

For a code change:

1. Keep the change focused and open an issue first for substantial design or
   architecture work.
2. Preserve the existing Foundry Local path unless the change explicitly
   targets the optional QNN backend.
3. Run the affected workflow manually, including tray behavior when relevant.
4. Run a syntax check before submitting:

   ```powershell
   uv run python -m compileall transcribe.py qnn_whisper.py benchmark
   ```

5. Describe how the change was tested and note the hardware used in the pull
   request.

## Contribution ideas

The following areas would be especially useful additions to the project.

### Improve the hackathon video

Create a clearer demonstration video for the hackathon presentation. The
current recording has audio synchronization issues, so a replacement should
keep the source audio, spoken narration, and displayed transcription aligned.
It should demonstrate startup, push-to-talk dictation, local model execution,
automatic paste, model selection, and the difference between available
hardware acceleration paths. Please include captions and avoid showing private
notifications, file paths, or transcription history.

### Add explicit language handling

Whisper can sometimes interpret German speech as a request to produce English
text. Translation can be useful, but it should be intentional rather than the
default surprise.

A contribution could add a persisted language preference with options such as
automatic detection, German, and English, along with an explicit transcription
versus translation mode. The setting should be available consistently through
the command line, `transcribe_config.json`, and the tray menu. Both the Foundry
Local API path in `transcribe.py` and the decoder prompt tokens in
`qnn_whisper.py` need to honor the same choice. Tests or repeatable sample files
should cover German-to-German transcription, English-to-English transcription,
automatic detection, and deliberate translation.

### Explore a faster C# or Rust implementation

Build or prototype a C# or Rust version with lower interaction latency than the
current Python implementation. Measure before optimizing: capture startup,
model-load, recording-stop-to-text, and recording-stop-to-paste timings using
the same audio, model, and hardware as the Python baseline.

The prototype should preserve the current core workflow: a global push-to-talk
shortcut, microphone capture at 16 kHz mono, local inference, clipboard output,
optional automatic paste, tray controls, configuration persistence, and useful
logs. Prefer a thin native client around supported Foundry Local or ONNX Runtime
APIs rather than changing model behavior solely to improve benchmark numbers.
Document build prerequisites and publish reproducible comparison results.

### Improve error handling

Make failures actionable without losing diagnostic detail. Useful improvements
include clearer messages for missing microphones, unavailable execution
providers, model download or loading failures, unsupported audio files,
clipboard failures, and invalid configuration. Background-thread failures
should be visible through the tray application as well as in the log, while
recoverable errors should not terminate the application.

Error-handling changes should include the failing operation and a practical next
step in user-facing messages, retain exception details in `transcribe.log`, and
avoid recording transcript text or other sensitive content unnecessarily.
Where possible, add a focused test or a deterministic reproduction procedure
for each failure mode.

## Pull request checklist

- The change is limited to one clear purpose.
- User-facing behavior and configuration changes are documented.
- Existing Python workflows still work, or any intentional incompatibility is
  explained.
- Relevant checks and manual scenarios pass.
- Performance claims include the model, hardware, input audio, and measurement
  method.
- Logs, screenshots, recordings, and fixtures contain no private data.
