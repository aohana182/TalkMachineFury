"""
Phase 1A — Russian ASR tests.

Requires: faster-whisper medium (default), or gigaam/vosk fallback.
Run generate_fixtures.py first to create f01_ru_clean.wav.
"""
import pathlib
import wave

import numpy as np
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_RATE = 16000

pytestmark = pytest.mark.integration  # skip in unit-only mode


def _load_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "r") as wf:
        raw = wf.readframes(wf.getnframes())
    s16 = np.frombuffer(raw, dtype=np.int16)
    return s16.astype(np.float32) / 32768.0


@pytest.fixture(scope="module")
def ru_audio():
    p = FIXTURES / "f01_ru_clean.wav"
    if not p.exists():
        pytest.skip(f"Fixture missing: {p}. Run tests/generate_fixtures.py")
    return _load_wav(p)


@pytest.fixture(scope="module")
def loaded_ru_model():
    """Load Russian ASR once per test module."""
    try:
        from server import asr_ru
        asr_ru.load("whisper:medium", intra_op_num_threads=2)
        return asr_ru
    except Exception as e:
        pytest.skip(f"Russian ASR model unavailable: {e}")


class TestRussianASR:
    def test_returns_nonempty_text(self, loaded_ru_model, ru_audio):
        text = loaded_ru_model.transcribe(ru_audio)
        assert isinstance(text, str), "transcribe() must return str"
        assert len(text) > 0, "transcribe() returned empty string on Russian speech"

    def test_output_is_russian(self, loaded_ru_model, ru_audio):
        text = loaded_ru_model.transcribe(ru_audio)
        # Check for Cyrillic characters
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
        assert has_cyrillic, f"Output has no Cyrillic: {text!r}"

    def test_empty_audio_returns_empty(self, loaded_ru_model):
        text = loaded_ru_model.transcribe(np.array([], dtype=np.float32))
        assert text == "", f"Expected empty string for empty audio, got: {text!r}"

    def test_backend_is_set(self, loaded_ru_model):
        backend = loaded_ru_model.backend()
        assert backend in ("gigaam_v3", "whisper", "vosk"), f"Unknown backend: {backend}"

    def test_rtf_under_3x(self, loaded_ru_model, ru_audio):
        """Real-time factor must be <3.0 on target hardware (i7-1355U).
        whisper:medium CPU int8 is typically 1.5-2.5x RTF on a 5s clip.
        gigaam CTC is <1.0x but was replaced to hit the 20% WER target.
        """
        import time
        duration_s = len(ru_audio) / SAMPLE_RATE
        t0 = time.perf_counter()
        loaded_ru_model.transcribe(ru_audio)
        elapsed = time.perf_counter() - t0
        rtf = elapsed / duration_s
        assert rtf < 3.0, f"RTF={rtf:.2f} — inference too slow (limit: 3x real-time)"
