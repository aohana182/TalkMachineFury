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
    }).catch((e) => {
      // If document already exists (race between hasDocument and createDocument), proceed.
      if (!e?.message?.includes('single offscreen document')) throw e;
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

  if (msg.type === 'transcript-update' || msg.type === 'capture-error'
      || msg.type === 'ready-to-stop' || msg.type === 'ws-connected') {
    if (msg.type === 'ws-connected') {
      // WebSocket is confirmed open — persist so popup restores correctly on reopen.
      chrome.storage.session.set({ tmf_state: 'listening' });
    }
    chrome.runtime.sendMessage(msg).catch(() => {}); // popup may not be open — ignore
    return false;
  }
});

async function ensureMicCapture(tabId) {
  // Inject mic-content.js into the meeting tab.
  // The tab already has mic permission (it's a meeting app) so no dialog appears.
  // This avoids the offscreen-document permission block in Brave and the UX
  // problem of an extra popup window.
  return new Promise((resolve) => {
    chrome.scripting.executeScript(
      { target: { tabId }, files: ['mic-content.js'] },
      () => {
        if (chrome.runtime.lastError) {
          console.warn('[TMF] mic inject failed:', chrome.runtime.lastError.message);
          resolve(); // non-fatal — tab audio still works
          return;
        }
        // Wait for mic-ready / mic-error from the injected script.
        const listener = (msg) => {
          if (msg.type === 'mic-ready' || msg.type === 'mic-error') {
            chrome.runtime.onMessage.removeListener(listener);
            resolve();
          }
        };
        chrome.runtime.onMessage.addListener(listener);
        // Safety timeout — resolve after 3s even if no response.
        setTimeout(() => { chrome.runtime.onMessage.removeListener(listener); resolve(); }, 3000);
      }
    );
  });
}

function closeMicCapture() {
  chrome.runtime.sendMessage({ type: 'release-mic' }).catch(() => {});
}

async function checkServer() {
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch('http://localhost:8765/health', { signal: ctrl.signal });
    return r.ok;
  } catch {
    return false;
  }
}

async function ensureServer() {
  // Fast path: if server already responding, skip native host entirely.
  // Native host requires starting a Python process which takes 10-30s on Windows
  // (antivirus scanning, module imports). Don't pay that cost when unnecessary.
  if (await checkServer()) return;

  // Server not running — use native host to start it
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(
      'com.talkmachinefury.host',
      { type: 'start-server' },
      (response) => {
        if (chrome.runtime.lastError) {
          // Native host not registered — non-fatal, WebSocket will fail and surface error
          console.warn('[TMF] Native host unavailable:', chrome.runtime.lastError.message);
          resolve();
        } else if (response?.ok) {
          resolve();
        } else {
          reject(new Error(response?.error ?? 'Server failed to start'));
        }
      }
    );
  });
}

async function handleStart(msg) {
  const { lang } = msg;

  await chrome.storage.session.set({ tmf_state: 'connecting', tmf_lang: lang });

  // Capture the target tab BEFORE opening the mic window — ensureMicCapture() opens
  // a new window which becomes the active window, making the tab query return the
  // wrong tab and causing "Extension has not been invoked for the current page".
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');

  await ensureServer();
  await ensureMicCapture(tab.id);  // injects into meeting tab, waits for mic-ready
  // Force-close any lingering offscreen doc before starting — a previous session
  // or extension reload may have left an active tab capture stream open, which
  // causes getMediaStreamId to fail with "Cannot capture a tab with an active stream."
  await closeOffscreen();
  await ensureOffscreen();

  await chrome.storage.session.set({ tmf_tab_title: tab.title || 'meeting' });

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
    closeMicCapture();
  }
});
