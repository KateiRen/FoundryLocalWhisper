# Whisper benchmarks

Benchmark tooling and generated artifacts are contained in this directory.

From the repository root, run a benchmark with:

```powershell
uv run python benchmark/benchmark_models.py <input.wav>
```

Results are written to `benchmark/results/` by default. Add storage and host-memory measurements to an existing result set with:

```powershell
uv run python benchmark/measure_model_footprints.py benchmark/results/sample_10min/benchmark_results.json
```

Generate the presentation with PptxGenJS available to Node:

```powershell
$env:NODE_PATH = (npm root -g)
node benchmark/generate_benchmark_pptx.js benchmark/results/sample_10min/benchmark_results.json benchmark/results/sample_10min/whisper_model_benchmark.pptx "Benchmark audio sample"
```

The transcript-alignment metrics use the QNN large-v3-turbo transcript as a relative reference, not human-verified ground truth.

## Python vs C# latency comparison

[../csharp/FoundryLocalWhisper](../csharp/FoundryLocalWhisper) is a C# prototype of the tray app
(see [../csharp/FoundryLocalWhisper/README.md](../csharp/FoundryLocalWhisper/README.md) for build
prerequisites). Both implementations emit `@@TIMING@@{...}` JSON checkpoints in
`--transcribe-file`/`--benchmark-timing` mode, so `compare_python_csharp.py` can run each
against the same audio file and model and report startup, model-load,
inference-to-text, and inference-to-paste latency side by side:

```powershell
cd csharp/FoundryLocalWhisper
dotnet build -c Release
cd ../..
uv run python benchmark/compare_python_csharp.py <input.wav> --model whisper-tiny --model whisper-small --execution-provider CPU --warmup 1 --repeat 3
```

Results are written to `benchmark/results/python_vs_csharp.md` (human-readable) and
`benchmark/results/python_vs_csharp.json` (raw checkpoints, for further analysis).

The file-mode benchmark does not measure microphone-stop latency: its inference clock
starts at `transcription_start` after audio has already been supplied to each app.
Use `inference_to_text` and `inference_to_paste` for the comparable model/pipeline
measurements. `--warmup` launches discarded processes, so it may warm downloaded files
but does not warm the measured process's runtime, catalog, or loaded model session.
For published comparisons, pin the same provider on both implementations, for example
`--execution-provider CPU`; `auto` is useful for exploration but can select different
variants.
