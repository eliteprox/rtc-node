import argparse
import base64
import io
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rtc_stream.controller import ControllerConfig, StreamController
from rtc_stream.frame_bridge import FRAME_BRIDGE


LOGGER = logging.getLogger("rtc_stream.server")

controller: Optional[StreamController] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    LOGGER.info("RTC stream server startup")
    if controller is None:
        LOGGER.error("Controller not initialized")
        raise RuntimeError("Controller missing")
    try:
        yield
    finally:
        if controller:
            try:
                await controller.stop()
            except Exception as exc:  # pragma: no cover - shutdown path
                LOGGER.error("Error while stopping controller: %s", exc)
        LOGGER.info("RTC stream server shutdown complete")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    stream_name: str = ""
    pipeline_config: Optional[Dict[str, Any]] = None


class FramePayload(BaseModel):
    frame_b64: str


@app.get("/healthz")
async def healthz():
    return {"ok": True, "frame_loop_ready": FRAME_BRIDGE.loop is not None}


@app.post("/start")
async def start_stream(req: StartRequest):
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    status = await controller.start(
        stream_name=req.stream_name,
        pipeline_override=req.pipeline_config,
    )
    return status


@app.post("/stop")
async def stop_stream():
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    status = await controller.stop()
    return status


@app.get("/status")
async def get_status():
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    status = await controller.status_async(refresh_remote=True)
    return status


@app.post("/frames")
async def push_frame(payload: FramePayload):
    if controller is None:
        raise HTTPException(status_code=500, detail="Controller unavailable")
    frame = decode_frame(payload.frame_b64)
    controller.enqueue_frame(frame)
    return JSONResponse({"accepted": True})


def decode_frame(blob_b64: str) -> np.ndarray:
    from PIL import Image

    decoded = base64.b64decode(blob_b64)
    image = Image.open(io.BytesIO(decoded)).convert("RGB")
    return np.array(image)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RTC Node streaming server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8890)
    parser.add_argument("--pipeline-config", default="pipeline_config.json")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--video-file", default="")
    parser.add_argument("--log-level", default="info")
    return parser.parse_args()


def main():
    global controller
    args = parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")

    pipeline_path = Path(args.pipeline_config).resolve()
    if not pipeline_path.exists():
        raise FileNotFoundError(f"Pipeline config not found at {pipeline_path}")
    video_path = Path(args.video_file).resolve() if args.video_file else None
    config = ControllerConfig(
        api_url=args.api_url,
        api_key=args.api_key,
        pipeline_path=pipeline_path,
        video_file=video_path,
    )
    controller = StreamController(config)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

