"""Quick smoke test — does recognize() return text on real audio?

Run: python docs/test_recognize.py

Uses a 3-second synthetic tone as a sanity check (expect empty or garbage).
If you have tests/fixtures/f01_ru_clean.wav, it tests that too.
"""
import pathlib, wave, time
import numpy as np
import onnx_asr

print("Loading GigaAM v3...")
t0 = time.perf_counter()
model = onnx_asr.load_model("gigaam-v3-e2e-ctc")
print(f"Loaded in {time.perf_counter()-t0:.1f}s")

# Test 1: silence
silence = np.zeros(16000, dtype=np.float32)
result = model.recognize(silence, sample_rate=16000)
print(f"\nSilence (1s):  repr={result!r}  type={type(result).__name__}")

# Test 2: fixture if exists
fixture = pathlib.Path("tests/fixtures/f01_ru_clean.wav")
if fixture.exists():
    with wave.open(str(fixture)) as wf:
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    t0 = time.perf_counter()
    result = model.recognize(audio, sample_rate=16000)
    elapsed = time.perf_counter() - t0
    print(f"\nf01_ru_clean ({len(audio)/16000:.1f}s): {elapsed:.2f}s wall")
    print(f"  result: {result!r}")
else:
    print(f"\n{fixture} not found — run tests/generate_fixtures.py to create it")
    # Synthetic speech-like chirp
    t = np.linspace(0, 2.0, 32000, dtype=np.float32)
    chirp = 0.3 * np.sin(2 * np.pi * (200 + 600*t) * t)
    result = model.recognize(chirp, sample_rate=16000)
    print(f"\nChirp (2s): {result!r}")
