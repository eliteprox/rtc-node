import logging
import sys

from . import api, controlnet, frame_nodes, js, pipeline_config

LOGGER = logging.getLogger("rtc_stream.nodes")


def _configure_rtc_logging():
    base_logger = logging.getLogger("rtc_stream")
    has_handler = any(getattr(handler, "_rtc_stream_handler", False) for handler in base_logger.handlers)
    if not has_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[RTC] %(levelname)s %(message)s"))
        handler._rtc_stream_handler = True
        base_logger.addHandler(handler)
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = True


_configure_rtc_logging()


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

NODE_CLASS_MAPPINGS.update(frame_nodes.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(frame_nodes.NODE_DISPLAY_NAME_MAPPINGS)
NODE_CLASS_MAPPINGS.update(pipeline_config.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(pipeline_config.NODE_DISPLAY_NAME_MAPPINGS)
NODE_CLASS_MAPPINGS.update(controlnet.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(controlnet.NODE_DISPLAY_NAME_MAPPINGS)
NODE_CLASS_MAPPINGS.update(js.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(js.NODE_DISPLAY_NAME_MAPPINGS)

