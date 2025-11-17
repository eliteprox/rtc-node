import base64
import io
import json
import logging
from pathlib import Path
from typing import Literal, Optional, Tuple

import numpy as np
import requests
import torch
from PIL import Image

from .frame_bridge import enqueue_tensor_frame, has_loop, tensor_to_uint8_frame


LOGGER = logging.getLogger("rtc_stream.frame_uplink")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8895
ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_DIR = ROOT_DIR / "settings"
STATE_PATH = SETTINGS_DIR / "local_api_server_state.json"
SETTINGS_PATH = SETTINGS_DIR / "rtc_stream_settings.json"

_SERVER_BASE_CACHE: Optional[str] = None
_STATE_MTIME: Optional[float] = None
_SETTINGS_MTIME: Optional[float] = None


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - diagnostics
        LOGGER.debug("Failed to read %s: %s", path, exc)
        return None


def _load_server_host_port_from_state() -> Optional[Tuple[str, int]]:
    data = _read_json(STATE_PATH)
    if not data:
        return None
    host = (data.get("host") or DEFAULT_HOST).strip()
    port = int(data.get("port") or DEFAULT_PORT)
    return host, port


def _load_server_host_port_from_settings() -> Optional[Tuple[str, int]]:
    data = _read_json(SETTINGS_PATH)
    if not data:
        return None
    host = (data.get("host") or DEFAULT_HOST).strip()
    port = int(data.get("port") or DEFAULT_PORT)
    return host, port


def _resolve_server_base() -> str:
    global _SERVER_BASE_CACHE, _STATE_MTIME, _SETTINGS_MTIME

    state_mtime = STATE_PATH.stat().st_mtime if STATE_PATH.exists() else None
    settings_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else None

    if (
        _SERVER_BASE_CACHE
        and _STATE_MTIME == state_mtime
        and _SETTINGS_MTIME == settings_mtime
    ):
        return _SERVER_BASE_CACHE

    host_port = _load_server_host_port_from_state() or _load_server_host_port_from_settings()
    if not host_port:
        host_port = (DEFAULT_HOST, DEFAULT_PORT)
    host, port = host_port
    base = f"http://{host}:{port}"

    _SERVER_BASE_CACHE = base
    _STATE_MTIME = state_mtime
    _SETTINGS_MTIME = settings_mtime
    return base


def _encode_frame_b64(frame: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(frame).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _post_frame_remote(frame: np.ndarray) -> bool:
    url = f"{_resolve_server_base().rstrip('/')}/frames"
    payload = {"frame_b64": _encode_frame_b64(frame)}
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=2,
        )
        response.raise_for_status()
    except Exception as exc:
        LOGGER.warning("Failed to push frame to %s: %s", url, exc)
        return False
    return True


FrameDeliveryMode = Literal["local", "remote"]


def deliver_tensor_frame(tensor: torch.Tensor) -> Tuple[bool, FrameDeliveryMode]:
    """
    Deliver a tensor frame to the RTC streaming pipeline, preferring the HTTP
    `/frames` endpoint for fastest ingestion even when the local loop is
    available. Falls back to the in-process queue if HTTP delivery fails and the
    loop is attached.

    Returns a tuple of (success, mode) where mode indicates whether delivery used
    the HTTP uplink ("remote") or the local queue ("local").
    """

    frame = tensor_to_uint8_frame(tensor)
    if _post_frame_remote(frame):
        LOGGER.debug("Delivered frame via HTTP uplink")
        return True, "remote"

    if has_loop():
        enqueue_tensor_frame(tensor)
        LOGGER.debug("HTTP uplink failed; enqueued via local loop")
        return True, "local"

    return False, "remote"


