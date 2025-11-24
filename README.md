# RTC Node for ComfyUI

Real-time streaming integration for ComfyUI that enables live video streaming to and from Daydream.live using WebRTC (WHIP/WHEP protocols).

## Features

- 🎥 **Stream frames from ComfyUI workflows** to Daydream.live for real-time AI processing
- 📥 **Receive processed frames back** into ComfyUI via WHEP
- 🔄 **Bidirectional streaming** - both input and output nodes
- 🚀 **Low-latency WebRTC** - optimized for real-time applications
- 🎛️ **Pipeline configuration** - full control over Daydream streaming parameters
- 🖥️ **Built-in sidebar UI** - manage streams directly from ComfyUI interface
- 🪟 **Live preview node** - iframe-based WHEP player embedded inside your graph

## Installation

### ComfyUI Desktop (Recommended)

1. **Install via ComfyUI Manager** (easiest method):
   - Open ComfyUI Desktop
   - Go to **Manager** → **Install Custom Nodes**
   - Search for "RTC Node" or "rtc-node"
   - Click **Install**
   - Restart ComfyUI

2. **Manual Installation**:
   ```bash
   cd %USERPROFILE%\Documents\ComfyUI\custom_nodes
   git clone https://github.com/your-org/rtc-node.git
   cd rtc-node
   pip install -r requirements.txt
   ```
   Then restart ComfyUI Desktop.

### Standalone ComfyUI Installation

For standard ComfyUI installations:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/your-org/rtc-node.git
cd rtc-node
pip install -r requirements.txt
```

Restart your ComfyUI server.

### Verify Installation

After installation, you should see:
- New nodes in the **"RTC Stream"** category
- A **Daydream sidebar** in the ComfyUI interface
- Server controls accessible from the UI

## Quick Start

### 1. Set Up Daydream Credentials

Copy the sample env file and fill in your API key:

**Windows (PowerShell)**:
```powershell
Copy-Item .env.example .env -Force
notepad .env  # set DAYDREAM_API_KEY (and optional DAYDREAM_API_URL)
```

**Linux/Mac**:
```bash
cp .env.example .env
${EDITOR:-nano} .env   # set DAYDREAM_API_KEY (and optional DAYDREAM_API_URL)
```

If you prefer to export variables per-shell instead of using `.env`, set them manually:

```powershell
$env:DAYDREAM_API_KEY="your_api_key_here"
```

```bash
export DAYDREAM_API_KEY="your_api_key_here"
```

The new `rtc_stream/credentials.py` helper loads `.env` (when python-dotenv is
installed) and is used everywhere—custom nodes, the FastAPI subprocess, CLI
tools, and tests—so credentials only have to be defined once. We no longer ship
placeholder keys: if `DAYDREAM_API_KEY` is missing the RTC nodes will explain
how to set it. Keeping secrets in environment variables (instead of node inputs)
prevents them from being serialized into your ComfyUI workflows or checked into
git history.

### 2. Add Nodes to Your Workflow

**Method 1: Using Start RTC Stream Node (Recommended)**:

1. Add **"Pipeline Config"** node to configure your stream parameters
2. Add **"Start RTC Stream"** node and connect the pipeline config
3. The node will automatically:
   - Start the stream on first run
   - Cache the stream ID to avoid recreating it on subsequent runs
   - Output `stream_id`, `playback_id`, and `whip_url`
   - Show a toast notification when stream starts
4. Add **"RTC Stream Frame Input"** node to send frames

**Updating Stream Parameters in Real-Time**:

1. Add **"Update RTC Stream"** node to your workflow
2. Connect the `stream_id` from **Start RTC Stream** node
3. Connect a **"Pipeline Config"** node with new parameters
4. The node will:
   - Only execute when pipeline config or seed changes (uses ComfyUI caching)
   - Send PATCH request to update running stream
   - Show toast notification on success/failure
   - Return update status
5. Change parameters in Pipeline Config and re-run to update live stream

**Auto-Randomize Updates** (optional):
- Right-click the **Update RTC Stream** node → **Properties**
- Set **"control_after_generate"** to **"randomize"**
- The `seed` value will automatically randomize after each execution
- Forces the node to update the stream on every workflow run
- Useful for continuous parameter exploration without manual changes

**Method 2: Using Sidebar Controls**:

1. Add **"RTC Stream Frame Input"** node to your workflow
2. Connect your image output to this node
3. Configure the Daydream sidebar:
   - Click "Start Stream" to begin streaming
   - Your frames will be sent to Daydream for processing

**Receiving Processed Frames**:

1. Add **"RTC Stream Frame Output"** node
2. Connect the WHEP URL from your stream (from Start RTC Stream node or sidebar)
3. The node will output the processed frames

**Previewing the Remote Stream Inside ComfyUI**:

1. Add **"RTC Stream UI"** (aka `RTCStreamUIPreview`) anywhere in your workflow
2. The node renders an iframe that loads the new WHEP preview UI
3. Click **Load status** to pull the most recent `whep_url` from the local API server
4. Hit **Connect** to establish a browser-side WebRTC session directly to the Livepeer gateway
5. Use **Disconnect** or uncheck *Auto-connect* if you only want on-demand playback
6. The event log inside the iframe shows connection progress, while the info panel mirrors `/status`

**Monitoring Stream Status**:

1. Add **"RTC Stream Status"** node to your workflow
2. Optionally connect `stream_id` from **Start RTC Stream** node
   - Creates workflow dependency (Status runs after Start completes)
   - Helps control execution order
3. Configure `refresh_interval` (default: 5 seconds)
   - Set to `0` for no cache (refresh every execution)
   - Set to higher values to reduce API calls
4. The node outputs:
   - `running` (BOOLEAN) - Whether stream is active
   - `stream_id`, `playback_id`, `whip_url` (STRING)
   - `frames_sent`, `queue_depth` (INT) - Performance metrics
   - `status_json` (STRING) - Full status as JSON
5. Use outputs to conditionally control workflow behavior

**Example**: Ensure Status checks after stream starts
```
┌────────────────────────┐
│ Start RTC Stream       │
│  stream_id ────────────┼──┐
└────────────────────────┘  │
                            v
