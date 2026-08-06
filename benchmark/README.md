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