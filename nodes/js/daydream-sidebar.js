import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const SERVER_BASE_SETTING_ID = "daydream.server_base";
const LEGACY_SERVER_BASE_SETTING_ID = "rtc.daydream.server_base";
const API_BASE_SETTING_ID = "daydream.api_base";
const WINDOW_SERVER_BASE =
  typeof window !== "undefined" && window.DAYDREAM_SERVER_BASE
    ? window.DAYDREAM_SERVER_BASE
    : null;
const DEFAULT_SERVER_BASE = "http://127.0.0.1:8895";
const DEFAULT_API_BASE = "https://api.daydream.live";
const STATUS_POLL_INTERVAL = 1000;
const RTC_CONTROL_ENDPOINT = "/rtc/control";
const CONFIG_ENDPOINT_CANDIDATES = ["/config", "/config/", "/runtime-config"];

let serverBase = DEFAULT_SERVER_BASE;
let pollTimer = null;
let uiRefs = null;
let currentStatus = {
  running: false,
  remote_status: {},
  stream_id: "",
  playback_id: "",
  whip_url: "",
  queue_depth: 0,
  queue_stats: { depth: 0, buffered: 0 },
};
let currentConfig = {
  frame_rate: 30,
  frame_width: 1280,
  frame_height: 720,
};
let streamUnavailable = false;
let pendingStreamId = "";
let resolvedConfigEndpoint = null;
let localServerState = { running: false, host: null, port: null };
let lastAlignedLocalBase = null;

function normalizeServerBase(value) {
  if (!value || typeof value !== "string") {
    return DEFAULT_SERVER_BASE;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return DEFAULT_SERVER_BASE;
  }
  return trimmed.replace(/\/+$/, "");
}

function setServerBase(newValue, { suppressRefresh = false } = {}) {
  const next = normalizeServerBase(newValue);
  if (serverBase === next) {
    return;
  }
  serverBase = next;
  if (!suppressRefresh && uiRefs) {
    refreshStatus(false);
  }
}

function initServerBaseSetting() {
  if (WINDOW_SERVER_BASE) {
    setServerBase(WINDOW_SERVER_BASE, { suppressRefresh: true });
    return;
  }

  app.registerExtension({
    name: "DayDream Live",
    settings: [
      {
        id: SERVER_BASE_SETTING_ID,
        name: "Local API Server Base URL",
        type: "text",
        defaultValue: DEFAULT_SERVER_BASE,
        tooltip: "Base REST endpoint for the local DayDream API server",
        attrs: {
          placeholder: DEFAULT_SERVER_BASE,
        },
        onChange: (value, _oldValue) => {
          setServerBase(value);
        },
      },
      {
        id: API_BASE_SETTING_ID,
        name: "DayDream API Base URL",
        type: "text",
        defaultValue: DEFAULT_API_BASE,
        tooltip: "Base REST endpoint for the DayDream Live API",
        attrs: {
          placeholder: DEFAULT_API_BASE,
        },
      },
    ],
    setup() {
      const stored = app.extensionManager.setting.get(SERVER_BASE_SETTING_ID);
      const legacyStored = app.extensionManager.setting.get(
        LEGACY_SERVER_BASE_SETTING_ID
      );
      const initialValue = stored ?? legacyStored;
      if (initialValue) {
        setServerBase(initialValue, { suppressRefresh: true });
        if (legacyStored && !stored) {
          app.extensionManager.setting
            .set(SERVER_BASE_SETTING_ID, legacyStored)
            .catch((err) =>
              console.error("Failed to migrate local API server setting", err)
            );
        }
      } else {
        app.extensionManager.setting
          .set(SERVER_BASE_SETTING_ID, DEFAULT_SERVER_BASE)
          .catch((err) =>
            console.error("Failed to persist local API server setting", err)
          );
      }
      const storedApiBase = app.extensionManager.setting.get(API_BASE_SETTING_ID);
      if (!storedApiBase) {
        app.extensionManager.setting
          .set(API_BASE_SETTING_ID, DEFAULT_API_BASE)
          .catch((err) => console.error("Failed to persist DayDream API Base setting", err));
      }
    },
  });
}

initServerBaseSetting();

function clampNumber(value, min, max, fallback) {
  const num = Number(value);
  if (Number.isFinite(num)) {
    return Math.min(max, Math.max(min, num));
  }
  return fallback;
}

