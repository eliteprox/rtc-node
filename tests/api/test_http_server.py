import pytest
import json
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.app import router, bootstrap_controller
import server.app as app_module

@pytest.fixture
def client(pipeline_config_path, mock_daydream_api, mock_pc):
    # Setup
    bootstrap_controller(
        api_url="http://test-api",
        api_key="test-key",
        pipeline_config=str(pipeline_config_path)
    )
    
    app = FastAPI()
    app.include_router(router)
    
    # Use TestClient which handles the async loop for the app
    with TestClient(app) as test_client:
        yield test_client
    
    # Teardown
    app_module.controller = None
    app_module.whep_controller = None

def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "queue_depth" in data

def test_start_stop_stream(client, mock_daydream_api):
    # Start
    response = client.post("/start", json={"stream_name": "test_run"})
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True
    assert data["stream_id"] == "stream-123"
    
    # Check status
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["running"] is True
    
    # Stop
    response = client.post("/stop")
    assert response.status_code == 200
    assert response.json()["running"] is False

def test_config_endpoints(client):
    # Get initial config
    response = client.get("/config")
    assert response.status_code == 200
    initial = response.json()
    assert "frame_rate" in initial
    assert initial["locked"] is False
    
    # Update config
    new_config = {
        "frame_rate": 60,
        "frame_width": 1920,
        "frame_height": 1080
    }
    response = client.post("/config", json=new_config)
    assert response.status_code == 200
    updated = response.json()
    assert updated["frame_rate"] == 60
    
    # Verify persistence (in memory for this test run)
    response = client.get("/config")
    assert response.json()["frame_rate"] == 60

def test_frame_push(client):
    # Create a tiny 1x1 white pixel PNG base64
    import base64
    import io
    from PIL import Image
    
    img = Image.new('RGB', (1, 1), color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    img_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    
    response = client.post("/frames", json={"frame_b64": img_b64})
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    
    # Verify depth increased
    health = client.get("/healthz")
    assert health.json()["queue_depth"] > 0

def test_pipeline_cache(client):
    payload = {
        "pipeline_config": {
            "pipeline": "new_pipeline",
            "params": {"foo": "bar"}
        }
    }
    response = client.post("/pipeline/cache", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["cached"] is True
    assert data["pipeline"] == "new_pipeline"
