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
import contextlib
import logging
import time
from collections.abc import Iterator

import websockets

from capture.backends.base import AudioSource
from capture.codec import OpusEncoder
from core import protocol as p

log = logging.getLogger("susurro.capture")

CLIENT_NAME = "susurro-capture/0.1.0"

#: One second of audio. If the link cannot keep up with that, the backlog is
#: not going to be caught up with -- audio arrives in real time and so does
#: more of it.
QUEUE_FRAMES = 50

#: How long to wait before reconnecting, and the ceiling it grows to. A core
#: restart or a Wi-Fi blip should not end a session: a capture client that dies
#: quietly mid-call is worse than one that keeps trying, because nobody looks
#: at the terminal during a meeting.
RECONNECT_DELAY_S = 1.0
RECONNECT_MAX_S = 15.0


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
    dropped = 0

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
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_FRAMES)
        loop = asyncio.get_running_loop()

        def offer(frame: bytes | None) -> None:
            """Hand a frame to the loop, dropping the oldest if the link lags.

            Blocking here would be worse than dropping. The device keeps
            producing regardless, so a stalled pump means its buffer overruns
            and audio is lost anyway -- silently, and in a burst. Dropping the
            oldest frame loses the same amount, sooner, and keeps the stream
            closest to live.
            """
            nonlocal dropped
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                    dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

        def pump(frames: Iterator[bytes]) -> None:
            for frame in frames:
                loop.call_soon_threadsafe(offer, frame)
            loop.call_soon_threadsafe(offer, None)

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
            if dropped:
                log.warning(
                    "lane %s dropped %d frames (%.1fs) keeping up with the link",
                    lane, dropped, dropped * 0.02,
                )
    return sent


async def stream_lane_forever(
    core_url: str,
    token: str,
    session_id: str,
    lane: p.Lane,
    source_factory,
    *,
    client_name: str = CLIENT_NAME,
    max_attempts: int | None = None,
) -> int:
    """Keep a lane open across disconnections.

    A core restart or a dropped Wi-Fi link should not end a session. Nobody
    watches the capture terminal during a meeting, so a client that exits
    quietly looks exactly like one that is working.

    Reconnection is for transport failures only. A refused lane or a bad token
    is a configuration mistake, and retrying it forever would bury the message
    that says so.
    """
    total = 0
    delay = RECONNECT_DELAY_S
    attempts = 0
    while True:
        attempts += 1
        try:
            total += await stream_lane(
                core_url, token, session_id, lane, source_factory(),
                client_name=client_name,
            )
            return total
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            if max_attempts is not None and attempts >= max_attempts:
                raise
            log.warning(
                "lane %s lost (%s); reconnecting in %.0fs", lane, exc, delay
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_S)


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
    sources: dict[p.Lane, AudioSource] | dict[p.Lane, object],
    *,
    reconnect: bool = False,
) -> dict[str, int]:
    """Run every lane concurrently.

    One lane failing for good takes the session down. Running on with a dead
    lane would be worse than stopping: the transcript would silently become
    one-sided, which looks like the other party going quiet rather than like a
    bug.

    With ``reconnect``, ``sources`` maps a lane to a *factory* rather than a
    source, because a reconnecting lane needs a fresh one each time -- a
    closed file or a terminated subprocess cannot be reopened.
    """
    if reconnect:
        runners = [
            stream_lane_forever(core_url, token, session_id, lane, factory)
            for lane, factory in sources.items()
        ]
    else:
        runners = [
            stream_lane(core_url, token, session_id, lane, source)
            for lane, source in sources.items()
        ]
    results = await asyncio.gather(*runners)
    return dict(zip(sources, results, strict=True))
