"""Windowing logic of the faster-whisper recognizer.

The model itself is stubbed. What matters here is the behaviour around it:
when a partial is emitted, when an utterance is closed, that an open utterance
on one lane is unaffected by the other, and that a window which never falls
silent still produces a final eventually.

Running the real model is a separate, opt-in test at the bottom. It is skipped
by default because this machine is a two-core ARM container with no
accelerator, where even the tiny model runs far slower than real time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from core.protocol import FRAME_MS
from core.stt.whisper import DEFAULT_MODELS, WhisperRecognizer, plausible

FRAME_BYTES = 640
SILENCE = bytes(FRAME_BYTES)


@dataclass
class FakeSegment:
    text: str
    end: float


class FakeModel:
    """Returns scripted segments, and records how often it was asked."""

    def __init__(self, segments: list[FakeSegment] | None = None):
        self.segments = segments if segments is not None else []
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        return list(self.segments), {}


def recognizer(model: FakeModel, **kwargs) -> WhisperRecognizer:
    # A still clock: decodes here are instant, and against the real one the gap
    # between two partials in a tight loop is as small as the cost itself.
    kwargs.setdefault("clock", lambda: 0.0)
    instance = WhisperRecognizer(language="en", **kwargs)
    instance._model = model
    return instance


def feed_seconds(instance: WhisperRecognizer, lane: str, seconds: float) -> list:
    """Feed like the server does: each frame stamped by the lane's frame clock."""
    clock = vars(instance).setdefault("_test_clock", {})
    out = []
    for _ in range(int(seconds * 1000 / FRAME_MS)):
        index = clock.get(lane, 0)
        clock[lane] = index + 1
        out.extend(instance.feed(lane, index * FRAME_MS * 1000, SILENCE))
    return out


class TestModelSelection:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_each_language_has_a_default_model(self, language: str) -> None:
        assert WhisperRecognizer(language=language).model_name == DEFAULT_MODELS[language]

    def test_the_model_can_be_overridden(self) -> None:
        assert WhisperRecognizer(model_size="tiny").model_name == "tiny"

    def test_selecting_a_language_changes_the_default_model(self) -> None:
        instance = WhisperRecognizer(language="en")
        instance._model = object()
        instance.select_language("es")
        assert instance.language == "es"
        assert instance.model_name == DEFAULT_MODELS["es"]
        # The loaded model was the English one; keeping it would transcribe
        # Spanish with the wrong weights.
        assert instance._model is None

    def test_selecting_the_same_language_keeps_the_loaded_model(self) -> None:
        instance = WhisperRecognizer(language="es")
        loaded = object()
        instance._model = loaded
        instance.select_language("es")
        assert instance._model is loaded

    def test_an_explicit_model_survives_a_language_change(self) -> None:
        instance = WhisperRecognizer(language="en", model_size="tiny")
        loaded = object()
        instance._model = loaded
        instance.select_language("es")
        assert instance._model is loaded

    def test_constructing_does_not_load_a_model(self) -> None:
        # Building a core must stay cheap; the weights load on first use.
        assert WhisperRecognizer(model_size="tiny")._model is None


class TestPartials:
    def test_a_partial_is_emitted_at_the_configured_interval(self) -> None:
        model = FakeModel([FakeSegment("so tell me", end=1.0)])
        instance = recognizer(model, partial_interval_s=1.0)
        segments = feed_seconds(instance, "far", 1.0)
        assert len(segments) == 1
        assert segments[0].final is False
        assert segments[0].text == "so tell me"

    def test_nothing_is_emitted_before_the_interval(self) -> None:
        instance = recognizer(FakeModel([FakeSegment("x", end=0.5)]),
                              partial_interval_s=1.0)
        assert feed_seconds(instance, "far", 0.5) == []

    def test_an_unchanged_partial_is_not_repeated(self) -> None:
        # Re-sending identical text would make the console flicker for no
        # information.
        model = FakeModel([FakeSegment("same text", end=0.2)])
        instance = recognizer(model, partial_interval_s=0.2, silence_s=99.0)
        segments = feed_seconds(instance, "far", 1.0)
        assert len(segments) == 1

    def test_an_empty_transcription_emits_nothing(self) -> None:
        instance = recognizer(FakeModel([]), partial_interval_s=0.2)
        assert feed_seconds(instance, "far", 1.0) == []


