from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import av
import numpy as np

# Enable HTTP signers for local development (must be set before importing SDK)
os.environ.setdefault("ALLOW_HTTP_SIGNER", "1")


from livepeer_gateway.media_publish import MediaPublish, MediaPublishConfig
from livepeer_gateway.orchestrator import (
    GetOrchestratorInfo,
    LiveVideoToVideo,
    StartJob,
    StartJobRequest,
)


from .frame_bridge import FRAME_BRIDGE, array_to_av_frame, normalize_uint8_frame

LOGGER = logging.getLogger("rtc_stream.network_controller")


def _ensure_allow_http_signer() -> None:
    """
    Ensure ALLOW_HTTP_SIGNER is set before initializing orchestrator client.
    """
    if os.environ.get("ALLOW_HTTP_SIGNER") != "1":
        os.environ["ALLOW_HTTP_SIGNER"] = "1"
        LOGGER.debug("ALLOW_HTTP_SIGNER set to 1 for orchestrator client initialization")


# Enforce orchestrator URLs stay https (transcoder endpoints are always https)
def _require_https_orchestrator(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Orchestrator URL must be https://host:port (got {url!r})")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"Orchestrator URL must not include a path/query/fragment: {url!r}")
    return f"https://{parsed.netloc}"


@dataclass
class NetworkControllerConfig:
    orchestrator_url: str
    signer_url: Optional[str] = None
    model_id: str = "comfystream"
    fps: float = 30.0
    frame_width: int = 512
    frame_height: int = 512
    keyframe_interval_s: float = 2.0


class NetworkController:
    """
    Trickle-based media publisher that streams frames directly to an orchestrator.
    Runs a dedicated asyncio loop in a background thread so ComfyUI nodes can
    enqueue frames synchronously.
    """

    def __init__(self, config: NetworkControllerConfig):
        self.config = config
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self.job: Optional[LiveVideoToVideo] = None
        self.media: Optional[MediaPublish] = None
        self._publisher_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.frames_sent: int = 0
        self.running: bool = False

    def update_config(self, config: NetworkControllerConfig) -> None:
        self.config = config

    def _ensure_loop(self) -> None:
        if self.loop:
            return
        loop = asyncio.new_event_loop()
        self.loop = loop
        self._thread = threading.Thread(target=loop.run_forever, name="network-controller-loop", daemon=True)
        self._thread.start()
        FRAME_BRIDGE.attach_loop(loop)
        LOGGER.info("NetworkController event loop started")

    def start(
        self,
        *,
        model_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        stream_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_loop()
        if not self.loop:
            raise RuntimeError("Failed to initialize asyncio loop")

        fut = asyncio.run_coroutine_threadsafe(
            self._start_async(
                model_id=model_id or self.config.model_id,
                params=params or {},
                request_id=request_id,
                stream_id=stream_id,
            ),
            self.loop,
        )
        return fut.result(timeout=30)

    def stop(self) -> Dict[str, Any]:
        if not self.loop:
            return {"running": False}
        fut = asyncio.run_coroutine_threadsafe(self._stop_async(), self.loop)
        try:
            fut.result(timeout=15)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Stop failed: %s", exc)
        return self.status()

    def status(self) -> Dict[str, Any]:
        info = self.job
        return {
            "running": self.running,
            "frames_sent": self.frames_sent,
            "publish_url": info.publish_url if info else "",
            "subscribe_url": info.subscribe_url if info else "",
            "control_url": info.control_url if info else "",
            "manifest_id": info.manifest_id if info else "",
            "model_id": self.config.model_id,
            "fps": self.config.fps,
            "frame_width": self.config.frame_width,
            "frame_height": self.config.frame_height,
        }

    async def _start_async(
        self,
        *,
        model_id: str,
        params: Dict[str, Any],
        request_id: Optional[str],
        stream_id: Optional[str],
    ) -> Dict[str, Any]:
        _ensure_allow_http_signer()
        await self._stop_async()
        assert self.loop

        orch_url = _require_https_orchestrator(self.config.orchestrator_url)
        LOGGER.info("Fetching orchestrator info for %s", self.config.orchestrator_url)
        info = GetOrchestratorInfo(
            orch_url,
            signer_url=self.config.signer_url,
            model_id=model_id,
        )
        LOGGER.info("Starting job model_id=%s", model_id)
        job = StartJob(
            info,
            StartJobRequest(
                model_id=model_id,
                params=params or None,
                request_id=request_id,
                stream_id=stream_id,
            ),
            signer_base_url=self.config.signer_url,
        )
        media = job.start_media(
            MediaPublishConfig(
                fps=self.config.fps,
                keyframe_interval_s=self.config.keyframe_interval_s,
            )
        )

        self.job = job
        self.media = media
        self.frames_sent = 0
        self.running = True
        self._stop_event = asyncio.Event()
        self._publisher_task = asyncio.create_task(self._publisher_loop(), name="network-publisher")
        LOGGER.info("NetworkController started publish_url=%s", job.publish_url)
        return self.status()

    async def _stop_async(self) -> None:
        self.running = False
        if self._stop_event:
            self._stop_event.set()
        if self._publisher_task:
            self._publisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._publisher_task
        if self.media:
            with contextlib.suppress(Exception):
                await self.media.close()
        if self.job:
            with contextlib.suppress(Exception):
                await self.job.close()
        self.job = None
        self.media = None
        self._publisher_task = None
        self._stop_event = None
        self.frames_sent = 0

    async def _publisher_loop(self) -> None:
        assert self.media
        pts = 0
        time_base = Fraction(1, int(round(self.config.fps)))
        while self.running and self._stop_event and not self._stop_event.is_set():
            frame = await FRAME_BRIDGE.queue.get()
            try:
                av_frame = self._to_av_frame(frame, pts, time_base)
                await self.media.write_frame(av_frame)
                self.frames_sent += 1
                pts += 1
            except Exception as exc:  # pragma: no cover - network/encode errors
                LOGGER.warning("Failed to publish frame: %s", exc)
        LOGGER.info("Publisher loop exit")

    def _to_av_frame(self, frame: np.ndarray, pts: int, time_base: Fraction) -> av.VideoFrame:
        rgb = normalize_uint8_frame(frame)
        return array_to_av_frame(
            rgb,
            pts=pts,
            fps=self.config.fps,
            width=self.config.frame_width,
            height=self.config.frame_height,
        )
