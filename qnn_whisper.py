"""Standalone Whisper-Large-V3-Turbo inference pipeline for the Qualcomm QNN NPU.

This bypasses the Foundry Local SDK entirely (it cannot load this BYO,
precompiled-QNN ONNX model format) and instead drives the encoder/decoder
graphs directly via onnxruntime + onnxruntime-qnn.

The decode algorithm (attention-mask convention, KV-cache handling, decode
length, stop condition) mirrors Qualcomm's own reference implementation in
qai_hub_models (`HfWhisperApp._transcribe_single_chunk` /
`_shared/hf_whisper/model.py`), reimplemented here with numpy only so we
avoid a `torch`/`transformers` dependency.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort
import onnxruntime_qnn
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)

# --- Audio / model constants (from qai_hub_models `_shared/hf_whisper/model.py`) ---
SAMPLE_RATE = 16000
CHUNK_LENGTH_S = 30
N_SAMPLES = CHUNK_LENGTH_S * SAMPLE_RATE  # 480000
N_FFT = 400
HOP_LENGTH = 160
N_MELS = 128
MEAN_DECODE_LEN = 200
MASK_NEG = -100.0
NUM_DECODER_LAYERS = 4
NUM_HEADS = 20
HEAD_DIM = 64

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_DIR = (
    SCRIPT_DIR
    / "BYO-Models"
    / "whisper_large_v3_turbo"
    / "whisper_large_v3_turbo-precompiled_qnn_onnx-float-qualcomm_snapdragon_x_elite"
)
CACHE_DIR = SCRIPT_DIR / ".cache"

# Pinned to the "large-v3" commit that added 128-mel-bin filters (this model
# needs mel_128, not the older mel_80) so the filterbank never silently
# changes underneath us.
_MEL_FILTERS_URL = (
    "https://raw.githubusercontent.com/openai/whisper/"
    "c5d42560760a05584c1c79546a098287e5a771eb/whisper/assets/mel_filters.npz"
)
_TOKENIZER_REPO = "openai/whisper-large-v3-turbo"

_qnn_ep_registered = False


def _ensure_qnn_ep_registered() -> None:
    global _qnn_ep_registered
    if _qnn_ep_registered:
        return
    ort.register_execution_provider_library(
        "QNNExecutionProvider", onnxruntime_qnn.get_library_path()
    )
    _qnn_ep_registered = True


def _create_qnn_session(model_path: Path) -> ort.InferenceSession:
    _ensure_qnn_ep_registered()
    devices = [
        d
        for d in ort.get_ep_devices()
        if d.ep_name == "QNNExecutionProvider" and str(d.device.type).endswith("NPU")
    ]
    if not devices:
        raise RuntimeError("No QNN NPU device found via onnxruntime-qnn.")
    so = ort.SessionOptions()
    so.add_provider_for_devices([devices[0]], {})
    return ort.InferenceSession(str(model_path), sess_options=so)


def _download_cached(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        logger.info("Downloading %s -> %s", url, dest)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 - fixed, pinned HTTPS URL
    return dest


def _load_mel_filters(n_mels: int = N_MELS) -> np.ndarray:
    path = _download_cached(_MEL_FILTERS_URL, CACHE_DIR / "mel_filters.npz")
    with np.load(path) as f:
        return f[f"mel_{n_mels}"].astype(np.float32)


def _log_mel_spectrogram(audio: np.ndarray) -> np.ndarray:
    """Reimplements openai/whisper's `log_mel_spectrogram` in numpy.

    Returns float16 array of shape [1, N_MELS, 3000].
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(audio) > N_SAMPLES:
        audio = audio[:N_SAMPLES]
    elif len(audio) < N_SAMPLES:
        audio = np.pad(audio, (0, N_SAMPLES - len(audio)))

    # Periodic Hann window, matching torch.hann_window(N_FFT, periodic=True).
    window = np.hanning(N_FFT + 1)[:-1].astype(np.float32)

    padded = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")
    n_frames = 1 + (len(padded) - N_FFT) // HOP_LENGTH
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, N_FFT),
        strides=(padded.strides[0] * HOP_LENGTH, padded.strides[0]),
    ).copy()
    frames *= window

    stft = np.fft.rfft(frames, n=N_FFT, axis=-1).T  # [N_FFT//2+1, n_frames]
    magnitudes = np.abs(stft[:, :-1]) ** 2  # drop last frame -> 3000

    filters = _load_mel_filters()
    mel_spec = filters @ magnitudes
    log_spec = np.log10(np.clip(mel_spec, 1e-10, None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec.astype(np.float16)[np.newaxis, :, :]


class QnnWhisperPipeline:
    """Runs whisper-large-v3-turbo transcription entirely on the QNN NPU."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        logger.info("Loading QNN whisper-large-v3-turbo encoder/decoder onto NPU")
        self._encoder = _create_qnn_session(model_dir / "encoder.onnx")
        self._decoder = _create_qnn_session(model_dir / "decoder.onnx")
        self._decoder_output_names = [o.name for o in self._decoder.get_outputs()]

        self._tokenizer = self._load_tokenizer()
        self._sot = self._tokenizer.token_to_id("<|startoftranscript|>")
        self._eot = self._tokenizer.token_to_id("<|endoftext|>")
        if self._sot is None or self._eot is None:
            raise RuntimeError(
                "Could not find <|startoftranscript|>/<|endoftext|> tokens in tokenizer."
            )
        logger.info("QNN whisper pipeline ready")

    @staticmethod
    def _load_tokenizer() -> Tokenizer:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=_TOKENIZER_REPO, filename="tokenizer.json")
        return Tokenizer.from_file(path)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe up to 30s of mono float32 audio sampled at 16kHz."""
        tokens = self._decode_tokens(audio)
        return self._tokenizer.decode(tokens, skip_special_tokens=True).strip()

    def _decode_tokens(self, audio: np.ndarray) -> list[int]:
        mel = _log_mel_spectrogram(audio)  # [1, 128, 3000] float16

        encoder_outputs = self._encoder.run(None, {"input_features": mel})
        encoder_output_names = [o.name for o in self._encoder.get_outputs()]
        kv_cache_cross = dict(zip(encoder_output_names, encoder_outputs))

        output_ids = [self._sot]
        position_ids = np.array([0], dtype=np.int32)
        attention_mask = np.full((1, 1, 1, MEAN_DECODE_LEN), MASK_NEG, dtype=np.float16)

        k_cache_self = {
            f"k_cache_self_{i}_in": np.zeros(
                (NUM_HEADS, 1, HEAD_DIM, MEAN_DECODE_LEN - 1), dtype=np.float16
            )
            for i in range(NUM_DECODER_LAYERS)
        }
        v_cache_self = {
            f"v_cache_self_{i}_in": np.zeros(
                (NUM_HEADS, 1, MEAN_DECODE_LEN - 1, HEAD_DIM), dtype=np.float16
            )
            for i in range(NUM_DECODER_LAYERS)
        }

        for n in range(MEAN_DECODE_LEN - 1):
            input_ids = np.array([[output_ids[n]]], dtype=np.int32)
            attention_mask[:, :, :, MEAN_DECODE_LEN - n - 1] = 0.0

            feed = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                **k_cache_self,
                **v_cache_self,
                **kv_cache_cross,
                "position_ids": position_ids,
            }
            outputs = self._decoder.run(None, feed)
            out = dict(zip(self._decoder_output_names, outputs))

            logits = out["logits"]  # [1, vocab, 1, 1]
            next_id = int(np.argmax(logits[0, :, 0, 0]))
            output_ids.append(next_id)

            if n >= MEAN_DECODE_LEN - 2 or next_id == self._eot:
                break

            for i in range(NUM_DECODER_LAYERS):
                k_cache_self[f"k_cache_self_{i}_in"] = out[f"k_cache_self_{i}_out"]
                v_cache_self[f"v_cache_self_{i}_in"] = out[f"v_cache_self_{i}_out"]
            position_ids = position_ids + 1

        return output_ids