class TestFinals:
    def test_trailing_silence_closes_the_utterance(self) -> None:
        # Speech ends at 0.2 s, the window reaches 1.0 s: 0.8 s of silence is
        # past the threshold, so the utterance is done.
        model = FakeModel([FakeSegment("all done", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        segments = feed_seconds(instance, "far", 1.0)
        assert len(segments) == 1
        assert segments[0].final is True
        assert segments[0].seq == 1

    def test_a_window_that_never_falls_silent_is_closed_anyway(self) -> None:
        # Someone who talks without pausing must still produce finals, or
        # nothing ever reaches the language model.
        model = FakeModel([FakeSegment("still going", end=3.0)])
        instance = recognizer(
            model, partial_interval_s=10.0, max_window_s=3.0, silence_s=0.6
        )
        segments = feed_seconds(instance, "far", 3.0)
        assert [s.final for s in segments] == [True]

    def test_sequence_numbers_increment_per_lane(self) -> None:
        model = FakeModel([FakeSegment("one", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        first = feed_seconds(instance, "far", 1.0)
        model.segments = [FakeSegment("two", end=0.2)]
        second = feed_seconds(instance, "far", 1.0)
        assert [first[0].seq, second[0].seq] == [1, 2]

    def test_lanes_are_independent(self) -> None:
        model = FakeModel([FakeSegment("far speech", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        far = feed_seconds(instance, "far", 1.0)
        mic = feed_seconds(instance, "mic", 1.0)
        assert far[0].lane == "far"
        assert mic[0].lane == "mic"
        assert mic[0].seq == 1  # its own counter, not a shared one

    def test_flush_closes_an_open_utterance(self) -> None:
        model = FakeModel([FakeSegment("cut off", end=0.4)])
        instance = recognizer(model, partial_interval_s=10.0)
        feed_seconds(instance, "far", 0.4)
        segments = instance.flush("far")
        assert len(segments) == 1 and segments[0].final

    def test_flush_on_an_idle_lane_does_nothing(self) -> None:
        instance = recognizer(FakeModel([FakeSegment("x", end=0.1)]))
        assert instance.flush("mic") == []

    def test_reset_clears_every_lane(self) -> None:
        model = FakeModel([FakeSegment("x", end=0.1)])
        instance = recognizer(model, partial_interval_s=10.0)
        feed_seconds(instance, "far", 0.4)
        instance.reset()
        assert instance.flush("far") == []


class TestVocabulary:
    def test_the_session_vocabulary_becomes_the_initial_prompt(self) -> None:
        seen: list[dict] = []

        class Recording(FakeModel):
            def transcribe(self, audio, **kwargs):
                seen.append(kwargs)
                return super().transcribe(audio, **kwargs)

        instance = recognizer(Recording([FakeSegment("x", end=0.2)]))
        instance.select_vocabulary(["Modbus", "TimescaleDB"])
        feed_seconds(instance, "far", 1.0)
        assert seen[-1]["initial_prompt"] == "Modbus, TimescaleDB."

    def test_without_a_vocabulary_no_prompt_is_passed(self) -> None:
        seen: list[dict] = []

        class Recording(FakeModel):
            def transcribe(self, audio, **kwargs):
                seen.append(kwargs)
                return super().transcribe(audio, **kwargs)

        instance = recognizer(Recording([FakeSegment("x", end=0.2)]))
        feed_seconds(instance, "far", 1.0)
        assert seen[-1]["initial_prompt"] is None


class TestFallingBehindRealTime:
    """The interval counts audio; a slow decode does not stop audio arriving.

    Without a limit the partials pile up behind a slow server, one decode after
    another, and recognition ends up a minute or more behind: a recorded call
    finished a hundred seconds late. Partials are a courtesy to the screen; the
    final is what a hint is written from. So under pressure the partials go.
    """

    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    class SlowModel(FakeModel):
        def __init__(self, clock, cost_s: float, segments=None):
            super().__init__(segments)
            self.clock, self.cost_s = clock, cost_s

        def transcribe(self, audio, **kwargs):
            self.clock.now += self.cost_s  # the decode takes wall time
            return super().transcribe(audio, **kwargs)

    def make(self, cost_s: float):
        clock = self.Clock()
        model = self.SlowModel(clock, cost_s, [FakeSegment("so tell me", end=99.0)])
        return recognizer(model, clock=clock, partial_interval_s=1.0), model, clock

    @staticmethod
    def feed(instance, clock, lane: str, seconds: float, *, realtime: bool) -> None:
        """Audio arriving. Real time moves the wall clock with it; a burst, as
        after a stall, delivers it all at once."""
        stamps = vars(instance).setdefault("_test_clock", {})
        for _ in range(int(seconds * 1000 / FRAME_MS)):
            index = stamps.get(lane, 0)
            stamps[lane] = index + 1
            if realtime:
                clock.now += FRAME_MS / 1000
            instance.feed(lane, index * FRAME_MS * 1000, SILENCE)

    def test_a_slow_decode_makes_the_lane_stop_asking_for_partials(self) -> None:
        instance, model, clock = self.make(cost_s=2.0)
        self.feed(instance, clock, "far", 1.0, realtime=True)
        assert model.calls == 1  # that decode left the lane 2 s behind
        self.feed(instance, clock, "far", 5.0, realtime=True)
        assert model.calls == 1  # unlimited this would be six

    def test_it_resumes_once_the_lane_has_caught_up(self) -> None:
        instance, model, clock = self.make(cost_s=2.0)
        self.feed(instance, clock, "far", 1.0, realtime=True)
        self.feed(instance, clock, "far", 2.0, realtime=True)
        assert model.calls == 1
        # The stalled audio arrives in a burst: no wall time passes, the lag
        # closes, and partials come back.
        self.feed(instance, clock, "far", 3.0, realtime=False)
        assert model.calls >= 2

    def test_a_model_that_keeps_up_is_not_slowed(self) -> None:
        instance, model, clock = self.make(cost_s=0.0)
        self.feed(instance, clock, "far", 5.0, realtime=True)
        assert model.calls == 5  # one per second of audio, as before

    def test_a_final_is_never_withheld_while_partials_are_shed(self) -> None:
        instance, model, clock = self.make(cost_s=2.0)
        self.feed(instance, clock, "far", 3.0, realtime=True)
        assert model.calls == 1
        assert [s.final for s in instance.flush("far")] == [True]

    def test_the_lag_is_measured_per_lane(self) -> None:
        instance, model, clock = self.make(cost_s=2.0)
        self.feed(instance, clock, "far", 1.0, realtime=True)  # far falls behind
        self.feed(instance, clock, "mic", 1.0, realtime=True)  # mic starts now
        assert model.calls == 2  # mic's first partial is not held for far's lag


class TestTiming:
    def test_an_utterance_carries_its_start_offset(self) -> None:
        model = FakeModel([FakeSegment("later", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        feed_seconds(instance, "far", 1.0)  # first utterance, 0.0 to 1.0
        second = feed_seconds(instance, "far", 1.0)
        assert second[0].t == pytest.approx(1.0, abs=0.05)

    def test_time_the_caller_withheld_still_counts(self) -> None:
        # The server does not feed silence. An utterance that starts after ten
        # seconds of it must be stamped ten seconds in, not zero.
        model = FakeModel([FakeSegment("hola", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        first = feed_seconds(instance, "far", 1.0)
        assert first[0].t == pytest.approx(0.0, abs=0.05)
        for index in range(500, 550):  # 10 s later, by the caller's clock
            segments = instance.feed("far", index * FRAME_MS * 1000, SILENCE)
            if segments:
                assert segments[0].t == pytest.approx(10.0, abs=0.05)
                return
        raise AssertionError("no segment emitted")

    def test_duration_matches_the_window(self) -> None:
        model = FakeModel([FakeSegment("x", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        segments = feed_seconds(instance, "far", 1.0)
        assert segments[0].dur == pytest.approx(1.0, abs=0.05)


@pytest.mark.skipif(
    os.environ.get("SUSURRO_SLOW_TESTS") != "1",
    reason=(
        "loads a real model; skipped unless SUSURRO_SLOW_TESTS=1. This machine "
        "is a two-core ARM container with no accelerator and the result says "
        "nothing about the target hardware."
    ),
)
def test_the_real_model_loads_and_accepts_audio() -> None:
    """Smoke test only: that the wiring works, not that it is fast or accurate.

    No assertion is made about the text. Deciding whether a transcription is
    correct needs real speech audio, which this repository does not ship, and
    deciding whether it is fast enough is M0's job on the target machine.
    """
    instance = WhisperRecognizer(language="en", model_size="tiny", compute_type="int8")
    feed_seconds(instance, "far", 1.0)
    segments = instance.flush("far")
    assert isinstance(segments, list)


class TestImplausibleTranscriptions:
    """A model in a loop is confident and fluent and wrong."""

    LOOP = (
        "El primer objetivo es que el sistema de control de las redes sociales "
        "y el sistema de control de las redes sociales."
    )

    def test_ordinary_speech_passes(self) -> None:
        assert plausible("Tenemos una línea de envasado con controladores de dos fabricantes", 5.0)

    def test_a_rate_nobody_can_speak_at_is_rejected(self) -> None:
        text = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce"
        assert not plausible(text, 1.0)  # twelve words in a second

    def test_a_few_words_in_a_short_clip_do_not_trip_the_rate(self) -> None:
        assert plausible("no se cae el enlace", 0.4)

    def test_a_long_run_repeated_in_one_utterance_is_a_loop(self) -> None:
        assert not plausible(self.LOOP, 30.0)  # plausible rate, still a loop

    def test_a_short_phrase_said_twice_is_not(self) -> None:
        assert plausible("sí, sí, claro que sí, claro que sí", 4.0)

    def test_the_recorded_loop_is_discarded_by_the_recognizer(self) -> None:
        model = FakeModel([FakeSegment(self.LOOP, end=2.0)])
        instance = recognizer(model, partial_interval_s=1.0)
        assert feed_seconds(instance, "mic", 2.2) == []
