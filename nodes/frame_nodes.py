import base64
import io
import logging
from typing import Any, Dict, Optional

import numpy as np
try:
    import torch  # type: ignore
except ImportError:  # pragma: no cover - provided by ComfyUI runtime
    torch = None  # type: ignore
try:
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover - dependency provided at runtime
    Image = None  # type: ignore

from rtc_stream.state_store import RTC_STATE
from .pipeline_config import hash_pipeline_config

PromptServer = None
try:  # pragma: no cover - PromptServer might not be available outside tests
    from server import PromptServer
except ImportError:
    pass


LOGGER = logging.getLogger("rtc_stream.nodes")


class RTCStreamFrameInput:
    """
    ComfyUI output node that makes the latest IMAGE available to the browser-side publisher.
    """

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
    def push_frame(image, enabled: bool = True):
        if enabled:
            if torch is None:
                LOGGER.error("torch is not available; cannot push frame")
                return ()
            frame_b64 = _tensor_to_png_b64(image)
            if frame_b64:
                RTC_STATE.set_input_frame(frame_b64)
        return ()


class RTCStreamFrameOutput:
    """
    ComfyUI node that retrieves the latest browser-captured output frame.
    """

    def __init__(self):
        pass

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

    def pull_frame(self, whep_url: str):
        if torch is None:
            LOGGER.error("torch is not available; cannot decode frame to IMAGE")
            return (None, whep_url)
        frame_b64, _meta, has_frame = RTC_STATE.get_output_frame()
        session = RTC_STATE.get_session()
        effective_whep = session.get("whep_url") or whep_url
        if not has_frame:
            return (self._blank_tensor(), effective_whep)
        tensor = self._b64_to_tensor(frame_b64)
        if tensor is None:
            return (self._blank_tensor(), effective_whep)
        return (tensor, effective_whep)

    @staticmethod
    def _b64_to_tensor(frame_b64: str):
        if not frame_b64:
            return None
        try:
            decoded = base64.b64decode(frame_b64)
            image = Image.open(io.BytesIO(decoded)).convert("RGB")
            np_frame = np.asarray(image, dtype=np.float32) / 255.0
            tensor = torch.from_numpy(np_frame).unsqueeze(0)
            return tensor
        except Exception as exc:  # pragma: no cover - image decoding
            LOGGER.error("Failed to decode frame payload: %s", exc)
            return None

    @staticmethod
    def _blank_tensor(width: int = 1280, height: int = 720):
        if torch is None:
            return None
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


