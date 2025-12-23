// Browser-side BYOC-SDK RTC implementation for ComfyUI.
//
// Responsibilities:
// - Poll `/rtc/frames/input` and paint frames to a canvas
// - Publish that canvas stream to WHIP using byoc-sdk HTTP helpers
// - View WHEP output using byoc-sdk StreamViewer
// - Sample output video frames and POST them to `/rtc/frames/output`
// - Report session metadata to `/rtc/session`

import { app } from "../../../scripts/app.js";

const SDK_VERSION = "1.1.1";
const SDK_BASE = `https://esm.sh/@muxionlabs/byoc-sdk@${SDK_VERSION}`;

const { StreamConfig, StreamViewer } = await import(`${SDK_BASE}`);
const { startStream, stopStream } = await import(`${SDK_BASE}/api/start`);
const { sendWhipOffer } = await import(`${SDK_BASE}/api/whip`);
const { sendStreamUpdate } = await import(`${SDK_BASE}/api/update`);

const DEFAULT_ICE_SERVERS = [
  { urls: "stun:stun.l.google.com:19302" },
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun1.l.google.com:19302" },
  { urls: "stun:stun2.l.google.com:19302" },
  { urls: "stun:stun3.l.google.com:19302" },
];

export const SETTINGS = {
  gatewayUrlId: "byoc.gateway_url",
  defaultPipelineId: "byoc.default_pipeline",
};

function getSetting(id, fallback) {
  try {
    return app?.extensionManager?.setting?.get(id) ?? fallback;
  } catch {
    return fallback;
  }
}

function normalizeBaseUrl(url) {
  const trimmed = (url || "").trim();
  return trimmed.replace(/\/+$/, "");
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
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
  return json;
}

async function postSession(patch) {
  try {
    await fetchJson("/rtc/session", { method: "POST", body: JSON.stringify(patch) });
  } catch {
    // best-effort
  }
}

