"""
Model registry — load once at startup, dispatch by language.

Usage:
    from server.models import load_models, model_for_lang

    load_models(config)          # at startup
    asr = model_for_lang("ru")  # per-request
    text = asr.transcribe(pcm)
"""
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import to allow importing models.py without all deps installed
_loaded: dict[str, Any] = {}


def load_models(config: dict) -> None:
    """
    Load all ASR models based on config.

    Args:
        config: parsed config.toml as dict
            config["models"]["russian"] — model name string
            config["inference"]["intra_op_num_threads"] — int
    """
    threads = config.get("inference", {}).get("intra_op_num_threads", 2)
    russian_model = config.get("models", {}).get("russian", "gigaam_v3")

    logger.info("Loading Russian ASR (%s, threads=%d)...", russian_model, threads)
    from server import asr_ru
    asr_ru.load(russian_model, intra_op_num_threads=threads)
    _loaded["ru"] = asr_ru

    logger.info("Loading English ASR (distil-small.en, threads=%d)...", threads)
    from server import asr_en
    asr_en.load(intra_op_num_threads=threads)
    _loaded["en"] = asr_en

    logger.info("All models loaded: %s", list(_loaded.keys()))


def model_for_lang(lang: str):
    """
    Return the ASR module for the given language code.

    Args:
        lang: "ru" or "en"

    Returns:
        Module with a .transcribe(audio: np.ndarray) -> str method.

    Raises:
        KeyError: if lang not loaded (call load_models first)
        ValueError: if lang not recognized
    """
    if lang not in ("ru", "en"):
        raise ValueError(f"Unsupported language: {lang!r}. Use 'ru' or 'en'.")
    if lang not in _loaded:
        raise KeyError(f"Model for '{lang}' not loaded. Call load_models() first.")
    return _loaded[lang]


def loaded_langs() -> list[str]:
    return list(_loaded.keys())
