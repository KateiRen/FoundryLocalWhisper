"""Benchmark every available Whisper model against the QNN large-v3 reference."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcribe import (
    QNN_BYO_MODEL_NAME,
    SAMPLE_RATE,
    FoundryTranscribeTrayApp,
    _read_wav_pcm,
    _write_wav_pcm,
    split_audio_on_silence,
)


DEFAULT_INPUT = Path(
    r"C:\Users\karstenh\OneDrive - Microsoft\Private\FY27\FY27 Landing\MCAPS Start for Partners.wav"
)


@dataclass
class BenchmarkResult:
    model: str
    status: str
    error: str | None
    latency_s: float | None
    audio_s: float
    realtime_factor: float | None
    realtime_speed: float | None
    word_count: int
    characters: int
    words_per_s: float | None
    wer: float | None = None
    word_accuracy_pct: float | None = None
    word_substitutions: int | None = None
    word_deletions: int | None = None
    word_insertions: int | None = None
    cer: float | None = None
    character_accuracy_pct: float | None = None
    transcript_file: str | None = None
    storage_mb: float | None = None
    storage_source: str | None = None
    memory_baseline_mb: float | None = None
    memory_loaded_mb: float | None = None
    memory_incremental_mb: float | None = None
    memory_peak_mb: float | None = None
    memory_scope: str | None = None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def _normalize_words(text: str) -> list[str]:
    return re.findall(r"\w+(?:['’]\w+)?", text.casefold(), flags=re.UNICODE)


def _normalize_characters(text: str) -> list[str]:
    return list(" ".join(_normalize_words(text)))


def _edit_counts(reference: list[str], hypothesis: list[str]) -> tuple[int, int, int]:
    """Return substitutions, deletions, and insertions for a minimum edit path."""
    previous = [(index, 0, 0, index) for index in range(len(hypothesis) + 1)]
    for ref_index in range(1, len(reference) + 1):
        row = [(ref_index, 0, ref_index, 0)]
        for hyp_index in range(1, len(hypothesis) + 1):
            if reference[ref_index - 1] == hypothesis[hyp_index - 1]:
                row.append(previous[hyp_index - 1])
                continue

            substitution = previous[hyp_index - 1]
            deletion = previous[hyp_index]
            insertion = row[hyp_index - 1]
            choices = (
                (substitution[0] + 1, substitution[1] + 1, substitution[2], substitution[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            )
            row.append(min(choices, key=lambda item: item[0]))
        previous = row
    _, substitutions, deletions, insertions = previous[-1]
    return substitutions, deletions, insertions


def _score(reference_text: str, hypothesis_text: str) -> dict[str, float | int]:
    reference_words = _normalize_words(reference_text)
    hypothesis_words = _normalize_words(hypothesis_text)
    substitutions, deletions, insertions = _edit_counts(reference_words, hypothesis_words)
    word_errors = substitutions + deletions + insertions
    wer = word_errors / len(reference_words) if reference_words else float(bool(hypothesis_words))

    reference_chars = _normalize_characters(reference_text)
    hypothesis_chars = _normalize_characters(hypothesis_text)
    char_counts = _edit_counts(reference_chars, hypothesis_chars)
    cer = sum(char_counts) / len(reference_chars) if reference_chars else float(bool(hypothesis_chars))
    return {
        "wer": wer,
        "word_accuracy_pct": max(0.0, 1.0 - wer) * 100.0,
        "word_substitutions": substitutions,
        "word_deletions": deletions,
        "word_insertions": insertions,
        "cer": cer,
        "character_accuracy_pct": max(0.0, 1.0 - cer) * 100.0,
    }


def _chunk_to_qnn_audio(
    chunk: np.ndarray, sample_rate: int, sample_width: int
) -> np.ndarray:
    audio = chunk.astype(np.float32)
    if sample_width == 1:
        audio = (audio - 128.0) / 128.0
    else:
        audio /= float(2 ** (sample_width * 8 - 1))
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        output_length = round(len(audio) * SAMPLE_RATE / sample_rate)
        source_positions = np.arange(len(audio), dtype=np.float64)
        target_positions = np.arange(output_length, dtype=np.float64) * sample_rate / SAMPLE_RATE
        audio = np.interp(target_positions, source_positions, audio).astype(np.float32)
    return np.clip(audio, -1.0, 1.0)


def _transcribe_chunks(
    app: FoundryTranscribeTrayApp,
    model: str,
    chunks: list[np.ndarray],
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> str:
    texts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        logging.info("%s: chunk %d/%d", model, index, len(chunks))
        if model == QNN_BYO_MODEL_NAME:
            text = app._require_qnn_pipeline().transcribe(
                _chunk_to_qnn_audio(chunk, sample_rate, sample_width)
            )
        else:
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_path = Path(temp_file.name)
                _write_wav_pcm(temp_path, chunk, sample_rate, channels, sample_width)
                response = app._require_audio_client().transcribe(str(temp_path))
                text = response.text if hasattr(response, "text") else str(response)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        if text.strip():
            texts.append(text.strip())
    return " ".join(texts)


def _unload_foundry_model(app: FoundryTranscribeTrayApp) -> None:
    if app._speech_model is not None:
        app._speech_model.unload()
    app._speech_model = None
    app._audio_client = None


def _write_reports(output_dir: Path, results: list[BenchmarkResult]) -> None:
    rows = [asdict(result) for result in results]
    (output_dir / "benchmark_results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (output_dir / "benchmark_results.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Whisper model benchmark",
        "",
        f"Reference model: `{QNN_BYO_MODEL_NAME}`",
        "",
        "| Model | Status | Latency | Speed | Words | WER | Word accuracy | CER |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        latency = f"{result.latency_s:.2f}s" if result.latency_s is not None else "-"
        speed = f"{result.realtime_speed:.2f}x" if result.realtime_speed is not None else "-"
        wer = f"{result.wer:.3f}" if result.wer is not None else "-"
        accuracy = f"{result.word_accuracy_pct:.1f}%" if result.word_accuracy_pct is not None else "-"
        cer = f"{result.cer:.3f}" if result.cer is not None else "-"
        lines.append(
            f"| {result.model} | {result.status} | {latency} | {speed} | "
            f"{result.word_count} | {wer} | {accuracy} | {cer} |"
        )
    (output_dir / "benchmark_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(input_path: Path, output_dir: Path) -> list[BenchmarkResult]:
    wav_data = _read_wav_pcm(input_path)
    if wav_data is None:
        raise ValueError("Benchmark input must be an 8-bit, 16-bit, or 32-bit PCM WAV file")
    frames, sample_rate, channels, sample_width = wav_data
    chunks = split_audio_on_silence(frames, sample_rate)
    audio_s = len(frames) / sample_rate
    output_dir.mkdir(parents=True, exist_ok=True)

    app = FoundryTranscribeTrayApp(model_name="whisper-tiny", auto_paste=False)
    app._resolve_model_name()
    app._initialize_foundry()
    models = list(app._available_models)
    if QNN_BYO_MODEL_NAME not in models:
        raise RuntimeError("The QNN large-v3 model is unavailable, so no reference can be produced")

    # Keep QNN last so its persistent NPU sessions cannot compete with Foundry models.
    models = [model for model in models if model != QNN_BYO_MODEL_NAME] + [QNN_BYO_MODEL_NAME]
    results: list[BenchmarkResult] = []
    transcripts: dict[str, str] = {}
    try:
        for model in models:
            try:
                if app.model_name != model or (app._speech_model is None and model != QNN_BYO_MODEL_NAME):
                    _unload_foundry_model(app)
                    app._load_speech_model(model)
                started = time.perf_counter()
                text = _transcribe_chunks(
                    app, model, chunks, sample_rate, channels, sample_width
                )
                elapsed = time.perf_counter() - started
                transcript_path = output_dir / f"{_safe_name(model)}.txt"
                transcript_path.write_text(text, encoding="utf-8")
                transcripts[model] = text
                results.append(
                    BenchmarkResult(
                        model=model,
                        status="ok",
                        error=None,
                        latency_s=elapsed,
                        audio_s=audio_s,
                        realtime_factor=elapsed / audio_s,
                        realtime_speed=audio_s / elapsed,
                        word_count=len(_normalize_words(text)),
                        characters=len(text),
                        words_per_s=len(_normalize_words(text)) / elapsed,
                        transcript_file=str(transcript_path),
                    )
                )
            except Exception as error:
                logging.exception("Benchmark failed for %s", model)
                results.append(
                    BenchmarkResult(
                        model=model,
                        status="error",
                        error=str(error),
                        latency_s=None,
                        audio_s=audio_s,
                        realtime_factor=None,
                        realtime_speed=None,
                        word_count=0,
                        characters=0,
                        words_per_s=None,
                    )
                )
    finally:
        _unload_foundry_model(app)

    reference = transcripts.get(QNN_BYO_MODEL_NAME)
    if not reference:
        raise RuntimeError("QNN large-v3 reference transcription failed or was empty")
    for result in results:
        if result.status == "ok":
            for key, value in _score(reference, transcripts[result.model]).items():
                setattr(result, key, value)
    _write_reports(output_dir, results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe one PCM WAV with every available Whisper model and benchmark against QNN large-v3."
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent / "results"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")
    results = run_benchmark(args.input, args.output_dir)
    print(f"\nResults: {args.output_dir / 'benchmark_report.md'}")
    for result in results:
        accuracy = f"{result.word_accuracy_pct:.1f}%" if result.word_accuracy_pct is not None else "n/a"
        print(f"{result.model}: {result.status}, word accuracy={accuracy}")


if __name__ == "__main__":
    main()