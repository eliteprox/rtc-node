import { app } from "../../../scripts/app.js";

const LOCAL_SERVER_SETTING_ID = "daydream_live.local_rtc_server";
const API_BASE_SETTING_ID = "daydream_live.api_base_url";
const API_KEY_SETTING_ID = "daydream_live.api_key";
const API_DASHBOARD_LINK_SETTING_ID = "daydream_live.api_dashboard_link";
const DEFAULT_SERVER_BASE = "http://127.0.0.1:8895";
const DEFAULT_API_BASE = "https://api.daydream.live";
const DAYDREAM_DASHBOARD_URL = "https://app.daydream.live/dashboard/api-keys";

let isExtensionInitializing = true;

function toast(severity, summary, detail) {
  app.extensionManager.toast.add({
    severity,
    summary,
    detail,
    life: 3500,
  });
}

function ensureSettingDefault(id, fallback) {
  const existing = app.extensionManager.setting.get(id);
  if (typeof existing === "string" && existing.trim().length > 0) {
    return existing;
  }
  app.extensionManager.setting.set(id, fallback).catch((err) => {
    console.error(`Failed to persist default for ${id}`, err);
  });
  return fallback;
}

function handleApiBaseSettingChange(value) {
  if (isExtensionInitializing) {
    return;
  }
  const normalized = (value || "").trim();
  if (!normalized) {
    toast(
      "warn",
      "Daydream Live",
      "API base URL cleared. Daydream defaults will be used until you provide one."
    );
    return;
  }
  toast("info", "Daydream Live", "Daydream API base URL updated.");
}

function handleApiKeySettingChange(value) {
  if (isExtensionInitializing) {
    return;
  }
  const trimmed = (value || "").trim();
  if (trimmed) {
    toast("success", "Daydream Live", "Daydream API key stored in ComfyUI settings.");
  } else {
    toast("warn", "Daydream Live", "Daydream API key removed from settings.");
  }
}

app.registerExtension({
  name: "Daydream Live Settings",
  settings: [
    {
      id: LOCAL_SERVER_SETTING_ID,
      name: "Local RTC Server Base URL",
      type: "text",
      defaultValue: DEFAULT_SERVER_BASE,
      category: ["Daydream Live", "Local Server", "Base URL"],
      tooltip: "Base REST endpoint for the Daydream Live custom node local API server",
      attrs: {
        placeholder: DEFAULT_SERVER_BASE,
      },
    },
    {
      id: API_BASE_SETTING_ID,
      name: "Daydream API Base URL",
      type: "text",
      defaultValue: DEFAULT_API_BASE,
      category: ["Daydream Live", "API Credentials", "API Base URL"],
      tooltip: "Base REST endpoint for the Daydream Live API (used by the local server)",
      attrs: {
        placeholder: DEFAULT_API_BASE,
      },
      onChange: (value) => handleApiBaseSettingChange(value),
    },
    {
      id: API_KEY_SETTING_ID,
      name: "Daydream API Key",
      type: "text",
      defaultValue: "",
      category: ["Daydream Live", "API Credentials", "API Key"],
      tooltip: "Stored within your ComfyUI profile settings.",
      attrs: {
        placeholder: "Paste API key (saved to settings)",
        autocomplete: "off",
        type: "password",
      },
      onChange: (value) => handleApiKeySettingChange(value),
    },
    {
      id: API_DASHBOARD_LINK_SETTING_ID,
      name: "Manage API Keys",
      type: "text",
      defaultValue: "Open Daydream Dashboard",
      category: ["Daydream Live", "API Credentials", "Dashboard"],
      tooltip: "Log in to Daydream Live to create or rotate API keys.",
      attrs: {
        type: "button",
        value: "Open Dashboard",
        onclick: `window.open('${DAYDREAM_DASHBOARD_URL}', '_blank'); return false;`,
        style: "cursor:pointer",
      },
    },
  ],
  setup() {
    ensureSettingDefault(LOCAL_SERVER_SETTING_ID, DEFAULT_SERVER_BASE);
    ensureSettingDefault(API_BASE_SETTING_ID, DEFAULT_API_BASE);

    setTimeout(() => {
      isExtensionInitializing = false;
    }, 300);
  },
});

