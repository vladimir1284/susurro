"""Session state and profile loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.session.profiles import Profile, ProfileError, ProfileStore
from core.session.state import SessionPhase, SessionState, TransitionError

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


class TestProfileStore:
    def test_lists_committed_fixtures(self) -> None:
        assert ProfileStore(FIXTURES).list_ids() == [
            "en-technical-backend",
            "es-business-industrial",
        ]

    @pytest.mark.parametrize(
        "profile_id, language",
        [("en-technical-backend", "en"), ("es-business-industrial", "es")],
    )
    def test_loads_both_languages(self, profile_id: str, language: str) -> None:
        assert ProfileStore(FIXTURES).load(profile_id).language == language

    def test_missing_profile(self) -> None:
        with pytest.raises(ProfileError, match="no profile"):
            ProfileStore(FIXTURES).load("nope")

    @pytest.mark.parametrize("profile_id", ["../secrets", "a/b", "", ".", ".."])
    def test_rejects_ids_that_could_escape_the_directory(self, profile_id: str) -> None:
        # Profile ids arrive from the console and index the filesystem.
        with pytest.raises(ProfileError, match="invalid profile id"):
            ProfileStore(FIXTURES).load(profile_id)

    def test_rejects_a_mismatched_id(self, tmp_path: Path) -> None:
        (tmp_path / "filed-as.json").write_text(
            json.dumps(
                {
                    "id": "declares-this",
                    "label": "x",
                    "language": "en",
                    "mode": "technical",
                    "context": {"resume": "x"},
                }
            )
        )
        with pytest.raises(ProfileError, match="filed as"):
            ProfileStore(tmp_path).load("filed-as")

    def test_missing_directory_lists_nothing(self, tmp_path: Path) -> None:
        assert ProfileStore(tmp_path / "absent").list_ids() == []


class TestProfileValidation:
    def valid(self, **overrides: object) -> dict:
        raw = {
            "id": "p",
            "label": "A profile",
            "language": "en",
            "mode": "technical",
            "context": {"resume": "some background"},
        }
        raw.update(overrides)
        return raw

    def test_accepts_a_valid_profile(self) -> None:
        assert Profile.from_dict(self.valid()).id == "p"

    @pytest.mark.parametrize("field", ["id", "label", "language", "mode"])
    def test_rejects_missing_fields(self, field: str) -> None:
        raw = self.valid()
        del raw[field]
        with pytest.raises(ProfileError):
            Profile.from_dict(raw)

    def test_rejects_an_unsupported_language(self) -> None:
        with pytest.raises(ProfileError, match="language"):
            Profile.from_dict(self.valid(language="fr"))

    def test_rejects_an_unknown_mode(self) -> None:
        with pytest.raises(ProfileError, match="mode"):
            Profile.from_dict(self.valid(mode="poetic"))

    def test_rejects_an_unknown_context_section(self) -> None:
        with pytest.raises(ProfileError, match="unknown context section"):
            Profile.from_dict(self.valid(context={"resume": "x", "secrets": "y"}))

    def test_rejects_an_empty_context(self) -> None:
        with pytest.raises(ProfileError, match="context is empty"):
            Profile.from_dict(self.valid(context={"resume": "   "}))


class TestPreamble:
    def test_sections_appear_in_a_fixed_order(self) -> None:
        profile = Profile.from_dict(
            {
                "id": "p",
                "label": "l",
                "language": "en",
                "mode": "technical",
                "context": {"notes": "N", "role": "R", "resume": "B"},
            }
        )
        assert profile.preamble() == "## BACKGROUND\nB\n\n## THIS SESSION\nR\n\n## NOTES\nN"

    def test_is_byte_stable_across_calls(self) -> None:
        # The prefix cache is keyed on this text. Instability costs a full
        # prefill without any visible error.
        profile = ProfileStore(FIXTURES).load("en-technical-backend")
        assert profile.preamble() == profile.preamble()

    def test_omits_absent_sections(self) -> None:
        profile = Profile.from_dict(
            {
                "id": "p",
                "label": "l",
                "language": "es",
                "mode": "business",
                "context": {"role": "solo rol"},
            }
        )
        assert profile.preamble() == "## THIS SESSION\nsolo rol"


class TestSessionState:
    def test_starts_idle(self) -> None:
        assert SessionState().phase is SessionPhase.IDLE

    def test_config_then_start_goes_live(self) -> None:
        state = SessionState()
        state.configure("p", "en", "technical")
        assert state.phase is SessionPhase.CONFIGURED
        state.start()
        assert state.phase is SessionPhase.LIVE

    def test_start_without_config_is_refused(self) -> None:
        with pytest.raises(TransitionError, match="must arrive before"):
            SessionState().start()

    def test_config_is_refused_while_live(self) -> None:
        # It decides the prefilled preamble and the loaded recognizer; honouring
        # it mid-session would silently discard both.
        state = SessionState()
        state.configure("p", "en", "technical")
        state.start()
        with pytest.raises(TransitionError, match="not allowed while live"):
            state.configure("other", "es", "business")

    def test_starting_twice_is_refused(self) -> None:
        state = SessionState()
        state.configure("p", "en", "technical")
        state.start()
        with pytest.raises(TransitionError, match="already live"):
            state.start()

    def test_stop_returns_to_configured(self) -> None:
        state = SessionState()
        state.configure("p", "en", "technical")
        state.start()
        state.stop()
        assert state.phase is SessionPhase.CONFIGURED

    def test_stop_when_not_live_is_refused(self) -> None:
        with pytest.raises(TransitionError, match="not live"):
            SessionState().stop()

    def test_audio_is_only_accepted_while_live(self) -> None:
        state = SessionState()
        assert not state.accepts_audio
        state.configure("p", "en", "technical")
        assert not state.accepts_audio
        state.start()
        assert state.accepts_audio

    def test_a_lane_cannot_connect_twice(self) -> None:
        state = SessionState()
        state.lane_up("mic")
        with pytest.raises(TransitionError, match="already connected"):
            state.lane_up("mic")
        state.lane_down("mic")
        state.lane_up("mic")

    def test_hints_require_a_live_session(self) -> None:
        with pytest.raises(TransitionError, match="require a live session"):
            SessionState().begin_hint()

    def test_hint_ids_increment_and_supersede(self) -> None:
        state = SessionState()
        state.configure("p", "en", "technical")
        state.start()
        first = state.begin_hint()
        second = state.begin_hint()
        assert (first, second) == ("h-1", "h-2")
        # The superseded hint can no longer close the session's active hint.
        assert state.end_hint(first) is False
        assert state.end_hint(second) is True


class TestProfileSaving:
    def profile(self, **overrides) -> Profile:
        values = {
            "id": "written",
            "label": "A written profile",
            "language": "en",
            "mode": "technical",
            "context": {"resume": "some background"},
        }
        values.update(overrides)
        return Profile(**values)

    def test_a_saved_profile_loads_back(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        store.save(self.profile())
        assert store.load("written").label == "A written profile"

    def test_saving_creates_the_directory(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "new" / "place")
        store.save(self.profile())
        assert store.list_ids() == ["written"]

    def test_saving_replaces_an_existing_profile(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        store.save(self.profile())
        store.save(self.profile(label="Renamed"))
        assert store.load("written").label == "Renamed"
        assert store.list_ids() == ["written"]

    def test_an_invalid_profile_never_reaches_the_disk(self, tmp_path: Path) -> None:
        # Round-tripped through validation before writing: a file that cannot
        # be loaded back is worse than a refused save.
        store = ProfileStore(tmp_path)
        with pytest.raises(ProfileError):
            store.save(self.profile(context={"resume": "   "}))
        assert store.list_ids() == []

    @pytest.mark.parametrize("profile_id", ["../escape", "a/b", "", ".", ".."])
    def test_an_id_that_could_escape_is_refused(
        self, tmp_path: Path, profile_id: str
    ) -> None:
        store = ProfileStore(tmp_path)
        with pytest.raises(ProfileError, match="invalid profile id"):
            store.save(self.profile(id=profile_id))

    def test_a_failed_write_leaves_no_rubbish(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        with pytest.raises(ProfileError):
            store.save(self.profile(language="fr"))
        assert list(tmp_path.iterdir()) == []

    def test_unicode_survives_the_round_trip(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        store.save(self.profile(context={"resume": "Ingeniería eléctrica — Camagüey"}))
        assert "Camagüey" in store.load("written").context["resume"]

    def test_the_written_file_is_readable_by_a_person(self, tmp_path: Path) -> None:
        # Profiles are meant to be editable by hand as well as by the console.
        store = ProfileStore(tmp_path)
        path = store.save(self.profile())
        text = path.read_text(encoding="utf-8")
        assert "\n" in text and text.endswith("\n")

    def test_an_interrupted_write_does_not_destroy_the_previous_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # The previous profile is the context a session is built from. Losing
        # it to a crash mid-write would be a miserable way to start a call.
        store = ProfileStore(tmp_path)
        store.save(self.profile(label="Original"))

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("core.session.profiles.os.replace", explode)
        with pytest.raises(OSError):
            store.save(self.profile(label="Half written"))
        assert store.load("written").label == "Original"
        assert [p.name for p in tmp_path.iterdir()] == ["written.json"]
