import { app } from "../../../scripts/app.js";
const LOCAL_SERVER_SETTING_ID = "Daydream Live.local_rtc_server";
const API_BASE_SETTING_ID = "Daydream Live.api_base_url";
const API_KEY_SETTING_ID = "Daydream Live.api_key";
const DEFAULT_SERVER_BASE = "http://127.0.0.1:8895";
const DEFAULT_API_BASE = "https://api.daydream.live";
const CREDENTIALS_ENDPOINT = "/rtc/credentials";
const DAYDREAM_DASHBOARD_URL = "https://app.daydream.live/dashboard/api-keys";

let credentialsState = {
  apiUrl: DEFAULT_API_BASE,
  hasApiKey: false,
  sources: { api_url: "default", api_key: "missing" },
};
let apiKeyDebounceTimer = null;
let suppressApiBaseEvent = false;
let suppressApiKeyEvent = false;
let isExtensionInitializing = true;

function normalizeApiBase(value) {
  if (!value || typeof value !== "string") {
    return DEFAULT_API_BASE;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return DEFAULT_API_BASE;
  }
  return trimmed.replace(/\/+$/, "");
}

async function fetchCredentialSnapshot() {
  try {
    const response = await fetch(CREDENTIALS_ENDPOINT, { method: "GET" });
    if (!response.ok) {
      throw new Error(response.statusText || "Failed to fetch credentials");
    }
    const payload = await response.json();
    const data = payload?.credentials || payload || {};
    const sources = data.sources || {};
    const apiUrl = normalizeApiBase(data.api_url || data.apiUrl || DEFAULT_API_BASE);
    const hasApiKey =
      typeof data.has_api_key === "boolean"
        ? data.has_api_key
        : Boolean(data.api_key || data.apiKey);
    credentialsState = {
      apiUrl,
      hasApiKey,
      sources: {
        api_url: sources.api_url || sources.apiUrl || "default",
        api_key: sources.api_key || sources.apiKey || (hasApiKey ? "file" : "missing"),
      },
    };
    return credentialsState;
  } catch (error) {
    console.warn("Unable to load Daydream credentials", error);
    return credentialsState;
  }
}

async function persistEnvCredentials({ apiUrl, apiKey } = {}) {
  const payload = {};
  let hasChanges = false;
  if (typeof apiUrl === "string") {
    payload.api_url = normalizeApiBase(apiUrl);
    hasChanges = true;
  }
  if (typeof apiKey === "string") {
    payload.api_key = apiKey;
    hasChanges = true;
  }
  if (!hasChanges) {
    return credentialsState;
  }
  const response = await fetch(CREDENTIALS_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result?.success === false) {
    const message = result?.error || response.statusText || "Unable to persist credentials";
    throw new Error(message);
  }
  const data = result?.credentials || {};
  const sources = data.sources || {};
  const nextState = {
    apiUrl: normalizeApiBase(data.api_url || payload.api_url || credentialsState.apiUrl),
    hasApiKey:
      typeof data.has_api_key === "boolean"
        ? data.has_api_key
        : typeof payload.api_key === "string"
        ? Boolean(payload.api_key.trim())
        : credentialsState.hasApiKey,
    sources: {
      api_url: sources.api_url || sources.apiUrl || "file",
      api_key:
        sources.api_key ||
        sources.apiKey ||
        (typeof payload.api_key === "string"
          ? payload.api_key.trim()
            ? "file"
            : "missing"
          : credentialsState.sources?.api_key || "missing"),
    },
  };
  credentialsState = nextState;
  return credentialsState;
}

function setApiBaseSettingSilently(value) {
  suppressApiBaseEvent = true;
  const result = app.extensionManager.setting
    .set(API_BASE_SETTING_ID, value)
    ?.catch((err) => console.error("Failed to persist Daydream API URL setting", err));
  if (result && typeof result.finally === "function") {
    return result.finally(() => {
      suppressApiBaseEvent = false;
    });
  }
  suppressApiBaseEvent = false;
  return Promise.resolve();
}

function clearApiKeySettingField() {
  suppressApiKeyEvent = true;
  const result = app.extensionManager.setting
    .set(API_KEY_SETTING_ID, "")
    ?.catch((err) => console.error("Failed to clear Daydream API key field", err));
  if (result && typeof result.finally === "function") {
    return result.finally(() => {
      setTimeout(() => {
        suppressApiKeyEvent = false;
      }, 100);
    });
  }
  setTimeout(() => {
    suppressApiKeyEvent = false;
  }, 100);
  return Promise.resolve();
}

