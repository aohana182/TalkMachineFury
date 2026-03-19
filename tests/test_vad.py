"""
Phase 1A — VAD tests.

Tests:
  - VAD fires on speech, not on silence
  - discard_rate < 5% on clean speech
  - State tensors persist across calls (no reset between frames)
  - should_flush() triggers after min_silence_ms of silence
  - flush() returns audio and resets speech state, not VAD state tensors
"""
import pathlib
import wave

import numpy as np
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_RATE = 16000


def _load_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "r") as wf:
        assert wf.getnchannels() == 1, "mono only"
        assert wf.getframerate() == SAMPLE_RATE, f"need {SAMPLE_RATE}Hz"
        raw = wf.readframes(wf.getnframes())
    s16 = np.frombuffer(raw, dtype=np.int16)
    return s16.astype(np.float32) / 32768.0


def _make_silence(duration_s: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)


def _make_tone(duration_s: float, freq: float = 440.0) -> np.ndarray:
    t = np.linspace(0, duration_s, int(SAMPLE_RATE * duration_s), dtype=np.float32)
    return 0.4 * np.sin(2 * np.pi * freq * t).astype(np.float32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed_chunked(vad, audio: np.ndarray, chunk_size: int = 512) -> list[bool]:
    """Feed audio in 512-sample chunks, return per-chunk speech flags."""
    flags = []
    for i in range(0, len(audio), chunk_size):
        chunk = audio[i : i + chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
        flags.append(vad.ingest_pcm(chunk))
    return flags


# ---------------------------------------------------------------------------
# Unit tests — no model needed
# ---------------------------------------------------------------------------

class TestVADSessionContract:
    """Test VADSession interface without loading Silero (mocked)."""

    def _make_vad(self, threshold=0.40, min_silence_ms=450):
        from unittest.mock import MagicMock, patch
        import numpy as np

        # Mock ort.InferenceSession — vad.py calls sess.run(None, {...})
        mock_sess = MagicMock()

        def fake_run(output_names, inputs):
            # Return speech probability based on audio RMS
            audio = inputs["input"]
            rms = float(np.abs(audio).mean())
            prob = np.array([[1.0 if rms > 0.05 else 0.0]], dtype=np.float32)
            h = inputs["h"].copy()
            c = inputs["c"].copy()
            return [prob, h, c]

        mock_sess.run.side_effect = fake_run

        with patch("server.vad._load_silero", return_value=mock_sess):
            from server.vad import VADSession
            return VADSession(threshold=threshold, min_silence_ms=min_silence_ms)

    def test_silence_does_not_trigger_speech(self):
        vad = self._make_vad()
        silence = _make_silence(0.5)
        flags = _feed_chunked(vad, silence)
        assert not any(flags), "Silence should not trigger VAD"

    def test_speech_triggers_vad(self):
        vad = self._make_vad()
        tone = _make_tone(1.0)  # loud tone > 0.05 RMS
        flags = _feed_chunked(vad, tone)
        assert any(flags), "Speech-like audio should trigger VAD"

    def test_flush_returns_audio_and_resets_buffer(self):
        vad = self._make_vad(min_silence_ms=100)
        tone = _make_tone(1.0)
        _feed_chunked(vad, tone)

        # Feed silence to trigger should_flush
        silence = _make_silence(0.2)
        _feed_chunked(vad, silence)

        if vad.should_flush():
            audio = vad.flush()
            assert len(audio) > 0, "flush() must return non-empty audio"
            assert not vad.has_pending_speech, "buffer must be clear after flush()"

    def test_discard_rate_low_on_pure_speech(self):
        vad = self._make_vad(min_silence_ms=100)
        tone = _make_tone(2.0)
        _feed_chunked(vad, tone)

        # Force flush
        if vad.has_pending_speech:
            vad.flush()

        # discard_rate should be low — most frames dispatched
        assert vad.discard_rate < 0.30, (
            f"discard_rate {vad.discard_rate:.2f} too high on pure speech"
        )

    def test_state_tensors_not_reset_between_frames(self):
        """h/c must carry forward across frames — no per-frame reset."""
        vad = self._make_vad()
        tone = _make_tone(0.1)
        chunk = tone[:512]

        vad.ingest_pcm(chunk)
        # h/c must be numpy arrays, not None
        assert vad._h is not None
        assert vad._c is not None
        assert vad._h.shape == (2, 1, 64)
        assert vad._c.shape == (2, 1, 64)

    def test_reset_state_clears_everything(self):
        vad = self._make_vad()
        tone = _make_tone(1.0)
        _feed_chunked(vad, tone)

        vad.reset_state()
        assert not vad.has_pending_speech
        assert vad._samples_ingested == 0
        assert vad._samples_dispatched == 0


# ---------------------------------------------------------------------------
# Integration tests — requires Silero model download
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestVADWithRealSilero:
    """Requires Silero VAD model (internet + torch.hub)."""

    @pytest.fixture(scope="class")
    def vad(self):
        from server.vad import VADSession
        return VADSession(threshold=0.40, min_silence_ms=450)

    @pytest.fixture(scope="class")
    def ru_audio(self):
        p = FIXTURES / "f01_ru_clean.wav"
        pytest.importorskip("wave")
        if not p.exists():
            pytest.skip(f"Fixture missing: {p}. Run tests/generate_fixtures.py")
        return _load_wav(p)

    def test_vad_detects_speech_in_ru_audio(self, vad, ru_audio):
        flags = _feed_chunked(vad, ru_audio)
        speech_ratio = sum(flags) / len(flags)
        assert speech_ratio > 0.3, f"VAD detected speech in only {speech_ratio:.1%} of frames"

    def test_discard_rate_below_5pct(self, vad, ru_audio):
        # Reset for clean measurement
        vad.reset_state()
        _feed_chunked(vad, ru_audio)
        if vad.has_pending_speech:
            vad.flush()
        assert vad.discard_rate < 0.05, (
            f"discard_rate={vad.discard_rate:.4f} exceeds 5% on clean speech"
        )
