import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

import numpy as np


LOGGER = logging.getLogger("rtc_stream.whep_frame_bridge")


class WhepFrameBridge:
    """
    Buffer that stores the most recent frame received from the WHEP subscriber.

    The WHEP controller (running inside the asyncio loop) calls `put_frame`
    whenever a new frame arrives. HTTP handlers await `get_latest_frame` to
    fetch the most recent image without blocking the controller.
    """

    def __init__(self, frame_width: int = 1280, frame_height: int = 720):
        self._lock: Optional[asyncio.Lock] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frames_received: int = 0
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._blank_template = self._make_blank(frame_width, frame_height)

    def _ensure_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _make_blank(width: int, height: int) -> np.ndarray:
        return np.zeros((height, width, 3), dtype=np.uint8)

    async def put_frame(self, frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be numpy.ndarray")
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("frame must be HxWxC (RGB/RGBA)")

        normalized = frame[:, :, :3]
        if normalized.dtype != np.uint8:
            normalized = np.clip(normalized, 0, 255).astype(np.uint8)

        async with self._ensure_lock():
            self._latest_frame = normalized.copy()
            self._latest_timestamp = time.time()
            self._frames_received += 1
            self.frame_height, self.frame_width = normalized.shape[:2]
            self._blank_template = self._make_blank(self.frame_width, self.frame_height)
            LOGGER.debug(
                "WHEP bridge stored frame %sx%s (total=%s)",
                self.frame_width,
                self.frame_height,
                self._frames_received,
            )

    async def reset(self) -> None:
        async with self._ensure_lock():
            self._latest_frame = None
            self._latest_timestamp = 0.0
            self._frames_received = 0

    async def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
        async with self._ensure_lock():
            if self._latest_frame is None:
                return None, 0.0
            return self._latest_frame.copy(), self._latest_timestamp

    async def get_latest_frame_or_blank(self) -> Tuple[np.ndarray, Dict[str, float], bool]:
        frame, timestamp = await self.get_latest_frame()
        if frame is None:
            return self.blank_frame(), {"timestamp": 0.0}, False
        return frame, {"timestamp": timestamp}, True

    def blank_frame(self) -> np.ndarray:
        return self._blank_template.copy()

    async def stats(self) -> Dict[str, float]:
        async with self._ensure_lock():
            return {
                "frames_received": self._frames_received,
                "timestamp": self._latest_timestamp,
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
            }


WHEP_FRAME_BRIDGE = WhepFrameBridge()