┌─────────────────────────────┐
│ RTC Stream Status           │
│ stream_id: [from Start]     │ ← Executes after Start
│ refresh_interval: 5.0       │
└─────────────────────────────┘
```

### 3. Example Workflow

**Complete Workflow with Live Updates**:

```
┌──────────────────┐
│ Pipeline Config  │ (Initial: prompt="sunset")
│ Node A           │
└────────┬─────────┘
         │
         v
┌────────────────────────┐
│ Start RTC Stream       │ ← Runs once, caches stream_id
│ Outputs:               │
│  - stream_id           │
│  - playback_id         │
│  - whip_url           │
└────────┬───────────────┘
         │ stream_id
         │
         v
┌──────────────────┐     ┌────────────────────────────────┐
│ Pipeline Config  │────>│ Update RTC Stream              │
│ Node B           │     │ (Only runs when config/seed    │
│ (Later: prompt=  │     │  changes)                      │
│  "cyberpunk")    │     │                                │
└──────────────────┘     │ 💡 Right-click → Properties    │
         │               │    "control_after_generate":   │
         │               │    "randomize" = auto-update!  │
         v               └────────────────────────────────┘
┌────────────────────────┐
│ Your Image Generation  │
│ (KSampler, etc.)       │
└────────┬───────────────┘
         │
         v
┌────────────────────────┐
│ RTC Stream Frame       │ ← Sends frames to stream
│ Output                 │
└────────────────────────┘
```

Load the example workflow from `examples/rtc_stream_workflow.json` to see a complete setup.

## Documentation

For detailed information, see the documentation in the `docs/` folder:

- **[docs/README.md](docs/README.md)** - Developer guide, API testing, and launch configurations
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Complete system architecture, data flow diagrams, and component details

## Development Setup

### Prerequisites

- Python 3.10+
- PyTorch (for ComfyUI integration)
- VS Code (recommended for debugging)

### Environment Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-org/rtc-node.git
   cd rtc-node
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   Create a `.env` file or set:
   ```bash
   DAYDREAM_API_URL=https://api.daydream.live/v1/streams
   DAYDREAM_API_KEY=your_api_key_here
   ```

### Debug Configurations

The project includes VS Code debug configurations in `.vscode/launch.json`:

#### 1. **StreamProcessor API Server**

Debug the FastAPI server independently without ComfyUI:

```json
{
  "name": "StreamProcessor API Server",
  "type": "debugpy",
  "request": "launch",
  "program": "${workspaceFolder}/server/app.py"
}
```

**Usage**:
- Press **F5** or click the Debug icon in VS Code
- Select **"StreamProcessor API Server"** from the dropdown
- The server starts on `http://127.0.0.1:8895`
- Test endpoints using curl or the examples in `docs/README.md`

