"""
Russian ASR.

Backends (selected via config.toml `russian = ...`):
  gigaam-v3-e2e-ctc   — GigaAM v3 CTC via onnx-asr (faster, ~24% WER)
  gigaam-v3-e2e-rnnt  — GigaAM v3 RNN-T via onnx-asr (~21% WER)
  whisper:<size>      — faster-whisper multilingual, lang=ru (beam=1, greedy)
                        sizes: tiny, base, small, medium, large-v3
                        medium recommended: ~18.8% WER, CPU-feasible
  vosk:<model-name>   — sherpa-onnx fallback

Utterances arrive pre-segmented by VAD — typically 1-10s.
"""
import re
import time
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Module-level singleton — loaded once, reused per request
_model = None
_backend: str = "unloaded"  # "gigaam_v3" | "whisper" | "vosk" | "unloaded"


def load(model_name: str = "whisper:medium", intra_op_num_threads: int = 2) -> None:
    """
    Load Russian ASR model.  Call once at server startup.

    Args:
        model_name: one of:
            "gigaam-v3-e2e-ctc"  (onnx-asr)
            "gigaam-v3-e2e-rnnt" (onnx-asr)
            "whisper:<size>"     (faster-whisper, e.g. "whisper:medium")
            "vosk:<model-name>"  (sherpa-onnx fallback)
        intra_op_num_threads: ONNX thread hint (gigaam/vosk only)
    """
    global _model, _backend

    if model_name.startswith("vosk:"):
        vosk_name = model_name.split(":", 1)[1]
        _load_vosk(vosk_name, intra_op_num_threads)
    elif model_name.startswith("whisper:"):
        size = model_name.split(":", 1)[1]
        _load_whisper(size)
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
        logger.error("Failed to load %s: %s -- attempting vosk fallback", model_name, e)
        _load_vosk("alphacep/vosk-model-ru", intra_op_num_threads)


def _load_whisper(size: str) -> None:
    global _model, _backend
    from faster_whisper import WhisperModel
    _model = WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
    )
    _backend = "whisper"
    logger.info("Russian ASR: faster-whisper %s loaded (CPU, int8)", size)


def _load_vosk(model_name: str, intra_op_num_threads: int) -> None:
    global _model, _backend
    try:
        import sherpa_onnx

        opts = sherpa_onnx.OnlineRecognizerConfig()
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
        elif _backend == "whisper":
            result = _transcribe_whisper(audio)
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
    result = _model.recognize(audio, sample_rate=16000)
    if isinstance(result, list):
        return " ".join(str(r) for r in result)
    return str(result) if result else ""


def _transcribe_whisper(audio: np.ndarray) -> str:
    segments, _ = _model.transcribe(
        audio,
        language="ru",
        task="transcribe",
        beam_size=1,
        vad_filter=False,
        word_timestamps=False,
        no_speech_threshold=0.6,
        temperature=0.0,
    )
    return " ".join(seg.text for seg in segments).strip()


def _transcribe_vosk(audio: np.ndarray) -> str:
    import sherpa_onnx

    stream = _model.create_stream()
    stream.accept_waveform(16000, audio)
    _model.decode_stream(stream)
    result = _model.get_result(stream)
    return result.text if hasattr(result, "text") else str(result)


def backend() -> str:
    return _backend
