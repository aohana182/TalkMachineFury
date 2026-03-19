# memory.md — Talk Machine Fury

### 2026-03-19 — Initial implementation (all phases)

**What was done:**
All phases (0–5) implemented in one session from the agreed architecture plan.

Phase 0:
- `docs/phase0_checks.py` — runs Gates 0A + 0D, writes `config.toml` and `docs/gigaam_v3_inspection.txt`
- `docs/test_audio_rate.html` — Gate 0B browser check (hardware AudioContext sample rate)
- `config.toml` — template with correct defaults; overwritten by `phase0_checks.py` after benchmarks
- `docs/gigaam_v3_inspection.txt` — placeholder; must be replaced by running `phase0_checks.py`

Phase 1A (server):
- `server/vad.py` — stateful Silero VAD v4 with h/c tensors, discard_rate, should_flush, flush
- `server/asr_ru.py` — GigaAM v3 CTC via onnx-asr; vosk/sherpa-onnx fallback
- `server/asr_en.py` — faster-whisper distil-small.en, beam=1, vad_filter=False
- `server/models.py` — load_models(), model_for_lang()
- `server/transcript.py` — cumulative TranscriptSession, to_dict() protocol
- `tests/test_vad.py` — VAD unit tests with mocked Silero; integration tests with real model
- `tests/test_asr_ru.py`, `tests/test_asr_en.py` — ASR integration tests + RTF check

Phase 2:
- `server/main.py` — FastAPI app: /asr WebSocket, /health, /save. Async receiver+processor with asyncio.Queue, back-pressure, ThreadPoolExecutor for ASR

Phase 1B (extension):
- `extension/manifest.json` — MV3, tabCapture, offscreen, alarms, storage
- `extension/background.js` — lazy offscreen create, chrome.alarms keepalive, message routing
- `extension/worklet.js` — hardware-rate AudioWorklet, lerp downsample to 16kHz, Int16 frames
- `extension/offscreen.html` / `offscreen.js` — getUserMedia-first constraint, WebSocket, frame dispatch
- Phase 1B stub: _sendFrame() logs to console when WS not connected

Phase 3:
- `extension/popup.html` / `popup.js` — state machine (idle→connecting→listening→transcribing→stopped→error→server-offline), auto-scroll, hover pause

Phase 4 artifacts:
- `tests/test_integration.py` — E2E test: latency, discard_rate, recognizable words
- `tests/measure_wer.py` — WER measurement via edit distance

Phase 5:
- `.gitignore`, `README.md`, `server/host.py` (stub), `docs/known_limitations.md`
- `tests/generate_fixtures.py` — TTS fixture generation (gtts + pydub)

**Why:**
Architecture was fully decided in prior session (council + adversarial round).
Implementation follows the spec exactly: stateful VAD, hardware-rate AudioContext,
getUserMedia-first, cumulative lines[] protocol.

**Critical decisions carried into code:**
- VAD h/c state persists across frames — never reset between calls (predecessor bug fix)
- AudioContext has no sampleRate hint — always at hardware rate
- getUserMedia() is the first and only awaited call in the offscreen message handler
- asyncio.Queue(maxsize=200) → back-pressure signal if queue fills
- beam=1 in faster-whisper for CPU speed (acceptable WER tradeoff)

**Open questions / next steps:**
1. Run `docs/phase0_checks.py` — confirms GigaAM v3 availability and sets real thread count
2. Run `docs/test_audio_rate.html` as unpacked extension → note hardware sample rate
3. Test Telemost tab capture during live call (Gate 0C) — determine mic-only vs tab capture
4. Run `python tests/generate_fixtures.py` → create f01_ru_clean.wav, f02_en_clean.wav
5. Run `pytest tests/test_vad.py -v` (unit tests, no model needed)
6. Run `pytest tests/ -v -m integration` after models downloaded
7. Commit with tag v0.1.0 after all integration tests pass

**Known issues to watch:**
- worklet.js lerp downsampler: fracPos tracking at non-integer ratios (44100→16000 = 2.75625x) — verify no drift over long sessions
- offscreen.js: `clients?.matchAll?.()` guard for hasOffscreen() fallback — test on Chrome 116
- asr_ru.py: onnx_asr.load() session_options TypeError catch — verify against actual onnx-asr version installed
