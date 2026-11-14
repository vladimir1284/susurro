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
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from core.llm.fake import CannedHintGenerator
from core.server import Core, serve_core
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

    async def main() -> None:
        async with serve_core(core, "127.0.0.1", 0, CONSOLE) as server:
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


def _serve(core: Core) -> Iterator[str]:
    """Run a core on its own thread and yield its base URL."""
    import asyncio
    import threading

    ready = threading.Event()
    port: list[int] = []
    loop = asyncio.new_event_loop()
    shutdown = asyncio.Event()

    async def main() -> None:
        async with serve_core(core, "127.0.0.1", 0, CONSOLE) as server:
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
    yield f"http://127.0.0.1:{port[0]}"
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=5)


def _core(profiles: Path) -> Core:
    return Core(
        profiles=ProfileStore(profiles),
        recognizer=ScriptedRecognizer(script=list(SCRIPT), partial_every=2),
        generator=CannedHintGenerator(chunk_delay_s=0.01),
        token=TOKEN,
    )


@pytest.fixture
def empty_console(tmp_path: Path) -> Iterator[tuple[str, Core]]:
    core = _core(tmp_path)
    for url in _serve(core):
        yield url, core


@pytest.fixture
def disappearing_console(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    import shutil

    directory = tmp_path / "profiles"
    directory.mkdir()
    shutil.copy(FIXTURES / "en-technical-backend.json", directory)
    core = _core(directory)
    for url in _serve(core):
        yield url, directory


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
        # The picker is populated from the core, so Start only becomes usable
        # once the profile list has arrived.
        expect(page.locator("#start")).to_be_enabled()

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
            from capture.codec import FRAME_BYTES, OpusEncoder

            encoder = OpusEncoder()
            for i in range(4):
                pcm = bytes(FRAME_BYTES)
                capture.send(
                    p.AudioFrame(
                        timestamp_us=i * 20_000, payload=encoder.encode(pcm)
                    ).encode()
                )
            row = page.locator('.utterance[data-lane="far"]')
            expect(row.first).to_contain_text("Tell me about a hard migration.")
            expect(row.first.locator(".who")).to_have_text("them")

    def test_the_picker_is_filled_from_the_core(self, page: Page, live_console) -> None:
        # The console used to need the profile id in the query string, which
        # meant hand-editing a URL before a call.
        url, _ = live_console
        self.open(page, url)
        options = page.locator("#profile option")
        expect(options).to_have_count(2)
        expect(options.first).to_contain_text("Senior backend")

    def test_start_is_refused_when_there_is_nothing_to_start(
        self, page: Page, empty_console
    ) -> None:
        url, _ = empty_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#session-pill")).to_have_text("idle")
        # Better than letting the core reject it: the console does not offer
        # an action it knows cannot work.
        expect(page.locator("#start")).to_be_disabled()
        expect(page.locator("#profile")).to_be_disabled()

    def test_an_error_from_the_core_is_shown(
        self, page: Page, disappearing_console
    ) -> None:
        # A reachable failure rather than a contrived one: the profile is
        # listed, then removed before the session starts. A rename or a tidy-up
        # between opening the console and pressing Start does exactly this.
        url, directory = disappearing_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        (directory / "en-technical-backend.json").unlink()
        page.locator("#start").click()
        expect(page.locator("#errors")).to_contain_text("bad_request")


class TestProfilePicker:
    def test_the_view_model_stores_the_listing(self, page: Page, live_console) -> None:
        url, _ = live_console
        page.goto(url)
        result = page.evaluate(
            """async () => {
                const m = await import('./state.js');
                let s = m.initial();
                s = m.apply(s, {type: 'profiles', profiles: [
                    {id: 'a', label: 'A', language: 'en', mode: 'technical'}
                ]});
                return s.profiles.length;
            }"""
        )
        assert result == 1

    def test_starting_sends_the_profile_s_own_language(
        self, page: Page, live_console
    ) -> None:
        # The profile decides the language; the core refuses a session.config
        # that disagrees with it. Sending the query string's value instead
        # would put the console in a position to be rejected.
        url, core = live_console
        page.goto(f"{url}/?token={TOKEN}&mode=business")
        expect(page.locator("#start")).to_be_enabled()
        page.select_option("#profile", "es-business-industrial")
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")
        assert core.state.language == "es"

    def test_the_picker_locks_while_live(self, page: Page, live_console) -> None:
        # Changing it mid-session would invalidate the prefilled cache, which
        # the core refuses. Disabling it is friendlier than explaining that.
        url, _ = live_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")
        expect(page.locator("#profile")).to_be_disabled()
        page.locator("#stop").click()
        expect(page.locator("#profile")).to_be_enabled()

    def test_a_query_string_profile_is_preselected(
        self, page: Page, live_console
    ) -> None:
        # Still supported: a bookmark straight into the right session.
        url, _ = live_console
        page.goto(f"{url}/?token={TOKEN}&profile=es-business-industrial")
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#profile")).to_have_value("es-business-industrial")


class TestProfileEditor:
    @pytest.fixture
    def writable_console(self, tmp_path: Path) -> Iterator[tuple[str, Core]]:
        import shutil

        directory = tmp_path / "profiles"
        directory.mkdir()
        for name in ("en-technical-backend.json", "es-business-industrial.json"):
            shutil.copy(FIXTURES / name, directory)
        core = _core(directory)
        for url in _serve(core):
            yield url, core

    def open_editor(self, page: Page, url: str) -> None:
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#edit").click()
        expect(page.locator("#editor")).to_be_visible()
        # The fields stay disabled until the profile arrives. Typing before
        # that used to be silently overwritten by the late reply.
        expect(page.locator("#editor-label")).to_be_enabled()

    def test_the_editor_is_closed_until_asked_for(
        self, page: Page, writable_console
    ) -> None:
        # The console's job is to be glanceable. A form is the opposite of
        # that, so it stays out of the way until someone wants it.
        url, _ = writable_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#editor")).to_be_hidden()

    def test_it_opens_filled_with_the_selected_profile(
        self, page: Page, writable_console
    ) -> None:
        url, _ = writable_console
        self.open_editor(page, url)
        expect(page.locator("#editor-label")).to_have_value("Senior backend / platform engineer — English technical screen")
        expect(page.locator("#editor-language")).to_have_value("en")
        expect(page.locator("#editor-resume")).not_to_have_value("")

    def test_an_edit_is_saved_and_shows_in_the_picker(
        self, page: Page, writable_console
    ) -> None:
        url, core = writable_console
        self.open_editor(page, url)
        page.fill("#editor-label", "Renamed from the console")
        page.fill("#editor-notes", "remember the cutover story")
        page.locator("#editor-save").click()

        expect(page.locator("#editor-status")).to_have_text("saved")
        expect(page.locator("#profile option").first).to_contain_text(
            "Renamed from the console"
        )
        saved = core.profiles.load("en-technical-backend")
        assert saved.context["notes"] == "remember the cutover story"

    def test_the_live_profile_cannot_be_saved_and_says_why(
        self, page: Page, writable_console
    ) -> None:
        url, _ = writable_console
        self.open_editor(page, url)
        page.locator("#editor-close").click()
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")

        page.locator("#edit").click()
        expect(page.locator("#editor")).to_be_visible()
        # The fields stay disabled until the profile arrives. Typing before
        # that used to be silently overwritten by the late reply.
        expect(page.locator("#editor-label")).to_be_enabled()
        page.fill("#editor-label", "Changed mid-call")
        page.locator("#editor-save").click()

        status = page.locator("#editor-status")
        expect(status).to_have_attribute("data-state", "error")
        expect(status).to_contain_text("prefilled")

    def test_closing_the_editor_leaves_the_transcript_alone(
        self, page: Page, writable_console
    ) -> None:
        url, _ = writable_console
        self.open_editor(page, url)
        page.locator("#editor-close").click()
        expect(page.locator("#editor")).to_be_hidden()
        expect(page.locator("#transcript")).to_be_visible()

    def test_typing_before_the_profile_arrives_is_not_discarded(
        self, page: Page, writable_console
    ) -> None:
        # Found by CI, not locally: the fields were editable while the request
        # was in flight, and the reply overwrote whatever had been typed. The
        # save then wrote the old label back and reported success.
        url, _ = writable_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#edit").click()
        expect(page.locator("#editor-label")).to_be_enabled()
        page.fill("#editor-label", "Typed immediately")
        page.locator("#editor-save").click()
        expect(page.locator("#editor-status")).to_have_text("saved")
        expect(page.locator("#editor-label")).to_have_value("Typed immediately")


class TestSessionHistory:
    @pytest.fixture
    def recorded_console(self, tmp_path: Path) -> Iterator[tuple[str, Core]]:
        import shutil

        from core.session.history import HistoryStore, Line, Recording

        profiles = tmp_path / "profiles"
        profiles.mkdir()
        shutil.copy(FIXTURES / "en-technical-backend.json", profiles)
        core = _core(profiles)
        core.history = HistoryStore(tmp_path / "sessions", enabled=True)
        past = Recording(
            session_id="2026-11-12T09-30-00Z",
            profile_id="en-technical-backend",
            language="en",
            mode="technical",
            started_at="2026-11-12T09:30:00+00:00",
            ended_at="2026-11-12T10:05:00+00:00",
            lines=[Line(lane="far", text="Why did you leave?", t=1.0, dur=2.0, seq=1)],
        )
        core.history.save(past)
        for url in _serve(core):
            yield url, core

    def open_history(self, page: Page, url: str) -> None:
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#history").click()
        expect(page.locator("#history-panel")).to_be_visible()

    def test_the_recording_indicator_is_absent_when_nothing_is_recorded(
        self, page: Page, live_console
    ) -> None:
        url, _ = live_console
        self.open_history(page, url)
        expect(page.locator("#recording-pill")).to_be_hidden()
        expect(page.locator("#history-empty")).to_contain_text("Recording is off")

    def test_the_recording_indicator_is_impossible_to_miss_when_it_is_on(
        self, page: Page, recorded_console
    ) -> None:
        # The one thing a user must not have to go looking for.
        url, _ = recorded_console
        self.open_history(page, url)
        expect(page.locator("#recording-pill")).to_be_visible()
        expect(page.locator("#recording-pill")).to_contain_text("recording")

    def test_sessions_are_listed_with_what_they_contain(
        self, page: Page, recorded_console
    ) -> None:
        url, _ = recorded_console
        self.open_history(page, url)
        row = page.locator("#history-list li").first
        expect(row).to_contain_text("2026-11-12")
        expect(row).to_contain_text("en-technical-backend")
        expect(row).to_contain_text("1 lines")

    def test_opening_one_shows_its_transcript(
        self, page: Page, recorded_console
    ) -> None:
        url, _ = recorded_console
        self.open_history(page, url)
        page.locator("#history-list li .view").first.click()
        expect(page.locator("#export-text")).to_contain_text("Why did you leave?")
        expect(page.locator("#export-text")).to_contain_text("**Them:**")

    def test_the_transcript_can_be_downloaded(
        self, page: Page, recorded_console
    ) -> None:
        url, _ = recorded_console
        self.open_history(page, url)
        page.locator("#history-list li .view").first.click()
        link = page.locator("#export-download")
        expect(link).to_have_attribute("download", "susurro-2026-11-12T09-30-00Z.md")
        assert link.get_attribute("href").startswith("blob:")

    def test_going_back_returns_to_the_list(
        self, page: Page, recorded_console
    ) -> None:
        url, _ = recorded_console
        self.open_history(page, url)
        page.locator("#history-list li .view").first.click()
        expect(page.locator("#export-view")).to_be_visible()
        page.locator("#export-close").click()
        expect(page.locator("#export-view")).to_be_hidden()
        expect(page.locator("#history-list")).to_be_visible()

    def test_deleting_takes_two_clicks(
        self, page: Page, recorded_console
    ) -> None:
        # Deleting a transcript cannot be undone. One click would be careless;
        # a dialog would be heavier than the decision deserves.
        url, core = recorded_console
        self.open_history(page, url)
        delete = page.locator("#history-list li .delete").first
        delete.click()
        expect(delete).to_have_text("Delete?")
        assert len(core.history.list_sessions()) == 1

        page.locator("#history-list li .delete").first.click()
        expect(page.locator("#history-list li")).to_have_count(0)
        assert core.history.list_sessions() == []

    def test_closing_the_panel_leaves_the_transcript_alone(
        self, page: Page, recorded_console
    ) -> None:
        url, _ = recorded_console
        self.open_history(page, url)
        page.locator("#history-close").click()
        expect(page.locator("#history-panel")).to_be_hidden()
        expect(page.locator("#transcript")).to_be_visible()


class TestHistoryViewModel:
    """`console/state.js` on the history events, without a server."""

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

    def test_recording_defaults_to_off(self, run_js) -> None:
        assert run_js("return state.recording", []) is False

    def test_the_flag_follows_the_core(self, run_js) -> None:
        events = [{"type": "sessions", "recording": True, "sessions": []}]
        assert run_js("return state.recording", events) is True

    def test_a_listing_replaces_the_previous_one(self, run_js) -> None:
        # A delete answers with a fresh listing; merging would leave the
        # deleted session on screen.
        events = [
            {"type": "sessions", "recording": True, "sessions": [{"session_id": "a"}]},
            {"type": "sessions", "recording": True, "sessions": []},
        ]
        assert run_js("return state.sessions.length", events) == 0

    def test_an_export_is_held_until_replaced(self, run_js) -> None:
        events = [{"type": "export", "session_id": "a", "markdown": "# Session a"}]
        assert run_js("return state.export.markdown", events) == "# Session a"
        assert run_js("return state.export.sessionId", events) == "a"


class TestFirstProfile:
    def test_a_profile_can_be_created_with_none_on_disk(
        self, page: Page, empty_console
    ) -> None:
        # The bootstrap case, found by writing the installation guide: Edit
        # needs something selected, and on a fresh install nothing is. Without
        # New there is no way to make the first profile from the console.
        url, core = empty_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_disabled()
        expect(page.locator("#new")).to_be_enabled()

        page.locator("#new").click()
        expect(page.locator("#editor-label")).to_be_enabled()
        page.fill("#editor-label", "My first role")
        page.fill("#editor-resume", "Fifteen years of backend work.")
        page.locator("#editor-save").click()

        expect(page.locator("#editor-status")).to_have_text("saved")
        expect(page.locator("#profile option")).to_have_count(1)
        expect(page.locator("#start")).to_be_enabled()
        assert core.profiles.list_ids()

    def test_new_is_unavailable_while_live(self, page: Page, live_console) -> None:
        url, _ = live_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")
        expect(page.locator("#new")).to_be_disabled()


class TestLatencyViewModel:
    """The measured time to first token, as the console keeps it."""

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

    def test_latency_is_hidden_until_something_is_measured(self, run_js) -> None:
        assert run_js("return state.latencies.length", []) == 0

    def test_a_measurement_is_kept_from_each_hint(self, run_js) -> None:
        events = [
            {"type": "hint.start", "hint_id": "h-1", "trigger": "manual", "mode": "x"},
            {"type": "hint.end", "hint_id": "h-1", "reason": "complete", "ttft_ms": 400},
        ]
        assert run_js("return state.latencies", events) == [400]

    def test_a_cancelled_hint_still_counts(self, run_js) -> None:
        # It measured the model either way.
        events = [
            {"type": "hint.start", "hint_id": "h-1", "trigger": "speculative",
             "mode": "x"},
            {"type": "hint.end", "hint_id": "h-1", "reason": "cancelled",
             "ttft_ms": 250},
        ]
        assert run_js("return state.latencies", events) == [250]

    def test_the_window_is_bounded(self, run_js) -> None:
        events = []
        for index in range(15):
            events.append(
                {"type": "hint.start", "hint_id": f"h-{index}", "trigger": "manual",
                 "mode": "x"}
            )
            events.append(
                {"type": "hint.end", "hint_id": f"h-{index}", "reason": "complete",
                 "ttft_ms": index}
            )
        assert run_js("return state.latencies.length", events) == 9

    def test_the_median_is_reported_not_the_last_value(self, run_js) -> None:
        # One slow hint should not make a good session look broken.
        events = []
        for index, value in enumerate([300, 320, 310, 4000]):
            events.append(
                {"type": "hint.start", "hint_id": f"h-{index}", "trigger": "manual",
                 "mode": "x"}
            )
            events.append(
                {"type": "hint.end", "hint_id": f"h-{index}", "reason": "complete",
                 "ttft_ms": value}
            )
        assert run_js("return m.medianLatency(state)", events) == 320

    def test_no_measurements_means_no_median(self, run_js) -> None:
        assert run_js("return m.medianLatency(state)", []) is None


class TestLostConnection:
    """A console that looks connected while it is not is worse than one that
    gives up: every control still responds and nothing happens."""

    def test_the_view_model_tracks_the_link(self, page: Page, live_console) -> None:
        url, _ = live_console
        page.goto(url)
        result = page.evaluate(
            """async () => {
                const m = await import('./state.js');
                let s = m.initial();
                const before = s.connected;
                s = m.apply(s, {type: 'connection', connected: true});
                const open = s.connected;
                s = m.apply(s, {type: 'connection', connected: false});
                return [before, open, s.connected];
            }"""
        )
        assert result == [False, True, False]

    def test_a_dropped_core_disables_the_console(
        self, page: Page, tmp_path: Path
    ) -> None:
        # The real thing: the core goes away mid-session.
        import shutil

        directory = tmp_path / "profiles"
        directory.mkdir()
        shutil.copy(FIXTURES / "en-technical-backend.json", directory)
        core = _core(directory)

        urls = _serve(core)
        url = next(urls)
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()

        # Stop the server. The console should notice rather than sit there
        # looking usable.
        for _ in urls:
            pass

        expect(page.locator("#link-pill")).to_be_visible()
        expect(page.locator("#start")).to_be_disabled()
        expect(page.locator("#hint-button")).to_be_disabled()


class TestModelStatusIndicator:
    def test_an_unavailable_model_is_shown(self, page: Page, live_console) -> None:
        # Without it, pressing Hint does nothing visible and the user presses
        # it again.
        url, _ = live_console
        page.goto(url)
        result = page.evaluate(
            """async () => {
                const m = await import('./state.js');
                let s = m.initial();
                s = m.apply(s, {type: 'state', session: 'live', stt: 'ready',
                                llm: 'unavailable', lanes: {mic: 'up', far: 'up'}});
                return [s.llm, s.stt];
            }"""
        )
        assert result == ["unavailable", "ready"]

    def test_the_loading_state_is_carried(self, page: Page, live_console) -> None:
        url, _ = live_console
        page.goto(url)
        result = page.evaluate(
            """async () => {
                const m = await import('./state.js');
                let s = m.initial();
                s = m.apply(s, {type: 'state', session: 'configured', stt: 'loading',
                                llm: 'unknown', lanes: {mic: 'down', far: 'down'}});
                return s.stt;
            }"""
        )
        assert result == "loading"

    def test_nothing_is_shown_when_both_are_fine(
        self, page: Page, live_console
    ) -> None:
        url, _ = live_console
        page.goto(f"{url}/?token={TOKEN}")
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#start").click()
        expect(page.locator("#session-pill")).to_have_text("live")
        expect(page.locator("#model-pill")).to_be_hidden()
