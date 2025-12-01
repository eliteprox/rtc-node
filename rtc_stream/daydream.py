import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

from .credentials import resolve_credentials


LOGGER = logging.getLogger("rtc_stream.daydream")


@dataclass
class StreamInfo:
    whip_url: str
    playback_id: str
    stream_id: str
    whep_url: str = ""
    stream_name: str = ""


def start_stream(
    api_url: str,
    api_key: str,
    pipeline_config: Dict[str, Any],
    stream_name: str = "",
    session: Optional[requests.Session] = None,
) -> StreamInfo:
    if not isinstance(pipeline_config, dict):
        raise ValueError("pipeline_config must be dict")

    if "params" in pipeline_config:
        pipeline_name = pipeline_config.get("pipeline", "streamdiffusion")
        params_section = pipeline_config.get("params")
    else:
        pipeline_name = "streamdiffusion"
        params_section = pipeline_config

    if not isinstance(params_section, dict):
        raise ValueError("pipeline_config params must be dict")

    params_payload = json.loads(json.dumps(params_section))
    if not stream_name:
        stream_name = f"comfyui-stream-{int(time.time())}"

    stream_request = {"pipeline": pipeline_name, "params": params_payload, "name": stream_name}
    stream_endpoint = "v1/streams"
    normalized_api_url = api_url.rstrip("/")
    if normalized_api_url.endswith("/" + stream_endpoint):
        create_stream_url = api_url
    else:
        create_stream_url = urljoin(normalized_api_url + "/", stream_endpoint)

    sess = session or requests.Session()
    response = sess.post(
        create_stream_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept-Encoding": "identity",
        },
        json=stream_request,
        timeout=30,
    )
    if response.status_code != 201:
        raise RuntimeError(f"Failed to create stream {response.status_code}: {response.text}")

    stream_data = response.json()
    LOGGER.info("Stream created: %s", stream_data.get("id", "unknown"))
    return StreamInfo(
        whip_url=stream_data.get("whip_url", ""),
        playback_id=stream_data.get("output_playback_id", ""),
        stream_id=stream_data.get("id", ""),
        whep_url=stream_data.get("whep_url", ""),
        stream_name=stream_data.get("name", stream_name),
    )


def get_stream_info(
    api_url: str,
    api_key: str,
    stream_id: str,
    session: Optional[requests.Session] = None,
) -> StreamInfo:
    """
    Retrieve the metadata for an existing stream.
    """
    if not stream_id:
        raise ValueError("stream_id is required to fetch stream info")

    resolved_url, resolved_key = resolve_credentials(api_url or "", api_key or "")

    stream_endpoint = "v1/streams"
    normalized = resolved_url.rstrip("/")
    if normalized.endswith("/" + stream_endpoint):
        base_url = normalized.rsplit("/" + stream_endpoint, 1)[0]
    elif normalized.endswith(stream_endpoint):
        base_url = normalized.rsplit(stream_endpoint, 1)[0].rstrip("/")
    else:
        base_url = normalized

    target = urljoin(base_url + "/", f"{stream_endpoint}/{stream_id}")
    sess = session or requests.Session()
    response = sess.get(
        target,
        headers={
            "Authorization": f"Bearer {resolved_key}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch stream info {stream_id}: {response.status_code} {response.text}"
        )
    stream_data = response.json()
    LOGGER.info("Fetched existing stream %s", stream_id)
    return StreamInfo(
        whip_url=stream_data.get("whip_url", ""),
        playback_id=stream_data.get("output_playback_id", ""),
        stream_id=stream_data.get("id", stream_id),
        whep_url=stream_data.get("whep_url", ""),
        stream_name=stream_data.get("name", ""),
    )


def poll_stream_status(
    api_url: str,
    api_key: str,
    stream_id: str,
    timeout: int = 60,
    interval: float = 2.0,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Poll the Daydream status endpoint until the stream becomes ready or timeout.
    Returns a dictionary containing the HTTP status code and body payload so
    callers can differentiate between OFFLINE, NOT FOUND, etc.
    """

    if not stream_id:
        raise ValueError("stream_id is required for status polling")

    sess = session or requests.Session()
    target = urljoin(api_url.rstrip("/") + "/", f"v1/streams/{stream_id}/status")

    deadline = time.time() + timeout
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        response = sess.get(
            target,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=15,
        )
        body = {}
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text}

        payload = {"http_status": response.status_code, "body": body}

        if response.status_code == 200:
            last_payload = payload
            # When Daydream reports ready/online we can stop polling.
            body_state = (
                body.get("status")
                or body.get("state")
                or body.get("data", {}).get("state")
            )
            if isinstance(body_state, str) and body_state.lower() in ("ready", "online"):
                return payload
        else:
            LOGGER.warning("Status poll failed: %s %s", response.status_code, body)
            last_payload = payload
        time.sleep(interval)

    return last_payload


def update_stream(
    api_url: str,
    api_key: str,
    stream_id: str,
    pipeline_config: Dict[str, Any],
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Update pipeline parameters for an existing stream.
    Forwards the update to Daydream API's PATCH /v1/streams/{id} endpoint.
    """
    if not isinstance(pipeline_config, dict):
        raise ValueError("pipeline_config must be dict")

    if "params" in pipeline_config:
        pipeline_name = pipeline_config.get("pipeline", "streamdiffusion")
        params_section = pipeline_config.get("params")
    else:
        pipeline_name = "streamdiffusion"
        params_section = pipeline_config

    if not isinstance(params_section, dict):
        raise ValueError("pipeline_config params must be dict")

    params_payload = json.loads(json.dumps(params_section))
    update_request = {"pipeline": pipeline_name, "params": params_payload}

    # Normalize the base URL similar to start_stream
    stream_endpoint = "v1/streams"
    normalized_api_url = api_url.rstrip("/")
    
    # Remove the endpoint if it's already in the URL
    if normalized_api_url.endswith("/" + stream_endpoint):
        base_url = normalized_api_url.rsplit("/" + stream_endpoint, 1)[0]
    elif normalized_api_url.endswith(stream_endpoint):
        base_url = normalized_api_url.rsplit(stream_endpoint, 1)[0].rstrip("/")
    else:
        base_url = normalized_api_url
    
    # Construct the update URL
    update_url = urljoin(base_url + "/", f"{stream_endpoint}/{stream_id}")
    
    LOGGER.info("Updating stream at %s", update_url)
    LOGGER.debug("Update payload: %s", json.dumps(update_request))

    sess = session or requests.Session()
    response = sess.patch(
        update_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept-Encoding": "identity",
        },
        json=update_request,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        error_msg = f"Failed to update stream {response.status_code}: {response.text}"
        LOGGER.error("Update request failed - URL: %s, Status: %s, Response: %s", 
                     update_url, response.status_code, response.text)
        
        # Check if it's a 405 Method Not Allowed
        if response.status_code == 405:
            LOGGER.warning(
                "PATCH method not allowed. The Daydream API endpoint may not support "
                "runtime updates yet. Consider stopping and restarting the stream with new parameters."
            )
        
        raise RuntimeError(error_msg)

    stream_data = response.json()
    LOGGER.info("Stream %s updated successfully", stream_id)
    return stream_data

