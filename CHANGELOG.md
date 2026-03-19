# Changelog

---

## [0.3.0] — 2026-03-19

### Save transcripts to disk

**Problem:** Save button only copied to clipboard. No persistent record. No way to find transcripts later.

**Solution:**
- Server `/save` endpoint writes to `C:\Transcripts\` (configurable in `config.toml`)
- Filename auto-generated: `YYYY-MM-DD_HH-MM_<tab-title>_<lang>.txt`
- Tab title captured from active tab at session start — gives meaningful names automatically
- Folder created on first save if it doesn't exist
- `/health` exposes `transcript_folder` so popup knows where files go without hardcoding

---

## [0.2.0] — 2026-03-19

### Microphone capture

**Problem:** Extension only captured tab audio (remote speakers). User's own voice not transcribed.

**Root cause:** Chrome/Brave silently blocks `getUserMedia` for microphone from offscreen documents — no dialog, no error. Offscreen docs have no visible UI so the browser treats them as untrusted for permission prompts.

**Solution:**
- `mic-permission.html` + `mic-permission.js`: minimal 1×1 popup window opened at session start
- The visible window triggers the mic permission dialog (once, then remembered)
- After grant, offscreen.js connects mic stream to the same AudioWorklet as tab audio
- Both streams mixed in Web Audio graph before downsampling

### Tab audio disappearing during capture

**Problem:** User couldn't hear the tab while extension was active.

**Root cause:** Chrome routes captured tab audio away from speakers when the stream is consumed by an AudioContext. Connecting `source → ctx.destination` in an offscreen document suspends the AudioContext on some Chrome versions.

**Solution:** `<audio>.srcObject = stream` passthrough — plays tab audio back to speakers via a separate path, independent of the AudioContext.

### 30-second latency / back-pressure

**Problem:** Transcript appeared every 30 seconds in large bursts, then frames started dropping.

**Root cause:** `max_speech_s` was never wired from `config.toml` to `VADSession`. It defaulted to 30s. GigaAM processing 30s of audio takes ~8s on CPU. During those 8s, the 200-frame queue filled up and frames were dropped.

**Solution:** `max_speech_s = 10` limits segments to ~2s ASR time. `queue_maxsize` 200 → 600 for headroom.

---

## [0.1.0] — 2026-03-19

### Initial working pipeline

**Problem:** WhisperScribe failed in production:
- 10-25s lag (Whisper 30s fixed decode window)
- 5-17s silent drops (stateless VAD — state reset between frames reproduced same bug every session)
- Russian WER 60-90% (Whisper trained primarily on English)

**Solution — model layer:**
- GigaAM v3 CTC via `onnx-asr` for Russian (~20-30% WER on meeting audio vs 60-90%)
- distil-whisper distil-small.en via `faster-whisper` for English
- Silero VAD v4 (ONNX) with stateful (2,1,128) tensor — state survives across frames

**Solution — transport layer:**
- Chrome MV3 offscreen document owns AudioContext (can't live in service worker)
- AudioWorklet at hardware rate (48kHz confirmed), lerp 3:1 downsample to 16kHz
- `getUserMedia` for tab audio must be first call in offscreen message handler — streamId expires on any prior await
- WebSocket asyncio queue with back-pressure signal
- Cumulative `lines[]` protocol (not deltas) — eliminates deduplication bug from WhisperScribe

**Gate 0 results (locked in config.toml):**
- GigaAM v3 exists in onnx-asr as `gigaam-v3-e2e-ctc` ✓
- Hardware sample rate = 48kHz (3:1 exact integer ratio — zero lerp aliasing) ✓
- Telemost tab audio present and healthy ✓
- `intra_op_num_threads = 2` optimal on i7-1355U ✓

---

## Next

- High-load testing: 90-min continuous session, thermal throttling, memory leak check
- Mic device picker (currently OS default)
- Speaker diarization (post-v1)
- Auto-start server (native messaging host)
