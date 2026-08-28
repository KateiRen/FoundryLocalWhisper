"""Compare interaction latency between the Python and C# Foundry Whisper implementations.

and reports startup, model-load, stop-to-text, and stop-to-paste latency side by side.
Runs both implementations in single-shot file-transcription mode against the same
audio file/model, parses the ``@@TIMING@@{...}`` JSON checkpoints each side emits,
and reports startup, model-load, inference-to-text, and inference-to-paste latency side by side.

Usage:
    uv run python benchmark/compare_python_csharp.py <input.wav> [--model whisper-tiny ...]

Requires:
    - The Python app's dependencies installed (``uv sync``).
    - The C# app built in Release (``dotnet build -c Release`` in csharp/FoundryLocalWhisper).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import statistics
import subprocess
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMING_PREFIX = "@@TIMING@@"

# Consecutive checkpoint pairs whose gap represents a named latency metric.
METRIC_SPANS = [
    ("startup", "process_start", "foundry_init_done"),
    ("model_discovery", "foundry_init_done", "model_discovery_done"),
    ("model_load", "model_discovery_done", "model_load_done"),
    ("inference_to_text", "transcription_start", "transcription_done"),
    ("inference_to_paste", "transcription_done", "paste_simulated"),
    ("end_to_end", "process_start", "paste_simulated"),
]


@dataclass
class RunResult:
    implementation: str
    model: str
    ok: bool
    checkpoints: dict[str, float] = field(default_factory=dict)
    metrics_ms: dict[str, float] = field(default_factory=dict)
    wall_clock_s: float = 0.0
    stdout_tail: str = ""
    error: str | None = None


def _dotnet_version() -> str:
    try:
        return subprocess.run(["dotnet", "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _audio_info(audio_path: Path) -> str:
    with contextlib.suppress(OSError, wave.Error):
        with wave.open(str(audio_path), "rb") as wav:
            duration_s = wav.getnframes() / float(wav.getframerate())
            return (f"{audio_path.name} ({duration_s:.1f}s, {wav.getframerate()} Hz, "
                    f"{wav.getnchannels()} ch, {wav.getsampwidth() * 8}-bit)")
    return audio_path.name


def collect_environment(audio_path: Path, models: list[str], repeat: int, warmup: int,
                        execution_provider: str | None) -> dict[str, str]:
    """Provenance for the published report - both implementations must run on the same
    host, audio, and models for the deltas to mean anything."""
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python": f"{platform.python_version()} ({sys.executable})",
        "dotnet_sdk": _dotnet_version(),
        "audio": _audio_info(audio_path),
        "models": ", ".join(models),
        "execution_provider": execution_provider or "auto (SDK default variant)",
        "runs": f"{warmup} warmup + {repeat} measured per model/implementation",
    }


def _parse_timing_lines(output: str) -> dict[str, float]:
    checkpoints: dict[str, float] = {}
    for line in output.splitlines():
        if not line.startswith(TIMING_PREFIX):
            continue
        try:
            payload = json.loads(line[len(TIMING_PREFIX) :])
            checkpoints[str(payload["event"])] = float(payload["elapsed_ms"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return checkpoints


def _compute_metrics(checkpoints: dict[str, float]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name, start_event, end_event in METRIC_SPANS:
        if start_event in checkpoints and end_event in checkpoints:
            metrics[name] = checkpoints[end_event] - checkpoints[start_event]
    return metrics


def run_python(audio_path: Path, model: str, timeout_s: float, execution_provider: str | None = None) -> RunResult:
    output_txt = audio_path.with_suffix(".python.txt")
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "transcribe.py"),
        "--transcribe-file",
        str(audio_path),
        "--output-file",
        str(output_txt),
        "--model-name",
        model,
        "--benchmark-timing",
    ]
    if execution_provider:
        cmd += ["--execution-provider", execution_provider]
    return _run_and_parse("python", model, cmd, timeout_s)


def run_csharp(audio_path: Path, model: str, timeout_s: float, csproj_dir: Path,
               execution_provider: str | None = None) -> RunResult:
    exe_candidates = list(csproj_dir.glob("bin/Release/*/FoundryLocalWhisper.exe"))
    if not exe_candidates:
        return RunResult("csharp", model, ok=False, error=(
            f"No built exe found under {csproj_dir}/bin/Release - "
            "run 'dotnet build -c Release' first"
        ))

    output_txt = audio_path.with_suffix(".csharp.txt")
    cmd = [
        str(exe_candidates[0]),
        "--transcribe-file",
        str(audio_path),
        "--output-file",
        str(output_txt),
        "--model-name",
        model,
    ]
    if execution_provider:
        cmd += ["--execution-provider", execution_provider]
    return _run_and_parse("csharp", model, cmd, timeout_s)


def run_many(
    fn,
    audio_path: Path,
    model: str,
    timeout_s: float,
    repeat: int,
    warmup: int,
    *extra_args,
) -> list[RunResult]:
    """Runs discarded subprocess passes before measured subprocess passes.

    These passes can warm disk/model-download caches, but they do not warm the
    measured process's runtime, catalog state, or loaded session.
    """
    for _ in range(warmup):
        fn(audio_path, model, timeout_s, *extra_args)

    return [fn(audio_path, model, timeout_s, *extra_args) for _ in range(max(1, repeat))]


def _run_and_parse(implementation: str, model: str, cmd: list[str], timeout_s: float) -> RunResult:
    wall_start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(implementation, model, ok=False, error=f"Timed out after {timeout_s}s: {exc}")
    wall_clock_s = time.perf_counter() - wall_start

    output = (proc.stdout or "") + (proc.stderr or "")
    checkpoints = _parse_timing_lines(output)
    metrics = _compute_metrics(checkpoints)
    tail = "\n".join(output.splitlines()[-15:])

    if proc.returncode != 0:
        return RunResult(implementation, model, ok=False, checkpoints=checkpoints, metrics_ms=metrics,
                          wall_clock_s=wall_clock_s, stdout_tail=tail, error=f"Exit code {proc.returncode}")

    return RunResult(implementation, model, ok=True, checkpoints=checkpoints, metrics_ms=metrics,
                      wall_clock_s=wall_clock_s, stdout_tail=tail)


def _format_ms(value: float | None) -> str:
    return f"{value:8.1f} ms" if value is not None else "        n/a"


def build_report(results: list[RunResult], environment: dict[str, str]) -> str:
    lines: list[str] = ["# Python vs C# Foundry Whisper latency comparison", ""]
    lines.append("## Environment")
    lines.append("")
    lines.append("| Setting | Value |")
    lines.append("|---|---|")
    for key, value in environment.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")
    lines.append(
        "> Both implementations run single-shot file transcription. The Python side acquires the "
        "Foundry catalog lazily inside model load, so its `model_discovery` is 0 and that cost "
        "shows up in `model_load`; the C# side acquires the catalog explicitly and reports it "
        "under `model_discovery`. Compare `end_to_end`, `stop_to_text`, and `stop_to_paste` "
        "across implementations - the intermediate split is not like-for-like."
    )
    lines.append("")

    by_model: dict[str, list[RunResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    for model, runs in by_model.items():
        lines.append(f"## Model: {model}")
        lines.append("")
        py_runs = [r for r in runs if r.implementation == "python" and r.ok]
        cs_runs = [r for r in runs if r.implementation == "csharp" and r.ok]
        n = max(len(py_runs), len(cs_runs))
        lines.append(f"Measured runs: python={len(py_runs)}, csharp={len(cs_runs)}"
                     + (" (median shown below)" if n > 1 else ""))
        lines.append("")
        lines.append("| Metric | Python | C# | Delta (C# - Python) |")
        lines.append("|---|---|---|---|")

        metric_names = [name for name, _, _ in METRIC_SPANS]

        def _median_wall(runs: list[RunResult]) -> float | None:
            return statistics.median(r.wall_clock_s * 1000 for r in runs) if runs else None

        py_wall = _median_wall(py_runs)
        cs_wall = _median_wall(cs_runs)
        wall_delta = f"{cs_wall - py_wall:+.1f} ms" if py_wall is not None and cs_wall is not None else "n/a"
        lines.append(f"| process_wall_clock (includes interpreter/runtime startup) | {_format_ms(py_wall)} | {_format_ms(cs_wall)} | {wall_delta} |")

        def _median_metric(runs: list[RunResult], metric: str) -> float | None:
            values = [r.metrics_ms[metric] for r in runs if metric in r.metrics_ms]
            return statistics.median(values) if values else None

        for metric in metric_names:
            py_val = _median_metric(py_runs, metric)
            cs_val = _median_metric(cs_runs, metric)
            delta = f"{cs_val - py_val:+.1f} ms" if py_val is not None and cs_val is not None else "n/a"
            lines.append(f"| {metric} | {_format_ms(py_val)} | {_format_ms(cs_val)} | {delta} |")

        lines.append("")
        for r in runs:
            if not r.ok:
                lines.append(f"> **{r.implementation} failed**: {r.error}")
                if r.stdout_tail:
                    lines.append("```")
                    lines.append(r.stdout_tail)
                    lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_file", type=Path, help="WAV file to transcribe with both implementations")
    parser.add_argument("--model", action="append", dest="models", default=None,
                         help="Model alias to benchmark (repeatable). Default: whisper-tiny")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-run timeout in seconds")
    parser.add_argument("--csharp-project", type=Path,
                         default=PROJECT_ROOT / "csharp" / "FoundryLocalWhisper",
                         help="Path to the built C# project directory")
    parser.add_argument("--output", type=Path,
                         default=PROJECT_ROOT / "benchmark" / "results" / "python_vs_csharp.md",
                         help="Markdown report output path")
    parser.add_argument("--json-output", type=Path,
                         default=PROJECT_ROOT / "benchmark" / "results" / "python_vs_csharp.json",
                         help="Raw JSON results output path")
    parser.add_argument("--execution-provider", choices=["CPU", "GPU", "NPU"], default=None,
                         help="Pin both implementations to the same device type variant for a fair comparison")
    parser.add_argument("--repeat", type=int, default=1,
                         help="Measured runs per model/implementation (median is reported)")
    parser.add_argument("--warmup", type=int, default=1,
                         help="Discarded runs before measuring, to warm the model cache/catalog")
    args = parser.parse_args()

    models = args.models or ["whisper-tiny"]
    audio_path = args.audio_file.resolve()
    if not audio_path.exists():
        parser.error(f"Audio file not found: {audio_path}")

    results: list[RunResult] = []
    for model in models:
        print(f"[python]  transcribing {audio_path.name} with {model} "
              f"({args.warmup} warmup + {args.repeat} measured) ...")
        results += run_many(run_python, audio_path, model, args.timeout, args.repeat, args.warmup,
                            args.execution_provider)
        print(f"[csharp]  transcribing {audio_path.name} with {model} "
              f"({args.warmup} warmup + {args.repeat} measured) ...")
        results += run_many(run_csharp, audio_path, model, args.timeout, args.repeat, args.warmup,
                            args.csharp_project, args.execution_provider)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    environment = collect_environment(audio_path, models, args.repeat, args.warmup,
                                      args.execution_provider)
    report = build_report(results, environment)
    args.output.write_text(report, encoding="utf-8")
    args.json_output.write_text(
        json.dumps({"environment": environment, "runs": [vars(r) for r in results]},
                   indent=2, default=str),
        encoding="utf-8",
    )

    print()
    print(report)
    print(f"\nWrote report to {args.output}")
    print(f"Wrote raw results to {args.json_output}")


if __name__ == "__main__":
    main()
