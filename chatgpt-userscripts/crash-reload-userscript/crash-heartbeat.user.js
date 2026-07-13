// ==UserScript==
// @name         Crash Heartbeat
// @namespace    urn:crash-reload
// @version      1.0.0
// @description  Plants a hidden DOM heartbeat that the crash reloader can verify.
// @match        *://*/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  var HEARTBEAT_ID = 'crash-reload-heartbeat';
  var HEARTBEAT_ATTR = 'data-last-seen';
  var INSTANCE_ATTR = 'data-instance-id';
  var STORE_KEY = 'crash-reload:heartbeat-at';
  var HEARTBEAT_INTERVAL_MS = 500;
  var ROOT_RETRY_MS = 50;

  var instanceId = (
    Date.now().toString(36) +
    Math.random().toString(36).slice(2)
  ).slice(0, 24);
  var started = false;
  var observer = null;
  var heartbeatTimer = 0;

  function now() {
    return Date.now();
  }

  function getRoot() {
    return document.documentElement || document.body || null;
  }

  function rememberHeartbeat(heartbeatAt) {
    try {
      sessionStorage.setItem(STORE_KEY, String(heartbeatAt));
    } catch {
      // Ignore storage failures. The DOM marker still carries the heartbeat.
    }
  }

  function ensureMarker() {
    var marker = document.getElementById(HEARTBEAT_ID);

    if (marker) {
      return marker;
    }

    var root = getRoot();

    if (!root) {
      return null;
    }

    marker = document.createElement('div');
    marker.id = HEARTBEAT_ID;
    marker.hidden = true;
    marker.setAttribute('aria-hidden', 'true');
    marker.setAttribute('data-crash-reload', 'alive');
    marker.style.cssText = 'display:none!important';
    root.appendChild(marker);

    return marker;
  }

  function pulse() {
    var marker = ensureMarker();

    if (!marker) {
      return;
    }

    var heartbeatAt = now();

    marker.setAttribute(INSTANCE_ATTR, instanceId);
    marker.setAttribute(HEARTBEAT_ATTR, String(heartbeatAt));
    rememberHeartbeat(heartbeatAt);
  }

  function attachObserver() {
    if (observer || !document.documentElement) {
      return;
    }

    observer = new MutationObserver(function () {
      if (!document.getElementById(HEARTBEAT_ID)) {
        pulse();
      }
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  function cleanup() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = 0;
    }

    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  function start() {
    if (started) {
      return;
    }

    var root = getRoot();

    if (!root) {
      setTimeout(start, ROOT_RETRY_MS);
      return;
    }

    started = true;
    pulse();

    heartbeatTimer = setInterval(pulse, HEARTBEAT_INTERVAL_MS);
    attachObserver();

    window.addEventListener('pageshow', pulse, { passive: true });
    document.addEventListener('visibilitychange', pulse, { passive: true });
    window.addEventListener('beforeunload', cleanup, { once: true });
  }

  start();
})();