function readConfigForm() {
  if (!uiRefs) {
    return currentConfig;
  }
  return {
    frame_rate: clampNumber(uiRefs.configFps?.value, 1, 240, currentConfig.frame_rate),
    frame_width: clampNumber(uiRefs.configWidth?.value, 64, 4096, currentConfig.frame_width),
    frame_height: clampNumber(uiRefs.configHeight?.value, 64, 4096, currentConfig.frame_height),
  };
}

function updateConfigControls(statusPayload = null, options = {}) {
  if (!uiRefs) return;
  const incoming =
    options.stream_settings || statusPayload?.stream_settings || null;
  if (incoming) {
    currentConfig = {
      frame_rate: incoming.frame_rate ?? currentConfig.frame_rate,
      frame_width: incoming.frame_width ?? currentConfig.frame_width,
      frame_height: incoming.frame_height ?? currentConfig.frame_height,
    };
  }
  const locked =
    typeof options.locked === "boolean"
      ? options.locked
      : Boolean(statusPayload?.running);
  if (uiRefs.configFps) {
    uiRefs.configFps.value = currentConfig.frame_rate;
    uiRefs.configFps.disabled = locked;
  }
  if (uiRefs.configWidth) {
    uiRefs.configWidth.value = currentConfig.frame_width;
    uiRefs.configWidth.disabled = locked;
  }
  if (uiRefs.configHeight) {
    uiRefs.configHeight.value = currentConfig.frame_height;
    uiRefs.configHeight.disabled = locked;
  }
  if (uiRefs.configSaveBtn) {
    uiRefs.configSaveBtn.disabled = locked;
  }
  if (uiRefs.configLockNote) {
    uiRefs.configLockNote.textContent = locked
      ? "Stop the stream to edit FPS or resolution."
      : "Changes apply to the next stream start.";
  }
}

function isNotFoundError(error) {
  const match = error?.message?.match(/^(\d+):/);
  return match !== null && Number(match[1]) === 404;
}

function composeConfigCandidates() {
  const candidates = [];
  if (resolvedConfigEndpoint) {
    candidates.push(resolvedConfigEndpoint);
  }
  for (const candidate of CONFIG_ENDPOINT_CANDIDATES) {
    if (resolvedConfigEndpoint && candidate === resolvedConfigEndpoint) {
      continue;
    }
    candidates.push(candidate);
  }
  return candidates;
}

async function requestConfigEndpoint(options = {}) {
  const candidates = composeConfigCandidates();
  let lastError = null;
  for (const candidate of candidates) {
    try {
      const payload = await fetchJSON(candidate, options);
      resolvedConfigEndpoint = candidate;
      return payload;
    } catch (error) {
      lastError = error;
      if (!isNotFoundError(error)) {
        throw error;
      }
    }
  }
  if (lastError) {
    throw lastError;
  }
  throw new Error("No config endpoint found");
}

async function fetchRuntimeConfig() {
  try {
    const payload = await requestConfigEndpoint();
    currentConfig = {
      frame_rate: payload.frame_rate ?? currentConfig.frame_rate,
      frame_width: payload.frame_width ?? currentConfig.frame_width,
      frame_height: payload.frame_height ?? currentConfig.frame_height,
    };
    updateConfigControls(null, {
      locked: Boolean(payload.locked),
      stream_settings: currentConfig,
    });
  } catch (error) {
    console.warn("Unable to load RTC runtime config", error);
  }
}

