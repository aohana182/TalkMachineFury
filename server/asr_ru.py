"""
Russian ASR via GigaAM v3 CTC (onnx-asr).

Fallback: vosk Zipformer2 via sherpa-onnx, activated when GigaAM v3 is absent
from onnx-asr's model registry (Gate 0A result stored in config.toml).

Overlap-and-stitch note: GigaAM v3 is full-context (no streaming tensors).
Utterances arrive pre-segmented by VAD — typically 1-10s.  No stitching required
for normal speech.  If an utterance exceeds 30s (VAD max_speech_s limit), it is
split by the VAD before reaching this module.
"""
import re
import time
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Module-level singleton — loaded once, reused per request
_model = None
_backend: str = "unloaded"  # "gigaam_v3" | "vosk" | "unloaded"


def load(model_name: str = "gigaam-v3-e2e-ctc", intra_op_num_threads: int = 2) -> None:
    """
    Load Russian ASR model.  Call once at server startup.

    Args:
        model_name: "gigaam-v3-e2e-ctc" (onnx-asr) or "vosk:<vosk-model-name>" (sherpa-onnx fallback)
        intra_op_num_threads: from config.toml, set by Gate 0D benchmark
    """
    global _model, _backend

    if model_name.startswith("vosk:"):
        vosk_name = model_name.split(":", 1)[1]
        _load_vosk(vosk_name, intra_op_num_threads)
    else:
        _load_gigaam(model_name, intra_op_num_threads)


def _load_gigaam(model_name: str, intra_op_num_threads: int) -> None:
    global _model, _backend
    try:
        import onnx_asr
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_op_num_threads
        opts.inter_op_num_threads = 1
        _model = onnx_asr.load_model(model_name, sess_options=opts)
        _backend = "gigaam_v3"
        logger.info("Russian ASR: %s loaded (threads=%d)", model_name, intra_op_num_threads)
    except Exception as e:
        logger.error("Failed to load %s: %s — attempting vosk fallback", model_name, e)
        _load_vosk("alphacep/vosk-model-ru", intra_op_num_threads)


def _load_vosk(model_name: str, intra_op_num_threads: int) -> None:
    global _model, _backend
    try:
        import sherpa_onnx

        opts = sherpa_onnx.OnlineRecognizerConfig()
        # sherpa-onnx handles threading internally; pass hint if API supports it
        _model = sherpa_onnx.OnlineRecognizer.from_pretrained(
            model_name,
        )
        _backend = "vosk"
        logger.info("Russian ASR: vosk/sherpa-onnx loaded (%s)", model_name)
    except Exception as e:
        raise RuntimeError(f"Failed to load vosk fallback ({model_name}): {e}") from e


def transcribe(audio: np.ndarray) -> str:
    """
    Transcribe float32 16kHz PCM. Returns UTF-8 Russian text.

    Args:
        audio: float32 numpy array, 16kHz, amplitude in [-1, 1]

    Returns:
        Transcribed text, or empty string if model returns nothing.
    """
    if _model is None:
        raise RuntimeError("Russian ASR model not loaded. Call asr_ru.load() first.")

    if len(audio) == 0:
        return ""

    t0 = time.perf_counter()

    try:
        if _backend == "gigaam_v3":
            result = _transcribe_gigaam(audio)
        elif _backend == "vosk":
            result = _transcribe_vosk(audio)
        else:
            raise RuntimeError(f"Unknown backend: {_backend}")
    except Exception as e:
        logger.error("Transcription error (%s): %s", _backend, e)
        return ""

    elapsed = (time.perf_counter() - t0) * 1000
    rtf = elapsed / (len(audio) / 16000 * 1000) if len(audio) > 0 else 0
    logger.debug("Russian ASR: %.0fms wall | RTF=%.2f | chars=%d", elapsed, rtf, len(result))

    return result.strip()


def _transcribe_gigaam(audio: np.ndarray) -> str:
    text = _model.transcribe(audio)
    if isinstance(text, list):
        text = " ".join(text)
    return text or ""


def _transcribe_vosk(audio: np.ndarray) -> str:
    import sherpa_onnx

    stream = _model.create_stream()
    # sherpa-onnx expects int16 or float32 depending on version
    stream.accept_waveform(16000, audio)
    _model.decode_stream(stream)
    result = _model.get_result(stream)
    return result.text if hasattr(result, "text") else str(result)


def backend() -> str:
    return _backend
