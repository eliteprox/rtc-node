import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import requests
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from .daydream import StreamInfo, poll_stream_status, resolve_credentials, start_stream
from .frame_bridge import FRAME_BRIDGE


LOGGER = logging.getLogger("rtc_stream.controller")


@dataclass
class ControllerConfig:
    api_url: str
    api_key: str
    pipeline_path: Path
    video_file: Optional[Path] = None
    max_duration: int = 3600


@dataclass
class ControllerState:
    info: Optional[StreamInfo] = None
    remote_status: Dict[str, Any] = field(default_factory=dict)
    last_remote_check: float = 0.0
    frames_sent: int = 0
    started_at: float = 0.0
    running: bool = False


class FrameQueueTrack(VideoStreamTrack):
    def __init__(self, bridge: Any, fallback_video: Optional[Path], frame_rate: float = 30.0):
        super().__init__()
        self.bridge = bridge
        self.fallback_video = fallback_video
        self.frame_rate = frame_rate
        self._frame_interval = 1.0 / frame_rate
        self._pts = 0
        self._time_base = Fraction(1, int(round(frame_rate)))
        self._dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self._last_sent = 0.0
        if fallback_video:
            import av  # local import to avoid circular dependency

            self.container = av.open(str(fallback_video))
            self.stream = self.container.streams.video[0]
            if self.stream.average_rate:
                self.frame_rate = float(self.stream.average_rate)
                self._frame_interval = 1.0 / self.frame_rate
            self._frame_iter = self.container.decode(self.stream)
        else:
            self.container = None
            self.stream = None
            self._frame_iter = None

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(self._frame_interval)
        frame_array = self.bridge.try_get_nowait()
        if frame_array is not None:
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
        elif self.container is not None:
            try:
                decoded = next(self._frame_iter)
            except StopIteration:
                self.container.seek(0)
                self._frame_iter = self.container.decode(self.stream)
                decoded = next(self._frame_iter)
            decoded = decoded.reformat(format="yuv420p")
            decoded.pts = self._pts
            decoded.time_base = self._time_base
            self._pts += 1
            return decoded
        else:
            frame = VideoFrame.from_ndarray(self._dummy_frame, format="rgb24")

        frame = frame.reformat(format="yuv420p")
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += 1
        self._last_sent = time.time()
        LOGGER.info("FrameQueueTrack sent frame pts=%s", frame.pts)
        return frame


class StreamController:
    def __init__(self, config: ControllerConfig):
        self.config = config
        self.state = ControllerState()
        self.pc: Optional[RTCPeerConnection] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._session = None

    def load_pipeline_config(self, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if override:
            return override
        with open(self.config.pipeline_path, "r", encoding="utf-8") as fp:
            return json.load(fp)

    async def start(self, stream_name: str = "", pipeline_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._lock:
            if self._task:
                return self.status()

            loop = asyncio.get_running_loop()
            FRAME_BRIDGE.attach_loop(loop)

            api_url, api_key = resolve_credentials(self.config.api_url, self.config.api_key)
            pipeline_payload = await loop.run_in_executor(None, self.load_pipeline_config, pipeline_override)
            info = await loop.run_in_executor(
                None,
                lambda: start_stream(
                    api_url=api_url,
                    api_key=api_key,
                    pipeline_config=pipeline_payload,
                    stream_name=stream_name,
                ),
            )
            LOGGER.info("Waiting for Daydream stream %s status...", info.stream_id)
            status_payload = await loop.run_in_executor(
                None,
                lambda: poll_stream_status(api_url=api_url, api_key=api_key, stream_id=info.stream_id),
            )

            self.state.info = info
            self.state.remote_status = status_payload or {}
            self.state.last_remote_check = time.time()
            self.state.running = True
            self.state.started_at = time.time()
            self._task = asyncio.create_task(self._run_session(info))
            return self.status()

    async def stop(self) -> Dict[str, Any]:
        async with self._lock:
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            if self.pc:
                await self.pc.close()
                self.pc = None
            self.state.running = False
            return self.status()

    def status(self) -> Dict[str, Any]:
        info = self.state.info
        return {
            "running": self.state.running,
            "frames_sent": self.state.frames_sent,
            "stream_id": info.stream_id if info else "",
            "playback_id": info.playback_id if info else "",
            "whip_url": info.whip_url if info else "",
            "started_at": self.state.started_at,
            "remote_status": self.state.remote_status,
        }

    async def status_async(self, refresh_remote: bool = False) -> Dict[str, Any]:
        if refresh_remote:
            await self._refresh_remote_status()
        return self.status()

    async def _refresh_remote_status(self) -> None:
        info = self.state.info
        if not info:
            return
        now = time.time()
        if now - self.state.last_remote_check < 3:
            return
        api_url, api_key = resolve_credentials(self.config.api_url, self.config.api_key)
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(
            None,
            lambda: poll_stream_status(api_url=api_url, api_key=api_key, stream_id=info.stream_id),
        )
        self.state.remote_status = payload or {}
        self.state.last_remote_check = time.time()

    def enqueue_frame(self, frame: np.ndarray) -> None:
        FRAME_BRIDGE.enqueue(frame)

    async def _run_session(self, info: StreamInfo) -> None:
        pc = RTCPeerConnection()
        self.pc = pc
        track = FrameQueueTrack(
            FRAME_BRIDGE,
            fallback_video=self.config.video_file,
        )
        pc.addTrack(track)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        loop = asyncio.get_running_loop()
        def _post_offer() -> str:
            response = requests.post(
                info.whip_url,
                headers={"Content-Type": "application/sdp"},
                data=offer.sdp,
                timeout=30,
            )
            response.raise_for_status()
            return response.text

        response_text = await loop.run_in_executor(None, _post_offer)
        answer = RTCSessionDescription(sdp=response_text, type="answer")
        await pc.setRemoteDescription(answer)

        LOGGER.info("StreamController established WHIP session")

        try:
            while True:
                await asyncio.sleep(1)
                self.state.frames_sent = track._pts
        finally:
            await pc.close()
            self.state.running = False

