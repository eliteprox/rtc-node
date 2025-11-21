import base64
import io
import logging
from typing import Literal, Tuple

import numpy as np
import requests
import torch
from PIL import Image

from .frame_bridge import enqueue_tensor_frame, has_loop, tensor_to_uint8_frame
from .local_api import build_local_api_url


LOGGER = logging.getLogger("rtc_stream.frame_uplink")

def _encode_frame_b64(frame: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(frame).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _post_frame_remote(frame: np.ndarray) -> bool:
    url = build_local_api_url("/frames")
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


