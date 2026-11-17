"""Server integration tests, over real WebSocket connections.

The backends are fakes, so these run anywhere — including on hardware that
cannot host a model at all. What is being tested is the plumbing: routing,
authorization, lane registration, the session state machine as the server
drives it, and the hint lifecycle.

One assertion here matters more than the rest:
``generator.prefill_count == 1`` after several hints. The entire latency
argument in docs/architecture/inference.md rests on the preamble being
processed once per session rather than once per hint, and that is a property of
this server, not of any model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import websockets

from capture.codec import FRAME_SAMPLES, OpusEncoder
from core import protocol as p
from core.llm.fake import CannedHintGenerator
from core.server import Core, serve_core
from core.session.history import HistoryStore, Line
from core.session.profiles import ProfileStore
from core.stt.base import Segment
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance
from core.stt.whisper import WhisperRecognizer

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
TOKEN = "test-token"

def opus_frame(encoder: OpusEncoder, n: int) -> bytes:
    """A real Opus packet. The server decodes before recognizing, so a frame of
    arbitrary bytes would be testing the decoder's tolerance rather than the
    path under test."""
    pcm = b"".join(
        int(3000 * ((i + n) % 32 - 16) / 16).to_bytes(2, "little", signed=True)
        for i in range(FRAME_SAMPLES)
    )
    return p.AudioFrame(timestamp_us=n * 20_000, payload=encoder.encode(pcm)).encode()


SCRIPT = [
    ScriptedUtterance(lane="far", text="So tell me about a hard migration.", frames=4),
    ScriptedUtterance(lane="mic", text="We ran both engines in parallel.", frames=4),
]


@pytest.fixture
async def core() -> Core:
    return Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(script=list(SCRIPT), partial_every=2),
        generator=CannedHintGenerator(),
        token=TOKEN,
    )


@pytest.fixture
async def url(core: Core) -> AsyncIterator[str]:
    # The server's own routing, not a copy of it: three test modules used to
    # reimplement this, so the real one was never executed by anything.
    async with serve_core(core, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


async def recv_json(ws: websockets.ClientConnection) -> dict:
    return json.loads(await ws.recv())


async def drain(ws: websockets.ClientConnection, kind: str, limit: int = 40) -> dict:
    """Read console events until one of ``kind`` arrives."""
    for _ in range(limit):
        event = await recv_json(ws)
        if event["type"] == kind:
            return event
    raise AssertionError(f"no {kind!r} event in {limit} messages")


async def await_phase(
    ws: websockets.ClientConnection, phase: str, limit: int = 40
) -> dict:
    """Wait for a specific session phase.

    More than one `state` arrives around session.start: the core announces
    that the speech model is loading before it announces that the session is
    live, so that a first run -- which downloads the model -- does not look
    like a hang.
    """
    for _ in range(limit):
        event = await recv_json(ws)
        if event["type"] == "state" and event["session"] == phase:
            return event
    raise AssertionError(f"session never reached {phase!r} in {limit} messages")


async def open_console(url: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"{url}/console?token={TOKEN}")
    # A console is greeted with the profiles it can choose from, then the
    # session state.
    await drain(ws, "state")
    return ws


async def go_live(console: websockets.ClientConnection, profile: str, language: str,
                  mode: str) -> None:
    await console.send(
        json.dumps(
            {
                "type": "session.config",
                "profile_id": profile,
                "language": language,
                "mode": mode,
            }
        )
    )
    await drain(console, "state")
    await console.send(json.dumps({"type": "session.start"}))
    await await_phase(console, "live")


class TestAuthorization:
    @pytest.mark.parametrize("endpoint", ["console", "capture"])
    async def test_a_wrong_token_is_refused(self, url: str, endpoint: str) -> None:
        async with websockets.connect(f"{url}/{endpoint}?token=wrong") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
            assert ws.close_code == 1008

    @pytest.mark.parametrize("endpoint", ["console", "capture"])
    async def test_a_missing_token_is_refused(self, url: str, endpoint: str) -> None:
        async with websockets.connect(f"{url}/{endpoint}") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
            assert ws.close_code == 1008

    async def test_an_unknown_endpoint_is_refused(self, url: str) -> None:
        async with websockets.connect(f"{url}/nope?token={TOKEN}") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
            assert ws.close_code == 1003


class TestConsoleSession:
    async def test_a_console_gets_state_on_connect(self, url: str) -> None:
        async with websockets.connect(f"{url}/console?token={TOKEN}") as ws:
            assert (await recv_json(ws))["type"] == "profiles"
            event = await recv_json(ws)
            assert event["type"] == "state"
            assert event["session"] == "idle"
            assert event["lanes"] == {"mic": "down", "far": "down"}

    async def test_start_before_config_is_refused(self, url: str) -> None:
        console = await open_console(url)
        await console.send(json.dumps({"type": "session.start"}))
        error = await recv_json(console)
        assert error["type"] == "error"
        assert error["code"] == "bad_transition"
        await console.close()

    async def test_a_profile_language_mismatch_is_refused(self, url: str) -> None:
        # The profile decides which recognizer is loaded. A session.config that
        # disagrees with it is a bug in the console, not a preference.
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "es-business-industrial",
                    "language": "en",
                    "mode": "business",
                }
            )
        )
        error = await recv_json(console)
        assert error["type"] == "error"
        assert "es-business-industrial" in error["detail"]
        await console.close()

    async def test_an_unknown_profile_is_refused(self, url: str) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "absent",
                    "language": "en",
                    "mode": "technical",
                }
            )
        )
        assert (await recv_json(console))["code"] == "bad_request"
        await console.close()

    async def test_config_is_refused_while_live(self, url: str) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "es-business-industrial",
                    "language": "es",
                    "mode": "business",
                }
            )
        )
        assert (await recv_json(console))["code"] == "bad_transition"
        await console.close()

    async def test_a_malformed_command_is_reported_not_fatal(self, url: str) -> None:
        console = await open_console(url)
        await console.send('{"type":"session.pause"}')
        assert (await recv_json(console))["code"] == "malformed_message"
        # The connection survives: one bad message must not drop the console
        # in the middle of a call.
        await console.send(json.dumps({"type": "session.stop"}))
        assert (await recv_json(console))["type"] == "error"
        await console.close()


class TestCapture:
    async def test_hello_is_acknowledged_and_lane_goes_up(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            assert (await recv_json(capture))["type"] == "ready"
            assert (await drain(console, "state"))["lanes"]["far"] == "up"
        assert (await drain(console, "state"))["lanes"]["far"] == "down"
        await console.close()

    async def test_a_lane_cannot_be_claimed_twice(self, url: str) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as first:
            await first.send(
                p.Hello(session_id="s", lane="mic", client="test/0.1").to_json()
            )
            await recv_json(first)
            async with websockets.connect(f"{url}/capture?token={TOKEN}") as second:
                await second.send(
                    p.Hello(session_id="s", lane="mic", client="other/0.1").to_json()
                )
                error = await recv_json(second)
                assert error["code"] == "lane_busy"

    async def test_an_unsupported_version_is_reported_before_closing(
        self, url: str
    ) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": 99,
                        "session_id": "s",
                        "lane": "mic",
                        "codec": "opus",
                        "sample_rate": 16000,
                        "frame_ms": 20,
                        "client": "test/0.1",
                    }
                )
            )
            assert (await recv_json(capture))["code"] == "unsupported_version"

    async def test_text_on_the_capture_socket_is_rejected(self, url: str) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="mic", client="test/0.1").to_json()
            )
            await recv_json(capture)
            await capture.send("this should have been binary")
            assert (await recv_json(capture))["code"] == "malformed_frame"

    async def test_a_malformed_frame_is_reported_not_fatal(self, url: str) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="mic", client="test/0.1").to_json()
            )
            await recv_json(capture)
            await capture.send(b"\x00" * 4)  # shorter than a timestamp
            assert (await recv_json(capture))["code"] == "malformed_frame"
            await capture.send(opus_frame(OpusEncoder(), 1))

    async def test_frames_before_session_start_are_dropped(
        self, url: str, core: Core
    ) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await recv_json(capture)
            encoder = OpusEncoder()
            for i in range(10):
                await capture.send(opus_frame(encoder, i))
        assert core.transcript == []


class TestTranscriptFlow:
    async def test_partials_then_a_final_reach_the_console(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await recv_json(capture)
            await drain(console, "state")
            encoder = OpusEncoder()
            for i in range(4):
                await capture.send(opus_frame(encoder, i))
            partial = await drain(console, "transcript.partial")
            final = await drain(console, "transcript.final")

        assert partial["lane"] == "far"
        assert final["text"] == "So tell me about a hard migration."
        assert final["seq"] == 1
        # Only the final is kept for the model, and it is attributed by lane
        # rather than by any speaker model.
        assert core.transcript == ["them: So tell me about a hard migration."]
        await console.close()


class TestHints:
    async def test_a_hint_streams_and_completes(self, url: str, core: Core) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        start = await drain(console, "hint.start")
        assert start["trigger"] == "manual"
        delta = await drain(console, "hint.delta")
        assert delta["hint_id"] == start["hint_id"]
        end = await drain(console, "hint.end")
        assert end["reason"] == "complete"
        assert end["tokens"] >= 1
        await console.close()

    async def test_the_preamble_is_prefilled_once_per_session_not_per_hint(
        self, url: str, core: Core
    ) -> None:
        # This is the assertion the latency budget depends on.
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        for _ in range(3):
            await console.send(
                json.dumps({"type": "hint.request", "mode": "conversational"})
            )
            await drain(console, "hint.end")
        assert core.generator.prefill_count == 1
        assert core.generator.prefilled.startswith("## BACKGROUND")
        assert core.generator.prefilled_language == "en"
        await console.close()

    async def test_a_hint_sees_the_committed_transcript(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        core.transcript.append("them: what did you do?")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        assert core.generator.contexts[-1].transcript == ("them: what did you do?",)
        await console.close()

    async def test_a_hint_requires_a_live_session(self, url: str) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        assert (await recv_json(console))["code"] == "bad_transition"
        await console.close()

    async def test_a_slow_hint_can_be_cancelled(self, url: str, core: Core) -> None:
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        start = await drain(console, "hint.start")
        await console.send(
            json.dumps({"type": "hint.cancel", "hint_id": start["hint_id"]})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "cancelled"
        assert end["hint_id"] == start["hint_id"]
        await console.close()

    async def test_a_new_request_supersedes_one_in_flight(
        self, url: str, core: Core
    ) -> None:
        # A speculative hint is often still streaming when the next end-of-turn
        # arrives. The newer question is the one worth answering.
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        first = await drain(console, "hint.start")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        cancelled = await drain(console, "hint.end")
        assert cancelled["hint_id"] == first["hint_id"]
        assert cancelled["reason"] == "cancelled"
        second = await drain(console, "hint.start")
        assert second["hint_id"] != first["hint_id"]
        await console.close()

    async def test_stopping_a_session_clears_the_transcript(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        core.transcript.append("them: something")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")
        assert core.transcript == []
        await console.close()


class TestHintCancellationIsReliable:
    """D2 and D3 of AUDIT.md: a cancel that silently did nothing."""

    async def test_stopping_the_session_ends_the_hint_in_flight(
        self, url: str, core: Core, tmp_path: Path
    ) -> None:
        core.history = HistoryStore(tmp_path, enabled=True)
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.delta")
        await console.send(json.dumps({"type": "session.stop"}))

        # Collect everything that arrives from the stop onwards, including
        # whatever precedes the state change.
        await asyncio.sleep(0.4)
        events = []
        while True:
            try:
                events.append(json.loads(await asyncio.wait_for(console.recv(), 0.1)))
            except TimeoutError:
                break
        types = [e["type"] for e in events]
        ends = [i for i, t in enumerate(types) if t == "hint.end"]
        assert len(ends) == 1
        assert ends and events[ends[0]]["reason"] == "cancelled"
        assert "hint.delta" not in types[ends[0] + 1 :]
        assert core._hint_task is None
        # The model is not left busy and the partial text is recorded.
        recorded = core.history.load(core.history.list_sessions()[0]["session_id"])
        assert recorded.hints[0].reason == "cancelled"
        assert recorded.hints[0].text
        await console.close()

    async def test_stop_sends_exactly_one_cancelled_end(
        self, url: str, core: Core
    ) -> None:
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.delta")
        await console.send(json.dumps({"type": "session.stop"}))
        end = await drain(console, "hint.end")
        assert end["reason"] == "cancelled"
        await await_phase(console, "configured")
        await asyncio.sleep(0.3)
        assert core._hint_task is None
        await console.close()

    async def test_a_stale_cancel_does_not_make_the_live_hint_uncancellable(
        self, url: str, core: Core
    ) -> None:
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        start = await drain(console, "hint.start")
        await console.send(json.dumps({"type": "hint.cancel", "hint_id": "h-999"}))
        await console.send(
            json.dumps({"type": "hint.cancel", "hint_id": start["hint_id"]})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "cancelled"
        assert end["hint_id"] == start["hint_id"]
        await console.close()


class TestProfileListing:
    async def test_a_console_is_greeted_with_the_profiles(self, url: str) -> None:
        async with websockets.connect(f"{url}/console?token={TOKEN}") as ws:
            event = await recv_json(ws)
            assert event["type"] == "profiles"
            assert {entry["id"] for entry in event["profiles"]} == {
                "en-technical-backend",
                "es-business-industrial",
            }

    async def test_the_listing_carries_what_the_picker_renders(self, url: str) -> None:
        async with websockets.connect(f"{url}/console?token={TOKEN}") as ws:
            entry = (await recv_json(ws))["profiles"][0]
            assert set(entry) == {"id", "label", "language", "mode"}

    async def test_it_can_be_asked_for_again(self, url: str) -> None:
        console = await open_console(url)
        await console.send(json.dumps({"type": "profiles.request"}))
        assert (await recv_json(console))["type"] == "profiles"
        await console.close()

    async def test_an_unreadable_profile_does_not_hide_the_rest(
        self, tmp_path: Path, core: Core
    ) -> None:
        # One bad file in the directory must not leave the console with an
        # empty picker and no way to start anything.
        import shutil

        shutil.copy(FIXTURES / "en-technical-backend.json", tmp_path)
        (tmp_path / "broken.json").write_text("{not json")
        core.profiles = ProfileStore(tmp_path)
        listing = json.loads(core.profiles_event())
        assert [entry["id"] for entry in listing["profiles"]] == [
            "en-technical-backend"
        ]


class TestProfileEditing:
    @pytest.fixture
    def writable(self, tmp_path: Path, core: Core) -> Path:
        import shutil

        shutil.copy(FIXTURES / "en-technical-backend.json", tmp_path)
        core.profiles = ProfileStore(tmp_path)
        return tmp_path

    async def test_a_profile_can_be_fetched_in_full(self, url: str) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps(
                {"type": "profile.request", "profile_id": "en-technical-backend"}
            )
        )
        event = await recv_json(console)
        assert event["type"] == "profile"
        # The listing carries labels only; the editor needs the context.
        assert "resume" in event["profile"]["context"]
        await console.close()

    async def test_saving_writes_the_file_and_refreshes_the_listing(
        self, url: str, core: Core, writable: Path
    ) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "en-technical-backend",
                    "label": "Edited from the console",
                    "language": "en",
                    "mode": "technical",
                    "context": {"resume": "rewritten"},
                }
            )
        )
        listing = await drain(console, "profiles")
        assert listing["profiles"][0]["label"] == "Edited from the console"
        assert core.profiles.load("en-technical-backend").context["resume"] == (
            "rewritten"
        )
        await console.close()

    async def test_saving_creates_a_new_profile(
        self, url: str, core: Core, writable: Path
    ) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "brand-new",
                    "label": "Brand new",
                    "language": "es",
                    "mode": "business",
                    "context": {"role": "una reunión"},
                }
            )
        )
        listing = await drain(console, "profiles")
        assert "brand-new" in {entry["id"] for entry in listing["profiles"]}
        await console.close()

    async def test_the_live_profile_cannot_be_overwritten(
        self, url: str, core: Core, writable: Path
    ) -> None:
        # Its context is already prefilled into the model's cache. Saving over
        # it would leave the console showing text the model has never seen,
        # with nothing to indicate it.
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        await console.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "en-technical-backend",
                    "label": "Changed mid-session",
                    "language": "en",
                    "mode": "technical",
                    "context": {"resume": "different"},
                }
            )
        )
        error = await recv_json(console)
        assert error["code"] == "bad_transition"
        assert "prefilled" in error["detail"]
        assert core.profiles.load("en-technical-backend").label != "Changed mid-session"
        await console.close()

    async def test_another_profile_can_be_edited_while_live(
        self, url: str, core: Core, writable: Path
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        await console.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "other",
                    "label": "Prepared for later",
                    "language": "en",
                    "mode": "conversational",
                    "context": {"notes": "for the next call"},
                }
            )
        )
        listing = await drain(console, "profiles")
        assert "other" in {entry["id"] for entry in listing["profiles"]}
        await console.close()

    async def test_an_id_that_could_escape_the_directory_is_refused(
        self, url: str, writable: Path
    ) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "../escaped",
                    "label": "Nope",
                    "language": "en",
                    "mode": "technical",
                    "context": {"resume": "x"},
                }
            )
        )
        error = await recv_json(console)
        assert error["code"] == "malformed_message"
        assert not (writable.parent / "escaped.json").exists()
        await console.close()

    async def test_a_save_reaches_other_consoles(
        self, url: str, writable: Path
    ) -> None:
        # Two screens is a normal setup; one must not show a stale listing.
        first = await open_console(url)
        second = await open_console(url)
        await first.send(
            json.dumps(
                {
                    "type": "profile.save",
                    "profile_id": "shared",
                    "label": "Seen by both",
                    "language": "en",
                    "mode": "conversational",
                    "context": {"notes": "x"},
                }
            )
        )
        listing = await drain(second, "profiles")
        assert "shared" in {entry["id"] for entry in listing["profiles"]}
        await first.close()
        await second.close()


class TestSessionRecording:
    @pytest.fixture
    def recorded(self, tmp_path: Path, core: Core) -> HistoryStore:
        core.history = HistoryStore(tmp_path, enabled=True)
        return core.history

    async def test_recording_is_off_unless_configured(self, url: str) -> None:
        console = await open_console(url)
        await console.send(json.dumps({"type": "sessions.request"}))
        event = await recv_json(console)
        assert event["type"] == "sessions"
        # The console shows this. A tool that quietly records conversations is
        # not one anybody should trust.
        assert event["recording"] is False
        assert event["sessions"] == []
        await console.close()

    async def test_exporting_without_recording_says_why(self, url: str) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps({"type": "session.export", "session_id": "whatever"})
        )
        error = await recv_json(console)
        assert error["code"] == "history_failed"
        assert "configuration" in error["detail"]
        await console.close()

    async def test_a_session_is_written_when_it_stops(
        self, url: str, core: Core, recorded
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        core.transcript.append("them: Why did you leave?")
        core._recording.lines.append(
            Line(lane="far", text="Why did you leave?", t=1.0, dur=2.0, seq=1)
        )
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")
        assert len(recorded.list_sessions()) == 1
        await console.close()

    async def test_an_empty_session_is_not_written(
        self, url: str, core: Core, recorded
    ) -> None:
        # An empty file for every accidental start would bury the ones that
        # matter.
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "technical")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")
        assert recorded.list_sessions() == []
        await console.close()

    async def test_a_hint_is_recorded_with_the_text_that_was_shown(
        self, url: str, core: Core, recorded
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")

        session_id = recorded.list_sessions()[0]["session_id"]
        hints = recorded.load(session_id).hints
        assert len(hints) == 1
        assert "Migration rollback" in hints[0].text
        assert hints[0].trigger == "manual"
        await console.close()

    async def test_an_export_renders_markdown(
        self, url: str, core: Core, recorded
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")

        session_id = recorded.list_sessions()[0]["session_id"]
        await console.send(
            json.dumps({"type": "session.export", "session_id": session_id})
        )
        export = await drain(console, "export")
        assert export["markdown"].startswith("# Session ")
        assert "Migration rollback" in export["markdown"]
        await console.close()

    async def test_a_recording_can_be_deleted(
        self, url: str, core: Core, recorded
    ) -> None:
        from core.session.history import Recording, now

        recorded.save(
            Recording(
                session_id="2026-01-01T00-00-00Z",
                profile_id="p",
                language="en",
                mode="technical",
                started_at=now(),
                lines=[],
            )
        )
        console = await open_console(url)
        await console.send(
            json.dumps(
                {"type": "session.delete", "session_id": "2026-01-01T00-00-00Z"}
            )
        )
        listing = await drain(console, "sessions")
        assert listing["sessions"] == []
        await console.close()

    async def test_a_session_id_that_could_escape_is_refused(
        self, url: str, recorded
    ) -> None:
        console = await open_console(url)
        await console.send(
            json.dumps({"type": "session.delete", "session_id": "../escaped"})
        )
        assert (await recv_json(console))["code"] == "malformed_message"
        await console.close()


class TestLatencyMeasurement:
    """The number M2's gate is stated in, measured in the live system.

    It was in the protocol, rendered by the console, stored in recordings and
    documented -- and nothing produced it. A field that is always absent is
    worse than a missing one: everything downstream looks finished.
    """

    async def test_a_completed_hint_reports_its_time_to_first_token(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "complete"
        assert isinstance(end["ttft_ms"], int)
        assert end["ttft_ms"] >= 0
        await console.close()

    async def test_the_clock_starts_when_the_hint_is_asked_for(
        self, url: str, core: Core
    ) -> None:
        # Not when the request reaches the model: scheduling and prompt
        # assembly are latency the user waits through too.
        core.generator.chunk_delay_s = 0.05
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        end = await drain(console, "hint.end")
        assert end["ttft_ms"] >= 50
        await console.close()

    async def test_a_cancelled_hint_keeps_the_measurement_it_made(
        self, url: str, core: Core
    ) -> None:
        # A speculative hint that was abandoned still measured the model.
        core.generator.chunk_delay_s = 0.02
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        start = await drain(console, "hint.start")
        await drain(console, "hint.delta")
        await console.send(
            json.dumps({"type": "hint.cancel", "hint_id": start["hint_id"]})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "cancelled"
        assert isinstance(end["ttft_ms"], int)
        await console.close()

    async def test_the_measurement_reaches_the_recording(
        self, url: str, core: Core, tmp_path: Path
    ) -> None:
        core.history = HistoryStore(tmp_path, enabled=True)
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")

        session_id = core.history.list_sessions()[0]["session_id"]
        recorded = core.history.load(session_id).hints[0]
        assert recorded.ttft_ms is not None
        await console.close()

    async def test_measurements_do_not_leak_between_hints(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        for _ in range(3):
            await console.send(
                json.dumps({"type": "hint.request", "mode": "conversational"})
            )
            await drain(console, "hint.end")
        # Each hint cleans up after itself; a session of a hundred hints must
        # not accumulate a hundred timers.
        assert core._hint_started == {}
        assert core._hint_ttft == {}
        await console.close()


class TestRecognizerOffTheEventLoop:
    """Whisper takes hundreds of milliseconds per pass.

    Run inline, that stalls audio ingestion and the console with it. The fake
    recognizer is instant, which is exactly why no test caught this until the
    question was asked directly.
    """

    async def test_a_slow_recognizer_does_not_block_the_console(
        self, url: str, core: Core
    ) -> None:
        import time as _time

        from capture.codec import OpusEncoder

        class SlowRecognizer:
            language = "en"
            calls = 0

            def select_language(self, language) -> None: ...

            def select_vocabulary(self, terms) -> None: ...

            def warmup(self) -> None: ...

            def reset(self) -> None: ...

            def feed(self, lane, timestamp_us, pcm):
                SlowRecognizer.calls += 1
                _time.sleep(0.05)  # a tenth of a real whisper pass
                return []

            def flush(self, lane):
                return []

        core.recognizer = SlowRecognizer()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")

        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await capture.recv()
            encoder = OpusEncoder()
            for index in range(4):
                await capture.send(opus_frame(encoder, index))

            # The console answers while recognition is still working. Inline,
            # this would wait for every frame to finish first.
            started = _time.perf_counter()
            await console.send(json.dumps({"type": "profiles.request"}))
            assert (await drain(console, "profiles"))["type"] == "profiles"
            assert _time.perf_counter() - started < 0.15

        await console.close()

    async def test_a_stalled_recognizer_does_not_stop_the_socket_being_read(
        self, url: str, core: Core
    ) -> None:
        # A lane that awaited the recognizer stopped reading whenever Whisper
        # was slow, the websocket library stopped answering pings with it, and
        # the client gave the lane up for dead after its keepalive timeout.
        import threading

        from capture.codec import OpusEncoder

        release = threading.Event()

        class Stalled:
            language = "en"

            def select_language(self, language) -> None: ...

            def select_vocabulary(self, terms) -> None: ...

            def warmup(self) -> None: ...

            def reset(self) -> None: ...

            def feed(self, lane, timestamp_us, pcm):
                release.wait(timeout=10)
                return []

            def flush(self, lane):
                return []

        core.recognizer = Stalled()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        try:
            async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
                await capture.send(
                    p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
                )
                await capture.recv()
                encoder = OpusEncoder()
                for index in range(80):
                    await capture.send(opus_frame(encoder, index))
                # The recognizer has not returned from its first frame. A pong
                # still comes back, because the socket is still being read.
                pong = await capture.ping()
                await asyncio.wait_for(pong, timeout=2.0)
        finally:
            release.set()
        await console.close()

    async def test_audio_already_received_is_transcribed_before_the_lane_closes(
        self, url: str, core: Core
    ) -> None:
        import time as _time

        from capture.codec import OpusEncoder

        fed: list[int] = []

        class Counting:
            language = "en"

            def select_language(self, language) -> None: ...

            def select_vocabulary(self, terms) -> None: ...

            def warmup(self) -> None: ...

            def reset(self) -> None: ...

            def feed(self, lane, timestamp_us, pcm):
                _time.sleep(0.005)  # slow enough that frames wait in the queue
                fed.append(timestamp_us)
                return []

            def flush(self, lane):
                return []

        core.recognizer = Counting()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await capture.recv()
            encoder = OpusEncoder()
            for index in range(40):
                await capture.send(opus_frame(encoder, index))
        for _ in range(200):  # the lane is released once its queue is worked through
            if core.state.lanes["far"] == "down":
                break
            await asyncio.sleep(0.02)
        assert core.state.lanes["far"] == "down"
        assert len(fed) == 40
        await console.close()

    async def test_the_session_takes_its_language_from_the_profile(
        self, url: str, core: Core
    ) -> None:
        # The recognizer is built once, from configuration, in English. A
        # Spanish profile must still get a Spanish recognizer; otherwise
        # whisper translates what it hears instead of transcribing it.
        assert core.recognizer.language == "en"
        console = await open_console(url)
        await go_live(console, "es-business-industrial", "es", "business")
        assert core.recognizer.language == "es"
        await console.close()

    async def test_the_model_is_loaded_before_the_session_goes_live(
        self, url: str, core: Core
    ) -> None:
        # A first run downloads the model, which takes minutes. Doing that
        # once audio is arriving looks exactly like a hang.
        order: list[str] = []

        class RecordingRecognizer:
            language = "en"

            def select_language(self, language) -> None: ...

            def select_vocabulary(self, terms) -> None: ...

            def warmup(self) -> None:
                order.append("warmup")

            def reset(self) -> None:
                order.append("reset")

            def feed(self, lane, timestamp_us, pcm):
                order.append("feed")
                return []

            def flush(self, lane):
                return []

        core.recognizer = RecordingRecognizer()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        assert order[0] == "warmup"
        await console.close()

    async def test_the_console_is_told_while_the_model_loads(
        self, url: str, core: Core
    ) -> None:
        class SlowToLoad:
            language = "en"

            def select_language(self, language) -> None: ...

            def select_vocabulary(self, terms) -> None: ...

            def warmup(self) -> None:
                import time as _time

                _time.sleep(0.05)

            def reset(self) -> None: ...

            def feed(self, lane, timestamp_us, pcm):
                return []

            def flush(self, lane):
                return []

        core.recognizer = SlowToLoad()
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "en-technical-backend",
                    "language": "en",
                    "mode": "conversational",
                }
            )
        )
        await drain(console, "state")
        await console.send(json.dumps({"type": "session.start"}))

        loading = await drain(console, "state")
        assert loading["stt"] == "loading"
        live = await await_phase(console, "live")
        assert live["stt"] == "ready"
        await console.close()


class TestWhenTheBackendsMisbehave:
    """Paths the fakes used to make unreachable.

    A stand-in that never hangs, never drops a connection and never raises
    will let a system ship with no handling for any of them. These fakes can
    now do all three.
    """

    async def test_a_model_that_never_speaks_ends_the_hint(
        self, url: str, core: Core
    ) -> None:
        # Otherwise the console says "writing…" for the rest of the call.
        core.generator.hang = True
        core.first_token_deadline_s = 0.2
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "error"
        error = await drain(console, "error")
        assert error["code"] == "hint_failed"
        assert "first token" in error["detail"]
        await console.close()

    async def test_a_model_that_stops_mid_sentence_ends_the_hint(
        self, url: str, core: Core
    ) -> None:
        core.generator.chunk_delay_s = 0.05
        core.hint_deadline_s = 0.12
        core.first_token_deadline_s = 0.2
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        end = await drain(console, "hint.end")
        assert end["reason"] == "error"
        await console.close()

    async def test_a_dropped_connection_is_reported_not_swallowed(
        self, url: str, core: Core
    ) -> None:
        core.generator.fail_after_chunks = 1
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.delta")
        assert (await drain(console, "hint.end"))["reason"] == "error"
        assert (await drain(console, "error"))["code"] == "hint_failed"
        await console.close()

    async def test_a_failed_hint_still_records_what_it_managed(
        self, url: str, core: Core, tmp_path: Path
    ) -> None:
        # A hint that stopped halfway is evidence about the model, not nothing.
        core.history = HistoryStore(tmp_path, enabled=True)
        core.generator.fail_after_chunks = 1
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        await console.send(json.dumps({"type": "session.stop"}))
        await await_phase(console, "configured")

        session_id = core.history.list_sessions()[0]["session_id"]
        recorded = core.history.load(session_id).hints[0]
        assert recorded.reason == "error"
        assert recorded.text
        await console.close()

    async def test_a_failing_recognizer_does_not_take_the_lane_down(
        self, url: str, core: Core
    ) -> None:
        # A transcription hiccup must not become a dead microphone for the
        # rest of a call.
        from capture.codec import OpusEncoder

        core.recognizer = ScriptedRecognizer(
            script=list(SCRIPT), partial_every=2, fail_on_feed=2
        )
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")

        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await capture.recv()
            encoder = OpusEncoder()
            for index in range(5):
                await capture.send(opus_frame(encoder, index))

            assert (await drain(console, "error"))["code"] == "stt_failed"
            # Still connected, still being fed, still accepting commands.
            await console.send(json.dumps({"type": "profiles.request"}))
            assert (await drain(console, "profiles"))["type"] == "profiles"
            assert core.state.lanes["far"] == "up"

        await console.close()

    async def test_a_speech_backend_that_cannot_warm_up_is_reported(
        self, url: str, core: Core
    ) -> None:
        # The "whisper.cpp server is not running" case: the likeliest first-run
        # mistake. It must be an error event, not a dropped connection.
        core.recognizer = ScriptedRecognizer(script=list(SCRIPT), fail_on_warmup=True)
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "en-technical-backend",
                    "language": "en",
                    "mode": "conversational",
                }
            )
        )
        await drain(console, "state")
        await console.send(json.dumps({"type": "session.start"}))
        error = await drain(console, "error")
        assert error["code"] == "stt_failed"
        assert "connection refused" in error["detail"]
        # Connection still open, session still configured, retry possible.
        await console.send(json.dumps({"type": "profiles.request"}))
        await drain(console, "profiles")
        assert core.state.phase.value == "configured"
        core.recognizer.fail_on_warmup = False
        await console.send(json.dumps({"type": "session.start"}))
        await await_phase(console, "live")
        await console.close()

    async def test_a_hint_after_a_failed_one_still_works(
        self, url: str, core: Core
    ) -> None:
        # The failure must not leave the session wedged.
        core.generator.fail_after_chunks = 1
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")

        core.generator.fail_after_chunks = None
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        assert (await drain(console, "hint.end"))["reason"] == "complete"
        await console.close()


class TestModelServerStatus:
    """`state` reported llm: "ready" unconditionally, including when nothing
    was listening. The same defect ttft_ms had: a field that never reflects
    anything is worse than a missing one."""

    class DeadGenerator:
        prefill_count = 0
        prefilled = None
        contexts: list = []

        async def prefill(self, preamble: str, language: str) -> None:
            raise ConnectionError("connection refused")

        async def stream(self, context):
            raise ConnectionError("connection refused")
            yield ""  # pragma: no cover - unreachable, satisfies the type

    async def test_an_unreachable_model_is_reported_at_session_start(
        self, url: str, core: Core
    ) -> None:
        # Not in the middle of a call, which is when the first hint would
        # otherwise have discovered it.
        core.generator = self.DeadGenerator()
        console = await open_console(url)
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "en-technical-backend",
                    "language": "en",
                    "mode": "conversational",
                }
            )
        )
        await drain(console, "state")
        await console.send(json.dumps({"type": "session.start"}))

        error = await drain(console, "error")
        assert error["code"] == "llm_unavailable"
        assert "Transcription will work" in error["detail"]
        await console.close()

    async def test_the_session_still_starts_without_a_model(
        self, url: str, core: Core
    ) -> None:
        # The audio path is still worth having, and the server may come back.
        core.generator = self.DeadGenerator()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        assert core.state.phase.value == "live"
        await console.close()

    async def test_the_state_says_unavailable(self, url: str, core: Core) -> None:
        core.generator = self.DeadGenerator()
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(json.dumps({"type": "profiles.request"}))
        await drain(console, "profiles")
        await console.send(json.dumps({"type": "session.stop"}))
        state = await await_phase(console, "configured")
        assert state["llm"] == "unavailable"
        await console.close()

    async def test_a_working_model_reports_ready(self, url: str, core: Core) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        await console.send(json.dumps({"type": "session.stop"}))
        assert (await await_phase(console, "configured"))["llm"] == "ready"
        await console.close()

    async def test_status_is_unknown_before_anything_asks(self, core: Core) -> None:
        # Honest about not knowing, rather than claiming readiness it has no
        # evidence for.
        assert json.loads(core.state_event())["llm"] == "unknown"

    async def test_a_failed_hint_marks_the_model_unavailable(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")
        core.generator.fail_after_chunks = 0
        await console.send(
            json.dumps({"type": "hint.request", "mode": "conversational"})
        )
        await drain(console, "hint.end")
        await drain(console, "error")
        await console.send(json.dumps({"type": "session.stop"}))
        assert (await await_phase(console, "configured"))["llm"] == "unavailable"
        await console.close()


class TestCorruptAudio:
    async def test_a_packet_that_will_not_decode_is_reported_not_fatal(
        self, url: str, core: Core
    ) -> None:
        # A frame can be well-formed and still not be Opus -- a wrong codec, a
        # truncated packet, a client bug. One bad packet must not take the lane
        # down for the rest of a call.
        console = await open_console(url)
        await go_live(console, "en-technical-backend", "en", "conversational")

        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await capture.recv()
            await capture.send(
                p.AudioFrame(timestamp_us=1, payload=b"\xff" * 300).encode()
            )
            error = json.loads(await capture.recv())
            assert error["code"] == "decode_failed"

            # Still up, and good audio afterwards still works.
            encoder = OpusEncoder()
            await capture.send(opus_frame(encoder, 1))
            assert core.state.lanes["far"] == "up"

        await console.close()


class TestRouting:
    """The server's own routing, now that the tests drive it rather than a copy."""

    async def test_an_unknown_path_is_refused(self, url: str) -> None:
        async with websockets.connect(f"{url}/nope?token={TOKEN}") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
            assert ws.close_code == 1003

    async def test_the_console_is_served_over_http_from_the_same_port(
        self, url: str
    ) -> None:
        # One address to remember, and no CORS to configure.
        #
        # Fetched with an async client on purpose: a blocking urllib call here
        # stalls the event loop the server is running on, so it cannot answer
        # and the test deadlocks until the timeout. The same mistake the
        # recognizer was making, in a test for the fix.
        import httpx

        http = url.replace("ws://", "http://")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{http}/state.js", timeout=5)
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]


