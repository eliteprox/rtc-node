import { app } from "../../../scripts/app.js";
import {
  state,
  subscribe,
  BADGE_CONFIG,
  RUNNING_BADGES,
  extractRemoteState,
  createEmptyStatus,
  handleStart,
  handleStop,
  saveConfig,
  refreshStatus,
  ensureStatusPolling,
  clampNumber
} from "./daydream-api.js";

let toolbarRefs = null;
let toolbarBusy = false;
let toolbarBusyLabel = "";
let lastStatusDescriptor = null;
let toolbarMountTimer = null;

function ensureToolbarStyles() {
  if (document.getElementById("daydream-toolbar-styles")) {
    return;
  }
  const style = document.createElement("style");
  style.id = "daydream-toolbar-styles";
  style.textContent = `
    .ddl-toolbar-controls {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-left: 12px;
    }
    .ddl-toolbar-badge {
      padding: 3px 10px;
      font-size: 0.75rem;
      font-weight: 700;
      border-radius: 12px;
      text-transform: uppercase;
      color: white;
    }
    .ddl-badge-idle { background: #4a4a4a; color: #fff; }
    .ddl-badge-info { background: #3a7bff; color: #fff; }
    .ddl-badge-starting { background: #2d7dd2; color: #fff; }
    .ddl-badge-loading { background: #e88c20; color: #1b1300; }
    .ddl-badge-running { background: #1c7c54; color: #fff; }
    .ddl-badge-warning { background: #d97706; color: #1b1300; }
    .ddl-badge-error { background: #b3261e; color: #fff; }
    .ddl-badge-pulse { animation: ddlPulse 1.4s ease-in-out infinite; }
    @keyframes ddlPulse {
      0% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.4); }
      70% { box-shadow: 0 0 0 4px rgba(255, 255, 255, 0); }
      100% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0); }
    }
    .ddl-toolbar-btn {
      padding: 5px 12px;
      border-radius: 6px;
      border: none;
      background: #5551d4;
      color: #fff;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, opacity 0.2s ease;
    }
    .ddl-toolbar-btn:hover { background: #6a65ff; }
    .ddl-toolbar-btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .ddl-toolbar-icon {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      border: none;
      background: #2f2f2f;
      color: #f5f5f5;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: background 0.2s ease;
    }
    .ddl-toolbar-icon:hover { background: #3a3a3a; }
    .ddl-dialog-overlay {
      position: fixed; inset: 0; background: rgba(0, 0, 0, 0.65);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
    }
    .ddl-dialog {
      background: #1b1b1b; border: 1px solid #333; border-radius: 8px;
      padding: 16px; min-width: 320px; max-width: 420px;
      box-shadow: 0 20px 45px rgba(0, 0, 0, 0.4);
      display: flex; flex-direction: column; gap: 12px;
      animation: ddlDialogIn 0.15s ease-out;
    }
    @keyframes ddlDialogIn {
      from { opacity: 0; transform: scale(0.96); }
      to { opacity: 1; transform: scale(1); }
    }
    .ddl-dialog-header {
      display: flex; justify-content: space-between; align-items: center;
      font-weight: 700; color: #f5f5f5;
    }
    .ddl-dialog-close {
      background: none; border: none; color: #f5f5f5; font-size: 1.2rem; cursor: pointer;
    }
    .ddl-dialog-body { display: flex; flex-direction: column; gap: 10px; }
    .ddl-dialog-field { display: flex; flex-direction: column; gap: 4px; }
    .ddl-dialog-field label { font-size: 0.8rem; color: #bbb; }
    .ddl-dialog-field input {
      background: #111; border: 1px solid #333; border-radius: 4px;
      padding: 6px; color: #fff;
    }
    .ddl-dialog-note { font-size: 0.78rem; color: #bbb; }
    .ddl-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .ddl-dialog-btn {
      padding: 6px 12px; border-radius: 4px; border: none;
      font-weight: 600; cursor: pointer;
    }
    .ddl-dialog-btn.primary { background: #5551d4; color: #fff; }
    .ddl-dialog-btn.secondary { background: #2f2f2f; color: #f5f5f5; }
    .ddl-dialog-btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .ddl-dialog-grid {
      display: grid; grid-template-columns: 120px 1fr; gap: 6px 10px; font-size: 0.85rem;
    }
    .ddl-dialog-grid-label { color: #aaa; }
    .ddl-dialog-grid-value { color: #eee; word-break: break-all; }
    .ddl-json-viewer {
      background: #141414; border-radius: 6px; padding: 8px;
      border: 1px solid #2a2a2a; color: #b7ffd8;
      font-family: "JetBrains Mono", "Fira Code", monospace;
      font-size: 0.75rem; overflow: auto; white-space: pre-wrap; word-break: break-word;
    }
  `;
  document.head.appendChild(style);
}

