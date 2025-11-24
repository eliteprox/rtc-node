# StreamProcessor Docs

## Launching for Local Testing

Before launching anything, copy the sample environment and set your Daydream credentials:

```powershell
Copy-Item .env.example .env -Force
notepad .env  # update DAYDREAM_API_KEY
```

```bash
cp .env.example .env
${EDITOR:-nano} .env   # update DAYDREAM_API_KEY
```

Use the `StreamProcessor API Server` configuration in `.vscode/launch.json` to start the FastAPI service without ComfyUI. It already points at your default `pipeline_config.json`, the fallback `test.mp4`, and exposes the Daydream credentials via environment variables, so hitting Debug ▶️ will:

- initialize `StreamController` and `WhepController`
- listen on `http://127.0.0.1:8895`
- attach `FRAME_BRIDGE` to the event loop for frame delivery

> If you need to try a different pipeline, edit `pipeline_config.json` or pass `pipeline_config` in the `/start` payload. Use `pip install -r requirements-standalone.txt` before debugging if your environment lacks PyTorch.

## cURL Smoke Tests

```
# start a stream (uses cached pipeline_config.json)
curl -s -X POST http://127.0.0.1:8895/start \
  -H "Content-Type: application/json" \
  -d '{"stream_name":"local-debug"}' | jq

# watch current state
curl -s http://127.0.0.1:8895/status | jq

# stop the stream
curl -s -X POST http://127.0.0.1:8895/stop | jq

# push a PNG frame manually
python - <<'PY' > /tmp/frame_b64.txt
import base64, io
from PIL import Image

img = Image.new("RGB", (640, 360), (255, 128, 0))
buffer = io.BytesIO()
img.save(buffer, format="PNG")
print(base64.b64encode(buffer.getvalue()).decode("ascii"))
PY

curl -s -X POST http://127.0.0.1:8895/frames \
  -H "Content-Type: application/json" \
  --data "{\"frame_b64\":\"$(cat /tmp/frame_b64.txt)\"}" | jq
```

`jq` is optional but helpful to map keys like `phase`, `frames_sent`, and `remote_status`.

## Architecture Reference

See `docs/ARCHITECTURE.md` for the signal-flow diagram and explanations of the FastAPI routes, controllers, and lifecycle responsibilities. This is the same content that used to live at the repository root.

