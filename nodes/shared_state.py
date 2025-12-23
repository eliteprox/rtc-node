
import threading
from typing import Dict, Any, Optional

# Shared buffer for the latest received frame (from JS to Python)
# Format: {"image": tensor, "timestamp": float}
FRAME_BUFFER = {}
FRAME_LOCK = threading.Lock()

# Shared buffer for status (from JS to Python)
STATUS_BUFFER = {
    "running": False,
    "stream_id": "",
    "playback_id": "",
    "whip_url": "",
    "frames_sent": 0,
    "queue_depth": 0,
    "remote_status": {}
}
STATUS_LOCK = threading.Lock()
