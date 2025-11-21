"""Tests for StartRTCStream, UpdateRTCStream, and RTCStreamStatus nodes."""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from nodes.frame_nodes import StartRTCStream, UpdateRTCStream, RTCStreamStatus


@pytest.fixture
def mock_server_status():
    """Mock server_status to indicate server is running."""
    with patch("nodes.frame_nodes.server_status") as mock:
        mock.return_value = {"running": True, "host": "127.0.0.1", "port": 8895}
        yield mock


@pytest.fixture
def mock_ensure_server():
    """Mock ensure_server_running."""
    with patch("nodes.frame_nodes.ensure_server_running") as mock:
        yield mock


@pytest.fixture
def start_node():
    """Create a StartRTCStream node instance."""
    return StartRTCStream()


def test_start_stream_creates_new_stream(start_node, mock_server_status, mock_ensure_server):
    """Test that start_stream creates a new stream when none exists."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {
            "model_id": "stabilityai/sd-turbo",
            "prompt": "test prompt",
            "width": 704,
            "height": 704,
        },
    }

    # Mock the HTTP session
    with patch.object(start_node, "_session") as mock_session:
        # Mock status check - no stream running
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": False}
        
        # Mock start request
        mock_start_response = MagicMock()
        mock_start_response.json.return_value = {
            "stream_id": "test_stream_123",
            "playback_id": "test_playback_456",
            "whip_url": "https://whip.example.com/test",
        }
        
        mock_session.get.return_value = mock_status_response
        mock_session.post.return_value = mock_start_response
        
        # Execute the node
        stream_id, playback_id, whip_url = start_node.start_stream(
            pipeline_config, 
            stream_name="test-stream",
            fps=30,
            width=512,
            height=512
        )
        
        # Verify results
        assert stream_id == "test_stream_123"
        assert playback_id == "test_playback_456"
        assert whip_url == "https://whip.example.com/test"
        
        # Verify POST was called with correct payload
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "/start" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["pipeline_config"] == pipeline_config
        assert payload["frame_rate"] == 30
        assert payload["frame_width"] == 512
        assert payload["frame_height"] == 512


def test_start_stream_reuses_existing_stream(start_node, mock_server_status, mock_ensure_server):
    """Test that start_stream reuses an existing running stream."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {"model_id": "stabilityai/sd-turbo"},
    }

    # Mock the HTTP session
    with patch.object(start_node, "_session") as mock_session:
        # Mock status check - stream already running
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {
            "running": True,
            "stream_id": "existing_stream_789",
            "playback_id": "existing_playback_012",
            "whip_url": "https://whip.example.com/existing",
        }
        
        mock_session.get.return_value = mock_status_response
        
        # Execute the node
        stream_id, playback_id, whip_url = start_node.start_stream(pipeline_config)
        
        # Verify results use existing stream
        assert stream_id == "existing_stream_789"
        assert playback_id == "existing_playback_012"
        assert whip_url == "https://whip.example.com/existing"
        
        # Verify POST was NOT called (reused existing stream)
        mock_session.post.assert_not_called()


