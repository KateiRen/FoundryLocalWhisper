"""Add model storage and host-memory footprints to an existing benchmark report."""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcribe import QNN_BYO_MODEL_NAME, QNN_MODEL_DIR


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _process_memory_mb() -> tuple[float, float]:
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    if not get_process_memory_info(
        ctypes.windll.kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    ):
        raise ctypes.WinError()
    mib = 1024 * 1024
    return counters.WorkingSetSize / mib, counters.PeakWorkingSetSize / mib


def _directory_size_mb(path: Path) -> float:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file()) / 1_000_000


def _measure_worker(model_name: str) -> dict[str, float | str]:
    baseline_mb, _ = _process_memory_mb()
    if model_name == QNN_BYO_MODEL_NAME:
        from qnn_whisper import QnnWhisperPipeline

        loaded_model: Any = QnnWhisperPipeline()
        storage_mb = _directory_size_mb(QNN_MODEL_DIR)
        storage_source = "local model files"
    else:
        from foundry_local_sdk import Configuration, FoundryLocalManager

        FoundryLocalManager.initialize(Configuration(app_name="whisper_footprint_probe"))
        manager = FoundryLocalManager.instance
        manager.download_and_register_eps()
        loaded_model = manager.catalog.get_model(model_name)
        if loaded_model is None:
            raise RuntimeError(f"Foundry model not found: {model_name}")
        loaded_model.download()
        loaded_model.load()
        storage_mb = float(loaded_model.info.file_size_mb)
        storage_source = "Foundry catalog package size"

    loaded_mb, peak_mb = _process_memory_mb()
    result = {
        "model": model_name,
        "storage_mb": storage_mb,
        "storage_source": storage_source,
        "memory_baseline_mb": baseline_mb,
        "memory_loaded_mb": loaded_mb,
        "memory_incremental_mb": max(0.0, loaded_mb - baseline_mb),
        "memory_peak_mb": peak_mb,
        "memory_scope": "Python host process working set; accelerator-reserved memory excluded",
    }
    if model_name != QNN_BYO_MODEL_NAME:
        loaded_model.unload()
    return result


def _run_isolated(model_name: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", model_name],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _write_reports(results_path: Path, rows: list[dict[str, Any]]) -> None:
    results_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = results_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report_path = results_path.with_name("benchmark_report.md")
    lines = [
        "# Whisper model benchmark",
        "",
        f"Reference model: `{QNN_BYO_MODEL_NAME}`",
        "",
        "| Model | Status | Latency | Speed | Word accuracy | CER | Storage | Loaded memory | Incremental memory |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        latency = f"{row['latency_s']:.2f}s" if row.get("latency_s") is not None else "-"
        speed = f"{row['realtime_speed']:.2f}x" if row.get("realtime_speed") is not None else "-"
        accuracy = f"{row['word_accuracy_pct']:.1f}%" if row.get("word_accuracy_pct") is not None else "-"
        cer = f"{row['cer']:.3f}" if row.get("cer") is not None else "-"
        storage = f"{row['storage_mb']:.0f} MB" if row.get("storage_mb") is not None else "-"
        loaded = f"{row['memory_loaded_mb']:.0f} MB" if row.get("memory_loaded_mb") is not None else "-"
        incremental = f"{row['memory_incremental_mb']:.0f} MB" if row.get("memory_incremental_mb") is not None else "-"
        lines.append(
            f"| {row['model']} | {row['status']} | {latency} | {speed} | {accuracy} | "
            f"{cer} | {storage} | {loaded} | {incremental} |"
        )
    lines.extend(
        [
            "",
            "Memory scope: isolated Python host-process working set after model load. "
            "Incremental memory is measured above the imported-runtime baseline. "
            "GPU/NPU driver allocations and accelerator-reserved memory may not be attributed to the Python process.",
            "",
            "Storage scope: Foundry catalog package size for Foundry models; actual local file size for the BYO QNN model.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def enrich_results(results_path: Path) -> None:
    rows: list[dict[str, Any]] = json.loads(results_path.read_text(encoding="utf-8"))
    for row in rows:
        if row["status"] != "ok":
            continue
        print(f"Measuring {row['model']}...", flush=True)
        row.update(_run_isolated(row["model"]))
    _write_reports(results_path, rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", type=Path)
    parser.add_argument("--worker")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(_measure_worker(args.worker)))
        return
    if args.results is None:
        parser.error("results JSON path is required")
    enrich_results(args.results)


if __name__ == "__main__":
    main()