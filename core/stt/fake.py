"""A recognizer that reads from a script instead of from audio.

Used by the integration tests, and by anyone working on the console or the
protocol who should not need model weights to run the thing.

It consumes frames and ignores their contents, emitting the next scripted
utterance once it has seen enough of them. That is enough to exercise
everything the server does with transcripts: partial-then-final ordering,
sequence numbering per lane, and the end-of-turn signal on the far lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.protocol import FRAME_MS, Lane, Language
from core.stt.base import Segment


@dataclass(frozen=True, slots=True)
class ScriptedUtterance:
    lane: Lane
    text: str
    #: How many frames of audio this utterance "takes" before it is final.
    frames: int = 25


@dataclass
class ScriptedRecognizer:
    """Emits a fixed sequence of utterances, paced by incoming frames."""

    script: list[ScriptedUtterance]
    language: Language = "en"
    #: Frames between partials while an utterance is open.
    partial_every: int = 10

    _position: dict[str, int] = field(default_factory=dict, init=False)
    _frames_seen: dict[str, int] = field(default_factory=dict, init=False)
    _seq: dict[str, int] = field(default_factory=dict, init=False)
    _elapsed_us: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._position = {"mic": 0, "far": 0}
        self._frames_seen = {"mic": 0, "far": 0}
        self._seq = {"mic": 0, "far": 0}
        self._elapsed_us = {"mic": 0, "far": 0}

    def _pending(self, lane: Lane) -> ScriptedUtterance | None:
        index = self._position[lane]
        for utterance in self.script[index:]:
            if utterance.lane == lane:
                return utterance
            index += 1
        return None

    def feed(self, lane: Lane, timestamp_us: int, pcm: bytes) -> list[Segment]:
        del timestamp_us, pcm  # the script does not care what was said
        utterance = self._pending(lane)
        if utterance is None:
            return []

        self._frames_seen[lane] += 1
        seen = self._frames_seen[lane]
        start_s = self._elapsed_us[lane] / 1_000_000

        if seen >= utterance.frames:
            self._advance(lane)
            self._seq[lane] += 1
            self._elapsed_us[lane] += utterance.frames * FRAME_MS * 1000
            self._frames_seen[lane] = 0
            return [
                Segment(
                    lane=lane,
                    text=utterance.text,
                    t=start_s,
                    final=True,
                    dur=utterance.frames * FRAME_MS / 1000,
                    seq=self._seq[lane],
                )
            ]

        if seen % self.partial_every == 0:
            words = utterance.text.split()
            shown = max(1, round(len(words) * seen / utterance.frames))
            return [
                Segment(
                    lane=lane,
                    text=" ".join(words[:shown]),
                    t=start_s,
                    final=False,
                )
            ]
        return []

    def _advance(self, lane: Lane) -> None:
        index = self._position[lane]
        while index < len(self.script) and self.script[index].lane != lane:
            index += 1
        self._position[lane] = index + 1

    def flush(self, lane: Lane) -> list[Segment]:
        if self._frames_seen[lane] == 0:
            return []
        utterance = self._pending(lane)
        self._frames_seen[lane] = 0
        if utterance is None:
            return []
        self._advance(lane)
        self._seq[lane] += 1
        return [
            Segment(
                lane=lane,
                text=utterance.text,
                t=self._elapsed_us[lane] / 1_000_000,
                final=True,
                dur=0.0,
                seq=self._seq[lane],
            )
        ]
