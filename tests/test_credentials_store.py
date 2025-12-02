import importlib
import json

import pytest

import rtc_stream.credentials_store as credentials_store_module


@pytest.fixture
def temp_credentials_store(tmp_path, monkeypatch):
    settings_path = tmp_path / "comfy.settings.json"
    monkeypatch.setenv("RTC_NODE_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("DAYDREAM_API_URL", raising=False)
    monkeypatch.delenv("DAYDREAM_API_KEY", raising=False)
    store = importlib.reload(credentials_store_module)
    try:
        yield store, settings_path
    finally:
        monkeypatch.delenv("RTC_NODE_SETTINGS_PATH", raising=False)
        importlib.reload(credentials_store_module)


def test_persist_and_load_credentials(temp_credentials_store):
    store, settings_path = temp_credentials_store

    state = store.persist_credentials_to_env(
        api_url="https://example.com/v1/",
        api_key="abc123\n",
    )

    assert state["api_url"] == "https://example.com/v1"
    assert state["api_key"] == "abc123"
    assert state["sources"]["api_url"] == "settings"
    assert settings_path.exists()
    contents = json.loads(settings_path.read_text(encoding="utf-8"))
    assert contents["daydream_live.api_base_url"] == "https://example.com/v1"
    assert contents["daydream_live.api_key"] == "abc123"

    cleared = store.persist_credentials_to_env(api_key="")
    assert cleared["api_key"] == ""
    assert cleared["sources"]["api_key"] in {"missing", "settings"}
    contents = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "daydream_live.api_key" not in contents


def test_load_credentials_falls_back_to_process_env(temp_credentials_store, monkeypatch):
    store, settings_path = temp_credentials_store
    monkeypatch.setenv("DAYDREAM_API_URL", "https://fallback.example/api")
    monkeypatch.setenv("DAYDREAM_API_KEY", "from-env")

    state = store.load_credentials_from_env()
    assert state["api_url"] == "https://fallback.example/api"
    assert state["sources"]["api_url"] == "env"
    assert state["api_key"] == "from-env"
    assert state["sources"]["api_key"] == "env"
    assert not settings_path.exists()