async function handleConfigSave() {
  if (currentStatus?.running) {
    toast("warn", "DayDream Live", "Stop the stream before editing FPS/size.");
    return;
  }
  const payload = readConfigForm();
  try {
    const response = await requestConfigEndpoint({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    currentConfig = {
      frame_rate: response.frame_rate,
      frame_width: response.frame_width,
      frame_height: response.frame_height,
    };
    updateConfigControls(null, {
      locked: Boolean(response.locked),
      stream_settings: currentConfig,
    });
    toast("success", "DayDream Live", "Stream settings saved");
  } catch (error) {
    toast("error", "DayDream Live", `Unable to save settings: ${error.message}`);
  }
}

const BADGE_CONFIG = {
  STOPPED: { label: "STOPPED", className: "idle", pulse: false },
  CREATING: { label: "CREATING", className: "info", pulse: true },
  STARTING: { label: "STARTING", className: "starting", pulse: true },
  LOADING: { label: "LOADING", className: "loading", pulse: true },
  ONLINE: { label: "ONLINE", className: "running", pulse: true },
  OFFLINE: { label: "OFFLINE", className: "warning", pulse: true },
  ERROR: { label: "ERROR", className: "error", pulse: false },
  NO_SERVER: { label: "NO SERVER", className: "error", pulse: false },
  NOT_FOUND: { label: "NOT FOUND", className: "error", pulse: false },
};

function ensureStylesInjected() {
  if (document.getElementById("daydream-live-styles")) {
    return;
  }
  const style = document.createElement("style");
  style.id = "daydream-live-styles";
  style.textContent = `
    .ddl-container {
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding: 12px;
      height: 100%;
    }
    .ddl-tab-buttons {
      display: flex;
      gap: 8px;
    }
    .ddl-tab-button {
      flex: 1;
      padding: 6px 8px;
      border: none;
      border-radius: 6px;
      background: #2f2f2f;
      color: #f5f5f5;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease;
    }
    .ddl-tab-button.active {
      background: #5551d4;
    }
    .ddl-tab-panel {
      display: none;
      flex-direction: column;
      gap: 12px;
      flex: 1;
    }
    .ddl-tab-panel.active {
      display: flex;
    }
    .ddl-actions {
      display: flex;
      gap: 8px;
    }
    .ddl-status-badge {
      padding: 4px 12px;
      border-radius: 18px;
      font-size: 0.8rem;
      font-weight: 700;
      text-transform: uppercase;
      width: fit-content;
      letter-spacing: 0.05em;
      transition: background 0.2s ease, color 0.2s ease;
      box-shadow: 0 0 10px rgba(0, 0, 0, 0.25);
    }
    .ddl-badge-idle {
      background: #4a4a4a;
      color: #fff;
    }
    .ddl-badge-info {
      background: #3a7bff;
      color: #fff;
    }
    .ddl-badge-starting {
      background: #2d7dd2;
      color: #fff;
    }
    .ddl-badge-loading {
      background: #e88c20;
      color: #1b1300;
    }
    .ddl-badge-running {
      background: #1c7c54;
      color: #fff;
    }
    .ddl-badge-warning {
      background: #d97706;
      color: #1b1300;
    }
    .ddl-badge-error {
      background: #b3261e;
      color: #fff;
    }
    .ddl-badge-pulse {
      animation: ddlPulse 1.4s ease-in-out infinite;
    }
    @keyframes ddlPulse {
      0% {
        box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.4);
      }
      70% {
        box-shadow: 0 0 0 12px rgba(255, 255, 255, 0);
      }
      100% {
        box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);
      }
    }
    .ddl-info-grid {
      display: grid;
      grid-template-columns: 110px 1fr;
      gap: 4px 8px;
      font-size: 0.85rem;
      word-break: break-all;
    }
    .ddl-label {
      color: #aaa;
      font-weight: 500;
    }
    .ddl-value {
      color: #eee;
    }
    .ddl-note {
      font-size: 0.78rem;
      color: #bbb;
    }
    .ddl-config-card {
      border: 1px solid #2a2a2a;
      border-radius: 6px;
      padding: 12px;
      background: #1b1b1b;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .ddl-config-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .ddl-config-field {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .ddl-config-input {
      background: #111;
      border: 1px solid #333;
      border-radius: 4px;
      color: #fff;
      padding: 6px;
      font-size: 0.85rem;
    }
    .ddl-config-input:disabled {
      opacity: 0.6;
    }
    .ddl-config-note {
      font-size: 0.75rem;
      color: #bbb;
    }
    .ddl-log-panel {
      display: flex;
      flex-direction: column;
      gap: 12px;
      flex: 1;
      overflow: hidden;
    }
    .ddl-json-viewer {
      background: #141414;
      border-radius: 6px;
      padding: 8px;
      border: 1px solid #2a2a2a;
      color: #b7ffd8;
      font-family: "JetBrains Mono", "Fira Code", monospace;
      font-size: 0.75rem;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .ddl-button {
      flex: 1;
      padding: 10px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-weight: 600;
      text-transform: uppercase;
    }
    .ddl-button-primary {
      background: #1c7c54;
      color: #fff;
    }
    .ddl-button-danger {
      background: #b3261e;
      color: #fff;
    }
    .ddl-button-secondary {
      background: #5551d4;
      color: #fff;
    }
    .ddl-button:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
  `;
  document.head.appendChild(style);
}

function toast(severity, summary, detail) {
  app.extensionManager.toast.add({
    severity,
    summary,
    detail,
    life: 3500,
  });
}

async function callRtcControl(action, payload = {}) {
  const body = JSON.stringify({ action, ...payload });
  const response = await fetch(RTC_CONTROL_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text };
  }
  if (!response.ok || data.success === false) {
    const message = data.error || response.statusText || "RTC control request failed";
    throw new Error(message);
  }
  return data;
}

async function refreshLocalServerState({ quiet = false } = {}) {
  try {
    const data = await callRtcControl("status");
    localServerState = data.status || { running: false, host: null, port: null };
    updateLocalApiDisplay();
    return localServerState;
  } catch (error) {
    localServerState = { running: false, host: null, port: null };
    updateLocalApiDisplay();
    if (quiet) {
      return localServerState;
    }
    throw error;
  }
}

function updateLocalApiDisplay() {
  if (!uiRefs?.localApiValue) {
    return;
  }
  const running = Boolean(localServerState?.running);
  if (running) {
    const host = localServerState.host || "127.0.0.1";
    const port = localServerState.port ?? "—";
    uiRefs.localApiValue.textContent = `${host}:${port}`;
    alignLocalServerBase(host, port);
  } else {
    uiRefs.localApiValue.textContent = "Offline";
    lastAlignedLocalBase = null;
  }
}

function alignLocalServerBase(host, port) {
  if (!host || !port) {
    return;
  }
  const localBase = `http://${host}:${port}`;
  if (lastAlignedLocalBase === localBase) {
    return;
  }
  const looksLocal = /^https?:\/\/127\.0\.0\.1/i.test(serverBase || "") || serverBase === DEFAULT_SERVER_BASE;
  if (looksLocal && serverBase !== localBase) {
    serverBase = localBase;
    lastAlignedLocalBase = localBase;
  }
}

async function ensureLocalServerReady() {
  if (localServerState?.running) {
    return true;
  }
  await callRtcControl("start");
  await refreshLocalServerState({ quiet: false });
  if (!localServerState?.running) {
    throw new Error("Local API server failed to start");
  }
  return true;
}

function apiUrl(path) {
  return `${serverBase}${path}`;
}

const LOCAL_REQUEST_TIMEOUT_MS = 1500;

async function fetchJSON(path, options = {}, timeout = LOCAL_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(apiUrl(path), {
      signal: controller.signal,
      ...options,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Request timed out");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
  const text = await response.text();
  let json = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { error: text };
  }
  if (!response.ok) {
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }
  if (response.status === 204) {
    return {};
  }
  return json;
}

function applyBadgeState(stateKey, overrideLabel) {
  if (!uiRefs) return;
  const cfg = BADGE_CONFIG[stateKey] || BADGE_CONFIG.STOPPED;
  const classNames = ["ddl-status-badge", `ddl-badge-${cfg.className}`];
  if (cfg.pulse) classNames.push("ddl-badge-pulse");
  uiRefs.statusBadge.className = classNames.join(" ");
  const label = (overrideLabel || cfg.label || stateKey || "UNKNOWN").toUpperCase();
  uiRefs.statusBadge.textContent = label;
}

function extractRemoteState(statusPayload) {
  const remoteStatus = statusPayload?.remote_status || {};
  const phase = (remoteStatus.phase || "").toUpperCase();
  const phaseDetail = remoteStatus.detail || remoteStatus.remoteText || "";
  if (phase) {
    if (phase === "STREAM_CREATED") {
      return { badge: "CREATING", remoteText: phaseDetail || "Stream created" };
    }
    if (phase === "WHIP_OFFER") {
      return { badge: "STARTING", remoteText: phaseDetail || "Sending WHIP offer" };
    }
    if (phase === "WHIP_ANSWER" || phase === "WHIP_ESTABLISHED") {
      return { badge: "ONLINE", remoteText: phaseDetail || "WHIP established" };
    }
    if (phase.startsWith("ICE_") || phase.startsWith("PEER_")) {
      return { badge: "LOADING", remoteText: phaseDetail || phase };
    }
    if (phase === "STOPPED") {
      return { badge: "STOPPED", remoteText: phaseDetail || "Stopped" };
    }
  }
  const httpStatus =
    remoteStatus.http_status ??
    remoteStatus.status_code ??
    remoteStatus.code;
  const body = remoteStatus.body || remoteStatus;

  if (httpStatus === 404) {
    return { badge: "NOT_FOUND", remoteText: "Not found" };
  }

  if (!statusPayload?.running) {
    if (
      statusPayload?.stream_id &&
      statusPayload.stream_id === pendingStreamId
    ) {
      return {
        badge: "STARTING",
        remoteText:
          phaseDetail || "Waiting for DayDream Live to accept the WHIP session",
      };
    }
    return { badge: "STOPPED", remoteText: "Stopped" };
  }

  const explicitState =
    body?.data?.state ||
    body?.state ||
    body?.status ||
    body?.inference_status?.state ||
    "";

  const normalized = typeof explicitState === "string" ? explicitState.toUpperCase() : "";

  if (normalized === "ONLINE" || normalized === "READY") {
    return { badge: "ONLINE", remoteText: normalized };
  }

  if (normalized === "LOADING" || normalized === "INITIALIZING" || normalized === "STARTING") {
    return { badge: "LOADING", remoteText: normalized };
  }

  if (normalized === "OFFLINE") {
    return { badge: "OFFLINE", remoteText: "OFFLINE" };
  }

  if (body?.success === true && body?.data?.state) {
    const state = body.data.state.toUpperCase();
    if (state === "OFFLINE") {
      return { badge: "OFFLINE", remoteText: "OFFLINE" };
    }
    if (state === "ONLINE") {
      return { badge: "ONLINE", remoteText: "ONLINE" };
    }
  }

  if (body?.error) {
    if (/not\s+found/i.test(body.error)) {
      return { badge: "NOT_FOUND", remoteText: "Not found" };
    }
    return { badge: "ERROR", remoteText: body.error };
  }

  if (httpStatus && httpStatus >= 400) {
    return { badge: "ERROR", remoteText: `HTTP ${httpStatus}` };
  }

  if (statusPayload?.stream_id) {
    return { badge: "STARTING", remoteText: normalized || "STARTING" };
  }

  return { badge: "STOPPED", remoteText: "Stopped" };
}

function updateInfoFields(statusPayload, options = {}) {
  if (!uiRefs) return;
  const basePayload = statusPayload || {};
  const shouldClear = options.resetStream || !basePayload.running;
  const displayPayload = { ...basePayload };
  if (shouldClear) {
    displayPayload.stream_id = "";
    displayPayload.playback_id = "";
    displayPayload.whip_url = "";
  }
  const { stream_id, playback_id, whip_url } = displayPayload;
  const queueDepth = basePayload?.queue_depth ?? 0;
  const buffered = basePayload?.queue_stats?.buffered ?? 0;
  uiRefs.streamId.textContent = stream_id || "—";
  if (playback_id) {
    uiRefs.playbackId.innerHTML = `<a href="https://lvpr.tv?v=${playback_id}" target="_blank" rel="noopener noreferrer">${playback_id}</a>`;
  } else {
    uiRefs.playbackId.textContent = "—";
  }
  uiRefs.whipUrl.textContent = whip_url || "—";
  uiRefs.queueDepth.textContent = buffered
    ? `${queueDepth} (buffering ${buffered})`
    : `${queueDepth}`;

  updateConfigControls(displayPayload);

  const descriptor =
    options.descriptorOverride || extractRemoteState(basePayload || {});
  uiRefs.remoteInfo.textContent = descriptor.remoteText || "No telemetry";
  applyBadgeState(descriptor.badge, descriptor.remoteText);
  if (uiRefs.statusJson) {
    const jsonSource =
      statusPayload?.remote_status?.body ??
      statusPayload?.remote_status ??
      statusPayload ??
      {};
    uiRefs.statusJson.textContent = JSON.stringify(jsonSource, null, 2);
  }
  updateActionButtons(displayPayload, descriptor);
}

function updateActionButtons(statusPayload, descriptor) {
  if (!uiRefs) return;
  const primary = uiRefs.primaryButton;
  const secondary = uiRefs.secondaryButton;
  const runningStates = new Set(["ONLINE", "LOADING", "STARTING"]);
  const isRunning = runningStates.has(descriptor.badge);
  const hasStreamId = Boolean(statusPayload?.stream_id);
  const pendingStream =
    hasStreamId &&
    !isRunning &&
    statusPayload?.stream_id === pendingStreamId;
  const isNotFound = descriptor.badge === "NOT_FOUND";

  if (isRunning) {
    pendingStreamId = "";
    primary.textContent = "Stop Stream";
    primary.className = "ddl-button ddl-button-danger";
    primary.disabled = false;
    primary.onclick = () => handleStop();
    secondary.style.display = "none";
    return;
  }

  if (isNotFound) {
    streamUnavailable = true;
    pendingStreamId = "";
    primary.textContent = "Start Stream";
    primary.className = "ddl-button ddl-button-primary";
    primary.disabled = true;
    primary.onclick = null;
    secondary.style.display = "inline-flex";
    secondary.className = "ddl-button ddl-button-secondary";
    secondary.textContent = "Create new stream";
    secondary.disabled = false;
    secondary.onclick = () => handleAbandon();
    return;
  }

  streamUnavailable = false;
  primary.textContent = "Start Stream";
  primary.className = "ddl-button ddl-button-primary";
  primary.disabled = false;
  primary.onclick = () => handleStart();

  if (pendingStream) {
    secondary.style.display = "inline-flex";
    secondary.className = "ddl-button ddl-button-secondary";
    secondary.textContent = "Abandon stream";
    secondary.disabled = false;
    secondary.onclick = () => handleAbandon();
  } else {
    secondary.style.display = "none";
  }
}

function createEmptyStatus() {
  return {
    running: false,
    remote_status: {},
    stream_id: "",
    playback_id: "",
    whip_url: "",
    queue_depth: 0,
    queue_stats: { depth: 0, buffered: 0 },
  };
}

async function refreshStatus(showToast = false) {
  try {
    const status = await fetchJSON("/status");
    currentStatus = status;
    updateInfoFields(status);
    if (showToast) {
      toast(
        "info",
        "DayDream Live",
        `Stream is ${status.running ? "running" : "stopped"}`
      );
    }
  } catch (error) {
    try {
      await refreshLocalServerState({ quiet: true });
    } catch {
      // swallow
    }
    applyBadgeState("NO_SERVER");
    //toast("error", "DayDream Live", `Unable to fetch status: ${error.message}`);
  }
}

async function handleStart() {
  if (streamUnavailable) {
    toast(
      "warn",
      "DayDream Live",
      "Stream previously removed; clear it before creating a new one."
    );
    return;
  }
  try {
    await ensureLocalServerReady();
  } catch (error) {
    applyBadgeState("NO_SERVER");
    toast("error", "DayDream Live", `Local API server unavailable: ${error.message}`);
    return;
  }
  applyBadgeState("CREATING");
  if (uiRefs) {
    uiRefs.remoteInfo.textContent = "Creating stream…";
  }
  try {
    const payload = await fetchJSON("/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    toast("success", "DayDream Live", "Stream started");
    currentStatus = payload;
    streamUnavailable = false;
    pendingStreamId = payload.stream_id || "";
    applyBadgeState("STARTING");
    if (uiRefs) {
      uiRefs.remoteInfo.textContent = "Starting…";
    }
    updateInfoFields(payload);
    await refreshStatus(false);
  } catch (error) {
    applyBadgeState("ERROR");
    toast("error", "DayDream Live", `Start failed: ${error.message}`);
  }
}

async function handleStop() {
  try {
    const payload = await fetchJSON("/stop", { method: "POST" });
    toast("warn", "DayDream Live", "Stream stopped");
    const clearedStatus = createEmptyStatus();
    currentStatus = clearedStatus;
    pendingStreamId = "";
    updateInfoFields(clearedStatus, {
      descriptorOverride: { badge: "STOPPED", remoteText: "Stream stopped" },
      resetStream: true,
    });
  } catch (error) {
    toast("error", "DayDream Live", `Stop failed: ${error.message}`);
  } finally {
    applyBadgeState("STOPPED");
    streamUnavailable = false;
    pendingStreamId = "";
  }
}

async function handleAbandon() {
  if (streamUnavailable) {
    streamUnavailable = false;
    clearStreamState("Stream cleared");
    return;
  }

  try {
    const payload = await fetchJSON("/stop", { method: "POST" });
    toast("warn", "DayDream Live", "Stream abandoned");
    const clearedStatus = createEmptyStatus();
    currentStatus = clearedStatus;
    updateInfoFields(clearedStatus, {
      descriptorOverride: { badge: "STOPPED", remoteText: "Stream abandoned" },
      resetStream: true,
    });
  } catch (error) {
    toast("error", "DayDream Live", `Unable to abandon stream: ${error.message}`);
  } finally {
    streamUnavailable = false;
    pendingStreamId = "";
  }
}

function clearStreamState(message = "Stream reset") {
  pendingStreamId = "";
  const resetStatus = createEmptyStatus();
  currentStatus = resetStatus;
  toast("info", "DayDream Live", message);
  updateInfoFields(resetStatus, {
    descriptorOverride: { badge: "STOPPED", remoteText: message },
    resetStream: true,
  });
}

function buildPanel(el) {
  ensureStylesInjected();
  el.innerHTML = "";
  el.className = "ddl-container";

  const tabs = document.createElement("div");
  tabs.className = "ddl-tab-buttons";
  const controlsTabBtn = document.createElement("button");
  controlsTabBtn.className = "ddl-tab-button active";
  controlsTabBtn.textContent = "Controls";
  controlsTabBtn.onclick = () => setActivePanel("controls");
  const logsTabBtn = document.createElement("button");
  logsTabBtn.className = "ddl-tab-button";
  logsTabBtn.textContent = "Status Logs";
  logsTabBtn.onclick = () => setActivePanel("logs");
  tabs.appendChild(controlsTabBtn);
  tabs.appendChild(logsTabBtn);
  el.appendChild(tabs);

  const controlsPanel = document.createElement("div");
  controlsPanel.className = "ddl-tab-panel active";

  const badge = document.createElement("span");
  badge.className = "ddl-status-badge ddl-badge-idle";
  badge.textContent = "STOPPED";
  controlsPanel.appendChild(badge);

  const header = document.createElement("div");
  header.innerHTML = "<h3>DayDream Live</h3><div class='ddl-note'>Control the RTC stream ingest</div>";
  controlsPanel.appendChild(header);

  const infoGrid = document.createElement("div");
  infoGrid.className = "ddl-info-grid";

  const localApiLabel = document.createElement("div");
  localApiLabel.className = "ddl-label";
  localApiLabel.textContent = "Local API";
  const localApiValue = document.createElement("div");
  localApiValue.className = "ddl-value";
  localApiValue.textContent = "Detecting…";

  const streamIdLabel = document.createElement("div");
  streamIdLabel.className = "ddl-label";
  streamIdLabel.textContent = "Stream ID";
  const streamIdValue = document.createElement("div");
  streamIdValue.className = "ddl-value";

  const playbackLabel = document.createElement("div");
  playbackLabel.className = "ddl-label";
  playbackLabel.textContent = "Playback ID";
  const playbackValue = document.createElement("div");
  playbackValue.className = "ddl-value";

  const whipLabel = document.createElement("div");
  whipLabel.className = "ddl-label";
  whipLabel.textContent = "WHIP URL";
  const whipValue = document.createElement("div");
  whipValue.className = "ddl-value";

  const remoteLabel = document.createElement("div");
  remoteLabel.className = "ddl-label";
  remoteLabel.textContent = "Remote";
  const remoteValue = document.createElement("div");
  remoteValue.className = "ddl-value";

  const queueLabel = document.createElement("div");
  queueLabel.className = "ddl-label";
  queueLabel.textContent = "Queue Depth";
  const queueValue = document.createElement("div");
  queueValue.className = "ddl-value";

  [
    localApiLabel,
    localApiValue,
    streamIdLabel,
    streamIdValue,
    playbackLabel,
    playbackValue,
    whipLabel,
    whipValue,
    queueLabel,
    queueValue,
    remoteLabel,
    remoteValue,
  ].forEach((node) => infoGrid.appendChild(node));

  controlsPanel.appendChild(infoGrid);

  const configCard = document.createElement("div");
  configCard.className = "ddl-config-card";

  const configTitle = document.createElement("div");
  configTitle.className = "ddl-note";
  configTitle.textContent = "Stream settings (apply before starting)";
  configCard.appendChild(configTitle);

  const configGrid = document.createElement("div");
  configGrid.className = "ddl-config-grid";

  const fpsInput = document.createElement("input");
  fpsInput.type = "number";
  fpsInput.min = "1";
  fpsInput.max = "240";
  fpsInput.step = "1";
  fpsInput.className = "ddl-config-input";
  fpsInput.value = currentConfig.frame_rate;

  const widthInput = document.createElement("input");
  widthInput.type = "number";
  widthInput.min = "64";
  widthInput.max = "4096";
  widthInput.step = "1";
  widthInput.className = "ddl-config-input";
  widthInput.value = currentConfig.frame_width;

  const heightInput = document.createElement("input");
  heightInput.type = "number";
  heightInput.min = "64";
  heightInput.max = "4096";
  heightInput.step = "1";
  heightInput.className = "ddl-config-input";
  heightInput.value = currentConfig.frame_height;

  const makeConfigField = (labelText, inputEl) => {
    const wrapper = document.createElement("div");
    wrapper.className = "ddl-config-field";
    const label = document.createElement("div");
    label.className = "ddl-label";
    label.textContent = labelText;
    wrapper.appendChild(label);
    wrapper.appendChild(inputEl);
    return wrapper;
  };

  configGrid.appendChild(makeConfigField("FPS", fpsInput));
  configGrid.appendChild(makeConfigField("Width", widthInput));
  configGrid.appendChild(makeConfigField("Height", heightInput));
  configCard.appendChild(configGrid);

  const configActions = document.createElement("div");
  configActions.className = "ddl-actions";
  const configSaveBtn = document.createElement("button");
  configSaveBtn.className = "ddl-button ddl-button-secondary";
  configSaveBtn.textContent = "Save Stream Settings";
  configSaveBtn.onclick = () => handleConfigSave();
  configActions.appendChild(configSaveBtn);
  configCard.appendChild(configActions);

  const configLockNote = document.createElement("div");
  configLockNote.className = "ddl-config-note";
  configLockNote.textContent = "Changes apply to the next stream start.";
  configCard.appendChild(configLockNote);

  controlsPanel.appendChild(configCard);

  const actions = document.createElement("div");
  actions.className = "ddl-actions";

  const primaryBtn = document.createElement("button");
  primaryBtn.className = "ddl-button ddl-button-primary";
  primaryBtn.textContent = "Start Stream";
  primaryBtn.onclick = () => handleStart();
  actions.appendChild(primaryBtn);

  const secondaryBtn = document.createElement("button");
  secondaryBtn.className = "ddl-button ddl-button-secondary";
  secondaryBtn.textContent = "Abandon stream";
  secondaryBtn.style.display = "none";
  secondaryBtn.onclick = () => handleAbandon();
  actions.appendChild(secondaryBtn);

  controlsPanel.appendChild(actions);

  const logsPanel = document.createElement("div");
  logsPanel.className = "ddl-tab-panel ddl-log-panel";
  const jsonViewer = document.createElement("pre");
  jsonViewer.className = "ddl-json-viewer";
  jsonViewer.textContent = "{}";

  const jsonLabel = document.createElement("div");
  jsonLabel.className = "ddl-note";
  jsonLabel.textContent = "Latest remote status payload";

  logsPanel.appendChild(jsonLabel);
  logsPanel.appendChild(jsonViewer);

  el.appendChild(controlsPanel);
  el.appendChild(logsPanel);

  uiRefs = {
    statusBadge: badge,
    streamId: streamIdValue,
    playbackId: playbackValue,
    whipUrl: whipValue,
    queueDepth: queueValue,
    remoteInfo: remoteValue,
    primaryButton: primaryBtn,
    controlsTabBtn,
    logsTabBtn,
    controlsPanel,
    logsPanel,
    statusJson: jsonViewer,
    configFps: fpsInput,
    configWidth: widthInput,
    configHeight: heightInput,
    configSaveBtn,
    configLockNote,
    secondaryButton: secondaryBtn,
    localApiValue,
  };

  updateConfigControls(null, { locked: false, stream_settings: currentConfig });
  fetchRuntimeConfig();
  refreshLocalServerState({ quiet: true })
    .catch(() => {})
    .finally(() => {
      refreshStatus(false);
      if (pollTimer) {
        clearInterval(pollTimer);
      }
      pollTimer = setInterval(() => refreshStatus(false), STATUS_POLL_INTERVAL);
    });
}

function setActivePanel(panel) {
  if (!uiRefs) return;
  const isControls = panel === "controls";
  uiRefs.controlsPanel.classList.toggle("active", isControls);
  uiRefs.logsPanel.classList.toggle("active", !isControls);
  uiRefs.controlsTabBtn.classList.toggle("active", isControls);
  uiRefs.logsTabBtn.classList.toggle("active", !isControls);
}

app.extensionManager.registerSidebarTab({
  id: "DayDreamLive",
  icon: "pi pi-play",
  title: "DayDream Live",
  tooltip: "Manage DayDream Live stream",
  type: "custom",
  render: (el) => buildPanel(el),
});

// Listen for notifications from backend nodes
api.addEventListener("rtc-stream-notification", (event) => {
  const { severity, summary, detail } = event.detail;
  if (severity && summary) {
    toast(severity, summary, detail || "");
  }
});
