"""
Process manager that launches the local API server (FastAPI + StreamController).
"""

import atexit
import json
import logging
import os
import signal
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


LOGGER = logging.getLogger("rtc_stream.api_server_manager")

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
CURRENT_HOST: Optional[str] = None
CURRENT_PORT: Optional[int] = None
STATE_PATH = ROOT_DIR / "settings" / "local_api_server_state.json"
def _write_state_file(pid: int, host: str, port: int) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fp:
            json.dump({"pid": pid, "host": host, "port": port}, fp)
    except Exception as exc:  # pragma: no cover - best effort
        LOGGER.warning("Failed to persist local API server state: %s", exc)


def _clear_state_file() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # pragma: no cover
        LOGGER.debug("Unable to remove RTC state file: %s", exc)


def _cleanup_orphan_process() -> None:
    if not STATE_PATH.exists():
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        pid = int(data.get("pid", 0))
    except Exception:
        pid = 0
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            LOGGER.info("Terminated leftover local API server pid %s", pid)
        except OSError:
            LOGGER.debug("Leftover local API server pid %s not running", pid)
    _clear_state_file()


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


def _is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _terminate_process_locked() -> bool:
    global SERVER_PROCESS, CURRENT_HOST, CURRENT_PORT
    if SERVER_PROCESS:
        LOGGER.info("Stopping local API server")
        SERVER_PROCESS.terminate()
        try:
            SERVER_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            SERVER_PROCESS.kill()
        SERVER_PROCESS = None
        CURRENT_HOST = None
        CURRENT_PORT = None
        _clear_state_file()
        return True
    return False


def ensure_server_running(host_override: Optional[str] = None, port_override: Optional[int] = None) -> bool:
    """
    Start the server process if it is not already running.
    """

    global SERVER_PROCESS, CURRENT_HOST, CURRENT_PORT
    with SERVER_LOCK:
        if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
            return True

        _cleanup_orphan_process()
        settings = load_settings()
        host = (host_override or settings["host"]).strip()
        base_port = int(port_override or settings["port"])
        pipeline_config = Path(settings["pipeline_config"]).resolve()
        video_file = settings.get("video_file", "")

        if not pipeline_config.exists():
            LOGGER.warning("Pipeline config %s not found", pipeline_config)

        script = _server_script()
        candidate_ports = [base_port + offset for offset in range(0, 8)]
        last_error = None

        for port in candidate_ports:
            if _is_port_in_use(host, port):
                LOGGER.warning("Port %s already in use; trying next", port)
                continue

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

            LOGGER.info("Starting local API server on %s:%s: %s", host, port, " ".join(cmd))
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            try:
                SERVER_PROCESS = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=Path(__file__).parent.parent,
                    env=env,
                )
            except OSError as exc:
                last_error = exc
                LOGGER.error("Failed to launch local API server on %s:%s (%s)", host, port, exc)
                continue

            threading.Thread(
                target=_relay_output, args=(SERVER_PROCESS.stdout, logging.INFO), daemon=True
            ).start()
            threading.Thread(
                target=_relay_output, args=(SERVER_PROCESS.stderr, logging.ERROR), daemon=True
            ).start()

            if _wait_for_port(host, port):
                CURRENT_HOST = host
                CURRENT_PORT = port
                _write_state_file(SERVER_PROCESS.pid, host, port)
                LOGGER.info("Local API server listening on %s:%s", host, port)
                if port != base_port:
                    LOGGER.warning("Local API server running on fallback port %s", port)
                return True

            LOGGER.error("Local API server failed to start on %s:%s", host, port)
            _terminate_process_locked()

        if last_error:
            raise RuntimeError(f"Unable to start local API server: {last_error}")
        raise RuntimeError("Unable to find available port for local API server")


def stop_server() -> bool:
    with SERVER_LOCK:
        return _terminate_process_locked()


def server_status() -> dict:
    running = SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None
    return {
        "running": running,
        "host": CURRENT_HOST if running else None,
        "port": CURRENT_PORT if running else None,
        "pid": SERVER_PROCESS.pid if running else None,
        "type": "local",
    }


def _relay_output(pipe, level):
    for line in iter(pipe.readline, b""):
        LOGGER.log(level, line.decode("utf-8").rstrip())


atexit.register(stop_server)

