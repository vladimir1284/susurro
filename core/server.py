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
import dataclasses
import hmac
import logging
import time
from collections import deque
from collections.abc import Callable
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
from core.bleed import BleedGate
from core.llm.base import HintContext, HintGenerator
from core.session import vocabulary
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

#: Microseconds in one audio frame; the unit of a lane's frame clock.
FRAME_US = p.FRAME_MS * 1000

#: Frames kept from before speech is declared, released when it starts. The
#: detector needs a couple of windows (~64 ms) to be sure and the first sound of
#: a sentence is already behind it by then; 15 frames is 300 ms of lead-in, which
#: is what keeps "so" from arriving as "o".
PREROLL_FRAMES = 15

#: Queued audio, in frames, past which a lane is said to be falling behind (5 s).
BACKLOG_WARN_FRAMES = 250


@dataclass(frozen=True, slots=True)
class _Work:
    """One step for a lane's recognition worker: audio to hear, or a turn's end."""

    kind: str  # "hear" or "end"
    timestamp_us: int = 0
    pcm: bytes = b""


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
    #: Keeps the far voice, played through loudspeakers, out of the mic lane.
    #: ``None`` leaves the mic lane as captured.
    bleed: BleedGate | None = None
    #: Recorded sessions. Disabled unless configured, because a tool that
    #: writes every conversation to disk unasked is not one to trust.
    history: HistoryStore | None = None
    #: Most characters of transcript a hint carries (about 3k tokens). Past it the
    #: window moves forward; see ``hint_window``. 0 sends everything.
    transcript_budget_chars: int = 12000
    #: Gates on a speculative hint (AUDIT D8). All off by default: which values
    #: are worth it has to be measured on recorded calls, and a backchannel
    #: ("mm-hm") starting a generation is a cost only that measurement can price.
    #: A far turn shorter than this many words, or this many seconds, does not
    #: start one; ``speculate_skip_while_user_speaks`` holds it back while the
    #: mic lane is voiced. ``speculations_skipped`` counts what the gates stopped.
    speculate_min_words: int = 0
    speculate_min_turn_s: float = 0.0
    speculate_skip_while_user_speaks: bool = False
    #: Monotonic seconds. Injectable so tests can move time without sleeping.
    clock: Callable[[], float] = time.monotonic

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
    #: Finals of the live session, kept so a console that reconnects can be
    #: given them again, and the mode of the hint in flight for the same reason.
    _finals: list[Segment] = field(default_factory=list, init=False)
    #: Speculative hints the D8 gates held back, since the core started.
    speculations_skipped: int = field(default=0, init=False)
    _hint_mode: str | None = field(default=None, init=False)
    #: First transcript line a hint carries. See ``hint_window``.
    _window_start: int = field(default=0, init=False)
    #: Checkpoints are written one at a time, so an older snapshot cannot land
    #: after a newer one.
    _checkpoint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    #: Per lane: frames held back while nobody is speaking, and how many frames
    #: the lane has delivered. See ``route_audio``.
    _preroll: dict[str, deque[tuple[int, bytes]]] = field(default_factory=dict, init=False)
    #: The session timeline: when it began, and each lane's last stamp. See
    #: ``stamp``.
    _session_t0: float | None = field(default=None, init=False)
    _lane_clock_us: dict[str, int] = field(default_factory=dict, init=False)

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

    # -- long calls ------------------------------------------------------------ #

    def hint_window(self) -> tuple[str, ...]:
        """The transcript lines a hint carries.

        Every hint used to send the whole transcript, so an hour-long call would
        outgrow the model's context. The window is a suffix of whole lines.
        It holds still while the transcript fits the budget, so the prompt keeps
        the same start and the server's prefix cache survives from hint to
        hint. When the budget is exceeded it jumps forward to keep about half of
        it: one cache miss, then a long quiet stretch. Trimming a little on
        every hint would move the start of the prompt each time and make the
        cache useless. The last line is always kept, however long.
        """
        lines = self.transcript
        if not lines:
            return ()
        budget = self.transcript_budget_chars
        start = min(self._window_start, len(lines) - 1)
        if budget > 0 and sum(len(line) + 1 for line in lines[start:]) > budget:
            kept, new = 0, len(lines)
            while new > 0 and kept + len(lines[new - 1]) + 1 <= budget // 2:
                kept += len(lines[new - 1]) + 1
                new -= 1
            start = max(start, min(new, len(lines) - 1))
            log.info("transcript window moved to line %d of %d", start, len(lines))
        self._window_start = start
        return tuple(lines[start:])

    def reset_hint_window(self) -> None:
        self._window_start = 0

    async def checkpoint(self) -> None:
        """Write the recording as it stands.

        It used to be written only by session.stop, so a crash, a Ctrl-C or a
        power cut lost the whole call. A snapshot goes to the same file every
        time (atomically), so an interrupted call leaves everything up to the
        last final or hint, and stopping later still leaves one file.
        """
        recording = self._recording
        if recording is None or self.history is None or not self.history.enabled:
            return
        if not recording.lines and not recording.hints:
            return
        snapshot = dataclasses.replace(
            recording, lines=list(recording.lines), hints=list(recording.hints)
        )
        async with self._checkpoint_lock:
            try:
                await asyncio.to_thread(self.history.save, snapshot)
            except (HistoryError, OSError):
                log.warning("could not checkpoint the session recording", exc_info=True)

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

        # Reading and recognizing are separate jobs. This loop only decodes and
        # queues; a worker does the slow part. When the loop itself awaited the
        # recognizer it stopped reading whenever Whisper was slow, the
        # websocket library stopped answering pings with it, and a recorded call
        # lost both lanes to a keepalive timeout while the model server was
        # merely behind.
        pending: asyncio.Queue[_Work | None] = asyncio.Queue()
        worker = asyncio.create_task(self._recognize_lane(lane, pending))
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
                # Stamped here, on arrival, not when the worker gets to it: a
                # backlog must not shift the audio's place on the timeline.
                # The bleed gate and voice activity run here too, on arrival
                # (AUDIT D6): behind a slow decode they would judge frames
                # that are already seconds old. Only what the recognizer
                # should hear is queued.
                try:
                    for work in await self.admit(lane, pcm, self.stamp(lane)):
                        pending.put_nowait(work)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad frame must not end the lane
                    log.exception("lane %s: audio admission failed", lane)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            # What was already received is still transcribed before the lane is
            # closed out; the worker finishes it, then ends on the sentinel.
            pending.put_nowait(None)
            await worker
            for segment in await self.flush_lane(lane):
                await self.emit_segment(segment)
            self._preroll.pop(lane, None)
            self.state.lane_down(lane)
            await self.broadcast(self.state_event())
            log.info("lane %s disconnected", lane)

    async def _recognize_lane(
        self, lane: p.Lane, pending: asyncio.Queue[_Work | None]
    ) -> None:
        """Work through one lane's queued audio, in order, until the sentinel."""
        warned = False
        while (work := await pending.get()) is not None:
            backlog = pending.qsize()
            if backlog > BACKLOG_WARN_FRAMES and not warned:
                log.warning(
                    "lane %s has %.0fs of audio waiting for recognition",
                    lane, backlog * p.FRAME_MS / 1000,
                )
                warned = True
            elif backlog < BACKLOG_WARN_FRAMES // 2:
                warned = False
            try:
                await self.perform(lane, work)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad frame must not end the lane
                log.exception("lane %s: audio routing failed", lane)

    def stamp(self, lane: p.Lane) -> int:
        """Place a frame on the session timeline, in microseconds.

        Arrival time since ``session.start``, never earlier than one frame
        after the lane's previous stamp. So a gap (a reconnection, frames the
        client dropped) advances the clock instead of becoming lag the
        recognizer can never recover, both lanes count from the same origin,
        and a burst of frames does not squeeze audio into less time than it
        lasts.
        """
        moment = self.clock()
        if self._session_t0 is None:
            self._session_t0 = moment
        arrived_us = int((moment - self._session_t0) * 1_000_000)
        previous = self._lane_clock_us.get(lane)
        stamp = arrived_us if previous is None else max(previous + FRAME_US, arrived_us)
        self._lane_clock_us[lane] = stamp
        return stamp

    async def route_audio(
        self, lane: p.Lane, pcm: bytes, timestamp_us: int | None = None
    ) -> None:
        """Admit one frame and run whatever it asks of the recognizer, in line.

        The socket reader does these in two stages (``admit`` on arrival,
        ``perform`` in the lane's worker); this is both, for callers that have
        no queue of their own, such as the replay tool and the tests.
        """
        if timestamp_us is None:
            timestamp_us = self.stamp(lane)
        for work in await self.admit(lane, pcm, timestamp_us):
            await self.perform(lane, work)

    async def admit(self, lane: p.Lane, pcm: bytes, timestamp_us: int) -> list[_Work]:
        """Decide what the recognizer hears, and when a turn is over.

        Voice activity detection runs first and gates recognition. Without it
        the recognizer decodes every lane continuously, silence included, and
        a speech model that takes most of a second per pass cannot keep up with
        two lanes of nothing: the model server queues requests, they time out,
        and the transcript falls further behind with every second of the call.
        Silence is also what Whisper-family models invent words from.

        So frames are held while nobody is speaking, the last
        ``PREROLL_FRAMES`` of them are released when speech starts (the
        detector needs a few windows to be sure, and the first phoneme is
        already gone by then), and the utterance is closed the moment the
        detector hears it end rather than after the recognizer's own silence
        timer. With no detector configured the recognizer hears everything.

        Timestamps are the session timeline (see ``stamp``), not the client's:
        segment times must keep counting through the silence that was never
        decoded, and through gaps.

        Nothing here waits for the recognizer. What it should do comes back as
        work, to be performed in order: audio to hear, and the end of a turn,
        which closes the utterance and only then lets a hint start (the hint
        must be written against a transcript that already holds the sentence it
        answers, so that step has to wait for the decode ahead of it). The
        other party starting to talk again is acted on at once.
        """
        if self.bleed is not None:
            if lane == "far":
                self.bleed.far(pcm)
            elif lane == "mic":
                pcm = self.bleed.mic(pcm)

        detector = (self.detectors or {}).get(lane)
        if detector is None:
            return [_Work("hear", timestamp_us, pcm)]

        work: list[_Work] = []
        events = detector.push(pcm)
        preroll = self._preroll.setdefault(lane, deque(maxlen=PREROLL_FRAMES))
        if VadEvent.SPEECH_START in events:
            work.extend(_Work("hear", held_us, held) for held_us, held in preroll)
            preroll.clear()
        if detector.speaking:
            work.append(_Work("hear", timestamp_us, pcm))
        else:
            preroll.append((timestamp_us, pcm))
        if VadEvent.SPEECH_END in events:
            work.append(_Work("end"))
        if VadEvent.SPEECH_START in events:
            await self.react_to_voice(lane, [VadEvent.SPEECH_START])
        return work

    async def perform(self, lane: p.Lane, work: _Work) -> None:
        if work.kind == "hear":
            await self.hear(lane, work.timestamp_us, work.pcm)
            return
        # Closed here for every lane, and before anything reacts to the turn
        # ending: a speculative hint must be written against a transcript that
        # already holds the sentence it answers.
        for segment in await self.flush_lane(lane):
            await self.emit_segment(segment)
        await self.react_to_voice(lane, [VadEvent.SPEECH_END])

    async def hear(self, lane: p.Lane, timestamp_us: int, pcm: bytes) -> None:
        for segment in await self.recognize(lane, timestamp_us, pcm):
            await self.emit_segment(segment)

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
            self._finals.append(segment)
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
            await self.checkpoint()
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
        # Snapshot before the first await: whatever is broadcast from here on
        # reaches this console by itself.
        finals = list(self._finals)
        live_hint = self.state.active_hint
        hint_text = self._hint_text.get(live_hint, "") if live_hint else ""
        await connection.send(self.profiles_event())
        await connection.send(self.state_event())
        if self.state.phase is SessionPhase.LIVE:
            # A phone whose screen locked mid-call used to come back blank.
            for segment in finals:
                await connection.send(
                    p.transcript_final(
                        segment.lane, segment.text, segment.t, segment.dur, segment.seq
                    )
                )
            if live_hint:
                await connection.send(
                    p.hint_start(
                        live_hint, self._hint_trigger or "manual",  # type: ignore[arg-type]
                        self._hint_mode or "conversational",  # type: ignore[arg-type]
                    )
                )
                if hint_text:
                    await connection.send(p.hint_delta(live_hint, hint_text))
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
                    # Cancel first: state.stop() clears active_hint, and a
                    # cancel that finds no active hint would orphan the task
                    # (it kept streaming and holding the model after stop).
                    await self.cancel_hint()
                    self.state.stop()
                    await self.finish_recording()
                    self.transcript.clear()
                    self._finals.clear()
                    self.reset_hint_window()
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

    async def finish_recording(self) -> None:
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
            async with self._checkpoint_lock:
                path = await asyncio.to_thread(self.history.save, recording)
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
                self.recognizer.select_language(profile.language)
                self.recognizer.select_vocabulary(vocabulary.extract(profile.context))
                await asyncio.to_thread(self.recognizer.warmup)
        except Exception as exc:  # noqa: BLE001 - surfaced to the console
            # With whispercpp this is "the server is not running", the likeliest
            # first-run mistake. Report it and stay configured so the user can
            # fix it and press start again; an exception escaping dispatch
            # would drop the console connection instead.
            self._stt_status = "ready"
            log.warning("the speech backend did not warm up: %s", exc)
            await self.broadcast(
                p.error(
                    "stt_failed",
                    f"the speech backend did not start: {exc}. The session was "
                    f"not started; fix the backend and start again.",
                )
            )
            return
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
        self._finals.clear()
        self.reset_hint_window()
        self.recognizer.reset()
        self.decoder.reset()
        for detector in (self.detectors or {}).values():
            detector.reset()
        if self.bleed is not None:
            self.bleed.reset()
        self._preroll.clear()
        self._session_t0 = self.clock()
        self._lane_clock_us.clear()
        # Once per session, not once per hint. This is the whole latency
        # argument in one line.
        #
        # It is also the first thing that talks to the model server, so it is
        # the earliest a dead one can be noticed. A failure does not stop the
        # session -- the audio path is still worth having, and the server may
        # come back -- but it is reported rather than left for the user to
        # discover mid-call.
        try:
            await self.generator.prefill(profile.preamble(), profile.language)
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

    async def react_to_voice(self, lane: p.Lane, events: list[VadEvent]) -> None:
        """Turn per-lane speech boundaries into hint lifecycle decisions.

        Only the ``far`` lane matters here. The user talking is not a reason to
        write them a hint, and it is not a reason to throw one away either --
        people think out loud while reading.
        """
        for event in events:
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
        if self.speculation_gated():
            self.speculations_skipped += 1
            return
        await self.start_hint(self.state.mode or "conversational", "speculative")

    def speculation_gated(self) -> bool:
        """Whether the turn that just ended is too slight to answer (AUDIT D8)."""
        if self.speculate_skip_while_user_speaks:
            mic = (self.detectors or {}).get("mic")
            if mic is not None and mic.speaking:
                return True
        if not (self.speculate_min_words or self.speculate_min_turn_s):
            return False
        turn = next((s for s in reversed(self._finals) if s.lane == "far"), None)
        if turn is None:
            return False
        if len(turn.text.split()) < self.speculate_min_words:
            return True
        return turn.dur < self.speculate_min_turn_s

    # -- hints --------------------------------------------------------------- #

    async def request_hint(self, mode: str) -> None:
        await self.cancel_hint()
        await self.start_hint(mode, "manual")

    async def start_hint(self, mode: str, trigger: str) -> None:
        hint_id = self.state.begin_hint()
        self._hint_trigger = trigger
        self._hint_mode = mode
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
            transcript=self.hint_window(),
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
            # A stream that ends cleanly with nothing in it is not a hint. A
            # reasoning model started with thinking on puts every token in
            # reasoning_content and leaves content empty: the 2026-11-18 replay
            # got 108 "complete" hints of zero text and no ttft, silently.
            if not self._hint_text.get(hint_id, "").strip():
                raise ValueError(
                    "the model finished without any content (a reasoning model "
                    "with thinking on keeps its output out of `content`)"
                )
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
            await self.checkpoint()
            self._hint_trigger = None
            await self.broadcast(p.hint_end(hint_id, "error"))
            await self.broadcast(p.error("hint_failed", str(exc)))
            return
        if self.state.end_hint(hint_id):
            self._llm_status = "ready"
            ttft_ms = self._hint_ttft.get(hint_id)
            self.record_hint(hint_id, mode, "complete", tokens=tokens, ttft_ms=ttft_ms)
            await self.checkpoint()
            self._hint_trigger = None
            await self.broadcast(
                p.hint_end(hint_id, "complete", tokens=tokens, ttft_ms=ttft_ms)
            )

    async def cancel_hint(self, hint_id: str | None = None) -> None:
        # Look before taking the task: a cancel for a hint that is no longer the
        # live one must leave the live one cancellable.
        task = self._hint_task
        active = self.state.active_hint
        if hint_id is not None and hint_id != active:
            return
        self._hint_task = None
        if task is None:
            return
        if active is None:
            # Finished on its own (or already superseded): nothing to announce.
            # An unfinished one is an orphan; stop it quietly.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
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
        await self.checkpoint()
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


def make_router(core: Core):
    """Route a connection to the endpoint its path names.

    Exposed rather than nested inside `run` so the tests drive the same
    routing the server does. Three test modules had their own copy of this,
    which meant the real one was never executed by anything.
    """

    async def router(connection: ServerConnection) -> None:
        route = urlparse(connection.request.path).path
        if route == "/capture":
            await core.handle_capture(connection)
        elif route == "/console":
            await core.handle_console(connection)
        else:
            await connection.close(1003, "unknown endpoint")

    return router


def serve_core(
    core: Core, host: str, port: int, console_root: Path = CONSOLE_ROOT
):
    """The server as an async context manager, for `run` and for tests."""

    def process_request(connection: ServerConnection, request: Request) -> Response | None:
        del connection
        return serve_console(console_root, request)

    return serve(make_router(core), host, port, process_request=process_request)


async def run(
    core: Core, host: str, port: int, console_root: Path = CONSOLE_ROOT
) -> None:
    async with serve_core(core, host, port, console_root):
        log.info("listening on ws://%s:%d", host, port)
        await asyncio.Future()


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))
