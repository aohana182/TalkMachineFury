/**
 * background.js — MV3 Service Worker (thin coordinator)
 *
 * Responsibilities:
 *   - Handle popup Start/Stop commands
 *   - Manage offscreen document lifecycle (lazy create, explicit close)
 *   - Keep service worker alive with chrome.alarms at 24s
 *   - Persist session state in chrome.storage.session (survives SW termination)
 *
 * The offscreen document (offscreen.js) is the session owner.
 * The service worker is stateless beyond what's in chrome.storage.session.
 */

const OFFSCREEN_URL = chrome.runtime.getURL('offscreen.html');
const KEEPALIVE_ALARM = 'tmf-keepalive';

// ---------------------------------------------------------------------------
// Service Worker keepalive
// ---------------------------------------------------------------------------

chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 24 / 60 }); // 24s

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === KEEPALIVE_ALARM) {
    // No-op — the alarm firing is enough to keep the SW alive
  }
});

// ---------------------------------------------------------------------------
// Offscreen document helpers
// ---------------------------------------------------------------------------

async function hasOffscreen() {
  if (chrome.offscreen && chrome.offscreen.hasDocument) {
    return chrome.offscreen.hasDocument();
  }
  // Fallback for older Chrome versions
  const clients = await clients?.matchAll?.({ type: 'window', includeUncontrolled: true }) ?? [];
  return clients.some(c => c.url === OFFSCREEN_URL);
}

async function ensureOffscreen() {
  const exists = await hasOffscreen().catch(() => false);
  if (!exists) {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK'],
      justification: 'Capture tab audio and stream PCM to local ASR server',
    });
  }
}

async function closeOffscreen() {
  const exists = await hasOffscreen().catch(() => false);
  if (exists) {
    await chrome.offscreen.closeDocument().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Message routing
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'start-capture') {
    handleStart(msg).then(sendResponse).catch(err => sendResponse({ ok: false, error: err.message }));
    return true; // async
  }

  if (msg.type === 'stop-capture') {
    handleStop().then(sendResponse).catch(err => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg.type === 'transcript-update' || msg.type === 'capture-error' || msg.type === 'ready-to-stop') {
    // Forward to popup (if open)
    chrome.runtime.sendMessage(msg).catch(() => {}); // popup may not be open — ignore
    return false;
  }
});

async function handleStart(msg) {
  const { lang } = msg;

  await chrome.storage.session.set({ tmf_state: 'connecting', tmf_lang: lang });
  await ensureOffscreen();

  // Get streamId for the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');

  const streamId = await new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(id);
    });
  });

  // Send streamId to offscreen — getUserMedia() must be first call there
  const response = await chrome.runtime.sendMessage({
    type: 'start-tab-capture',
    streamId,
    lang,
  });

  if (!response?.ok) throw new Error(response?.error ?? 'Offscreen start failed');

  await chrome.storage.session.set({ tmf_state: 'listening' });
  return { ok: true };
}

async function handleStop() {
  await chrome.storage.session.set({ tmf_state: 'stopping' });

  await chrome.runtime.sendMessage({ type: 'stop-tab-capture' }).catch(() => {});

  // Offscreen will send 'ready-to-stop' when drained; close doc after that
  return { ok: true };
}

// Listen for offscreen drain confirmation before closing doc
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'ready-to-stop') {
    chrome.storage.session.set({ tmf_state: 'idle' });
    closeOffscreen().catch(() => {});
  }
});
