
import logging
from aiohttp import web
import json
import base64
import io
import torch
import numpy as np
from PIL import Image

try:
    from server import PromptServer
except ImportError:
    PromptServer = None

from .shared_state import FRAME_BUFFER, FRAME_LOCK, STATUS_BUFFER, STATUS_LOCK

LOGGER = logging.getLogger("rtc_stream.routes")

def _b64_to_tensor(frame_b64: str):
    if not frame_b64:
        return None
    try:
        decoded = base64.b64decode(frame_b64)
        image = Image.open(io.BytesIO(decoded)).convert("RGB")
        np_frame = np.asarray(image, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(np_frame).unsqueeze(0)
        return tensor
    except Exception as exc:
        LOGGER.error("Failed to decode frame payload: %s", exc)
        return None

async def receive_frame(request):
    try:
        data = await request.json()
        frame_b64 = data.get("frame_b64")
        if frame_b64:
            tensor = _b64_to_tensor(frame_b64)
            if tensor is not None:
                with FRAME_LOCK:
                    FRAME_BUFFER["latest"] = tensor
        return web.json_response({"success": True})
    except Exception as e:
        LOGGER.error(f"Error receiving frame: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def receive_status(request):
    try:
        data = await request.json()
        with STATUS_LOCK:
            STATUS_BUFFER.update(data)
        return web.json_response({"success": True})
    except Exception as e:
        LOGGER.error(f"Error receiving status: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

def register_routes():
    if PromptServer is None:
        LOGGER.warning("PromptServer not available, skipping route registration")
        return

    app = PromptServer.instance.app
    routes = [
        web.post("/extensions/rtc/frame_buffer", receive_frame),
        web.post("/extensions/rtc/status", receive_status),
    ]
    app.add_routes(routes)
    LOGGER.info("RTC Stream routes registered")