def test_start_stream_caching(start_node, mock_server_status, mock_ensure_server):
    """Test that start_stream uses cache for identical inputs."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {"model_id": "stabilityai/sd-turbo"},
    }

    # Mock the HTTP session
    with patch.object(start_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": False}
        
        mock_start_response = MagicMock()
        mock_start_response.json.return_value = {
            "stream_id": "cached_stream",
            "playback_id": "cached_playback",
            "whip_url": "https://whip.example.com/cached",
        }
        
        mock_session.get.return_value = mock_status_response
        mock_session.post.return_value = mock_start_response
        
        # First call - should make HTTP request
        result1 = start_node.start_stream(pipeline_config)
        assert mock_session.post.call_count == 1
        
        # Second call with same config - should use cache
        result2 = start_node.start_stream(pipeline_config)
        assert result1 == result2
        # Should not make another POST request
        assert mock_session.post.call_count == 1


def test_start_stream_stop_request_resets_toggle(start_node, mock_server_status, mock_ensure_server):
    """Test that stop_stream flag triggers stop endpoint and resets widget."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {"model_id": "stabilityai/sd-turbo"},
    }
    extra_pnginfo = {
        "workflow": {
            "nodes": [
                {
                    "id": "node-1",
                    "widgets_values": ["", True],
                }
            ]
        }
    }

    with patch.object(start_node, "_session") as mock_session:
        mock_stop_response = MagicMock()
        mock_session.post.return_value = mock_stop_response

        result = start_node.start_stream(
            pipeline_config,
            stream_name="",
            stop_stream=True,
            unique_id="node-1",
            extra_pnginfo=extra_pnginfo,
        )

        # Stop returns empty identifiers
        assert result == ("", "", "")

        # Ensure /stop endpoint was called
        mock_session.post.assert_called_once()
        stop_url = mock_session.post.call_args[0][0]
        assert stop_url.endswith("/stop")

        # Widget value should have been reset to False
        widget_value = extra_pnginfo["workflow"]["nodes"][0]["widgets_values"][1]
        assert widget_value is False


def test_is_changed_returns_hash():
    """Test that IS_CHANGED returns a consistent hash for inputs."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {"model_id": "test"}}
    
    # Same inputs should produce same hash
    hash1 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=30, width=512, height=512, enabled=True, stop_stream=False)
    hash2 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=30, width=512, height=512, enabled=True, stop_stream=False)
    assert hash1 == hash2
    
    # Different stream name should produce different hash
    hash3 = StartRTCStream.IS_CHANGED(pipeline_config, "stream2", fps=30, width=512, height=512, enabled=True, stop_stream=False)
    assert hash1 != hash3

    # Different fps should produce different hash
    hash4 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=60, width=512, height=512, enabled=True, stop_stream=False)
    assert hash1 != hash4

    # Different dimensions should produce different hash
    hash5 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=30, width=1024, height=512, enabled=True, stop_stream=False)
    assert hash1 != hash5

    # Disabled should produce different hash
    hash6 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=30, width=512, height=512, enabled=False, stop_stream=False)
    assert hash1 != hash6

    # Stop toggle should force a different hash
    hash7 = StartRTCStream.IS_CHANGED(pipeline_config, "stream1", fps=30, width=512, height=512, enabled=True, stop_stream=True)
    assert hash1 != hash7


def test_start_stream_disabled_returns_cached_or_empty(start_node, mock_server_status, mock_ensure_server):
    """Test that disabled node returns cached results or empty values."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {"model_id": "stabilityai/sd-turbo"},
    }

    # First, populate the cache
    start_node._cached_result = ("cached_id", "cached_playback", "cached_whip")

    with patch.object(start_node, "_session") as mock_session:
        # Execute with enabled=False
        result = start_node.start_stream(
            pipeline_config,
            stream_name="test",
            fps=30,
            width=512,
            height=512,
            enabled=False,
            stop_stream=False
        )

        # Should return cached values
        assert result == ("cached_id", "cached_playback", "cached_whip")
        
        # Should NOT make any HTTP requests
        mock_session.get.assert_not_called()
        mock_session.post.assert_not_called()

    # Test with no cache
    start_node._cached_result = None

    with patch.object(start_node, "_session") as mock_session:
        result = start_node.start_stream(
            pipeline_config,
            stream_name="test",
            fps=30,
            width=512,
            height=512,
            enabled=False,
            stop_stream=False
        )

        # Should return empty values
        assert result == ("", "", "")
        
        # Should NOT make any HTTP requests
        mock_session.get.assert_not_called()
        mock_session.post.assert_not_called()


def test_send_notification(start_node):
    """Test that _send_notification sends messages to PromptServer."""
    with patch("nodes.frame_nodes.PromptServer") as mock_server_class:
        mock_instance = MagicMock()
        mock_server_class.instance = mock_instance
        
        start_node._send_notification("success", "Test Summary", "Test Detail")
        
        mock_instance.send_sync.assert_called_once_with(
            "rtc-stream-notification",
            {
                "severity": "success",
                "summary": "Test Summary",
                "detail": "Test Detail",
            },
        )


