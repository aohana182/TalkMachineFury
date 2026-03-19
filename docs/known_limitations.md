# Known Limitations — Talk Machine Fury v0.1.0

These are documented v1 constraints. They are not bugs.

---

## 1. Code-switching (Russian sentences with English tech terms)

**Symptom:** English words embedded in Russian speech (e.g., "наш pipeline вырос") may be
transcribed incorrectly or dropped. GigaAM v3 CTC is trained primarily on Russian; English
loan words are hit-or-miss.

**Scope:** Very common in Russian tech meetings.

**Plan:** v1.1 — evaluate hybrid approach: detect English word-level switches and route
through distil-small.en for those tokens.

---

## 2. Telemost tab audio (may be microphone-only)

**Symptom:** If `tabCapture` of the Telemost tab returns near-zero RMS, the extension falls
back to microphone-only mode. Remote participants will not be transcribed.

**Why:** Telemost may route audio through a WebRTC stack that is inaccessible to Chrome's
`tabCapture` API. This depends on the specific Telemost client version and meeting type.

**Verification:** Run Gate 0C (see `docs/phase0_checks.py`) during a live Telemost call.
If RMS ≈ 0, microphone-only is the confirmed v1 behavior.

**Plan:** Document in README. Investigate Telemost's audio routing in v1.1.

---

## 3. Server startup is manual

**Symptom:** User must run `uvicorn server.main:app --port 8765` in a terminal before
using the extension. The popup shows "Start the server first" if /health is unreachable.

**Plan:** v1.1 — native messaging host (`server/host.py`) to auto-start the server.

---

## 4. No speaker diarization

**Symptom:** All transcript lines are attributed to `speaker: 0`. Multiple speakers are
not distinguished.

**Plan:** Post-v1 — pyannote.audio or similar, but this requires GPU for real-time use.
Out of scope for the target i7-1355U.

---

## 5. Thermal throttling on long sessions (90+ minutes)

**Symptom:** Transcription latency may increase on 90+ minute meetings as the i7-1355U
throttles under sustained load.

**Verification:** Post-launch load test (not run pre-v1).

**Plan:** Monitor RTF in /health endpoint. If RTF exceeds 0.8, emit a warning in the popup.

---

## 6. Linear interpolation downsampler (lerp)

**Symptom:** The AudioWorklet downsampler (`worklet.js`) uses linear interpolation
(lerp), which aliases high-frequency content at non-integer ratios (44100→16000 = 2.756x).

**Impact on v1:** Low — meeting speech is band-limited to ~4kHz, well below the Nyquist
limit for 16kHz output. No observed WER regression on clean speech.

**Plan:** If WER regression is observed on specific audio, replace with server-side
`scipy.signal.resample()` after PCM receipt.
