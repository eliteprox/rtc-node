import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from rtc_stream.controller import StreamController, ControllerConfig, ControllerState

@pytest.fixture
def controller(pipeline_config_path, mock_pc, mock_daydream_api):
    config = ControllerConfig(
        api_url="http://test",
        api_key="key",
        pipeline_path=pipeline_config_path
    )
    ctrl = StreamController(config)
    return ctrl

@pytest.mark.asyncio
async def test_controller_lifecycle(controller, bridge_loop):
    # Start
    status = await controller.start(stream_name="test")
    assert status["running"] is True
    assert status["stream_id"] == "stream-123"
    assert controller.state.running is True
    assert controller._task is not None
    
    # Stop
    status = await controller.stop()
    assert status["running"] is False
    assert controller.state.running is False
    assert controller._task is None

@pytest.mark.asyncio
async def test_controller_restart(controller, bridge_loop):
    await controller.start()
    task1 = controller._task
    assert task1 is not None
    
    # Restarting should cancel previous task
    await controller.start()
    task2 = controller._task
    assert task2 is not task1
    assert not task1.done() or task1.cancelled() # Depending on how fast it cancels
    assert controller.state.running is True

@pytest.mark.asyncio
async def test_start_failure(controller, bridge_loop, monkeypatch):
    # Mock start_stream to raise exception
    def mock_raise(*args, **kwargs):
        raise RuntimeError("API Error")
        
    monkeypatch.setattr("rtc_stream.controller.start_stream", mock_raise)
    
    with pytest.raises(RuntimeError, match="API Error"):
        await controller.start()
    
    assert controller.state.running is False
    assert controller._task is None

@pytest.mark.asyncio
async def test_status_async_throttling(controller, bridge_loop, monkeypatch):
    # Setup
    await controller.start()
    
    mock_poll = MagicMock(return_value={"state": "ready"})
    monkeypatch.setattr("rtc_stream.controller.poll_stream_status", mock_poll)
    
    # First poll
    await controller.status_async(refresh_remote=True)
    assert mock_poll.call_count == 1
    
    # Immediate second poll should be throttled
    await controller.status_async(refresh_remote=True)
    assert mock_poll.call_count == 1  # Still 1
    
    # Wait 3s+
    controller.state.last_remote_check -= 4
    await controller.status_async(refresh_remote=True)
    assert mock_poll.call_count == 2

@pytest.mark.asyncio
async def test_enqueue_frame(controller, bridge_loop):
    import numpy as np
    from rtc_stream.frame_bridge import FRAME_BRIDGE
    
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    controller.enqueue_frame(frame)
    
    assert FRAME_BRIDGE.depth() == 1
    got = await FRAME_BRIDGE.queue.get()
    assert got is not None