class RTCStreamStatus:
    """
    ComfyUI node that retrieves stream status from the local API server.
    Reads fast in-memory state updated by background polling.
    """

    def __init__(self):
        pass

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
        """
        Always execute - no caching.
        The local /status endpoint is fast since it reads in-memory state.
        """
        return float("nan")

    def get_status(self, stream_id: str = ""):
        """
        Retrieve stream status from the local API server.
        No caching - queries live state every execution.
        
        Args:
            stream_id: Optional input to create workflow dependency (not used in query)
        """
        import json

        session = RTC_STATE.get_session()
        running = bool(session.get("running"))
        stream_id_out = session.get("stream_id") or ""
        playback_id = session.get("playback_url") or ""
        whep_url = session.get("whep_url") or ""
        _in_b64, in_meta, _ = RTC_STATE.get_input_frame()
        _out_b64, out_meta, _ = RTC_STATE.get_output_frame()
        status = {
            "running": running,
            "stream_id": stream_id_out,
            "playback_id": playback_id,
            "whip_url": session.get("whip_url") or "",
            "whep_url": whep_url,
            "frame_in": in_meta,
            "frame_out": out_meta,
            "session": session,
            "desired": RTC_STATE.get_desired_config(),
        }

        frames_sent = int(in_meta.get("sequence", 0))
        queue_depth_val = 0
        status_json = json.dumps(status, indent=2)
        return (running, stream_id_out, playback_id, whep_url, frames_sent, queue_depth_val, status_json)

    def _empty_status(self):
        """Return empty status values."""
        return (False, "", "", "", 0, 0, "{}")

    @staticmethod
    def _extract_whep_url(status: Dict[str, Any]) -> str:
        """
        Attempt to extract a WHEP URL from the status payload or nested remote status.
        """
        direct = status.get("whep_url")
        if isinstance(direct, str) and direct:
            return direct

        remote_status = status.get("remote_status")
        if isinstance(remote_status, dict):
            remote_body = remote_status.get("body")
            parsed = RTCStreamStatus._parse_gateway_whep(remote_body)
            if parsed:
                return parsed

        parsed = RTCStreamStatus._parse_gateway_whep(status)
        if parsed:
            return parsed

        legacy = status.get("whip_url")
        if isinstance(legacy, str):
            return legacy
        return ""

    @staticmethod
    def _parse_gateway_whep(payload: Any) -> str:
        """
        Parse Daydream gateway responses shaped like:
        {
            "success": true,
            "error": null,
            "data": {
                "gateway_status": {
                    "whep_url": "https://...."
                }
            }
        }
        """
        if not isinstance(payload, dict):
            return ""

        success = payload.get("success")
        error = payload.get("error")
        if success is True and error is None:
            data = payload.get("data") or {}
            if isinstance(data, dict):
                gateway_status = data.get("gateway_status") or {}
                if isinstance(gateway_status, dict):
                    whep_url = gateway_status.get("whep_url")
                    if isinstance(whep_url, str):
                        return whep_url

        data = payload.get("data")
        if isinstance(data, dict):
            whep_url = data.get("whep_url")
            if isinstance(whep_url, str):
                return whep_url
        return ""


class UpdateRTCStream:
    """
    ComfyUI node that updates pipeline parameters for a running stream.
    Uses ComfyUI caching to only execute when pipeline_config changes.
    """

    def __init__(self):
        pass

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
        """
        Return a hash of the pipeline config.
        ComfyUI will only re-execute if this hash changes.
        """
        if not enabled:
            return "update-disabled"
        return hash_pipeline_config(pipeline_config)

    def update_stream(self, pipeline_config: Dict[str, Any], enabled: bool = True):
        """
        Update the pipeline configuration for a running stream.
        Sends a PATCH request to the local API server.
        """
        if not enabled:
            LOGGER.debug("UpdateRTCStream disabled; skipping update")
            return ()

        desired = RTC_STATE.set_desired_config(pipeline_config=pipeline_config)
        self._send_notification(
            "info",
            "Pipeline Updated (pending)",
            f"Browser will apply next update (pipeline={desired.get('pipeline','')})",
        )
        return ()

    def _send_notification(self, severity: str, summary: str, detail: str):
        """Send a notification to the ComfyUI frontend."""
        try:
            if PromptServer is None:
                return

            server = PromptServer.instance
            if server:
                server.send_sync(
                    "rtc-stream-notification",
                    {
                        "severity": severity,
                        "summary": summary,
                        "detail": detail,
                    },
                )
                LOGGER.debug("Sent notification: %s - %s", summary, detail)
        except Exception as exc:
            LOGGER.debug("Failed to send notification: %s", exc)


