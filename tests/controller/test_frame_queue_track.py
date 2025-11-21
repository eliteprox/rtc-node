import pytest
import asyncio
import numpy as np
from unittest.mock import MagicMock, patch
from rtc_stream.controller import FrameQueueTrack
from rtc_stream.frame_bridge import FRAME_BRIDGE

@pytest.fixture
def track(bridge_loop):
    # Use high framerate to speed up tests slightly, or mock sleep
    t = FrameQueueTrack(
        bridge=FRAME_BRIDGE,
        fallback_video=None,
        frame_rate=100.0
    )
    # Monkeypatch sleep to be instant
    original_sleep = asyncio.sleep
    async def instant_sleep(delay):
        return
    t.recv = patch('asyncio.sleep', side_effect=instant_sleep)(t.recv)
    
    # Since recv is bound method, we need to be careful. 
    # Actually, mocking asyncio.sleep inside the module is better or just accepting the small delay.
    # But wait, recv calls asyncio.sleep(self._frame_interval).
    # If I set frame_rate high (e.g. 1000), interval is 1ms.
    return t

@pytest.mark.asyncio
async def test_track_live_frames(track, bridge_loop):
    # Enqueue live frames
    f1 = np.zeros((720, 1280, 3), dtype=np.uint8)
    f1[0,0] = [255, 0, 0] # Red pixel
    
    FRAME_BRIDGE.enqueue(f1)
    
    # Receive
    frame_out = await track.recv()
    assert frame_out.pts == 0
    assert frame_out.time_base == track._time_base
    
    # Check pixel content (converted to YUV, but we can check source logic if exposed or just trust behavior)
    # We can check internal state if needed or just rely on valid frame return
    assert track._last_source == "queue"

@pytest.mark.asyncio
async def test_track_cached_replay(track, bridge_loop):
    # 1. Send one frame
    f1 = np.zeros((720, 1280, 3), dtype=np.uint8)
    FRAME_BRIDGE.enqueue(f1)
    await track.recv()
    assert track._last_source == "queue"
    
    # 2. Queue empty, should replay last
    frame_out = await track.recv()
    assert frame_out.pts == 1
    assert track._last_source == "queue_cached"

@pytest.mark.asyncio
async def test_track_fallback_folder(track, bridge_loop, tmp_path, monkeypatch):
    # Create temp images
    from PIL import Image
    d = tmp_path / "frames"
    d.mkdir()
    
    img = Image.new('RGB', (100, 100), color='blue')
    img.save(d / "test1.png")
    
    # Patch FolderFrameSource output dir
    # We need to patch the class attribute before instantiation or patch the instance
    # Since track is already created in fixture, we'll patch its folder_source
    track.folder_source.OUTPUT_DIR = d
    track.folder_source._refresh_files() # Force refresh
    
    # Ensure queue empty and no cached frame
    track._last_live_frame = None
    
    frame_out = await track.recv()
    assert track._last_source == "fallback_folder"
    assert frame_out.pts == 0

@pytest.mark.asyncio
async def test_track_dummy_fallback(track, bridge_loop):
    # No queue, no cache, no folder (default folder is empty usually in test env)
    # Or ensure it's empty
    track.folder_source.files = [] 
    track._last_live_frame = None
    
    frame_out = await track.recv()
    assert track._last_source == "fallback_dummy"
    assert frame_out.pts == 0

@pytest.mark.asyncio
async def test_track_monotonicity(track, bridge_loop):
    # Sequence: Live -> Live -> Empty(Cache) -> Live -> Empty(Cache)
    
    # 1. Live
    FRAME_BRIDGE.enqueue(np.zeros((720, 1280, 3), dtype=np.uint8))
    f1 = await track.recv()
    assert f1.pts == 0
    
    # 2. Live
    FRAME_BRIDGE.enqueue(np.zeros((720, 1280, 3), dtype=np.uint8))
    f2 = await track.recv()
    assert f2.pts == 1
    
    # 3. Empty (Cache)
    f3 = await track.recv()
    assert f3.pts == 2
    assert track._last_source == "queue_cached"
    
    # 4. Live
    FRAME_BRIDGE.enqueue(np.zeros((720, 1280, 3), dtype=np.uint8))
    f4 = await track.recv()
    assert f4.pts == 3
    assert track._last_source == "queue"
    
    # Verify PTS strictly increasing
    assert f1.pts < f2.pts < f3.pts < f4.pts

@pytest.mark.asyncio
async def test_intermittent_source_switching(track, bridge_loop, tmp_path):
    # Setup fallback folder
    d = tmp_path / "frames"
    d.mkdir()
    from PIL import Image
    Image.new('RGB', (100, 100), color='blue').save(d / "test1.png")
    track.folder_source.OUTPUT_DIR = d
    track.folder_source._refresh_files()
    
    # 1. Fallback folder
    f1 = await track.recv()
    assert track._last_source == "fallback_folder"
    
    # 2. Live frame arrives
    FRAME_BRIDGE.enqueue(np.zeros((720, 1280, 3), dtype=np.uint8))
    f2 = await track.recv()
    assert track._last_source == "queue"
    
    # 3. Queue empty -> Cache
    f3 = await track.recv()
    assert track._last_source == "queue_cached"
    
    # 4. Clear cache manually to force fallback (simulate stream restart or cache expiry if we had it)
    track._last_live_frame = None
    f4 = await track.recv()
    assert track._last_source == "fallback_folder"
    
    assert f1.pts < f2.pts < f3.pts < f4.pts

