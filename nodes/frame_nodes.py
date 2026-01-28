import asyncio
import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import folder_paths
import numpy as np
import requests
import torch
from PIL import Image

from rtc_stream.frame_bridge import has_loop, queue_depth
from rtc_stream.frame_uplink import deliver_tensor_frame
from .server_manager import server_status
from .settings_storage import DEFAULT_PORT
from .pipeline_config import hash_pipeline_config

PromptServer = None
try:  # pragma: no cover - PromptServer might not be available outside tests
    from server import PromptServer
except ImportError:
    pass


LOGGER = logging.getLogger("rtc_stream.nodes")


def query_status_api(stream_id: str = "") -> Dict[str, Any]:
    """
    Query the RTC stream status from the local API server.

    Args:
        stream_id: Optional stream identifier (for future use, currently unused)

    Returns:
        Dict containing status information, or empty dict on failure
    """
    try:
        # Get server status
        status = server_status()
        if not status.get("running"):
            LOGGER.error("Local RTC API server is not running")
            return {}

        host = status.get("host") or "127.0.0.1"
        port = status.get("port") or DEFAULT_PORT
        base_url = f"http://{host}:{port}"

        # Make API request
        session = requests.Session()
        response = session.get(f"{base_url}/status", timeout=10)
        response.raise_for_status()
        return response.json()

    except requests.RequestException as exc:
        LOGGER.error("Failed to query RTC status API: %s", exc)
        return {}
    except Exception as exc:
        LOGGER.error("Unexpected error querying RTC status: %s", exc)
        return {}


