"""
Multi-segment pipeline test.

Validates the core architectural guarantee of the three-stage pipeline:
vad_worker must continue processing frames while asr_worker is blocked in Whisper.

The test sends two speech segments separated by silence at full speed (no real-time pacing).
Both segments must reach segment_queue via vad_worker independently of when ASR finishes.

In the old single-processor architecture (processor() coupled VAD + ASR), this test
would fail: processor() blocked in run_in_executor during segment 1, causing frame_queue
to saturate and segment 2 frames to be dropped.

Run:
    pytest tests/test_pipeline_multisegment.py -v -m integration
"""
import pathlib
import wave

import numpy as np
import pytest

pytestmark = pytest.mark.integration

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FRAME_SAMPLES = 512
SAMPLE_RATE = 16000


def _load_wav_bytes(path: pathlib.Path) -> bytes:
    with wave.open(str(path), "r") as wf:
        assert wf.getframerate() == SAMPLE_RATE, f"{path}: must be 16kHz"
        assert wf.getnchannels() == 1, f"{path}: must be mono"
        return wf.readframes(wf.getnframes())


def _silence_bytes(duration_s: float) -> bytes:
    """Generate silent PCM frames."""
    n_samples = int(duration_s * SAMPLE_RATE)
    return b"\x00" * (n_samples * 2)  # Int16 = 2 bytes/sample


def _send_pcm(ws, raw_bytes: bytes) -> None:
    """Send PCM bytes as 512-sample frames."""
    frame_bytes = FRAME_SAMPLES * 2
    for i in range(0, len(raw_bytes), frame_bytes):
        frame = raw_bytes[i : i + frame_bytes]
        if len(frame) < frame_bytes:
            frame = frame + b"\x00" * (frame_bytes - len(frame))
        ws.send_bytes(frame)


@pytest.fixture(scope="module")
def client():
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
    return _load_wav_bytes(p)


class TestMultiSegmentPipeline:
    """
    Validates three-stage pipeline decoupling: VAD continues while ASR runs.

    Audio layout:  [speech_A: 14.6s] [silence: 1.5s] [speech_B: 14.6s] [stop]

    1.5s silence > min_silence_ms=1000ms → VAD flushes segment A at the silence boundary.
    Segment B accumulates in vad_worker while asr_worker processes segment A.
    Both segments must produce transcript output.
    """

    def test_two_segments_produce_two_lines(self, client, ru_wav_bytes):
        """Both speech segments must produce independent transcript lines."""
        transcript_updates = []
        backpressure_count = 0

        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config

            # Segment A
            _send_pcm(ws, ru_wav_bytes)
            # Silence gap — forces VAD flush of segment A
            _send_pcm(ws, _silence_bytes(1.5))
            # Segment B
            _send_pcm(ws, ru_wav_bytes)
            # Stop
            ws.send_bytes(b"")

            for _ in range(2000):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "transcript":
                        transcript_updates.append(msg)
                    elif msg["type"] == "backpressure":
                        backpressure_count += 1
                    elif msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        final = transcript_updates[-1] if transcript_updates else None
        line_count = len(final["lines"]) if final else 0

        assert line_count >= 2, (
            f"Expected ≥2 transcript lines (one per speech segment), got {line_count}. "
            f"Received {len(transcript_updates)} transcript updates. "
            "This indicates vad_worker failed to process segment B while "
            "asr_worker was blocked on segment A — pipeline not properly decoupled."
        )

    def test_no_frame_loss_under_two_segments(self, client, ru_wav_bytes):
        """frame_queue must not saturate when sending two segments back-to-back."""
        backpressure_count = 0

        with client.websocket_connect("/asr?lang=ru") as ws:
            ws.receive_json()  # config

            _send_pcm(ws, ru_wav_bytes)
            _send_pcm(ws, _silence_bytes(1.5))
            _send_pcm(ws, ru_wav_bytes)
            ws.send_bytes(b"")

            for _ in range(2000):
                try:
                    msg = ws.receive_json()
                    if msg["type"] == "backpressure":
                        backpressure_count += 1
                    elif msg["type"] == "ready_to_stop":
                        break
                except Exception:
                    break

        assert backpressure_count == 0, (
            f"frame_queue saturated {backpressure_count} times — "
            "vad_worker is not draining fast enough or is blocked."
        )
