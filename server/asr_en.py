"""
English ASR via faster-whisper distil-small.en.

Model: distil-whisper/distil-small.en (CTranslate2 format, ~166M params)
Chosen over full Whisper small for 2-3x faster inference on i7-1355U.

Note on threading: faster-whisper uses CTranslate2 internally.
Inter-op threading configured once at load time.  Do not change without
re-running the concurrent benchmark (Gate 0D extension for English).
"""
import logging
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_model = None
_compute_type: str = "int8"  # int8 = best on CPU without AVX-512


def load(intra_op_num_threads: int = 2) -> None:
    """Load faster-whisper distil-small.en. Call once at server startup."""
    global _model
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            "distil-whisper/distil-small.en",
            device="cpu",
            compute_type=_compute_type,
            cpu_threads=intra_op_num_threads,
            num_workers=1,
        )
        logger.info("English ASR: distil-small.en loaded (threads=%d, type=%s)",
                    intra_op_num_threads, _compute_type)
    except Exception as e:
        raise RuntimeError(f"Failed to load English ASR model: {e}") from e


def transcribe(audio: np.ndarray) -> str:
    """
    Transcribe float32 16kHz PCM. Returns English text.

    Args:
        audio: float32 numpy array, 16kHz, amplitude in [-1, 1]

    Returns:
        Transcribed text, stripped of leading/trailing whitespace.
    """
    if _model is None:
        raise RuntimeError("English ASR model not loaded. Call asr_en.load() first.")

    if len(audio) == 0:
        return ""

    t0 = time.perf_counter()

    try:
        segments, info = _model.transcribe(
            audio,
            language="en",
            task="transcribe",
            beam_size=1,           # beam=1 for speed on CPU; acceptable WER tradeoff
            vad_filter=False,      # VAD is handled server-side by vad.py — do not double-filter
            word_timestamps=False,
        )
        text = " ".join(seg.text for seg in segments).strip()
    except Exception as e:
        logger.error("English ASR transcription error: %s", e)
        return ""

    elapsed = (time.perf_counter() - t0) * 1000
    rtf = elapsed / (len(audio) / 16000 * 1000) if len(audio) > 0 else 0
    logger.debug("English ASR: %.0fms wall | RTF=%.2f | chars=%d", elapsed, rtf, len(text))

    return text
