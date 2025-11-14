import logging
from typing import Any, Dict

import torch

from rtc_stream.frame_bridge import enqueue_tensor_frame, has_loop


LOGGER = logging.getLogger("rtc_stream.nodes")


class RTCStreamFrameInput:
    """
    ComfyUI node that enqueues frame tensors into the streaming pipeline.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "optional": {
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "push_frame"
    CATEGORY = "RTC Stream"

    def push_frame(self, image: torch.Tensor, enabled: bool = True):
        if enabled and has_loop():
            enqueue_tensor_frame(image)
        elif enabled:
            LOGGER.warning("RTC stream server loop not ready; frame dropped")
        return (image,)


NODE_CLASS_MAPPINGS = {
    "RTCStreamFrameInput": RTCStreamFrameInput,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamFrameInput": "RTC Stream Frame Input",
}

