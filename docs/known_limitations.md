# Known Limitations — Talk Machine Fury v0.5.0

These are documented constraints. They are not bugs.

---

## 1. Code-switching (Russian sentences with English tech terms)

**Symptom:** English words embedded in Russian speech (e.g., "наш pipeline вырос") may be
transcribed incorrectly or dropped. faster-whisper medium handles code-switching better than
GigaAM but still garbles or drops English loan words mid-Russian sentence.

**Scope:** Very common in Russian tech meetings.

**Plan:** Evaluate word-level language detection — route English tokens through distil-small.en.

---

## 2. Telemost tab audio (may be microphone-only)

**Symptom:** If `tabCapture` of the Telemost tab returns near-zero RMS, the extension falls
back to microphone-only mode. Remote participants will not be transcribed.

**Why:** Telemost may route audio through a WebRTC stack that is inaccessible to Chrome's
`tabCapture` API. This depends on the specific Telemost client version and meeting type.

**Verification:** Run Gate 0C (see `docs/phase0_checks.py`) during a live Telemost call.
If RMS ≈ 0, microphone-only is the confirmed behavior.

---

## 3. No speaker diarization

**Symptom:** All transcript lines are attributed to `speaker: 0`. Multiple speakers are
not distinguished.

**Plan:** Post-hoc diarization after Stop is clicked (approach documented in `memory.md`).
Real-time diarization is out of scope — mislabels speakers early in session before enough
audio exists to cluster reliably.

---

## 4. Latency

**Symptom:** Transcript appears in bursts, not word-by-word. Typical lag: 5-15s from speech
to display, depending on pause frequency and segment length.

**Why:** Whisper medium is a full-utterance model — it transcribes complete segments, not
streaming tokens. Segments flush at 1s silence or 25s max. Whisper inference on a 25s
segment takes ~15-25s on i7-1355U CPU.

**Tradeoff:** Longer segments → better WER (more context for Whisper). Shorter segments →
lower latency, higher WER. Current settings (max_speech_s=25, min_silence_ms=1000)
are tuned for WER over latency.

---

## 5. Thermal throttling on long sessions (90+ minutes)

**Symptom:** Transcription latency may increase on 90+ minute meetings as the i7-1355U
throttles under sustained CPU load.

**Verification:** Post-launch load test (not run yet).

**Plan:** Monitor RTF. If RTF exceeds 1.0, emit a warning in the popup.

---

## 6. Linear interpolation downsampler (lerp)

**Symptom:** The AudioWorklet downsampler (`worklet.js`) uses linear interpolation,
which aliases high-frequency content at non-integer ratios (44100→16000 = 2.756x).

**Impact:** Low — meeting speech is band-limited to ~4kHz, well below the Nyquist
limit for 16kHz output. No observed WER regression on clean speech. 48kHz hardware
rate (confirmed on target machine) gives integer 3:1 ratio — zero aliasing.

---

## 7. VC++ Redistributable required on fresh Windows installs

**Symptom:** `install.bat` fails on the `onnxruntime` step with a DLL error.

**Fix:** Install [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) and re-run `install.bat`.
