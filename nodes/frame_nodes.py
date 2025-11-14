import logging
import random
from typing import Any, Dict

import torch

from rtc_stream.frame_bridge import enqueue_tensor_frame, has_loop, queue_depth


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
                "seed": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": 2**31 - 1,
                        "step": 1,
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "push_frame"
    CATEGORY = "RTC Stream"

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        seed = kwargs.get("seed", -1)
        if seed is None or int(seed) < 0:
            return True
        return False

    @staticmethod
    def _resolve_seed(seed: int) -> int:
        if seed is None or seed < 0:
            return random.randint(0, 2**31 - 1)
        return int(seed)

    def push_frame(self, image: torch.Tensor, enabled: bool = True, seed: int = -1):
        actual_seed = self._resolve_seed(seed)
        if enabled:
            enqueue_tensor_frame(image)
            LOGGER.debug(
                "RTC stream enqueued frame (loop_ready=%s depth=%s seed=%s)",
                has_loop(),
                queue_depth(),
                actual_seed,
            )
        return (image,)


NODE_CLASS_MAPPINGS = {
    "RTCStreamFrameInput": RTCStreamFrameInput,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RTCStreamFrameInput": "RTC Stream Frame Input",
}

