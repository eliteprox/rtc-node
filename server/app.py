import argparse
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


LOGGER = logging.getLogger("rtc_stream.api")

runtime_config = load_runtime_config()
controller: Optional[StreamController] = None

router = APIRouter()


def _controller_running() -> bool:
    return bool(controller and controller.state.running)


def _apply_runtime_config_to_controller() -> None:
    if controller:
        controller.update_stream_settings(runtime_config)


class StartRequest(BaseModel):
    stream_name: str = ""
    pipeline_config: Optional[Dict[str, Any]] = None


class FramePayload(BaseModel):
    frame_b64: str


class RuntimeConfigPayload(BaseModel):
    frame_rate: int
    frame_width: int
    frame_height: int


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


def decode_frame(blob_b64: str) -> np.ndarray:
    from PIL import Image

    decoded = base64.b64decode(blob_b64)
    image = Image.open(io.BytesIO(decoded)).convert("RGB")
    return np.array(image)


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


def bootstrap_controller(api_url: str, api_key: str, pipeline_config: str, video_file: str = "") -> None:
    global controller
    controller = init_controller(api_url, api_key, pipeline_config, video_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DayDream Live local API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--pipeline-config", default="pipeline_config.json")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--video-file", default="")
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

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