function isStreamActive(payload, descriptor) {
  if (payload?.running) {
    return true;
  }
  if (descriptor?.badge && RUNNING_BADGES.has(descriptor.badge)) {
    return true;
  }
  if (payload?.stream_id && descriptor?.badge === "STARTING") {
    return true;
  }
  return false;
}

function setToolbarBusy(isBusy, label = "") {
  toolbarBusy = isBusy;
  toolbarBusyLabel = label;
  updateToolbarBadge();
}

function updateToolbarBadge(statusPayload = null, descriptorOverride = null) {
  const payload = statusPayload || state.currentStatus || createEmptyStatus();
  const descriptor = descriptorOverride || extractRemoteState(payload || {});
  lastStatusDescriptor = descriptor;
  if (!toolbarRefs) {
    return;
  }
  const cfg = BADGE_CONFIG[descriptor.badge] || BADGE_CONFIG.STOPPED;
  if (toolbarRefs.statusBadge) {
    const badgeClasses = ["ddl-status-badge", "ddl-toolbar-badge", `ddl-badge-${cfg.className}`];
    if (cfg.pulse) {
      badgeClasses.push("ddl-badge-pulse");
    }
    toolbarRefs.statusBadge.className = badgeClasses.join(" ");
    toolbarRefs.statusBadge.textContent =
      descriptor.badge === "ONLINE"
        ? "Streaming"
        : (descriptor.remoteText || cfg.label || descriptor.badge || "Status").toUpperCase();
    toolbarRefs.statusBadge.title = descriptor.remoteText || "No telemetry";
  }

  if (toolbarRefs.toggleBtn) {
    let buttonLabel = "Start";
    const active = isStreamActive(payload, descriptor);
    if (toolbarBusy) {
      buttonLabel = toolbarBusyLabel || (active ? "Stopping..." : "Starting...");
    } else if (state.pendingStreamId && !active) {
      buttonLabel = "Waiting...";
    } else if (active) {
      buttonLabel = "Stop";
    }
    toolbarRefs.toggleBtn.textContent = buttonLabel;
    toolbarRefs.toggleBtn.disabled = toolbarBusy;
    toolbarRefs.toggleBtn.title = active
      ? "Stop the active Daydream stream"
      : "Start a Daydream stream";
  }
  if (toolbarRefs.settingsBtn) {
    toolbarRefs.settingsBtn.title = "Configure Daydream stream settings";
  }
  if (toolbarRefs.detailsBtn) {
    toolbarRefs.detailsBtn.title = `View detailed status${
      descriptor.remoteText ? `: ${descriptor.remoteText}` : ""
    }`;
  }
}

async function handleToolbarToggle() {
  if (toolbarBusy) {
    return;
  }
  const descriptor = lastStatusDescriptor || extractRemoteState(state.currentStatus || {});
  const active = isStreamActive(state.currentStatus, descriptor);
  setToolbarBusy(true, active ? "Stopping..." : "Starting...");
  try {
    if (active) {
      await handleStop();
    } else {
      await handleStart();
    }
  } finally {
    setToolbarBusy(false);
  }
}

