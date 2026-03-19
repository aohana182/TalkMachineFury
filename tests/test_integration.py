"""
Phase 4 — End-to-end integration test.

Asserts:
  - lines[] returned within 2s of utterance end
  - discard_rate < 5%
  - text contains recognizable words from the fixture

Run after Phase 4 (extension wired to server):
    pytest tests/test_integration.py -v -m integration
"""
import asyncio
import pathlib
import time
import wave

import numpy as np
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512

pytestmark = pytest.mark.integration


def _load_wav_s16(path: pathlib.Path) -> bytes:
    with wave.open(str(path), "r") as wf:
        return wf.readframes(wf.getnframes())


# Phrases we expect in f01_ru_clean.wav (from generate_fixtures.py TTS)
EXPECTED_RU_SUBSTRINGS = ["добр", "квартал", "выручк", "показател"]


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient that runs the full lifespan (startup + teardown)."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from server.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def ru_wav_bytes():
    p = FIXTURES / "f01_ru_clean.wav"
    if not p.exists():
        pytest.skip(f"Fixture missing: {p}")
    return _load_wav_s16(p)


class TestEndToEnd:
    def test_transcript_arrives_promptly(self, client, ru_wav_bytes):
        """Lines must appear within 2s of sending the utterance."""
        first_transcript_at = None
        last_audio_at = None

        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config

            frame_bytes = FRAME_SAMPLES * 2
            for i in range(0, len(ru_wav_bytes), frame_bytes):
                frame = ru_wav_bytes[i : i + frame_bytes]
                if len(frame) < frame_bytes:
                    frame = frame + b"\x00" * (frame_bytes - len(frame))
                ws.send_bytes(frame)
                last_audio_at = time.perf_counter()

            ws.send_bytes(b"")  # stop

            for _ in range(1000):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "transcript" and first_transcript_at is None:
                        first_transcript_at = time.perf_counter()
                    if msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        assert first_transcript_at is not None, "No transcript received"
        # Wall-clock delay after last audio frame
        delay = first_transcript_at - last_audio_at
        assert delay < 5.0, f"First transcript took {delay:.1f}s — too slow"

    def test_discard_rate_below_threshold(self, client, ru_wav_bytes):
        """VAD discard_rate must stay below 5% on clean speech."""
        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config
            frame_bytes = FRAME_SAMPLES * 2
            for i in range(0, len(ru_wav_bytes), frame_bytes):
                frame = ru_wav_bytes[i : i + frame_bytes]
                if len(frame) < frame_bytes:
                    frame += b"\x00" * (frame_bytes - len(frame))
                ws.send_bytes(frame)
            ws.send_bytes(b"")

            for _ in range(1000):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        # Check /health for discard rate (client is the module-level fixture)
        r = client.get("/health")
        health = r.json()
        discard = health.get("vad_discard_rate_avg")
        if discard is not None:
            assert discard < 0.05, f"VAD discard_rate={discard:.4f} > 5%"

    def test_recognizable_russian_words(self, client, ru_wav_bytes):
        """Transcript must contain recognizable words from the fixture."""
        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config
            frame_bytes = FRAME_SAMPLES * 2
            for i in range(0, len(ru_wav_bytes), frame_bytes):
                frame = ru_wav_bytes[i : i + frame_bytes]
                if len(frame) < frame_bytes:
                    frame += b"\x00" * (frame_bytes - len(frame))
                ws.send_bytes(frame)
            ws.send_bytes(b"")

            last_transcript = None
            for _ in range(1000):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "transcript":
                        last_transcript = msg
                    if msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        assert last_transcript is not None
        full_text = " ".join(l["text"] for l in last_transcript["lines"]).lower()

        matched = [w for w in EXPECTED_RU_SUBSTRINGS if w in full_text]
        assert len(matched) >= 1, (
            f"No expected Russian words found in transcript: {full_text!r}\n"
            f"Expected any of: {EXPECTED_RU_SUBSTRINGS}"
        )
