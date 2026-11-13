"""The core server: two WebSocket endpoints over one session.

Owns the plumbing and nothing else. Recognition and hint generation are
injected, so the whole server can be exercised against the fake backends in
``core.stt.fake`` and ``core.llm.fake`` without model weights — which is the
only way it can be developed on a machine that cannot host a model.

One session at a time. The core serves one person on one call; a session
registry would be structure with no requirement behind it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import Request, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from core import protocol as p
from core.audio import DecodeError, LaneDecoder
from core.llm.base import HintContext, HintGenerator
from core.session.profiles import ProfileError, ProfileStore
from core.session.state import SessionPhase, SessionState, TransitionError
from core.stt.base import Recognizer, Segment

log = logging.getLogger("susurro.core")

DEFAULT_PORT = 8723

#: The console is static files with no build step, served from the same port as
#: the WebSocket endpoints so there is one address to remember and no CORS.
CONSOLE_ROOT = Path(__file__).resolve().parent.parent / "console"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}

#: Close codes. 1008 is "policy violation", which is what a bad token is.
CLOSE_UNAUTHORIZED = 1008
CLOSE_PROTOCOL = 1002


@dataclass
class Core:
    profiles: ProfileStore
    recognizer: Recognizer
    generator: HintGenerator
    token: str

    state: SessionState = field(default_factory=SessionState)
    decoder: LaneDecoder = field(default_factory=LaneDecoder, init=False)
    consoles: set[ServerConnection] = field(default_factory=set, init=False)
    transcript: list[str] = field(default_factory=list, init=False)
    _hint_task: asyncio.Task[None] | None = field(default=None, init=False)

    # -- auth --------------------------------------------------------------- #

    def authorized(self, path: str) -> bool:
        query = parse_qs(urlparse(path).query)
        supplied = query.get("token", [""])[0]
        # compare_digest rather than ==: token comparison should not leak its
        # length or its first differing byte through timing.
        return hmac.compare_digest(supplied, self.token)

    # -- fan-out ------------------------------------------------------------ #

    async def broadcast(self, message: str) -> None:
        if not self.consoles:
            return
        await asyncio.gather(
            *(self._send(console, message) for console in tuple(self.consoles)),
            return_exceptions=True,
        )

    async def _send(self, console: ServerConnection, message: str) -> None:
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            await console.send(message)

    def state_event(self) -> str:
        return p.state(
            session=self.state.phase.value,
            stt="ready",
            llm="ready",
            lanes=dict(self.state.lanes),
        )

    # -- capture endpoint ---------------------------------------------------- #

    async def handle_capture(self, connection: ServerConnection) -> None:
        if not self.authorized(connection.request.path):
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return

        try:
            hello = p.Hello.from_json(await connection.recv())
        except p.ProtocolError as exc:
            await connection.send(p.error_for(exc))
            await connection.close(CLOSE_PROTOCOL, exc.code)
            return

        lane = hello.lane
        try:
            self.state.lane_up(lane)
        except TransitionError as exc:
            await connection.send(p.error("lane_busy", str(exc)))
            await connection.close(CLOSE_PROTOCOL, "lane_busy")
            return

        await connection.send(p.ready())
        await self.broadcast(self.state_event())
        log.info("lane %s connected (%s)", lane, hello.client)

        try:
            async for message in connection:
                if isinstance(message, str):
                    await connection.send(
                        p.error("malformed_frame", "expected a binary audio frame")
                    )
                    continue
                try:
                    frame = p.AudioFrame.decode(message)
                except p.MalformedFrame as exc:
                    await connection.send(p.error_for(exc))
                    continue
                if not self.state.accepts_audio:
                    # Frames before session.start are dropped rather than
                    # buffered: there is no prefilled cache to transcribe into
                    # yet, and a buffer would only delay the discovery.
                    continue
                try:
                    pcm = self.decoder.decode(lane, frame.payload)
                except DecodeError as exc:
                    # One corrupt packet is not worth dropping the lane for.
                    await connection.send(p.error(exc.code, str(exc)))
                    continue
                for segment in self.recognizer.feed(lane, frame.timestamp_us, pcm):
                    await self.emit_segment(segment)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            for segment in self.recognizer.flush(lane):
                await self.emit_segment(segment)
            self.state.lane_down(lane)
            await self.broadcast(self.state_event())
            log.info("lane %s disconnected", lane)

    async def emit_segment(self, segment: Segment) -> None:
        if segment.final:
            # Only finals reach the model, and only here. The prompt is
            # append-only so the prefix cache survives between hints.
            speaker = "them" if segment.lane == "far" else "me"
            self.transcript.append(f"{speaker}: {segment.text}")
            await self.broadcast(
                p.transcript_final(
                    segment.lane, segment.text, segment.t, segment.dur, segment.seq
                )
            )
        else:
            await self.broadcast(
                p.transcript_partial(segment.lane, segment.text, segment.t)
            )

    # -- console endpoint ---------------------------------------------------- #

    async def handle_console(self, connection: ServerConnection) -> None:
        if not self.authorized(connection.request.path):
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return

        self.consoles.add(connection)
        await connection.send(self.state_event())
        try:
            async for message in connection:
                try:
                    command = p.parse_console_command(message)
                except p.ProtocolError as exc:
                    await connection.send(p.error_for(exc))
                    continue
                await self.dispatch(command, connection)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.consoles.discard(connection)

    async def dispatch(self, command: Any, connection: ServerConnection) -> None:
        try:
            match command:
                case p.SessionConfig(profile_id=profile_id, language=lang, mode=mode):
                    profile = self.profiles.load(profile_id)
                    if profile.language != lang:
                        raise ProfileError(
                            f"profile {profile_id!r} is {profile.language!r}, "
                            f"session.config asked for {lang!r}"
                        )
                    self.state.configure(profile_id, lang, mode)
                case p.SessionStart():
                    await self.start_session()
                case p.SessionStop():
                    self.state.stop()
                    await self.cancel_hint()
                    self.transcript.clear()
                case p.HintRequest(mode=mode):
                    await self.request_hint(mode)
                    return
                case p.HintCancel(hint_id=hint_id):
                    await self.cancel_hint(hint_id)
                    return
        except (TransitionError, ProfileError) as exc:
            await connection.send(p.error(exc.code, str(exc)))
            return
        await self.broadcast(self.state_event())

    async def start_session(self) -> None:
        if self.state.phase is SessionPhase.IDLE:
            raise TransitionError("session.config must arrive before session.start")
        assert self.state.profile_id is not None
        profile = self.profiles.load(self.state.profile_id)
        self.state.start()
        self.transcript.clear()
        self.recognizer.reset()
        self.decoder.reset()
        # Once per session, not once per hint. This is the whole latency
        # argument in one line.
        await self.generator.prefill(profile.preamble())

    # -- hints --------------------------------------------------------------- #

    async def request_hint(self, mode: str) -> None:
        await self.cancel_hint()
        hint_id = self.state.begin_hint()
        await self.broadcast(p.hint_start(hint_id, "manual", mode))  # type: ignore[arg-type]
        self._hint_task = asyncio.create_task(self.run_hint(hint_id, mode))

    async def run_hint(self, hint_id: str, mode: str) -> None:
        context = HintContext(
            mode=mode,  # type: ignore[arg-type]
            language=self.state.language or "en",
            transcript=tuple(self.transcript),
        )
        tokens = 0
        try:
            async for chunk in self.generator.stream(context):
                tokens += 1
                await self.broadcast(p.hint_delta(hint_id, chunk))
        except asyncio.CancelledError:
            # The cancelling side sends hint.end; sending it here too would
            # race and emit the event twice.
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the console
            log.exception("hint %s failed", hint_id)
            self.state.end_hint(hint_id)
            await self.broadcast(p.hint_end(hint_id, "error"))
            await self.broadcast(p.error("hint_failed", str(exc)))
            return
        if self.state.end_hint(hint_id):
            await self.broadcast(p.hint_end(hint_id, "complete", tokens=tokens))

    async def cancel_hint(self, hint_id: str | None = None) -> None:
        task, self._hint_task = self._hint_task, None
        active = self.state.active_hint
        if task is None or active is None:
            return
        if hint_id is not None and hint_id != active:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self.state.end_hint(active)
        await self.broadcast(p.hint_end(active, "cancelled"))


def serve_console(root: Path, request: Request) -> Response | None:
    """Answer a plain HTTP request with a console file.

    Returning ``None`` lets the request continue as a WebSocket handshake, which
    is how both protocols share one port.
    """
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None

    route = urlparse(request.path).path
    relative = "index.html" if route in ("/", "") else route.lstrip("/")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        return Response(404, "Not Found", Headers({"Content-Length": "0"}), b"")

    body = target.read_bytes()
    content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
    return Response(
        200,
        "OK",
        Headers(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                # The console is served from the operator's own machine and
                # changes whenever the core is updated. Caching it only makes a
                # stale UI harder to notice.
                "Cache-Control": "no-store",
            }
        ),
        body,
    )


async def run(
    core: Core, host: str, port: int, console_root: Path = CONSOLE_ROOT
) -> None:
    async def router(connection: ServerConnection) -> None:
        route = urlparse(connection.request.path).path
        if route == "/capture":
            await core.handle_capture(connection)
        elif route == "/console":
            await core.handle_console(connection)
        else:
            await connection.close(1003, "unknown endpoint")

    def process_request(connection: ServerConnection, request: Request) -> Response | None:
        del connection
        return serve_console(console_root, request)

    async with serve(router, host, port, process_request=process_request):
        log.info("listening on ws://%s:%d", host, port)
        await asyncio.Future()


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))
