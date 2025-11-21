import argparse
import asyncio
import base64
import io
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import JSONResponse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rtc_stream.config_store import load_runtime_config, save_runtime_config
from rtc_stream.controller import ControllerConfig, StreamController
from rtc_stream.frame_bridge import FRAME_BRIDGE
from rtc_stream.whep_controller import WhepController, WhepControllerConfig
from rtc_stream.whep_frame_bridge import WHEP_FRAME_BRIDGE


LOGGER = logging.getLogger("rtc_stream.api")

runtime_config = load_runtime_config()
controller: Optional[StreamController] = None
whep_controller: Optional[WhepController] = None

router = APIRouter()


def _controller_running() -> bool:
    return bool(controller and controller.state.running)


def _apply_runtime_config_to_controller() -> None:
    if controller:
        controller.update_stream_settings(runtime_config)


class StartRequest(BaseModel):
    stream_name: str = ""
    pipeline_config: Optional[Dict[str, Any]] = None
    frame_rate: Optional[int] = None
    frame_width: Optional[int] = None
    frame_height: Optional[int] = None


class FramePayload(BaseModel):
    frame_b64: str


class RuntimeConfigPayload(BaseModel):
    frame_rate: int
    frame_width: int
    frame_height: int


class WhepConnectPayload(BaseModel):
    whep_url: str


class PipelineCachePayload(BaseModel):
    pipeline_config: Dict[str, Any]


class PipelineUpdatePayload(BaseModel):
    pipeline_config: Dict[str, Any]


@router.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "frame_loop_ready": FRAME_BRIDGE.loop is not None,
        "queue_depth": FRAME_BRIDGE.depth(),
    }


@router.post("/start")
async def start_stream(req: StartRequest):
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    _apply_runtime_config_to_controller()
    
    # Apply stream settings if provided
    settings = {}
    if req.frame_rate is not None:
        settings["frame_rate"] = req.frame_rate
    if req.frame_width is not None:
        settings["frame_width"] = req.frame_width
    if req.frame_height is not None:
        settings["frame_height"] = req.frame_height
    if settings:
        controller.update_stream_settings(settings)
    
    status = await controller.start(
        stream_name=req.stream_name,
        pipeline_override=req.pipeline_config,
    )
    return status


@router.post("/stop")
async def stop_stream():
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    status = await controller.stop()
    return status


@router.get("/status")
async def get_status():
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    status = await controller.status_async(refresh_remote=True)
    return status


@router.post("/frames")
async def push_frame(payload: FramePayload):
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    frame = decode_frame(payload.frame_b64)
    controller.enqueue_frame(frame)
    LOGGER.info("HTTP /frames accepted frame (depth=%s)", FRAME_BRIDGE.depth())
    return JSONResponse({"accepted": True, "queue_depth": FRAME_BRIDGE.depth()})


@router.get("/config")
async def get_runtime_config():
    locked = _controller_running()
    return {
        "frame_rate": runtime_config["frame_rate"],
        "frame_width": runtime_config["frame_width"],
        "frame_height": runtime_config["frame_height"],
        "locked": locked,
    }


@router.post("/config")
async def update_runtime_config(payload: RuntimeConfigPayload):
    if _controller_running():
        raise HTTPException(status_code=409, detail="Stream is running; stop before changing settings")
    normalized = normalize_runtime_config(payload)
    runtime_config.update(normalized)
    save_runtime_config(runtime_config)
    _apply_runtime_config_to_controller()
    return {**runtime_config, "locked": False}


@router.post("/pipeline/cache")
async def cache_pipeline_config(payload: PipelineCachePayload):
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    loop = asyncio.get_running_loop()
    normalized = await loop.run_in_executor(
        None,
        controller.cache_pipeline_config,
        payload.pipeline_config,
    )
    return {
        "cached": True,
        "pipeline": normalized.get("pipeline", ""),
        "path": str(controller.config.pipeline_path),
    }


