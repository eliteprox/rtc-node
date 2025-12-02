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
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from .daydream import StreamInfo, poll_stream_status, resolve_credentials, start_stream, update_stream
from .frame_bridge import FRAME_BRIDGE, FolderFrameSource


LOGGER = logging.getLogger("rtc_stream.controller")


@dataclass
class ControllerConfig:
    api_url: str
    api_key: str
    pipeline_path: Path
    video_file: Optional[Path] = None
    max_duration: int = 3600
    frame_rate: float = 30.0
    frame_width: int = 1280
    frame_height: int = 720


@dataclass
class ControllerState:
    info: Optional[StreamInfo] = None
    remote_status: Dict[str, Any] = field(default_factory=dict)
    last_remote_check: float = 0.0
    frames_sent: int = 0
    started_at: float = 0.0
    running: bool = False


class FrameQueueTrack(VideoStreamTrack):
    def __init__(
        self,
        bridge: Any,
        fallback_video: Optional[Path],
        frame_rate: float = 30.0,
        frame_width: int = 1280,
        frame_height: int = 720,
    ):
        super().__init__()
        self.bridge = bridge
        self.fallback_video = fallback_video
        self.frame_rate = frame_rate
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._frame_interval = 1.0 / frame_rate
        self._pts = 0
        self._time_base = Fraction(1, int(round(frame_rate)))
        self._dummy_frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        self.folder_source = FolderFrameSource()
        self._last_sent = 0.0
        self._last_source = "none"
        self._last_live_frame: Optional[np.ndarray] = None
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

    def _log_source_change(self, source: str) -> None:
        if source != self._last_source:
            LOGGER.info("FrameQueueTrack source -> %s", source)
            self._last_source = source

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(self._frame_interval)
        frame = self.bridge.try_get_nowait()
        if frame is not None:
            self._last_live_frame = frame.copy()
            image = frame[:, :, ::-1]
            source = "queue"
        elif self._last_live_frame is not None:
            image = self._last_live_frame[:, :, ::-1]
            source = "queue_cached"
        elif self.container is not None:
            try:
                decoded = next(self._frame_iter)
            except StopIteration:
                self.container.seek(0)
                self._frame_iter = self.container.decode(self.stream)
                decoded = next(self._frame_iter)
            image = decoded.to_ndarray(format="bgr24")
            source = "fallback_video"
        else:
            folder_frame = self.folder_source.next_frame()
            if folder_frame is not None:
                image = folder_frame[:, :, ::-1]
                source = "fallback_folder"
            else:
                image = self._dummy_frame[:, :, ::-1]
                source = "fallback_dummy"

        self._log_source_change(source)

        frame = VideoFrame.from_ndarray(image, format="bgr24")
        frame = frame.reformat(
            width=self.frame_width,
            height=self.frame_height,
            format="yuv420p",
        )
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

    def cache_pipeline_config(self, pipeline_config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(pipeline_config, dict):
            raise ValueError("pipeline_config must be a dict")
        payload = dict(pipeline_config)
        path = self.config.pipeline_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
        LOGGER.info("Cached pipeline config at %s", path)
        return payload

    def _set_phase_status(self, phase: str, detail: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "phase": phase,
            "detail": detail,
            "timestamp": time.time(),
        }
        if extra:
            payload.update(extra)
        self.state.remote_status = payload

    async def start(self, stream_name: str = "", pipeline_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._lock:
            if self._task or self.state.running:
                LOGGER.info("Existing stream detected; stopping before starting a new session")
                await self._stop_locked(reason="Restarting stream")

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
            self._set_phase_status(
                "STREAM_CREATED",
                detail="Daydream stream created",
                extra={"stream_id": info.stream_id},
            )
            self.state.info = info
            self.state.running = True
            self.state.started_at = time.time()
            self._task = asyncio.create_task(self._run_session(info))
            asyncio.create_task(
                self._initial_remote_poll(api_url=api_url, api_key=api_key, stream_id=info.stream_id)
            )
            return self.status()

    async def stop(self) -> Dict[str, Any]:
        async with self._lock:
            await self._stop_locked()
            return self.status()

    async def _stop_locked(self, reason: str = "Stream stopped") -> None:
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
        self.state.frames_sent = 0
        self.state.started_at = 0.0
        self.state.info = None
        self.state.remote_status = {}
        self.state.last_remote_check = 0.0
        self._set_phase_status("STOPPED", detail=reason)

    def status(self) -> Dict[str, Any]:
        info = self.state.info
        queue_stats = FRAME_BRIDGE.stats()
        return {
            "running": self.state.running,
            "frames_sent": self.state.frames_sent,
            "stream_id": info.stream_id if info else "",
            "playback_id": info.playback_id if info else "",
            "whip_url": info.whip_url if info else "",
            "started_at": self.state.started_at,
            "remote_status": self.state.remote_status,
            "queue_depth": queue_stats["depth"],
            "queue_stats": queue_stats,
            "stream_settings": {
                "frame_rate": self.config.frame_rate,
                "frame_width": self.config.frame_width,
                "frame_height": self.config.frame_height,
            },
        }

    async def status_async(self, refresh_remote: bool = False) -> Dict[str, Any]:
        if refresh_remote:
            await self._refresh_remote_status()
        return self.status()

    async def _initial_remote_poll(self, api_url: str, api_key: str, stream_id: str) -> None:
        """Poll immediately after stream creation to get initial gateway status."""
        loop = asyncio.get_running_loop()
        LOGGER.info("Fetching initial Daydream stream %s status...", stream_id)
        payload = await loop.run_in_executor(
            None,
            lambda: poll_stream_status(api_url=api_url, api_key=api_key, stream_id=stream_id),
        )
        if payload:
            async with self._lock:
                self.state.remote_status = {"phase": "REMOTE_STATUS", **payload}
                self.state.last_remote_check = time.time()

    async def _poll_remote_status_loop(self, api_url: str, api_key: str, stream_id: str) -> None:
        """
        Continuously poll Daydream API to keep gateway status (including whep_url) updated.
        Runs in background after WHIP is established.
        """
        LOGGER.info("Starting background status polling for stream %s", stream_id)
        # Poll immediately once, then every 5 seconds
        await asyncio.sleep(0.5)  # Brief delay to let WHIP settle
        
        while True:
            try:
                loop = asyncio.get_running_loop()
                payload = await loop.run_in_executor(
                    None,
                    lambda: poll_stream_status(api_url=api_url, api_key=api_key, stream_id=stream_id),
                )
                if payload:
                    async with self._lock:
                        self.state.remote_status = {"phase": "REMOTE_STATUS", **payload}
                        self.state.last_remote_check = time.time()
                    LOGGER.debug("Background poll updated remote status for stream %s", stream_id)
            except asyncio.CancelledError:
                LOGGER.info("Background status polling cancelled for stream %s", stream_id)
                raise
            except Exception as exc:
                LOGGER.warning("Background remote poll failed for stream %s: %s", stream_id, exc)
            
            await asyncio.sleep(5.0)

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
        if payload:
            self.state.remote_status = {"phase": "REMOTE_STATUS", **payload}
        self.state.last_remote_check = time.time()

    def enqueue_frame(self, frame: np.ndarray) -> None:
        FRAME_BRIDGE.enqueue(frame)
        LOGGER.debug("Controller enqueue_frame depth=%s", FRAME_BRIDGE.depth())

    def update_stream_settings(self, settings: Dict[str, Any]) -> None:
        frame_rate = settings.get("frame_rate")
        frame_width = settings.get("frame_width")
        frame_height = settings.get("frame_height")
        if frame_rate is not None:
            try:
                self.config.frame_rate = float(frame_rate)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid frame_rate provided: %s", frame_rate)
        if frame_width is not None:
            try:
                self.config.frame_width = int(frame_width)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid frame_width provided: %s", frame_width)
        if frame_height is not None:
            try:
                self.config.frame_height = int(frame_height)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid frame_height provided: %s", frame_height)

    async def update_pipeline(self, pipeline_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update pipeline configuration for the running stream.
        This forwards the update to Daydream API without restarting the stream.
        """
        async with self._lock:
            if not self.state.running or not self.state.info:
                raise ValueError("No active stream to update")

            api_url, api_key = resolve_credentials(self.config.api_url, self.config.api_key)
            stream_id = self.state.info.stream_id

            loop = asyncio.get_running_loop()

            result = await loop.run_in_executor(
                None,
                lambda: update_stream(
                    api_url=api_url,
                    api_key=api_key,
                    stream_id=stream_id,
                    pipeline_config=pipeline_config,
                ),
            )

            self._set_phase_status(
                "PIPELINE_UPDATED",
                detail="Pipeline parameters updated",
                extra={"stream_id": stream_id},
            )

            return result

    async def _run_session(self, info: StreamInfo) -> None:
        LOGGER.info("Initiating RTC connection to WHIP URL: %s", info.whip_url)
        config = RTCConfiguration(
            iceServers=[
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun.cloudflare.com:3478"]),
                RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun3.l.google.com:19302"]),
            ]
        )
        pc = RTCPeerConnection(configuration=config)
        self.pc = pc
        track = FrameQueueTrack(
            FRAME_BRIDGE,
            fallback_video=self.config.video_file,
            frame_rate=self.config.frame_rate,
            frame_width=self.config.frame_width,
            frame_height=self.config.frame_height,
        )
        pc.addTrack(track)

        @pc.on("iceconnectionstatechange")
        async def _on_ice_state_change():
            state = pc.iceConnectionState or "unknown"
            LOGGER.info("ICE connection state -> %s", state)
            self._set_phase_status(f"ICE_{state.upper()}", detail="ICE connection state change")

        @pc.on("connectionstatechange")
        async def _on_conn_state_change():
            state = pc.connectionState or "unknown"
            LOGGER.info("Peer connection state -> %s", state)
            self._set_phase_status(f"PEER_{state.upper()}", detail="Peer connection state change")

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        self._set_phase_status("WHIP_OFFER", detail="Sending SDP offer to Daydream gateway")

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

        LOGGER.info("Sending WHIP offer to %s", info.whip_url)
        response_text = await loop.run_in_executor(None, _post_offer)
        self._set_phase_status("WHIP_ANSWER", detail="Received SDP answer from Daydream gateway")
        LOGGER.info("Received WHIP answer from Daydream gateway")
        answer = RTCSessionDescription(sdp=response_text, type="answer")
        await pc.setRemoteDescription(answer)

        LOGGER.info("StreamController established WHIP session")
        self._set_phase_status("WHIP_ESTABLISHED", detail="WHIP session established")

        # Start background status polling after WHIP is established
        api_url, api_key = resolve_credentials(self.config.api_url, self.config.api_key)
        poll_task = asyncio.create_task(
            self._poll_remote_status_loop(api_url, api_key, info.stream_id)
        )

        try:
            while True:
                await asyncio.sleep(1)
                self.state.frames_sent = track._pts
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            await pc.close()
            self.state.running = False

