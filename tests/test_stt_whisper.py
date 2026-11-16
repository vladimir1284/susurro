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
from core.stt.whisper import DEFAULT_MODELS, WhisperRecognizer

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
    instance = WhisperRecognizer(language="en", **kwargs)
    instance._model = model
    return instance


def feed_seconds(instance: WhisperRecognizer, lane: str, seconds: float) -> list:
    out = []
    for _ in range(int(seconds * 1000 / FRAME_MS)):
        out.extend(instance.feed(lane, 0, SILENCE))
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


class TestTiming:
    def test_an_utterance_carries_its_start_offset(self) -> None:
        model = FakeModel([FakeSegment("later", end=0.2)])
        instance = recognizer(model, partial_interval_s=1.0, silence_s=0.6)
        feed_seconds(instance, "far", 1.0)  # first utterance, 0.0 to 1.0
        second = feed_seconds(instance, "far", 1.0)
        assert second[0].t == pytest.approx(1.0, abs=0.05)

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