function createDialogOverlay(id, title) {
  const existing = document.getElementById(id);
  if (existing) {
    existing.remove();
  }
  ensureToolbarStyles();
  const overlay = document.createElement("div");
  overlay.id = id;
  overlay.className = "ddl-dialog-overlay";
  const dialog = document.createElement("div");
  dialog.className = "ddl-dialog";
  const header = document.createElement("div");
  header.className = "ddl-dialog-header";
  const titleEl = document.createElement("div");
  titleEl.textContent = title;
  const closeBtn = document.createElement("button");
  closeBtn.className = "ddl-dialog-close";
  closeBtn.textContent = "×";
  closeBtn.onclick = () => overlay.remove();
  header.appendChild(titleEl);
  header.appendChild(closeBtn);
  dialog.appendChild(header);
  const body = document.createElement("div");
  body.className = "ddl-dialog-body";
  dialog.appendChild(body);
  const actions = document.createElement("div");
  actions.className = "ddl-dialog-actions";
  dialog.appendChild(actions);
  overlay.appendChild(dialog);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      overlay.remove();
    }
  });
  document.body.appendChild(overlay);
  return {
    overlay,
    dialog,
    body,
    actions,
    close: () => overlay.remove(),
  };
}

function openToolbarConfigDialog() {
  const locked = Boolean(state.currentStatus?.running);
  const { body, actions, close } = createDialogOverlay(
    "daydream-config-dialog",
    "Daydream Live Stream Settings"
  );

  const fpsField = document.createElement("div");
  fpsField.className = "ddl-dialog-field";
  const fpsLabel = document.createElement("label");
  fpsLabel.textContent = "Frame Rate (FPS)";
  const fpsInput = document.createElement("input");
  fpsInput.type = "number";
  fpsInput.min = "1";
  fpsInput.max = "240";
  fpsInput.step = "1";
  fpsInput.value = state.currentConfig.frame_rate;
  fpsInput.disabled = locked;
  fpsField.appendChild(fpsLabel);
  fpsField.appendChild(fpsInput);

  const widthField = document.createElement("div");
  widthField.className = "ddl-dialog-field";
  const widthLabel = document.createElement("label");
  widthLabel.textContent = "Frame Width";
  const widthInput = document.createElement("input");
  widthInput.type = "number";
  widthInput.min = "64";
  widthInput.max = "4096";
  widthInput.step = "1";
  widthInput.value = state.currentConfig.frame_width;
  widthInput.disabled = locked;
  widthField.appendChild(widthLabel);
  widthField.appendChild(widthInput);

  const heightField = document.createElement("div");
  heightField.className = "ddl-dialog-field";
  const heightLabel = document.createElement("label");
  heightLabel.textContent = "Frame Height";
  const heightInput = document.createElement("input");
  heightInput.type = "number";
  heightInput.min = "64";
  heightInput.max = "4096";
  heightInput.step = "1";
  heightInput.value = state.currentConfig.frame_height;
  heightInput.disabled = locked;
  heightField.appendChild(heightLabel);
  heightField.appendChild(heightInput);

  body.appendChild(fpsField);
  body.appendChild(widthField);
  body.appendChild(heightField);

  const note = document.createElement("div");
  note.className = "ddl-dialog-note";
  note.textContent = locked
    ? "Stop the stream to edit FPS or resolution."
    : "Changes apply to the next stream start.";
  body.appendChild(note);

  const closeBtn = document.createElement("button");
  closeBtn.className = "ddl-dialog-btn secondary";
  closeBtn.textContent = "Close";
  closeBtn.onclick = () => close();
  actions.appendChild(closeBtn);

  if (!locked) {
    const saveBtn = document.createElement("button");
    saveBtn.className = "ddl-dialog-btn primary";
    saveBtn.textContent = "Save";
    saveBtn.onclick = async () => {
      const payload = {
        frame_rate: clampNumber(fpsInput.value, 1, 240, state.currentConfig.frame_rate),
        frame_width: clampNumber(widthInput.value, 64, 4096, state.currentConfig.frame_width),
        frame_height: clampNumber(heightInput.value, 64, 4096, state.currentConfig.frame_height),
      };
      saveBtn.disabled = true;
      try {
        await saveConfig(payload);
        close();
      } catch (error) {
        // Toast handled in saveConfig
      } finally {
        saveBtn.disabled = false;
      }
    };
    actions.appendChild(saveBtn);
  }
}

