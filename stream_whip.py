import argparse
import asyncio
import json
import logging
from fractions import Fraction
from typing import Any, Dict, Optional

import av
import numpy as np
import requests
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from rtc_stream.credentials import resolve_credentials
from rtc_stream.daydream import StreamInfo, get_stream_info, start_stream as create_stream

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("stream_whip")


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
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun.cloudflare.com:3478"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun3.l.google.com:19302"]),
        ]
    )
    pc = RTCPeerConnection(configuration=config)
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
    parser.add_argument(
        "--api-url",
        required=False,
        default="",
        help="API URL to use for the Daydream API (defaults to DAYDREAM_API_URL or https://api.daydream.live)",
    )
    parser.add_argument(
        "--api-key",
        required=False,
        default="",
        help="API key to use for the Daydream API (defaults to DAYDREAM_API_KEY)",
    )
    parser.add_argument(
        "--pipeline-config",
        required=False,
        help="Path to pipeline config JSON (required unless --stream-id is supplied)",
    )
    parser.add_argument(
        "--stream-id",
        required=False,
        default=None,
        help="Existing stream ID to reuse; skip creating a new stream",
    )
    parser.add_argument("--video-file", required=False)
    parser.add_argument("--stream-name", required=False, default="comfyworkflow")
    parser.add_argument("--duration", type=int, default=60, help="Stream duration in seconds (ignored if --loop is set)")
    parser.add_argument("--loop", action="store_true", help="Stream video file in continuous loop until interrupted")
    args = parser.parse_args()
    if not args.stream_id and not args.pipeline_config:
        parser.error("Either --pipeline-config or --stream-id must be provided.")
    return args


def main() -> None:
    args = parse_args()

    api_url, api_key = resolve_credentials(args.api_url or "", args.api_key or "")
    stream_info: StreamInfo

    if args.stream_id:
        stream_info = get_stream_info(api_url, api_key, args.stream_id)
        LOGGER.info("Reusing existing stream %s", stream_info.stream_id)
    else:
        assert args.pipeline_config is not None
        pipeline_config = load_pipeline_config(args.pipeline_config)
        stream_info = create_stream(
            api_url=api_url,
            api_key=api_key,
            pipeline_config=pipeline_config,
            stream_name=args.stream_name,
        )

    LOGGER.info(
        "Streaming to WHIP URL %s (stream: %s, playback: %s)",
        stream_info.whip_url,
        stream_info.stream_id,
        stream_info.playback_id,
    )

    # Use a very large duration if loop is enabled (effectively infinite until Ctrl+C)
    duration = 999999999 if args.loop else args.duration

    try:
        asyncio.run(stream_to_whip(stream_info.whip_url, args.video_file, max_duration=duration))
    except KeyboardInterrupt:
        LOGGER.info("Stream interrupted by user")

    LOGGER.info("Streaming session complete. Playback ID: %s", stream_info.playback_id)


if __name__ == "__main__":
    main()

