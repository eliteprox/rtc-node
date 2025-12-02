import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

export const LOCAL_SERVER_SETTING_ID = "daydream_live.local_rtc_server";
export const DEFAULT_SERVER_BASE = "http://127.0.0.1:8895";
export const STATUS_POLL_INTERVAL = 1000;
export const RTC_CONTROL_ENDPOINT = "/rtc/control";
export const CONFIG_ENDPOINT_CANDIDATES = ["/config", "/config/", "/runtime-config"];

export const BADGE_CONFIG = {
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

export const RUNNING_BADGES = new Set(["ONLINE", "LOADING", "STARTING", "CREATING"]);

export function createEmptyStatus() {
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

// State
export const state = {
  serverBase: DEFAULT_SERVER_BASE,
  currentStatus: createEmptyStatus(),
  currentConfig: {
    frame_rate: 30,
    frame_width: 1280,
    frame_height: 720,
  },
  localServerState: { running: false, host: null, port: null },
  streamUnavailable: false,
  pendingStreamId: "",
  lastAlignedLocalBase: null,
  resolvedConfigEndpoint: null,
  statusPollInitialized: false,
  pollTimer: null,
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify(event = null) {
  for (const fn of listeners) {
    try {
      fn(state, event);
    } catch (e) {
      console.error("Daydream state listener error:", e);
    }
  }
}

export function toast(severity, summary, detail) {
  if (app.extensionManager && app.extensionManager.toast) {
    app.extensionManager.toast.add({
      severity,
      summary,
      detail,
      life: 3500,
    });
  } else {
    console.log(`[${severity.toUpperCase()}] ${summary}: ${detail}`);
  }
}

export function normalizeServerBase(value) {
  if (!value || typeof value !== "string") {
    return DEFAULT_SERVER_BASE;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return DEFAULT_SERVER_BASE;
  }
  return trimmed.replace(/\/+$/, "");
}

export function refreshServerBase() {
  const val = app.extensionManager.setting.get(LOCAL_SERVER_SETTING_ID);
  const next = normalizeServerBase(val);
  if (state.serverBase !== next) {
    state.serverBase = next;
    refreshStatus(false);
  }
}

export function clampNumber(value, min, max, fallback) {
  const num = Number(value);
  if (Number.isFinite(num)) {
    return Math.min(max, Math.max(min, num));
  }
  return fallback;
}

export function isNotFoundError(error) {
  const match = error?.message?.match(/^(\d+):/);
  return match !== null && Number(match[1]) === 404;
}

function composeConfigCandidates() {
  const candidates = [];
  if (state.resolvedConfigEndpoint) {
    candidates.push(state.resolvedConfigEndpoint);
  }
  for (const candidate of CONFIG_ENDPOINT_CANDIDATES) {
    if (state.resolvedConfigEndpoint && candidate === state.resolvedConfigEndpoint) {
      continue;
    }
    candidates.push(candidate);
  }
  return candidates;
}

function apiUrl(path) {
  return `${state.serverBase}${path}`;
}

const LOCAL_REQUEST_TIMEOUT_MS = 1500;

export async function fetchJSON(path, options = {}, timeout = LOCAL_REQUEST_TIMEOUT_MS) {
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

export async function requestConfigEndpoint(options = {}) {
  const candidates = composeConfigCandidates();
  let lastError = null;
  for (const candidate of candidates) {
    try {
      const payload = await fetchJSON(candidate, options);
      state.resolvedConfigEndpoint = candidate;
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

export async function fetchRuntimeConfig() {
  try {
    const payload = await requestConfigEndpoint();
    state.currentConfig = {
      frame_rate: payload.frame_rate ?? state.currentConfig.frame_rate,
      frame_width: payload.frame_width ?? state.currentConfig.frame_width,
      frame_height: payload.frame_height ?? state.currentConfig.frame_height,
    };
    notify({ type: "config_updated", payload });
  } catch (error) {
    console.warn("Unable to load RTC runtime config", error);
  }
}

export async function saveConfig(newConfig) {
  if (state.currentStatus?.running) {
    toast("warn", "Daydream Live", "Stop the stream before editing FPS/size.");
    throw new Error("Stream running");
  }
  try {
    const response = await requestConfigEndpoint({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newConfig),
    });
    state.currentConfig = {
      frame_rate: response.frame_rate,
      frame_width: response.frame_width,
      frame_height: response.frame_height,
    };
    toast("success", "Daydream Live", "Stream settings saved");
    notify({ type: "config_saved", payload: response });
    return response;
  } catch (error) {
    toast("error", "Daydream Live", `Unable to save settings: ${error.message}`);
    throw error;
  }
}

export async function callRtcControl(action, payload = {}) {
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

export async function refreshLocalServerState({ quiet = false } = {}) {
  try {
    const data = await callRtcControl("status");
    state.localServerState = data.status || { running: false, host: null, port: null };
    alignLocalServerBase(state.localServerState.host, state.localServerState.port);
    notify({ type: "local_state_updated" });
    return state.localServerState;
  } catch (error) {
    state.localServerState = { running: false, host: null, port: null };
    state.lastAlignedLocalBase = null;
    notify({ type: "local_state_updated" });
    if (quiet) {
      return state.localServerState;
    }
    throw error;
  }
}

function alignLocalServerBase(host, port) {
  if (!host || !port) {
    return;
  }
  const localBase = `http://${host}:${port}`;
  if (state.lastAlignedLocalBase === localBase) {
    return;
  }
  const looksLocal = /^https?:\/\/127\.0\.0\.1/i.test(state.serverBase || "") || state.serverBase === DEFAULT_SERVER_BASE;
  if (looksLocal && state.serverBase !== localBase) {
    state.serverBase = localBase;
    state.lastAlignedLocalBase = localBase;
  }
}

export async function ensureLocalServerReady() {
  if (state.localServerState?.running) {
    return true;
  }
  await callRtcControl("start");
  await refreshLocalServerState({ quiet: false });
  if (!state.localServerState?.running) {
    throw new Error("Local API server failed to start");
  }
  return true;
}

export function extractRemoteState(statusPayload) {
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
      statusPayload.stream_id === state.pendingStreamId
    ) {
      return {
        badge: "STARTING",
        remoteText:
          phaseDetail || "Waiting for Daydream Live to accept the WHIP session",
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

export async function refreshStatus(showToast = false) {
  try {
    const status = await fetchJSON("/status");
    state.currentStatus = status;
    notify({ type: "status_updated", payload: status });
    if (showToast) {
      toast(
        "info",
        "Daydream Live",
        `Stream is ${status.running ? "running" : "stopped"}`
      );
    }
  } catch (error) {
    console.warn("Unable to fetch Daydream Live status", error);
    const fallbackStatus = state.currentStatus ? { ...state.currentStatus } : createEmptyStatus();
    const localState = await refreshLocalServerState({ quiet: true });
    let descriptorOverride = {
      badge: "NO_SERVER",
      remoteText: "Local API server unavailable",
    };

    if (localState?.running) {
      const awaitingPending =
        state.pendingStreamId && state.pendingStreamId === fallbackStatus.stream_id;
      if (awaitingPending) {
        fallbackStatus.running = true;
        descriptorOverride = {
          badge: "STARTING",
          remoteText: "Waiting for Daydream Live to finish starting…",
        };
      } else {
        const hasActiveStream =
          Boolean(fallbackStatus.running) || Boolean(fallbackStatus.stream_id);
        fallbackStatus.running = hasActiveStream;
        descriptorOverride = hasActiveStream
          ? {
              badge: "LOADING",
              remoteText: "Stream is starting...",
            }
          : { badge: "STOPPED", remoteText: "Stopped" };
      }
    }
    notify({ type: "status_error", payload: fallbackStatus, descriptorOverride });
  }
}

export async function handleStart() {
  if (state.streamUnavailable) {
    toast(
      "warn",
      "Daydream Live",
      "Stream previously removed; clear it before creating a new one."
    );
    return;
  }
  try {
    await ensureLocalServerReady();
  } catch (error) {
    notify({ type: "error_no_server", message: error.message });
    toast("error", "Daydream Live", `Local API server unavailable: ${error.message}`);
    return;
  }
  
  notify({ type: "creating_stream" });
  
  try {
    const payload = await fetchJSON("/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    toast("success", "Daydream Live", "Stream started");
    state.currentStatus = payload;
    state.streamUnavailable = false;
    state.pendingStreamId = payload.stream_id || "";
    
    notify({ type: "starting_stream", payload });
    await refreshStatus(false);
  } catch (error) {
    notify({ type: "error_start", message: error.message });
    toast("error", "Daydream Live", `Start failed: ${error.message}`);
  }
}

export async function handleStop() {
  try {
    await fetchJSON("/stop", { method: "POST" });
    toast("warn", "Daydream Live", "Stream stopped");
    clearStreamState("Stream stopped", "STOPPED");
  } catch (error) {
    toast("error", "Daydream Live", `Stop failed: ${error.message}`);
  } finally {
    state.streamUnavailable = false;
    state.pendingStreamId = "";
    notify({ type: "stopped" });
  }
}

export async function handleAbandon() {
  if (state.streamUnavailable) {
    state.streamUnavailable = false;
    clearStreamState("Stream cleared");
    return;
  }

  try {
    await fetchJSON("/stop", { method: "POST" });
    toast("warn", "Daydream Live", "Stream abandoned");
    clearStreamState("Stream abandoned", "STOPPED");
  } catch (error) {
    toast("error", "Daydream Live", `Unable to abandon stream: ${error.message}`);
  } finally {
    state.streamUnavailable = false;
    state.pendingStreamId = "";
    notify({ type: "abandoned" });
  }
}

export function clearStreamState(message = "Stream reset", badge = "STOPPED") {
  state.pendingStreamId = "";
  const resetStatus = createEmptyStatus();
  state.currentStatus = resetStatus;
  toast("info", "Daydream Live", message);
  notify({ 
    type: "reset", 
    payload: resetStatus,
    descriptorOverride: { badge, remoteText: message } 
  });
}

export function ensureStatusPolling() {
  if (!state.statusPollInitialized) {
    state.statusPollInitialized = true;
    fetchRuntimeConfig();
    refreshLocalServerState({ quiet: true }).catch(() => {});
  }
  if (!state.pollTimer) {
    refreshStatus(false);
    state.pollTimer = setInterval(() => refreshStatus(false), STATUS_POLL_INTERVAL);
  }
}

// Listen for notifications from backend nodes
api.addEventListener("rtc-stream-notification", (event) => {
  const { severity, summary, detail } = event.detail;
  if (severity && summary) {
    toast(severity, summary, detail || "");
  }
});

