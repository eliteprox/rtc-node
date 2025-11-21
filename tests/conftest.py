import asyncio
import pytest
import sys
from unittest.mock import MagicMock, AsyncMock

# Mock torch before it gets imported by any module
mock_torch = MagicMock()
mock_torch.Tensor = MagicMock
sys.modules["torch"] = mock_torch

from pathlib import Path

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rtc_stream.frame_bridge import FRAME_BRIDGE

@pytest.fixture(scope="function")
def bridge_loop():
    """
    Attaches the running event loop to the global FRAME_BRIDGE for the duration of the test.
    Resets the bridge afterwards.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    FRAME_BRIDGE.attach_loop(loop)
    
    # Clear any existing items
    while not FRAME_BRIDGE.queue.empty():
        FRAME_BRIDGE.queue.get_nowait()
    
    yield loop
    
    # Teardown
    if FRAME_BRIDGE.loop == loop:
        FRAME_BRIDGE.loop = None
    loop.close()

@pytest.fixture
def mock_daydream_api(monkeypatch):
    """
    Mocks the requests calls in rtc_stream.daydream to simulate Daydream API responses.
    """
    mock_post = MagicMock()
    mock_get = MagicMock()
    
    def side_effect_post(url, **kwargs):
        if "v1/streams" in url:
            return MagicMock(
                status_code=201,
                json=lambda: {
                    "id": "stream-123",
                    "whip_url": "http://fake-whip/endpoint",
                    "output_playback_id": "playback-123",
                    "whep_url": "http://fake-whep/endpoint",
                    "name": "test-stream"
                }
            )
        return MagicMock(status_code=404)

    def side_effect_get(url, **kwargs):
        if "status" in url:
            return MagicMock(
                status_code=200,
                json=lambda: {"state": "ready"}
            )
        return MagicMock(status_code=404)

    mock_post.side_effect = side_effect_post
    mock_get.side_effect = side_effect_get
    
    monkeypatch.setattr("requests.post", mock_post)
    monkeypatch.setattr("requests.Session.post", mock_post)
    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.Session.get", mock_get)
    
    return mock_post

@pytest.fixture
def mock_pc(monkeypatch):
    """
    Mocks aiortc.RTCPeerConnection to avoid real WebRTC stack overhead.
    """
    pc_mock = MagicMock()
    pc_instance = MagicMock()
    
    # Async methods need AsyncMock
    pc_instance.createOffer = AsyncMock(return_value=MagicMock(sdp="v=0..."))
    pc_instance.setLocalDescription = AsyncMock()
    pc_instance.setRemoteDescription = AsyncMock()
    pc_instance.close = AsyncMock()
    pc_instance.addTrack = MagicMock()
    
    # Mock event handlers storage
    handlers = {}
    def on(event, handler=None):
        def decorator(func):
            handlers[event] = func
            return func
        if handler:
            handlers[event] = handler
            return handler
        return decorator
    
    pc_instance.on = on
    pc_instance._emit = lambda event, *args: asyncio.create_task(handlers.get(event, lambda *a: None)(*args)) if event in handlers else None
    
    pc_mock.return_value = pc_instance
    monkeypatch.setattr("rtc_stream.controller.RTCPeerConnection", pc_mock)
    monkeypatch.setattr("rtc_stream.whep_controller.RTCPeerConnection", pc_mock)
    
    return pc_instance

@pytest.fixture
def pipeline_config_path(tmp_path):
    """Creates a temporary pipeline config file."""
    path = tmp_path / "pipeline_config.json"
    import json
    with open(path, "w") as f:
        json.dump({"pipeline": "test_pipeline", "params": {}}, f)
    return path

