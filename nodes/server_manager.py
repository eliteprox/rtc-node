"""
Legacy Daydream/WHIP/WHEP server manager removed.
This stub remains for backward compatibility; all functions are no-ops.
"""

import logging
from typing import Optional

LOGGER = logging.getLogger("rtc_stream.api_server_manager")


def ensure_server_running(host_override: Optional[str] = None, port_override: Optional[int] = None) -> bool:
    LOGGER.debug(
        "ensure_server_running() no-op (legacy server removed), host=%s port=%s",
        host_override,
        port_override,
    )
    return False


def stop_server() -> bool:
    LOGGER.debug("stop_server() no-op (legacy server removed)")
    return False


def server_status() -> dict:
    return {"running": False, "host": None, "port": None, "adopted": False}