function openToolbarStatusDialog() {
  const { body, actions, close } = createDialogOverlay(
    "daydream-status-dialog",
    "Daydream Stream Status"
  );

  const grid = document.createElement("div");
  grid.className = "ddl-dialog-grid";

  function addRow(labelText) {
    const label = document.createElement("div");
    label.className = "ddl-dialog-grid-label";
    label.textContent = labelText;
    const value = document.createElement("div");
    value.className = "ddl-dialog-grid-value";
    grid.appendChild(label);
    grid.appendChild(value);
    return value;
  }

  const stateValue = addRow("State");
  const remoteValue = addRow("Remote Detail");
  const streamIdValue = addRow("Stream ID");
  const playbackValue = addRow("Playback ID");
  const whipValue = addRow("WHIP URL");
  const queueValue = addRow("Queue");
  const localApiValue = addRow("Local API");

  const jsonLabel = document.createElement("div");
  jsonLabel.className = "ddl-dialog-note";
  jsonLabel.textContent = "Latest remote payload";

  const jsonViewer = document.createElement("pre");
  jsonViewer.className = "ddl-json-viewer";
  jsonViewer.style.maxHeight = "200px";

  body.appendChild(grid);
  body.appendChild(jsonLabel);
  body.appendChild(jsonViewer);

  function renderDetails() {
    const payload = state.currentStatus || createEmptyStatus();
    const descriptor = lastStatusDescriptor || extractRemoteState(payload || {});
    stateValue.textContent = descriptor.badge || "UNKNOWN";
    remoteValue.textContent = descriptor.remoteText || "No telemetry";
    streamIdValue.textContent = payload.stream_id || "—";
    playbackValue.textContent = payload.playback_id || "—";
    whipValue.textContent = payload.whip_url || "—";
    const queueDepth = payload.queue_depth ?? 0;
    const buffered = payload.queue_stats?.buffered ?? 0;
    queueValue.textContent = buffered ? `${queueDepth} (buffered ${buffered})` : `${queueDepth}`;
    if (payload.playback_id) {
      playbackValue.innerHTML = `<a href="https://lvpr.tv?v=${payload.playback_id}" target="_blank" rel="noopener noreferrer">${payload.playback_id}</a>`;
    }
    if (payload.whip_url) {
      whipValue.textContent = payload.whip_url;
    }
    const running = state.localServerState?.running;
    localApiValue.textContent = running
      ? `${state.localServerState.host || "127.0.0.1"}:${state.localServerState.port ?? "—"}`
      : "Offline";
    const jsonSource =
      payload?.remote_status?.body ||
      payload?.remote_status ||
      payload ||
      {};
    jsonViewer.textContent = JSON.stringify(jsonSource, null, 2);
  }

  const closeBtn = document.createElement("button");
  closeBtn.className = "ddl-dialog-btn secondary";
  closeBtn.textContent = "Close";
  closeBtn.onclick = () => close();

  const refreshBtn = document.createElement("button");
  refreshBtn.className = "ddl-dialog-btn primary";
  refreshBtn.textContent = "Refresh";
  refreshBtn.onclick = async () => {
    refreshBtn.disabled = true;
    try {
      await refreshStatus(false);
    } finally {
      refreshBtn.disabled = false;
      renderDetails();
    }
  };

  actions.appendChild(closeBtn);
  actions.appendChild(refreshBtn);
  renderDetails();
}

