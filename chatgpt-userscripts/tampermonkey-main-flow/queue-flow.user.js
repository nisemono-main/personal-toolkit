// ==UserScript==
// @name         Queue Flow Panel
// @namespace    urn:queue-flow-panel
// @version      1.4.8
// @description  Injects a docked in-page queue rail, sequence modal, and prompt index for ChatGPT.
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const ROOT_ID = 'tmq-root';
  const SEQUENCES_KEY = 'tmq:sequences:v1';
  const DRAFT_KEY = 'tmq:draft:v1';
  const JOB_KEY = 'tmq:job:v1';
  const UI_OPEN_KEY = 'tmq:ui-open:v1';
  const CHATGPT_PAGE_SETTLE_MS = 5000;
  const CHATGPT_IDLE_STABILITY_MS = 2500;
  const CHATGPT_SEND_DELAY_MS = 700;
  const CHATGPT_NO_BUSY_COMPLETION_GRACE_MS = 12000;
  const CHATGPT_SEND_BUTTON_WAIT_MS = 15000;
  const WAIT_PROGRESS_STATUS_MS = 30000;
  const POLL_INTERVAL_MS = 250;
  const RAIL_WIDTH_FACTOR = 0.28;
  const RAIL_WIDTH_MIN = 320;
  const RAIL_WIDTH_MAX = 620;
  const RAIL_COLLAPSED_WIDTH = 56;
  const CHATGPT_SHELL_SELECTOR = 'body > div > div.flex.h-svh.w-screen.flex-col';
  const PROMPT_INDEX_KEY_PREFIX = 'tmq:prompt-index:v1:';
  const PROMPT_INDEX_REFRESH_DEBOUNCE_MS = 180;
  const PROMPT_INDEX_BACKFILL_STEP_RATIO = 0.85;
  const PROMPT_INDEX_BACKFILL_PAUSE_MS = 250;
  const PROMPT_INDEX_BACKFILL_MAX_STEPS = 80;

  const state = {
    sequences: {},
    draftMessages: [],
    draftName: '',
    selectedSequence: '',
    panelOpen: false,
    jobProcessing: false,
    processTimer: null,
    automationRunId: 0,
    uiReady: false,
    launcherBadge: 0,
    sequenceModalOpen: false,
    promptIndex: [],
    promptIndexByKey: new Map(),
    promptIndexScope: '',
    promptIndexActiveKey: '',
    promptIndexRefreshTimer: null,
    promptIndexBackfillRunning: false,
    promptIndexObserver: null,
    dockLayoutTimer: null,
    lastLocationHref: '',
    locationWatcherInstalled: false
  };

  let host = null;
  let shadow = null;
  let els = {};

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function randomId(prefix) {
    const suffix = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

    return `${prefix}${suffix}`;
  }

  function safeParseJSON(text, fallback) {
    if (!text) return fallback;

    try {
      return JSON.parse(text);
    } catch {
      return fallback;
    }
  }

  function loadLocalJSON(key, fallback) {
    try {
      return safeParseJSON(localStorage.getItem(key), fallback);
    } catch {
      return fallback;
    }
  }

  function loadSessionJSON(key, fallback) {
    try {
      return safeParseJSON(sessionStorage.getItem(key), fallback);
    } catch {
      return fallback;
    }
  }

  function saveLocalJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  }

  function saveSessionJSON(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  }

  function loadPersistentJSON(key, fallback) {
    const storageSources = [
      { name: 'local', storage: localStorage },
      { name: 'session', storage: sessionStorage }
    ];

    for (const source of storageSources) {
      try {
        const raw = source.storage.getItem(key);
        if (raw === null) {
          continue;
        }

        const parsed = safeParseJSON(raw, undefined);
        if (parsed !== undefined) {
          if (source.name === 'session') {
            try {
              localStorage.setItem(key, JSON.stringify(parsed));
            } catch {
              // Ignore persistence migration failures.
            }
          }

          return parsed;
        }
      } catch {
        // Ignore storage access failures and try the next source.
      }
    }

    return fallback;
  }

  function savePersistentJSON(key, value) {
    const serialized = JSON.stringify(value);
    let saved = false;

    try {
      localStorage.setItem(key, serialized);
      saved = true;
    } catch {
      // Ignore persistence failures.
    }

    try {
      sessionStorage.setItem(key, serialized);
      saved = true;
    } catch {
      // Ignore persistence failures.
    }

    return saved;
  }

  function normalizeMessages(messages) {
    return Array.isArray(messages)
      ? messages.map((message) => String(message || '').trim()).filter(Boolean)
      : [];
  }

  function readUiOpen() {
    try {
      const value = sessionStorage.getItem(UI_OPEN_KEY);
      return value === null ? null : value === '1';
    } catch {
      return null;
    }
  }

  function writeUiOpen(value) {
    try {
      sessionStorage.setItem(UI_OPEN_KEY, value ? '1' : '0');
    } catch {
      // Ignore storage failures.
    }
  }

  function isVisible(element) {
    if (!element || element.hidden) return false;
    if (element.closest?.('[aria-hidden="true"], [inert]')) return false;
    if (typeof element.getClientRects !== 'function' || element.getClientRects().length === 0) {
      return false;
    }

    const style = window.getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  }

  function isDisabledControl(element) {
    return !!(
      element &&
      (
        element.disabled ||
        element.getAttribute?.('aria-disabled') === 'true' ||
        element.closest?.('[disabled], [aria-disabled="true"]')
      )
    );
  }

  function findFirstSelector(selectors, root = document, options = {}) {
    const visibleOnly = options.visibleOnly !== false;

    for (const selector of selectors) {
      const candidates = Array.from(root.querySelectorAll(selector));
      const element = candidates.find((candidate) => !isInOurUI(candidate) && isVisible(candidate));

      if (element) {
        return { selector, element };
      }

      if (!visibleOnly && candidates[0]) {
        return { selector, element: candidates[0] };
      }
    }

    return { selector: '', element: null };
  }

  function findVisibleSelector(selectors, root = document) {
    return findFirstSelector(selectors, root, { visibleOnly: true });
  }

  function isInOurUI(node) {
    return !!(shadow && node instanceof Node && node.getRootNode && node.getRootNode() === shadow);
  }

  function findComposer() {
    const selectors = [
      '#prompt-textarea',
      'textarea#prompt-textarea',
      'textarea[aria-label*="Message"]',
      'textarea[aria-label*="Ask"]',
      'textarea[placeholder*="Message"]',
      'textarea[placeholder*="Ask"]',
      'textarea',
      '[data-testid="prompt-textarea"]',
      '[role="textbox"][contenteditable]',
      '[contenteditable="true"][role="textbox"]',
      '[contenteditable="plaintext-only"][role="textbox"]',
      '[contenteditable="true"]',
      '[contenteditable="plaintext-only"]',
      '[contenteditable]:not([contenteditable="false"])'
    ];

    for (const selector of selectors) {
      const elements = Array.from(document.querySelectorAll(selector));
      const element = elements.find((node) => !isInOurUI(node) && isVisible(node));

      if (element) {
        return element;
      }
    }

    return null;
  }

  function findSendButton() {
    const direct = findVisibleSelector([
      'button[data-testid="send-button"]',
      'button[aria-label="Send prompt"]',
      'button[aria-label="Send message"]',
      'button[type="submit"]'
    ]);

    if (direct.element && !isInOurUI(direct.element)) {
      return direct.element;
    }

    const buttons = Array.from(document.querySelectorAll('button')).filter((node) => !isInOurUI(node) && isVisible(node));
    return buttons.find((button) => {
      const label = (
        button.getAttribute('aria-label') ||
        button.innerText ||
        button.textContent ||
        ''
      ).toLowerCase();

      return label === 'send prompt' ||
        label === 'send message' ||
        label === 'send';
    }) || null;
  }

  function findStopButton() {
    const direct = findVisibleSelector(['button[data-testid="stop-button"]']);

    if (direct.element && !isInOurUI(direct.element) && !isDisabledControl(direct.element)) {
      return direct.element;
    }

    const buttons = Array.from(document.querySelectorAll('button'))
      .filter((node) => !isInOurUI(node) && isVisible(node) && !isDisabledControl(node));
    return buttons.find((button) => {
      const label = (
        button.getAttribute('aria-label') ||
        button.innerText ||
        button.textContent ||
        ''
      ).toLowerCase();

      return (
        label.includes('stop answering') ||
        label.includes('stop generating') ||
        label.includes('stop streaming') ||
        label.includes('stop response') ||
        label.includes('interrupt')
      );
    }) || null;
  }

  function dispatchInput(element, inputType, data) {
    const eventInit = {
      bubbles: true,
      cancelable: true,
      inputType,
      data
    };

    let event;

    try {
      event = new InputEvent('input', eventInit);
    } catch {
      event = new Event('input', { bubbles: true, cancelable: true });
    }

    element.dispatchEvent(event);
  }

  function setNativeValue(element, value) {
    const valueSetter = Object.getOwnPropertyDescriptor(element.__proto__, 'value')?.set;
    const prototype = Object.getPrototypeOf(element);
    const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;

    if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {
      prototypeValueSetter.call(element, value);
    } else if (valueSetter) {
      valueSetter.call(element, value);
    } else {
      element.value = value;
    }
  }

  function getComposerText(composer) {
    if (!composer) {
      return '';
    }

    if (composer.tagName && composer.tagName.toLowerCase() === 'textarea') {
      return composer.value || '';
    }

    return composer.innerText || composer.textContent || '';
  }

  function composerTextMatches(composer, text) {
    return normalizePromptText(getComposerText(composer)) === normalizePromptText(text);
  }

  function dispatchBeforeInput(element, inputType, data) {
    try {
      element.dispatchEvent(new InputEvent('beforeinput', {
        bubbles: true,
        cancelable: true,
        inputType,
        data
      }));
    } catch {
      // Some browsers/userscript contexts do not allow constructing beforeinput.
    }
  }

  function tryExecCommandInsert(composer, text) {
    if (!composer || !composer.isContentEditable || typeof document.execCommand !== 'function') {
      return false;
    }

    try {
      composer.focus();
      const selection = window.getSelection?.();
      const range = document.createRange();
      range.selectNodeContents(composer);
      selection?.removeAllRanges();
      selection?.addRange(range);
      document.execCommand('delete', false, null);
      return document.execCommand('insertText', false, text);
    } catch {
      return false;
    }
  }

  function setComposerText(composer, text) {
    composer.focus();

    if (composer.tagName && composer.tagName.toLowerCase() === 'textarea') {
      dispatchBeforeInput(composer, 'insertText', text);
      setNativeValue(composer, text);
      dispatchInput(composer, 'insertText', text);
      return;
    }

    if (composer.isContentEditable) {
      dispatchBeforeInput(composer, 'insertText', text);

      const insertedWithCommand = tryExecCommandInsert(composer, text);
      if (!insertedWithCommand || !composerTextMatches(composer, text)) {
        composer.innerHTML = '';
        const paragraph = document.createElement('p');
        paragraph.innerText = text;
        composer.appendChild(paragraph);
      }

      dispatchInput(composer, 'insertText', text);
      return;
    }

    dispatchBeforeInput(composer, 'insertText', text);
    composer.textContent = text;
    dispatchInput(composer, 'insertText', text);
  }

  function clearComposer(composer) {
    if (!composer) return;

    composer.focus();

    if (composer.tagName && composer.tagName.toLowerCase() === 'textarea') {
      dispatchBeforeInput(composer, 'deleteContentBackward', null);
      setNativeValue(composer, '');
      dispatchInput(composer, 'deleteContentBackward', null);
      return;
    }

    if (composer.isContentEditable) {
      dispatchBeforeInput(composer, 'deleteContentBackward', null);
      composer.innerHTML = '';
      dispatchInput(composer, 'deleteContentBackward', null);
      return;
    }

    dispatchBeforeInput(composer, 'deleteContentBackward', null);
    composer.textContent = '';
    dispatchInput(composer, 'deleteContentBackward', null);
  }

  function inspectPageState() {
    const buttons = Array.from(document.querySelectorAll('button')).filter((node) => !isInOurUI(node) && isVisible(node));
    const stopButton = findStopButton();

    const resultStreaming = findVisibleSelector([
      '.result-streaming',
      '[data-testid*="conversation-turn"] .result-streaming',
      '[data-message-streaming="true"]',
      '[data-testid*="streaming"]'
    ]).element;

    const loadingShimmer = findVisibleSelector([
      '.loading-shimmer',
      '[class*="loading-shimmer"]'
    ]).element;

    const sendButton = findSendButton();
    const composer = findComposer();
    const statusText = Array.from(document.querySelectorAll(
      '[role="status"], [aria-live], [data-testid*="status"], [data-testid*="progress"], [data-testid*="research"]'
    ))
      .map((node) => node.innerText || node.textContent || '')
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 600)
      .toLowerCase();

    const recentConversationText = Array.from(document.querySelectorAll(
      '[data-testid^="conversation-turn-"], section[data-turn]'
    ))
      .filter((node) => node instanceof HTMLElement && !isInOurUI(node) && isVisible(node))
      .slice(-4)
      .map((node) => node.innerText || node.textContent || '')
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 1200)
      .toLowerCase();

    const rateLimitNoticeText = Array.from(document.querySelectorAll(
      '[role="dialog"], [role="alert"], [role="alertdialog"], [data-testid*="toast"], [data-testid*="notification"], [data-testid*="error"], [class*="toast"], [class*="snackbar"], [class*="notification"]'
    ))
      .filter((node) => node instanceof HTMLElement && !isInOurUI(node) && isVisible(node))
      .map((node) => `${node.innerText || node.textContent || ''} ${node.querySelector('h1, h2, h3')?.textContent || ''}`)
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 2000)
      .toLowerCase();
    const rateLimitText = `${rateLimitNoticeText} ${statusText}`
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
    const rateLimitMarkers = [
      'too many requests',
      "you’re making requests too quickly",
      "you're making requests too quickly",
      'temporarily limited access to your conversations',
      'please wait a few minutes before trying again',
      'temporarily limited access'
    ];
    const matchedRateLimitMarker = rateLimitMarkers.find((marker) => rateLimitText.includes(marker)) || '';

    const hasTryAgainButton = buttons.some((button) => {
      const label = (button.innerText || button.getAttribute('aria-label') || '').toLowerCase().trim();
      return label === 'retry' || label === 'try again';
    });

    const errorText = `${statusText} ${recentConversationText}`.trim();
    const researchText = `${statusText} ${buttons.map((button) => button.innerText || button.getAttribute('aria-label') || '').join(' ')}`.toLowerCase();
    const researchProgressMarkers = [
      'deep research',
      'researching',
      'searching the web',
      'searching sources',
      'reading sources',
      'analyzing sources',
      'gathering sources',
      'checking sources',
      'synthesizing',
      'creating report',
      'writing report'
    ];
    const matchedResearchMarker = researchProgressMarkers.find((marker) => researchText.includes(marker)) || '';
    const deepResearchActive = !!matchedResearchMarker && !!stopButton && !sendButton;
    const matchedError =
      errorText.includes('something went wrong') ||
      errorText.includes('there was an error') ||
      errorText.includes('error generating a response') ||
      errorText.includes('network error') ||
      errorText.includes('failed to generate') ||
      errorText.includes('try again later');

    return {
      readyState: document.readyState || 'complete',
      isLoading: (document.readyState || 'complete') !== 'complete',
      hasStopButton: !!stopButton,
      hasStreamingIndicator: !!resultStreaming,
      hasLoadingShimmer: !!loadingShimmer,
      hasSendButton: !!sendButton,
      sendButtonDisabled: !!sendButton && (
        !!sendButton.disabled ||
        sendButton.getAttribute('aria-disabled') === 'true'
      ),
      composerFound: !!composer,
      composerText: getComposerText(composer),
      composerTextLength: getComposerText(composer).length,
      generating: !!(stopButton || resultStreaming || loadingShimmer || deepResearchActive),
      deepResearchActive,
      matchedResearchMarker,
      hasRateLimitNotice: !!matchedRateLimitMarker,
      matchedRateLimitMarker,
      hasError: matchedError,
      hasTryAgainButton,
      title: document.title,
      url: location.href,
      statusText,
      recentConversationText
    };
  }

  function getQueueBusyReason(page, options = {}) {
    const requireComposer = options.requireComposer !== false;

    if (!page) {
      return 'page state unavailable';
    }

    if (page.isLoading) {
      return 'document is still loading';
    }

    if (page.hasStopButton && !page.hasSendButton) {
      return 'ChatGPT stop button is still active';
    }

    if (page.deepResearchActive) {
      return 'deep research still appears active';
    }

    if (requireComposer && !page.composerFound) {
      return 'composer was not found';
    }

    return '';
  }

  function isPageBusyForQueue(page, options = {}) {
    return !!getQueueBusyReason(page, options);
  }

  function loadSequences() {
    const raw = loadLocalJSON(SEQUENCES_KEY, {});
    const cleaned = {};

    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return {};
    }

    for (const [name, messages] of Object.entries(raw)) {
      if (typeof name !== 'string' || !name.trim() || !Array.isArray(messages)) {
        continue;
      }

      cleaned[name.trim()] = messages
        .map((message) => String(message || '').trim())
        .filter(Boolean);
    }

    return cleaned;
  }

  function saveSequences() {
    return saveLocalJSON(SEQUENCES_KEY, state.sequences);
  }

  function loadDraftState() {
    const data = loadSessionJSON(DRAFT_KEY, null);
    return {
      messages: Array.isArray(data?.messages)
        ? data.messages.map((message) => String(message || '').trim()).filter(Boolean)
        : [],
      name: typeof data?.name === 'string' ? data.name : '',
      selectedSequence: typeof data?.selectedSequence === 'string' ? data.selectedSequence : ''
    };
  }

  function saveDraftState() {
    return saveSessionJSON(DRAFT_KEY, {
      messages: state.draftMessages,
      name: state.draftName,
      selectedSequence: state.selectedSequence
    });
  }

  function loadJob() {
    const data = loadSessionJSON(JOB_KEY, null);

    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return null;
    }

    const queue = normalizeMessages(data.queue);

    const currentMessage = typeof data.currentMessage === 'string' ? data.currentMessage.trim() : '';
    const phase = ['waiting', 'sending', 'paused'].includes(data.phase) ? data.phase : 'queued';
    const pendingCount = queue.length > 0 ? queue.length : (currentMessage ? 1 : 0);

    if (pendingCount <= 0) {
      try {
        sessionStorage.removeItem(JOB_KEY);
      } catch {
        // Ignore storage failures.
      }
      return null;
    }

    return {
      id: typeof data.id === 'string' && data.id ? data.id : randomId('job-'),
      kind: data.kind === 'single' ? 'single' : 'sequence',
      queue,
      currentMessage,
      phase,
      paused: data.paused === true || data.phase === 'paused',
      completedCount: Number.isFinite(Number(data.completedCount)) ? Number(data.completedCount) : 0,
      readyAfter: Number.isFinite(Number(data.readyAfter)) ? Number(data.readyAfter) : 0,
      conversationScope: typeof data.conversationScope === 'string' ? data.conversationScope : '',
      createdAt: Number.isFinite(Number(data.createdAt)) ? Number(data.createdAt) : Date.now(),
      updatedAt: Number.isFinite(Number(data.updatedAt)) ? Number(data.updatedAt) : Date.now()
    };
  }

  function getJobPendingCount(job) {
    if (!job) {
      return 0;
    }

    const queueLength = Array.isArray(job.queue) ? job.queue.length : 0;
    return queueLength > 0 ? queueLength : (job.currentMessage ? 1 : 0);
  }

  function parsePositiveStep(value, fallback) {
    const parsed = Number.parseInt(String(value ?? ''), 10);
    return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
  }

  function resolveSequenceRange(messages, options = {}) {
    const queue = normalizeMessages(messages);
    const total = queue.length;
    const startStep = parsePositiveStep(options.startStep, 1);
    const requestedEndStep = parsePositiveStep(options.endStep, total);
    const endStep = Math.min(total, requestedEndStep);
    const startIndex = Math.max(0, startStep - 1);
    const slicedQueue = queue.slice(startIndex, endStep);

    return {
      queue,
      total,
      startStep,
      endStep,
      startIndex,
      slicedQueue
    };
  }

  function formatSequenceRange(startStep, endStep) {
    return startStep === endStep
      ? `step ${startStep}`
      : `steps ${startStep} to ${endStep}`;
  }

  function clearSequenceRangeInputs() {
    if (els.startStep) {
      els.startStep.value = '';
    }

    if (els.endStep) {
      els.endStep.value = '';
    }
  }

  function createJobFromMessages(messages, options = {}) {
    const {
      startIndex,
      slicedQueue
    } = resolveSequenceRange(messages, options);

    if (!slicedQueue.length) {
      return null;
    }

    const now = Date.now();

    return {
      id: randomId('job-'),
      kind: options.kind === 'single' ? 'single' : 'sequence',
      queue: slicedQueue,
      currentMessage: '',
      phase: 'queued',
      paused: false,
      completedCount: startIndex,
      readyAfter: now + CHATGPT_PAGE_SETTLE_MS,
      conversationScope: getConversationStorageScope(),
      createdAt: now,
      updatedAt: now
    };
  }

  function appendMessagesToJob(job, messages) {
    const queue = normalizeMessages(messages);

    if (!job || !queue.length) {
      return 0;
    }

    if (!Array.isArray(job.queue)) {
      job.queue = [];
    }

    job.queue.push(...queue);
    job.updatedAt = Date.now();
    return queue.length;
  }

  function saveJob(job) {
    if (!job) {
      try {
        sessionStorage.removeItem(JOB_KEY);
      } catch {
        // Ignore storage failures.
      }
      state.launcherBadge = 0;
      updateLauncherBadge();
      updateJobSummary();
      return;
    }

    if (getJobPendingCount(job) <= 0) {
      clearJob();
      return;
    }

    job.updatedAt = Date.now();
    const saved = saveSessionJSON(JOB_KEY, job);
    state.launcherBadge = getJobPendingCount(job);
    updateLauncherBadge();
    updateJobSummary(job);
    return saved;
  }

  function clearJob() {
    try {
      sessionStorage.removeItem(JOB_KEY);
    } catch {
      // Ignore storage failures.
    }
    state.launcherBadge = 0;
    updateLauncherBadge();
    updateJobSummary(null);
  }

  function invalidateAutomationRun() {
    state.automationRunId += 1;

    if (state.processTimer !== null) {
      clearTimeout(state.processTimer);
      state.processTimer = null;
    }

    state.jobProcessing = false;
  }

  function isAutomationRunActive(runId) {
    return state.automationRunId === runId;
  }

  function clearAutomationQueue(message = 'Automation cleared. Ready for a new queue.') {
    invalidateAutomationRun();
    clearJob();
    setStatus(message, 'warn');
  }

  function updateLauncherBadge() {
    if (!els.badge) return;

    const count = Number(state.launcherBadge || 0);
    els.badge.textContent = count > 0 ? String(count) : '';
    els.badge.hidden = count <= 0;
  }

  function setStatus(message, level = 'info') {
    if (!els.status) return;

    const text = String(message || '').trim();

    if (!text) {
      els.status.hidden = true;
      els.status.textContent = '';
      return;
    }

    els.status.hidden = false;
    els.status.textContent = text;
    els.status.dataset.level = level;
  }

  function computeRailWidth(expanded = state.panelOpen) {
    if (!expanded) {
      return RAIL_COLLAPSED_WIDTH;
    }

    const idealWidth = window.innerWidth * RAIL_WIDTH_FACTOR;
    const contentMinWidth = window.innerWidth < 900 ? 280 : 520;
    const maxForViewport = Math.max(RAIL_COLLAPSED_WIDTH, window.innerWidth - contentMinWidth);
    const width = Math.min(Math.max(idealWidth, RAIL_WIDTH_MIN), RAIL_WIDTH_MAX, maxForViewport);
    return Math.round(Math.max(RAIL_COLLAPSED_WIDTH, width));
  }

  function findChatGPTShell() {
    return document.querySelector(CHATGPT_SHELL_SELECTOR) ||
      document.querySelector('#stage-slideover-sidebar')?.closest('div.flex.h-svh.w-screen.flex-col') ||
      document.querySelector('main')?.closest('div.flex.h-svh.w-screen.flex-col') ||
      document.querySelector('div.flex.h-svh.w-screen.flex-col');
  }

  function syncDockLayout() {
    const width = computeRailWidth(state.panelOpen);

    document.documentElement.style.setProperty('--tmq-rail-width', `${width}px`);
    document.documentElement.dataset.tmqQueueDock = '1';

    const shell = findChatGPTShell();
    if (shell) {
      shell.style.width = 'calc(100vw - var(--tmq-rail-width))';
      shell.style.maxWidth = 'calc(100vw - var(--tmq-rail-width))';
      shell.style.minWidth = '0';
      shell.style.transition = 'width 180ms ease, max-width 180ms ease';
    }
  }

  function scheduleDockLayoutSync() {
    if (state.dockLayoutTimer !== null) {
      return;
    }

    state.dockLayoutTimer = window.setTimeout(() => {
      state.dockLayoutTimer = null;
      syncDockLayout();
    }, 120);
  }

  function installDockLayoutStyle() {
    if (document.getElementById('tmq-page-dock-style')) {
      return;
    }

    const style = document.createElement('style');
    style.id = 'tmq-page-dock-style';
    style.textContent = `
      html[data-tmq-queue-dock="1"] body > div > div.flex.h-svh.w-screen.flex-col {
        width: calc(100vw - var(--tmq-rail-width)) !important;
        max-width: calc(100vw - var(--tmq-rail-width)) !important;
        transition: width 180ms ease, max-width 180ms ease;
      }

      html[data-tmq-queue-dock="1"] main {
        min-width: 0 !important;
      }

      html[data-tmq-queue-dock="1"] body {
        overflow-x: hidden !important;
      }

      .tmq-prompt-highlight {
        outline: 2px solid color-mix(in srgb, var(--text-primary, currentColor) 28%, transparent);
        outline-offset: 4px;
      }
    `;

    (document.head || document.documentElement).appendChild(style);
  }

  function updateRailChrome() {
    if (!els.panel) return;

    els.panel.classList.toggle('tmq-collapsed', !state.panelOpen);

    if (els.closeButton) {
      const isOpen = !!state.panelOpen;
      els.closeButton.title = isOpen ? 'Collapse panel' : 'Expand panel';
      els.closeButton.setAttribute('aria-label', isOpen ? 'Collapse queue panel' : 'Expand queue panel');
      els.closeButton.setAttribute('aria-expanded', String(isOpen));
    }

    if (els.badge) {
      els.badge.hidden = Number(state.launcherBadge || 0) <= 0;
    }

    syncDockLayout();
  }

  function setSequenceModalOpen(open) {
    state.sequenceModalOpen = !!open;
    if (els.modal) {
      els.modal.hidden = !state.sequenceModalOpen;
    }
  }

  function previewText(text, maxLength = 120) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
  }

  function getConversationIdFromPath(pathname = location.pathname || '/') {
    const match = String(pathname || '/').match(/(?:^|\/)c\/([^/?#]+)/i);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function getSessionValue(key, fallback = '') {
    try {
      const existing = sessionStorage.getItem(key);
      if (existing) {
        return existing;
      }

      const nextValue = typeof fallback === 'function' ? fallback() : fallback;
      if (nextValue) {
        sessionStorage.setItem(key, nextValue);
      }

      return nextValue || '';
    } catch {
      return typeof fallback === 'function' ? fallback() : fallback;
    }
  }

  function resetTransientConversationScope() {
    try {
      sessionStorage.setItem('tmq:new-chat-scope:v1', randomId('new-'));
    } catch {
      // Ignore storage failures.
    }
  }

  function getConversationStorageScope(pathname = location.pathname || '/') {
    const conversationId = getConversationIdFromPath(pathname);
    if (conversationId) {
      return `${location.host}:conversation:${conversationId}`;
    }

    const normalizedPath = pathname || '/';
    if (normalizedPath === '/') {
      const transientId = getSessionValue('tmq:new-chat-scope:v1', () => randomId('new-'));
      return `${location.host}:new:${transientId}`;
    }

    return `${location.host}:path:${normalizedPath}`;
  }

  function getScopeConversationId(scope) {
    const match = String(scope || '').match(/:conversation:([^:]+)$/);
    return match ? match[1] : '';
  }

  function isTransientConversationScope(scope) {
    return String(scope || '').includes(':new:') || !getScopeConversationId(scope);
  }

  function canAdoptConversationScope(job, nextScope) {
    if (!job || !nextScope) {
      return false;
    }

    const previousScope = String(job.conversationScope || '');
    if (!previousScope || previousScope === nextScope) {
      return true;
    }

    return isTransientConversationScope(previousScope) &&
      !!getScopeConversationId(nextScope);
  }

  function reconcileJobConversationScope(job, nextScope = getConversationStorageScope()) {
    if (!job) {
      return { ok: true, changed: false };
    }

    if (!job.conversationScope) {
      job.conversationScope = nextScope;
      return { ok: true, changed: true };
    }

    if (job.conversationScope === nextScope) {
      return { ok: true, changed: false };
    }

    if (canAdoptConversationScope(job, nextScope)) {
      job.conversationScope = nextScope;
      return { ok: true, changed: true };
    }

    return {
      ok: false,
      changed: false,
      error: 'Conversation changed. Queue preserved; return to the original chat or press Resume after reviewing the queue.'
    };
  }

  function ensureJobConversationScope(job) {
    const reconciliation = reconcileJobConversationScope(job);
    if (!reconciliation.ok) {
      adoptCurrentConversationScope(job);
      return { ok: true };
    }

    if (reconciliation.changed) {
      saveJob(job);
    }

    return { ok: true };
  }

  function adoptCurrentConversationScope(job, nextScope = getConversationStorageScope()) {
    if (!job) {
      return false;
    }

    job.conversationScope = nextScope;

    if (job.currentMessage && job.phase === 'sending' && hasVisibleSubmittedPrompt(job.currentMessage)) {
      job.phase = 'waiting';
    }

    return saveJob(job);
  }

  function getPromptIndexStorageKey(scope = getConversationStorageScope()) {
    return `${PROMPT_INDEX_KEY_PREFIX}${encodeURIComponent(scope)}`;
  }

  function getLegacyPromptIndexStorageKey() {
    return `${PROMPT_INDEX_KEY_PREFIX}${encodeURIComponent(`${location.host}${location.pathname || '/'}`)}`;
  }

  function normalizePromptText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function escapeAttributeValue(value) {
    return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function getPromptTurnContainer(node) {
    if (!node || typeof node.closest !== 'function') {
      return null;
    }

    return node.closest('section[data-turn="user"]') ||
      node.closest('[data-testid^="conversation-turn-"]') ||
      null;
  }

  function getPromptTurnOrder(node) {
    const container = getPromptTurnContainer(node);

    if (!container) {
      return null;
    }

    const testId = String(container.getAttribute('data-testid') || '');
    const match = testId.match(/conversation-turn-(\d+)/i);

    if (!match) {
      return null;
    }

    const parsed = Number.parseInt(match[1], 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function extractPromptText(node) {
    if (!node) {
      return '';
    }

    const bubble = node.querySelector?.('.user-message-bubble-color');
    if (bubble) {
      const bubbleText = normalizePromptText(bubble.innerText || bubble.textContent || '');
      if (bubbleText) {
        return bubbleText;
      }
    }

    const markdown = node.querySelector?.('.markdown');
    if (markdown) {
      const markdownText = normalizePromptText(markdown.innerText || markdown.textContent || '');
      if (markdownText) {
        return markdownText;
      }
    }

    return normalizePromptText(node.innerText || node.textContent || '');
  }

  function normalizePromptRecord(record) {
    if (!record || typeof record !== 'object' || Array.isArray(record)) {
      return null;
    }

    const messageId = typeof record.messageId === 'string' ? record.messageId.trim() : '';
    const turnId = typeof record.turnId === 'string' ? record.turnId.trim() : '';
    const text = normalizePromptText(record.text);
    const turnOrderValue = Number.parseInt(String(record.turnOrder ?? ''), 10);
    const turnOrder = Number.isFinite(turnOrderValue) && turnOrderValue > 0 ? Math.floor(turnOrderValue) : null;
    const textOrdinalValue = Number.parseInt(String(record.textOrdinal ?? ''), 10);
    const textOrdinal = Number.isFinite(textOrdinalValue) && textOrdinalValue > 0 ? Math.floor(textOrdinalValue) : 1;
    const positionHintValue = Number(record.positionHint);
    const capturedAtValue = Number(record.capturedAt);
    const updatedAtValue = Number(record.updatedAt);

    return {
      messageId,
      turnId,
      turnOrder,
      textOrdinal,
      positionHint: Number.isFinite(positionHintValue) ? positionHintValue : null,
      text,
      capturedAt: Number.isFinite(capturedAtValue) ? capturedAtValue : Date.now(),
      updatedAt: Number.isFinite(updatedAtValue) ? updatedAtValue : Date.now()
    };
  }

  function getPromptRecordKey(record) {
    if (!record) {
      return '';
    }

    if (record.messageId) {
      return `id:${record.messageId}`;
    }

    if (record.turnId) {
      return `turn-id:${record.turnId}`;
    }

    if (Number.isFinite(record.turnOrder)) {
      return `turn:${record.turnOrder}`;
    }

    const text = normalizePromptText(record.text).slice(0, 160);
    return text ? `text:${record.textOrdinal || 1}:${text}` : '';
  }

  function comparePromptRecords(a, b) {
    const aOrder = Number.isFinite(a?.turnOrder) ? a.turnOrder : Number.POSITIVE_INFINITY;
    const bOrder = Number.isFinite(b?.turnOrder) ? b.turnOrder : Number.POSITIVE_INFINITY;

    if (aOrder !== bOrder) {
      return aOrder - bOrder;
    }

    const aPosition = Number.isFinite(Number(a?.positionHint)) ? Number(a.positionHint) : Number.POSITIVE_INFINITY;
    const bPosition = Number.isFinite(Number(b?.positionHint)) ? Number(b.positionHint) : Number.POSITIVE_INFINITY;

    if (aPosition !== bPosition) {
      return aPosition - bPosition;
    }

    const aCaptured = Number.isFinite(Number(a?.capturedAt)) ? Number(a.capturedAt) : 0;
    const bCaptured = Number.isFinite(Number(b?.capturedAt)) ? Number(b.capturedAt) : 0;

    if (aCaptured !== bCaptured) {
      return aCaptured - bCaptured;
    }

    return normalizePromptText(a?.text).localeCompare(normalizePromptText(b?.text));
  }

  function mergePromptRecords(existing, incoming) {
    const merged = {
      ...existing,
      ...incoming
    };

    merged.messageId = incoming.messageId || existing.messageId || '';
    merged.turnId = incoming.turnId || existing.turnId || merged.messageId || '';
    merged.turnOrder = Number.isFinite(incoming.turnOrder) ? incoming.turnOrder : existing.turnOrder ?? null;
    merged.textOrdinal = incoming.textOrdinal || existing.textOrdinal || 1;
    merged.positionHint = Number.isFinite(Number(incoming.positionHint))
      ? Number(incoming.positionHint)
      : existing.positionHint ?? null;
    merged.text = normalizePromptText(incoming.text || existing.text);
    merged.capturedAt = Number.isFinite(Number(existing.capturedAt)) ? Number(existing.capturedAt) : Number(incoming.capturedAt) || Date.now();
    merged.updatedAt = Date.now();
    return merged;
  }

  function serializePromptRecord(record) {
    const normalized = normalizePromptRecord(record);

    if (!normalized || !normalized.text) {
      return null;
    }

    return {
      messageId: normalized.messageId,
      turnId: normalized.turnId,
      turnOrder: normalized.turnOrder,
      textOrdinal: normalized.textOrdinal,
      positionHint: normalized.positionHint,
      text: normalized.text,
      capturedAt: normalized.capturedAt,
      updatedAt: normalized.updatedAt
    };
  }

  function loadPromptIndex() {
    const scope = getConversationStorageScope();
    const storageKey = getPromptIndexStorageKey(scope);
    let raw = loadPersistentJSON(storageKey, []);
    if ((!Array.isArray(raw) || raw.length === 0) && getConversationIdFromPath()) {
      const legacyRaw = loadPersistentJSON(getLegacyPromptIndexStorageKey(), []);
      if (Array.isArray(legacyRaw) && legacyRaw.length > 0) {
        raw = legacyRaw;
      }
    }
    const byKey = new Map();

    if (Array.isArray(raw)) {
      for (const entry of raw) {
        const record = normalizePromptRecord(entry);

        if (!record || !record.text) {
          continue;
        }

        const key = getPromptRecordKey(record);
        if (!key) {
          continue;
        }

        const existing = byKey.get(key);
        const merged = existing ? mergePromptRecords(existing, record) : record;
        byKey.set(key, merged);
      }
    }

    const records = Array.from(byKey.values()).sort(comparePromptRecords);
    state.promptIndexScope = scope;
    state.promptIndexByKey = byKey;
    state.promptIndex = records;
    return records;
  }

  function savePromptIndex() {
    const scope = getConversationStorageScope();
    const storageKey = getPromptIndexStorageKey(scope);
    const serialized = state.promptIndex
      .slice()
      .sort(comparePromptRecords)
      .map(serializePromptRecord)
      .filter(Boolean);

    savePersistentJSON(storageKey, serialized);
    state.promptIndex = serialized.map((record) => ({ ...record }));
    state.promptIndexByKey = new Map(
      state.promptIndex.map((record) => [getPromptRecordKey(record), record])
    );
    state.promptIndexScope = scope;
  }

  function clearPromptIndex() {
    state.promptIndex = [];
    state.promptIndexByKey = new Map();
    state.promptIndexActiveKey = '';
    state.promptIndexScope = getConversationStorageScope();
    savePromptIndex();
    renderPromptIndex();
  }

  function upsertPromptRecord(record) {
    const normalized = normalizePromptRecord(record);

    if (!normalized || !normalized.text) {
      return false;
    }

    const key = getPromptRecordKey(normalized);
    if (!key) {
      return false;
    }

    const existing = state.promptIndexByKey.get(key);
    const nextRecord = existing ? mergePromptRecords(existing, normalized) : normalized;

    state.promptIndexByKey.set(key, nextRecord);

    const index = state.promptIndex.findIndex((entry) => getPromptRecordKey(entry) === key);
    if (index >= 0) {
      state.promptIndex[index] = nextRecord;
    } else {
      state.promptIndex.push(nextRecord);
    }

    return !existing ||
      existing.text !== nextRecord.text ||
      existing.turnOrder !== nextRecord.turnOrder ||
      existing.turnId !== nextRecord.turnId ||
      existing.positionHint !== nextRecord.positionHint;
  }

  function getNodePositionHint(node) {
    if (!node || typeof node.getBoundingClientRect !== 'function') {
      return null;
    }

    const rect = node.getBoundingClientRect();
    const scroller = findScrollableAncestor(node);

    if (scroller && typeof scroller.scrollTop === 'number') {
      const scrollerRect = scroller.getBoundingClientRect();
      return Math.round((scroller.scrollTop || 0) + rect.top - scrollerRect.top);
    }

    return Math.round((window.scrollY || 0) + rect.top);
  }

  function collectVisiblePromptRecords() {
    const nodes = Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
      .filter((node) => node instanceof HTMLElement && !isInOurUI(node) && isVisible(node));
    const records = [];
    const textCounts = new Map();

    for (const node of nodes) {
      const messageId = String(node.getAttribute('data-message-id') || '').trim();
      const turnContainer = getPromptTurnContainer(node);
      const turnId = String(
        turnContainer?.getAttribute('data-turn-id') ||
        turnContainer?.getAttribute('data-message-id') ||
        messageId ||
        ''
      ).trim();
      const turnOrder = getPromptTurnOrder(node);
      const text = extractPromptText(node);

      if (!text) {
        continue;
      }

      const textKey = normalizePromptText(text).slice(0, 160);
      const textOrdinal = (textCounts.get(textKey) || 0) + 1;
      textCounts.set(textKey, textOrdinal);

      records.push({
        messageId,
        turnId,
        turnOrder,
        textOrdinal,
        positionHint: getNodePositionHint(node),
        text,
        capturedAt: Date.now(),
        updatedAt: Date.now()
      });
    }

    return records;
  }

  function refreshPromptIndexFromDom() {
    const records = collectVisiblePromptRecords();
    let changed = false;

    for (const record of records) {
      changed = upsertPromptRecord(record) || changed;
    }

    if (changed) {
      state.promptIndex.sort(comparePromptRecords);
      savePromptIndex();
      renderPromptIndex();
    }

    return changed;
  }

  function renderPromptIndex() {
    if (!els.promptIndexList) {
      return;
    }

    const records = state.promptIndex
      .slice()
      .sort(comparePromptRecords);

    els.promptIndexList.innerHTML = '';

    if (!records.length) {
      const empty = document.createElement('div');
      empty.className = 'tmq-empty';
      empty.textContent = 'No user prompts captured yet.';
      els.promptIndexList.appendChild(empty);
      return;
    }

    records.forEach((record, index) => {
      const key = getPromptRecordKey(record);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'tmq-prompt-row';
      row.dataset.promptKey = key;
      row.dataset.active = key && key === state.promptIndexActiveKey ? '1' : '0';
      row.title = record.text;
      row.setAttribute('aria-label', `Jump to prompt ${index + 1}`);
      row.addEventListener('click', () => {
        void jumpToPromptRecord(record);
      });

      const number = document.createElement('span');
      number.className = 'tmq-prompt-number';
      number.textContent = `${index + 1}.`;

      const text = document.createElement('span');
      text.className = 'tmq-prompt-text';
      text.textContent = previewText(record.text, 120);

      row.appendChild(number);
      row.appendChild(text);
      els.promptIndexList.appendChild(row);
    });
  }

  function isScrollableElement(element) {
    if (!element || element === document.body || element === document.documentElement) {
      return false;
    }

    if (typeof element.scrollHeight !== 'number' || typeof element.clientHeight !== 'number') {
      return false;
    }

    const style = window.getComputedStyle(element);
    const overflowY = style.overflowY || '';
    return /(auto|scroll|overlay)/i.test(overflowY) && element.scrollHeight > element.clientHeight + 20;
  }

  function findScrollableAncestor(node) {
    let current = node instanceof Element ? node : null;

    while (current && current !== document.body && current !== document.documentElement) {
      if (isScrollableElement(current)) {
        return current;
      }

      current = current.parentElement;
    }

    return null;
  }

  function findConversationScroller() {
    const userNode = document.querySelector('[data-message-author-role="user"]');
    const turnNode = userNode ? getPromptTurnContainer(userNode) : document.querySelector('main');

    if (turnNode) {
      const ancestor = findScrollableAncestor(turnNode);
      if (ancestor) {
        return ancestor;
      }
    }

    const candidates = [
      document.querySelector('main'),
      document.querySelector('[data-scroll-root]'),
      document.querySelector('[data-scroll-root="true"]'),
      document.querySelector('div.flex.h-svh.w-screen.flex-col'),
      document.scrollingElement,
      document.documentElement
    ];

    for (const candidate of candidates) {
      if (candidate && typeof candidate.scrollHeight === 'number' && candidate.scrollHeight > candidate.clientHeight + 20) {
        return candidate;
      }
    }

    return document.scrollingElement || document.documentElement;
  }

  function getScrollPosition(scroller = findConversationScroller()) {
    if (!scroller || scroller === document.scrollingElement || scroller === document.documentElement || scroller === document.body) {
      return {
        kind: 'window',
        top: window.scrollY || 0,
        left: window.scrollX || 0
      };
    }

    return {
      kind: 'element',
      node: scroller,
      top: scroller.scrollTop || 0,
      left: scroller.scrollLeft || 0
    };
  }

  function restoreScrollPosition(position) {
    if (!position) {
      return;
    }

    if (position.kind === 'window') {
      window.scrollTo({
        top: position.top || 0,
        left: position.left || 0,
        behavior: 'auto'
      });
      return;
    }

    if (position.node) {
      position.node.scrollTop = position.top || 0;
      position.node.scrollLeft = position.left || 0;
    }
  }

  function scrollConversationBy(delta, scroller = findConversationScroller()) {
    if (!scroller) {
      window.scrollBy({
        top: delta,
        behavior: 'auto'
      });
      return;
    }

    if (scroller === document.scrollingElement || scroller === document.documentElement || scroller === document.body) {
      window.scrollBy({
        top: delta,
        behavior: 'auto'
      });
      return;
    }

    scroller.scrollTop += delta;
  }

  function getVisiblePromptTurnRange() {
    const records = collectVisiblePromptRecords();
    const turnOrders = records
      .map((record) => record.turnOrder)
      .filter((value) => Number.isFinite(value));

    if (!turnOrders.length) {
      return null;
    }

    return {
      min: Math.min(...turnOrders),
      max: Math.max(...turnOrders)
    };
  }

  function findPromptRecordByKey(key) {
    if (!key) {
      return null;
    }

    return state.promptIndexByKey.get(key) || null;
  }

  function findPromptNodeForRecord(record) {
    if (!record) {
      return null;
    }

    if (record.messageId) {
      const byId = document.querySelector(`[data-message-id="${escapeAttributeValue(record.messageId)}"]`);
      if (byId && !isInOurUI(byId)) {
        return byId;
      }
    }

    if (record.turnId) {
      const byTurnId = document.querySelector(`[data-turn-id="${escapeAttributeValue(record.turnId)}"] [data-message-author-role="user"], [data-message-id="${escapeAttributeValue(record.turnId)}"][data-message-author-role="user"]`);
      if (byTurnId && !isInOurUI(byTurnId)) {
        return byTurnId;
      }
    }

    if (Number.isFinite(record.turnOrder)) {
      const selector = `section[data-testid="conversation-turn-${record.turnOrder}"] [data-message-author-role="user"]`;
      const byTurn = document.querySelector(selector);
      if (byTurn && !isInOurUI(byTurn)) {
        return byTurn;
      }
    }

    const canonical = normalizePromptText(record.text);
    if (canonical) {
      const candidates = Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
        .filter((node) => node instanceof HTMLElement && !isInOurUI(node) && isVisible(node));

      return candidates.find((node) => normalizePromptText(extractPromptText(node)) === canonical) || null;
    }

    return null;
  }

  function highlightPromptNode(node) {
    if (!node) {
      return;
    }

    const turn = getPromptTurnContainer(node) || node.closest?.('section');
    const target = turn || node;

    target.classList.add('tmq-prompt-highlight');
    window.clearTimeout(target._tmqPromptHighlightTimer);
    target._tmqPromptHighlightTimer = window.setTimeout(() => {
      target.classList.remove('tmq-prompt-highlight');
    }, 2200);
  }

  function focusPromptRecord(record) {
    const key = getPromptRecordKey(record);
    state.promptIndexActiveKey = key;
    renderPromptIndex();
  }

  function scrollPromptNodeIntoView(node) {
    const target = getPromptTurnContainer(node) || node;

    if (target && typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({
        block: 'center',
        inline: 'nearest',
        behavior: 'smooth'
      });
    }

    highlightPromptNode(target);
  }

  async function crawlPromptHistory(targetRecord = null, direction = -1, restore = false) {
    const scroller = findConversationScroller();
    const initialScroll = restore ? getScrollPosition(scroller) : null;
    const step = Math.max(320, Math.round((window.innerHeight || 900) * PROMPT_INDEX_BACKFILL_STEP_RATIO));
    const target = targetRecord ? normalizePromptRecord(targetRecord) : null;
    let found = target ? findPromptNodeForRecord(target) : null;

    state.promptIndexBackfillRunning = true;

    try {
      if (!target && restore) {
        refreshPromptIndexFromDom();
      }

      for (let index = 0; index < PROMPT_INDEX_BACKFILL_MAX_STEPS; index += 1) {
        if (target && found) {
          break;
        }

        const position = getScrollPosition(scroller);
        const isWindowLike = !scroller ||
          scroller === document.scrollingElement ||
          scroller === document.documentElement ||
          scroller === document.body;
        const maxScrollTop = isWindowLike
          ? Math.max(0, (document.documentElement?.scrollHeight || document.body?.scrollHeight || 0) - window.innerHeight)
          : Math.max(0, (scroller.scrollHeight || 0) - (scroller.clientHeight || 0));
        const atEdge = direction < 0
          ? position.top <= 0
          : position.top >= maxScrollTop - 2;

        if (atEdge) {
          refreshPromptIndexFromDom();
          break;
        }

        scrollConversationBy(direction * step, scroller);
        await sleep(PROMPT_INDEX_BACKFILL_PAUSE_MS);
        refreshPromptIndexFromDom();

        if (target) {
          found = findPromptNodeForRecord(target);
        }
      }
    } finally {
      state.promptIndexBackfillRunning = false;

      if (restore && initialScroll) {
        restoreScrollPosition(initialScroll);
      }
    }

    if (target) {
      found = findPromptNodeForRecord(target) || found;
    }

    return found;
  }

  async function jumpToPromptRecord(record) {
    const target = normalizePromptRecord(record);

    if (!target || !target.text) {
      setStatus('Prompt not found.', 'warn');
      return;
    }

    const key = getPromptRecordKey(target);
    focusPromptRecord(target);

    let node = findPromptNodeForRecord(target);
    if (node) {
      scrollPromptNodeIntoView(node);
      const index = state.promptIndex.findIndex((entry) => getPromptRecordKey(entry) === key);
      setStatus(index >= 0 ? `Jumped to prompt ${index + 1}.` : 'Jumped to prompt.', 'success');
      return;
    }

    const visibleRange = getVisiblePromptTurnRange();
    let direction = -1;

    if (visibleRange && Number.isFinite(target.turnOrder)) {
      if (target.turnOrder > visibleRange.max) {
        direction = 1;
      } else if (target.turnOrder >= visibleRange.min && target.turnOrder <= visibleRange.max) {
        direction = -1;
      }
    }

    setStatus(direction < 0 ? 'Backfilling older prompts...' : 'Searching newer prompts...', 'info');
    node = await crawlPromptHistory(target, direction, false);

    if (!node) {
      const reverseDirection = direction * -1;
      node = await crawlPromptHistory(target, reverseDirection, false);
    }

    if (!node) {
      setStatus('That prompt is not currently loaded.', 'warn');
      return;
    }

    scrollPromptNodeIntoView(node);
    const index = state.promptIndex.findIndex((entry) => getPromptRecordKey(entry) === key);
    setStatus(index >= 0 ? `Jumped to prompt ${index + 1}.` : 'Jumped to prompt.', 'success');
  }

  async function backfillPromptIndex() {
    if (state.promptIndexBackfillRunning) {
      return;
    }

    setStatus('Backfilling older prompts...', 'info');
    await crawlPromptHistory(null, -1, true);
    refreshPromptIndexFromDom();
    savePromptIndex();
    renderPromptIndex();
    setStatus(`Captured ${state.promptIndex.length} prompt${state.promptIndex.length === 1 ? '' : 's'} in the index.`, 'success');
  }

  function schedulePromptIndexRefresh() {
    if (state.promptIndexRefreshTimer !== null) {
      return;
    }

    state.promptIndexRefreshTimer = window.setTimeout(() => {
      state.promptIndexRefreshTimer = null;
      refreshPromptIndexFromDom();
    }, PROMPT_INDEX_REFRESH_DEBOUNCE_MS);
  }

  function installPromptIndexObservers() {
    if (state.promptIndexObserver || !document.body) {
      return;
    }

    state.promptIndexObserver = new MutationObserver(() => {
      schedulePromptIndexRefresh();
      scheduleDockLayoutSync();
    });

    state.promptIndexObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });

    document.addEventListener('scroll', schedulePromptIndexRefresh, true);
    window.addEventListener('resize', schedulePromptIndexRefresh, { passive: true });
  }

  function handleLocationChange() {
    const href = location.href;
    const hrefChanged = href !== state.lastLocationHref;
    state.lastLocationHref = href;

    if ((location.pathname || '/') === '/' && state.promptIndexScope && !state.promptIndexScope.includes(':new:')) {
      resetTransientConversationScope();
    }

    const scope = getConversationStorageScope();
    const scopeChanged = scope !== state.promptIndexScope;

    if (scopeChanged) {
      loadPromptIndex();
      state.promptIndexActiveKey = '';
      renderPromptIndex();
      schedulePromptIndexRefresh();
    }

    const job = loadJob();
    if (!job || job.paused) {
      return;
    }

    if (!hrefChanged && !scopeChanged) {
      return;
    }

    const reconciliation = reconcileJobConversationScope(job, scope);
    if (!reconciliation.ok) {
      adoptCurrentConversationScope(job, scope);
      scheduleProcess();
      return;
    }

    saveJob(job);
    scheduleProcess();
  }

  function installLocationWatcher() {
    if (state.locationWatcherInstalled) {
      return;
    }

    state.locationWatcherInstalled = true;

    const notify = () => {
      window.dispatchEvent(new Event('tmq-locationchange'));
    };

    for (const methodName of ['pushState', 'replaceState']) {
      const original = history[methodName];
      if (typeof original !== 'function' || original.__tmqWrapped) {
        continue;
      }

      const wrapped = function (...args) {
        const result = original.apply(this, args);
        notify();
        return result;
      };

      wrapped.__tmqWrapped = true;
      history[methodName] = wrapped;
    }

    window.addEventListener('popstate', notify);
    window.addEventListener('tmq-locationchange', handleLocationChange);
  }

  function setPromptDisplay(element, text, emptyText, maxLength = 88) {
    if (!element) return;

    const value = String(text || '').replace(/\s+/g, ' ').trim();

    if (!value) {
      element.textContent = emptyText;
      element.title = '';
      element.dataset.empty = '1';
      return;
    }

    element.textContent = previewText(value, maxLength);
    element.title = value;
    element.dataset.empty = '0';
  }

  function updateJobSummary(job = loadJob()) {
    if (!els.jobSummary || !els.jobMeta || !els.currentPrompt || !els.nextPrompt) return;

    if (!job) {
      els.jobSummary.textContent = 'Idle · 0 pending';
      els.jobMeta.textContent = 'Queue is idle.';
      setPromptDisplay(els.currentPrompt, '', 'No current prompt.');
      setPromptDisplay(els.nextPrompt, '', 'No next prompt.');
      return;
    }

    const pending = getJobPendingCount(job);
    const total = (job.completedCount || 0) + pending;
    const phase = job.paused
      ? 'Paused'
      : job.currentMessage
        ? (job.phase === 'waiting' ? 'Waiting' : 'Running')
        : (job.phase === 'done' ? 'Done' : 'Queued');
    const current = job.currentMessage || '';
    const next = job.currentMessage
      ? (job.queue.length > 1 ? (job.queue[1] || '') : '')
      : (job.queue[0] || '');

    els.jobSummary.textContent = `${phase} · ${pending} pending of ${total}`;
    els.jobMeta.textContent = job.paused
      ? 'Queue is paused. Hover any prompt for the full text.'
      : 'Hover any prompt for the full text.';
    setPromptDisplay(els.currentPrompt, current, 'No current prompt.');
    setPromptDisplay(els.nextPrompt, next, 'No next prompt.');
  }

  function renderDraftList() {
    if (!els.draftList) return;

    els.draftList.innerHTML = '';

    if (!state.draftMessages.length) {
      const empty = document.createElement('div');
      empty.className = 'tmq-empty';
      empty.textContent = 'No draft messages yet.';
      els.draftList.appendChild(empty);
      return;
    }

    state.draftMessages.forEach((message, index) => {
      const item = document.createElement('div');
      item.className = 'tmq-message-row';

      const text = document.createElement('div');
      text.className = 'tmq-message-text';
      text.textContent = `${index + 1}. ${message}`;

      const controls = document.createElement('div');
      controls.className = 'tmq-message-controls';

      const editButton = document.createElement('button');
      editButton.type = 'button';
      editButton.textContent = 'Edit';
      editButton.addEventListener('click', () => {
        if (els.draftInput) {
          els.draftInput.value = message;
          els.draftInput.focus();
        }

        state.draftMessages.splice(index, 1);
        saveDraftState();
        renderDraftList();
      });

      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.textContent = 'Remove';
      removeButton.addEventListener('click', () => {
        state.draftMessages.splice(index, 1);
        saveDraftState();
        renderDraftList();
      });

      controls.appendChild(editButton);
      controls.appendChild(removeButton);
      item.appendChild(text);
      item.appendChild(controls);
      els.draftList.appendChild(item);
    });
  }

  function renderSequenceSelect() {
    if (!els.sequenceSelect) return;

    const names = Object.keys(state.sequences).sort((a, b) => a.localeCompare(b));
    const current = state.selectedSequence && state.sequences[state.selectedSequence]
      ? state.selectedSequence
      : '';

    els.sequenceSelect.innerHTML = '';

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = names.length ? 'Select a saved sequence' : 'Save a sequence first';
    els.sequenceSelect.appendChild(placeholder);

    names.forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      els.sequenceSelect.appendChild(option);
    });

    els.sequenceSelect.value = current;

    if (current && state.sequences[current]) {
      state.selectedSequence = current;
      if (els.sequenceName) {
        els.sequenceName.value = current;
      }
    }

    saveDraftState();
  }

  function renderPanelFromState() {
    if (els.draftInput) {
      els.draftInput.value = '';
    }

    if (els.sequenceName) {
      els.sequenceName.value = state.draftName || state.selectedSequence || '';
    }

    renderDraftList();
    renderSequenceSelect();
    renderPromptIndex();
    updateJobSummary();
  }

  function openPanel(open) {
    state.panelOpen = !!open;
    updateRailChrome();
    writeUiOpen(state.panelOpen);
  }

  function togglePanel() {
    openPanel(!state.panelOpen);
  }

  function setButtonDisabled(button, disabled) {
    if (!button) return;
    button.disabled = !!disabled;
  }

  function readSequenceName() {
    return String(els.sequenceName?.value || '').trim();
  }

  function syncDraftFromInputs() {
    state.draftName = readSequenceName();
    saveDraftState();
  }

  function addDraftMessage() {
    const text = String(els.draftInput?.value || '').trim();

    if (!text) {
      setStatus('Type a draft message first.', 'warn');
      return;
    }

    state.draftMessages.push(text);
    if (els.draftInput) {
      els.draftInput.value = '';
      els.draftInput.focus();
    }

    saveDraftState();
    renderDraftList();
    setStatus(`Added message ${state.draftMessages.length}.`, 'success');
  }

  function loadSequence(name) {
    const sequence = state.sequences[name];

    if (!sequence) {
      setStatus('Select a saved sequence first.', 'warn');
      return;
    }

    state.selectedSequence = name;
    state.draftName = name;
    state.draftMessages = [...sequence];

    if (els.sequenceName) {
      els.sequenceName.value = name;
    }

    saveDraftState();
    renderDraftList();
    renderSequenceSelect();
    setStatus(`Loaded "${name}".`, 'success');
  }

  function saveSequence() {
    const name = readSequenceName();

    if (!name) {
      setStatus('Enter a sequence name first.', 'warn');
      return;
    }

    if (!state.draftMessages.length) {
      setStatus('Add at least one draft message before saving.', 'warn');
      return;
    }

    state.sequences[name] = [...state.draftMessages];
    state.selectedSequence = name;
    state.draftName = name;
    const saved = saveSequences();
    saveDraftState();
    renderSequenceSelect();
    setStatus(
      saved
        ? `Saved "${name}" with ${state.draftMessages.length} messages.`
        : `Could not persist "${name}" to browser storage. It may only exist until reload.`,
      saved ? 'success' : 'error'
    );
  }

  function deleteSequence() {
    const name = state.selectedSequence || readSequenceName();

    if (!name || !state.sequences[name]) {
      setStatus('Select a saved sequence to delete.', 'warn');
      return;
    }

    delete state.sequences[name];
    state.selectedSequence = '';
    if (els.sequenceName) {
      els.sequenceName.value = '';
    }

    const saved = saveSequences();
    saveDraftState();
    renderSequenceSelect();
    setStatus(
      saved
        ? `Deleted "${name}".`
        : `Deleted "${name}" in memory, but storage did not confirm the change.`,
      saved ? 'warn' : 'error'
    );
  }

  function startSequenceFromDraft() {
    const messages = [...state.draftMessages];

    if (!messages.length) {
      setStatus('No draft messages to send.', 'warn');
      return;
    }

    const startRaw = String(els.startStep?.value || '').trim();
    const endRaw = String(els.endStep?.value || '').trim();
    const useFullSequence = !startRaw || !endRaw;
    const startStep = useFullSequence
      ? 1
      : parsePositiveStep(startRaw, 1);
    const requestedEndStep = useFullSequence
      ? messages.length
      : parsePositiveStep(endRaw, messages.length);
    const endStep = Math.min(messages.length, requestedEndStep);

    if (startStep > messages.length) {
      setStatus(`Start step ${startStep} is beyond the draft length (${messages.length}).`, 'warn');
      return;
    }

    if (endStep < startStep) {
      setStatus(`End step ${endStep} must be at least start step ${startStep}.`, 'warn');
      return;
    }

    const queueMessages = messages.slice(startStep - 1, endStep);
    const existing = loadJob();
    const rangeText = formatSequenceRange(startStep, endStep);

    if (existing) {
      const addedCount = appendMessagesToJob(existing, queueMessages);

      if (!addedCount) {
        setStatus('No messages were added to the queue.', 'warn');
        return;
      }

      if (!saveJob(existing)) {
        setStatus('Could not persist the updated queue in this tab.', 'error');
        return;
      }
      clearSequenceRangeInputs();
      setStatus(
        existing.paused
          ? `Queued sequence from ${rangeText} behind a paused run (${addedCount} message${addedCount === 1 ? '' : 's'}).`
          : `Queued sequence from ${rangeText} behind the current run (${addedCount} message${addedCount === 1 ? '' : 's'}).`,
        'success'
      );

      if (!existing.paused) {
        scheduleProcess();
      }

      return;
    }

    const job = createJobFromMessages(messages, { startStep, endStep, kind: 'sequence' });

    if (!job) {
      setStatus(`Start step ${startStep} is beyond the draft length (${messages.length}).`, 'warn');
      return;
    }

    if (!saveJob(job)) {
      setStatus('Could not persist the queue in this tab.', 'error');
      return;
    }
    clearSequenceRangeInputs();
    setStatus(
      startStep > 1 || endStep < messages.length
          ? `Started sequence from ${rangeText} with ${job.queue.length} message${job.queue.length === 1 ? '' : 's'}.`
        : `Started sequence with ${job.queue.length} message${job.queue.length === 1 ? '' : 's'}.`,
      'success'
    );
    scheduleProcess();
  }

  function enqueueNextMessage() {
    const text = String(els.nextMessage?.value || '').trim();

    if (!text) {
      setStatus('Type a message to send next.', 'warn');
      return;
    }

    if (els.nextMessage) {
      els.nextMessage.value = '';
    }

    const job = loadJob();

    if (job) {
      const addedCount = appendMessagesToJob(job, [text]);

      if (!addedCount) {
        setStatus('Type a message to send next.', 'warn');
        return;
      }

      if (!saveJob(job)) {
        setStatus('Could not persist the updated queue in this tab.', 'error');
        return;
      }
      setStatus(
        job.paused
          ? `Queued 1 message behind a paused run. ${getJobPendingCount(job)} pending.`
          : `Queued 1 message behind the current run. ${getJobPendingCount(job)} pending.`,
        'success'
      );

      if (!job.paused) {
        scheduleProcess();
      }

      return;
    }

    const newJob = createJobFromMessages([text], { kind: 'single' });

    if (!newJob) {
      setStatus('Type a message to send next.', 'warn');
      return;
    }

    if (!saveJob(newJob)) {
      setStatus('Could not persist the queue in this tab.', 'error');
      return;
    }
    setStatus('Started a single-entry sequence for the current tab.', 'success');
    scheduleProcess();
  }

  function stopAutomation() {
    const job = loadJob();

    if (!job) {
      setStatus('No active automation to stop.', 'warn');
      return;
    }

    if (job.paused) {
      clearAutomationQueue();
      return;
    }

    pauseAutomation('Automation paused. Press Stop again to clear the queue.');
  }

  function pauseAutomation(message = 'Automation paused. Queue preserved.') {
    const job = loadJob();

    if (!job) {
      setStatus('No active automation to stop.', 'warn');
      return false;
    }

    job.paused = true;
    job.phase = job.currentMessage ? job.phase || 'waiting' : 'queued';
    saveJob(job);
    setStatus(message, 'warn');
    return true;
  }

  function resumeAutomation() {
    const job = loadJob();

    if (!job) {
      setStatus('No saved automation to resume.', 'warn');
      return;
    }

    if (!job.paused) {
      setStatus('Automation is already running.', 'info');
      scheduleProcess();
      return;
    }

    job.paused = false;
    job.conversationScope = getConversationStorageScope();
    job.readyAfter = Date.now() + CHATGPT_PAGE_SETTLE_MS;

    if (job.currentMessage && job.phase === 'sending' && hasVisibleSubmittedPrompt(job.currentMessage)) {
      job.phase = 'waiting';
    }

    saveJob(job);
    setStatus('Resuming automation...', 'info');
    scheduleProcess();
  }

  async function waitForSendButtonReady(runId) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < CHATGPT_SEND_BUTTON_WAIT_MS) {
      if (!isAutomationRunActive(runId)) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const job = loadJob();
      if (!job) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const scopeResult = ensureJobConversationScope(job);
      if (!scopeResult.ok) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: scopeResult.error
        };
      }

      if (job.paused) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: 'Automation is paused.'
        };
      }

      const page = inspectPageState();
      if (page.hasRateLimitNotice) {
        return {
          ok: false,
          retryable: false,
          rateLimited: true,
          error: 'ChatGPT is rate limited.',
          details: page
        };
      }

      const button = findSendButton();
      if (button && !button.disabled && button.getAttribute('aria-disabled') !== 'true') {
        return { ok: true, button };
      }

      await sleep(POLL_INTERVAL_MS);
    }

    return {
      ok: false,
      retryable: true,
      error: 'Send button never became ready.'
    };
  }

  async function waitForPromptSubmission(text, runId) {
    const startedAt = Date.now();

    while (Date.now() - startedAt < CHATGPT_SEND_BUTTON_WAIT_MS) {
      if (!isAutomationRunActive(runId)) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const job = loadJob();
      if (!job) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      if (job.paused) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: 'Automation is paused.'
        };
      }

      const page = inspectPageState();
      const composer = findComposer();
      const composerEmpty = !!composer && !normalizePromptText(getComposerText(composer));

      if (page.hasRateLimitNotice) {
        return {
          ok: false,
          retryable: false,
          rateLimited: true,
          error: 'ChatGPT is rate limited.',
          details: page
        };
      }

      if (page.generating || hasVisibleSubmittedPrompt(text) || composerEmpty) {
        return {
          ok: true,
          details: {
            acceptedByPage: true,
            generating: page.generating,
            promptVisible: hasVisibleSubmittedPrompt(text),
            composerEmpty
          }
        };
      }

      await sleep(POLL_INTERVAL_MS);
    }

    return {
      ok: false,
      retryable: true,
      error: 'Prompt was not confirmed submitted.'
    };
  }

  async function sendPrompt(text, runId) {
    if (!isAutomationRunActive(runId)) {
      return {
        ok: false,
        retryable: false,
        error: 'Automation was stopped.'
      };
    }

    const initialJob = loadJob();
    if (!initialJob) {
      return {
        ok: false,
        retryable: false,
        error: 'Automation was stopped.'
      };
    }

    if (initialJob.paused) {
      return {
        ok: false,
        retryable: false,
        paused: true,
        error: 'Automation is paused.'
      };
    }

    const initialScopeResult = ensureJobConversationScope(initialJob);
    if (!initialScopeResult.ok) {
      return {
        ok: false,
        retryable: false,
        paused: true,
        error: initialScopeResult.error
      };
    }

    const initialPage = inspectPageState();
    if (initialPage.hasRateLimitNotice) {
      return {
        ok: false,
        retryable: false,
        rateLimited: true,
        error: 'ChatGPT is rate limited.',
        details: initialPage
      };
    }

    const composer = findComposer();

    if (!composer) {
      return {
        ok: false,
        retryable: true,
        error: 'Composer was not found yet.'
      };
    }

    setComposerText(composer, text);
    await sleep(CHATGPT_SEND_DELAY_MS);

    if (!composerTextMatches(composer, text)) {
      clearComposer(composer);
      await sleep(150);
      setComposerText(composer, text);
      await sleep(CHATGPT_SEND_DELAY_MS);
    }

    if (!composerTextMatches(composer, text)) {
      return {
        ok: false,
        retryable: true,
        error: 'Composer did not retain the queued prompt.'
      };
    }

    if (!isAutomationRunActive(runId)) {
      return {
        ok: false,
        retryable: false,
        error: 'Automation was stopped.'
      };
    }

    const latestJob = loadJob();
    if (!latestJob) {
      return {
        ok: false,
        retryable: false,
        error: 'Automation was stopped.'
      };
    }

    if (latestJob.paused) {
      return {
        ok: false,
        retryable: false,
        paused: true,
        error: 'Automation is paused.'
      };
    }

    const latestScopeResult = ensureJobConversationScope(latestJob);
    if (!latestScopeResult.ok) {
      return {
        ok: false,
        retryable: false,
        paused: true,
        error: latestScopeResult.error
      };
    }

    const latestPage = inspectPageState();
    if (latestPage.hasRateLimitNotice) {
      return {
        ok: false,
        retryable: false,
        rateLimited: true,
        error: 'ChatGPT is rate limited.',
        details: latestPage
      };
    }

    const buttonResult = await waitForSendButtonReady(runId);

    if (!isAutomationRunActive(runId)) {
      return {
        ok: false,
        retryable: false,
        error: 'Automation was stopped.'
      };
    }

    if (!buttonResult.ok) {
      return buttonResult;
    }

    const sendButton = buttonResult.button || findSendButton();
    if (sendButton) {
      sendButton.click();
      return waitForPromptSubmission(text, runId);
    }

    composer.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      code: 'Enter',
      bubbles: true,
      cancelable: true
    }));

    composer.dispatchEvent(new KeyboardEvent('keyup', {
      key: 'Enter',
      code: 'Enter',
      bubbles: true,
      cancelable: true
    }));

    const submissionResult = await waitForPromptSubmission(text, runId);
    if (!submissionResult.ok) {
      return submissionResult;
    }

    return {
      ok: true,
      details: { usedKeyboardFallback: true }
    };
  }

  async function waitForStableIdle(job, runId) {
    const startedAt = Date.now();
    let idleSince = 0;
    let lastProgressStatusAt = startedAt;
    let lastBusyReason = '';
    let showedReadyWait = false;

    while (true) {
      if (!isAutomationRunActive(runId)) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const currentJob = loadJob();
      if (!currentJob) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const scopeResult = ensureJobConversationScope(currentJob);
      if (!scopeResult.ok) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: scopeResult.error
        };
      }

      if (currentJob.paused) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: 'Automation is paused.'
        };
      }

      const readyAfter = Number(currentJob?.readyAfter || job?.readyAfter || 0);
      if (readyAfter && Date.now() < readyAfter) {
        if (!showedReadyWait || Date.now() - lastProgressStatusAt >= WAIT_PROGRESS_STATUS_MS) {
          const remainingMs = Math.max(0, readyAfter - Date.now());
          setStatus(`Waiting for ChatGPT page settle (${Math.ceil(remainingMs / 1000)}s).`, 'info');
          showedReadyWait = true;
          lastProgressStatusAt = Date.now();
        }
        await sleep(Math.min(POLL_INTERVAL_MS, readyAfter - Date.now()));
        continue;
      }

      const page = inspectPageState();

      if (page.hasRateLimitNotice) {
        return {
          ok: false,
          retryable: false,
          rateLimited: true,
          error: 'ChatGPT is rate limited.',
          details: page
        };
      }

      if (page.hasTryAgainButton || page.hasError) {
        return {
          ok: false,
          retryable: false,
          error: 'ChatGPT showed an error or retry state.',
          details: page
        };
      }

      const busyReason = getQueueBusyReason(page);
      const busy = !!busyReason;

      if (busy) {
        idleSince = 0;
        if (busyReason !== lastBusyReason || Date.now() - lastProgressStatusAt >= WAIT_PROGRESS_STATUS_MS) {
          setStatus(`Still waiting for ChatGPT to become idle before sending (${busyReason}).`, 'info');
          lastBusyReason = busyReason;
          lastProgressStatusAt = Date.now();
        }
        await sleep(POLL_INTERVAL_MS);
        continue;
      }

      if (!idleSince) {
        idleSince = Date.now();
        await sleep(POLL_INTERVAL_MS);
        continue;
      }

      if (Date.now() - idleSince >= CHATGPT_IDLE_STABILITY_MS) {
        return {
          ok: true,
          details: page
        };
      }

      await sleep(POLL_INTERVAL_MS);
    }
  }

  function hasVisibleSubmittedPrompt(text) {
    const canonical = normalizePromptText(text);
    if (!canonical) {
      return false;
    }

    const nodes = Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
      .filter((node) => node instanceof HTMLElement && !isInOurUI(node) && isVisible(node));

    if (!nodes.length) {
      return false;
    }

    return nodes
      .slice(-3)
      .some((node) => normalizePromptText(extractPromptText(node)) === canonical);
  }

  async function waitForResponse(job, runId) {
    const startedAt = Date.now();
    let sawBusy = false;
    let idleSince = 0;
    let noBusyIdleSince = 0;
    let lastProgressStatusAt = startedAt;
    let lastBusyReason = '';

    while (true) {
      if (!isAutomationRunActive(runId)) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const currentJob = loadJob();
      if (!currentJob) {
        return {
          ok: false,
          retryable: false,
          error: 'Automation was stopped.'
        };
      }

      const scopeResult = ensureJobConversationScope(currentJob);
      if (!scopeResult.ok) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: scopeResult.error
        };
      }

      if (currentJob.paused) {
        return {
          ok: false,
          retryable: false,
          paused: true,
          error: 'Automation is paused.'
        };
      }

      const page = inspectPageState();

      if (page.hasRateLimitNotice) {
        return {
          ok: false,
          retryable: false,
          rateLimited: true,
          error: 'ChatGPT is rate limited.',
          details: page
        };
      }

      if (page.hasTryAgainButton || page.hasError) {
        return {
          ok: false,
          retryable: false,
          error: 'ChatGPT showed an error or retry state.',
          details: page
        };
      }

      const busyReason = getQueueBusyReason(page, { requireComposer: false });
      if (busyReason) {
        sawBusy = true;
        idleSince = 0;
        noBusyIdleSince = 0;
        if (busyReason !== lastBusyReason || Date.now() - lastProgressStatusAt >= WAIT_PROGRESS_STATUS_MS) {
          setStatus(`ChatGPT is still working (${busyReason}). Queue wait is uncapped.`, 'info');
          lastBusyReason = busyReason;
          lastProgressStatusAt = Date.now();
        }
        await sleep(POLL_INTERVAL_MS);
        continue;
      }

      if (sawBusy) {
        if (!idleSince) {
          idleSince = Date.now();
          await sleep(POLL_INTERVAL_MS);
          continue;
        }

        if (Date.now() - idleSince >= CHATGPT_IDLE_STABILITY_MS) {
          return {
            ok: true,
            details: page
          };
        }

        await sleep(POLL_INTERVAL_MS);
        continue;
      }

      if (hasVisibleSubmittedPrompt(currentJob.currentMessage)) {
        if (!noBusyIdleSince) {
          noBusyIdleSince = Date.now();
          await sleep(POLL_INTERVAL_MS);
          continue;
        }

        if (Date.now() - noBusyIdleSince >= CHATGPT_NO_BUSY_COMPLETION_GRACE_MS) {
          return {
            ok: true,
            details: {
              assumedCompleteWithoutBusyIndicator: true,
              elapsedMs: Date.now() - startedAt,
              page
            }
          };
        }
      } else {
        noBusyIdleSince = 0;
      }

      if (Date.now() - lastProgressStatusAt >= WAIT_PROGRESS_STATUS_MS) {
        setStatus('Waiting for ChatGPT response state. Queue wait is uncapped.', 'info');
        lastProgressStatusAt = Date.now();
      }

      await sleep(POLL_INTERVAL_MS);
    }
  }

  function scheduleProcess() {
    if (state.jobProcessing) {
      return;
    }

    const runId = state.automationRunId + 1;
    state.automationRunId = runId;
    state.jobProcessing = true;
    state.processTimer = setTimeout(() => {
      state.processTimer = null;
      processJob(runId)
        .catch((error) => {
          const message = error instanceof Error ? error.message : String(error || 'unknown error');
          console.error('[Queue Flow] Automation processor failed:', error);
          if (state.automationRunId === runId) {
            setStatus(`Automation error while waiting: ${message}`, 'error');
          }
        })
        .finally(() => {
          if (state.automationRunId === runId) {
            state.jobProcessing = false;
          }
        });
    }, 0);
  }

  async function processJob(runId) {
    while (true) {
      if (!isAutomationRunActive(runId)) {
        return;
      }

      const job = loadJob();

      if (!job) {
        if (!isAutomationRunActive(runId)) {
          return;
        }

        setStatus('', 'info');
        updateJobSummary(null);
        return;
      }

      if (job.paused) {
        if (!isAutomationRunActive(runId)) {
          return;
        }

        setStatus('Automation paused. Queue preserved.', 'warn');
        updateJobSummary(job);
        return;
      }

      const scopeResult = ensureJobConversationScope(job);
      if (!scopeResult.ok) {
        setStatus(scopeResult.error, 'warn');
        updateJobSummary(loadJob());
        return;
      }

      state.launcherBadge = getJobPendingCount(job);
      updateLauncherBadge();
      updateJobSummary(job);

      if (!isAutomationRunActive(runId)) {
        return;
      }

      if (job.currentMessage && job.phase === 'waiting') {
        setStatus('Waiting for the current response to finish...', 'info');
        const waitResult = await waitForResponse(job, runId);

        if (!isAutomationRunActive(runId)) {
          return;
        }

        if (!waitResult.ok) {
          if (waitResult.retryable) {
            await sleep(POLL_INTERVAL_MS);
            continue;
          }

          setStatus(waitResult.error, 'error');
          return;
        }

        const completed = loadJob();
        if (!completed) return;

        if (!isAutomationRunActive(runId)) {
          return;
        }

        completed.queue.shift();
        completed.currentMessage = '';
        completed.phase = completed.queue.length ? 'queued' : 'done';
        completed.completedCount = (completed.completedCount || 0) + 1;
        completed.readyAfter = 0;
        completed.updatedAt = Date.now();

        if (!completed.queue.length) {
          clearJob();
          setStatus('Queue complete.', 'success');
          return;
        }

        saveJob(completed);
        continue;
      }

      if (job.currentMessage && job.phase === 'sending') {
        if (!isAutomationRunActive(runId)) {
          return;
        }

        const page = inspectPageState();

        if (page.generating || hasVisibleSubmittedPrompt(job.currentMessage)) {
          const waiting = loadJob();
          if (!waiting) return;

          if (!isAutomationRunActive(runId)) {
            return;
          }

          waiting.phase = 'waiting';
          waiting.updatedAt = Date.now();
          saveJob(waiting);
          continue;
        }
      }

      if (!job.currentMessage && job.queue.length > 0) {
        if (!isAutomationRunActive(runId)) {
          return;
        }

        job.currentMessage = job.queue[0];
        job.phase = 'sending';
        job.readyAfter = 0;
        job.updatedAt = Date.now();
        saveJob(job);
      }

      if (!job.currentMessage) {
        if (!isAutomationRunActive(runId)) {
          return;
        }

        clearJob();
        setStatus('', 'info');
        return;
      }

      setStatus('Waiting for the page to become idle before sending the current prompt.', 'info');
      const idleResult = await waitForStableIdle(job, runId);

      if (!isAutomationRunActive(runId)) {
        return;
      }

      if (!idleResult.ok) {
        if (idleResult.retryable) {
          await sleep(POLL_INTERVAL_MS);
          continue;
        }

        if (idleResult.rateLimited) {
          pauseAutomation('ChatGPT is rate limited. Queue preserved.');
          return;
        }

        if (idleResult.paused) {
          setStatus('Automation paused. Queue preserved.', 'warn');
          return;
        }

        setStatus(idleResult.error, 'error');
        return;
      }

      setStatus('Sending the current prompt.', 'info');
      const sendResult = await sendPrompt(job.currentMessage, runId);

      if (!isAutomationRunActive(runId)) {
        return;
      }

      if (!sendResult.ok) {
        if (sendResult.retryable) {
          await sleep(POLL_INTERVAL_MS);
          continue;
        }

        if (sendResult.rateLimited) {
          pauseAutomation('ChatGPT is rate limited. Queue preserved.');
          return;
        }

        if (sendResult.paused) {
          setStatus('Automation paused. Queue preserved.', 'warn');
          return;
        }

        setStatus(sendResult.error, 'error');
        return;
      }

      const afterSend = loadJob();
      if (!afterSend) return;

      if (!isAutomationRunActive(runId)) {
        return;
      }

      afterSend.phase = 'waiting';
      afterSend.updatedAt = Date.now();
      saveJob(afterSend);

      const waitResult = await waitForResponse(afterSend, runId);

      if (!isAutomationRunActive(runId)) {
        return;
      }

      if (!waitResult.ok) {
        if (waitResult.retryable) {
          await sleep(POLL_INTERVAL_MS);
          continue;
        }

        if (waitResult.rateLimited) {
          pauseAutomation('ChatGPT is rate limited. Queue preserved.');
          return;
        }

        if (waitResult.paused) {
          setStatus('Automation paused. Queue preserved.', 'warn');
          return;
        }

        setStatus(waitResult.error, 'error');
        return;
      }

      const finished = loadJob();
      if (!finished) return;

      if (!isAutomationRunActive(runId)) {
        return;
      }

      finished.queue.shift();
      finished.currentMessage = '';
      finished.completedCount = (finished.completedCount || 0) + 1;
      finished.phase = finished.queue.length ? 'queued' : 'done';
      finished.readyAfter = 0;
      finished.updatedAt = Date.now();

      if (!finished.queue.length) {
        clearJob();
        setStatus('Queue complete.', 'success');
        return;
      }

      saveJob(finished);
    }
  }

  function buildUi() {
    host = document.createElement('div');
    host.id = ROOT_ID;
    host.style.position = 'fixed';
    host.style.inset = '0';
    host.style.zIndex = '2147483647';
    host.style.pointerEvents = 'none';

    shadow = host.attachShadow({ mode: 'open' });

    document.body.appendChild(host);
    installDockLayoutStyle();

    const style = document.createElement('style');
    style.textContent = `
      :host {
        all: initial;
      }

      *, *::before, *::after {
        box-sizing: border-box;
      }

      .tmq-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 18px;
        height: 18px;
        padding: 0 5px;
        border-radius: 999px;
        background: #f97316;
        color: white;
        font-size: 11px;
        font-weight: 700;
        line-height: 1;
        transform: translateY(-1px);
      }

      .tmq-panel {
        position: fixed;
        inset-block: 0;
        inset-inline-end: 0;
        width: var(--tmq-rail-width, 420px);
        max-height: 100vh;
        pointer-events: auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 12px 12px 14px;
        border-inline-start: 1px solid var(--border-light);
        background: var(--sidebar-surface-primary, var(--bg-primary));
        color: var(--text-primary);
        box-shadow: none;
        backdrop-filter: none;
        overflow: hidden;
      }

      .tmq-panel.tmq-collapsed {
        width: var(--tmq-rail-width, 56px);
        padding-inline: 8px;
      }

      .tmq-panel[hidden] {
        display: none !important;
      }

      .tmq-rail-top {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 32px;
      }

      .tmq-rail-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-inline-start: auto;
      }

      .tmq-rail-toggle {
        width: 32px;
        height: 32px;
        border-radius: 10px;
        border: 1px solid var(--border-light);
        background: var(--bg-primary);
        color: var(--text-primary);
        font: 700 16px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0;
      }

      .tmq-rail-toggle-glyph {
        display: inline-block;
        width: 1em;
        line-height: 1;
        text-align: center;
        transform-origin: 50% 50%;
        transition: transform 120ms ease;
      }

      .tmq-panel.tmq-collapsed .tmq-rail-toggle-glyph {
        transform: scaleX(-1);
      }

      .tmq-rail-toggle:hover {
        background: var(--bg-elevated-primary, var(--bg-primary));
      }

      .tmq-rail-actions button {
        border: 1px solid var(--border-light);
        border-radius: 10px;
        padding: 9px 10px;
        font: 600 12px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
        color: var(--text-primary);
        background: var(--bg-primary);
        min-width: 0;
        white-space: nowrap;
        text-align: center;
      }

      .tmq-rail-actions button:hover {
        background: var(--bg-elevated-primary, var(--bg-primary));
      }

      .tmq-rail-actions .tmq-secondary {
        background: var(--bg-primary);
        color: var(--text-primary);
      }

      .tmq-close {
        border: 0;
        border-radius: 10px;
        width: 32px;
        height: 32px;
        background: var(--bg-primary);
        color: var(--text-primary);
        border: 1px solid var(--border-light);
        font: 600 18px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
      }

      .tmq-panel.tmq-collapsed .tmq-rail-actions,
      .tmq-panel.tmq-collapsed .tmq-summary,
      .tmq-panel.tmq-collapsed .tmq-status,
      .tmq-panel.tmq-collapsed .tmq-body {
        display: none;
      }

      .tmq-status {
        padding: 8px 10px;
        border-radius: 12px;
        background: var(--bg-elevated-primary, var(--bg-primary));
        color: var(--text-primary);
        font: 500 12px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        border: 1px solid var(--border-light);
      }

      .tmq-status[data-level="success"] {
        color: #16a34a;
      }

      .tmq-status[data-level="warn"] {
        color: #d97706;
      }

      .tmq-status[data-level="error"] {
        color: #dc2626;
      }

      .tmq-summary {
        display: grid;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--bg-primary);
        border: 1px solid var(--border-light);
        color: var(--text-primary);
        font: 500 12px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-summary-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: wrap;
      }

      .tmq-summary-head strong {
        color: var(--text-primary);
        font: 600 12px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        min-width: 0;
      }

      .tmq-summary-head span {
        color: var(--text-quaternary);
        font: 500 11px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        text-align: right;
        min-width: 0;
        flex: 1 1 160px;
      }

      .tmq-summary-row {
        display: grid;
        gap: 4px;
      }

      .tmq-summary-label {
        color: var(--text-quaternary);
        font: 600 10px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }

      .tmq-summary-value {
        color: var(--text-primary);
        font: 500 12px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .tmq-summary-value[data-empty="1"] {
        color: var(--text-quaternary);
      }

      .tmq-body {
        overflow: auto;
        display: grid;
        gap: 12px;
        padding-right: 2px;
      }

      .tmq-card {
        display: grid;
        gap: 10px;
        padding: 12px;
        border-radius: 14px;
        background: var(--bg-primary);
        border: 1px solid var(--border-light);
      }

      .tmq-card h3 {
        margin: 0;
        font: 600 12px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        color: var(--text-primary);
        letter-spacing: 0.02em;
        text-transform: uppercase;
      }

      .tmq-label {
        font: 600 11px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        color: var(--text-quaternary);
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }

      .tmq-textarea,
      .tmq-input,
      .tmq-select {
        width: 100%;
        border: 1px solid var(--border-light);
        border-radius: 12px;
        background: var(--bg-primary);
        color: var(--text-primary);
        padding: 10px 12px;
        font: 500 13px/1.4 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        outline: none;
      }

      .tmq-textarea {
        resize: vertical;
        min-height: 90px;
      }

      .tmq-input {
        height: 38px;
      }

      .tmq-select {
        height: 38px;
      }

      .tmq-textarea:focus,
      .tmq-input:focus,
      .tmq-select:focus {
        border-color: var(--text-primary);
        box-shadow: 0 0 0 2px color-mix(in srgb, var(--text-primary) 10%, transparent);
      }

      .tmq-row {
        display: flex;
        gap: 8px;
        align-items: center;
        width: 100%;
      }

      .tmq-start-row {
        align-items: stretch;
      }

      .tmq-row .tmq-tight {
        flex: 0 0 64px;
        width: 64px;
        min-width: 64px;
        max-width: 64px;
      }

      .tmq-range-separator {
        flex: 0 0 auto;
        color: var(--text-quaternary);
        font: 600 11px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        text-transform: none;
        letter-spacing: 0;
        white-space: nowrap;
        align-self: center;
        padding: 0 2px;
      }

      .tmq-row .tmq-grow {
        flex: 1 1 auto;
        min-width: 0;
      }

      .tmq-start-row .tmq-grow {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        padding: 0 12px;
        border: 0;
        border-radius: 10px;
        font: 600 12px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
        white-space: nowrap;
        text-align: center;
        background: var(--text-primary);
        color: var(--bg-primary);
      }

      .tmq-start-row .tmq-grow:hover {
        opacity: 0.92;
      }

      .tmq-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .tmq-actions button {
        border: 1px solid var(--border-light);
        border-radius: 10px;
        padding: 9px 10px;
        font: 600 12px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
        color: var(--text-primary);
        background: var(--bg-primary);
        min-width: 0;
        white-space: nowrap;
        text-align: center;
      }

      .tmq-actions button:hover {
        background: var(--bg-elevated-primary, var(--bg-primary));
      }

      .tmq-actions .tmq-primary {
        background: var(--text-primary);
        color: var(--bg-primary);
        border-color: var(--text-primary);
      }

      .tmq-actions .tmq-danger {
        color: #ef4444;
        border-color: color-mix(in srgb, #ef4444 45%, var(--border-light));
      }

      .tmq-actions .tmq-secondary {
        background: var(--bg-primary);
        color: var(--text-primary);
      }

      .tmq-run-actions {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .tmq-run-actions button {
        width: 100%;
      }

      @media (max-width: 520px) {
        .tmq-run-actions {
          grid-template-columns: 1fr;
        }
      }

      .tmq-list {
        display: grid;
        gap: 8px;
        max-height: 210px;
        overflow: auto;
        padding-right: 2px;
      }

      .tmq-message-row {
        display: grid;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--bg-primary);
        border: 1px solid var(--border-light);
      }

      .tmq-message-text {
        color: var(--text-primary);
        font: 500 12px/1.4 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        white-space: pre-wrap;
        word-break: break-word;
      }

      .tmq-message-controls {
        display: flex;
        gap: 6px;
        justify-content: flex-end;
      }

      .tmq-message-controls button {
        border: 0;
        border-radius: 999px;
        padding: 6px 10px;
        font: 600 11px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
        background: var(--bg-elevated-primary, var(--bg-primary));
        color: var(--text-primary);
      }

      .tmq-empty {
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px dashed var(--border-light);
        color: var(--text-quaternary);
        font: 500 12px/1.4 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-note {
        color: var(--text-quaternary);
        font: 500 11px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-index-actions {
        display: flex;
        justify-content: flex-start;
      }

      .tmq-prompt-list {
        max-height: 280px;
        overflow-y: auto;
        overflow-x: hidden;
        gap: 8px;
        min-width: 0;
      }

      .tmq-prompt-row {
        width: 100%;
        box-sizing: border-box;
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid var(--border-light);
        background: var(--bg-primary);
        color: var(--text-primary);
        cursor: pointer;
        text-align: left;
        min-width: 0;
      }

      .tmq-prompt-row:hover {
        background: var(--bg-elevated-primary, var(--bg-primary));
      }

      .tmq-prompt-row[data-active="1"] {
        border-color: var(--text-primary);
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--text-primary) 40%, transparent);
      }

      .tmq-prompt-number {
        flex: 0 0 auto;
        min-width: 2.5ch;
        color: var(--text-quaternary);
        font: 600 11px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-prompt-text {
        flex: 1 1 auto;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font: 500 12px/1.45 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-prompt-highlight {
        outline: 2px solid color-mix(in srgb, var(--text-primary) 28%, transparent);
        outline-offset: 4px;
      }

      .tmq-modal {
        position: fixed;
        inset: 0;
        z-index: 2;
        display: grid;
        place-items: center;
        pointer-events: auto;
      }

      .tmq-modal[hidden] {
        display: none !important;
      }

      .tmq-modal-backdrop {
        position: absolute;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
      }

      .tmq-modal-dialog {
        position: relative;
        width: min(720px, calc(100vw - 48px));
        max-height: min(86vh, 920px);
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 16px;
        border: 1px solid var(--border-light);
        border-radius: 18px;
        background: var(--bg-primary);
        color: var(--text-primary);
        box-shadow: 0 24px 80px rgba(0, 0, 0, 0.42);
      }

      .tmq-modal-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }

      .tmq-modal-title {
        display: grid;
        gap: 3px;
      }

      .tmq-modal-title strong {
        font: 600 14px/1.2 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-modal-title span {
        color: var(--text-quaternary);
        font: 500 12px/1.3 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
      }

      .tmq-modal-close {
        border: 1px solid var(--border-light);
        border-radius: 10px;
        width: 32px;
        height: 32px;
        background: var(--bg-primary);
        color: var(--text-primary);
        font: 600 18px/1 var(--font-sans, -apple-system-body, ui-sans-serif, system-ui, sans-serif);
        cursor: pointer;
      }

      .tmq-modal-body {
        overflow: auto;
        display: grid;
        gap: 12px;
      }
    `;

    const panel = document.createElement('aside');
    panel.className = 'tmq-panel';
    panel.innerHTML = `
      <div class="tmq-rail-top">
        <button type="button" class="tmq-close tmq-rail-toggle" data-action="toggle-rail" title="Collapse panel" aria-label="Collapse queue panel"><span class="tmq-rail-toggle-glyph" aria-hidden="true">&gt;</span></button>
        <span class="tmq-badge" hidden></span>
        <div class="tmq-rail-actions">
          <button type="button" class="tmq-secondary" data-action="open-sequence-modal">New sequence</button>
        </div>
      </div>

      <div class="tmq-summary">
        <div class="tmq-summary-head">
          <strong data-field="job-summary">Idle · 0 pending</strong>
          <span data-field="job-meta">Queue is idle.</span>
        </div>
        <div class="tmq-summary-row">
          <span class="tmq-summary-label">Current prompt</span>
          <div class="tmq-summary-value" data-field="current-prompt">No current prompt.</div>
        </div>
        <div class="tmq-summary-row">
          <span class="tmq-summary-label">Next prompt</span>
          <div class="tmq-summary-value" data-field="next-prompt">No next prompt.</div>
        </div>
      </div>

      <div class="tmq-status" data-level="info" hidden></div>

      <div class="tmq-body">
        <section class="tmq-card">
          <h3>Run Controls</h3>

          <div class="tmq-label">Saved sequence</div>
          <select class="tmq-select" data-field="sequence-select"></select>

          <div class="tmq-label">Send sequence from - to</div>
          <div class="tmq-row tmq-start-row">
            <input class="tmq-input tmq-tight" data-field="start-step" type="number" min="1" step="1">
            <span class="tmq-range-separator">to</span>
            <input class="tmq-input tmq-tight" data-field="end-step" type="number" min="1" step="1">
            <button type="button" class="tmq-send-button tmq-grow" data-action="send-sequence">Send sequence</button>
          </div>

          <div class="tmq-label">Send message next</div>
          <textarea class="tmq-textarea" data-field="next-message" placeholder="Type one message to send next, or add it to the current queue."></textarea>

          <div class="tmq-actions tmq-run-actions">
            <button type="button" class="tmq-primary" data-action="enqueue-message">Queue message</button>
            <button type="button" class="tmq-secondary" data-action="resume-automation">Resume</button>
            <button type="button" class="tmq-danger" data-action="stop-automation">Stop</button>
          </div>

          <div class="tmq-note">The queue waits for the ChatGPT page to become truly idle before sending the next message.</div>
        </section>

        <section class="tmq-card">
          <h3>Prompt Index</h3>
          <div class="tmq-note">Known user prompts are listed in chat order. Click one to jump to it.</div>

          <div class="tmq-actions tmq-index-actions">
            <button type="button" class="tmq-secondary" data-action="backfill-prompts">Load older prompts</button>
          </div>

          <div class="tmq-list tmq-prompt-list" data-field="prompt-index-list"></div>
        </section>
      </div>
    `;

    const modal = document.createElement('div');
    modal.className = 'tmq-modal';
    modal.hidden = true;
    modal.innerHTML = `
      <div class="tmq-modal-backdrop" data-action="close-sequence-modal"></div>
      <div class="tmq-modal-dialog" role="dialog" aria-modal="true" aria-label="Sequence builder">
        <div class="tmq-modal-header">
          <div class="tmq-modal-title">
            <strong>Sequence Builder</strong>
            <span>Create or edit a saved queue sequence.</span>
          </div>
          <button type="button" class="tmq-modal-close" data-action="close-sequence-modal" aria-label="Close sequence builder">×</button>
        </div>

        <div class="tmq-modal-body">
          <section class="tmq-card">
            <h3>Sequence Builder</h3>

            <div class="tmq-label">Draft message</div>
            <textarea class="tmq-textarea" data-field="draft-input" placeholder="Type one message, then add it to the sequence."></textarea>

            <div class="tmq-actions">
              <button type="button" class="tmq-primary" data-action="add-message">Add message</button>
              <button type="button" class="tmq-secondary" data-action="clear-draft">Clear draft</button>
            </div>

            <div class="tmq-label">Draft messages</div>
            <div class="tmq-list" data-field="draft-list"></div>

            <div class="tmq-label">Sequence name</div>
            <input class="tmq-input" data-field="sequence-name" type="text" placeholder="Sequence name">

            <div class="tmq-actions">
              <button type="button" class="tmq-primary" data-action="save-sequence">Save sequence</button>
              <button type="button" class="tmq-secondary" data-action="delete-sequence">Delete sequence</button>
            </div>
          </section>
        </div>
      </div>
    `;

    shadow.appendChild(style);
    shadow.appendChild(panel);
    shadow.appendChild(modal);

    els = {
      root: host,
      panel,
      modal,
      badge: panel.querySelector('.tmq-badge'),
      closeButton: panel.querySelector('[data-action="toggle-rail"]'),
      status: panel.querySelector('.tmq-status'),
      jobSummary: panel.querySelector('[data-field="job-summary"]'),
      jobMeta: panel.querySelector('[data-field="job-meta"]'),
      currentPrompt: panel.querySelector('[data-field="current-prompt"]'),
      nextPrompt: panel.querySelector('[data-field="next-prompt"]'),
      promptIndexList: panel.querySelector('[data-field="prompt-index-list"]'),
      draftInput: modal.querySelector('[data-field="draft-input"]'),
      draftList: modal.querySelector('[data-field="draft-list"]'),
      sequenceName: modal.querySelector('[data-field="sequence-name"]'),
      sequenceSelect: panel.querySelector('[data-field="sequence-select"]'),
      startStep: panel.querySelector('[data-field="start-step"]'),
      endStep: panel.querySelector('[data-field="end-step"]'),
      nextMessage: panel.querySelector('[data-field="next-message"]')
    };

    shadow.querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', () => {
        switch (button.getAttribute('data-action')) {
          case 'toggle-rail':
            openPanel(!state.panelOpen);
            break;
          case 'open-sequence-modal':
            setSequenceModalOpen(true);
            if (els.draftInput) {
              els.draftInput.focus();
            }
            break;
          case 'close-sequence-modal':
            setSequenceModalOpen(false);
            break;
          case 'add-message':
            addDraftMessage();
            break;
          case 'clear-draft':
            state.draftMessages = [];
            saveDraftState();
            renderDraftList();
            setStatus('Cleared the draft list.', 'warn');
            break;
          case 'save-sequence':
            saveSequence();
            break;
          case 'delete-sequence':
            deleteSequence();
            break;
          case 'send-sequence':
            startSequenceFromDraft();
            break;
          case 'enqueue-message':
            enqueueNextMessage();
            break;
          case 'resume-automation':
            resumeAutomation();
            break;
          case 'stop-automation':
            stopAutomation();
            break;
          case 'backfill-prompts':
            void backfillPromptIndex();
            break;
          default:
            break;
        }
      });
    });

    els.sequenceSelect.addEventListener('change', () => {
      const value = String(els.sequenceSelect.value || '');
      if (value) {
        loadSequence(value);
      }
    });
    els.sequenceName.addEventListener('input', syncDraftFromInputs);
    els.sequenceName.addEventListener('change', syncDraftFromInputs);
    els.draftInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        addDraftMessage();
      }
    });
    els.nextMessage.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        enqueueNextMessage();
      }
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        setSequenceModalOpen(false);
      }
    });

    window.addEventListener('storage', (event) => {
      if (event.key === SEQUENCES_KEY) {
        state.sequences = loadSequences();
        renderSequenceSelect();
      }
    });

    window.addEventListener('resize', syncDockLayout, { passive: true });
    installPromptIndexObservers();
    installLocationWatcher();
    schedulePromptIndexRefresh();

    state.uiReady = true;
  }

  function bootstrap() {
    if (document.getElementById(ROOT_ID)) {
      return;
    }

    state.sequences = loadSequences();
    const draft = loadDraftState();
    state.draftMessages = draft.messages;
    state.draftName = draft.name;
    state.selectedSequence = draft.selectedSequence;
    state.lastLocationHref = location.href;
    loadPromptIndex();
    state.panelOpen = true;

    buildUi();
    renderPanelFromState();
    openPanel(state.panelOpen);

    const job = loadJob();
    if (job) {
      state.launcherBadge = getJobPendingCount(job);
      updateLauncherBadge();
      updateJobSummary(job);
      if (job.paused) {
        setStatus('Loaded a paused queue. Press Resume to continue or Stop again to clear it.', 'warn');
      } else {
        setStatus('Loaded a saved queue. Resuming when the page is ready.', 'info');
        scheduleProcess();
      }
      } else {
        setStatus('', 'info');
      }
  }

  function waitForBody() {
    if (document.body) {
      bootstrap();
      return;
    }

    const observer = new MutationObserver(() => {
      if (document.body) {
        observer.disconnect();
        bootstrap();
      }
    });

    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', waitForBody, { once: true });
  } else {
    waitForBody();
  }
})();
