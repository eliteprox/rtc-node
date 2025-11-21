import json
import logging
from pathlib import Path
from typing import Optional, Tuple


LOGGER = logging.getLogger("rtc_stream.local_api")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8895

ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_DIR = ROOT_DIR / "settings"
STATE_PATH = SETTINGS_DIR / "local_api_server_state.json"
SETTINGS_PATH = SETTINGS_DIR / "rtc_stream_settings.json"

_SERVER_BASE_CACHE: Optional[str] = None
_STATE_MTIME: Optional[float] = None
_SETTINGS_MTIME: Optional[float] = None


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - diagnostics only
        LOGGER.debug("Failed to read %s: %s", path, exc)
        return None


def _load_server_host_port_from_state() -> Optional[Tuple[str, int]]:
    data = _read_json(STATE_PATH)
    if not data:
        return None
    host = (data.get("host") or DEFAULT_HOST).strip()
    port = int(data.get("port") or DEFAULT_PORT)
    return host, port


def _load_server_host_port_from_settings() -> Optional[Tuple[str, int]]:
    data = _read_json(SETTINGS_PATH)
    if not data:
        return None
    host = (data.get("host") or DEFAULT_HOST).strip()
    port = int(data.get("port") or DEFAULT_PORT)
    return host, port


def resolve_server_base() -> str:
    """
    Determine the local API server's base URL using the state file if it exists,
    falling back to the persisted settings. The result is cached and invalidated
    automatically when either file changes.
    """

    global _SERVER_BASE_CACHE, _STATE_MTIME, _SETTINGS_MTIME

    state_mtime = STATE_PATH.stat().st_mtime if STATE_PATH.exists() else None
    settings_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else None

    if (
        _SERVER_BASE_CACHE
        and _STATE_MTIME == state_mtime
        and _SETTINGS_MTIME == settings_mtime
    ):
        return _SERVER_BASE_CACHE

    host_port = _load_server_host_port_from_state() or _load_server_host_port_from_settings()
    if not host_port:
        host_port = (DEFAULT_HOST, DEFAULT_PORT)

    host, port = host_port
    base = f"http://{host}:{port}"

    _SERVER_BASE_CACHE = base
    _STATE_MTIME = state_mtime
    _SETTINGS_MTIME = settings_mtime
    return base


def build_local_api_url(path: str) -> str:
    """
    Construct a fully-qualified URL for the local API server by combining the
    resolved base with the provided path segment.
    """

    base = resolve_server_base().rstrip("/")
    suffix = path.lstrip("/")
    return f"{base}/{suffix}"


__all__ = ["resolve_server_base", "build_local_api_url"]

