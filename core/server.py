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
import time
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
from core.session.history import (
    Hint,
    HistoryError,
    HistoryStore,
    Line,
    Recording,
    now,
    render_markdown,
)
from core.session.profiles import Profile, ProfileError, ProfileStore
from core.session.state import SessionPhase, SessionState, TransitionError
from core.stt.base import Recognizer, Segment
from core.vad import Detector, VadEvent

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

#: A hint that has not finished by now is abandoned. The user is in a
#: conversation: a suggestion that arrives after this is not late, it is noise,
#: and leaving the console saying "writing…" indefinitely is worse than saying
#: it failed. The fakes never hang, so nothing exercised this until a deadline
#: existed to test.
HINT_DEADLINE_S = 20.0

#: Time to first token beyond which the hint is abandoned. Much tighter than
#: the overall deadline: if the model has not started speaking by now it is
#: either stuck or so loaded that the answer would be useless anyway.
HINT_FIRST_TOKEN_DEADLINE_S = 8.0


@dataclass
class Core:
    profiles: ProfileStore
    recognizer: Recognizer
    generator: HintGenerator
    token: str
    #: Abandon a hint that has not produced its first token, or has not
    #: finished, by these deadlines. Both are generous against the one-second
    #: budget: they exist to catch a stuck model, not to enforce quality.
    hint_deadline_s: float = HINT_DEADLINE_S
    first_token_deadline_s: float = HINT_FIRST_TOKEN_DEADLINE_S
    #: Per-lane voice activity. ``None`` disables end-of-turn detection, which
    #: leaves hints manual -- everything else still works.
    detectors: dict[str, Detector] | None = None
    #: Recorded sessions. Disabled unless configured, because a tool that
    #: writes every conversation to disk unasked is not one to trust.
    history: HistoryStore | None = None

    state: SessionState = field(default_factory=SessionState)
    decoder: LaneDecoder = field(default_factory=LaneDecoder, init=False)
    consoles: set[ServerConnection] = field(default_factory=set, init=False)
    transcript: list[str] = field(default_factory=list, init=False)
    _hint_task: asyncio.Task[None] | None = field(default=None, init=False)
    #: What started the hint currently in flight, so that speech resuming can
    #: cancel a speculative hint without touching one the user asked for.
    _hint_trigger: str | None = field(default=None, init=False)
    _recording: Recording | None = field(default=None, init=False)
    #: When each in-flight hint was asked for, and how long until its first
    #: token arrived. This is the number the product lives on -- M2's gate is
    #: stated in it -- and until now nothing measured it.
    _hint_started: dict[str, float] = field(default_factory=dict, init=False)
    _hint_ttft: dict[str, int] = field(default_factory=dict, init=False)
    #: Recognition runs on a worker thread, and one at a time. Whisper takes
    #: hundreds of milliseconds per pass -- on the event loop that stalls audio
    #: ingestion and the console with it -- and a model is not safe to call
    #: from two threads at once.
    _recognizer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _stt_status: str = field(default="ready", init=False)
    #: Whether the model server answered the last time anything asked it.
    #: Reported in `state`, which said "ready" unconditionally until a session
    #: was started against a model server that was not running.
    _llm_status: str = field(default="unknown", init=False)
    _hint_text: dict[str, str] = field(default_factory=dict, init=False)

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

    def profiles_event(self) -> str:
        entries = []
        for profile_id in self.profiles.list_ids():
            try:
                profile = self.profiles.load(profile_id)
            except ProfileError as exc:
                # One unreadable file must not hide the rest. The console shows
                # what loaded; the log says what did not.
                log.warning("skipping profile %s: %s", profile_id, exc)
                continue
            entries.append(
                {
                    "id": profile.id,
                    "label": profile.label,
                    "language": profile.language,
                    "mode": profile.mode,
                }
            )
        return p.profiles(entries)

    def require_history(self) -> HistoryStore:
        if self.history is None or not self.history.enabled:
            raise HistoryError(
                "recording is disabled; enable [session] record in the core's "
                "configuration"
            )
        return self.history

    def sessions_event(self) -> str:
        enabled = self.history is not None and self.history.enabled
        entries = self.history.list_sessions() if enabled else []
        return p.sessions(entries, recording=enabled)

    def state_event(self) -> str:
        return p.state(
            session=self.state.phase.value,
            stt=self._stt_status,
            llm=self._llm_status,
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
                for segment in await self.recognize(lane, frame.timestamp_us, pcm):
                    await self.emit_segment(segment)
                await self.observe_voice(lane, pcm)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            for segment in await self.flush_lane(lane):
                await self.emit_segment(segment)
            self.state.lane_down(lane)
            await self.broadcast(self.state_event())
            log.info("lane %s disconnected", lane)

    async def recognize(
        self, lane: p.Lane, timestamp_us: int, pcm: bytes
    ) -> list[Segment]:
        return await self._recognizer_call(
            self.recognizer.feed, lane, timestamp_us, pcm
        )

    async def flush_lane(self, lane: p.Lane) -> list[Segment]:
        return await self._recognizer_call(self.recognizer.flush, lane)

    async def _recognizer_call(self, method, *args) -> list[Segment]:
        """Run a recognizer call on a worker thread, surviving its failures.

        A model can raise -- bad audio, an out-of-memory moment, a backend
        changing under us. Letting that propagate would drop the capture
        connection and take the lane down with it, turning a transcription
        hiccup into a dead microphone for the rest of the call. The fakes never
        raise, so nothing exercised this before.
        """
        try:
            async with self._recognizer_lock:
                return await asyncio.to_thread(method, *args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            log.exception("recognizer failed")
            await self.broadcast(p.error("stt_failed", str(exc)))
            return []

    async def emit_segment(self, segment: Segment) -> None:
        if segment.final:
            # Only finals reach the model, and only here. The prompt is
            # append-only so the prefix cache survives between hints.
            speaker = "them" if segment.lane == "far" else "me"
            self.transcript.append(f"{speaker}: {segment.text}")
            if self._recording is not None:
                self._recording.lines.append(
                    Line(
                        lane=segment.lane,
                        text=segment.text,
                        t=segment.t,
                        dur=segment.dur,
                        seq=segment.seq,
                    )
                )
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
        await connection.send(self.profiles_event())
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
                case p.ProfilesRequest():
                    await connection.send(self.profiles_event())
                    return
                case p.SessionsRequest():
                    await connection.send(self.sessions_event())
                    return
                case p.SessionExport(session_id=session_id):
                    recording = self.require_history().load(session_id)
                    await connection.send(
                        p.export(session_id, render_markdown(recording))
                    )
                    return
                case p.SessionDelete(session_id=session_id):
                    self.require_history().delete(session_id)
                    await connection.send(self.sessions_event())
                    return
                case p.ProfileRequest(profile_id=profile_id):
                    profile = self.profiles.load(profile_id)
                    await connection.send(p.profile(profile.to_dict()))
                    return
                case p.ProfileSave() as command:
                    await self.save_profile(command)
                    await connection.send(self.profiles_event())
                    await self.broadcast(self.profiles_event())
                    return
                case p.SessionStart():
                    await self.start_session()
                case p.SessionStop():
                    self.state.stop()
                    await self.cancel_hint()
                    self.finish_recording()
                    self.transcript.clear()
                case p.HintRequest(mode=mode):
                    await self.request_hint(mode)
                    return
                case p.HintCancel(hint_id=hint_id):
                    await self.cancel_hint(hint_id)
                    return
        except (TransitionError, ProfileError, HistoryError) as exc:
            await connection.send(p.error(exc.code, str(exc)))
            return
        await self.broadcast(self.state_event())

    def record_hint(
        self,
        hint_id: str,
        mode: str,
        reason: str,
        *,
        tokens: int | None = None,
        ttft_ms: int | None = None,
    ) -> None:
        text = self._hint_text.pop(hint_id, "")
        self._hint_started.pop(hint_id, None)
        self._hint_ttft.pop(hint_id, None)
        if self._recording is None or not text:
            return
        self._recording.hints.append(
            Hint(
                hint_id=hint_id,
                trigger=self._hint_trigger or "manual",
                mode=mode,
                text=text,
                reason=reason,
                at=self._recording.lines[-1].t if self._recording.lines else 0.0,
                tokens=tokens,
                ttft_ms=ttft_ms,
            )
        )

    def finish_recording(self) -> None:
        """Write the session out, if one was being recorded.

        A failure here is logged and swallowed. Losing a transcript is
        annoying; raising out of session.stop and leaving the core in a state
        the console cannot see would be worse.
        """
        recording, self._recording = self._recording, None
        if recording is None or self.history is None:
            return
        if not recording.lines and not recording.hints:
            # Nothing was said. An empty file for every accidental start would
            # bury the sessions that matter.
            return
        recording.ended_at = now()
        try:
            path = self.history.save(recording)
            log.info("recorded session to %s", path)
        except (HistoryError, OSError):
            log.exception("could not write the session recording")

    async def save_profile(self, command: p.ProfileSave) -> None:
        """Persist an edited profile.

        Refused for the profile the live session was built from. Its preamble
        is already prefilled into the model's cache; saving over it would leave
        the console showing text the model has never seen, with no error and no
        way to notice. Other profiles are fine to edit mid-session.
        """
        if (
            self.state.phase is SessionPhase.LIVE
            and command.profile_id == self.state.profile_id
        ):
            raise TransitionError(
                f"profile {command.profile_id!r} is in use by the live session: its "
                "context is already prefilled into the model. Stop the session first."
            )
        profile = Profile(
            id=command.profile_id,
            label=command.label,
            language=command.language,
            mode=command.mode,
            context=command.context,
        )
        try:
            self.profiles.save(profile)
        except OSError as exc:
            raise ProfileError(f"could not write profile: {exc}") from exc
        log.info("saved profile %s", profile.id)

    async def start_session(self) -> None:
        if self.state.phase is SessionPhase.IDLE:
            raise TransitionError("session.config must arrive before session.start")
        assert self.state.profile_id is not None
        profile = self.profiles.load(self.state.profile_id)

        # Load the speech model before anything says the session is live. A
        # first run downloads it, which takes minutes; doing that once audio is
        # arriving stalls the server and looks exactly like a hang.
        self._stt_status = "loading"
        await self.broadcast(self.state_event())
        try:
            async with self._recognizer_lock:
                await asyncio.to_thread(self.recognizer.warmup)
        finally:
            self._stt_status = "ready"

        self.state.start()
        self.transcript.clear()
        self._hint_text.clear()
        self._hint_started.clear()
        self._hint_ttft.clear()
        if self.history is not None and self.history.enabled:
            self._recording = Recording(
                session_id=now().replace(":", "-"),
                profile_id=profile.id,
                language=profile.language,
                mode=profile.mode,
                started_at=now(),
            )
        self.transcript.clear()
        self.recognizer.reset()
        self.decoder.reset()
        for detector in (self.detectors or {}).values():
            detector.reset()
        # Once per session, not once per hint. This is the whole latency
        # argument in one line.
        #
        # It is also the first thing that talks to the model server, so it is
        # the earliest a dead one can be noticed. A failure does not stop the
        # session -- the audio path is still worth having, and the server may
        # come back -- but it is reported rather than left for the user to
        # discover mid-call.
        try:
            await self.generator.prefill(profile.preamble())
            self._llm_status = "ready"
        except Exception as exc:  # noqa: BLE001 - surfaced to the console
            self._llm_status = "unavailable"
            log.warning("the model server did not answer: %s", exc)
            await self.broadcast(
                p.error(
                    "llm_unavailable",
                    f"the model server did not answer: {exc}. Transcription will "
                    f"work; hints will not until it is back.",
                )
            )

    # -- voice activity -------------------------------------------------------- #

    async def observe_voice(self, lane: p.Lane, pcm: bytes) -> None:
        """Turn per-lane speech boundaries into hint lifecycle decisions.

        Only the ``far`` lane matters here. The user talking is not a reason to
        write them a hint, and it is not a reason to throw one away either --
        people think out loud while reading.
        """
        detector = (self.detectors or {}).get(lane)
        if detector is None:
            return
        for event in detector.push(pcm):
            if lane != "far":
                continue
            if event is VadEvent.SPEECH_START:
                # Only a speculative hint is abandoned. One the user asked for
                # stays, because they asked for it.
                if self._hint_trigger == "speculative":
                    await self.cancel_hint()
            elif event is VadEvent.SPEECH_END:
                await self.speculate()

    async def speculate(self) -> None:
        """Start writing a hint because the other party stopped talking.

        The recognizer is flushed first. Without that, the hint would be
        generated against a transcript missing the sentence that just ended --
        the one it is supposed to answer -- because the final for it has not
        been emitted yet.
        """
        if not self.state.accepts_audio or self.state.active_hint is not None:
            return
        for segment in await self.flush_lane("far"):
            await self.emit_segment(segment)
        if not self.transcript:
            return
        await self.start_hint(self.state.mode or "conversational", "speculative")

    # -- hints --------------------------------------------------------------- #

    async def request_hint(self, mode: str) -> None:
        await self.cancel_hint()
        await self.start_hint(mode, "manual")

    async def start_hint(self, mode: str, trigger: str) -> None:
        hint_id = self.state.begin_hint()
        self._hint_trigger = trigger
        # The clock starts when the hint is asked for, not when the request
        # reaches the model. Scheduling and prompt assembly are latency the
        # user waits through too.
        self._hint_started[hint_id] = time.perf_counter()
        await self.broadcast(p.hint_start(hint_id, trigger, mode))  # type: ignore[arg-type]
        self._hint_task = asyncio.create_task(self.run_hint(hint_id, mode))

    async def run_hint(self, hint_id: str, mode: str) -> None:
        context = HintContext(
            mode=mode,  # type: ignore[arg-type]
            language=self.state.language or "en",
            transcript=tuple(self.transcript),
        )
        tokens = 0
        started = self._hint_started.get(hint_id, time.perf_counter())
        try:
            stream = self.generator.stream(context)
            while True:
                # Two deadlines, because the failures differ. Nothing at all
                # means a stuck or overloaded model; stopping mid-sentence
                # means a dropped connection. Both end the hint rather than
                # leaving the console saying "writing…" for the rest of the
                # call.
                budget = (
                    self.first_token_deadline_s
                    if tokens == 0
                    else self.hint_deadline_s - (time.perf_counter() - started)
                )
                if budget <= 0:
                    raise TimeoutError(
                        f"hint exceeded its {self.hint_deadline_s:.0f}s deadline"
                    )
                try:
                    chunk = await asyncio.wait_for(anext(stream), timeout=budget)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    stage = "its first token" if tokens == 0 else "a further token"
                    raise TimeoutError(f"the model stopped before {stage}") from exc
                if tokens == 0:
                    self._hint_ttft[hint_id] = round(
                        (time.perf_counter() - started) * 1000
                    )
                tokens += 1
                self._hint_text[hint_id] = self._hint_text.get(hint_id, "") + chunk
                await self.broadcast(p.hint_delta(hint_id, chunk))
        except asyncio.CancelledError:
            # The cancelling side sends hint.end; sending it here too would
            # race and emit the event twice.
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the console
            log.exception("hint %s failed", hint_id)
            self._llm_status = "unavailable"
            self.state.end_hint(hint_id)
            # Whatever was written before it failed is still recorded: a hint
            # that stopped halfway is evidence about the model, not nothing.
            self.record_hint(
                hint_id, mode, "error", tokens=tokens,
                ttft_ms=self._hint_ttft.get(hint_id),
            )
            self._hint_trigger = None
            await self.broadcast(p.hint_end(hint_id, "error"))
            await self.broadcast(p.error("hint_failed", str(exc)))
            return
        if self.state.end_hint(hint_id):
            self._llm_status = "ready"
            ttft_ms = self._hint_ttft.get(hint_id)
            self.record_hint(hint_id, mode, "complete", tokens=tokens, ttft_ms=ttft_ms)
            self._hint_trigger = None
            await self.broadcast(
                p.hint_end(hint_id, "complete", tokens=tokens, ttft_ms=ttft_ms)
            )

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
        # A cancelled hint is still recorded: afterwards it is useful to see
        # what the thing was about to say and whether abandoning it was right.
        # Its time to first token is kept too, when it got that far -- a
        # speculative hint that was cancelled still measured the model.
        ttft_ms = self._hint_ttft.get(active)
        self.record_hint(
            active,
            self.state.mode or "conversational",
            "cancelled",
            ttft_ms=ttft_ms,
        )
        self._hint_trigger = None
        await self.broadcast(p.hint_end(active, "cancelled", ttft_ms=ttft_ms))


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
