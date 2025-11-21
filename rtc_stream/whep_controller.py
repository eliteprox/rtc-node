import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests
from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from .whep_frame_bridge import WHEP_FRAME_BRIDGE


LOGGER = logging.getLogger("rtc_stream.whep_controller")


@dataclass
class WhepControllerConfig:
    frame_width: int = 1280
    frame_height: int = 720
    reconnect_backoff: float = 5.0
    request_timeout: int = 30


@dataclass
class WhepControllerState:
    whep_url: str = ""
    connected: bool = False
    connecting: bool = False
    connection_state: str = "idle"
    ice_state: str = "new"
    connected_at: float = 0.0
    frames_received: int = 0
    last_error: str = ""


class WhepController:
    def __init__(self, config: Optional[WhepControllerConfig] = None):
        self.config = config or WhepControllerConfig()
        self.state = WhepControllerState()
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self.pc: Optional[RTCPeerConnection] = None

    async def connect(self, whep_url: str) -> Dict[str, object]:
        normalized = (whep_url or "").strip()
        if not normalized:
            raise ValueError("whep_url is required")

        async with self._lock:
            if normalized == self.state.whep_url and (self.state.connected or self.state.connecting):
                LOGGER.info("Already connected/connecting to %s", normalized)
                return self.status()

            await self._disconnect_locked(reason="Switching WHEP URL")
            self.state.whep_url = normalized
            self.state.connecting = True
            self.state.connection_state = "connecting"
            self.state.last_error = ""
            self.state.frames_received = 0
            self._task = asyncio.create_task(self._run_subscription(normalized))

        return self.status()

    async def disconnect(self, reason: str = "Manual disconnect") -> Dict[str, object]:
        async with self._lock:
            await self._disconnect_locked(reason=reason)
            return self.status()

    async def _disconnect_locked(self, reason: str = "") -> None:
        current_task = asyncio.current_task()
        if self._task and self._task is not current_task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self.pc:
            await self.pc.close()
            self.pc = None

        await WHEP_FRAME_BRIDGE.reset()
        self.state.connected = False
        self.state.connecting = False
        self.state.connection_state = "idle"
        self.state.ice_state = "new"
        self.state.connected_at = 0.0
        self.state.frames_received = 0
        if reason:
            self.state.last_error = reason

    def status(self) -> Dict[str, object]:
        return {
            "whep_url": self.state.whep_url,
            "connected": self.state.connected,
            "connecting": self.state.connecting,
            "connection_state": self.state.connection_state,
            "ice_state": self.state.ice_state,
            "connected_at": self.state.connected_at,
            "frames_received": self.state.frames_received,
            "last_error": self.state.last_error,
        }

    async def _run_subscription(self, whep_url: str) -> None:
        try:
            await self._establish_connection(whep_url)
        except asyncio.CancelledError:
            LOGGER.debug("WHEP subscription task cancelled")
            raise
        except Exception as exc:  # pragma: no cover - network heavy
            LOGGER.error("WHEP subscription failed: %s", exc)
            async with self._lock:
                self.state.last_error = str(exc)
                self.state.connecting = False
                self.state.connected = False
                self.state.connection_state = "error"
        finally:
            async with self._lock:
                if not self.state.connected:
                    await self._disconnect_locked(reason=self.state.last_error or "Subscription ended")

    async def _establish_connection(self, whep_url: str) -> None:
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
        pc.addTransceiver("video", direction="recvonly")

        @pc.on("track")
        async def _on_track(track: MediaStreamTrack):
            LOGGER.info("WHEP subscriber received track kind=%s", track.kind)
            if track.kind == "video":
                asyncio.create_task(self._consume_video_track(track))

        @pc.on("connectionstatechange")
        async def _on_connection_state_change():
            async with self._lock:
                self.state.connection_state = pc.connectionState or "unknown"
                LOGGER.info("WHEP connection state -> %s", self.state.connection_state)
                if self.state.connection_state in {"failed", "closed"}:
                    self.state.connected = False

        @pc.on("iceconnectionstatechange")
        async def _on_ice_state_change():
            async with self._lock:
                ice_state = pc.iceConnectionState or "unknown"
                self.state.ice_state = ice_state
                LOGGER.info("WHEP ICE state -> %s", ice_state)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        loop = asyncio.get_running_loop()

        def _post_offer() -> str:
            LOGGER.info("Posting WHEP offer to %s", whep_url)
            response = requests.post(
                whep_url,
                headers={"Content-Type": "application/sdp"},
                data=offer.sdp,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            return response.text

        answer_sdp = await loop.run_in_executor(None, _post_offer)
        answer = RTCSessionDescription(sdp=answer_sdp, type="answer")
        await pc.setRemoteDescription(answer)

        async with self._lock:
            self.state.connected = True
            self.state.connecting = False
            self.state.connection_state = pc.connectionState or "connected"
            self.state.connected_at = time.time()
            LOGGER.info("WHEP subscription established")

        try:
            while True:
                await asyncio.sleep(1)
                if pc.connectionState in {"failed", "closed"}:
                    raise RuntimeError(f"Peer connection closed ({pc.connectionState})")
        finally:
            await pc.close()
            self.pc = None

    async def _consume_video_track(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                frame = await track.recv()
                np_frame = frame.to_ndarray(format="rgb24")
                await WHEP_FRAME_BRIDGE.put_frame(np_frame)
                async with self._lock:
                    self.state.frames_received += 1
        except asyncio.CancelledError:
            LOGGER.debug("Video track consumer cancelled")
            raise
        except Exception as exc:  # pragma: no cover - network/IO heavy
            LOGGER.warning("Video track consumer stopped: %s", exc)
            async with self._lock:
                self.state.connection_state = "error"
                self.state.last_error = str(exc)
        finally:
            await track.stop()


