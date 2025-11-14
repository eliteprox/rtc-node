import argparse
import asyncio
import json
import logging
import os
import uuid
from fractions import Fraction
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import av
import numpy as np
import requests
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("stream_whip")


def get_credentials() -> Tuple[str, str]:
    api_url = os.environ.get("DAYDREAM_API_URL", "").strip()
    api_key = os.environ.get("DAYDREAM_API_KEY", "").strip()
    if not api_url or not api_key:
        raise ValueError("DAYDREAM_API_URL and DAYDREAM_API_KEY must be set in the environment")
    return api_url, api_key


def start_stream(
    api_url: str,
    api_key: str,
    pipeline_config: Dict[str, Any],
    stream_name: str = "",
    max_duration: int = 3600,
) -> Tuple[str, str, str]:
    """
    Create a stream via the Daydream API and return the WHIP URL with playback identifiers.
    """

    if not api_key.strip():
        api_url, api_key = get_credentials()
        LOGGER.info("Loaded API credentials from environment")

    if not api_url.strip():
        raise ValueError("api_url must be provided either via argument or environment")

    if not stream_name:
        stream_name = f"comfyui-stream-{uuid.uuid4().hex[:8]}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept-Encoding": "identity",
    }

    if not isinstance(pipeline_config, dict):
        raise ValueError("pipeline_config must be a dictionary payload")

    if "params" in pipeline_config:
        pipeline_name = pipeline_config.get("pipeline", "streamdiffusion")
        params_section = pipeline_config.get("params")
    else:
        pipeline_name = "streamdiffusion"
        params_section = pipeline_config

    if not isinstance(params_section, dict):
        raise ValueError("pipeline_config params must be a dictionary")

    try:
        params_payload = json.loads(json.dumps(params_section))
    except TypeError as exc:
        raise ValueError(f"Pipeline params contain non-serialisable values: {exc}") from exc

    stream_request = {"pipeline": pipeline_name, "params": params_payload, "name": stream_name}
    stream_endpoint = "v1/streams"
    normalized_api_url = api_url.rstrip("/")
    if normalized_api_url.endswith("/" + stream_endpoint):
        create_stream_url = api_url
    else:
        create_stream_url = urljoin(normalized_api_url + "/", stream_endpoint)
    LOGGER.info("Creating stream at %s", create_stream_url)
    LOGGER.debug("Stream payload: %s", json.dumps(stream_request))

    response = requests.post(
        create_stream_url,
        headers=headers,
        json=stream_request,
        timeout=30,
    )

    if response.status_code != 201:
        raise RuntimeError(
            f"Failed to create stream {response.status_code}: {response.text}"
        )

    stream_data = response.json()
    whip_url = stream_data.get("whip_url", "")
    output_playback_id = stream_data.get("output_playback_id", "")
    stream_id = stream_data.get("id", "")
    return whip_url, output_playback_id, stream_id


class VideoSourceTrack(VideoStreamTrack):
    def __init__(self, video_path: Optional[str], frame_rate: float = 30.0):
        super().__init__()
        self.video_path = video_path
        self.frame_rate = frame_rate
        self._frame_iter = None
        self._dummy_frame_count = 0
        self.container: Optional[av.container.InputContainer] = None
        self.stream: Optional[av.video.stream.VideoStream] = None

        if video_path:
            self.container = av.open(video_path)
            self.stream = self.container.streams.video[0]
            self.stream.thread_type = "AUTO"
            self._frame_iter = self.container.decode(self.stream)
            avg_rate = self.stream.average_rate
            if avg_rate:
                self.frame_rate = float(avg_rate)
            self._time_base = self.stream.time_base
        else:
            self._time_base = Fraction(1, int(round(frame_rate)))
        self._frame_interval = 1 / self.frame_rate

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(self._frame_interval)

        if self.container and self._frame_iter:
            try:
                frame = next(self._frame_iter)
            except StopIteration:
                self.container.seek(0)
                self._frame_iter = self.container.decode(self.stream)
                frame = next(self._frame_iter)
            frame.pts = frame.pts or self._dummy_frame_count
            frame.time_base = self._time_base
            frame = frame.reformat(format="yuv420p")
            pts = frame.pts
        else:
            image = np.zeros((720, 1280, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(image, format="bgr24")
            frame = frame.reformat(format="yuv420p")
            frame.pts = self._dummy_frame_count
            frame.time_base = self._time_base
            pts = frame.pts

        self._dummy_frame_count = max(self._dummy_frame_count, pts + 1)
        frame_seconds = float(pts * frame.time_base)
        LOGGER.info("Sending frame pts=%s ts=%.3f", pts, frame_seconds)
        return frame


async def stream_to_whip(
    whip_url: str,
    video_path: Optional[str],
    max_duration: int = 3600,
) -> None:
    pc = RTCPeerConnection()
    track = VideoSourceTrack(video_path)
    pc.addTrack(track)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    response = requests.post(
        whip_url,
        headers={"Content-Type": "application/sdp"},
        data=offer.sdp,
        timeout=30,
    )
    response.raise_for_status()
    answer = RTCSessionDescription(sdp=response.text, type="answer")
    await pc.setRemoteDescription(answer)

    try:
        await asyncio.sleep(max_duration)
    finally:
        await pc.close()


def load_pipeline_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as source_file:
        return json.load(source_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream video to WHIP ingest endpoint.")
    parser.add_argument("--api-url", required=True, default="", help="API URL to use for the Daydream API")
    parser.add_argument("--api-key", required=True, default="", help="API key to use for the Daydream API")
    parser.add_argument("--pipeline-config", required=True)
    parser.add_argument("--video-file", required=False)
    parser.add_argument("--stream-name", required=False, default="comfyworkflow")
    parser.add_argument("--duration", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pipeline_config = load_pipeline_config(args.pipeline_config)

    whip_url, playback_id, stream_id = start_stream(
        api_url=args.api_url or "",
        api_key=args.api_key,
        pipeline_config=pipeline_config,
        stream_name=args.stream_name,
        max_duration=args.duration,
    )
    LOGGER.info("Streaming to WHIP URL %s (stream: %s, playback: %s)", whip_url, stream_id, playback_id)

    asyncio.run(stream_to_whip(whip_url, args.video_file, max_duration=args.duration))
    LOGGER.info("Streaming session complete. Playback ID: %s", playback_id)


if __name__ == "__main__":
    main()

