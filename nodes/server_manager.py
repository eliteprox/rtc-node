"""
Process manager that launches the local RTC stream FastAPI server.
"""

import atexit
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore
    _DOTENV_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback if dependency missing
    _DOTENV_AVAILABLE = False

    def load_dotenv(*_args, **_kwargs):
        return False

from .settings_storage import load_settings


LOGGER = logging.getLogger("rtc_stream.server_manager")

ROOT_DIR = Path(__file__).parent.parent
DOTENV_PATH = ROOT_DIR / ".env"
if _DOTENV_AVAILABLE:
    loaded = load_dotenv(DOTENV_PATH)
    if loaded:
        LOGGER.info("Loaded environment variables from %s", DOTENV_PATH)
    else:
        LOGGER.debug("No .env file found at %s", DOTENV_PATH)
else:
    LOGGER.warning("python-dotenv not installed; .env support disabled")

SERVER_PROCESS: Optional[subprocess.Popen] = None
SERVER_LOCK = threading.Lock()


def _server_script() -> Path:
    return Path(__file__).parent.parent / "server" / "app.py"


def _wait_for_port(host: str, port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                time.sleep(1)
    return False


def ensure_server_running() -> None:
    """
    Start the server process if it is not already running.
    """

    global SERVER_PROCESS
    with SERVER_LOCK:
        if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
            return

        settings = load_settings()
        host = settings["host"]
        port = int(settings["port"])
        pipeline_config = Path(settings["pipeline_config"]).resolve()
        video_file = settings.get("video_file", "")

        if not pipeline_config.exists():
            LOGGER.warning("Pipeline config %s not found", pipeline_config)

        script = _server_script()
        cmd = [
            sys.executable,
            "-u",
            str(script),
            "--host",
            host,
            "--port",
            str(port),
            "--pipeline-config",
            str(pipeline_config),
            "--api-url",
            os.environ.get("DAYDREAM_API_URL", "https://api.daydream.live"),
            "--api-key",
            os.environ.get("DAYDREAM_API_KEY", "sk_XXQmxSCDHjdaBFVXZLxF8btbPGtHHSRruNojX5xqjFTZGV3M8Vi6WGwRknnHRwsM"),
        ]
        if video_file:
            cmd.extend(["--video-file", video_file])

        LOGGER.info("Starting RTC stream server: %s", " ".join(cmd))
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        SERVER_PROCESS = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(__file__).parent.parent,
            env=env,
        )

        threading.Thread(target=_relay_output, args=(SERVER_PROCESS.stdout, logging.INFO), daemon=True).start()
        threading.Thread(target=_relay_output, args=(SERVER_PROCESS.stderr, logging.ERROR), daemon=True).start()

        if not _wait_for_port(host, port):
            LOGGER.error("RTC stream server failed to start on %s:%s", host, port)
        else:
            LOGGER.info("RTC stream server listening on %s:%s", host, port)


def stop_server() -> None:
    global SERVER_PROCESS
    with SERVER_LOCK:
        if SERVER_PROCESS:
            LOGGER.info("Stopping RTC stream server")
            SERVER_PROCESS.terminate()
            try:
                SERVER_PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                SERVER_PROCESS.kill()
            SERVER_PROCESS = None


def _relay_output(pipe, level):
    for line in iter(pipe.readline, b""):
        LOGGER.log(level, line.decode("utf-8").rstrip())


atexit.register(stop_server)

# Start server when module is imported within ComfyUI
try:
    ensure_server_running()
except Exception as exc:
    LOGGER.error("Failed to auto-start RTC stream server: %s", exc)

