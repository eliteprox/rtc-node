import asyncio
import logging
from typing import Optional

import numpy as np
import torch
from asyncio import QueueEmpty


LOGGER = logging.getLogger("rtc_stream.frame_bridge")


class FrameBridge:
    """
    Shared queue that allows ComfyUI custom nodes (sync context) to enqueue frames
    for the async streaming controller.
    """

    def __init__(self, max_size: int = 90):
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=max_size)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        LOGGER.info("FrameBridge attached to loop %s", loop)

    def _schedule_put(self, frame: np.ndarray) -> None:
        if self.loop is None:
            LOGGER.debug("FrameBridge loop not ready; dropping frame")
            return
        asyncio.run_coroutine_threadsafe(self.queue.put(frame), self.loop)

    def enqueue(self, frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be a numpy array")
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("frame must be HxWxC RGB/RGBA")
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        self._schedule_put(frame[:, :, :3])

    def try_get_nowait(self) -> Optional[np.ndarray]:
        try:
            return self.queue.get_nowait()
        except QueueEmpty:
            return None


FRAME_BRIDGE = FrameBridge()


def has_loop() -> bool:
    return FRAME_BRIDGE.loop is not None


def enqueue_array_frame(frame: np.ndarray) -> None:
    FRAME_BRIDGE.enqueue(frame)


def enqueue_tensor_frame(tensor: torch.Tensor) -> None:
    """
    Convert a ComfyUI tensor (B,H,W,C) in float range 0-1 into uint8 array.
    """

    if tensor is None:
        raise ValueError("tensor is None")
    data = tensor
    if data.dim() == 4:
        data = data[0]
    if data.dim() == 3 and data.shape[0] in (1, 3):
        # assume CHW
        data = data.permute(1, 2, 0)
    np_frame = (
        data.detach().cpu().numpy()
    )
    if np_frame.max() <= 1.0:
        np_frame = np_frame * 255.0
    enqueue_array_frame(np_frame.astype(np.uint8))

