"""
Standalone integration runner — starts server in subprocess, sends fixture, checks output.

Run: python tests/run_integration.py
"""
import asyncio
import json
import pathlib
import subprocess
import sys
import time
import wave

PYTHON = sys.executable
ROOT = pathlib.Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "f01_ru_clean.wav"
FRAME_SAMPLES = 512


def load_wav_frames(path):
    with wave.open(str(path)) as wf:
        raw = wf.readframes(wf.getnframes())
    frame_bytes = FRAME_SAMPLES * 2  # int16
    frames = []
    for i in range(0, len(raw) - frame_bytes + 1, frame_bytes):
        frames.append(raw[i:i + frame_bytes])
    return frames


async def test_pipeline():
    try:
        import websockets
    except ImportError:
        print("pip install websockets")
        sys.exit(1)

    frames = load_wav_frames(FIXTURE)
    print(f"Fixture: {len(frames)} frames ({len(frames)*FRAME_SAMPLES/16000:.1f}s)")

    # Start server
    print("Starting server...")
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "server.main:app", "--port", "8766", "--log-level", "warning"],
        cwd=str(ROOT),
    )

    # Wait for server to be ready
    import urllib.request
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen("http://localhost:8766/health", timeout=2)
            print("Server ready")
            break
        except Exception:
            print(".", end="", flush=True)
    else:
        print("\nServer failed to start")
        proc.terminate()
        sys.exit(1)

    try:
        async with websockets.connect("ws://localhost:8766/asr?lang=ru") as ws:
            config = json.loads(await ws.recv())
            print(f"Config: {config}")

            for i, frame in enumerate(frames):
                await ws.send(frame)
                # Check for backpressure
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.001))
                    print(f"  Server msg during send: {msg}")
                except (asyncio.TimeoutError, Exception):
                    pass

            print("Sending stop signal...")
            await ws.send(b"")

            lines = []
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                except asyncio.TimeoutError:
                    print("TIMEOUT waiting for server response")
                    break

                print(f"  [{msg['type']}]", end=" ")
                if msg["type"] == "transcript":
                    lines = msg["lines"]
                    print(f"{len(lines)} lines")
                    for line in lines:
                        print(f"    > {line['text']}")
                elif msg["type"] == "ready_to_stop":
                    print()
                    break
                elif msg["type"] == "error":
                    print(f"ERROR: {msg.get('message')}")
                    break
                else:
                    print()

        print(f"\n--- RESULT ---")
        if lines:
            print(f"PASS: {len(lines)} lines")
            for line in lines:
                print(f"  {line['text']}")
        else:
            print("FAIL: no transcript lines")
            sys.exit(1)

    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    asyncio.run(test_pipeline())
