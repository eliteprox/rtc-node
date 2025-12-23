
import logging
import time
import base64
import io
import uuid
import json
from typing import Any, Dict, Optional

import numpy as np
import torch
from PIL import Image

try:
    from server import PromptServer
except ImportError:
    PromptServer = None

from .shared_state import FRAME_BUFFER, FRAME_LOCK, STATUS_BUFFER, STATUS_LOCK
from .pipeline_config import hash_pipeline_config
from .credentials_store import load_credentials_from_env

LOGGER = logging.getLogger("rtc_stream.nodes")

def tensor_to_b64(tensor: torch.Tensor) -> Optional[str]:
    try:
        # Expecting (B, H, W, C)
        if len(tensor.shape) == 4:
            tensor = tensor[0]
        
        # Convert to numpy (H, W, C)
        np_frame = (tensor.cpu().numpy() * 255.0).astype(np.uint8)
        image = Image.fromarray(np_frame)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:
        LOGGER.error("Failed to encode frame: %s", exc)
        return None

class RTCStreamFrameInput:
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "optional": {
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    OUTPUT_NODE = True
    FUNCTION = "push_frame"
    CATEGORY = "RTC Stream"

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        return True

    @staticmethod
    def push_frame(image: torch.Tensor, enabled: bool = True):
        if enabled and PromptServer is not None:
            b64_frame = tensor_to_b64(image)
            if b64_frame:
                PromptServer.instance.send_sync(
                    "rtc-frame",
                    {"frame": b64_frame, "timestamp": time.time()}
                )
        return ()


class RTCStreamFrameOutput:
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "whep_url": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "whep_url")
    FUNCTION = "pull_frame"
    CATEGORY = "RTC Stream"
    OUTPUT_NODE = True
    OUTPUT_IS_LIST = (False, False)

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        return True

    def _blank_tensor(self, width: int = 1280, height: int = 720) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)

    def pull_frame(self, whep_url: str):
        # Poll for frame from shared buffer
        tensor = None
        with FRAME_LOCK:
            tensor = FRAME_BUFFER.get("latest")
        
        if tensor is None:
            return (self._blank_tensor(), whep_url)
        
        return (tensor, whep_url)


class RTCStreamStatus:
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "optional": {
                "stream_id": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("running", "stream_id", "playback_id", "whep_url", "frames_sent", "queue_depth", "status_json")
    FUNCTION = "get_status"
    CATEGORY = "RTC Stream"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def get_status(self, stream_id: str = ""):
        with STATUS_LOCK:
            status = STATUS_BUFFER.copy()
        
        running = status.get("running", False)
        stream_id_out = status.get("stream_id", "")
        playback_id = status.get("playback_id", "")
        whep_url = status.get("whip_url", "")
        frames_sent = status.get("frames_sent", 0)
        queue_depth_val = status.get("queue_depth", 0)
        status_json = json.dumps(status, indent=2)

        return (running, stream_id_out, playback_id, whep_url, frames_sent, queue_depth_val, status_json)


class StartRTCStream:
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "pipeline_config": ("PIPELINE_CONFIG",),
            },
            "optional": {
                "stream_name": ("STRING", {"default": "comfyui-livestream"}),
                "fps": ("INT", {"default": 30, "min": 1, "max": 120}),
                "width": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "height": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "enabled": ("BOOLEAN", {"default": True}),
                "stop_stream": ("BOOLEAN", {"default": False}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("stream_id", "playback_id", "whip_url")
    FUNCTION = "start_stream"
    CATEGORY = "RTC Stream"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, pipeline_config, stream_name="", fps=30, width=512, height=512, enabled=True, stop_stream=False, **kwargs):
        digest = hash_pipeline_config(pipeline_config)
        return f"{digest}:{stream_name}:{fps}:{width}:{height}:{int(bool(enabled))}:{int(bool(stop_stream))}"

    def start_stream(
        self,
        pipeline_config: Dict[str, Any],
        stream_name: str = "",
        fps: int = 30,
        width: int = 512,
        height: int = 512,
        enabled: bool = True,
        stop_stream: bool = False,
        unique_id=None,
        extra_pnginfo=None,
    ):
        if not enabled and not stop_stream:
            return ("", "", "")

        if stop_stream:
            if PromptServer:
                PromptServer.instance.send_sync("rtc-command", {"action": "stop"})
            return ("", "", "")

        # Generate a tentative stream ID to track this session
        stream_id = str(uuid.uuid4())
        
        # Load credentials
        creds = load_credentials_from_env()
        
        # Send start command to JS
        if PromptServer:
            PromptServer.instance.send_sync("rtc-command", {
                "action": "start",
                "config": {
                    "stream_name": stream_name,
                    "pipeline_config": pipeline_config,
                    "frame_rate": fps,
                    "frame_width": width,
                    "frame_height": height,
                    "stream_id": stream_id
                },
                "credentials": {
                    "api_url": creds.get("api_url"),
                    "api_key": creds.get("api_key")
                }
            })

        # We return the stream_id so other nodes can link, 
        # but the actual WHIP URL might not be ready yet.
        # Downstream nodes should handle empty/loading states via Status node.
        return (stream_id, "", "")


class UpdateRTCStream:
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "pipeline_config": ("PIPELINE_CONFIG",),
            },
            "optional": {
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "update_stream"
    CATEGORY = "RTC Stream"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, pipeline_config, enabled=True, **kwargs):
        if not enabled:
            return "update-disabled"
        return hash_pipeline_config(pipeline_config)

    def update_stream(self, pipeline_config: Dict[str, Any], enabled: bool = True):
        if enabled and PromptServer:
             PromptServer.instance.send_sync("rtc-command", {
                "action": "update",
                "pipeline_config": pipeline_config
            })
        return ()


NODE_CLASS_MAPPINGS = {
    "RTCStreamFrameInput": RTCStreamFrameInput,
    "RTCStreamFrameOutput": RTCStreamFrameOutput,
    "StartRTCStream": StartRTCStream,
    "UpdateRTCStream": UpdateRTCStream,
    "RTCStreamStatus": RTCStreamStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamFrameInput": "RTC Stream Frame Input",
    "RTCStreamFrameOutput": "RTC Stream Frame Output",
    "StartRTCStream": "Start RTC Stream",
    "UpdateRTCStream": "Update RTC Stream",
    "RTCStreamStatus": "RTC Stream Status",
}