function ensureCredentialsHydrated() {
  fetchCredentialSnapshot()
    .then((state) => {
      const storedSetting = normalizeApiBase(
        app.extensionManager.setting.get(API_BASE_SETTING_ID) || DEFAULT_API_BASE
      );
      const envSource = state?.sources?.api_url || "default";
      if (
        envSource !== "file" &&
        storedSetting &&
        storedSetting !== DEFAULT_API_BASE &&
        storedSetting !== state.apiUrl
      ) {
        return persistEnvCredentials({ apiUrl: storedSetting })
          .then((nextState) => setApiBaseSettingSilently(nextState.apiUrl))
          .catch((error) => {
            console.warn("Unable to sync stored Daydream API URL to .env", error);
            return setApiBaseSettingSilently(state.apiUrl);
          });
      }
      return setApiBaseSettingSilently(state.apiUrl);
    })
    .catch((error) => {
      console.warn("Unable to hydrate Daydream credentials", error);
    });
}

function handleApiBaseSettingChange(value) {
  if (suppressApiBaseEvent) {
    return;
  }
  const normalized = normalizeApiBase(value);
  persistEnvCredentials({ apiUrl: normalized })
    .then(() =>
      toast("info", "Daydream Live", "Daydream API URL saved to rtc-node/.env")
    )
    .catch((error) =>
      toast("error", "Daydream Live", `Unable to save API URL: ${error.message}`)
    );
}

function handleApiKeySettingChange(value) {
  if (isExtensionInitializing || suppressApiKeyEvent) {
    return;
  }
  if (apiKeyDebounceTimer) {
    clearTimeout(apiKeyDebounceTimer);
  }
  apiKeyDebounceTimer = setTimeout(() => persistApiKeyValue(value), 600);
}

async function persistApiKeyValue(value) {
  apiKeyDebounceTimer = null;
  const trimmed = (value || "").trim();
  try {
    await persistEnvCredentials({ apiKey: trimmed });
    if (trimmed) {
      toast("success", "Daydream Live", "Daydream API key saved to rtc-node/.env");
    } else {
      toast("warn", "Daydream Live", "Daydream API key cleared");
    }
  } catch (error) {
    toast("error", "Daydream Live", `Unable to save API key: ${error.message}`);
  } finally {
    clearApiKeySettingField();
  }
}

function resolveLocalServerSetting() {
  const existing = app.extensionManager.setting.get(LOCAL_SERVER_SETTING_ID);
  if (existing) {
    return existing;
  }
  app.extensionManager.setting
    .set(LOCAL_SERVER_SETTING_ID, DEFAULT_SERVER_BASE)
    .catch((err) => console.error("Failed to persist default local server value", err));
  return DEFAULT_SERVER_BASE;
}

function resolveApiBaseSetting() {
  const existing = app.extensionManager.setting.get(API_BASE_SETTING_ID);
  if (existing) {
    return existing;
  }
  app.extensionManager.setting
    .set(API_BASE_SETTING_ID, DEFAULT_API_BASE)
    .catch((err) => console.error("Failed to persist default Daydream API URL", err));
  return DEFAULT_API_BASE;
}

function toast(severity, summary, detail) {
  app.extensionManager.toast.add({
    severity,
    summary,
    detail,
    life: 3500,
  });
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
      onChange: (value) => {
        // The sidebar reads this setting directly, so we don't need to do much here
        // other than ensure it's valid if we wanted to.
      },
    },
    {
      id: API_BASE_SETTING_ID,
      name: "Daydream API Base URL",
      type: "text",
      defaultValue: DEFAULT_API_BASE,
      category: ["Daydream Live", "API Credentials", "API Base URL"],
      tooltip: "Base REST endpoint for the Daydream Live API (synced to DAYDREAM_API_URL)",
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
      tooltip: "Stored securely in rtc-node/.env as DAYDREAM_API_KEY",
      attrs: {
        placeholder: "Paste API key (saved to .env)",
        autocomplete: "off",
        type: "password",
      },
      onChange: (value) => handleApiKeySettingChange(value),
    },
    {
      id: "Daydream Live.api_dashboard_link",
      name: "Manage API Keys",
      type: "text",
      defaultValue: "Open Daydream Dashboard",
      category: ["Daydream Live", "API Credentials", "Dashboard"],
      tooltip: "Log in to manage Daydream API keys",
      attrs: {
        type: "button",
        value: "Open Dashboard",
        onclick: `window.open('${DAYDREAM_DASHBOARD_URL}', '_blank'); return false;`,
        style: "cursor:pointer",
      },
    },
  ],
  setup() {
    // Ensure defaults are set if missing
    resolveLocalServerSetting();
    const initialApiBase = resolveApiBaseSetting();
    credentialsState.apiUrl = normalizeApiBase(initialApiBase);
    
    ensureCredentialsHydrated();

    // Force clear the API key field on startup to prevent loops/ghost values
    suppressApiKeyEvent = true;
    app.extensionManager.setting.set(API_KEY_SETTING_ID, "");
    setTimeout(() => {
      suppressApiKeyEvent = false;
    }, 100);

    initSettingsEnhancements();
    setTimeout(() => {
      isExtensionInitializing = false;
    }, 500);
  },
});