function b64ToBlob(b64, mime = "image/png") {
  const bytes = atob(b64);
  const len = bytes.length;
  const arr = new Uint8Array(len);
  for (let i = 0; i < len; i++) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

async function waitForIceGathering(pc, timeoutMs = 3000) {
  if (pc.iceGatheringState === "complete") return;
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    pc.addEventListener("icecandidate", (ev) => {
      if (ev.candidate === null) {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

export class ComfyByocRtc {
  constructor() {
    this.running = false;
    this.pc = null;
    this.viewer = null;
    this.viewerVideoEl = null;
    this.canvas = null;
    this.canvasCtx = null;
    this.canvasStream = null;
    this.inputPollTimer = null;
    this.outputCaptureTimer = null;
    this.streamInfo = null;
    this.lastInputSeq = 0;
    this.lastOutputCaptureAt = 0;
  }

  async start({ outputVideoEl }) {
    if (this.running) return;
    this.running = true;
    this.viewerVideoEl = outputVideoEl || null;

    await postSession({ status: "connecting", running: false, error: "" });

    const gatewayUrl = normalizeBaseUrl(getSetting(SETTINGS.gatewayUrlId, "https://localhost:8088"));
    const defaultPipeline = (getSetting(SETTINGS.defaultPipelineId, "comfystream") || "comfystream").trim();

    const desiredResp = await fetchJson("/rtc/pipeline");
    const desired = desiredResp?.config || {};
    const pipeline = (desired.pipeline || defaultPipeline || "comfystream").trim();
    const width = Number(desired.width || 512);
    const height = Number(desired.height || 512);
    const fpsLimit = Number(desired.fps || 30);
    const streamName = (desired.stream_name || "comfyui-livestream").trim();
    const customParams = desired.pipeline_config?.params || {};

    const cfg = new StreamConfig({
      gatewayUrl,
      defaultPipeline: pipeline,
      iceServers: DEFAULT_ICE_SERVERS,
    });

    // 1) Start stream via gateway (control-plane)
    const startOptions = {
      streamName,
      pipeline,
      width,
      height,
      fpsLimit,
      enableVideoIngress: true,
      enableVideoEgress: true,
      enableAudioIngress: false,
      enableAudioEgress: false,
      enableDataOutput: true,
      customParams,
    };

    const info = await startStream(cfg.getStreamStartUrl(), startOptions);
    this.streamInfo = info;

    await postSession({
      status: "connecting",
      running: true,
      streamId: info.streamId,
      whipUrl: info.whipUrl,
      whepUrl: info.whepUrl,
      rtmpUrl: info.rtmpUrl,
      playbackUrl: info.playbackUrl,
      updateUrl: info.updateUrl,
      statusUrl: info.statusUrl,
      stopUrl: info.stopUrl,
      dataUrl: info.dataUrl,
    });

    // 2) Create canvas ingest from ComfyUI frames
    this.canvas = document.createElement("canvas");
    this.canvas.width = width;
    this.canvas.height = height;
    this.canvasCtx = this.canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.canvasStream = this.canvas.captureStream(fpsLimit);

    // 3) Publish to WHIP using byoc-sdk helper
    this.pc = new RTCPeerConnection({ iceServers: cfg.iceServers || DEFAULT_ICE_SERVERS });
    const [videoTrack] = this.canvasStream.getVideoTracks();
    if (videoTrack) {
      this.pc.addTrack(videoTrack, this.canvasStream);
    }

    const offer = await this.pc.createOffer({ offerToReceiveAudio: false, offerToReceiveVideo: false });
    await this.pc.setLocalDescription(offer);
    await waitForIceGathering(this.pc);

    const whipResp = await sendWhipOffer(info.whipUrl, this.pc.localDescription?.sdp || "");
    await this.pc.setRemoteDescription({ type: "answer", sdp: whipResp.answerSdp });

    // 4) Start WHEP viewer (output) if we have a video element
    if (this.viewerVideoEl) {
      this.viewer = new StreamViewer(cfg);
      this.viewer.setVideoElement(this.viewerVideoEl);
      await this.viewer.start({ whepUrl: info.whepUrl });
    }

    // 5) Start polling input frames + capturing output frames
    this._startInputPolling();
    this._startOutputCapture();

    await postSession({ status: "connected", running: true, error: "" });
  }

  async stop() {
    if (!this.running) return;
    this.running = false;

    const stopUrl = this.streamInfo?.stopUrl;

    try {
      if (this.inputPollTimer) clearInterval(this.inputPollTimer);
      this.inputPollTimer = null;
      if (this.outputCaptureTimer) clearInterval(this.outputCaptureTimer);
      this.outputCaptureTimer = null;

      if (this.viewer) {
        await this.viewer.stop();
      }
      this.viewer = null;

      if (this.pc) {
        try {
          this.pc.close();
        } catch {
          // ignore
        }
      }
      this.pc = null;

      if (this.canvasStream) {
        this.canvasStream.getTracks().forEach((t) => t.stop());
      }
      this.canvasStream = null;
      this.canvas = null;
      this.canvasCtx = null;

      if (stopUrl) {
        await stopStream(stopUrl);
      }
    } finally {
      this.streamInfo = null;
      await postSession({ status: "disconnected", running: false, error: "" });
    }
  }

  async updateFromDesiredConfig() {
    if (!this.streamInfo?.updateUrl || !this.streamInfo?.streamId) return;
    const desiredResp = await fetchJson("/rtc/pipeline");
    const desired = desiredResp?.config || {};
    const pipeline = (desired.pipeline || "").trim() || null;
    const params = desired.pipeline_config?.params || {};
    if (!pipeline) return;
    await sendStreamUpdate(this.streamInfo.updateUrl, this.streamInfo.streamId, pipeline, params);
  }

  _startInputPolling() {
    const intervalMs = 33;
    this.inputPollTimer = setInterval(async () => {
      if (!this.running || !this.canvasCtx) return;
      try {
        const payload = await fetchJson("/rtc/frames/input");
        if (!payload?.has_frame) return;
        const meta = payload?.metadata || {};
        const seq = Number(meta.sequence || 0);
        if (seq && seq === this.lastInputSeq) return;
        this.lastInputSeq = seq;

        const mime = meta.mime || "image/png";
        const blob = b64ToBlob(payload.frame_b64 || "", mime);
        const bmp = await createImageBitmap(blob);
        this.canvasCtx.drawImage(bmp, 0, 0, this.canvas.width, this.canvas.height);
        bmp.close?.();
      } catch {
        // ignore transient fetch/bitmap errors
      }
    }, intervalMs);
  }

  _startOutputCapture() {
    if (!this.viewerVideoEl) return;
    const targetFps = 10;
    const minDelta = 1000 / targetFps;

    const scratch = document.createElement("canvas");
    const ctx = scratch.getContext("2d", { alpha: false, desynchronized: true });
    this.outputCaptureTimer = setInterval(async () => {
      if (!this.running || !this.viewerVideoEl) return;
      const now = Date.now();
      if (now - this.lastOutputCaptureAt < minDelta) return;
      if (this.viewerVideoEl.readyState < 2) return;
      this.lastOutputCaptureAt = now;

      const w = this.viewerVideoEl.videoWidth || 0;
      const h = this.viewerVideoEl.videoHeight || 0;
      if (!w || !h) return;
      scratch.width = w;
      scratch.height = h;
      ctx.drawImage(this.viewerVideoEl, 0, 0, w, h);
      const dataUrl = scratch.toDataURL("image/png");
      const comma = dataUrl.indexOf(",");
      const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : "";
      if (!b64) return;
      await fetchJson("/rtc/frames/output", {
        method: "POST",
        body: JSON.stringify({ frame_b64: b64, mime: "image/png" }),
      });
    }, 50);
  }
}