**What it does**:
- Initializes `StreamController` and `WhepController`
- Loads `pipeline_config.json` as the default configuration
- Uses `test.mp4` as a fallback video source
- Attaches `FRAME_BRIDGE` to the event loop for frame delivery

#### 2. **Create and Stream to WHIP**

Test the full WHIP streaming pipeline with a video file:

```json
{
  "name": "Create and Stream to WHIP",
  "type": "debugpy",
  "request": "launch",
  "program": "stream_whip.py"
}
```

**Usage**:
- Select **"Create and Stream to WHIP"** configuration
- Runs a complete stream using `test.mp4`
- Creates a Daydream stream and sends frames via WHIP
- Useful for testing end-to-end streaming without ComfyUI

### Testing

Run the test suite:

```bash
# Run all tests
python run_tests.py

# Run specific test file
pytest tests/controller/test_stream_controller.py

# Run with verbose output
pytest -v
```

### Manual API Testing

When the API server is running, test endpoints with curl:

```bash
# Start a stream
curl -X POST http://127.0.0.1:8895/start \
  -H "Content-Type: application/json" \
  -d '{"stream_name":"test-stream"}'

# Check status
curl http://127.0.0.1:8895/status

# Push a frame (base64 PNG)
curl -X POST http://127.0.0.1:8895/frames \
  -H "Content-Type: application/json" \
  -d '{"frame_b64":"iVBORw0KGgoAAAANS..."}'

# Update pipeline parameters on running stream
curl -X PATCH http://127.0.0.1:8895/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline_config": {
      "pipeline": "streamdiffusion",
      "params": {
        "prompt": "cyberpunk cityscape at night",
        "guidance_scale": 7.5,
        "delta": 0.5
      }
    }
  }'

# Stop the stream
curl -X POST http://127.0.0.1:8895/stop
```

See [docs/README.md](docs/README.md) for more detailed curl examples and smoke tests.

## Project Structure

```
rtc-node/
├── nodes/                      # ComfyUI custom nodes
│   ├── frame_nodes.py         # RTC Stream input/output nodes
│   ├── pipeline_config.py     # Pipeline configuration node
│   ├── server_manager.py      # FastAPI server lifecycle
│   ├── api/                   # ComfyUI API endpoints
│   └── js/                    # Frontend sidebar UI
├── rtc_stream/                # Core streaming logic
│   ├── controller.py          # WHIP streaming controller
│   ├── whep_controller.py     # WHEP playback controller
│   ├── frame_bridge.py        # Async frame queue
│   ├── frame_uplink.py        # Frame delivery to API
│   └── daydream.py           # Daydream API client
├── server/                    # Standalone FastAPI server
│   └── app.py                # HTTP routes and handlers
├── docs/                      # Documentation
│   ├── README.md             # Developer guide
│   └── ARCHITECTURE.md       # System architecture
├── examples/                  # Example workflows
│   └── rtc_stream_workflow.json
├── tests/                     # Test suite
├── .vscode/
│   └── launch.json           # Debug configurations
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

## How It Works

1. **Frame Output**: ComfyUI nodes send image tensors to the FastAPI server
2. **Server Processing**: Frames are queued and converted to WebRTC format
3. **WHIP Streaming**: Frames are sent to Daydream via WebRTC WHIP protocol
4. **Processing**: Daydream processes frames with AI pipelines
5. **WHEP Playback**: Processed frames are received back via WHEP protocol
6. **Frame Input**: Frames are converted back to ComfyUI tensors

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed component diagrams and data flow.

## Configuration

### Runtime Configuration

The server maintains runtime configuration in `settings/rtc_runtime_config.json`:

```json
{
  "frame_rate": 30,
  "width": 640,
  "height": 360
}
```

Update via API:
```bash
curl -X POST http://127.0.0.1:8895/config \
  -H "Content-Type: application/json" \
  -d '{"frame_rate": 60}'
