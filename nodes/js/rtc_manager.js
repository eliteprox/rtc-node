
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ByocClient } from "./byoc-sdk.js";

class RTCManager {
    constructor() {
        this.client = null;
        this.canvas = document.createElement("canvas");
        this.ctx = this.canvas.getContext("2d");
        this.stream = null;
        this.lastFrameTime = 0;
        this.isStreamActive = false;
        
        // Status state
        this.status = {
            running: false,
            stream_id: "",
            whip_url: "",
            frames_sent: 0
        };

        this.setupEventListeners();
        console.log("RTCManager initialized");
    }

    setupEventListeners() {
        // Listen for Python commands
        api.addEventListener("rtc-command", (event) => {
            const data = event.detail;
            if (data.action === "start") {
                this.startStream(data.config, data.credentials);
            } else if (data.action === "stop") {
                this.stopStream();
            } else if (data.action === "update") {
                this.updateConfig(data.pipeline_config);
            }
        });

        // Listen for frames
        api.addEventListener("rtc-frame", (event) => {
            const data = event.detail;
            this.handleFrame(data);
        });
    }

    subscribe(callback) {
        if (!this.subscribers) this.subscribers = new Set();
        this.subscribers.add(callback);
        return () => this.subscribers.delete(callback);
    }

    notifySubscribers(event) {
        if (this.subscribers) {
            this.subscribers.forEach(cb => cb(event));
        }
    }

    async startStream(config, credentials) {
        if (this.isStreamActive) {
            console.log("Stream already active, stopping first...");
            await this.stopStream();
        }

        console.log("RTCManager starting stream", config);
        
        // Initialize SDK
        this.client = new ByocClient({
            url: credentials.api_url || "https://api.daydream.live",
            apiKey: credentials.api_key
        });

        // Setup canvas size
        this.canvas.width = config.frame_width || 1280;
        this.canvas.height = config.frame_height || 720;

        // Get stream from canvas
        this.stream = this.canvas.captureStream(config.frame_rate || 30);
        
        this.client.on("connect", (data) => {
            console.log("RTC Connected", data);
            this.status.running = true;
            this.status.stream_id = data.streamId;
            this.status.whip_url = data.whipUrl;
            this.syncStatus();
            this.notifySubscribers({ type: "started", stream: this.stream });
        });

        this.client.on("disconnect", () => {
            console.log("RTC Disconnected");
            this.status.running = false;
            this.syncStatus();
            this.notifySubscribers({ type: "stopped" });
        });

        // Start SDK
        await this.client.start({
            streamId: config.stream_id,
            ...config
        });
        
        this.client.publish(this.stream);
        this.isStreamActive = true;
    }

    async stopStream() {
        if (this.client) {
            await this.client.stop();
            this.client = null;
        }
        this.isStreamActive = false;
        this.status.running = false;
        this.syncStatus();
    }

    updateConfig(pipelineConfig) {
        console.log("Updating pipeline config", pipelineConfig);
        // SDK might support updating metadata or config
    }

    handleFrame(data) {
        if (!data.frame) return;

        const img = new Image();
        img.onload = () => {
            this.ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height);
            this.status.frames_sent++;
            
            // If we have an active stream, the canvas stream is already attached.
            // But if we have an internal viewer (preview node), we might want to emit an event.
            // Or the preview node can just attach to this.canvas or this.stream.
            
            // Periodically sync status (e.g., every 30 frames)
            if (this.status.frames_sent % 30 === 0) {
                this.syncStatus();
            }
        };
        img.src = "data:image/jpeg;base64," + data.frame;
    }

    async syncStatus() {
        try {
            await fetch("/extensions/rtc/status", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(this.status)
            });
        } catch (e) {
            console.error("Failed to sync status", e);
        }
    }
}

const rtcManager = new RTCManager();
export { rtcManager };

// Register extension to ensure loading
app.registerExtension({
    name: "RTC.Manager",
    setup() {
        // Already initialized globally
    }
});
