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
from rtc_stream.credentials import resolve_credentials


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
ADOPTED_SERVER = False
LOG_TAILER_THREAD: Optional[threading.Thread] = None
STATE_PATH = ROOT_DIR / "settings" / "local_api_server_state.json"
LOG_FILE_PATH = ROOT_DIR / "settings" / "api_server.log"
def _write_state_file(pid: int, host: str, port: int) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fp:
            json.dump({"pid": pid, "host": host, "port": port, "launcher_pid": os.getpid()}, fp)
    except Exception as exc:  # pragma: no cover - best effort
        LOGGER.warning("Failed to persist local API server state: %s", exc)


def _clear_state_file() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # pragma: no cover
        LOGGER.debug("Unable to remove RTC state file: %s", exc)


def _server_is_healthy(host: str, port: int) -> bool:
    """Check if server at host:port is responding to /healthz."""
    try:
        import http.client
        conn = http.client.HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/healthz")
        response = conn.getresponse()
        conn.close()
        return response.status == 200
    except Exception:
        return False


def _cleanup_orphan_process() -> None:
    if not STATE_PATH.exists():
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        pid = int(data.get("pid", 0))
        host = data.get("host", "127.0.0.1")
        port = int(data.get("port", 8895))
    except Exception:
        pid = 0
        host = "127.0.0.1"
        port = 8895
    
    if pid:
        # Check if server is still healthy
        if _server_is_healthy(host, port):
            LOGGER.debug("Server pid %s still healthy, not cleaning up", pid)
            return
        
        # Server not responding, check if process exists
        try:
            os.kill(pid, 0)  # Check if process exists
            # Process exists but not responding, kill it
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            LOGGER.info("Terminated stale local API server pid %s", pid)
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


def _tail_log_file(log_path: Path) -> None:
    """Tail the log file and print to ComfyUI terminal."""
    try:
        if not log_path.exists():
            log_path.touch()
        
        with open(log_path, "r", encoding="utf-8") as f:
            # Skip to end of existing content
            f.seek(0, 2)
            
            while True:
                line = f.readline()
                if line:
                    # Print without extra newline since line already has one
                    print(f"[RTC] {line.rstrip()}")
                else:
                    time.sleep(0.1)
    except Exception as exc:
        LOGGER.debug("Log tailer stopped: %s", exc)


def _terminate_process_locked() -> bool:
    global SERVER_PROCESS, CURRENT_HOST, CURRENT_PORT, ADOPTED_SERVER, LOG_TAILER_THREAD
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
        LOG_TAILER_THREAD = None
        _clear_state_file()
        return True
    elif ADOPTED_SERVER:
        # Don't kill adopted server, just disconnect
        LOGGER.debug("Disconnecting from adopted server")
        CURRENT_HOST = None
        CURRENT_PORT = None
        ADOPTED_SERVER = False
        return True
    return False


def ensure_server_running(host_override: Optional[str] = None, port_override: Optional[int] = None) -> bool:
    """
    Start the server process if it is not already running.
    """

    global SERVER_PROCESS, CURRENT_HOST, CURRENT_PORT, ADOPTED_SERVER, LOG_TAILER_THREAD
    with SERVER_LOCK:
        if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
            return True
        
        # Already adopted
        if ADOPTED_SERVER and CURRENT_HOST and CURRENT_PORT:
            return True

        # Try to adopt existing server from state file
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                host = data.get("host", "127.0.0.1")
                port = int(data.get("port", 8895))
                pid = int(data.get("pid", 0))
                
                if _server_is_healthy(host, port):
                    LOGGER.debug("Adopting existing local API server at %s:%s (pid %s)", host, port, pid)
                    CURRENT_HOST = host
                    CURRENT_PORT = port
                    ADOPTED_SERVER = True
                    
                    # Start log tailer if not already running
                    if LOG_TAILER_THREAD is None or not LOG_TAILER_THREAD.is_alive():
                        LOG_TAILER_THREAD = threading.Thread(
                            target=_tail_log_file, args=(LOG_FILE_PATH,), daemon=True
                        )
                        LOG_TAILER_THREAD.start()
                    
                    return True
            except Exception as exc:
                LOGGER.debug("Failed to adopt existing server: %s", exc)
        
        _cleanup_orphan_process()
        settings = load_settings()
        host = (host_override or settings["host"]).strip()
        base_port = int(port_override or settings["port"])
        pipeline_config = Path(settings["pipeline_config"]).resolve()
        video_file = settings.get("video_file", "")
        try:
            api_url, api_key = resolve_credentials()
        except ValueError as exc:
            LOGGER.error("Unable to resolve Daydream credentials: %s", exc)
            raise RuntimeError(
                "Daydream credentials missing. Set DAYDREAM_API_KEY (and optional DAYDREAM_API_URL) "
                "in your environment or .env file before starting the RTC server."
            ) from exc

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
                api_url,
                "--api-key",
                api_key,
            ]
            if video_file:
                cmd.extend(["--video-file", video_file])

            LOGGER.info("Starting local API server on %s:%s: %s", host, port, " ".join(cmd))
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            # Redirect output to log file to avoid pipe contention between workers
            LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                log_file = open(LOG_FILE_PATH, "a", encoding="utf-8")
                SERVER_PROCESS = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    cwd=Path(__file__).parent.parent,
                    env=env,
                )
            except OSError as exc:
                last_error = exc
                LOGGER.error("Failed to launch local API server on %s:%s (%s)", host, port, exc)
                continue
            
            # Start log tailer thread to show server output in ComfyUI terminal
            LOG_TAILER_THREAD = threading.Thread(
                target=_tail_log_file, args=(LOG_FILE_PATH,), daemon=True
            )
            LOG_TAILER_THREAD.start()

            if _wait_for_port(host, port):
                CURRENT_HOST = host
                CURRENT_PORT = port
                ADOPTED_SERVER = False
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
    running = (SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None) or ADOPTED_SERVER
    return {
        "running": running,
        "host": CURRENT_HOST if running else None,
        "port": CURRENT_PORT if running else None,
        "pid": SERVER_PROCESS.pid if (SERVER_PROCESS and running) else None,
        "type": "local",
    }


atexit.register(stop_server)

