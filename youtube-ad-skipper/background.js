// Background service worker: performs trusted clicks via DevTools
// and maintains a lifetime skip counter with a badge on the extension icon.

const STORAGE_KEY = "skipCount";

async function cdp(tabId, method, params) {
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

async function trustedClickAt(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mousePressed",
    x,
    y,
    button: "left",
    clickCount: 1,
  });
  await cdp(tabId, "Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x,
    y,
    button: "left",
    clickCount: 1,
  });
}

// Badge helpers
async function getCount() {
  const obj = await chrome.storage.local.get(STORAGE_KEY);
  const n = obj?.[STORAGE_KEY];
  return Number.isFinite(n) ? n : 0;
}

function formatBadge(n) {
  if (n < 1000) return String(n);
  if (n < 10000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (n < 1000000) return Math.floor(n / 1000) + "k";
  if (n < 10000000) return (n / 1000000).toFixed(1).replace(/\.0$/, "") + "m";
  return Math.floor(n / 1000000) + "m+";
}

async function updateBadge(n) {
  try {
    await chrome.action.setBadgeText({ text: formatBadge(n) });
    await chrome.action.setBadgeBackgroundColor({ color: "#111827" });
  } catch (_) {}
}

async function setCount(n) {
  await chrome.storage.local.set({ [STORAGE_KEY]: n });
  await updateBadge(n);
}

async function incrementCount(delta = 1) {
  const n = (await getCount()) + delta;
  await setCount(n);
}

chrome.runtime.onInstalled.addListener(async () => {
  const n = await getCount();
  await updateBadge(n);
});

chrome.runtime.onStartup.addListener(async () => {
  const n = await getCount();
  await updateBadge(n);
});

chrome.runtime.onMessage.addListener(async (msg, sender, sendResponse) => {
  if (!sender.tab || !sender.tab.id) return;
  if (msg?.type === "SKIP_CLICK" && msg?.point) {
    const tabId = sender.tab.id;
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
      await trustedClickAt(tabId, msg.point.x, msg.point.y);
      await incrementCount(1);
      sendResponse({ ok: true });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    } finally {
      try {
        await chrome.debugger.detach({ tabId });
      } catch {}
    }
    return true; // async
  }
});