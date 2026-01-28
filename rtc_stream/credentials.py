"""
Shared helpers for resolving Daydream API credentials.

All components (server manager, controller, CLI scripts, etc.) should use
``resolve_credentials`` so we only have one place that knows how to pull values
from the environment or .env files.
"""

from __future__ import annotations

import os
from typing import Tuple

from .credentials_store import load_credentials_from_settings, load_network_settings

DEFAULT_API_URL = "https://api.daydream.live"
ENV_API_URL = "DAYDREAM_API_URL"
ENV_API_KEY = "DAYDREAM_API_KEY"

DEFAULT_ORCH_URL = "https://hky.eliteencoder.net:8936"
DEFAULT_SIGNER_URL = "http://localhost:8081"
ENV_ORCH_URL = "ORCHESTRATOR_URL"
ENV_SIGNER_URL = "SIGNER_URL"


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


def resolve_network_config(
    orchestrator_url: str = "",
    signer_url: str = "",
) -> Tuple[str, str]:
    """
    Resolve orchestrator and signer endpoints for the Livepeer network.
    Preference order:
    1. Explicit parameters supplied by caller.
    2. Values stored in ComfyUI settings.
    3. Process environment variables.
    4. Default orchestrator URL (signer is optional).
    """

    state = load_network_settings()
    settings_orch = (state.get("orchestrator_url") or "").strip()
    settings_signer = (state.get("signer_url") or "").strip()

    resolved_orch = (orchestrator_url or settings_orch or os.environ.get(ENV_ORCH_URL, DEFAULT_ORCH_URL)).strip()
    resolved_signer = (
        signer_url or settings_signer or os.environ.get(ENV_SIGNER_URL, DEFAULT_SIGNER_URL)
    ).strip()

    if not resolved_orch:
        raise ValueError(
            "Orchestrator URL is missing. Set ORCHESTRATOR_URL or pass orchestrator_url."
        )

    return resolved_orch, resolved_signer


__all__ = ["resolve_credentials", "resolve_network_config"]

