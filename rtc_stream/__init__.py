"""
RTC streaming package for ComfyUI custom nodes.

This package exposes helpers for pushing frames from custom nodes into
the streaming controller's shared queue.
"""

from .frame_bridge import FRAME_BRIDGE, enqueue_array_frame, enqueue_tensor_frame, has_loop  # noqa: F401

