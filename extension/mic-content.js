/**
 * mic-content.js — Injected into the active meeting tab to capture microphone.
 *
 * Why a content script instead of offscreen.js or a popup window:
 *   Brave denies getUserMedia in offscreen documents (no visible UI → auto-dismissed).
 *   A popup window requires user interaction and appears as an intrusive extra window.
 *   A content script runs inside the meeting tab which already has mic permission
 *   (Telemost, Zoom, Meet, Teams all request mic themselves) — no extra dialog needed.
 *
 * Flow:
 *   1. background.js injects this script via chrome.scripting.executeScript on Start.
 *   2. getUserMedia runs in the tab's permission context → succeeds silently.
 *   3. PCM frames are relayed to offscreen.js via chrome.runtime.sendMessage.
 *   4. On 'release-mic' from background.js: stop tracks and clean up.
 */

// Guard against double-injection if Start is clicked twice without Stop.
if (!window.__tmfMicActive) {
  window.__tmfMicActive = true;

  let _stream     = null;
  let _audioCtx   = null;
  let _source     = null;
  let _workletNode = null;
  let _active     = false;

  navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    video: false,
  }).then(async (stream) => {
    _stream   = stream;
    _audioCtx = new AudioContext();

    // worklet.js is web-accessible so it can be loaded from the extension origin.
    await _audioCtx.audioWorklet.addModule(chrome.runtime.getURL('worklet.js'));

    _workletNode = new AudioWorkletNode(_audioCtx, 'pcm-processor', {
      processorOptions: { inputSampleRate: _audioCtx.sampleRate },
      numberOfInputs: 1,
      numberOfOutputs: 0,
    });

    _workletNode.port.onmessage = ({ data }) => {
      if (!_active) return;
      // ArrayBuffer is not JSON-serializable — chrome.runtime.sendMessage uses JSON.
      // Send as Int16 array instead; offscreen.js converts back to ArrayBuffer.
      // Wrap in try-catch: sendMessage throws synchronously when extension context is
      // invalidated (e.g. after reload). .catch() alone doesn't stop synchronous throws.
      try {
        chrome.runtime.sendMessage({
          type: 'mic-pcm',
          samples: Array.from(new Int16Array(data)),
        }).catch(() => {});
      } catch (_) {
        _active = false; // context gone — stop sending, end the error storm
      }
    };

    _source = _audioCtx.createMediaStreamSource(stream);
    _source.connect(_workletNode);
    _active = true;

    chrome.runtime.sendMessage({ type: 'mic-ready' }).catch(() => {});

  }).catch((err) => {
    window.__tmfMicActive = false;
    chrome.runtime.sendMessage({ type: 'mic-error', error: err.message }).catch(() => {});
  });

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'release-mic') {
      _active = false;
      if (_source)      _source.disconnect();
      if (_audioCtx)    _audioCtx.close().catch(() => {});
      if (_stream)      _stream.getTracks().forEach(t => t.stop());
      window.__tmfMicActive = false;
    }
  });
}