function attachToolbarHandlers(refs) {
  if (!refs) return;
  if (refs.toggleBtn) {
    refs.toggleBtn.onclick = () => handleToolbarToggle();
  }
  if (refs.settingsBtn) {
    refs.settingsBtn.onclick = () => openToolbarConfigDialog();
  }
  if (refs.detailsBtn) {
    refs.detailsBtn.onclick = () => openToolbarStatusDialog();
  }
}

function buildToolbarControls(menuBar) {
  ensureToolbarStyles();
  const existing = document.getElementById("daydream-toolbar-controls");
  if (existing) {
    toolbarRefs = {
      container: existing,
      statusBadge: existing.querySelector(".ddl-toolbar-badge"),
      toggleBtn: existing.querySelector('[data-role="ddl-toolbar-toggle"]'),
      settingsBtn: existing.querySelector('[data-role="ddl-toolbar-settings"]'),
      detailsBtn: existing.querySelector('[data-role="ddl-toolbar-details"]'),
    };
    attachToolbarHandlers(toolbarRefs);
    updateToolbarBadge();
    return toolbarRefs;
  }

  const container = document.createElement("div");
  container.id = "daydream-toolbar-controls";
  container.className = "ddl-toolbar-controls";

  const badge = document.createElement("span");
  badge.className = "ddl-status-badge ddl-toolbar-badge ddl-badge-idle";
  badge.textContent = "Stopped";

  const toggleBtn = document.createElement("button");
  toggleBtn.className = "ddl-toolbar-btn";
  toggleBtn.textContent = "Start";
  toggleBtn.dataset.role = "ddl-toolbar-toggle";

  const settingsBtn = document.createElement("button");
  settingsBtn.className = "ddl-toolbar-icon";
  settingsBtn.innerHTML = "⚙";
  settingsBtn.dataset.role = "ddl-toolbar-settings";

  const infoBtn = document.createElement("button");
  infoBtn.className = "ddl-toolbar-icon";
  infoBtn.innerHTML = "ℹ";
  infoBtn.dataset.role = "ddl-toolbar-details";

  container.appendChild(badge);
  container.appendChild(toggleBtn);
  container.appendChild(settingsBtn);
  container.appendChild(infoBtn);
  menuBar.appendChild(container);

  toolbarRefs = {
    container,
    statusBadge: badge,
    toggleBtn,
    settingsBtn,
    detailsBtn: infoBtn,
  };
  attachToolbarHandlers(toolbarRefs);
  updateToolbarBadge();
  return toolbarRefs;
}

function initToolbarControls() {
  if (toolbarRefs && document.body.contains(toolbarRefs.container)) {
    return toolbarRefs;
  }
  const menuBar = document.querySelector(".comfyui-menu");
  if (menuBar) {
    return buildToolbarControls(menuBar);
  }
  if (!toolbarMountTimer) {
    let attempts = 0;
    toolbarMountTimer = setInterval(() => {
      const target = document.querySelector(".comfyui-menu");
      if (target) {
        clearInterval(toolbarMountTimer);
        toolbarMountTimer = null;
        buildToolbarControls(target);
      } else if (attempts++ > 40) {
        clearInterval(toolbarMountTimer);
        toolbarMountTimer = null;
      }
    }, 250);
  }
  return null;
}

// Subscribe to state changes
subscribe((newState, event) => {
  const descriptor = event?.descriptorOverride;
  updateToolbarBadge(newState.currentStatus, descriptor);
});

app.registerExtension({
  name: "Daydream.ToolbarBadge",
  async setup() {
    initToolbarControls();
    ensureStatusPolling();
  },
});

