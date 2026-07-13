// content.js
// YouTube Silent Mode overlay + aggressive ad detection for reliable startup handling,
// plus deterministic (100ms) trusted Skip click via background debugger.
//
// This iteration adds:
// - fast startup polling (high-frequency checks for the first few seconds after load/navigation)
// - detection of ad DOM indicators even when not visible (proactive overlay + mute enforcement)
// - document-level mutation observer that reacts to ad-related node insertions quickly
// - overlay remains the single source of truth for mute enforcement

(function () {
  "use strict";

  // ============= Config =============
  const CONFIG = {
    overlay: {
      id: "yt-ad-blackout-overlay",
      text: "hiding ads...",
      textColor: "#fff",
      textOpacity: 0.9,
      textFont:
        "600 14px/1.6 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
      zIndex: 2147483647,
    },

    // main poll frequency for normal operation
    pollInterval: 500,

    // deterministic skip delay
    TRIGGER_DELAY_MS: 100,

    // Startup fast poll: aggressive checks right after navigation/page load
    startupFastPoll: {
      enabled: true,
      intervalMs: 200,    // check every 200ms
      durationMs: 8000,   // for the first 8 seconds after load/navigation
      extraImmediateChecks: 4, // do a few immediate updateAdState runs
    },

    // enforcement params (existing)
    enforceIntervalMs: 250, // how often we reapply mute while overlay visible
  };

  // ============= State =============
  const STATE = {
    adActive: false,
    mediaState: new WeakMap(),
    prevMutedCaptured: false,
    observers: [],
    pollerId: null,
    pendingSkipTimer: null,
    enforcementTimer: null,
    mutationObserver: null,
    perElementListeners: new WeakMap(),
    startupFastPollTimer: null,
    startupFastPollHandle: null,
  };

  // ============= DOM helpers =============
  function getPlayerRoot() {
    return (
      document.getElementById("movie_player") ||
      document.querySelector(".html5-video-player")
    );
  }

  function getAllMediaElements() {
    const videos = Array.from(document.querySelectorAll("video"));
    const audios = Array.from(document.querySelectorAll("audio"));
    return videos.concat(audios);
  }

  function isElementVisible(el) {
    if (!(el instanceof Element)) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.visibility !== "hidden" &&
      style.display !== "none" &&
      parseFloat(style.opacity || "1") > 0.05
    );
  }

  function resolveYouTubeBackgroundColor() {
    const root = document.documentElement;
    const rootStyle = getComputedStyle(root);
    const vars = [
      "--yt-spec-base-background",
      "--yt-spec-general-background-a",
      "--yt-spec-elevated-background",
      "--yt-simple-primary-background",
    ];
    for (const v of vars) {
      const val = rootStyle.getPropertyValue(v)?.trim();
      if (val) return val;
    }
    const tryEls = [document.body, root, getPlayerRoot()].filter(Boolean);
    for (const el of tryEls) {
      const c = getComputedStyle(el).backgroundColor?.trim();
      if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") return c;
    }
    return "#0f0f0f";
  }

  // ============= Overlay =============
  function ensureOverlay() {
    const player = getPlayerRoot();
    if (!player) return null;

    let overlay = document.getElementById(CONFIG.overlay.id);
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = CONFIG.overlay.id;
      Object.assign(overlay.style, {
        position: "absolute",
        inset: "0",
        pointerEvents: "none",
        background: resolveYouTubeBackgroundColor(),
        zIndex: String(CONFIG.overlay.zIndex),
        display: "none", // switch to 'flex' when visible
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
      });

      const label = document.createElement("div");
      label.textContent = CONFIG.overlay.text;
      Object.assign(label.style, {
        color: CONFIG.overlay.textColor,
        opacity: String(CONFIG.overlay.textOpacity),
        font: CONFIG.overlay.textFont,
        letterSpacing: "0.3px",
        userSelect: "none",
      });
      overlay.appendChild(label);

      const computed = getComputedStyle(player);
      if (computed.position === "static") {
        player.style.position = "relative";
      }
      player.appendChild(overlay);
    } else {
      const label = overlay.firstElementChild;
      if (label) label.textContent = CONFIG.overlay.text;
      overlay.style.background = resolveYouTubeBackgroundColor();
    }
    return overlay;
  }

  // ============= Strong mute enforcement (unchanged) =============
  function captureMediaState(el) {
    try {
      if (!el) return;
      if (!STATE.mediaState.has(el)) {
        STATE.mediaState.set(el, {
          prevMuted: Boolean(el.muted),
          prevVolume: typeof el.volume === "number" ? el.volume : 1,
        });
      }
    } catch (_) {}
  }

  function muteMediaElement(el) {
    try {
      if (!el) return;
      el.muted = true;
      if (typeof el.volume === "number") el.volume = 0;
    } catch (_) {}
  }

  function restoreMediaElement(el) {
    try {
      if (!el) return;
      const info = STATE.mediaState.get(el);
      if (!info) return;
      if (typeof el.volume === "number" && typeof info.prevVolume === "number") {
        el.volume = info.prevVolume;
      }
      el.muted = Boolean(info.prevMuted);
      STATE.mediaState.delete(el);
    } catch (_) {}
  }

  function enforceMuteOnce() {
    const medias = getAllMediaElements();
    for (const m of medias) {
      if (!STATE.mediaState.has(m)) {
        captureMediaState(m);
      }
      muteMediaElement(m);
    }
  }

  function startEnforcement() {
    enforceMuteOnce();

    const medias = getAllMediaElements();
    for (const m of medias) {
      if (!STATE.perElementListeners.has(m)) {
        const onVolumeChange = () => {
          if (overlayIsVisible()) {
            try { m.muted = true; if (typeof m.volume === "number") m.volume = 0; } catch (_) {}
          }
        };
        const onPlay = () => {
          if (overlayIsVisible()) {
            try { m.muted = true; if (typeof m.volume === "number") m.volume = 0; } catch (_) {}
          }
        };
        m.addEventListener("volumechange", onVolumeChange, { passive: true });
        m.addEventListener("play", onPlay, { passive: true });
        STATE.perElementListeners.set(m, { onVolumeChange, onPlay });
      } else {
        muteMediaElement(m);
      }
    }

    if (!STATE.mutationObserver) {
      STATE.mutationObserver = new MutationObserver((mutations) => {
        for (const mu of mutations) {
          for (const node of mu.addedNodes) {
            try {
              if (!(node instanceof Element)) continue;
              if (node.tagName === "VIDEO" || node.tagName === "AUDIO") {
                captureMediaState(node);
                muteMediaElement(node);
                if (!STATE.perElementListeners.has(node)) {
                  const onVolumeChange = () => {
                    if (overlayIsVisible()) {
                      try { node.muted = true; if (typeof node.volume === "number") node.volume = 0; } catch (_) {}
                    }
                  };
                  const onPlay = () => {
                    if (overlayIsVisible()) {
                      try { node.muted = true; if (typeof node.volume === "number") node.volume = 0; } catch (_) {}
                    }
                  };
                  node.addEventListener("volumechange", onVolumeChange, { passive: true });
                  node.addEventListener("play", onPlay, { passive: true });
                  STATE.perElementListeners.set(node, { onVolumeChange, onPlay });
                }
              } else {
                const videos = node.querySelectorAll ? node.querySelectorAll("video,audio") : [];
                for (const v of videos) {
                  captureMediaState(v);
                  muteMediaElement(v);
                  if (!STATE.perElementListeners.has(v)) {
                    const onVolumeChange = () => {
                      if (overlayIsVisible()) {
                        try { v.muted = true; if (typeof v.volume === "number") v.volume = 0; } catch (_) {}
                      }
                    };
                    const onPlay = () => {
                      if (overlayIsVisible()) {
                        try { v.muted = true; if (typeof v.volume === "number") v.volume = 0; } catch (_) {}
                      }
                    };
                    v.addEventListener("volumechange", onVolumeChange, { passive: true });
                    v.addEventListener("play", onPlay, { passive: true });
                    STATE.perElementListeners.set(v, { onVolumeChange, onPlay });
                  }
                }
              }
            } catch (_) {}
          }
        }
      });
      STATE.mutationObserver.observe(document.documentElement || document.body, {
        childList: true,
        subtree: true,
      });
    }

    if (!STATE.enforcementTimer) {
      STATE.enforcementTimer = setInterval(enforceMuteOnce, CONFIG.enforceIntervalMs);
    }
  }

  function stopEnforcementAndRestore() {
    if (STATE.enforcementTimer) {
      clearInterval(STATE.enforcementTimer);
      STATE.enforcementTimer = null;
    }
    if (STATE.mutationObserver) {
      try { STATE.mutationObserver.disconnect(); } catch (_) {}
      STATE.mutationObserver = null;
    }
    const medias = getAllMediaElements();
    for (const m of medias) {
      const listeners = STATE.perElementListeners.get(m);
      if (listeners) {
        try { m.removeEventListener("volumechange", listeners.onVolumeChange); } catch (_) {}
        try { m.removeEventListener("play", listeners.onPlay); } catch (_) {}
        STATE.perElementListeners.delete(m);
      }
      if (STATE.mediaState.has(m)) {
        restoreMediaElement(m);
      }
    }
    STATE.mediaState = new WeakMap();
  }

  function overlayIsVisible() {
    const overlay = document.getElementById(CONFIG.overlay.id);
    return !!overlay && overlay.style.display !== "none";
  }

  // ============= Overlay visibility control (single source of truth for mute) =============
  function setOverlayVisible(visible) {
    const overlay = ensureOverlay();
    if (!overlay) return;

    if (visible) {
      // capture current media states and start enforcement
      const medias = getAllMediaElements();
      for (const m of medias) captureMediaState(m);
      overlay.style.background = resolveYouTubeBackgroundColor();
      overlay.style.display = "flex";
      startEnforcement();
    } else {
      overlay.style.display = "none";
      stopEnforcementAndRestore();
      overlay.style.background = resolveYouTubeBackgroundColor();
    }
  }

  // ============= Detection heuristics =============
  // Strict playing detection using class toggles
  function detectAdPlayingByClass() {
    const player = getPlayerRoot();
    if (!player) return false;
    return player.classList.contains("ad-showing") || player.classList.contains("ad-interrupting");
  }

  // Presence of ad-related DOM markers (even if not visible) — useful at startup
  function detectAdIndicatorsPresent() {
    // selectors that typically exist during ads (preroll/midroll)
    const selectors = [
      ".video-ads.ytp-ad-module",
      ".ytp-ad-player-overlay-layout",
      ".ytp-ad-badge", // ad badge
      ".ytp-visit-advertiser-link",
      ".ytp-ad-pod-index",
      ".ytp-skip-ad-button",
      ".ytp-ad-text",
      "[aria-label='Sponsored']",
    ];
    for (const sel of selectors) {
      if (document.querySelector(sel)) return true;
    }

    // also look for elements with ad-like classNames being inserted early
    const candidates = Array.from(document.querySelectorAll("*")).slice(0, 300);
    for (const el of candidates) {
      const cls = el.className;
      if (typeof cls === "string" && /ytp-?ad|video-ads|skip-ad|ad-pod/i.test(cls)) return true;
    }
    return false;
  }

  // Combined ad detection
  function detectAdPlaying() {
    // 1) direct class signals (most reliable)
    if (detectAdPlayingByClass()) return true;

    // 2) overlay-layout visible
    const adModule = document.querySelector(".video-ads.ytp-ad-module");
    if (adModule && isElementVisible(adModule)) {
      const overlayLayout = adModule.querySelector(".ytp-ad-player-overlay-layout");
      if (overlayLayout && isElementVisible(overlayLayout)) return true;
    }

    // 3) ad-badge / visit-advertiser visible AND adModule present
    if (
      (document.querySelector(".ytp-visit-advertiser-link") ||
        document.querySelector(".ytp-ad-pod-index") ||
        document.querySelector(".ytp-ad-text")) &&
      adModule &&
      isElementVisible(adModule)
    ) {
      return true;
    }

    // 4) startup presence of ad indicators (non-visible) - but only during the fast startup window
    if (startupFastWindowActive()) {
      if (detectAdIndicatorsPresent()) return true;
    }

    return false;
  }

  // ============= Skip detection & scheduling (100ms) =============
  const SKIP_SELECTORS = [
    "button.ytp-skip-ad-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-modern",
    'button[aria-label^="Skip ad"]',
  ];

  function findVisibleSkipButtonRect() {
    for (const sel of SKIP_SELECTORS) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const visible =
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        parseFloat(style.opacity || "1") > 0.05 &&
        !el.hasAttribute("disabled") &&
        style.pointerEvents !== "none";
      if (visible) return rect;
    }
    return null;
  }

  function scheduleDeterministicSkip() {
    if (STATE.pendingSkipTimer !== null) return;
    STATE.pendingSkipTimer = setTimeout(async () => {
      STATE.pendingSkipTimer = null;
      if (!STATE.adActive) return;
      const rect = findVisibleSkipButtonRect();
      if (!rect) return;
      const x = Math.floor(rect.left + rect.width / 2);
      const y = Math.floor(rect.top + rect.height / 2);
      try {
        await chrome.runtime.sendMessage({ type: "SKIP_CLICK", point: { x, y } });
      } catch {}
    }, CONFIG.TRIGGER_DELAY_MS);
  }

  function trySkipIfPossible() {
    if (!STATE.adActive) return;
    const rect = findVisibleSkipButtonRect();
    if (!rect) return;
    scheduleDeterministicSkip();
  }

  // ============= Startup fast-poll helpers =============
  function startupFastWindowActive() {
    return Boolean(STATE.startupFastPollHandle);
  }

  function startStartupFastPolling() {
    if (!CONFIG.startupFastPoll.enabled) return;
    // Clear any existing fast poll
    stopStartupFastPolling();

    // extra immediate checks
    for (let i = 0; i < CONFIG.startupFastPoll.extraImmediateChecks; i++) {
      setTimeout(updateAdState, i * 60);
    }

    // high-frequency interval
    STATE.startupFastPollHandle = setInterval(updateAdState, CONFIG.startupFastPoll.intervalMs);
    // stop the fast window after the duration
    STATE.startupFastPollTimer = setTimeout(() => {
      stopStartupFastPolling();
    }, CONFIG.startupFastPoll.durationMs);
  }

  function stopStartupFastPolling() {
    if (STATE.startupFastPollHandle) {
      clearInterval(STATE.startupFastPollHandle);
      STATE.startupFastPollHandle = null;
    }
    if (STATE.startupFastPollTimer) {
      clearTimeout(STATE.startupFastPollTimer);
      STATE.startupFastPollTimer = null;
    }
  }

  // ============= Ad transitions =============
  function onAdStart() {
    if (STATE.adActive) return;
    STATE.adActive = true;

    // overlay-controlled muting: show overlay (will start enforcement)
    setOverlayVisible(true);

    // kick skip flow
    trySkipIfPossible();
  }

  function onAdEnd() {
    if (!STATE.adActive) return;
    STATE.adActive = false;

    // hide overlay => restore media states
    setOverlayVisible(false);

    if (STATE.pendingSkipTimer !== null) {
      clearTimeout(STATE.pendingSkipTimer);
      STATE.pendingSkipTimer = null;
    }
  }

  function updateAdState() {
    const adNow = detectAdPlaying();
    if (adNow && !STATE.adActive) {
      onAdStart();
    } else if (!adNow && STATE.adActive) {
      onAdEnd();
    }
    if (adNow) trySkipIfPossible();
  }

  // ============= Observers & init =============
  function setupObservers() {
    cleanupObservers();

    const player = getPlayerRoot();
    if (player) {
      const mo = new MutationObserver(updateAdState);
      mo.observe(player, { attributes: true, attributeFilter: ["class"] });
      STATE.observers.push(mo);
    }

    const adModule = document.querySelector(".video-ads.ytp-ad-module");
    if (adModule) {
      const mo2 = new MutationObserver(updateAdState);
      mo2.observe(adModule, { attributes: true, childList: true, subtree: true });
      STATE.observers.push(mo2);
    }

    // document-level mutation observer that watches for ad-like node insertions
    const docObserver = new MutationObserver((mutations) => {
      for (const mu of mutations) {
        for (const node of mu.addedNodes) {
          try {
            if (!(node instanceof Element)) continue;
            const cls = node.className || "";
            if (typeof cls === "string" && /ytp-?ad|video-ads|skip-ad|ad-pod|ytp-ad-player-overlay-layout/i.test(cls)) {
              // immediate re-check
              updateAdState();
              return;
            }
            // also check descendants quickly
            if (node.querySelector && node.querySelector(".ytp-ad-player-overlay-layout, .video-ads, .ytp-skip-ad-button, .ytp-ad-badge")) {
              updateAdState();
              return;
            }
          } catch (_) {}
        }
      }
    });
    try {
      docObserver.observe(document.documentElement || document.body, {
        childList: true,
        subtree: true,
      });
      STATE.observers.push(docObserver);
    } catch (_) {
      // ignore if observe fails
    }

    if (STATE.pollerId) clearInterval(STATE.pollerId);
    STATE.pollerId = setInterval(updateAdState, CONFIG.pollInterval);

    // initial sync
    updateAdState();
  }

  function cleanupObservers() {
    for (const mo of STATE.observers) {
      try { mo.disconnect(); } catch (_) {}
    }
    STATE.observers = [];
    if (STATE.pollerId) {
      clearInterval(STATE.pollerId);
      STATE.pollerId = null;
    }
  }

  function handleNavigation() {
    // SPA navigation: re-run observers and start fast polling
    window.addEventListener("yt-navigate-finish", () => {
      // start a fresh fast-poll window to handle prerolls after navigation
      startStartupFastPolling();
      setTimeout(() => {
        const overlay = ensureOverlay();
        if (overlay) overlay.style.background = resolveYouTubeBackgroundColor();
        setupObservers();
      }, 200);
    });
  }

  // Also react to page-level load events (hard loads)
  function setupPageLoadHandlers() {
    window.addEventListener("load", () => {
      startStartupFastPolling();
      // try immediate detection after load
      for (let i = 0; i < 6; i++) {
        setTimeout(updateAdState, i * 100);
      }
    });
    document.addEventListener("DOMContentLoaded", () => {
      // ensure overlay created early
      ensureOverlay();
    });
  }

  function init() {
    ensureOverlay();
    setupObservers();
    handleNavigation();
    setupPageLoadHandlers();

    // start startup fast polling now (covers initial page load)
    startStartupFastPolling();

    // If video starts playing immediately, re-check (helps detect prerolls that start instantly)
    try {
      const video = document.querySelector("video");
      if (video) {
        video.addEventListener("playing", () => {
          // If playing and we see ad indicators, show overlay
          if (detectAdIndicatorsPresent() || detectAdPlayingByClass()) {
            updateAdState();
          }
        }, { passive: true });
      }
    } catch (_) {}
  }

  setTimeout(init, 300);
  window.addEventListener("beforeunload", cleanupObservers);
})();