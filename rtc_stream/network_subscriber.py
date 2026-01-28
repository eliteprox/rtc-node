from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from livepeer_gateway.media_decode import VideoDecodedMediaFrame
from livepeer_gateway.media_output import MediaOutput

from .whep_frame_bridge import WHEP_FRAME_BRIDGE

LOGGER = logging.getLogger("rtc_stream.network_subscriber")


@dataclass
class NetworkSubscriberConfig:
    start_seq: int = -2
    chunk_size: int = 256 * 1024
    max_retries: int = 5


class NetworkSubscriber:
    """
    Trickle subscriber that pulls output frames from the orchestrator and stores
    the latest frame in the shared WHEP_FRAME_BRIDGE for consumption by ComfyUI nodes.

    Follows the pattern from livepeer-python-gateway examples:
    - Uses MediaOutput for trickle subscription
    - Decodes frames and stores them in a thread-safe bridge
    """

    def __init__(self, config: Optional[NetworkSubscriberConfig] = None):
        self.config = config or NetworkSubscriberConfig()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Future] = None
        self._running = False
        self.frames_received = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def start(self, subscribe_url: str) -> None:
        if not self.loop:
            raise RuntimeError("NetworkSubscriber requires an attached asyncio loop")
        self.stop()
        self._running = True
        self.frames_received = 0
        LOGGER.info("Starting trickle subscriber for %s", subscribe_url)
        self._task = asyncio.run_coroutine_threadsafe(
            self._consume(subscribe_url), self.loop
        )

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _consume(self, subscribe_url: str) -> None:
        """
        Consume frames from the trickle subscriber and store them in the bridge.
        Pattern follows livepeer-python-gateway examples.
        """
        LOGGER.info(
            "Subscriber connecting to %s (start_seq=%d)",
            subscribe_url,
            self.config.start_seq,
        )
        try:
            output = MediaOutput(
                subscribe_url,
                start_seq=self.config.start_seq,
                max_retries=self.config.max_retries,
                chunk_size=self.config.chunk_size,
            )
            async for decoded in output.frames():
                if not self._running:
                    break
                if isinstance(decoded, VideoDecodedMediaFrame):
                    frame = decoded.frame.to_ndarray(format="rgb24")
                    # Use thread-safe sync method since bridge is accessed from multiple threads
                    WHEP_FRAME_BRIDGE.put_frame_sync(np.array(frame))
                    self.frames_received += 1
                    LOGGER.debug(
                        "Subscriber stored frame %sx%s pts=%s (total=%d)",
                        decoded.width,
                        decoded.height,
                        decoded.pts,
                        self.frames_received,
                    )
        except asyncio.CancelledError:
            LOGGER.info("Subscriber cancelled")
        except Exception as exc:
            LOGGER.error("Subscriber error: %s", exc)
