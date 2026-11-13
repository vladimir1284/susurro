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

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import websockets
from websockets.asyncio.server import ServerConnection, serve

from core import protocol as p
from core.llm.fake import CannedHintGenerator
from core.server import Core
from core.session.profiles import ProfileStore
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
TOKEN = "test-token"

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
    async def router(connection: ServerConnection) -> None:
        from urllib.parse import urlparse

        route = urlparse(connection.request.path).path
        if route == "/capture":
            await core.handle_capture(connection)
        elif route == "/console":
            await core.handle_console(connection)
        else:
            await connection.close(1003, "unknown endpoint")

    async with serve(router, "127.0.0.1", 0) as server:
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


async def open_console(url: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"{url}/console?token={TOKEN}")
    await recv_json(ws)  # opening state event
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
    assert (await drain(console, "state"))["session"] == "live"


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
            await capture.send(p.AudioFrame(timestamp_us=1, payload=b"x").encode())

    async def test_frames_before_session_start_are_dropped(
        self, url: str, core: Core
    ) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as capture:
            await capture.send(
                p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
            )
            await recv_json(capture)
            for i in range(10):
                await capture.send(
                    p.AudioFrame(timestamp_us=i * 20_000, payload=b"x").encode()
                )
            await capture.send(
                p.AudioFrame(timestamp_us=999, payload=b"x").encode()
            )
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
            for i in range(4):
                await capture.send(
                    p.AudioFrame(timestamp_us=i * 20_000, payload=b"x").encode()
                )
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
        assert (await drain(console, "state"))["session"] == "configured"
        assert core.transcript == []
        await console.close()