class RTCStreamFrameInput:
    """
    ComfyUI output node that enqueues frame tensors into the streaming pipeline.
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
    def push_frame(image: torch.Tensor, enabled: bool = True):
        if enabled:
            success, mode = deliver_tensor_frame(image)
            if success and mode == "local":
                LOGGER.debug(
                    "RTC stream enqueued frame (loop_ready=%s depth=%s)",
                    has_loop(),
                    queue_depth(),
                )
            elif success:
                LOGGER.debug("RTC stream uploaded frame via HTTP uplink")
            else:
                LOGGER.warning("Failed to deliver frame via HTTP uplink")
        return ()


class RTCStreamFrameOutput:
    """
    ComfyUI node that retrieves the latest frame from the WHEP subscriber.
    """

    def __init__(self):
        self._session = requests.Session()

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
        base_url = self._resolve_base_url()
        if not base_url:
            LOGGER.error("Local RTC API server unavailable; returning blank frame")
            return (self._blank_tensor(), whep_url)

        status = self._get_whep_status(base_url)
        if status is None:
            return (self._blank_tensor(), whep_url)

        should_connect = not (status.get("connected") or status.get("connecting"))
        if should_connect:
            if whep_url:
                self._connect_whep(base_url, whep_url)
            else:
                LOGGER.warning("WHEP subscriber idle but no whep_url provided")

        frame_payload = self._fetch_frame(base_url)
        if not frame_payload:
            return (self._blank_tensor(), whep_url)

        frame_b64 = frame_payload.get("frame_b64") or ""
        tensor = self._b64_to_tensor(frame_b64)
        if tensor is None:
            return (self._blank_tensor(), whep_url)
        return (tensor, whep_url)

    def _resolve_base_url(self) -> Optional[str]:
        status = server_status()
        if not status.get("running"):
            LOGGER.error("Local RTC API server is not running")
            return None
        host = status.get("host") or "127.0.0.1"
        port = status.get("port") or DEFAULT_PORT
        return f"http://{host}:{port}"

    def _get_whep_status(self, base_url: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.get(f"{base_url}/whep/status", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            LOGGER.error("Failed to query WHEP status: %s", exc)
            return None

    def _connect_whep(self, base_url: str, whep_url: str) -> None:
        try:
            response = self._session.post(
                f"{base_url}/whep/connect",
                json={"whep_url": whep_url},
                timeout=5,
            )
            response.raise_for_status()
            LOGGER.info("Requested WHEP subscription for %s", whep_url)
        except requests.RequestException as exc:
            LOGGER.error("Failed to request WHEP connection: %s", exc)

    def _fetch_frame(self, base_url: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.get(f"{base_url}/whep/frame", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            LOGGER.error("Failed to fetch WHEP frame: %s", exc)
            return None

    @staticmethod
    def _b64_to_tensor(frame_b64: str) -> Optional[torch.Tensor]:
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
    def _blank_tensor(width: int = 1280, height: int = 720) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


class RTCStreamStatus:
    """
    ComfyUI node that retrieves stream status from the local API server.
    Reads fast in-memory state updated by background polling.
    """

    def __init__(self):
        self._session = requests.Session()

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
        base_url = self._resolve_base_url()
        if not base_url:
            LOGGER.error("Local RTC API server unavailable for status check")
            return self._empty_status()

        try:
            response = self._session.get(f"{base_url}/status", timeout=2)
            response.raise_for_status()
            status = response.json()
        except requests.RequestException as exc:
            LOGGER.error("Failed to fetch stream status: %s", exc)
            return self._empty_status()

        # Extract fields
        import json
        
        running = status.get("running", False)
        stream_id_out = status.get("stream_id", "")
        playback_id = status.get("playback_id", "")
        whep_url = self._extract_whep_url(status)
        frames_sent = int(status.get("frames_sent", 0))
        queue_depth_val = int(status.get("queue_depth", 0))
        status_json = json.dumps(status, indent=2)

        return (running, stream_id_out, playback_id, whep_url, frames_sent, queue_depth_val, status_json)

    def _resolve_base_url(self) -> Optional[str]:
        """Resolve the local API server base URL."""
        status = server_status()
        if not status.get("running"):
            LOGGER.error("Local RTC API server is not running")
            return None
        host = status.get("host") or "127.0.0.1"
        port = status.get("port") or DEFAULT_PORT
        return f"http://{host}:{port}"

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
        self._session = requests.Session()

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

        # Resolve the API server base URL
        base_url = self._resolve_base_url()
        if not base_url:
            LOGGER.error("Local RTC API server unavailable")
            self._send_notification("error", "Update Failed", "API server unavailable")
            return ()

        stream_id = ""
        try:
            status_response = self._session.get(f"{base_url}/status", timeout=10)
            status_response.raise_for_status()
            status_data = status_response.json()
            stream_id = status_data.get("stream_id", "")
            running = bool(status_data.get("running"))
        except requests.RequestException as exc:
            LOGGER.warning("Failed to query stream status: %s", exc)
            running = False

        if not running or not stream_id:
            LOGGER.warning("No running stream available for update")
            self._send_notification(
                "warn",
                "Update Skipped",
                "No active stream found; start a stream first",
            )
            return ()

        # Send the update request
        try:
            payload = {"pipeline_config": pipeline_config}
            LOGGER.info("Updating stream %s with new pipeline config", stream_id)
            response = self._session.patch(
                f"{base_url}/pipeline",
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            response.json()

            LOGGER.info("Stream %s updated successfully", stream_id)

            self._send_notification(
                "success",
                "Pipeline Updated",
                f"Stream {stream_id[:12]}... parameters updated",
            )

            return ()

        except requests.RequestException as exc:
            error_msg = str(exc)
            LOGGER.error("Failed to update stream: %s", error_msg)
            
            # Check for specific error conditions
            if "409" in error_msg:
                self._send_notification("warn", "No Active Stream", 
                                       "Start a stream before updating parameters")
            elif "405" in error_msg:
                self._send_notification("warn", "Update Not Supported", 
                                       "PATCH endpoint not available. Stop and restart stream instead.")
            else:
                self._send_notification("error", "Update Failed", error_msg)
            return ()

    def _resolve_base_url(self) -> Optional[str]:
        """Resolve the local API server base URL."""
        status = server_status()
        if not status.get("running"):
            LOGGER.error("Local RTC API server is not running")
            return None
        host = status.get("host") or "127.0.0.1"
        port = status.get("port") or DEFAULT_PORT
        return f"http://{host}:{port}"

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
        self._session = requests.Session()
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
        Start a stream with the given pipeline configuration.
        This method is called by ComfyUI and handles the actual stream creation.
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

        base_url = self._resolve_base_url()
        if not base_url:
            LOGGER.error("Local RTC API server unavailable")
            if stop_stream:
                self._reset_stop_toggle(unique_id, extra_pnginfo)
            return ("", "", "")

        if stop_stream:
            # Check if stream is actually running before sending stop
            is_running = False
            try:
                status_response = self._session.get(f"{base_url}/status", timeout=5)
                status_response.raise_for_status()
                status = status_response.json()
                is_running = status.get("running", False)
            except requests.RequestException as exc:
                LOGGER.debug("Failed to check stream status before stop: %s", exc)
            
            if is_running:
                stopped = self._stop_stream(base_url)
                if stopped:
                    self._send_notification("info", "Stream Stopped", "Stop request sent")
            else:
                LOGGER.debug("Stream already stopped; skipping stop request")
            
            self._cache_key = None
            self._cached_result = None
            self._reset_stop_toggle(unique_id, extra_pnginfo)
            return ("", "", "")

        # Check if a stream is already running
        try:
            status_response = self._session.get(f"{base_url}/status", timeout=10)
            status_response.raise_for_status()
            status = status_response.json()

            if status.get("running"):
                stream_id = status.get("stream_id", "")
                playback_id = status.get("playback_id", "")
                whip_url = status.get("whip_url", "")

                if stream_id:
                    LOGGER.info("Stream already running (stream_id=%s), reusing", stream_id)
                    result = (stream_id, playback_id, whip_url)
                    self._cache_key = current_cache_key
                    self._cached_result = result
                    self._send_notification(
                        "info",
                        "Stream Already Running",
                        f"Reusing existing stream: {stream_id[:12]}...",
                    )
                    return result
        except requests.RequestException as exc:
            LOGGER.warning("Failed to check stream status: %s", exc)

        # Start a new stream
        try:
            payload = {
                "stream_name": stream_name or "",
                "pipeline_config": pipeline_config,
                "frame_rate": fps,
                "frame_width": width,
                "frame_height": height,
            }

            LOGGER.info("Starting new stream with config: %s (fps=%d, %dx%d)", stream_name or "default", fps, width, height)
            response = self._session.post(
                f"{base_url}/start",
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            result_data = response.json()

            stream_id = result_data.get("stream_id", "")
            playback_id = result_data.get("playback_id", "")
            whip_url = result_data.get("whip_url", "")

            LOGGER.info("Stream started successfully (stream_id=%s)", stream_id)

            result = (stream_id, playback_id, whip_url)
            self._cache_key = current_cache_key
            self._cached_result = result

            self._send_notification(
                "success",
                "Stream Started",
                f"Stream ID: {stream_id[:12]}...",
            )

            return result

        except requests.RequestException as exc:
            LOGGER.error("Failed to start stream: %s", exc)
            self._send_notification("error", "Stream Start Failed", str(exc))
            return ("", "", "")

    def _resolve_base_url(self) -> Optional[str]:
        """Resolve the local API server base URL."""
        status = server_status()
        if not status.get("running"):
            LOGGER.error("Local RTC API server is not running")
            return None
        host = status.get("host") or "127.0.0.1"
        port = status.get("port") or DEFAULT_PORT
        return f"http://{host}:{port}"

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

    def _stop_stream(self, base_url: str) -> bool:
        try:
            response = self._session.post(f"{base_url}/stop", timeout=15)
            response.raise_for_status()
            LOGGER.info("Stop request sent successfully")
            return True
        except requests.RequestException as exc:
            LOGGER.error("Failed to stop stream: %s", exc)
            self._send_notification("error", "Stream Stop Failed", str(exc))
            return False

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
    "TrickleFrameInput": "Trickle Frame Input",
    "TrickleFrameOutput": "Trickle Frame Output",
    "StartTrickleStream": "Start Trickle Stream",
    "UpdateTrickleStream": "Update Trickle Stream",
}

LEGACY_DAYDREAM_ENABLED = False

if not LEGACY_DAYDREAM_ENABLED:
    for _legacy_key in [
        "RTCStreamFrameInput",
        "RTCStreamFrameOutput",
        "StartRTCStream",
        "UpdateRTCStream",
        "RTCStreamStatus",
    ]:
        NODE_CLASS_MAPPINGS.pop(_legacy_key, None)
        NODE_DISPLAY_NAME_MAPPINGS.pop(_legacy_key, None)


# --- Network (trickle) nodes ---

from rtc_stream.credentials import resolve_network_config
from rtc_stream.frame_bridge import enqueue_tensor_frame, queue_depth, has_loop, FRAME_BRIDGE
from rtc_stream.network_controller import NetworkController, NetworkControllerConfig
from rtc_stream.network_subscriber import NetworkSubscriber, NetworkSubscriberConfig
from rtc_stream.trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE


@dataclass
class _NetworkRuntime:
    controller: Optional[NetworkController] = None
    subscriber: Optional[NetworkSubscriber] = None
    last_startup_error: Optional[str] = None  # Track startup failures


_NETWORK_RUNTIME = _NetworkRuntime()


def _get_controller(config: NetworkControllerConfig) -> NetworkController:
    if _NETWORK_RUNTIME.controller:
        ctrl = _NETWORK_RUNTIME.controller
        # Clear if stream is dead (ERROR or CLOSED state)
        if ctrl._stream_state in (
            NetworkController.StreamState.ERROR,
            NetworkController.StreamState.CLOSED,
        ):
            LOGGER.info(
                "Clearing dead controller (state=%s, last_error=%s)",
                ctrl._stream_state.value,
                ctrl._last_error or "none",
            )
            _NETWORK_RUNTIME.controller = None
        else:
            ctrl.update_config(config)
            return ctrl
    controller = NetworkController(config)
    _NETWORK_RUNTIME.controller = controller
    return controller


def _get_subscriber(start_seq: int, loop: asyncio.AbstractEventLoop) -> NetworkSubscriber:
    if _NETWORK_RUNTIME.subscriber:
        _NETWORK_RUNTIME.subscriber.attach_loop(loop)
        _NETWORK_RUNTIME.subscriber.config.start_seq = start_seq
        return _NETWORK_RUNTIME.subscriber
    subscriber = NetworkSubscriber(NetworkSubscriberConfig(start_seq=start_seq))
    subscriber.attach_loop(loop)
    _NETWORK_RUNTIME.subscriber = subscriber
    return subscriber


class TrickleConfig:
    """
    Configuration node for trickle streaming parameters.
    Outputs a config dict that can be connected to Start Trickle Stream.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "orchestrator_url": ("STRING", {
                    "default": "https://localhost:8935",
                    "tooltip": "Orchestrator URL (e.g., https://hky.eliteencoder.net:8936)",
                }),
                "signer_url": ("STRING", {
                    "default": "",
                    "tooltip": "Signer URL for authentication (e.g., http://localhost:8081)",
                }),
                "model_id": ("STRING", {
                    "default": "noop",
                    "tooltip": "Model ID to use (e.g., noop, comfystream)",
                }),
                "fps": ("FLOAT", {
                    "default": 30.0,
                    "min": 1.0,
                    "max": 120.0,
                    "step": 0.1,
                    "tooltip": "Frames per second",
                }),
                "keyframe_interval": ("FLOAT", {
                    "default": 2.0,
                    "min": 0.5,
                    "max": 10.0,
                    "step": 0.1,
                    "tooltip": "Keyframe interval in seconds",
                }),
            },
        }

    RETURN_TYPES = ("TRICKLE_CONFIG",)
    RETURN_NAMES = ("config",)
    FUNCTION = "create_config"
    CATEGORY = "Trickle"

    def create_config(
        self,
        orchestrator_url: str,
        signer_url: str,
        model_id: str,
        fps: float,
        keyframe_interval: float,
    ) -> tuple:
        config = {
            "orchestrator_url": orchestrator_url,
            "signer_url": signer_url,
            "model_id": model_id,
            "fps": fps,
            "keyframe_interval": keyframe_interval,
        }
        return (config,)