```

### Pipeline Configuration

Create pipeline configurations using the **Pipeline Config Node** in ComfyUI, or manually edit `pipeline_config.json`.

#### Understanding Node Caching Behavior

The RTC Stream nodes leverage ComfyUI's built-in caching system for optimal performance:

**Start RTC Stream Node**:
- Computes a hash of `pipeline_config` + `stream_name`
- If hash hasn't changed, ComfyUI uses cached output (no execution)
- Avoids creating duplicate streams on repeated workflow runs
- Example: Run workflow 10 times → stream created only once

**Update RTC Stream Node**:
- Computes hash of `pipeline_config` + `seed` (ignores `stream_id`)
- Only executes when you change the pipeline parameters or seed
- Example: Change prompt from "sunset" to "cyberpunk" → executes and updates stream
- Example: Run workflow again with same "cyberpunk" and seed → cached, doesn't execute
- **Randomize mode**: Enable "control_after_generate" → "randomize" to auto-update on every run
  - Seed automatically randomizes after execution
  - Forces update even with same config
  - Great for continuous exploration

**Pipeline Config Node**:
- Already caches its output automatically
- Changing any parameter updates the cache key
- Downstream nodes (Start/Update) detect the change

**Update Running Stream**: You can update pipeline parameters (prompt, guidance_scale, controlnets, etc.) on a running stream without restarting:

```bash
curl -X PATCH http://127.0.0.1:8895/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline_config": {
      "params": {
        "prompt": "futuristic neon cityscape",
        "guidance_scale": 8.0,
        "delta": 0.4
      }
    }
  }'
```

This forwards the update to the [Daydream API's PATCH endpoint](https://docs.daydream.live/api-reference/update-stream), allowing real-time parameter adjustments without interrupting the stream.

**Advantages of Using Update RTC Stream Node**:
- ✅ **Integrated in workflow**: Parameters update automatically as part of your workflow execution
- ✅ **Smart caching**: Only updates when parameters actually change, not on every run
- ✅ **Visual feedback**: Toast notifications show when updates succeed or fail
- ✅ **No manual API calls**: No need to copy stream IDs or construct curl commands
- ✅ **Type safety**: Pipeline Config node validates parameters before sending
- ✅ **Auto-randomization**: "control_after_generate" enables continuous exploration

**When to Use "control_after_generate"**:

| Use Case | Setting | Behavior |
|----------|---------|----------|
| **Manual control** | `fixed` (default) | Only updates when you change config |
| **Continuous exploration** | `randomize` | Updates every run with random seed |
| **Live performance** | `randomize` | Auto-varying visuals for VJ/art |
| **Parameter discovery** | `randomize` | Explore variations automatically |

**How to Enable**:
1. Right-click **Update RTC Stream** node
2. Select **Properties**
3. Set **"control_after_generate"** to **"randomize"**
4. Run workflow - seed auto-randomizes after each execution
5. Each run triggers a stream update with new variation

> **Note**: If you receive a `405 Not Allowed` error, the PATCH endpoint may not be available yet on the Daydream API. As a workaround, stop the current stream and start a new one with updated parameters.

## Troubleshooting

### Server Not Starting

- Check that port 8895 is available
- Verify Daydream API credentials are set
- Check logs in ComfyUI console
- Try using the debug configuration to see detailed errors

### No Frames Being Sent

- Verify the stream is started (check status endpoint)
- Ensure the RTC Stream Frame Input node is enabled
- Check that frames are reaching the server (`/status` shows `frames_sent`)
- Verify network connectivity to Daydream API

### WHEP Connection Issues

- Ensure WHEP URL is valid
- Check that the remote stream is active
- Verify firewall settings allow WebRTC traffic
- Test WHEP endpoint directly using the `/whep/status` route

For more help, see the troubleshooting section in [docs/README.md](docs/README.md).

## Requirements

- Python 3.10+
- PyTorch 2.0+
- aiortc 1.9.0+
- FastAPI 0.115.0+
- See `requirements.txt` for complete list

## License

[Your License Here]

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## Support

- **Documentation**: See `docs/` folder
- **Issues**: [GitHub Issues](https://github.com/your-org/rtc-node/issues)
- **Discord**: [Your Discord Link]

## Credits

Built with:
- [aiortc](https://github.com/aiortc/aiortc) - WebRTC implementation
- [FastAPI](https://fastapi.tiangolo.com/) - API server framework
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) - Node-based UI framework
- [Daydream.live](https://daydream.live) - Real-time AI streaming platform

