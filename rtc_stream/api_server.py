from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image

from livepeer_gateway.media_publish import MediaPublishConfig
from livepeer_gateway.orchestrator import (
    GetOrchestratorInfo,
    LivepeerGatewayError,
    StartJob,
    StartJobRequest,
)

from .frame_bridge import FRAME_BRIDGE
from .network_controller import NetworkController, NetworkControllerConfig
from .network_subscriber import NetworkSubscriber, NetworkSubscriberConfig
from .credentials import resolve_network_config


app = FastAPI(title="comfyui-rtc network API (trickle)")


@dataclass
class Runtime:
    controller: Optional[NetworkController] = None
    subscriber: Optional[NetworkSubscriber] = None
    loop: Optional[asyncio.AbstractEventLoop] = None


runtime = Runtime()
runtime_lock = asyncio.Lock()


class StartRequest(BaseModel):
    orchestrator_url: str = "https://localhost:8935"
    signer_url: Optional[str] = None
    model_id: str = "comfystream"
    fps: float = 30.0
    width: int = 512
    height: int = 512
    keyframe_interval_s: float = 2.0
    params: Optional[dict] = None
    request_id: Optional[str] = None
    stream_id: Optional[str] = None
    start_seq: int = -2


class FramePayload(BaseModel):
    frame_b64: str


def _decode_png_to_numpy(frame_b64: str) -> np.ndarray:
    decoded = base64.b64decode(frame_b64)
    image = Image.open(io.BytesIO(decoded)).convert("RGB")
    return np.array(image)


def _ensure_runtime_loop() -> asyncio.AbstractEventLoop:
    if runtime.loop:
        return runtime.loop
    loop = asyncio.new_event_loop()
    runtime.loop = loop
    asyncio.get_event_loop_policy().set_event_loop(loop)
    return loop


@app.post("/start")
async def start_stream(req: StartRequest):
    async with runtime_lock:
        loop = _ensure_runtime_loop()
        resolved_orch, resolved_signer = resolve_network_config(req.orchestrator_url, req.signer_url)
        config = NetworkControllerConfig(
            orchestrator_url=resolved_orch,
            signer_url=resolved_signer or None,
            model_id=req.model_id,
            fps=float(req.fps),
            frame_width=req.width,
            frame_height=req.height,
            keyframe_interval_s=req.keyframe_interval_s,
        )
        controller = runtime.controller or NetworkController(config)
        controller.update_config(config)
        runtime.controller = controller

        try:
            status = controller.start(
                model_id=req.model_id,
                params=req.params or {},
                request_id=req.request_id,
                stream_id=req.stream_id,
            )
        except LivepeerGatewayError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Start subscriber if we have a subscribe_url
        subscribe_url = status.get("subscribe_url")
        if subscribe_url:
            subscriber = runtime.subscriber or NetworkSubscriber(NetworkSubscriberConfig(start_seq=req.start_seq))
            subscriber.attach_loop(controller.loop)
            subscriber.config.start_seq = req.start_seq
            subscriber.start(subscribe_url)
            runtime.subscriber = subscriber

        return status


@app.post("/frames")
async def push_frame(payload: FramePayload):
    if not runtime.controller or not runtime.controller.running:
        raise HTTPException(status_code=409, detail="No active stream; call /start first.")
    frame = _decode_png_to_numpy(payload.frame_b64)
    FRAME_BRIDGE.enqueue(frame)
    return {"accepted": True, "queue_depth": FRAME_BRIDGE.depth()}


@app.post("/stop")
async def stop_stream():
    if not runtime.controller:
        return {"stopped": True}
    runtime.controller.stop()
    return {"stopped": True}


@app.get("/status")
async def status():
    if not runtime.controller:
        return {"running": False}
    return runtime.controller.status()


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn
    uvicorn.run(
        "rtc_stream.api_server:app",
        host="127.0.0.1",
        port=8895,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