class StartTrickleStream:
    """
    Start a trickle-based stream directly to an orchestrator.
    Requires a TrickleConfig node for connection settings.
    Proactively checks stream health if last execution was over 4 seconds ago.
    Each new stream gets unique trickle URLs from the orchestrator.
    
    IS_CHANGED returns a value based on stream state, so when the stream ends,
    ComfyUI knows to re-execute this node and dependent nodes.
    """

    # If more than this many seconds since last execution, proactively check stream health
    STALE_CHECK_SECONDS = 4.0
    
    # Class-level counter for stream generations - increments when a new stream starts
    _stream_generation: int = 0

    def __init__(self):
        self._status_cache: Optional[tuple[str, str, str, str]] = None
        self._last_execution_time: float = 0.0

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "config": ("TRICKLE_CONFIG",),
            },
            "optional": {
                "pipeline_params": ("PIPELINE_CONFIG",),
                "width": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "height": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "start_seq": ("INT", {"default": -2}),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Enable to start/continue streaming. Disable to stop the stream and reset for a new session.",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("manifest_id", "publish_url", "subscribe_url", "error")
    FUNCTION = "start_trickle_stream"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> str:
        """
        Return a value that changes when stream state changes.
        This tells ComfyUI to re-execute when stream dies or a new stream starts.
        """
        controller = _NETWORK_RUNTIME.controller
        
        if not controller:
            # No controller - check if there was a startup error
            has_error = bool(_NETWORK_RUNTIME.last_startup_error)
            return f"no_stream_{cls._stream_generation}_error={has_error}"
        
        # Check if background tasks are still alive (updates state if dead)
        controller.check_tasks_alive()
        
        state = controller._stream_state
        running = controller.running
        
        # Return a composite value that changes when stream state changes
        # This triggers re-execution when stream dies
        return f"{state.value}_{running}_{cls._stream_generation}"

    def _stop_stream(self):
        """
        Stop the current stream, reset tracking, and return notification.
        Called when enabled=False.
        """
        stream_was_running = False
        
        # Stop the controller if active
        if _NETWORK_RUNTIME.controller:
            controller = _NETWORK_RUNTIME.controller
            stream_was_running = controller.running
            
            LOGGER.info("StartTrickleStream: Stopping stream (user disabled)")
            controller.stop()
            
            # Clear runtime state so next run starts fresh
            _NETWORK_RUNTIME.controller = None
        
        # Stop the subscriber if active
        if _NETWORK_RUNTIME.subscriber:
            try:
                _NETWORK_RUNTIME.subscriber.stop()
            except Exception as exc:
                LOGGER.warning("Failed to stop subscriber: %s", exc)
            _NETWORK_RUNTIME.subscriber = None
        
        # Reset the frame bridge to clear old loop bindings
        FRAME_BRIDGE.reset()
        
        # Reset instance state
        self._status_cache = ("", "", "", "Stream stopped")
        self._last_execution_time = 0.0
        
        # Reset TrickleFrameInput timing so it doesn't think stream is stale
        TrickleFrameInput._last_frame_time = 0.0
        
        if stream_was_running:
            LOGGER.info("StartTrickleStream: Stream stopped successfully")
            message = "Stream stopped. Enable to start a new stream."
        else:
            LOGGER.info("StartTrickleStream: No active stream to stop")
            message = "No active stream. Enable to start streaming."
        
        # Return with UI notification
        return {
            "ui": {"text": [message]},
            "result": self._status_cache,
        }

    def _check_and_clear_stale_stream(self) -> None:
        """
        If enough time has passed since last execution, proactively check if
        the existing stream is still alive. If dead, clear the controller so
        a new stream will be started.
        """
        now = time.perf_counter()
        elapsed = now - self._last_execution_time
        
        if elapsed > self.STALE_CHECK_SECONDS and _NETWORK_RUNTIME.controller:
            ctrl = _NETWORK_RUNTIME.controller
            state = ctrl._stream_state
            
            # Check if stream died
            if state in (
                NetworkController.StreamState.ERROR,
                NetworkController.StreamState.CLOSED,
            ):
                LOGGER.info(
                    "StartTrickleStream: Stream stale after %.1fs (state=%s), will start new stream",
                    elapsed,
                    state.value,
                )
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None
            elif not ctrl.is_healthy():
                # Also check is_healthy for edge cases (e.g., STARTING but grace period expired)
                health = ctrl.get_health()
                LOGGER.info(
                    "StartTrickleStream: Stream unhealthy after %.1fs (state=%s, error=%s), will start new stream",
                    elapsed,
                    state.value,
                    health.get("last_error", ""),
                )
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None

    def start_trickle_stream(
        self,
        config: Dict[str, Any],
        pipeline_params: Optional[Dict[str, Any]] = None,
        width: int = 512,
        height: int = 512,
        start_seq: int = -2,
        enabled: bool = True,
    ):
        # When disabled, stop the stream and reset state
        if not enabled:
            return self._stop_stream()

        # Proactively check for stale/dead streams before proceeding
        self._check_and_clear_stale_stream()

        # Extract values from config
        orchestrator_url = config.get("orchestrator_url", "https://localhost:8935")
        signer_url = config.get("signer_url", "")
        model_id = config.get("model_id", "noop")
        fps = config.get("fps", 30.0)
        keyframe_interval = config.get("keyframe_interval", 2.0)

        resolved_orch, resolved_signer = resolve_network_config(orchestrator_url, signer_url)
        controller_config = NetworkControllerConfig(
            orchestrator_url=resolved_orch,
            signer_url=resolved_signer or None,
            model_id=model_id,
            fps=float(fps),
            frame_width=width,
            frame_height=height,
            keyframe_interval_s=float(keyframe_interval),
        )
        controller = _get_controller(controller_config)
        
        # Option 2: Validate stream state before reusing
        # Force restart if stream is dead/closed/idle, or if not healthy
        needs_restart = False
        if controller._stream_state in (
            NetworkController.StreamState.ERROR,
            NetworkController.StreamState.CLOSED,
            NetworkController.StreamState.IDLE,
        ):
            LOGGER.info(
                "StartTrickleStream: Stream state=%s, forcing restart",
                controller._stream_state.value,
            )
            needs_restart = True
        elif not controller.is_healthy():
            health = controller.get_health()
            LOGGER.info(
                "StartTrickleStream: Stream unhealthy (state=%s, error=%s), forcing restart",
                controller._stream_state.value,
                health.get("last_error", ""),
            )
            needs_restart = True
        elif controller.running:
            # Stream is running and healthy - reuse existing stream
            LOGGER.debug(
                "StartTrickleStream: Reusing healthy stream (state=%s, frames_sent=%d)",
                controller._stream_state.value,
                controller.frames_sent,
            )
            status = controller.status()
            health = controller.get_health()
            
            # Ensure subscriber is running if we have a subscribe_url
            subscribe_url = status.get("subscribe_url")
            if subscribe_url:
                subscriber = _get_subscriber(start_seq, controller.loop)
                if not subscriber.task_alive:
                    task_error = subscriber.check_task_exception()
                    LOGGER.info(
                        "StartTrickleStream: Subscriber task not alive (error=%s), restarting",
                        task_error or "none",
                    )
                    subscriber.start(subscribe_url)
            
            # Skip to output - don't restart
            needs_restart = False
        else:
            needs_restart = True
        
        if needs_restart:
            try:
                status = controller.start(
                    model_id=model_id,
                    params=pipeline_params or {},
                )
                health = controller.get_health()
                
                # Increment stream generation so IS_CHANGED reflects the new stream
                StartTrickleStream._stream_generation += 1
                
                # Clear any previous startup error since we succeeded
                _NETWORK_RUNTIME.last_startup_error = None
                
                LOGGER.info(
                    "StartTrickleStream: New stream started (generation=%d, publish_url=%s)",
                    StartTrickleStream._stream_generation,
                    status.get("publish_url", "")[:50] + "...",
                )
                
                # Start subscriber if subscribe_url is present (only on restart)
                if status.get("subscribe_url"):
                    subscriber = _get_subscriber(start_seq, controller.loop)
                    subscriber.start(status["subscribe_url"])
            except Exception as exc:
                # Handle connection/timeout errors gracefully
                error_str = str(exc)
                LOGGER.error("StartTrickleStream: Failed to start stream: %s", error_str)
                
                # Properly stop and clean up the controller before clearing
                if controller:
                    try:
                        controller.stop()
                    except Exception as stop_exc:
                        LOGGER.warning("Failed to stop controller during error cleanup: %s", stop_exc)
                
                # Reset the frame bridge to clear old loop bindings
                FRAME_BRIDGE.reset()
                
                # Clear the controller so next execution tries fresh
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None
                self._status_cache = None
                
                # Track the startup error so other nodes know why there's no stream
                error_msg = f"Failed to start stream: {error_str}"
                _NETWORK_RUNTIME.last_startup_error = error_msg
                
                self._status_cache = ("", "", "", error_msg)
                return self._status_cache

        # Update tracking state
        self._last_execution_time = time.perf_counter()

        manifest_id = status.get("manifest_id", "")
        publish_url = status.get("publish_url", "")
        subscribe_url = status.get("subscribe_url", "")
        error_msg = "" if controller.is_healthy() else health.get("last_error", "")
        self._status_cache = (manifest_id, publish_url, subscribe_url, error_msg)
        return self._status_cache


class TrickleFrameInput:
    """
    Enqueue frames into the trickle publisher queue.
    Connect the publish_url output from StartTrickleStream to ensure correct execution order.
    """
    
    # Class-level tracking for proactive health checks
    _last_frame_time: float = 0.0
    HEALTH_CHECK_INTERVAL_SECONDS: float = 4.0

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "optional": {
                "publish_url": ("STRING", {
                    "default": "",
                    "tooltip": "Connect from StartTrickleStream to ensure stream starts first",
                }),
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "push_frame"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        return True

    def push_frame(self, image: torch.Tensor, publish_url: str = "", enabled: bool = True):
        if enabled:
            controller = _NETWORK_RUNTIME.controller
            
            # No controller - stream not started or failed to start
            if not controller:
                # Check if there was a startup error
                if _NETWORK_RUNTIME.last_startup_error:
                    LOGGER.error(
                        "TrickleFrameInput: No stream available - %s",
                        _NETWORK_RUNTIME.last_startup_error,
                    )
                    raise RuntimeError(
                        f"Trickle stream failed to start: {_NETWORK_RUNTIME.last_startup_error}. "
                        "Check orchestrator connection and try again."
                    )
                else:
                    # No error but no controller - likely execution order issue
                    LOGGER.warning(
                        "TrickleFrameInput: No stream started yet, dropping frame. "
                        "Connect publish_url from StartTrickleStream to ensure correct order."
                    )
                    return ()
            
            # Proactive health check if enough time has passed since last frame
            now = time.perf_counter()
            elapsed_since_last = now - TrickleFrameInput._last_frame_time
            
            if elapsed_since_last > self.HEALTH_CHECK_INTERVAL_SECONDS:
                # Check if background tasks are still alive
                tasks_alive = controller.check_tasks_alive()
                if not tasks_alive:
                    health = controller.get_health()
                    error_msg = health.get("last_error", "stream tasks exited")
                    LOGGER.error(
                        "TrickleFrameInput: Stream died (detected via task check after %.1fs gap): %s",
                        elapsed_since_last, error_msg,
                    )
                    raise RuntimeError(
                        f"Trickle stream ended: {error_msg}. "
                        "Re-run workflow to start a new stream."
                    )
            
            # Check stream state for more specific handling
            state = controller._stream_state
            running = controller.running
            
            # Log state for debugging (only occasionally to avoid spam)
            if controller.frames_sent % 30 == 0:
                LOGGER.debug(
                    "TrickleFrameInput: state=%s running=%s frames_sent=%d",
                    state.value, running, controller.frames_sent,
                )
            
            if state == NetworkController.StreamState.IDLE:
                LOGGER.warning(
                    "TrickleFrameInput: Stream in IDLE state, dropping frame. "
                    "Connect publish_url from StartTrickleStream to ensure correct order."
                )
                return ()
            
            # Stream is dead - raise error to stop workflow
            if state in (NetworkController.StreamState.ERROR, NetworkController.StreamState.CLOSED):
                health = controller.get_health()
                error_msg = health.get("last_error", "")
                LOGGER.error(
                    "TrickleFrameInput: Stream ended (state=%s, error=%s)",
                    state.value, error_msg,
                )
                raise RuntimeError(
                    f"Trickle stream ended (state={state.value}): {error_msg or 'stream closed'}. "
                    "Re-run workflow to start a new stream."
                )
            
            # Also check running flag - if False but state not ERROR/CLOSED, stream died unexpectedly
            if not running and state not in (NetworkController.StreamState.STARTING,):
                health = controller.get_health()
                error_msg = health.get("last_error", "")
                LOGGER.error(
                    "TrickleFrameInput: Stream not running (state=%s, error=%s)",
                    state.value, error_msg,
                )
                raise RuntimeError(
                    f"Trickle stream stopped (state={state.value}): {error_msg or 'publisher stopped'}. "
                    "Re-run workflow to start a new stream."
                )
            
            # STARTING, RUNNING, DEGRADED states - check is_healthy for grace period logic
            if state == NetworkController.StreamState.STARTING:
                # Allow frames during startup grace period
                if not controller.is_healthy():
                    health = controller.get_health()
                    error_msg = health.get("last_error", "")
                    raise RuntimeError(
                        f"Trickle stream failed to start: {error_msg or 'startup timeout'}. "
                        "Re-run workflow to try again."
                    )
            
            enqueue_tensor_frame(image)
            
            # Update last frame time for health check interval tracking
            TrickleFrameInput._last_frame_time = time.perf_counter()
            
            LOGGER.debug(
                "Trickle frame enqueued (loop_ready=%s depth=%s state=%s)",
                has_loop(),
                queue_depth(),
                state.value,
            )
        return ()


class TrickleFrameOutput:
    """
    Retrieve the latest decoded frame from the trickle subscriber.
    
    The subscriber is automatically started by StartTrickleStream and runs in
    the background, storing the latest output frame in a shared bridge.
    This node returns the most recent frame and displays a preview.
    """

    def __init__(self):
        self._blank = self._blank_tensor()
        self._output_dir = folder_paths.get_temp_directory()
        self._type = "temp"
        self._prefix = "trickle_output"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "optional": {},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "pull_frame"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True  # Always executes and shows preview

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> float:
        # NaN != NaN, so this always triggers re-execution
        return float("nan")

    def pull_frame(self):
        """Pull the latest frame from the trickle subscriber (synchronous)."""
        from PIL import Image
        
        # Check if subscriber is active
        subscriber = _NETWORK_RUNTIME.subscriber
        if not subscriber:
            return self._return_with_preview(self._blank)
        
        # Check if task has crashed
        task_error = subscriber.check_task_exception()
        if task_error:
            LOGGER.warning("TrickleFrameOutput: Subscriber task crashed: %s", task_error)
        
        if not subscriber.running and not subscriber.task_alive:
            return self._return_with_preview(self._blank)
        
        try:
            frame_np, timestamp, has_frame = TRICKLE_OUTPUT_BRIDGE.get_frame_or_blank_sync()
            tensor = torch.from_numpy(frame_np.astype(np.float32) / 255.0).unsqueeze(0)
            return self._return_with_preview(tensor)
        except Exception as exc:
            LOGGER.error("TrickleFrameOutput error: %s", exc)
            return self._return_with_preview(self._blank)

    def _return_with_preview(self, tensor: torch.Tensor):
        """Return tensor with UI preview."""
        from PIL import Image
        import uuid
        
        results = []
        for img_tensor in tensor:
            # Convert tensor to PIL Image
            img_np = (img_tensor.cpu().numpy() * 255).astype(np.uint8)
            img = Image.fromarray(img_np)
            
            # Save to temp directory
            filename = f"{self._prefix}_{uuid.uuid4().hex[:8]}.png"
            filepath = os.path.join(self._output_dir, filename)
            img.save(filepath, compress_level=1)
            
            results.append({
                "filename": filename,
                "subfolder": "",
                "type": self._type,
            })
        
        return {
            "ui": {"images": results},
            "result": (tensor,),
        }

    @staticmethod
    def _blank_tensor(width: int = 512, height: int = 512) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


class UpdateTrickleStream:
    """
    Send control messages to the running trickle stream (if supported).
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "control_payload": ("DICT",),
            },
            "optional": {
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "update_trickle_stream"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return True

    def update_trickle_stream(self, control_payload: Dict[str, Any], enabled: bool = True):
        if not enabled:
            return ()
        controller = _NETWORK_RUNTIME.controller
        if not controller or not controller.job or not controller.job.control:
            LOGGER.warning("No active trickle stream control channel available")
            return ()
        try:
            future = asyncio.run_coroutine_threadsafe(
                controller.job.control.write_control(control_payload),
                controller.loop,
            )
            future.result(timeout=5)
            return ()
        except Exception as exc:
            LOGGER.error("Failed to send control payload: %s", exc)
            return ()


# Register trickle nodes into the mapping dictionaries
NODE_CLASS_MAPPINGS.update(
    {
        "TrickleConfig": TrickleConfig,
        "TrickleFrameInput": TrickleFrameInput,
        "TrickleFrameOutput": TrickleFrameOutput,
        "StartTrickleStream": StartTrickleStream,
        "UpdateTrickleStream": UpdateTrickleStream,
    }
)

NODE_DISPLAY_NAME_MAPPINGS.update(
    {
        "TrickleConfig": "Trickle Config",
        "TrickleFrameInput": "Trickle Frame Input",
        "TrickleFrameOutput": "Trickle Frame Output",
        "StartTrickleStream": "Start Trickle Stream",
        "UpdateTrickleStream": "Update Trickle Stream",
    }
)