# UpdateRTCStream Node Tests

@pytest.fixture
def update_node():
    """Create an UpdateRTCStream node instance."""
    return UpdateRTCStream()


def test_update_stream_success(update_node, mock_server_status, mock_ensure_server):
    """Test that update_stream successfully updates a running stream."""
    pipeline_config = {
        "pipeline": "streamdiffusion",
        "params": {
            "model_id": "stabilityai/sd-turbo",
            "prompt": "updated prompt",
            "guidance_scale": 8.0,
        },
    }

    with patch.object(update_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {
            "running": True,
            "stream_id": "test_stream_123",
        }
        mock_status_response.raise_for_status.return_value = None

        mock_patch_response = MagicMock()
        mock_patch_response.json.return_value = {"updated": True}

        mock_session.get.return_value = mock_status_response
        mock_session.patch.return_value = mock_patch_response

        # Execute the node
        result = update_node.update_stream(pipeline_config)

        # Verify results
        assert result == ()

        # Verify PATCH was called with correct payload
        mock_session.patch.assert_called_once()
        call_args = mock_session.patch.call_args
        assert "/pipeline" in call_args[0][0]
        assert call_args[1]["json"]["pipeline_config"] == pipeline_config


def test_update_stream_disabled(update_node):
    """Ensure update_stream returns early when disabled."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {}}

    with patch.object(update_node, "_session") as mock_session:
        result = update_node.update_stream(pipeline_config, enabled=False)

        assert result == ()
        mock_session.get.assert_not_called()
        mock_session.patch.assert_not_called()


def test_update_stream_enable_toggle(update_node, mock_server_status, mock_ensure_server):
    """Toggling from disabled to enabled should run update once."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {}}

    with patch.object(update_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": True, "stream_id": "test"}
        mock_status_response.raise_for_status.return_value = None
        mock_patch_response = MagicMock()
        mock_patch_response.json.return_value = {"updated": True}

        mock_session.get.return_value = mock_status_response
        mock_session.patch.return_value = mock_patch_response

        update_node.update_stream(pipeline_config, enabled=False)
        assert mock_session.get.call_count == 0

        update_node.update_stream(pipeline_config, enabled=True)
        assert mock_session.get.call_count == 1
        assert mock_session.patch.call_count == 1


def test_update_stream_no_active_stream(update_node, mock_server_status, mock_ensure_server):
    """Test that update_stream skips when no stream is running."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {}}

    with patch.object(update_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": False}
        mock_status_response.raise_for_status.return_value = None

        mock_session.get.return_value = mock_status_response

        # Execute node
        result = update_node.update_stream(pipeline_config)

        assert result == ()
        mock_session.patch.assert_not_called()


def test_update_stream_method_not_allowed(update_node, mock_server_status, mock_ensure_server):
    """Test that update_stream handles 405 error (method not allowed)."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {}}

    with patch.object(update_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": True, "stream_id": "test_stream_123"}
        mock_status_response.raise_for_status.return_value = None

        mock_patch_response = MagicMock()
        mock_patch_response.raise_for_status.side_effect = requests.RequestException("405: Method Not Allowed")

        mock_session.get.return_value = mock_status_response
        mock_session.patch.return_value = mock_patch_response

        result = update_node.update_stream(pipeline_config)
        assert result == ()

def test_update_stream_error_handling(update_node, mock_server_status, mock_ensure_server):
    """Test that update_stream handles 409 error (no active stream)."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {}}

    with patch.object(update_node, "_session") as mock_session:
        mock_status_response = MagicMock()
        mock_status_response.json.return_value = {"running": True, "stream_id": "test_stream_123"}
        mock_status_response.raise_for_status.return_value = None

        mock_patch_response = MagicMock()
        mock_patch_response.raise_for_status.side_effect = requests.RequestException("409: No active stream")

        mock_session.get.return_value = mock_status_response
        mock_session.patch.return_value = mock_patch_response

        result = update_node.update_stream(pipeline_config)
        assert result == ()


def test_update_stream_is_changed_returns_hash():
    """Test that IS_CHANGED returns consistent hash based on config only."""
    pipeline_config = {"pipeline": "streamdiffusion", "params": {"prompt": "test"}}

    hash1 = UpdateRTCStream.IS_CHANGED(pipeline_config)
    hash2 = UpdateRTCStream.IS_CHANGED(pipeline_config)
    assert hash1 == hash2

    hash3 = UpdateRTCStream.IS_CHANGED({"pipeline": "streamdiffusion", "params": {"prompt": "different"}})
    assert hash1 != hash3


def test_update_stream_caching_behavior(update_node, mock_server_status, mock_ensure_server):
    """Test that ComfyUI caching works as expected with IS_CHANGED."""
    config1 = {"pipeline": "streamdiffusion", "params": {"prompt": "prompt1"}}
    config2 = {"pipeline": "streamdiffusion", "params": {"prompt": "prompt2"}}

    hash1 = UpdateRTCStream.IS_CHANGED(config1)
    hash2 = UpdateRTCStream.IS_CHANGED(config1)
    assert hash1 == hash2

    hash3 = UpdateRTCStream.IS_CHANGED(config2)
    assert hash1 != hash3


# RTCStreamStatus Node Tests

@pytest.fixture
def status_node():
    """Create an RTCStreamStatus node instance."""
    return RTCStreamStatus()


def test_status_node_fetches_status(status_node, mock_server_status, mock_ensure_server):
    """Test that status node successfully fetches stream status."""
    mock_status_data = {
        "running": True,
        "stream_id": "test_stream_123",
        "playback_id": "test_playback_456",
        "whip_url": "https://whip.example.com/test",
        "frames_sent": 100,
        "queue_depth": 5,
        "started_at": 1234567890.0,
        "remote_status": {},
        "queue_stats": {"depth": 5},
        "stream_settings": {"frame_rate": 30, "frame_width": 1280, "frame_height": 720},
    }

    with patch.object(status_node, "_session") as mock_session:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_status_data
        mock_session.get.return_value = mock_response
        
        # Execute the node
        running, stream_id, playback_id, whip_url, frames_sent, queue_depth, status_json = (
            status_node.get_status(refresh_interval=5.0)
        )
        
        # Verify results
        assert running is True
        assert stream_id == "test_stream_123"
        assert playback_id == "test_playback_456"
        assert whip_url == "https://whip.example.com/test"
        assert frames_sent == 100
        assert queue_depth == 5
        assert "test_stream_123" in status_json
        
        # Verify GET was called
        mock_session.get.assert_called_once()


def test_status_node_caching(status_node, mock_server_status, mock_ensure_server):
    """Test that status node caches responses within refresh interval."""
    mock_status_data = {
        "running": True,
        "stream_id": "cached_stream",
        "playback_id": "cached_playback",
        "whip_url": "https://whip.example.com/cached",
        "frames_sent": 50,
        "queue_depth": 2,
    }

    with patch.object(status_node, "_session") as mock_session:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_status_data
        mock_session.get.return_value = mock_response
        
        # First call - should fetch
        result1 = status_node.get_status(refresh_interval=10.0)
        assert mock_session.get.call_count == 1
        
        # Second call immediately after - should use cache
        result2 = status_node.get_status(refresh_interval=10.0)
        assert mock_session.get.call_count == 1  # No additional call
        assert result1 == result2


def test_status_node_refresh_after_interval(status_node, mock_server_status, mock_ensure_server):
    """Test that status node refreshes after interval expires."""
    mock_status_data = {
        "running": True,
        "stream_id": "refresh_test",
        "playback_id": "refresh_playback",
        "whip_url": "https://whip.example.com/refresh",
        "frames_sent": 75,
        "queue_depth": 3,
    }

    with patch.object(status_node, "_session") as mock_session:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_status_data
        mock_session.get.return_value = mock_response
        
        # First call
        status_node.get_status(refresh_interval=0.1)  # 0.1 second interval
        assert mock_session.get.call_count == 1
        
        # Sleep to exceed interval
        time.sleep(0.15)
        
        # Second call - should refresh
        status_node.get_status(refresh_interval=0.1)
        assert mock_session.get.call_count == 2  # New call made


def test_status_node_no_cache_mode(status_node, mock_server_status, mock_ensure_server):
    """Test that refresh_interval=0 disables caching."""
    mock_status_data = {
        "running": True,
        "stream_id": "no_cache",
        "playback_id": "no_cache_playback",
        "whip_url": "https://whip.example.com/nocache",
        "frames_sent": 25,
        "queue_depth": 1,
    }

    with patch.object(status_node, "_session") as mock_session:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_status_data
        mock_session.get.return_value = mock_response
        
        # Multiple calls with interval=0 - should always fetch
        status_node.get_status(refresh_interval=0.0)
        status_node.get_status(refresh_interval=0.0)
        status_node.get_status(refresh_interval=0.0)
        
        # Should have made 3 separate calls
        assert mock_session.get.call_count == 3


def test_status_node_is_changed_behavior():
    """Test that IS_CHANGED returns appropriate values for caching."""
    # With interval > 0, value should change over time buckets
    with patch("time.time", return_value=10.0):
        val1 = RTCStreamStatus.IS_CHANGED(stream_id="stream-a", refresh_interval=5.0)

    with patch("time.time", return_value=12.0):
        val2 = RTCStreamStatus.IS_CHANGED(stream_id="stream-a", refresh_interval=5.0)

    # Still within same 5-second window
    assert val1 == val2

    with patch("time.time", return_value=16.0):
        val3 = RTCStreamStatus.IS_CHANGED(stream_id="stream-a", refresh_interval=5.0)

    # Now in next 5-second window
    assert val1 != val3

    # stream_id change should force refresh immediately
    with patch("time.time", return_value=12.0):
        val4 = RTCStreamStatus.IS_CHANGED(stream_id="stream-b", refresh_interval=5.0)
    assert val1 != val4

    # With interval = 0, should always be different
    val5 = RTCStreamStatus.IS_CHANGED(stream_id="stream-a", refresh_interval=0.0)
    time.sleep(0.01)
    val6 = RTCStreamStatus.IS_CHANGED(stream_id="stream-a", refresh_interval=0.0)
    assert val5 != val6


def test_status_node_error_handling(status_node, mock_server_status, mock_ensure_server):
    """Test that status node handles errors gracefully."""
    with patch.object(status_node, "_session") as mock_session:
        # Mock request exception
        mock_session.get.side_effect = requests.RequestException("Connection failed")
        
        # Execute the node
        running, stream_id, playback_id, whip_url, frames_sent, queue_depth, status_json = (
            status_node.get_status(refresh_interval=5.0)
        )
        
        # Should return empty values
        assert running is False
        assert stream_id == ""
        assert playback_id == ""
        assert whip_url == ""
        assert frames_sent == 0
        assert queue_depth == 0
        assert status_json == "{}"


def test_status_node_uses_cached_on_error(status_node, mock_server_status, mock_ensure_server):
    """Test that status node uses cached data if fetch fails."""
    mock_status_data = {
        "running": True,
        "stream_id": "cached_on_error",
        "playback_id": "cached_playback",
        "whip_url": "https://whip.example.com/cached",
        "frames_sent": 200,
        "queue_depth": 10,
    }

    with patch.object(status_node, "_session") as mock_session:
        # First call succeeds
        mock_response = MagicMock()
        mock_response.json.return_value = mock_status_data
        mock_session.get.return_value = mock_response
        
        result1 = status_node.get_status(refresh_interval=0.1)
        
        # Sleep to exceed interval
        time.sleep(0.15)
        
        # Second call fails
        mock_session.get.side_effect = requests.RequestException("Network error")
        
        result2 = status_node.get_status(refresh_interval=0.1)
        
        # Should return cached data from first call
        assert result2 == result1
        assert result2[1] == "cached_on_error"  # stream_id

