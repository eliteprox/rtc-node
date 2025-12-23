import { app } from "../../../scripts/app.js";

const GATEWAY_URL_SETTING_ID = "byoc.gateway_url";
const DEFAULT_PIPELINE_SETTING_ID = "byoc.default_pipeline";
const DEFAULT_GATEWAY_URL = "https://localhost:8088";
const DEFAULT_PIPELINE = "comfystream";

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

function handleGatewaySettingChange(value) {
  if (isExtensionInitializing) {
    return;
  }
  const trimmed = (value || "").trim();
  if (!trimmed) {
    toast("warn", "BYOC Streaming", "Gateway URL cleared. Streaming will fail until set.");
    return;
  }
  toast("info", "BYOC Streaming", "Gateway URL updated.");
}

app.registerExtension({
  name: "BYOC Streaming Settings",
  settings: [
    {
      id: GATEWAY_URL_SETTING_ID,
      name: "Gateway URL",
      type: "text",
      defaultValue: DEFAULT_GATEWAY_URL,
      category: ["BYOC Streaming", "Gateway", "URL"],
      tooltip: "BYOC gateway base URL (e.g. https://localhost:8088)",
      attrs: {
        placeholder: DEFAULT_GATEWAY_URL,
      },
      onChange: (value) => handleGatewaySettingChange(value),
    },
    {
      id: DEFAULT_PIPELINE_SETTING_ID,
      name: "Default Pipeline",
      type: "text",
      defaultValue: DEFAULT_PIPELINE,
      category: ["BYOC Streaming", "Gateway", "Pipeline"],
      tooltip: "Default pipeline name to request from the gateway",
      attrs: {
        placeholder: DEFAULT_PIPELINE,
      },
    },
  ],
  setup() {
    ensureSettingDefault(GATEWAY_URL_SETTING_ID, DEFAULT_GATEWAY_URL);
    ensureSettingDefault(DEFAULT_PIPELINE_SETTING_ID, DEFAULT_PIPELINE);

    setTimeout(() => {
      isExtensionInitializing = false;
    }, 300);
  },
});

