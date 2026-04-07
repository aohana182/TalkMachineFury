# Talk Machine Fury

Real-time Russian + English meeting transcription. Fully local. No cloud. No lag.

**Replaces:** WhisperScribe (10-25s lag, 60-90% Russian WER, stateless VAD drops)
**Target:** Intel i7-1355U, Windows 11, Brave/Chrome

---

## Requirements

- Windows 10/11
- Python 3.10+ — [python.org](https://python.org) — check "Add Python to PATH" during install
- Brave or Chrome (MV3)
- ~1.6GB disk for models (downloaded during setup — one time only)

---

## Install

```bat
install.bat
```

Creates a venv, installs all deps, downloads models (~1.6GB, takes 5-10 min on first run), creates `C:\Transcripts`. Run once.

---

## Load the extension

`brave://extensions` → Developer mode → Load unpacked → select `extension/`

One-time only. The server starts automatically after this.

---

## Usage

1. Open a meeting tab (Zoom, Telemost, Google Meet)
2. Click the Talk Machine Fury icon → select language → **Start**
3. Grant mic permission when prompted (once, never again)
4. Server starts automatically in the background (first click may take ~15s)
5. Transcript appears in the popup as speech is detected
6. **Stop** → transcript written to `C:\Transcripts\YYYY-MM-DD_HH-MM-SS_<lang>.txt`

Change the save folder in `config.toml` → `[server] transcript_folder`.

---

## Architecture

```
Chrome Extension (MV3)
  background.js    service worker — coordinates start/stop, owns offscreen lifecycle
                   on Start: native host → mic permission → offscreen → tabCapture
  offscreen.js     session owner — AudioContext at hardware rate (48kHz)
                   tab audio (tabCapture) + mic (getUserMedia via content script) → worklet
  worklet.js       AudioWorkletProcessor — lerp 48→16kHz, 512-sample Int16 frames → WebSocket
  popup.js         state machine (idle/connecting/listening/transcribing/stopped/error)

Server (FastAPI localhost:8765)
  host.py          native messaging host — starts uvicorn on demand, no terminal needed
  vad.py           Silero VAD v4 (ONNX, stateful) — segments speech at silence boundaries
                   state tensor (2,1,128) survives across frames — stateless = predecessor bug
  asr_ru.py        faster-whisper medium (multilingual, lang=ru) — Russian ASR, 16.4% WER
  asr_en.py        faster-whisper distil-small.en — English ASR
  main.py          three-stage pipeline: receiver → vad_worker → asr_worker
                   /asr WebSocket (PCM in, cumulative lines[] out)
                   /health GET, /save POST

Data flow:
  tab + mic audio (48kHz PCM)
    → worklet (downsample → 16kHz Int16)
      → WebSocket /asr
        → receiver() → frame_queue
          → vad_worker() [always running, never blocked by ASR]
            → segment_queue
              → asr_worker() [faster-whisper, sequential]
                → cumulative lines[] → popup
                  → auto-written to C:\Transcripts\<filename>.txt
```

---

## Security

| Surface | Status | Notes |
|---------|--------|-------|
| Audio data | ✓ Never leaves the machine | WebSocket is localhost only |
| Transcripts | ✓ Local disk only | `C:\Transcripts\`, no upload |
| Models | ✓ Downloaded once, run offline | faster-whisper, onnx-asr |
| Server port | ⚠ Localhost, no auth | Any process on the machine can POST to `/save` or connect to `/asr` |
| Native host | ⚠ Executes on extension Start | Registered per-user in HKCU, not HKLM |
| Mic capture | ⚠ Captures default mic | No indicator light beyond browser's own mic-in-use icon |
| Tab audio | ⚠ Captures entire tab audio | Including any tab — not scoped to meeting apps |

**Gaps:**
- `/save` accepts arbitrary file paths — a malicious page that can reach `localhost:8765` could write files anywhere on disk. Acceptable for single-user local use; not acceptable if the server is ever exposed beyond localhost.
- No authentication on the WebSocket — any local process can inject audio or read transcripts.

---

## Models

| Model | Task | Why |
|-------|------|-----|
| **faster-whisper medium** (OpenAI/CTranslate2) | Russian ASR | 16.4% WER on real Russian conversational speech (SOVA dataset, 30 samples). Multilingual, runs CPU int8. Segmented by VAD before inference — no streaming required. |
| **faster-whisper distil-small.en** (Systran) | English ASR | Distilled Whisper at 1/6 the size, English-only, CTranslate2 format (CPU-optimized). English-only model avoids language detection overhead. |
| **Silero VAD v4** (snakers4) | Voice activity detection | Gates ASR — only transcribe when speech is present. Stateful ONNX model: state tensor (2,1,128) survives across frames. Stateless operation reproduced the predecessor bug (5-17s silent drops). Downloaded via onnx-asr. |

**Rejected alternatives:**
- GigaAM v3 CTC/RNNT: Russian-native, faster inference (~2-3s/segment), but WER 21-24% — above target
- Whisper large-v3: better WER (~14%), but RTF > 3x on i7-1355U — causes unbounded queue lag
- vosk Zipformer2 (sherpa-onnx): fallback only — lower Russian accuracy, streaming but WER ~25%
- WebSpeech API: cloud-dependent, no offline support

---

## Config

`config.toml` — edit freely except `[inference]` (set by benchmark, change requires re-run):

| Key | Value | Notes |
|-----|-------|-------|
| `vad.threshold` | 0.40 | Speech probability threshold. Lower = more sensitive. Change requires counted measurement. |
| `vad.min_silence_ms` | 1000 | Silence gap that triggers segment flush. 1000ms waits for real sentence boundaries. |
| `vad.min_speech_ms` | 500 | Segments shorter than 500ms are discarded — Whisper hallucinates on sub-second clips. |
| `vad.max_speech_s` | 25.0 | Max segment length before forced flush. Longer = more context for Whisper = better WER. |
| `inference.intra_op_num_threads` | 2 | From threading benchmark (Gate 0D). Do not change without re-running benchmark. |
| `models.russian` | `whisper:medium` | Russian ASR model. See `server/asr_ru.py` for other options. |
| `server.transcript_folder` | `C:/Transcripts` | Where transcripts are written. Safe to change. |

---

## Known Limitations (v0.5)

- Code-switching (Russian sentences with English terms) degrades accuracy — Whisper handles it better than GigaAM but still drops or garbles English loan words mid-Russian sentence
- No speaker diarization — all lines attributed to speaker 0
- Mic selection is OS default — no in-app device picker
- `onnxruntime` requires [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) on fresh Windows installs. If `install.bat` fails on the `onnxruntime` step, install that first and re-run.
- Latency per segment: 5-15s depending on segment length and CPU load. Segments flush at 1s silence or 25s max.

---

## Tests

```bash
python tests/generate_fixtures.py        # one-time: generates TTS WAVs in tests/fixtures/
pytest tests/ -v                         # full suite (~3.5h — dominated by Whisper inference)
python -X utf8 tests/measure_wer.py --model ru --corpus tests/wer_corpus_sova/  # real WER
```

To regenerate the SOVA WER corpus (30 real Russian speech samples, ~3MB):
```bash
python tests/download_sova_corpus.py     # downloads from HuggingFace bond005/sova_rudevices
```

---

See [CHANGELOG.md](CHANGELOG.md) for version history.
