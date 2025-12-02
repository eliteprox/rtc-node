"""
Helpers for reading and writing Daydream credentials in the local .env file.

Used by the ComfyUI settings dialog so that API credentials live in one place.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from threading import Lock
from typing import Dict, List, Tuple

from .credentials import DEFAULT_API_URL, ENV_API_KEY, ENV_API_URL

ROOT_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH_OVERRIDE = os.environ.get("RTC_NODE_ENV_PATH")
DOTENV_PATH = Path(_ENV_PATH_OVERRIDE) if _ENV_PATH_OVERRIDE else ROOT_DIR / ".env"

_HEADER_LINES = [
    "# rtc-node environment configuration",
    "# Managed automatically via the DayDream settings dialog.",
    "",
]

_ENV_LOCK = Lock()


def _read_lines() -> List[str]:
    if DOTENV_PATH.exists():
        with open(DOTENV_PATH, "r", encoding="utf-8") as fp:
            return fp.read().splitlines()
    return []


def _write_lines(lines: List[str]) -> None:
    DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    with open(DOTENV_PATH, "w", encoding="utf-8") as fp:
        fp.write(text)


def _parse_assignment(line: str) -> Tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value


def _apply_updates(
    lines: List[str], new_values: Dict[str, str], removals: set[str]
) -> List[str]:
    updated = []
    touched = set()
    for raw_line in lines:
        parsed = _parse_assignment(raw_line)
        if not parsed:
            updated.append(raw_line)
            continue
        key, _ = parsed
        if key in removals:
            touched.add(key)
            continue
        if key in new_values:
            updated.append(f"{key}={new_values[key]}")
            touched.add(key)
        else:
            updated.append(raw_line)
    for key, value in new_values.items():
        if key not in touched:
            updated.append(f"{key}={value}")
    return updated


def _normalize_api_url(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return DEFAULT_API_URL
    return re.sub(r"/+$", "", candidate)


def _sanitize(value: str) -> str:
    return value.strip().replace("\n", "").replace("\r", "")


def _build_state(lines: List[str]) -> Dict[str, str]:
    state: Dict[str, str] = {}
    for raw_line in lines:
        parsed = _parse_assignment(raw_line)
        if not parsed:
            continue
        key, value = parsed
        state[key] = value
    return state


def load_credentials_from_env() -> Dict[str, Dict[str, str] | str]:
    """
    Load credentials from .env (preferred), falling back to os.environ/defaults.
    Returns the resolved values plus their sources for the UI.
    """

    with _ENV_LOCK:
        lines = _read_lines()
    state = _build_state(lines)

    url_source = "default"
    api_url = DEFAULT_API_URL
    if ENV_API_URL in state:
        api_url = _normalize_api_url(state[ENV_API_URL])
        url_source = "file"
    else:
        env_value = os.environ.get(ENV_API_URL, "").strip()
        if env_value:
            api_url = _normalize_api_url(env_value)
            url_source = "env"

    key_source = "missing"
    api_key = ""
    if ENV_API_KEY in state:
        api_key = _sanitize(state[ENV_API_KEY])
        key_source = "file"
    else:
        env_key = os.environ.get(ENV_API_KEY, "").strip()
        if env_key:
            api_key = env_key
            key_source = "env"

    return {
        "api_url": api_url,
        "api_key": api_key,
        "sources": {"api_url": url_source, "api_key": key_source},
    }


def persist_credentials_to_env(
    api_url: str | None = None, api_key: str | None = None
) -> Dict[str, Dict[str, str] | str]:
    """
    Merge the provided credentials into the .env file (creating it if missing).
    Empty api_key removes the key from the file.
    """

    new_values: Dict[str, str] = {}
    removals: set[str] = set()

    if api_url is not None:
        new_values[ENV_API_URL] = _normalize_api_url(api_url)

    if api_key is not None:
        cleaned = _sanitize(api_key)
        if cleaned:
            new_values[ENV_API_KEY] = cleaned
        else:
            removals.add(ENV_API_KEY)

    if not new_values and not removals:
        return load_credentials_from_env()

    with _ENV_LOCK:
        lines = _read_lines()
        if not lines:
            lines = list(_HEADER_LINES)
        lines = _apply_updates(lines, new_values, removals)
        _write_lines(lines)

    for key, value in new_values.items():
        os.environ[key] = value
    for key in removals:
        os.environ.pop(key, None)

    return load_credentials_from_env()


__all__ = ["load_credentials_from_env", "persist_credentials_to_env", "DOTENV_PATH"]

