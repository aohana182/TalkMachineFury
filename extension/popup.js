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
      statusBar.textContent = 'Server offline — click Start to launch automatically';
      statusBar.className = 'warning';
      btnStart.disabled = false;  // Start triggers native host auto-launch
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
      btnStart.disabled = false;
      break;
  }

  renderTranscript();
}

function formatTs(unixSec) {
  const d = new Date(unixSec * 1000);
  return d.toTimeString().slice(0, 8); // HH:MM:SS local time
}

function renderTranscript() {
  if (_transcript.length === 0) {
    transcript.innerHTML = '<div class="placeholder">Transcript will appear here.</div>';
    return;
  }
  transcript.innerHTML = _transcript
    .map(l => `<div class="line"><span class="ts">${formatTs(l.ts)}</span>${escapeHtml(l.text)}</div>`)
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

  // Do NOT pre-check health here. background.js ensureServer() contacts the
  // native host which starts the server and waits up to 30s for it to be ready.
  // A pre-flight health check with a 1.5s timeout would race and lose against
  // that 30s startup, re-blocking the UI before the server is up.
  const lang = langSelect.value;
  const response = await chrome.runtime.sendMessage({ type: 'start-capture', lang });
  if (!response?.ok) {
    statusBar.className = 'error';
    statusBar.textContent = `Error: ${response?.error ?? 'Unknown'}`;
    _state = 'error';
    btnStart.disabled = false;
  }
  // On success stay in 'connecting' — the 'ws-connected' message from offscreen.js
  // advances the state to 'listening' once the WebSocket handshake completes.
});

btnStop.addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'stop-capture' });
  setState('stopped');
});

btnSave.addEventListener('click', async () => {
  const text = _transcript.map(l => `[${formatTs(l.ts)}] ${l.text}`).join('\n');

  // Build filename: YYYY-MM-DD_HH-MM_<tab-title>_<lang>.txt
  const { tmf_tab_title, tmf_lang } = await chrome.storage.session.get(['tmf_tab_title', 'tmf_lang']);
  const ts = new Date().toISOString().slice(0, 16).replace('T', '_').replace(':', '-');
  const slug = (tmf_tab_title || 'meeting')
    .replace(/[\\/:*?"<>|]/g, '')   // strip illegal filename chars
    .replace(/\s+/g, '_')
    .slice(0, 60);
  const lang = tmf_lang || 'ru';
  const filename = `${ts}_${slug}_${lang}.txt`;

  try {
    const r = await fetch('http://localhost:8765/health');
    const { transcript_folder } = await r.json();
    const path = `${transcript_folder}/${filename}`;

    const resp = await fetch('http://localhost:8765/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, path }),
    });
    const result = await resp.json();
    if (result.error) throw new Error(result.error);
    statusBar.textContent = `Saved: ${path}`;
  } catch (e) {
    statusBar.textContent = `Save failed: ${e.message}`;
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
  if (msg.type === 'ws-connected') {
    // WebSocket handshake confirmed — advance from 'connecting' to 'listening'.
    if (_state === 'connecting') setState('listening');
    return false;
  }

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

  if (tmf_state === 'listening') {
    setState('listening');
  } else if (tmf_state === 'connecting') {
    // WebSocket not yet confirmed open — stay in connecting until ws-connected fires.
    setState('connecting');
  } else if (tmf_state === 'stopping') {
    setState('stopped');
  } else {
    setState('idle');
  }
}

init();
