"""
Shared helpers for resolving Daydream API credentials.

All components (server manager, controller, CLI scripts, etc.) should use
``resolve_credentials`` so we only have one place that knows how to pull values
from the environment or .env files.
"""

from __future__ import annotations

import os
from typing import Tuple

from .credentials_store import load_credentials_from_settings

DEFAULT_API_URL = "https://api.daydream.live"
ENV_API_URL = "DAYDREAM_API_URL"
ENV_API_KEY = "DAYDREAM_API_KEY"


def resolve_credentials(api_url: str = "", api_key: str = "") -> Tuple[str, str]:
    """
    Resolve Daydream API credentials.

    Preference order:
    1. Explicit parameters supplied by caller.
    2. Values stored in ComfyUI settings.
    3. Process environment variables.
    4. Default API URL (key has no default and must be supplied).
    """

    state = load_credentials_from_settings()
    settings_url = (state.get("api_url") or "").strip()
    settings_key = (state.get("api_key") or "").strip()

    resolved_url = (api_url or settings_url or os.environ.get(ENV_API_URL, DEFAULT_API_URL)).strip()
    resolved_key = (api_key or settings_key or os.environ.get(ENV_API_KEY, "")).strip()

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

