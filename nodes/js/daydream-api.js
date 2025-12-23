import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { ComfyByocRtc } from "./byoc-comfy-rtc.js";

export const STATUS_POLL_INTERVAL = 1000;

export const BADGE_CONFIG = {
  STOPPED: { label: "STOPPED", className: "idle", pulse: false },
  CREATING: { label: "CREATING", className: "info", pulse: true },
  STARTING: { label: "STARTING", className: "starting", pulse: true },
  LOADING: { label: "LOADING", className: "loading", pulse: true },
  ONLINE: { label: "ONLINE", className: "running", pulse: true },
  OFFLINE: { label: "OFFLINE", className: "warning", pulse: true },
  ERROR: { label: "ERROR", className: "error", pulse: false },
  NO_SERVER: { label: "NO SERVER", className: "error", pulse: false }, // legacy (unused)
  NOT_FOUND: { label: "NOT FOUND", className: "error", pulse: false }, // legacy (unused)
};

export const RUNNING_BADGES = new Set(["ONLINE", "LOADING", "STARTING", "CREATING"]);

export function createEmptyStatus() {
  return {
    running: false,
    remote_status: {},
    stream_id: "",
    playback_id: "",
    whip_url: "",
    whep_url: "",
    queue_depth: 0,
    queue_stats: { depth: 0, buffered: 0 },
  };
}

// State
export const state = {
  currentStatus: createEmptyStatus(),
  currentConfig: {
    frame_rate: 30,
    frame_width: 1280,
    frame_height: 720,
  },
  // Legacy fields still referenced by sidebar/toolbar UI.
  localServerState: { running: true, host: null, port: null, type: "in_process" },
  streamUnavailable: false,
  pendingStreamId: "",
  statusPollInitialized: false,
  pollTimer: null,
};

const listeners = new Set();
const rtc = new ComfyByocRtc();

// ---------------------------------------------------------------------------
// Legacy no-ops (kept so existing UI modules keep working)
// ---------------------------------------------------------------------------

export function refreshServerBase() {
  // No separate local server anymore.
}

export async function refreshLocalServerState() {
  state.localServerState = { running: true, host: null, port: null, type: "in_process" };
  notify({ type: "local_state_updated" });
  return state.localServerState;
}

export async function ensureLocalServerReady() {
  return true;
}

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

export function clampNumber(value, min, max, fallback) {
  const num = Number(value);
  if (Number.isFinite(num)) {
    return Math.min(max, Math.max(min, num));
  }
  return fallback;
}

export async function fetchRuntimeConfig() {
  try {
    const payload = await fetch("/rtc/pipeline", { cache: "no-store" }).then((r) => r.json());
    const cfg = payload?.config || {};
    state.currentConfig = {
      frame_rate: cfg.fps ?? state.currentConfig.frame_rate,
      frame_width: cfg.width ?? state.currentConfig.frame_width,
      frame_height: cfg.height ?? state.currentConfig.frame_height,
    };
    notify({ type: "config_updated", payload: cfg });
  } catch (error) {
    console.warn("Unable to load RTC runtime config", error);
  }
}

export async function saveConfig(newConfig) {
  try {
    const response = await fetch("/rtc/pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fps: newConfig.frame_rate,
        width: newConfig.frame_width,
        height: newConfig.frame_height,
      }),
    }).then((r) => r.json());
    state.currentConfig = {
      frame_rate: response?.config?.fps ?? state.currentConfig.frame_rate,
      frame_width: response?.config?.width ?? state.currentConfig.frame_width,
      frame_height: response?.config?.height ?? state.currentConfig.frame_height,
    };
    toast("success", "BYOC Streaming", "Stream settings saved");
    notify({ type: "config_saved", payload: response });
    return response;
  } catch (error) {
    toast("error", "BYOC Streaming", `Unable to save settings: ${error.message}`);
    throw error;
  }
}

export function extractRemoteState(statusPayload) {
  if (!statusPayload?.running) {
    return { badge: "STOPPED", remoteText: "Stopped" };
  }
  const status = (statusPayload?.remote_status?.status || statusPayload?.status || "").toUpperCase();
  if (status === "CONNECTED") return { badge: "ONLINE", remoteText: "CONNECTED" };
  if (status === "CONNECTING") return { badge: "STARTING", remoteText: "CONNECTING" };
  if (status === "ERROR") return { badge: "ERROR", remoteText: statusPayload?.error || "ERROR" };
  return { badge: "LOADING", remoteText: status || "RUNNING" };
}

export async function refreshStatus(showToast = false) {
  try {
    const payload = await fetch("/rtc/session", { cache: "no-store" }).then((r) => r.json());
    const session = payload?.session || {};
    const status = {
      ...createEmptyStatus(),
      running: Boolean(session.running),
      stream_id: session.stream_id || "",
      playback_id: session.playback_url || "",
      whip_url: session.whip_url || "",
      whep_url: session.whep_url || "",
      status: session.status || "disconnected",
      error: session.error || "",
      remote_status: { status: session.status || "disconnected" },
    };
    state.currentStatus = status;
    notify({ type: "status_updated", payload: status });
    if (showToast) {
      toast(
        "info",
        "BYOC Streaming",
        `Stream is ${status.running ? "running" : "stopped"}`
      );
    }
  } catch (error) {
    console.warn("Unable to fetch BYOC session status", error);
    notify({ type: "status_error", payload: state.currentStatus, descriptorOverride: { badge: "ERROR", remoteText: "Status unavailable" } });
  }
}

export async function handleStart() {
  try {
    notify({ type: "starting_stream" });
    const outputVideoEl =
      document.getElementById("previewVideo") ||
      document.querySelector("video") ||
      null;
    await rtc.start({ outputVideoEl });
    toast("success", "BYOC Streaming", "Stream started");
    await refreshStatus(false);
  } catch (error) {
    notify({ type: "error_start", message: error.message });
    toast("error", "BYOC Streaming", `Start failed: ${error.message}`);
  }
}

export async function handleStop() {
  try {
    await rtc.stop();
    toast("warn", "BYOC Streaming", "Stream stopped");
    clearStreamState("Stream stopped", "STOPPED");
  } catch (error) {
    toast("error", "BYOC Streaming", `Stop failed: ${error.message}`);
  }
}

export async function handleAbandon() {
  await handleStop();
}

export function clearStreamState(message = "Stream reset", badge = "STOPPED") {
  const resetStatus = createEmptyStatus();
  state.currentStatus = resetStatus;
  toast("info", "BYOC Streaming", message);
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

