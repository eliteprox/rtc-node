import logging
from typing import Any, Dict

try:
    from aiohttp import web  # type: ignore
except ImportError:  # pragma: no cover - aiohttp provided by ComfyUI runtime
    web = None  # type: ignore

try:
    from server import PromptServer
except ImportError:  # pragma: no cover - PromptServer not available outside ComfyUI
    PromptServer = None  # type: ignore

from rtc_stream.credentials_store import load_credentials_from_env, persist_credentials_to_env
from rtc_stream.state_store import RTC_STATE


LOGGER = logging.getLogger("rtc_stream.api")
routes = getattr(getattr(PromptServer, "instance", None), "routes", None) if web else None


if routes:
    # ---------------------------------------------------------------------
    # BYOC <-> ComfyUI bridge endpoints (in-process; no subprocess)
    # ---------------------------------------------------------------------

    @routes.get("/rtc/session")
    async def rtc_session_get(_request):
        return web.json_response({"success": True, "session": RTC_STATE.get_session()})

    @routes.post("/rtc/session")
    async def rtc_session_post(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        patch = payload.get("session") if isinstance(payload.get("session"), dict) else payload
        if not isinstance(patch, dict):
            return web.json_response({"success": False, "error": "Invalid session payload"}, status=400)
        session = RTC_STATE.update_session(patch)
        return web.json_response({"success": True, "session": session})

    @routes.post("/rtc/session/clear")
    async def rtc_session_clear(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        error = payload.get("error") if isinstance(payload.get("error"), str) else ""
        session = RTC_STATE.clear_session(error=error)
        return web.json_response({"success": True, "session": session})

    @routes.get("/rtc/pipeline")
    async def rtc_pipeline_get(_request):
        return web.json_response({"success": True, "config": RTC_STATE.get_desired_config()})

    @routes.post("/rtc/pipeline")
    async def rtc_pipeline_post(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        config = RTC_STATE.set_desired_config(
            stream_name=payload.get("stream_name"),
            pipeline=payload.get("pipeline"),
            pipeline_config=payload.get("pipeline_config") if isinstance(payload.get("pipeline_config"), dict) else None,
            width=payload.get("width"),
            height=payload.get("height"),
            fps=payload.get("fps"),
        )
        return web.json_response({"success": True, "config": config})

    @routes.get("/rtc/frames/input")
    async def rtc_frames_input_get(_request):
        frame_b64, meta, has_frame = RTC_STATE.get_input_frame()
        return web.json_response({"success": True, "frame_b64": frame_b64, "has_frame": has_frame, "metadata": meta})

    @routes.post("/rtc/frames/input")
    async def rtc_frames_input_post(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        frame_b64 = payload.get("frame_b64")
        if not isinstance(frame_b64, str):
            return web.json_response({"success": False, "error": "frame_b64 must be a string"}, status=400)
        mime = payload.get("mime") if isinstance(payload.get("mime"), str) else "image/png"
        info = RTC_STATE.set_input_frame(frame_b64, mime=mime)
        return web.json_response({"success": True, "accepted": True, "info": info})

    @routes.get("/rtc/frames/output")
    async def rtc_frames_output_get(_request):
        frame_b64, meta, has_frame = RTC_STATE.get_output_frame()
        return web.json_response({"success": True, "frame_b64": frame_b64, "has_frame": has_frame, "metadata": meta})

    @routes.post("/rtc/frames/output")
    async def rtc_frames_output_post(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        frame_b64 = payload.get("frame_b64")
        if not isinstance(frame_b64, str):
            return web.json_response({"success": False, "error": "frame_b64 must be a string"}, status=400)
        mime = payload.get("mime") if isinstance(payload.get("mime"), str) else "image/png"
        info = RTC_STATE.set_output_frame(frame_b64, mime=mime)
        return web.json_response({"success": True, "accepted": True, "info": info})

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
    LOGGER.warning("PromptServer routes not available; RTC bridge endpoints disabled")