class TestEntryPoints:
    @pytest.mark.parametrize(
        "name, target",
        [
            ("susurro-core", "core.main:main"),
            ("susurro-capture", "capture.main:main"),
            ("susurro-bench", "bench.run:main"),
        ],
    )
    def test_every_announced_command_exists(self, name: str, target: str) -> None:
        # argparse has announced these names since the beginning and nothing
        # declared them, so `susurro-core` was "command not found" in a shell
        # and in the installation guide.
        import tomllib

        root = Path(__file__).resolve().parent.parent
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert config["project"]["scripts"][name] == target


class TestLanesShareOneSessionTimeline:
    """D4 of AUDIT.md: each lane counted frames from its own first frame, so a
    gap became lag that never recovered and a late lane was offset against the
    other. Frames are now stamped with arrival time on the session's clock."""

    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    class StampingRecognizer(ScriptedRecognizer):
        """Final per frame, timed by the timestamp the server handed it."""

        def feed(self, lane, timestamp_us, pcm):
            return [
                Segment(
                    lane=lane,
                    text=f"{lane}@{timestamp_us / 1e6:.1f}",
                    t=timestamp_us / 1e6,
                    final=True,
                    dur=0.02,
                )
            ]

    @staticmethod
    def frame() -> bytes:
        return bytes(FRAME_SAMPLES * 2)

    def make(self, tmp_path: Path, recognizer=None) -> tuple[Core, Clock]:
        clock = self.Clock()
        core = Core(
            profiles=ProfileStore(FIXTURES),
            recognizer=recognizer or self.StampingRecognizer(script=[]),
            generator=CannedHintGenerator(),
            token=TOKEN,
            clock=clock,
        )
        core.state.configure("en-technical-backend", "en", "conversational")
        return core, clock

    async def test_a_lane_that_connects_late_is_not_offset_against_the_other(
        self, tmp_path: Path
    ) -> None:
        from core.session.history import Recording, render_markdown

        core, clock = self.make(tmp_path)
        await core.start_session()
        core._recording = Recording(
            session_id="s", profile_id="p", language="en", mode="conversational",
            started_at="now",
        )
        for _ in range(3):
            clock.now += 1.0
            await core.route_audio("far", self.frame())
        clock.now += 1.0
        await core.route_audio("mic", self.frame())  # connects late
        clock.now += 1.0
        await core.route_audio("far", self.frame())

        spoken = [line.text for line in core._recording.lines]
        # Spoken order: far (3 s), far (4 s)... with mic between them at 4 s.
        assert [line.t for line in core._recording.lines] == sorted(
            line.t for line in core._recording.lines
        )
        exported = render_markdown(core._recording)
        positions = [exported.index(text) for text in spoken]
        assert positions == sorted(positions)
        mic = next(line for line in core._recording.lines if line.lane == "mic")
        assert mic.t >= 3.0, "a late lane must not restart at t=0"

    async def test_a_gap_advances_the_lane_clock(self, tmp_path: Path) -> None:
        seen: list[int] = []

        class Spy(self.StampingRecognizer):
            def feed(self, lane, timestamp_us, pcm):
                seen.append(timestamp_us)
                return []

        core, clock = self.make(tmp_path, Spy(script=[]))
        await core.start_session()
        await core.route_audio("far", self.frame())
        clock.now += 10.0  # frames that never arrived
        await core.route_audio("far", self.frame())
        assert seen[1] - seen[0] >= 10_000_000

    async def test_audio_after_a_gap_still_gets_partials(
        self, tmp_path: Path
    ) -> None:
        # The audit's reproduction: a 10 s gap, then real-time audio. lag_s
        # stayed at 10 and the lane shed partials for the rest of the call.
        from test_stt_whisper import FakeModel, FakeSegment

        model = FakeModel([FakeSegment("so tell me", end=99.0)])
        clock = self.Clock()
        whisper = WhisperRecognizer(
            language="en", clock=clock, partial_interval_s=1.0, max_window_s=60.0
        )
        whisper._model = model
        core, _ = self.make(tmp_path, whisper)
        core.clock = clock
        await core.start_session()
        for _ in range(50):
            clock.now += 0.02
            await core.route_audio("far", self.frame())
        before = model.calls
        clock.now += 10.0
        for _ in range(1500):  # 30 s at exactly real-time pace
            clock.now += 0.02
            await core.route_audio("far", self.frame())
        assert model.calls - before >= 25
        assert whisper.lag_s("far", core._lane_clock_us["far"]) < 1.0
