/**
 * popup.js — Popup UI state machine.
 *
 * States: idle → connecting → listening → transcribing → stopped
 *                                                       → error
 *                                                       → server-offline
 *
 * The popup is ephemeral (destroyed when closed). State is reconstructed
 * from chrome.storage.session on open.
 */

const SERVER_HEALTH_URL = 'http://localhost:8765/health';
const HEALTH_TIMEOUT_MS = 1500;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const statusBar  = document.getElementById('status-bar');
const transcript = document.getElementById('transcript');
const langSelect = document.getElementById('lang-select');
const btnStart   = document.getElementById('btn-start');
const btnStop    = document.getElementById('btn-stop');
const btnSave    = document.getElementById('btn-save');

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

/** @type {'idle'|'connecting'|'listening'|'transcribing'|'stopped'|'error'|'server-offline'} */
let _state = 'idle';
let _transcript = [];   // [{text, ts}]
let _autoScroll = true;

function setState(next) {
  _state = next;
  render();
}

function render() {
  // Reset button states
  btnStart.disabled = true;
  btnStop.disabled  = true;
  btnSave.disabled  = true;
  langSelect.disabled = false;

  statusBar.className = '';
  statusBar.textContent = '';

  switch (_state) {
    case 'server-offline':
      statusBar.textContent = 'Start the server first:  uvicorn server.main:app --port 8765';
      statusBar.className = 'warning';
      // Start disabled — user must fix server first
      break;

    case 'idle':
      statusBar.textContent = 'Ready';
      btnStart.disabled = false;
      if (_transcript.length > 0) btnSave.disabled = false;
      break;

    case 'connecting':
      statusBar.textContent = 'Connecting...';
      statusBar.className = 'active';
      langSelect.disabled = true;
      break;

    case 'listening':
      statusBar.textContent = 'Listening...';
      statusBar.className = 'active';
      btnStop.disabled = false;
      langSelect.disabled = true;
      break;

    case 'transcribing':
      statusBar.textContent = 'Transcribing...';
      statusBar.className = 'active';
      btnStop.disabled = false;
      langSelect.disabled = true;
      break;

    case 'stopped':
      statusBar.textContent = 'Stopped';
      btnStart.disabled = false;
      if (_transcript.length > 0) btnSave.disabled = false;
      break;

    case 'error':
      statusBar.className = 'error';
      break;
  }

  renderTranscript();
}

function renderTranscript() {
  if (_transcript.length === 0) {
    transcript.innerHTML = '<div class="placeholder">Transcript will appear here.</div>';
    return;
  }
  transcript.innerHTML = _transcript
    .map(l => `<div class="line">${escapeHtml(l.text)}</div>`)
    .join('');

  if (_autoScroll) {
    transcript.scrollTop = transcript.scrollHeight;
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Server health check
// ---------------------------------------------------------------------------

async function checkServer() {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), HEALTH_TIMEOUT_MS);
    const r = await fetch(SERVER_HEALTH_URL, { signal: ctrl.signal });
    clearTimeout(timeout);
    return r.ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------

btnStart.addEventListener('click', async () => {
  setState('connecting');

  const alive = await checkServer();
  if (!alive) {
    setState('server-offline');
    return;
  }

  // Request mic permission from popup (visible page — Chrome shows the dialog here).
  // The stream is released immediately; offscreen.js opens its own.
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach(t => t.stop());
  } catch (e) {
    console.warn('[TMF popup] Mic permission denied — only tab audio will be transcribed');
  }

  const lang = langSelect.value;
  const response = await chrome.runtime.sendMessage({ type: 'start-capture', lang });
  if (response?.ok) {
    setState('listening');
  } else {
    statusBar.className = 'error';
    statusBar.textContent = `Error: ${response?.error ?? 'Unknown'}`;
    _state = 'error';
    btnStart.disabled = false;
  }
});

btnStop.addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'stop-capture' });
  setState('stopped');
});

btnSave.addEventListener('click', async () => {
  const text = _transcript.map(l => l.text).join('\n');
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const filename = `transcript_${ts}.txt`;

  // Use downloads API to save (requires downloads permission)
  // Fallback: copy to clipboard
  try {
    await navigator.clipboard.writeText(text);
    statusBar.textContent = 'Copied to clipboard';
  } catch {
    statusBar.textContent = 'Save failed — copy manually';
  }
});

// ---------------------------------------------------------------------------
// Auto-scroll pause on hover
// ---------------------------------------------------------------------------

transcript.addEventListener('mouseenter', () => { _autoScroll = false; });
transcript.addEventListener('mouseleave', () => { _autoScroll = true; });

// ---------------------------------------------------------------------------
// Background message listener
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'transcript-update') {
    const { lines } = msg.data;
    _transcript = lines.map(l => ({ text: l.text, ts: l.ts }));
    if (_state === 'listening') setState('transcribing');
    else renderTranscript();
    return false;
  }

  if (msg.type === 'ready-to-stop') {
    setState('stopped');
    return false;
  }

  if (msg.type === 'capture-error') {
    _state = 'error';
    statusBar.className = 'error';
    statusBar.textContent = `Error: ${msg.error}`;
    btnStart.disabled = false;
    render();
    return false;
  }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  // Restore state from storage (SW may have been restarted)
  const { tmf_state, tmf_lang } = await chrome.storage.session.get(['tmf_state', 'tmf_lang']);

  if (tmf_lang) langSelect.value = tmf_lang;

  // Check server health first
  const serverAlive = await checkServer();

  if (!serverAlive) {
    setState('server-offline');
    return;
  }

  if (tmf_state === 'listening' || tmf_state === 'connecting') {
    setState('listening');
  } else if (tmf_state === 'stopping') {
    setState('stopped');
  } else {
    setState('idle');
  }
}

init();