class StartRTCStream:
    """
    ComfyUI node that starts a stream with the given pipeline configuration.
    Uses caching to avoid recreating streams on subsequent workflow runs.
    """

    def __init__(self):
        self._cache_key = None
        self._cached_result = None

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "pipeline_config": ("PIPELINE_CONFIG",),
            },
            "optional": {
                "stream_name": ("STRING", {"default": "comfyui-livestream"}),
                "fps": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 120,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Frames per second for the stream",
                }),
                "width": ("INT", {
                    "default": 512,
                    "min": 64,
                    "max": 4096,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Frame width in pixels (can connect from pipeline config)",
                    "forceInput": False,
                }),
                "height": ("INT", {
                    "default": 512,
                    "min": 64,
                    "max": 4096,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Frame height in pixels (can connect from pipeline config)",
                    "forceInput": False,
                }),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable/disable stream start operations",
                }),
                "stop_stream": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "Stop stream",
                        "label_off": "Idle",
                    },
                ),
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
        """
        Return a hash of the inputs to enable caching.
        If inputs haven't changed, ComfyUI will use cached outputs.
        """
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
        """
        Publish the desired stream configuration for the browser to start via BYOC-SDK.
        The actual WHIP/WHEP session runs in the ComfyUI frontend (browser).
        """
        # If disabled and not stopping, return cached or empty
        if not enabled and not stop_stream:
            LOGGER.debug("StartRTCStream disabled; skipping start")
            return self._cached_result or ("", "", "")

        pipeline_digest = hash_pipeline_config(pipeline_config)
        current_cache_key = f"{pipeline_digest}:{stream_name}:{fps}:{width}:{height}"

        # Check if we can use cached result
        if (
            not stop_stream
            and self._cache_key == current_cache_key
            and self._cached_result is not None
        ):
            LOGGER.info("Using cached stream (stream_id=%s)", self._cached_result[0])
            return self._cached_result

        if stop_stream:
            RTC_STATE.clear_session()
            self._cache_key = None
            self._cached_result = None
            self._reset_stop_toggle(unique_id, extra_pnginfo)
            return ("", "", "")

        # Publish desired config for the browser publisher.
        RTC_STATE.set_desired_config(
            stream_name=stream_name or "comfyui-livestream",
            pipeline=pipeline_config.get("pipeline") if isinstance(pipeline_config, dict) else None,
            pipeline_config=pipeline_config,
            width=width,
            height=height,
            fps=fps,
        )
        session = RTC_STATE.get_session()
        stream_id = session.get("stream_id") or ""
        playback_id = session.get("playback_url") or ""
        whip_url = session.get("whip_url") or ""
        result = (stream_id, playback_id, whip_url)
        self._cache_key = current_cache_key
        self._cached_result = result
        self._send_notification(
            "info",
            "Stream Requested",
            "Open the Daydream Live sidebar (browser) and click Start Stream.",
        )
        return result

    def _send_notification(self, severity: str, summary: str, detail: str):
        """Send a notification to the ComfyUI frontend."""
        try:
            if PromptServer is None:
                return

            server = PromptServer.instance
            if server:
                server.send_sync(
                    "rtc-stream-notification",
                    {
                        "severity": severity,
                        "summary": summary,
                        "detail": detail,
                    },
                )
                LOGGER.debug("Sent notification: %s - %s", summary, detail)
        except Exception as exc:
            LOGGER.debug("Failed to send notification: %s", exc)

    def _reset_stop_toggle(self, unique_id, extra_pnginfo) -> None:
        if unique_id is None or not extra_pnginfo:
            return
        workflow = extra_pnginfo.get("workflow")
        if not workflow:
            return
        nodes = workflow.get("nodes") or []
        target = next(
            (node for node in nodes if str(node.get("id")) == str(unique_id)),
            None,
        )
        if not target:
            return
        widgets = target.get("widgets_values")
        if not widgets or len(widgets) < 2:
            return
        if widgets[1]:
            widgets[1] = False

def _tensor_to_png_b64(image) -> str:
    try:
        if Image is None:
            return ""
        tensor = image
        if hasattr(tensor, "detach"):
            tensor = tensor.detach()
        if hasattr(tensor, "cpu"):
            tensor = tensor.cpu()
        arr = tensor.numpy()
        if arr.ndim == 4:
            arr = arr[0]
        arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
        pil = Image.fromarray(arr, mode="RGB")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # pragma: no cover
        LOGGER.error("Failed to encode IMAGE tensor to PNG: %s", exc)
        return ""



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

