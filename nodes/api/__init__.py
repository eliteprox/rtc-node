import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

try:
    from server import PromptServer
except ImportError:  # pragma: no cover - PromptServer not available outside ComfyUI
    PromptServer = None  # type: ignore

from ..server_manager import ensure_server_running, server_status, stop_server
from rtc_stream.credentials_store import (
    load_credentials_from_env,
    persist_credentials_to_env,
)


LOGGER = logging.getLogger("rtc_stream.api")
routes = getattr(getattr(PromptServer, "instance", None), "routes", None)


class LocalAPIServerController:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def start(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: ensure_server_running(host_override=host, port_override=port)
            )

    async def stop(self) -> bool:
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, stop_server)

    async def restart(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        await self.stop()
        return await self.start(host=host, port=port)

    def status(self) -> Dict[str, Any]:
        return server_status()


def _normalize_host_port(payload: Dict[str, Any]) -> (Optional[str], Optional[int]):
    host = payload.get("host")
    port = payload.get("port")

    if isinstance(host, str):
        host = host.strip() or None

    if isinstance(port, str):
        port = port.strip()
        port = int(port) if port.isdigit() else None
    elif isinstance(port, (int, float)):
        port = int(port)
    else:
        port = None

    return host, port


if routes:
    controller = LocalAPIServerController()

    @routes.get("/rtc/control")
    async def rtc_control_status(_request):
        return web.json_response({"success": True, "status": controller.status()})

    @routes.post("/rtc/control")
    async def rtc_control(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        action = (payload.get("action") or "status").lower()
        settings = payload.get("settings") or {}
        host, port = _normalize_host_port({**payload, **settings})

        try:
            if action == "status":
                return web.json_response({"success": True, "status": controller.status()})
            if action == "start":
                success = await controller.start(host=host, port=port)
                return web.json_response({"success": success, "status": controller.status()})
            if action == "stop":
                success = await controller.stop()
                return web.json_response({"success": success, "status": controller.status()})
            if action == "restart":
                success = await controller.restart(host=host, port=port)
                return web.json_response({"success": success, "status": controller.status()})

            return web.json_response(
                {"success": False, "error": f"Invalid action '{action}'"}, status=400
            )
        except Exception as exc:  # pragma: no cover - runtime path
            LOGGER.error("RTC control action '%s' failed: %s", action, exc)
            return web.json_response({"success": False, "error": str(exc)}, status=500)

    def _public_credentials(state: Dict[str, Any]) -> Dict[str, Any]:
        sources = state.get("sources", {})
        return {
            "api_url": state.get("api_url"),
            "has_api_key": bool(state.get("api_key")),
            "sources": {
                "api_url": sources.get("api_url", "default"),
                "api_key": sources.get("api_key", "missing"),
            },
        }

    @routes.get("/rtc/credentials")
    async def rtc_credentials_get(_request):
        state = load_credentials_from_env()
        return web.json_response({"success": True, "credentials": _public_credentials(state)})

    @routes.post("/rtc/credentials")
    async def rtc_credentials_post(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        api_url = payload.get("api_url")
        api_key = payload.get("api_key")

        if api_url is not None and not isinstance(api_url, str):
            return web.json_response(
                {"success": False, "error": "api_url must be a string"}, status=400
            )
        if api_key is not None and not isinstance(api_key, str):
            return web.json_response(
                {"success": False, "error": "api_key must be a string"}, status=400
            )

        try:
            state = persist_credentials_to_env(api_url=api_url, api_key=api_key)
        except ValueError as exc:  # pragma: no cover - validation error propagation
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception as exc:  # pragma: no cover - runtime persistence failure
            LOGGER.error("Failed to persist DayDream credentials: %s", exc)
            return web.json_response({"success": False, "error": "Persistence failed"}, status=500)

        return web.json_response({"success": True, "credentials": _public_credentials(state)})
else:
    LOGGER.warning("PromptServer routes not available; RTC API control disabled")

