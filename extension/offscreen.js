/**
 * offscreen.js — Session owner for audio capture and WebSocket.
 *
 * Critical constraints (from architecture council):
 *   1. AudioContext must use HARDWARE sample rate — no sampleRate hint.
 *   2. getUserMedia() must be FIRST call in the message handler. No await before it.
 *      The tabCapture streamId expires if any async operation precedes getUserMedia().
 *   3. WebSocket is initialized after getUserMedia() resolves.
 *
 * WebSocket streams PCM frames to the local ASR server (ws://localhost:8765/asr).
 */

const SERVER_URL = 'ws://localhost:8765/asr';

let _audioCtx = null;
let _workletNode = null;
let _stream = null;
let _ws = null;
let _lang = 'ru';
let _isRunning = false;

// ---------------------------------------------------------------------------
// Message handler — getUserMedia MUST be the first call, no await before it
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'start-tab-capture') {
    if (_isRunning) {
      // Already running — tear down existing pipeline before starting new one.
      _cleanup();
    }
    // CRITICAL: navigator.mediaDevices.getUserMedia() is called synchronously
    // (no await preceding it). The streamId expires if we yield first.
    _lang = msg.lang || 'ru';

    navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: msg.streamId,
        },
      },
      video: false,
    })
      .then(stream => {
        _stream = stream;
        return _initPipeline(stream);
      })
      .then(() => sendResponse({ ok: true }))
      .catch(err => {
        console.error('[TMF offscreen] getUserMedia failed:', err);
        sendResponse({ ok: false, error: err.message });
      });

    return true; // async sendResponse
  }

  if (msg.type === 'stop-tab-capture') {
    _stop();
    sendResponse({ ok: true });
    return false;
  }
});

// ---------------------------------------------------------------------------
// Audio pipeline
// ---------------------------------------------------------------------------

async function _initPipeline(stream) {
  // AudioContext at hardware rate — do NOT specify sampleRate
  _audioCtx = new AudioContext();
  console.log('[TMF offscreen] AudioContext sample rate:', _audioCtx.sampleRate);

  await _audioCtx.audioWorklet.addModule(chrome.runtime.getURL('worklet.js'));

  _workletNode = new AudioWorkletNode(_audioCtx, 'pcm-processor', {
    processorOptions: { inputSampleRate: _audioCtx.sampleRate },
    numberOfInputs: 1,
    numberOfOutputs: 0,
  });

  _workletNode.port.onmessage = ({ data }) => {
    if (_isRunning) {
      _sendFrame(data);
    }
  };

  // Tab audio → worklet
  const tabSource = _audioCtx.createMediaStreamSource(stream);
  tabSource.connect(_workletNode);

  // Detect silent stream dropout. Chrome can silently kill the audio track
  // without triggering a WebSocket error or AudioContext event.
  stream.getAudioTracks().forEach(track => {
    track.addEventListener('ended', () => {
      console.warn('[TMF offscreen] Audio track ended — stream disconnected');
      if (_isRunning) {
        chrome.runtime.sendMessage({ type: 'capture-error', error: 'Audio stream disconnected' }).catch(() => {});
        _isRunning = false;
      }
    });
    track.addEventListener('mute', () => {
      console.warn('[TMF offscreen] Audio track muted');
    });
  });

  // Mic PCM arrives via chrome.runtime messages from mic-capture.html (see bottom of file).
  // mic-capture.html is a visible window that holds the mic stream for the session.

  // Passthrough: play tab stream back to speakers so the user can still hear the tab.
  // We use an <audio> element (not ctx.destination) — connecting to ctx.destination
  // suspends the AudioContext in offscreen documents on some Chrome versions.
  const passthrough = document.getElementById('passthrough');
  if (passthrough) {
    passthrough.srcObject = stream;
  }

  _initWebSocket();
  _isRunning = true;
}

// ---------------------------------------------------------------------------
// Mic PCM relay — frames arrive from mic-capture.html via extension messages
// ---------------------------------------------------------------------------

// mic-content.js (injected into the meeting tab) owns the mic stream and sends
// each PCM frame here via structured clone. We forward it over the same WebSocket.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'mic-pcm' && _isRunning) {
    _sendFrame(msg.samples); // ArrayBuffer via structured clone — no conversion needed
  }
});

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function _initWebSocket() {
  const url = `${SERVER_URL}?lang=${_lang}`;
  console.log('[TMF offscreen] Connecting WebSocket:', url);

  _ws = new WebSocket(url);
  _ws.binaryType = 'arraybuffer';

  _ws.onopen = () => {
    console.log('[TMF offscreen] WebSocket connected');
    chrome.runtime.sendMessage({ type: 'ws-connected' }).catch(() => {});
  };

  _ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === 'transcript') {
      chrome.runtime.sendMessage({ type: 'transcript-update', data: msg }).catch(() => {});
    } else if (msg.type === 'ready_to_stop') {
      chrome.runtime.sendMessage({ type: 'ready-to-stop' }).catch(() => {});
      _cleanup();
    } else if (msg.type === 'backpressure') {
      console.warn('[TMF offscreen] Server back-pressure — queue full');
    } else if (msg.type === 'error') {
      console.error('[TMF offscreen] Server error:', msg.message);
      chrome.runtime.sendMessage({ type: 'capture-error', error: msg.message }).catch(() => {});
    }
  };

  _ws.onerror = (err) => {
    console.error('[TMF offscreen] WebSocket error:', err);
    chrome.runtime.sendMessage({ type: 'capture-error', error: 'WebSocket error' }).catch(() => {});
  };

  _ws.onclose = (evt) => {
    console.log('[TMF offscreen] WebSocket closed', evt.code, evt.reason);
    if (_isRunning) {
      chrome.runtime.sendMessage({ type: 'capture-error', error: 'Connection lost' }).catch(() => {});
      _isRunning = false;
    }
  };
}

function _sendFrame(buffer) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(buffer);
  } else {
    // Phase 1B stub: log frame to console when WebSocket not connected
    console.log(`[TMF offscreen] PCM frame: ${buffer.byteLength} bytes @ ${Date.now()}`);
  }
}

// ---------------------------------------------------------------------------
// Stop
// ---------------------------------------------------------------------------

function _stop() {
  _isRunning = false;

  if (_ws && _ws.readyState === WebSocket.OPEN) {
    // Send empty frame as stop signal
    _ws.send(new ArrayBuffer(0));
    // Cleanup happens when server sends ready_to_stop
  } else {
    _cleanup();
    chrome.runtime.sendMessage({ type: 'ready-to-stop' }).catch(() => {});
  }
}

function _cleanup() {
  if (_workletNode) {
    _workletNode.disconnect();
    _workletNode = null;
  }
  if (_audioCtx) {
    _audioCtx.close().catch(() => {});
    _audioCtx = null;
  }
  if (_stream) {
    _stream.getTracks().forEach(t => t.stop());
    _stream = null;
  }
  // Mic stream is owned by mic-capture.html — released by background.js on stop.
  if (_ws) {
    if (_ws.readyState === WebSocket.OPEN) _ws.close();
    _ws = null;
  }
  _isRunning = false;
}
