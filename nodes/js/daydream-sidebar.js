import { app } from "../../../scripts/app.js";
import {
  state,
  subscribe,
  refreshServerBase,
  fetchRuntimeConfig,
  refreshLocalServerState,
  refreshStatus,
  handleStart,
  handleStop,
  handleAbandon,
  saveConfig,
  extractRemoteState,
  BADGE_CONFIG,
  RUNNING_BADGES,
  clampNumber,
  createEmptyStatus
} from "./daydream-api.js";

let uiRefs = null;
let pollTimer = null; // Used for local UI updates if needed, or we rely on api.js polling
let statusDotRef = null;
let statusDotMountTimer = null;

function updateConfigControls(statusPayload = null, options = {}) {
  if (!uiRefs) return;
  const incoming =
    options.stream_settings || statusPayload?.stream_settings || null;
  if (incoming) {
    // We rely on state.currentConfig, but here we might receive a payload
  }
  // Use state.currentConfig as the source of truth for values if not passed
  const config = incoming || state.currentConfig;
  
  const locked =
    typeof options.locked === "boolean"
      ? options.locked
      : Boolean(statusPayload?.running);
  if (uiRefs.configFps) {
    uiRefs.configFps.value = config.frame_rate;
    uiRefs.configFps.disabled = locked;
  }
  if (uiRefs.configWidth) {
    uiRefs.configWidth.value = config.frame_width;
    uiRefs.configWidth.disabled = locked;
  }
  if (uiRefs.configHeight) {
    uiRefs.configHeight.value = config.frame_height;
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

function readConfigForm() {
  if (!uiRefs) {
    return state.currentConfig;
  }
  return {
    frame_rate: clampNumber(uiRefs.configFps?.value, 1, 240, state.currentConfig.frame_rate),
    frame_width: clampNumber(uiRefs.configWidth?.value, 64, 4096, state.currentConfig.frame_width),
    frame_height: clampNumber(uiRefs.configHeight?.value, 64, 4096, state.currentConfig.frame_height),
  };
}

async function handleConfigSaveWrapper() {
  const payload = readConfigForm();
  try {
    await saveConfig(payload);
    // UI update happens via subscription
  } catch (error) {
    // Error toast handled in api.js
  }
}

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
    .ddl-sidebar-status-dot {
      position: absolute;
      top: 4px;
      right: 4px;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #4a4a4a;
      border: 1px solid rgba(0,0,0,0.3);
      pointer-events: none;
      transition: background 0.3s ease, box-shadow 0.3s ease;
    }
    .ddl-sidebar-status-dot.active {
      background: #1c7c54;
      box-shadow: 0 0 6px #1c7c54;
    }
    .ddl-sidebar-status-dot.starting {
      background: #2d7dd2;
      box-shadow: 0 0 6px #2d7dd2;
      animation: ddlDotPulse 1.2s ease-in-out infinite;
    }
    .ddl-sidebar-status-dot.error {
      background: #b3261e;
      box-shadow: 0 0 6px #b3261e;
    }
    @keyframes ddlDotPulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
  `;
  document.head.appendChild(style);
}

function updateLocalApiDisplay() {
  if (!uiRefs?.localApiValue) {
    return;
  }
  const running = Boolean(state.localServerState?.running);
  if (running) {
    const host = state.localServerState.host || "127.0.0.1";
    const port = state.localServerState.port ?? "—";
    uiRefs.localApiValue.textContent = `${host}:${port}`;
  } else {
    uiRefs.localApiValue.textContent = "Offline";
  }
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
    statusPayload?.stream_id === state.pendingStreamId;
  const isNotFound = descriptor.badge === "NOT_FOUND";

  if (isRunning) {
    primary.textContent = "Stop Stream";
    primary.className = "ddl-button ddl-button-danger";
    primary.disabled = false;
    primary.onclick = () => handleStop();
    secondary.style.display = "none";
    return;
  }

  if (isNotFound) {
    // State handled in API, here we just render
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
  header.innerHTML = "<h3>Daydream Live</h3><div class='ddl-note'>Control the RTC stream ingest</div>";
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
  fpsInput.value = state.currentConfig.frame_rate;

  const widthInput = document.createElement("input");
  widthInput.type = "number";
  widthInput.min = "64";
  widthInput.max = "4096";
  widthInput.step = "1";
  widthInput.className = "ddl-config-input";
  widthInput.value = state.currentConfig.frame_width;

  const heightInput = document.createElement("input");
  heightInput.type = "number";
  heightInput.min = "64";
  heightInput.max = "4096";
  heightInput.step = "1";
  heightInput.className = "ddl-config-input";
  heightInput.value = state.currentConfig.frame_height;

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
  configSaveBtn.onclick = () => handleConfigSaveWrapper();
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

  updateConfigControls(null, { locked: false, stream_settings: state.currentConfig });
  updateInfoFields(state.currentStatus);
  updateLocalApiDisplay();
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
  id: "DaydreamLive",
  icon: "pi pi-play",
  title: "Daydream Live",
  tooltip: "Manage Daydream Live stream",
  type: "custom",
  render: (el) => buildPanel(el),
});

function updateSidebarStatusDot(descriptor = null) {
  if (!statusDotRef) return;
  const desc = descriptor || extractRemoteState(state.currentStatus || {});
  const badge = desc?.badge || "STOPPED";
  
  statusDotRef.classList.remove("active", "starting", "error");
  
  if (badge === "ONLINE") {
    statusDotRef.classList.add("active");
  } else if (RUNNING_BADGES.has(badge) && badge !== "ONLINE") {
    statusDotRef.classList.add("starting");
  } else if (badge === "ERROR" || badge === "NO_SERVER" || badge === "NOT_FOUND") {
    statusDotRef.classList.add("error");
  }
  // Otherwise stays idle (gray)
}

function mountSidebarStatusDot() {
  ensureStylesInjected();
  // Find the sidebar tab button for DaydreamLive
  const tabButton = document.querySelector('[data-id="DaydreamLive"]');
  if (!tabButton) return false;
  
  // Check if dot already exists
  if (tabButton.querySelector(".ddl-sidebar-status-dot")) {
    statusDotRef = tabButton.querySelector(".ddl-sidebar-status-dot");
    return true;
  }
  
  // Ensure the button has position relative for absolute positioning of dot
  const computedStyle = window.getComputedStyle(tabButton);
  if (computedStyle.position === "static") {
    tabButton.style.position = "relative";
  }
  
  const dot = document.createElement("span");
  dot.className = "ddl-sidebar-status-dot";
  tabButton.appendChild(dot);
  statusDotRef = dot;
  updateSidebarStatusDot();
  return true;
}

function initSidebarStatusDot() {
  if (statusDotRef && document.body.contains(statusDotRef)) {
    return;
  }
  if (mountSidebarStatusDot()) {
    if (statusDotMountTimer) {
      clearInterval(statusDotMountTimer);
      statusDotMountTimer = null;
    }
    return;
  }
  // Retry until sidebar is rendered
  if (!statusDotMountTimer) {
    let attempts = 0;
    statusDotMountTimer = setInterval(() => {
      if (mountSidebarStatusDot()) {
        clearInterval(statusDotMountTimer);
        statusDotMountTimer = null;
      } else if (attempts++ > 60) {
        clearInterval(statusDotMountTimer);
        statusDotMountTimer = null;
      }
    }, 500);
  }
}

// Subscribe to state changes from api.js
subscribe((newState, event) => {
  if (event?.type === "local_state_updated") {
    updateLocalApiDisplay();
  }
  const descriptor = event?.descriptorOverride;
  updateInfoFields(newState.currentStatus, { descriptorOverride: descriptor });
  if (event?.type === "config_updated" || event?.type === "config_saved") {
    updateConfigControls(null, { stream_settings: newState.currentConfig });
  }
  updateSidebarStatusDot(descriptor);
});

// Initial sync
refreshServerBase();
initSidebarStatusDot();
