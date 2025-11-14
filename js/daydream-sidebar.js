import { app } from "../../../scripts/app.js";

const DEFAULT_SERVER_BASE = "http://127.0.0.1:8890";
const STATUS_POLL_INTERVAL = 5000;

let serverBase = DEFAULT_SERVER_BASE;
let pollTimer = null;
let uiRefs = null;
let currentStatus = {
  running: false,
  remote_status: {},
  stream_id: "",
  playback_id: "",
};

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
    .ddl-button {
      flex: 1;
      padding: 8px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-weight: 600;
      text-transform: uppercase;
    }
    .ddl-button-start {
      background: #1c7c54;
      color: #fff;
    }
    .ddl-button-stop {
      background: #b3261e;
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

function apiUrl(path) {
  return `${serverBase}${path}`;
}

async function fetchJSON(path, options = {}) {
  const response = await fetch(apiUrl(path), options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail || response.statusText}`);
  }
  if (response.status === 204) {
    return {};
  }
  return response.json();
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
  if (!statusPayload?.running) {
    return { badge: "STOPPED", remoteText: "Stopped" };
  }

  const remoteStatus = statusPayload.remote_status || {};
  const httpStatus =
    remoteStatus.http_status ??
    remoteStatus.status_code ??
    remoteStatus.code;
  const body = remoteStatus.body || remoteStatus;

  if (httpStatus === 404) {
    return { badge: "NOT_FOUND", remoteText: "Not found" };
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

  if (statusPayload.stream_id) {
    return { badge: "STARTING", remoteText: normalized || "STARTING" };
  }

  return { badge: "STOPPED", remoteText: "Stopped" };
}

function updateInfoFields(statusPayload) {
  if (!uiRefs) return;
  const { stream_id, playback_id, whip_url } = statusPayload || {};
  uiRefs.streamId.textContent = stream_id || "—";
  if (playback_id) {
    uiRefs.playbackId.innerHTML = `<a href="https://lvpr.tv?v=${playback_id}" target="_blank" rel="noopener noreferrer">${playback_id}</a>`;
  } else {
    uiRefs.playbackId.textContent = "—";
  }
  uiRefs.whipUrl.textContent = whip_url || "—";

  const descriptor = extractRemoteState(statusPayload);
  uiRefs.remoteInfo.textContent = descriptor.remoteText || "No telemetry";
  applyBadgeState(descriptor.badge, descriptor.remoteText);
}

function setBusy(isBusy) {
  if (!uiRefs) return;
  uiRefs.startBtn.disabled = isBusy;
  uiRefs.stopBtn.disabled = isBusy;
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
    applyBadgeState("NO_SERVER");
    toast("error", "DayDream Live", `Unable to fetch status: ${error.message}`);
  }
}

async function handleStart() {
  setBusy(true);
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
    applyBadgeState("STARTING");
    if (uiRefs) {
      uiRefs.remoteInfo.textContent = "Starting…";
    }
    updateInfoFields(payload);
    await refreshStatus(false);
  } catch (error) {
    applyBadgeState("ERROR");
    toast("error", "DayDream Live", `Start failed: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function handleStop() {
  setBusy(true);
  try {
    const payload = await fetchJSON("/stop", { method: "POST" });
    toast("warn", "DayDream Live", "Stream stopped");
    currentStatus = payload;
    updateInfoFields(payload);
  } catch (error) {
    toast("error", "DayDream Live", `Stop failed: ${error.message}`);
  } finally {
    applyBadgeState("STOPPED");
    setBusy(false);
  }
}

function buildPanel(el) {
  ensureStylesInjected();
  el.innerHTML = "";
  el.className = "ddl-container";

  const header = document.createElement("div");
  header.innerHTML = "<h3>DayDream Live</h3><div class='ddl-note'>Control the RTC stream ingest</div>";
  el.appendChild(header);

  const badge = document.createElement("span");
  badge.className = "ddl-status-badge ddl-badge-idle";
  badge.textContent = "STOPPED";
  el.appendChild(badge);

  const infoGrid = document.createElement("div");
  infoGrid.className = "ddl-info-grid";

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

  [
    streamIdLabel,
    streamIdValue,
    playbackLabel,
    playbackValue,
    whipLabel,
    whipValue,
    remoteLabel,
    remoteValue,
  ].forEach((node) => infoGrid.appendChild(node));

  el.appendChild(infoGrid);

  const actions = document.createElement("div");
  actions.className = "ddl-actions";

  const startBtn = document.createElement("button");
  startBtn.className = "ddl-button ddl-button-start";
  startBtn.textContent = "Start";
  startBtn.onclick = () => handleStart();

  const stopBtn = document.createElement("button");
  stopBtn.className = "ddl-button ddl-button-stop";
  stopBtn.textContent = "Stop";
  stopBtn.onclick = () => handleStop();

  actions.appendChild(startBtn);
  actions.appendChild(stopBtn);
  el.appendChild(actions);

  uiRefs = {
    statusBadge: badge,
    streamId: streamIdValue,
    playbackId: playbackValue,
    whipUrl: whipValue,
    remoteInfo: remoteValue,
    startBtn,
    stopBtn,
  };

  refreshStatus(false);
  if (pollTimer) {
    clearInterval(pollTimer);
  }
  pollTimer = setInterval(() => refreshStatus(false), STATUS_POLL_INTERVAL);
}

app.extensionManager.registerSidebarTab({
  id: "DayDreamLive",
  icon: "pi pi-play",
  title: "DayDream Live",
  tooltip: "Manage DayDream Live stream",
  type: "custom",
  render: (el) => buildPanel(el),
});
