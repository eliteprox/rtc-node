"""
Helpers for reading and writing Daydream credentials using the ComfyUI settings file.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from threading import Lock
from typing import Dict

LOGGER = logging.getLogger("rtc_stream.credentials_store")
DEFAULT_API_URL = "https://api.daydream.live"
ENV_API_URL = "DAYDREAM_API_URL"
ENV_API_KEY = "DAYDREAM_API_KEY"
DEFAULT_ORCH_URL = "https://localhost:8935"
ENV_ORCH_URL = "ORCHESTRATOR_URL"
ENV_SIGNER_URL = "SIGNER_URL"

ROOT_DIR = Path(__file__).resolve().parent.parent
COMFY_ROOT = ROOT_DIR.parent.parent
_SETTINGS_PATH_OVERRIDE = os.environ.get("RTC_NODE_SETTINGS_PATH")
SETTINGS_PATH = (
    Path(_SETTINGS_PATH_OVERRIDE)
    if _SETTINGS_PATH_OVERRIDE
    else COMFY_ROOT / "user" / "default" / "comfy.settings.json"
)

SETTINGS_API_URL_KEY = "daydream_live.api_base_url"
SETTINGS_API_KEY_KEY = "daydream_live.api_key"
SETTINGS_ORCH_URL_KEY = "livepeer.orchestrator_url"
SETTINGS_SIGNER_URL_KEY = "livepeer.signer_url"

_SETTINGS_LOCK = Lock()


def _normalize_api_url(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return DEFAULT_API_URL
    return re.sub(r"/+$", "", candidate)


def _sanitize(value: str) -> str:
    return value.strip().replace("\n", "").replace("\r", "")


def _load_settings_dict() -> Dict[str, str]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to read settings from %s: %s", SETTINGS_PATH, exc)
        return {}


def _write_settings_dict(data: Dict[str, str]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)


def load_credentials_from_settings() -> Dict[str, Dict[str, str] | str]:
    """
    Load credentials from ComfyUI's settings file. Falls back to process env vars if missing
    to preserve CLI compatibility.
    """

    with _SETTINGS_LOCK:
        settings = _load_settings_dict()

    api_url = _normalize_api_url(settings.get(SETTINGS_API_URL_KEY))
    api_key = _sanitize(settings.get(SETTINGS_API_KEY_KEY, ""))

    url_source = "settings" if settings.get(SETTINGS_API_URL_KEY) else "default"
    key_source = "settings" if settings.get(SETTINGS_API_KEY_KEY) else "missing"

    if not api_url or api_url == DEFAULT_API_URL:
        env_url = os.environ.get(ENV_API_URL, "").strip()
        if env_url:
            api_url = _normalize_api_url(env_url)
            url_source = "env"

    if not api_key:
        env_key = os.environ.get(ENV_API_KEY, "").strip()
        if env_key:
            api_key = env_key
            key_source = "env"

    return {
        "api_url": api_url or DEFAULT_API_URL,
        "api_key": api_key,
        "sources": {"api_url": url_source, "api_key": key_source},
    }


def load_network_settings() -> Dict[str, Dict[str, str] | str]:
    """
    Load Livepeer network endpoints (orchestrator/signer) from ComfyUI settings,
    falling back to environment variables and sensible defaults.
    """
    with _SETTINGS_LOCK:
        settings = _load_settings_dict()

    orch_url = _normalize_api_url(settings.get(SETTINGS_ORCH_URL_KEY) or DEFAULT_ORCH_URL)
    signer_url = _sanitize(settings.get(SETTINGS_SIGNER_URL_KEY, ""))

    orch_source = "settings" if settings.get(SETTINGS_ORCH_URL_KEY) else "default"
    signer_source = "settings" if settings.get(SETTINGS_SIGNER_URL_KEY) else "missing"

    env_orch = os.environ.get(ENV_ORCH_URL, "").strip()
    if env_orch:
        orch_url = _normalize_api_url(env_orch)
        orch_source = "env"

    env_signer = os.environ.get(ENV_SIGNER_URL, "").strip()
    if env_signer:
        signer_url = _sanitize(env_signer)
        signer_source = "env"

    return {
        "orchestrator_url": orch_url or DEFAULT_ORCH_URL,
        "signer_url": signer_url,
        "sources": {"orchestrator_url": orch_source, "signer_url": signer_source},
    }


def persist_credentials_to_settings(
    api_url: str | None = None, api_key: str | None = None
) -> Dict[str, Dict[str, str] | str]:
    """
    Merge the provided credentials into the ComfyUI settings file. Used primarily by the
    fallback REST endpoint and tests; the settings UI writes values directly.
    """

    with _SETTINGS_LOCK:
        data = _load_settings_dict()

        if api_url is not None:
            cleaned_url = _normalize_api_url(api_url)
            if cleaned_url:
                data[SETTINGS_API_URL_KEY] = cleaned_url
            elif SETTINGS_API_URL_KEY in data:
                data.pop(SETTINGS_API_URL_KEY, None)

        if api_key is not None:
            cleaned_key = _sanitize(api_key)
            if cleaned_key:
                data[SETTINGS_API_KEY_KEY] = cleaned_key
            else:
                data.pop(SETTINGS_API_KEY_KEY, None)

        _write_settings_dict(data)

    return load_credentials_from_settings()


def persist_network_settings(
    orchestrator_url: str | None = None, signer_url: str | None = None
) -> Dict[str, Dict[str, str] | str]:
    """
    Persist Livepeer network endpoints into ComfyUI settings.
    """
    with _SETTINGS_LOCK:
        data = _load_settings_dict()

        if orchestrator_url is not None:
            cleaned = _normalize_api_url(orchestrator_url)
            if cleaned:
                data[SETTINGS_ORCH_URL_KEY] = cleaned
            elif SETTINGS_ORCH_URL_KEY in data:
                data.pop(SETTINGS_ORCH_URL_KEY, None)

        if signer_url is not None:
            cleaned = _sanitize(signer_url)
            if cleaned:
                data[SETTINGS_SIGNER_URL_KEY] = cleaned
            else:
                data.pop(SETTINGS_SIGNER_URL_KEY, None)

        _write_settings_dict(data)

    return load_network_settings()


# Backwards-compatible aliases for existing imports
def load_credentials_from_env() -> Dict[str, Dict[str, str] | str]:
    return load_credentials_from_settings()


def persist_credentials_to_env(
    api_url: str | None = None, api_key: str | None = None
) -> Dict[str, Dict[str, str] | str]:
    return persist_credentials_to_settings(api_url=api_url, api_key=api_key)


__all__ = [
    "load_credentials_from_settings",
    "persist_credentials_to_settings",
    "load_network_settings",
    "persist_network_settings",
    "load_credentials_from_env",
    "persist_credentials_to_env",
    "SETTINGS_PATH",
]

