# Talk Machine Fury

Real-time Russian + English meeting transcription. Fully local. No cloud.

**Target hardware:** Intel i7-1355U (15W TDP, Windows 11)
**Models:** GigaAM v3 CTC (Russian) + distil-whisper/distil-small.en (English)

---

## Quick start (< 5 minutes)

### 1. Install Python dependencies

```bash
cd TalkMachineFury
pip install -r server/requirements.txt
```

On first run, models are downloaded automatically (~400MB total):
- Silero VAD v4 (via `onnx-asr`)
- GigaAM v3 CTC (~220MB, via `onnx-asr`)
- distil-whisper/distil-small.en (~166MB, via `faster-whisper`)

### 2. Run Phase 0 verification gates

```bash
python docs/phase0_checks.py
```

This confirms model availability, benchmarks ONNX threading, and writes `config.toml`.
**Required before first use.** Commit `docs/gigaam_v3_inspection.txt` and `config.toml`.

### 3. Start the ASR server

```bash
uvicorn server.main:app --port 8765
```

Verify:
```bash
curl http://localhost:8765/health
# {"status":"ok","models":["ru","en"],...}
```

### 4. Load the Chrome extension

1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the `extension/` folder

### 5. Transcribe

1. Open a meeting tab (Telemost, Google Meet, etc.)
2. Click the Talk Machine Fury icon in the toolbar
3. Select language (RU / EN)
4. Click **Start**
5. Speak — transcript appears within ~1 second of utterance end
6. Click **Stop** when done
7. Click **Save** to copy transcript to clipboard

---

## Running tests

```bash
# Generate test fixtures (TTS, requires gtts + pydub)
python tests/generate_fixtures.py

# Unit tests (no model download required)
pytest tests/test_vad.py -v

# Integration tests (requires models + fixtures)
pytest tests/ -v -m integration

# WER measurement
python tests/measure_wer.py --model ru --corpus tests/wer_corpus/
```

---

## Architecture

```
Chrome Extension (MV3)
  background.js (service worker — thin coordinator)
    → chrome.offscreen API → offscreen.js (session owner)
      → AudioContext at HARDWARE RATE
        → AudioWorkletProcessor (worklet.js)
          → downsample to 16kHz PCM s16le
            → WebSocket → localhost:8765

Python FastAPI Server (localhost:8765)
  /asr WebSocket
    → Silero VAD v4 (ONNX, stateful)
      → GigaAM v3 CTC — Russian ASR
      → distil-whisper/distil-small.en — English ASR
  /health GET
  /save POST
```

---

## Known limitations

See [`docs/known_limitations.md`](docs/known_limitations.md).

Short version:
- Code-switching (Russian + English words) → v1.1
- Telemost may be microphone-only if tabCapture fails → see Gate 0C
- Server must be started manually → native host in v1.1
- No speaker diarization → post-v1

---

## WER targets

| Language | Target WER | Notes |
|----------|------------|-------|
| Russian  | < 25%      | On meeting audio (not clean read speech) |
| English  | < 15%      | distil-small.en baseline |

---

See [CHANGELOG.md](CHANGELOG.md) for version history.
