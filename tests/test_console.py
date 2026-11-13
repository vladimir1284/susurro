"""Console tests.

Two layers:

* The view model in ``console/state.js`` is pure, so it is exercised directly in
  a browser page with no server and no sockets. That is where the behaviour
  worth pinning down lives — a partial replaced rather than appended, a
  cancelled hint removed rather than left on screen.
* The page itself is driven against a real core with fake backends, which
  checks the wiring: controls enable and disable with the session, transcript
  rows appear, hints stream in.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect, sync_playwright
from websockets.asyncio.server import ServerConnection, serve

from core.llm.fake import CannedHintGenerator
from core.server import Core, serve_console
from core.session.profiles import ProfileStore
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "console"
FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
TOKEN = "test-token"

SCRIPT = [
    ScriptedUtterance(lane="far", text="Tell me about a hard migration.", frames=4),
    ScriptedUtterance(lane="mic", text="We ran both engines in parallel.", frames=4),
]


@pytest.fixture(scope="module")
def browser() -> Iterator:
    with sync_playwright() as pw:
        instance = pw.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 420, "height": 860})
    page = context.new_page()
    yield page
    context.close()


class TestViewModel:
    """`console/state.js` on its own: no socket, no session.

    The page is loaded from the running core rather than from disk, because a
    browser refuses a dynamic import over file://. Nothing else about these
    tests touches the server.
    """

    @pytest.fixture
    def run_js(self, page: Page, live_console):
        url, _ = live_console
        page.goto(url)

        def run(body: str, events: list[dict]):
            return page.evaluate(
                """async ([body, events]) => {
                    const m = await import('./state.js');
                    let state = m.initial();
                    for (const event of events) state = m.apply(state, event);
                    return new Function('state', 'm', body)(state, m);
                }""",
                [body, events],
            )

        return run

    def test_starts_empty(self, run_js) -> None:
        assert run_js("return state.session", []) == "idle"

    def test_a_partial_is_replaced_not_appended(self, run_js) -> None:
        events = [
            {"type": "transcript.partial", "lane": "far", "text": "so tell", "t": 1},
            {"type": "transcript.partial", "lane": "far", "text": "so tell me", "t": 1},
        ]
        assert run_js("return state.partials.far.text", events) == "so tell me"
        assert run_js("return state.lines.length", events) == 0

    def test_a_final_commits_and_clears_its_own_lane_only(self, run_js) -> None:
        events = [
            {"type": "transcript.partial", "lane": "far", "text": "so tell", "t": 1},
            {"type": "transcript.partial", "lane": "mic", "text": "well I", "t": 1},
            {
                "type": "transcript.final",
                "lane": "far",
                "text": "So tell me.",
                "t": 1,
                "dur": 1,
                "seq": 1,
            },
        ]
        assert run_js("return state.lines.map(l => l.text)", events) == ["So tell me."]
        assert run_js("return state.partials.far", events) is None
        # The other lane may still have an utterance open.
        assert run_js("return state.partials.mic.text", events) == "well I"

    def test_hint_deltas_accumulate(self, run_js) -> None:
        events = [
            {"type": "hint.start", "hint_id": "h-1", "trigger": "manual", "mode": "x"},
            {"type": "hint.delta", "hint_id": "h-1", "text": "one "},
            {"type": "hint.delta", "hint_id": "h-1", "text": "two"},
        ]
        assert run_js("return state.hint.text", events) == "one two"
        assert run_js("return state.hint.done", events) is False

    def test_a_delta_for_a_superseded_hint_is_discarded(self, run_js) -> None:
        # Two generations can overlap when a speculative hint is cancelled late.
        events = [
            {"type": "hint.start", "hint_id": "h-2", "trigger": "manual", "mode": "x"},
            {"type": "hint.delta", "hint_id": "h-1", "text": "stale"},
            {"type": "hint.delta", "hint_id": "h-2", "text": "fresh"},
        ]
        assert run_js("return state.hint.text", events) == "fresh"

    def test_a_cancelled_hint_is_removed_not_greyed_out(self, run_js) -> None:
        # It answers a question the other party has moved on from, so leaving it
        # up competes with whatever comes next.
        events = [
            {"type": "hint.start", "hint_id": "h-1", "trigger": "speculative", "mode": "x"},
            {"type": "hint.delta", "hint_id": "h-1", "text": "half an answer"},
            {"type": "hint.end", "hint_id": "h-1", "reason": "cancelled"},
        ]
        assert run_js("return state.hint", events) is None

    def test_a_completed_hint_keeps_its_text_and_metrics(self, run_js) -> None:
        events = [
            {"type": "hint.start", "hint_id": "h-1", "trigger": "manual", "mode": "x"},
            {"type": "hint.delta", "hint_id": "h-1", "text": "done"},
            {
                "type": "hint.end",
                "hint_id": "h-1",
                "reason": "complete",
                "tokens": 4,
                "ttft_ms": 780,
            },
        ]
        assert run_js("return state.hint.text", events) == "done"
        assert run_js("return state.hint.done", events) is True
        assert run_js("return state.hint.ttft_ms", events) == 780

    def test_leaving_live_clears_the_transcript(self, run_js) -> None:
        events = [
            {"type": "state", "session": "live", "stt": "r", "llm": "r",
             "lanes": {"mic": "up", "far": "up"}},
            {"type": "transcript.final", "lane": "far", "text": "said", "t": 1,
             "dur": 1, "seq": 1},
            {"type": "state", "session": "configured", "stt": "r", "llm": "r",
             "lanes": {"mic": "down", "far": "down"}},
        ]
        assert run_js("return state.lines.length", events) == 0

    def test_errors_are_kept_but_bounded(self, run_js) -> None:
        events = [
            {"type": "error", "code": f"e{i}", "detail": "d"} for i in range(8)
        ]
        assert run_js("return state.errors.length", events) == 5
        assert run_js("return state.errors[4].code", events) == "e7"

    def test_unknown_events_are_ignored(self, run_js) -> None:
        # A newer core may send events this console does not know yet.
        events = [{"type": "something.new", "payload": 1}]
        assert run_js("return state.session", events) == "idle"


@pytest.fixture
def live_console() -> Iterator[tuple[str, Core]]:
    """A core on its own thread, serving the console over HTTP and WebSocket."""
    core = Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(script=list(SCRIPT), partial_every=2),
        generator=CannedHintGenerator(chunk_delay_s=0.01),
        token=TOKEN,
    )
    ready = threading.Event()
    port: list[int] = []
    loop = asyncio.new_event_loop()
    shutdown = asyncio.Event()

    async def router(connection: ServerConnection) -> None:
        route = urlparse(connection.request.path).path
        if route == "/capture":
            await core.handle_capture(connection)
        elif route == "/console":
            await core.handle_console(connection)
        else:
            await connection.close(1003, "unknown endpoint")

    async def main() -> None:
        async with serve(
            router,
            "127.0.0.1",
            0,
            process_request=lambda _conn, request: serve_console(CONSOLE, request),
        ) as server:
            port.append(server.sockets[0].getsockname()[1])
            ready.set()
            await shutdown.wait()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
        loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "core did not start"
    yield f"http://127.0.0.1:{port[0]}", core
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=5)


class TestStaticServing:
    def test_the_index_is_served_at_the_root(self, page: Page, live_console) -> None:
        url, _ = live_console
        response = page.goto(url)
        assert response is not None and response.status == 200
        expect(page.locator("h1")).to_have_text("Susurro")

    def test_module_assets_are_served_with_a_usable_content_type(
        self, page: Page, live_console
    ) -> None:
        # A JavaScript module served as text/plain is refused by the browser,
        # and the failure shows up as a blank page rather than an error.
        url, _ = live_console
        response = page.request.get(f"{url}/state.js")
        assert response.status == 200
        assert "javascript" in response.headers["content-type"]

    def test_a_path_outside_the_console_directory_is_refused(
        self, page: Page, live_console
    ) -> None:
        url, _ = live_console
        assert page.request.get(f"{url}/../pyproject.toml").status == 404


class TestConsolePage:
    def open(self, page: Page, url: str, **params: str) -> None:
        query = "&".join(
            f"{key}={value}"
            for key, value in {"token": TOKEN, **params}.items()
        )
        page.goto(f"{url}/?{query}")
        expect(page.locator("#session-pill")).to_have_text("idle")

    def test_controls_follow_the_session(self, page: Page, live_console) -> None:
        url, _ = live_console
        self.open(page, url, profile="en-technical-backend", language="en",
                  mode="conversational")
        expect(page.locator("#hint-button")).to_be_disabled()

        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")
        expect(page.locator("#hint-button")).to_be_enabled()
        expect(page.locator("#start")).to_be_disabled()

        page.locator("#stop").click()
        expect(page.locator("#session-pill")).to_have_text("configured")
        expect(page.locator("#hint-button")).to_be_disabled()

    def test_a_hint_streams_into_the_page(self, page: Page, live_console) -> None:
        url, _ = live_console
        self.open(page, url, profile="en-technical-backend", language="en",
                  mode="conversational")
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")

        page.locator("#hint-button").click()
        expect(page.locator("#hint-body")).to_contain_text("Migration rollback")
        expect(page.locator("#hint-meta")).to_contain_text("manual")

    def test_space_requests_a_hint(self, page: Page, live_console) -> None:
        url, _ = live_console
        self.open(page, url, profile="en-technical-backend", language="en",
                  mode="conversational")
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")

        page.locator("body").press("Space")
        expect(page.locator("#hint-body")).to_contain_text("Migration rollback")

    def test_lane_indicators_follow_capture(self, page: Page, live_console) -> None:
        url, core = live_console
        self.open(page, url, profile="en-technical-backend", language="en",
                  mode="conversational")
        expect(page.locator("#lane-far")).to_have_attribute("data-up", "false")

        import websockets.sync.client

        from core import protocol as p

        with websockets.sync.client.connect(
            f"{url.replace('http', 'ws')}/capture?token={TOKEN}"
        ) as capture:
            capture.send(p.Hello(session_id="s", lane="far", client="t/0.1").to_json())
            capture.recv()
            expect(page.locator("#lane-far")).to_have_attribute("data-up", "true")
        expect(page.locator("#lane-far")).to_have_attribute("data-up", "false")

    def test_transcript_rows_are_attributed_by_lane(
        self, page: Page, live_console
    ) -> None:
        url, _ = live_console
        self.open(page, url, profile="en-technical-backend", language="en",
                  mode="conversational")
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")

        import websockets.sync.client

        from core import protocol as p

        with websockets.sync.client.connect(
            f"{url.replace('http', 'ws')}/capture?token={TOKEN}"
        ) as capture:
            capture.send(p.Hello(session_id="s", lane="far", client="t/0.1").to_json())
            capture.recv()
            for i in range(4):
                capture.send(
                    p.AudioFrame(timestamp_us=i * 20_000, payload=b"x" * 20).encode()
                )
            row = page.locator('.utterance[data-lane="far"]')
            expect(row.first).to_contain_text("Tell me about a hard migration.")
            expect(row.first.locator(".who")).to_have_text("them")

    def test_an_error_from_the_core_is_shown(self, page: Page, live_console) -> None:
        url, _ = live_console
        # No profile in the query string, so Start sends session.start with
        # nothing configured and the core refuses it.
        self.open(page, url)
        page.locator("#start").click()
        expect(page.locator("#errors")).to_contain_text("bad_transition")
