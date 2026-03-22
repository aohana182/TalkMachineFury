/**
 * mic-capture.js — Dedicated visible window for microphone capture.
 *
 * Why a separate window instead of offscreen.js:
 *   Brave (and Chrome) deny getUserMedia in offscreen documents — they have no
 *   visible UI, so the browser cannot show a permission dialog and auto-dismisses
 *   the request. A regular visible window CAN show the dialog and hold the
 *   permission for the duration of the session.
 *
 * Flow:
 *   1. Page loads → getUserMedia → Brave shows permission dialog (visible window).
 *   2. On grant: start AudioWorklet, relay PCM frames to offscreen.js via
 *      chrome.runtime.sendMessage({type:'mic-pcm', buffer: ArrayBuffer}).
 *   3. On 'release-mic' message from background.js: stop tracks and close window.
 */

const statusEl = document.getElementById('status');

let _stream     = null;
let _audioCtx   = null;
let _source     = null;
let _workletNode = null;
let _active     = false;

async function start() {
  try {
    _stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
    });

    statusEl.textContent = '🎤 Mic active';

    _audioCtx = new AudioContext();
    await _audioCtx.audioWorklet.addModule(chrome.runtime.getURL('worklet.js'));

    _workletNode = new AudioWorkletNode(_audioCtx, 'pcm-processor', {
      processorOptions: { inputSampleRate: _audioCtx.sampleRate },
      numberOfInputs: 1,
      numberOfOutputs: 0,
    });

    // Relay each PCM frame to offscreen.js over extension messaging.
    // ArrayBuffer is supported by Chrome's structured-clone serialisation for
    // in-extension messages (Chromium 86+).
    _workletNode.port.onmessage = ({ data }) => {
      if (_active) {
        chrome.runtime.sendMessage({ type: 'mic-pcm', buffer: data }).catch(() => {});
      }
    };

    _source = _audioCtx.createMediaStreamSource(_stream);
    _source.connect(_workletNode);
    _active = true;

    chrome.runtime.sendMessage({ type: 'mic-ready' }).catch(() => {});

  } catch (err) {
    statusEl.textContent = `Mic denied: ${err.message}`;
    chrome.runtime.sendMessage({ type: 'mic-error', error: err.message }).catch(() => {});
  }
}

// background.js sends this when recording stops.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'release-mic') {
    _active = false;
    if (_source)      _source.disconnect();
    if (_audioCtx)    _audioCtx.close().catch(() => {});
    if (_stream)      _stream.getTracks().forEach(t => t.stop());
    window.close();
  }
});

start();
