import base64
import io
import json
import logging
import time
from typing import Any, Dict, Optional

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
            },
            "optional": {
                "connect_timeout": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 30.0, "step": 0.25}),
                "frame_timeout": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 30.0, "step": 0.25}),
            },
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

    def pull_frame(
        self,
        whep_url: str,
        connect_timeout: float = 4.0,
        frame_timeout: float = 2.0,
    ):
        connect_timeout = max(connect_timeout, 0.0)
        frame_timeout = max(frame_timeout, 0.0)
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
                if connect_timeout > 0:
                    _, latest_status = self._wait_for_connection(
                        base_url, connect_timeout
                    )
                    if latest_status:
                        status = latest_status
            else:
                LOGGER.warning("WHEP subscriber idle but no whep_url provided")

        frame_payload = self._fetch_frame(base_url)
        if not frame_payload:
            wait_for_ready = (
                connect_timeout > 0 and status and not status.get("connected")
            ) or frame_timeout > 0
            if wait_for_ready:
                frame_payload = self._wait_for_frame(
                    base_url,
                    connect_timeout if should_connect else frame_timeout,
                )
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

    def _wait_for_connection(
        self, base_url: str, timeout: float, poll_interval: float = 0.25
    ):
        deadline = time.monotonic() + timeout
        latest_status = None
        while time.monotonic() < deadline:
            status = self._get_whep_status(base_url)
            latest_status = status
            if status and status.get("connected"):
                return True, status
            if timeout == 0:
                break
            time.sleep(poll_interval)
        return False, latest_status

    def _fetch_frame(self, base_url: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.get(f"{base_url}/whep/frame", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            LOGGER.error("Failed to fetch WHEP frame: %s", exc)
            return None

    def _wait_for_frame(
        self, base_url: str, timeout: float, poll_interval: float = 0.25
    ) -> Optional[Dict[str, Any]]:
        if timeout <= 0:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self._fetch_frame(base_url)
            if payload and payload.get("frame_b64"):
                return payload
            time.sleep(poll_interval)
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

    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING", "STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("running", "stream_id", "playback_id", "playback_url", "whep_url", "frames_sent", "queue_depth", "status_json")
    FUNCTION = "get_status"
    CATEGORY = "RTC Stream"
    OUTPUT_NODE = True

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
        playback_url = f"https://lvpr.tv?v={playback_id}" if playback_id else ""
        whep_url = self._extract_whep_url(status)
        frames_sent = int(status.get("frames_sent", 0))
        queue_depth_val = int(status.get("queue_depth", 0))
        status_json = json.dumps(status, indent=2)

        return (
            running,
            stream_id_out,
            playback_id,
            playback_url,
            whep_url,
            frames_sent,
            queue_depth_val,
            status_json,
        )

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
        return (False, "", "", "", "", 0, 0, "{}")

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
}

