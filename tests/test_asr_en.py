"""
Phase 1A — English ASR tests.

Requires: faster-whisper with distil-small.en downloaded.
Run generate_fixtures.py first to create f02_en_clean.wav.
"""
import pathlib
import wave

import numpy as np
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_RATE = 16000

pytestmark = pytest.mark.integration


def _load_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "r") as wf:
        raw = wf.readframes(wf.getnframes())
    s16 = np.frombuffer(raw, dtype=np.int16)
    return s16.astype(np.float32) / 32768.0


@pytest.fixture(scope="module")
def en_audio():
    p = FIXTURES / "f02_en_clean.wav"
    if not p.exists():
        pytest.skip(f"Fixture missing: {p}. Run tests/generate_fixtures.py")
    return _load_wav(p)


@pytest.fixture(scope="module")
def loaded_en_model():
    try:
        from server import asr_en
        asr_en.load(intra_op_num_threads=2)
        return asr_en
    except Exception as e:
        pytest.skip(f"English ASR model unavailable: {e}")


class TestEnglishASR:
    def test_returns_nonempty_text(self, loaded_en_model, en_audio):
        text = loaded_en_model.transcribe(en_audio)
        assert isinstance(text, str)
        assert len(text) > 0, "transcribe() returned empty on English speech"

    def test_output_is_latin(self, loaded_en_model, en_audio):
        text = loaded_en_model.transcribe(en_audio)
        latin_chars = sum(1 for c in text if c.isalpha() and ord(c) < 256)
        assert latin_chars > 5, f"Output has no Latin characters: {text!r}"

    def test_empty_audio_returns_empty(self, loaded_en_model):
        text = loaded_en_model.transcribe(np.array([], dtype=np.float32))
        assert text == ""

    def test_rtf_under_1x(self, loaded_en_model, en_audio):
        import time
        duration_s = len(en_audio) / SAMPLE_RATE
        t0 = time.perf_counter()
        loaded_en_model.transcribe(en_audio)
        elapsed = time.perf_counter() - t0
        rtf = elapsed / duration_s
        assert rtf < 1.0, f"RTF={rtf:.2f} — English ASR slower than real-time"
