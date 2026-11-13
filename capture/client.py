"""The capture client's network half.

One WebSocket connection per lane, because the protocol establishes a lane in
the hello and never mixes two in one stream. Two connections is also what makes
a split setup possible later — one machine capturing the microphone, another
capturing the meeting audio — without changing anything here.

Frames are stamped with this client's monotonic clock at the moment they leave
the source. Wall-clock time is not used: it can step backwards, and the core
only needs ordering and jitter within a lane.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator

import websockets

from capture.backends.base import AudioSource
from capture.codec import OpusEncoder
from core import protocol as p

log = logging.getLogger("susurro.capture")

CLIENT_NAME = "susurro-capture/0.1.0"


class CaptureError(Exception):
    pass


async def stream_lane(
    core_url: str,
    token: str,
    session_id: str,
    lane: p.Lane,
    source: AudioSource,
    *,
    client_name: str = CLIENT_NAME,
) -> int:
    """Open one lane and pump it until the source ends. Returns frames sent."""
    url = f"{core_url.rstrip('/')}/capture?token={token}"
    encoder = OpusEncoder()
    sent = 0

    async with websockets.connect(url) as connection:
        await connection.send(
            p.Hello(session_id=session_id, lane=lane, client=client_name).to_json()
        )
        reply = await connection.recv()
        acknowledged = _parse_ready(reply)
        if not acknowledged:
            raise CaptureError(f"core refused lane {lane!r}: {reply}")
        log.info("lane %s open: %s", lane, source.description)

        # The source is a blocking iterator by design -- device backends read
        # from a pipe or a device. Pulling it on a worker thread keeps the event
        # loop free to flush the socket.
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)
        loop = asyncio.get_running_loop()

        def pump(frames: Iterator[bytes]) -> None:
            for frame in frames:
                asyncio.run_coroutine_threadsafe(queue.put(frame), loop).result()
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        reader = asyncio.create_task(asyncio.to_thread(pump, source.frames()))
        try:
            while True:
                pcm = await queue.get()
                if pcm is None:
                    break
                packet = encoder.encode(pcm)
                frame = p.AudioFrame(
                    timestamp_us=time.monotonic_ns() // 1000, payload=packet
                )
                await connection.send(frame.encode())
                sent += 1
        finally:
            source.close()
            reader.cancel()
    return sent


def _parse_ready(reply: str | bytes) -> bool:
    import json

    try:
        return json.loads(reply).get("type") == "ready"
    except (json.JSONDecodeError, AttributeError):
        return False


async def stream_session(
    core_url: str,
    token: str,
    session_id: str,
    sources: dict[p.Lane, AudioSource],
) -> dict[str, int]:
    """Run every lane concurrently. One lane failing takes the session down.

    Running on with a dead lane would be worse than stopping: the transcript
    would silently become one-sided, which looks like the other party going
    quiet rather than like a bug.
    """
    results = await asyncio.gather(
        *(
            stream_lane(core_url, token, session_id, lane, source)
            for lane, source in sources.items()
        )
    )
    return dict(zip(sources, results, strict=True))
