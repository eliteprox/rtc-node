import importlib

import pytest

import rtc_stream.credentials_store as credentials_store_module


@pytest.fixture
def temp_credentials_store(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("RTC_NODE_ENV_PATH", str(env_path))
    store = importlib.reload(credentials_store_module)
    try:
        yield store, env_path
    finally:
        monkeypatch.delenv("RTC_NODE_ENV_PATH", raising=False)
        importlib.reload(credentials_store_module)


def test_persist_and_load_credentials(temp_credentials_store):
    store, env_path = temp_credentials_store

    state = store.persist_credentials_to_env(
        api_url="https://example.com/v1/",
        api_key="abc123\n",
    )

    assert state["api_url"] == "https://example.com/v1"
    assert state["api_key"] == "abc123"
    assert state["sources"]["api_url"] == "file"
    assert env_path.exists()
    contents = env_path.read_text(encoding="utf-8")
    assert "DAYDREAM_API_URL=https://example.com/v1" in contents
    assert "DAYDREAM_API_KEY=abc123" in contents

    cleared = store.persist_credentials_to_env(api_key="")
    assert cleared["api_key"] == ""
    assert cleared["sources"]["api_key"] == "missing"
    contents = env_path.read_text(encoding="utf-8")
    assert "DAYDREAM_API_KEY=" not in contents


def test_load_credentials_falls_back_to_process_env(temp_credentials_store, monkeypatch):
    store, env_path = temp_credentials_store
    monkeypatch.setenv("DAYDREAM_API_URL", "https://fallback.example/api")
    monkeypatch.setenv("DAYDREAM_API_KEY", "from-env")

    state = store.load_credentials_from_env()
    assert state["api_url"] == "https://fallback.example/api"
    assert state["sources"]["api_url"] == "env"
    assert state["api_key"] == "from-env"
    assert state["sources"]["api_key"] == "env"
    assert not env_path.exists()

