// ==UserScript==
// @name         Crash Reloader
// @namespace    urn:crash-reload
// @version      1.1.0
// @description  Watches the crash title and a companion DOM heartbeat, then restores or reloads the tab.
// @match        *://*/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  var TARGET_TITLE = 'tab crash reporter';
  var HEARTBEAT_ID = 'crash-reload-heartbeat';
  var HEARTBEAT_ATTR = 'data-last-seen';
  var ATTEMPT_KEY = 'crash-reload:attempted-at';
  var ARM_DELAY_MS = 1200;
  var POLL_INTERVAL_MS = 250;
  var MISSING_GRACE_MS = 1500;
  var HEARTBEAT_STALE_MS = 2200;
  var RETRY_DELAY_MS = 3000;
  var FALLBACK_RELOAD_MS = 600;

  var startedAt = Date.now();
  var seenHeartbeat = false;
  var missingSince = 0;
  var lastHeartbeatAt = 0;
  var attemptAt = readAttemptAt();
  var pollTimer = 0;
  var fallbackTimer = 0;
  var armTimer = 0;

  function normalizedTitle() {
    return (document.title || '').trim().toLowerCase();
  }

  function isReloadableUrl() {
    return location.protocol === 'http:' || location.protocol === 'https:';
  }

  function readAttemptAt() {
    try {
      return Number(sessionStorage.getItem(ATTEMPT_KEY) || 0) || 0;
    } catch {
      return 0;
    }
  }

  function writeAttemptAt(value) {
    attemptAt = value;

    try {
      sessionStorage.setItem(ATTEMPT_KEY, String(value));
    } catch {
      // Ignore storage failures and keep the in-memory guard.
    }
  }

  function clearAttemptAt() {
    attemptAt = 0;

    try {
      sessionStorage.removeItem(ATTEMPT_KEY);
    } catch {
      // Ignore storage failures and keep the in-memory guard.
    }
  }

  function readHeartbeatAt() {
    var marker = document.getElementById(HEARTBEAT_ID);

    if (!marker) {
      return 0;
    }

    return Number(marker.getAttribute(HEARTBEAT_ATTR) || 0) || 0;
  }

  function isFreshHeartbeat(heartbeatAt, now) {
    return Number.isFinite(heartbeatAt) && heartbeatAt > 0 && now - heartbeatAt <= HEARTBEAT_STALE_MS;
  }

  function getRestoreButton() {
    var selectors = [
      '#restoreTab',
      'button[data-l10n-id="crashed-restore-tab-button"]',
      'button[aria-label*="Restore This Tab"]',
      'button.primary'
    ];

    for (var i = 0; i < selectors.length; i += 1) {
      var button = document.querySelector(selectors[i]);

      if (!button) {
        continue;
      }

      var label = (
        button.getAttribute('aria-label') ||
        button.textContent ||
        ''
      ).trim().toLowerCase();

      if (
        button.id === 'restoreTab' ||
        label.includes('restore this tab') ||
        label === 'restore tab' ||
        label.includes('restore')
      ) {
        return button;
      }
    }

    return null;
  }

  function clickRestoreButton() {
    var button = getRestoreButton();

    if (!button) {
      return false;
    }

    try {
      button.click();
      return true;
    } catch {
      return false;
    }
  }

  function clearFallbackTimer() {
    if (fallbackTimer) {
      clearTimeout(fallbackTimer);
      fallbackTimer = 0;
    }
  }

  function clearGuardsIfHealthy(now) {
    if (!isReloadableUrl()) {
      return;
    }

    if (normalizedTitle() === TARGET_TITLE) {
      return;
    }

    if (seenHeartbeat && isFreshHeartbeat(lastHeartbeatAt, now)) {
      clearAttemptAt();
    }
  }

  function shouldRetry(now) {
    return !attemptAt || now - attemptAt >= RETRY_DELAY_MS;
  }

  function forceReload() {
    var href = location.href;

    clearFallbackTimer();
    writeAttemptAt(Date.now());

    if (clickRestoreButton()) {
      scheduleFallbackReload(href);
      return;
    }

    try {
      location.reload();
    } catch {
      // Fall through to the other reload primitives.
    }

    scheduleFallbackReload(href);
  }

  function scheduleFallbackReload(href) {
    clearFallbackTimer();

    fallbackTimer = setTimeout(function () {
      fallbackTimer = 0;

      if (!isReloadableUrl()) {
        return;
      }

      if (normalizedTitle() !== TARGET_TITLE && seenHeartbeat && isFreshHeartbeat(lastHeartbeatAt, Date.now())) {
        clearAttemptAt();
        return;
      }

      try {
        location.replace(href);
      } catch {
        // Ignore and try the next primitive.
      }

      fallbackTimer = setTimeout(function () {
        fallbackTimer = 0;

        if (!isReloadableUrl()) {
          return;
        }

        if (normalizedTitle() !== TARGET_TITLE && seenHeartbeat && isFreshHeartbeat(lastHeartbeatAt, Date.now())) {
          clearAttemptAt();
          return;
        }

        try {
          history.go(0);
        } catch {
          // No other browser-safe reload primitive left.
        }
      }, FALLBACK_RELOAD_MS);
    }, FALLBACK_RELOAD_MS);
  }

  function inspectState() {
    var now = Date.now();

    if (!isReloadableUrl()) {
      clearGuardsIfHealthy(now);
      return;
    }

    if (normalizedTitle() === TARGET_TITLE) {
      if (shouldRetry(now)) {
        forceReload();
      }

      return;
    }

    var heartbeatAt = readHeartbeatAt();

    if (heartbeatAt > 0) {
      seenHeartbeat = true;
      lastHeartbeatAt = heartbeatAt;
      missingSince = 0;
      clearGuardsIfHealthy(now);

      if (!isFreshHeartbeat(heartbeatAt, now) && shouldRetry(now)) {
        forceReload();
      }

      return;
    }

    if (!seenHeartbeat) {
      if (now - startedAt < ARM_DELAY_MS + MISSING_GRACE_MS) {
        return;
      }

      if (shouldRetry(now)) {
        forceReload();
      }

      return;
    }

    if (!missingSince) {
      missingSince = now;
      return;
    }

    if (now - missingSince >= MISSING_GRACE_MS && shouldRetry(now)) {
      forceReload();
    }
  }

  function start() {
    if (pollTimer || armTimer) {
      return;
    }

    armTimer = setTimeout(function () {
      armTimer = 0;

      inspectState();
      pollTimer = setInterval(inspectState, POLL_INTERVAL_MS);
    }, ARM_DELAY_MS);
  }

  start();
})();
