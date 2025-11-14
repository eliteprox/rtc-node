"""
Lightweight settings storage for local API server configuration.
"""

import json
import logging
import os
import threading
from pathlib import Path


LOGGER = logging.getLogger("rtc_stream.settings")

LEGACY_PORT = 8890
DEFAULT_PORT = 8895

DEFAULT_SETTINGS = {
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    "pipeline_config": "pipeline_config.json",
    "video_file": "",
}

_settings_lock = threading.Lock()


def _settings_dir() -> Path:
    base = Path(__file__).parent.parent / "settings"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _settings_path() -> Path:
    return _settings_dir() / "rtc_stream_settings.json"


def load_settings() -> dict:
    path = _settings_path()
    with _settings_lock:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
            except Exception as exc:
                LOGGER.error("Failed to load settings: %s", exc)
                data = {}
        else:
            data = {}

        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)
        if merged.get("port") == LEGACY_PORT:
            merged["port"] = DEFAULT_PORT
            try:
                with open(path, "w", encoding="utf-8") as fp:
                    json.dump(merged, fp, indent=2)
                LOGGER.info("Migrated RTC stream port from %s to %s", LEGACY_PORT, DEFAULT_PORT)
            except Exception as exc:
                LOGGER.error("Failed to persist migrated settings: %s", exc)
        return merged


def save_settings(settings: dict) -> bool:
    path = _settings_path()
    with _settings_lock:
        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(settings, fp, indent=2)
            return True
        except Exception as exc:
            LOGGER.error("Failed to save settings: %s", exc)
            return False

