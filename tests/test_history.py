"""Session recording and export.

Recording is the feature most at odds with the rest of the project: everything
else keeps a conversation on machines you own, and this one writes it down.
So the tests lean on the switch being off, on deletion working, and on the
console being told when recording is happening.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.session.history import (
    Hint,
    HistoryError,
    HistoryStore,
    Line,
    Recording,
    now,
    render_markdown,
)


def recording(**overrides) -> Recording:
    values = {
        "session_id": "2026-11-13T10-00-00Z",
        "profile_id": "en-technical-backend",
        "language": "en",
        "mode": "technical",
        "started_at": now(),
    }
    values.update(overrides)
    return Recording(**values)


class TestDisabledByDefault:
    def test_a_store_does_not_record_unless_asked(self, tmp_path: Path) -> None:
        # The project's whole argument is that a conversation stays on machines
        # you own. Recording every meeting unasked would undercut it.
        store = HistoryStore(tmp_path)
        assert store.enabled is False
        with pytest.raises(HistoryError, match="disabled"):
            store.save(recording())
        assert list(tmp_path.iterdir()) == []

    def test_listing_an_empty_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert HistoryStore(tmp_path / "absent", enabled=True).list_sessions() == []


class TestStorage:
    @pytest.fixture
    def store(self, tmp_path: Path) -> HistoryStore:
        return HistoryStore(tmp_path, enabled=True)

    def test_a_session_round_trips(self, store: HistoryStore) -> None:
        original = recording()
        original.lines.append(Line(lane="far", text="Why?", t=1.0, dur=1.0, seq=1))
        store.save(original)
        assert store.load(original.session_id).lines[0].text == "Why?"

    def test_sessions_are_listed_newest_first(self, store: HistoryStore) -> None:
        for started in ("2026-11-01T09:00:00+00:00", "2026-11-12T09:00:00+00:00"):
            store.save(
                recording(session_id=started.replace(":", "-"), started_at=started)
            )
        listed = [entry["started_at"] for entry in store.list_sessions()]
        assert listed == sorted(listed, reverse=True)

    def test_a_summary_carries_what_a_list_shows(self, store: HistoryStore) -> None:
        subject = recording()
        subject.lines.append(Line(lane="mic", text="x", t=0.0, dur=1.0, seq=1))
        store.save(subject)
        entry = store.list_sessions()[0]
        assert entry["lines"] == 1 and entry["hints"] == 0

    def test_one_unreadable_file_does_not_hide_the_rest(
        self, store: HistoryStore, tmp_path: Path
    ) -> None:
        store.save(recording())
        (tmp_path / "broken.json").write_text("{not json")
        assert len(store.list_sessions()) == 1

    def test_deleting_is_as_easy_as_recording(self, store: HistoryStore) -> None:
        store.save(recording())
        store.delete("2026-11-13T10-00-00Z")
        assert store.list_sessions() == []

    def test_deleting_something_absent_says_so(self, store: HistoryStore) -> None:
        with pytest.raises(HistoryError, match="no session"):
            store.delete("never-existed")

    @pytest.mark.parametrize("session_id", ["../escape", "a/b", "", ".", ".."])
    def test_ids_cannot_address_anything_outside_the_directory(
        self, store: HistoryStore, session_id: str
    ) -> None:
        with pytest.raises(HistoryError, match="invalid session id"):
            store.load(session_id)
        with pytest.raises(HistoryError, match="invalid session id"):
            store.delete(session_id)

    def test_an_interrupted_write_keeps_the_previous_recording(
        self, store: HistoryStore, tmp_path: Path, monkeypatch
    ) -> None:
        first = recording()
        first.lines.append(Line(lane="far", text="original", t=0.0, dur=1.0, seq=1))
        store.save(first)

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("core.session.history.os.replace", explode)
        second = recording()
        second.lines.append(Line(lane="far", text="replacement", t=0.0, dur=1.0, seq=1))
        with pytest.raises(OSError):
            store.save(second)
        assert store.load(first.session_id).lines[0].text == "original"
        assert [path.name for path in tmp_path.iterdir()] == [
            f"{first.session_id}.json"
        ]

    def test_unicode_survives(self, store: HistoryStore) -> None:
        subject = recording(language="es")
        subject.lines.append(
            Line(lane="far", text="¿Por qué dejaste Camagüey?", t=0.0, dur=1.0, seq=1)
        )
        store.save(subject)
        assert "Camagüey" in store.load(subject.session_id).lines[0].text

    def test_the_file_is_readable_json(self, store: HistoryStore, tmp_path: Path) -> None:
        store.save(recording())
        written = json.loads((tmp_path / "2026-11-13T10-00-00Z.json").read_text())
        assert written["schema"] == 1
        assert written["profile_id"] == "en-technical-backend"

    def test_a_corrupt_recording_is_refused_with_its_source(
        self, store: HistoryStore, tmp_path: Path
    ) -> None:
        (tmp_path / "half.json").write_text('{"session_id": "half"}')
        with pytest.raises(HistoryError, match="not a readable recording"):
            store.load("half")


class TestMarkdown:
    def test_an_empty_session_says_so(self) -> None:
        assert "Nothing was said" in render_markdown(recording())

    def test_speakers_are_named_not_laned(self) -> None:
        # "far" and "mic" are wire details. A transcript is read by a person.
        subject = recording()
        subject.lines.append(Line(lane="far", text="Their words", t=1.0, dur=1, seq=1))
        subject.lines.append(Line(lane="mic", text="My words", t=2.0, dur=1, seq=1))
        rendered = render_markdown(subject)
        assert "**Them:** Their words" in rendered
        assert "**You:** My words" in rendered

    def test_a_hint_follows_the_question_it_answered(self) -> None:
        # Read afterwards, what matters is which question a suggestion was for.
        subject = recording()
        subject.lines.append(Line(lane="far", text="First?", t=1.0, dur=1, seq=1))
        subject.lines.append(Line(lane="far", text="Second?", t=5.0, dur=1, seq=2))
        subject.hints.append(
            Hint(
                hint_id="h-1",
                trigger="speculative",
                mode="technical",
                text="- about the first",
                reason="complete",
                at=1.0,
            )
        )
        rendered = render_markdown(subject)
        assert rendered.index("about the first") < rendered.index("Second?")

    def test_metrics_appear_when_they_were_recorded(self) -> None:
        subject = recording()
        subject.hints.append(
            Hint(
                hint_id="h-1",
                trigger="manual",
                mode="technical",
                text="x",
                reason="complete",
                ttft_ms=740,
            )
        )
        assert "740 ms to first token" in render_markdown(subject)

    def test_a_multi_line_hint_stays_quoted(self) -> None:
        subject = recording()
        subject.hints.append(
            Hint(
                hint_id="h-1",
                trigger="manual",
                mode="conversational",
                text="- one\n- two\n",
                reason="complete",
            )
        )
        rendered = render_markdown(subject)
        assert "> - one" in rendered and "> - two" in rendered

    def test_an_unfinished_session_says_so_rather_than_lying(self) -> None:
        subject = recording(ended_at=None)
        subject.lines.append(Line(lane="far", text="x", t=0.0, dur=1, seq=1))
        assert "not recorded" in render_markdown(subject)
