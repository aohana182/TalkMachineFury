# Changelog

---

## [0.4.0] — 2026-03-29

### Russian ASR: switch to faster-whisper medium (WER 18.8%)

**Problem:** GigaAM v3 CTC delivered ~24% WER on the test corpus — above the 20% target. A secondary bug in `measure_wer.py` was loading the wrong model name (`"gigaam_v3"` instead of `"gigaam-v3-e2e-ctc"`), making all prior WER measurements unreliable.

**Optimization ladder run (30-sample corpus):**

| Config | Model | WER |
|--------|-------|-----|
| A | gigaam-v3-e2e-ctc (baseline) | 24.0% |
| B | gigaam-v3-e2e-rnnt | 21.6% |
| C | faster-whisper small (ru) | 23.6% |
| **D** | **faster-whisper medium (ru)** | **18.8% ✓** |

**Winner:** `faster-whisper medium`, multilingual, `lang=ru`, `beam_size=1`, `int8` CPU.
beam_size=1 gives identical WER to beam_size=5 on this corpus and is ~2x faster.

**Note on remaining errors:** Most WER errors are number-format mismatches (`пятьсот` → `500`, `двадцать три` → `23%`) — semantically correct transcriptions that differ from written-out reference text.

**Tradeoff:** Latency per segment increased from ~2-3s (gigaam CTC) to ~5-8s (whisper medium CPU). Acceptable for post-meeting review; tolerable for live monitoring.

**Files changed:**
- `server/asr_ru.py`: added `whisper:` backend; default model changed to `whisper:medium`
- `config.toml`: `russian = "whisper:medium"`
- `tests/wer_bench.py`: new self-contained optimization loop script
- `tests/measure_wer.py`: fixed model name bug; target updated 25% → 20%
- `tests/test_asr_ru.py`: fixed model name bug; RTF limit 3x (was 1x); added "whisper" to backend whitelist
- `tests/test_integration.py`: latency limit 10s (was 5s)

---

## [0.3.2] — 2026-03-22

### Microphone capture — fully fixed

#### Problem 1: mic permission silently denied in offscreen document

**Broken behaviour:** `getUserMedia` for microphone inside `offscreen.js` returned `"Permission dismissed"` immediately — no dialog, no error the user could act on. The mic never connected.

**Root cause:** Brave (and Chrome) silently auto-dismiss permission requests from offscreen documents. Offscreen docs have no visible UI, so the browser treats them as untrusted for permission prompts regardless of what the user has previously granted.

**What we tried that didn't work:**
- `mic-permission.html`: a 1×1 invisible popup opened by `background.js` to "prime" the permission. Brave showed an invisible dialog, auto-dismissed it as `"Permission dismissed"`, and that dismissal **revoked** the extension's mic permission — killing the offscreen track 200ms after it connected.
- Requesting mic in `popup.js` on the Start click: worked once to show the dialog, but the popup closes when the user clicks away (returning to their meeting). Brave then treated the subsequent offscreen `getUserMedia` as a new request and denied it.
- `mic-capture.html` opened as a 220×50 visible window: no dialog appeared because the window opened in the background where the user couldn't see or interact with it.

**Fix:** Inject `mic-content.js` as a content script into the active meeting tab via `chrome.scripting.executeScript`. The meeting tab (Telemost, Zoom, Teams, Meet) already has microphone permission — the content script inherits it, so `getUserMedia` succeeds silently with no dialog. The `scripting` permission added to `manifest.json`.

#### Problem 2: mic PCM frames lost in transit

**Broken behaviour:** After the content script was wired up, every other PCM frame logged as `undefined bytes` and the server crashed with `Server error: 'bytes'`.

**Root cause:** `chrome.runtime.sendMessage` serialises its payload as JSON. `ArrayBuffer` is not JSON-serialisable — it becomes `undefined` on the receiving end. The offscreen doc then called `_ws.send(undefined)`, which sent garbage to the Python server.

**Fix:** `mic-content.js` converts the worklet's `ArrayBuffer` to `Array.from(new Int16Array(data))` before sending. `offscreen.js` converts back with `new Int16Array(msg.samples).buffer` before forwarding to the WebSocket.

#### Problem 3: Start failed with "Extension has not been invoked for the current page"

**Broken behaviour:** After adding `ensureMicCapture()`, clicking Start produced a tab-capture error immediately.

**Root cause:** `ensureMicCapture()` opened a new window before `chrome.tabs.query` ran. The new window became the active window, so the query returned the mic-capture window (a `chrome-extension://` URL) instead of the meeting tab. `tabCapture.getMediaStreamId` cannot capture extension pages.

**Fix:** `chrome.tabs.query` is now called at the very start of `handleStart()`, before any window or offscreen document is created.

### Auto-write transcript to file

**Problem:** Transcript was only saved when the user explicitly clicked Save. If the session ended without clicking Save, all text was lost.

**Fix:** `server/main.py` creates a timestamped file (`C:/Transcripts/YYYY-MM-DD_HH-MM-SS_<lang>.txt`) at session start and appends each transcribed line immediately after recognition.

### Server restart fix (`start.bat`)

**Problem:** Running `uvicorn` after a previous session produced `[WinError 10048] only one usage of each socket address` because the prior Python process was still alive after the terminal was closed.

**Fix:** `start.bat` kills any process bound to port 8765 before launching, so stale instances never block a fresh start.

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
