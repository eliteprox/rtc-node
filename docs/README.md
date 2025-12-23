# BYOC-SDK Streaming Docs

This project no longer runs a standalone FastAPI/aiortc server. WebRTC WHIP/WHEP is handled
entirely in the **ComfyUI browser frontend** via `byoc-sdk`, and Python only provides
in-process `/rtc/*` endpoints for exchanging frames/config/session info.

## Runtime endpoints (served by ComfyUI)

- **`GET/POST /rtc/frames/input`**: ComfyUI → browser (latest input frame as base64 PNG)
- **`GET/POST /rtc/frames/output`**: browser → ComfyUI (latest output frame as base64 PNG)
- **`GET/POST /rtc/pipeline`**: desired stream settings + pipeline config (nodes → browser)
- **`GET/POST /rtc/session`**: active session metadata (browser → nodes/UI)

## Manual smoke test (requires ComfyUI running)

1. Run a workflow that hits **`RTC Stream Frame Input`** so frames arrive in `/rtc/frames/input`.
2. Open the **Daydream Live** sidebar tab and click **Start Stream** (this creates the BYOC session).
3. Verify state:

```bash
curl -s http://127.0.0.1:8188/rtc/session | python3 -m json.tool
curl -s http://127.0.0.1:8188/rtc/frames/input | python3 -m json.tool
curl -s http://127.0.0.1:8188/rtc/frames/output | python3 -m json.tool
```

(Replace `8188` with your ComfyUI port.)

