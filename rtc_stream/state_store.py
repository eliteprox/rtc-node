"""
In-process state store for bridging ComfyUI custom nodes <-> browser BYOC-SDK RTC.

This replaces the old FastAPI + aiortc relay pipeline by keeping:
- Latest input frame (ComfyUI -> browser publisher)
- Latest output frame (browser viewer -> ComfyUI)
- Desired stream config/pipeline (nodes -> browser)
- Active session metadata (browser -> nodes/UI)

All access is thread-safe and intentionally minimal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional, Tuple


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class FrameSlot:
    frame_b64: str = ""
    updated_at_ms: int = 0
    sequence: int = 0
    mime: str = "image/png"


@dataclass
class StreamDesiredConfig:
    stream_name: str = "comfyui-livestream"
    pipeline: str = "comfystream"
    pipeline_config: Dict[str, Any] = field(default_factory=dict)
    width: int = 512
    height: int = 512
    fps: int = 30


@dataclass
class StreamSession:
    running: bool = False
    status: str = "disconnected"  # disconnected|connecting|connected|error
    error: str = ""
    stream_id: str = ""
    whip_url: str = ""
    whep_url: str = ""
    rtmp_url: str = ""
    playback_url: str = ""
    update_url: str = ""
    status_url: str = ""
    stop_url: str = ""
    data_url: str = ""
    started_at_ms: int = 0
    updated_at_ms: int = 0


class RtcStateStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._input = FrameSlot()
        self._output = FrameSlot()
        self._desired = StreamDesiredConfig()
        self._session = StreamSession()

    # ---------------------------------------------------------------------
    # Frames
    # ---------------------------------------------------------------------
    def set_input_frame(self, frame_b64: str, *, mime: str = "image/png") -> Dict[str, Any]:
        with self._lock:
            self._input.frame_b64 = frame_b64 or ""
            self._input.mime = mime or "image/png"
            self._input.sequence += 1
            self._input.updated_at_ms = _now_ms()
            return {
                "sequence": self._input.sequence,
                "updated_at_ms": self._input.updated_at_ms,
            }

    def get_input_frame(self) -> Tuple[str, Dict[str, Any], bool]:
        with self._lock:
            has_frame = bool(self._input.frame_b64)
            meta = {
                "sequence": self._input.sequence,
                "updated_at_ms": self._input.updated_at_ms,
                "mime": self._input.mime,
            }
            return self._input.frame_b64, meta, has_frame

    def set_output_frame(self, frame_b64: str, *, mime: str = "image/png") -> Dict[str, Any]:
        with self._lock:
            self._output.frame_b64 = frame_b64 or ""
            self._output.mime = mime or "image/png"
            self._output.sequence += 1
            self._output.updated_at_ms = _now_ms()
            return {
                "sequence": self._output.sequence,
                "updated_at_ms": self._output.updated_at_ms,
            }

    def get_output_frame(self) -> Tuple[str, Dict[str, Any], bool]:
        with self._lock:
            has_frame = bool(self._output.frame_b64)
            meta = {
                "sequence": self._output.sequence,
                "updated_at_ms": self._output.updated_at_ms,
                "mime": self._output.mime,
            }
            return self._output.frame_b64, meta, has_frame

    # ---------------------------------------------------------------------
    # Desired pipeline/config (nodes -> browser)
    # ---------------------------------------------------------------------
    def set_desired_config(
        self,
        *,
        stream_name: Optional[str] = None,
        pipeline: Optional[str] = None,
        pipeline_config: Optional[Dict[str, Any]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if stream_name is not None:
                self._desired.stream_name = (stream_name or "").strip() or "comfyui-livestream"
            if pipeline is not None:
                self._desired.pipeline = (pipeline or "").strip() or self._desired.pipeline
            if pipeline_config is not None:
                self._desired.pipeline_config = dict(pipeline_config)
            if width is not None:
                self._desired.width = int(width)
            if height is not None:
                self._desired.height = int(height)
            if fps is not None:
                self._desired.fps = int(fps)
            return self.get_desired_config()

    def get_desired_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "stream_name": self._desired.stream_name,
                "pipeline": self._desired.pipeline,
                "pipeline_config": dict(self._desired.pipeline_config),
                "width": self._desired.width,
                "height": self._desired.height,
                "fps": self._desired.fps,
            }

    # ---------------------------------------------------------------------
    # Session (browser -> nodes/UI)
    # ---------------------------------------------------------------------
    def update_session(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "running" in patch:
                self._session.running = bool(patch.get("running"))
            if "status" in patch and isinstance(patch.get("status"), str):
                self._session.status = patch.get("status") or self._session.status
            if "error" in patch and isinstance(patch.get("error"), str):
                self._session.error = patch.get("error") or ""

            # StreamStartResponse-ish
            for field_name, attr in (
                ("stream_id", "stream_id"),
                ("streamId", "stream_id"),
                ("whip_url", "whip_url"),
                ("whipUrl", "whip_url"),
                ("whep_url", "whep_url"),
                ("whepUrl", "whep_url"),
                ("rtmp_url", "rtmp_url"),
                ("rtmpUrl", "rtmp_url"),
                ("playback_url", "playback_url"),
                ("playbackUrl", "playback_url"),
                ("update_url", "update_url"),
                ("updateUrl", "update_url"),
                ("status_url", "status_url"),
                ("statusUrl", "status_url"),
                ("stop_url", "stop_url"),
                ("stopUrl", "stop_url"),
                ("data_url", "data_url"),
                ("dataUrl", "data_url"),
            ):
                value = patch.get(field_name)
                if isinstance(value, str) and value:
                    setattr(self._session, attr, value)

            if "started_at_ms" in patch:
                try:
                    self._session.started_at_ms = int(patch["started_at_ms"])
                except Exception:
                    pass
            if not self._session.started_at_ms and self._session.running:
                self._session.started_at_ms = _now_ms()

            self._session.updated_at_ms = _now_ms()
            return self.get_session()

    def clear_session(self, *, error: str = "") -> Dict[str, Any]:
        with self._lock:
            self._session = StreamSession()
            if error:
                self._session.status = "error"
                self._session.error = error
                self._session.updated_at_ms = _now_ms()
            return self.get_session()

    def get_session(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._session.running,
                "status": self._session.status,
                "error": self._session.error,
                "stream_id": self._session.stream_id,
                "whip_url": self._session.whip_url,
                "whep_url": self._session.whep_url,
                "rtmp_url": self._session.rtmp_url,
                "playback_url": self._session.playback_url,
                "update_url": self._session.update_url,
                "status_url": self._session.status_url,
                "stop_url": self._session.stop_url,
                "data_url": self._session.data_url,
                "started_at_ms": self._session.started_at_ms,
                "updated_at_ms": self._session.updated_at_ms,
            }


RTC_STATE = RtcStateStore()

