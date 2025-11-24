"""
Shared helpers for resolving Daydream API credentials.

All components (server manager, controller, CLI scripts, etc.) should use
``resolve_credentials`` so we only have one place that knows how to pull values
from the environment or .env files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple


DEFAULT_API_URL = "https://api.daydream.live"
ENV_API_URL = "DAYDREAM_API_URL"
ENV_API_KEY = "DAYDREAM_API_KEY"

_DOTENV_LOADED = False


def _load_dotenv_if_available() -> None:
    """Load the project's .env file once if python-dotenv is installed."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return

    root_dir = Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    load_dotenv(env_path)


def resolve_credentials(api_url: str = "", api_key: str = "") -> Tuple[str, str]:
    """
    Resolve Daydream API credentials.

    Preference order:
    1. Explicit parameters supplied by caller.
    2. Environment variables / values loaded from .env.
    3. Default API URL (key has no default and must be supplied).
    """

    _load_dotenv_if_available()

    resolved_url = (api_url or os.environ.get(ENV_API_URL, DEFAULT_API_URL)).strip()
    resolved_key = (api_key or os.environ.get(ENV_API_KEY, "")).strip()

    if not resolved_url:
        raise ValueError(
            "Daydream API URL is missing. Set DAYDREAM_API_URL or pass api_url."
        )

    if not resolved_key:
        raise ValueError(
            "Daydream API key is missing. Set DAYDREAM_API_KEY or pass api_key."
        )

    return resolved_url, resolved_key


__all__ = ["resolve_credentials"]

