import json
import logging
from pathlib import Path
from threading import Lock
from typing import Dict


LOGGER = logging.getLogger("rtc_stream.config_store")

ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_DIR = ROOT_DIR / "settings"
CONFIG_PATH = SETTINGS_DIR / "rtc_runtime_config.json"

DEFAULT_RUNTIME_CONFIG: Dict[str, int] = {
    "frame_rate": 30,
    "frame_width": 1280,
    "frame_height": 720,
}

_CONFIG_LOCK = Lock()


def _ensure_settings_dir() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def load_runtime_config() -> Dict[str, int]:
    _ensure_settings_dir()
    data: Dict[str, int] = {}
    with _CONFIG_LOCK:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                    loaded = json.load(fp)
                    if isinstance(loaded, dict):
                        data = {k: int(v) for k, v in loaded.items() if k in DEFAULT_RUNTIME_CONFIG}
            except Exception as exc:  # pragma: no cover - just log and continue
                LOGGER.error("Failed to load runtime config: %s", exc)
    merged = DEFAULT_RUNTIME_CONFIG.copy()
    merged.update(data)
    return merged


def save_runtime_config(config: Dict[str, int]) -> None:
    _ensure_settings_dir()
    with _CONFIG_LOCK:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
                json.dump(config, fp, indent=2)
        except Exception as exc:  # pragma: no cover - persistence failure
            LOGGER.error("Failed to save runtime config: %s", exc)

