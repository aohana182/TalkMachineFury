"""
Phase 2 — WebSocket server integration test.

Sends a WAV file over the /asr WebSocket and asserts:
  - lines[] returned within reasonable time
  - transcript contains non-empty text
  - /health returns 200 with models listed

Requires the server to be running:
    uvicorn server.main:app --port 8765
OR run in-process via ASGI test client (httpx + anyio).
"""
import asyncio
import pathlib
import wave
import json

import numpy as np
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # worklet frame size


def _load_wav_s16(path: pathlib.Path) -> bytes:
    with wave.open(str(path), "r") as wf:
        return wf.readframes(wf.getnframes())


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app():
    """Return FastAPI app for in-process testing."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from server.main import app
    return app


@pytest.fixture(scope="module")
def ru_wav_bytes():
    p = FIXTURES / "f01_ru_clean.wav"
    if not p.exists():
        pytest.skip(f"Fixture missing: {p}")
    return _load_wav_s16(p)


class TestHealthEndpoint:
    def test_health_ok(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "ru" in body["models"]
        assert "en" in body["models"]


class TestASRWebSocket:
    def test_ru_wav_produces_transcript(self, app, ru_wav_bytes):
        from fastapi.testclient import TestClient
        client = TestClient(app)

        received_messages = []

        with client.websocket_connect("/asr?lang=ru") as ws:
            # Receive config message
            config_msg = ws.receive_json()
            assert config_msg["type"] == "config"

            # Send audio in frames
            frame_bytes = FRAME_SAMPLES * 2  # int16 = 2 bytes/sample
            for i in range(0, len(ru_wav_bytes), frame_bytes):
                frame = ru_wav_bytes[i : i + frame_bytes]
                if len(frame) < frame_bytes:
                    frame = frame + b"\x00" * (frame_bytes - len(frame))
                ws.send_bytes(frame)

            # Send stop signal (empty frame)
            ws.send_bytes(b"")

            # Collect responses until ready_to_stop
            for _ in range(500):  # safety limit
                try:
                    msg = ws.receive_json()
                    received_messages.append(msg)
                    if msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        transcript_msgs = [m for m in received_messages if m.get("type") == "transcript"]
        assert len(transcript_msgs) > 0, "No transcript messages received"

        last = transcript_msgs[-1]
        assert "lines" in last
        assert len(last["lines"]) > 0, "lines[] is empty"

        all_text = " ".join(line["text"] for line in last["lines"])
        assert len(all_text) > 0, "All transcript lines are empty"

    def test_empty_audio_returns_ready_to_stop(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)

        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config
            ws.send_bytes(b"")  # immediate stop
            msg = ws.receive_json()
            assert msg["type"] == "ready_to_stop"

    def test_invalid_lang_closes_with_error(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)

        with client.websocket_connect("/asr?lang=xx") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