@router.patch("/pipeline")
async def update_pipeline(payload: PipelineUpdatePayload):
    """
    Update pipeline configuration for the running stream.
    Forwards the update to Daydream API without restarting the stream.
    """
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")

    if not controller.state.running:
        raise HTTPException(status_code=409, detail="No active stream to update")

    try:
        result = await controller.update_pipeline(payload.pipeline_config)
        return {
            "updated": True,
            "stream_id": controller.state.info.stream_id if controller.state.info else "",
            "pipeline": result.get("pipeline", ""),
            "params": result.get("params", {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/whep/status")
async def get_whep_status():
    if whep_controller is None:
        raise HTTPException(status_code=500, detail="WHEP controller unavailable")
    status = whep_controller.status()
    bridge_stats = await WHEP_FRAME_BRIDGE.stats()
    return {**status, "bridge_stats": bridge_stats}


@router.post("/whep/connect")
async def connect_whep(payload: WhepConnectPayload):
    if whep_controller is None:
        raise HTTPException(status_code=500, detail="WHEP controller unavailable")
    status = await whep_controller.connect(payload.whep_url)
    return status


@router.post("/whep/disconnect")
async def disconnect_whep():
    if whep_controller is None:
        raise HTTPException(status_code=500, detail="WHEP controller unavailable")
    status = await whep_controller.disconnect()
    return status


@router.get("/whep/frame")
async def fetch_whep_frame():
    if whep_controller is None:
        raise HTTPException(status_code=500, detail="WHEP controller unavailable")
    frame, metadata, has_frame = await WHEP_FRAME_BRIDGE.get_latest_frame_or_blank()
    encoded = encode_frame(frame)
    return {"frame_b64": encoded, "has_frame": has_frame, "metadata": metadata, "status": whep_controller.status()}


def decode_frame(blob_b64: str) -> np.ndarray:
    from PIL import Image

    decoded = base64.b64decode(blob_b64)
    image = Image.open(io.BytesIO(decoded)).convert("RGB")
    return np.array(image)


def encode_frame(frame: np.ndarray) -> str:
    from PIL import Image

    with io.BytesIO() as buffer:
        image = Image.fromarray(frame.astype(np.uint8))
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")


def normalize_runtime_config(payload: RuntimeConfigPayload) -> Dict[str, int]:
    def _norm(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(int(value), maximum))

    return {
        "frame_rate": _norm(payload.frame_rate, 1, 240),
        "frame_width": _norm(payload.frame_width, 64, 4096),
        "frame_height": _norm(payload.frame_height, 64, 4096),
    }


def init_controller(api_url: str, api_key: str, pipeline_config: str, video_file: str = "") -> StreamController:
    pipeline_path = Path(pipeline_config).resolve()
    if not pipeline_path.exists():
        raise FileNotFoundError(f"Pipeline config not found at {pipeline_path}")
    video_path = Path(video_file).resolve() if video_file else None
    config = ControllerConfig(
        api_url=api_url,
        api_key=api_key,
        pipeline_path=pipeline_path,
        video_file=video_path,
        frame_rate=runtime_config["frame_rate"],
        frame_width=runtime_config["frame_width"],
        frame_height=runtime_config["frame_height"],
    )
    return StreamController(config)


def init_whep_controller() -> WhepController:
    config = WhepControllerConfig(
        frame_width=runtime_config["frame_width"],
        frame_height=runtime_config["frame_height"],
    )
    return WhepController(config)


def bootstrap_controller(api_url: str, api_key: str, pipeline_config: str, video_file: str = "") -> None:
    global controller, whep_controller
    controller = init_controller(api_url, api_key, pipeline_config, video_file)
    whep_controller = init_whep_controller()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DayDream Live local API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--pipeline-config", default="pipeline_config.json")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--video-file", default="")
    parser.add_argument(
        "--whep-url",
        default="",
        help="Optional WHEP endpoint to subscribe to immediately after startup",
    )
    parser.add_argument("--log-level", default="info")
    return parser.parse_args()


def main():
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    args = parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")

    bootstrap_controller(args.api_url, args.api_key, args.pipeline_config, args.video_file)

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    initial_whep_url = args.whep_url.strip()

    if initial_whep_url:
        @app.on_event("startup")
        async def auto_start_whep() -> None:
            if whep_controller is None:
                LOGGER.error("WHEP controller unavailable; cannot auto-connect to %s", initial_whep_url)
                return
            try:
                await whep_controller.connect(initial_whep_url)
                LOGGER.info("Auto-connected WHEP subscriber to %s", initial_whep_url)
            except Exception as exc:  # pragma: no cover - network interactions
                LOGGER.error("Failed to auto-connect WHEP subscriber: %s", exc)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

