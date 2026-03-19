# Changelog

## [0.2.0] — 2026-03-19

### Added
- Microphone capture alongside tab audio — both streams mixed in AudioWorklet
- `mic-permission.html` / `mic-permission.js` — tiny visible window that primes
  Chrome/Brave mic permission before offscreen document tries to use it
- Audio passthrough — user hears tab audio normally during capture (`<audio>.srcObject`)
- VAD flush diagnostics — server logs RMS + duration per segment
- Integration test (`tests/run_integration.py`) — spins up server, sends fixture, asserts transcript
- Test fixtures (`tests/fixtures/`) — Russian and English TTS WAVs + silence

### Fixed
- `lines=0` despite healthy audio — `max_speech_s` was never wired from config to VADSession,
  causing all segments to hit the 30s default and fill the queue with back-pressure
- Tab audio disappearing during capture — Chrome mutes captured tabs; fixed with `<audio>.srcObject`
  passthrough instead of `ctx.destination` (which suspends AudioContext in offscreen docs)
- Back-pressure dropping frames — `queue_maxsize` 200 → 600, `max_speech_s` 30 → 10s

### Changed
- `config.toml`: added `max_speech_s = 10.0`, `queue_maxsize` 200 → 600

---

## [0.1.0] — 2026-03-19

### Added
- Full server pipeline: Silero VAD → GigaAM v3 CTC (Russian) + distil-whisper (English)
- Chrome MV3 extension: offscreen document, AudioWorklet, tabCapture, WebSocket
- 48kHz → 16kHz lerp downsampler in `worklet.js` (3:1 exact integer ratio)
- Stateful VAD (`vad.py`) — single (2,1,128) state tensor + 64-sample context window
- Cumulative `lines[]` protocol — eliminates deduplication bug from WhisperScribe
- Service worker keepalive via `chrome.alarms` at 24s
- `/health`, `/asr`, `/save` endpoints
- Popup UI with state machine: idle → connecting → listening → transcribing → stopped
- `config.toml` with Gate 0 results: `intra_op_num_threads=2`, `hardware_sample_rate=48000`

### Notes
- Replaces WhisperScribe (10-25s lag, 60-90% Russian WER, stateless VAD drops)
- Gate 0 confirmed: GigaAM v3 exists in onnx-asr, hardware rate 48kHz, Telemost tab audio present
