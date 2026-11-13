"""Speech recognition with faster-whisper.

Fed 20 ms PCM frames, it accumulates a per-lane window and re-decodes it
periodically. Two kinds of output come out, matching the interface: a partial
every ``partial_interval_s`` while an utterance is open, and a final when the
utterance closes.

The window **overlaps rather than tiles**. Fixed independent chunks cut words at
their boundaries, and Whisper-family models are confidently wrong on a chunk
that holds only a fragment or only silence. Re-decoding an overlapping region
means a word split across a boundary is still seen whole on the next pass.

Not benchmarked. The machine this was written on is a two-core ARM container
with no accelerator, where the tiny model runs far slower than real time and the
numbers mean nothing for the target hardware. Model size, compute type and
window length are all configuration, and M0 is what picks them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from core.protocol import FRAME_MS, SAMPLE_RATE, Lane, Language
from core.stt.base import Segment

log = logging.getLogger("susurro.stt")

#: Recognizers are selected from the profile's language. English gets a
#: dedicated fast path if one is configured; see docs/architecture/audio.md.
DEFAULT_MODELS: dict[str, str] = {"en": "small.en", "es": "large-v3-turbo"}


@dataclass
class _LaneBuffer:
    pcm: bytearray = field(default_factory=bytearray)
    frames: int = 0
    frames_since_partial: int = 0
    started_at_s: float = 0.0
    seq: int = 0
    last_partial: str = ""


@dataclass
class WhisperRecognizer:
    """faster-whisper behind the ``Recognizer`` interface."""

    language: Language = "en"
    model_size: str | None = None
    device: str = "auto"
    compute_type: str = "int8"
    #: How often an open utterance is re-decoded for a partial.
    partial_interval_s: float = 1.0
    #: An utterance is closed at the latest after this much audio, even if the
    #: voice activity detector has not found an end. Without a ceiling, someone
    #: who talks without pausing would never produce a final, and nothing would
    #: ever reach the language model.
    max_window_s: float = 12.0
    #: Trailing silence that closes an utterance.
    silence_s: float = 0.6

    _model: object | None = field(default=None, init=False)
    _lanes: dict[str, _LaneBuffer] = field(default_factory=dict, init=False)
    _elapsed: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.reset()

    # -- model -------------------------------------------------------------- #

    @property
    def model_name(self) -> str:
        return self.model_size or DEFAULT_MODELS.get(self.language, "small")

    def model(self):
        """Loaded on first use so that constructing a core is cheap."""
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("loading whisper model %s (%s)", self.model_name, self.compute_type)
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        return self._model

    # -- interface ----------------------------------------------------------- #

    def reset(self) -> None:
        self._lanes = {"mic": _LaneBuffer(), "far": _LaneBuffer()}
        self._elapsed = {"mic": 0.0, "far": 0.0}

    def feed(self, lane: Lane, timestamp_us: int, pcm: bytes) -> list[Segment]:
        del timestamp_us
        buffer = self._lanes[lane]
        if buffer.frames == 0:
            buffer.started_at_s = self._elapsed[lane]
        buffer.pcm.extend(pcm)
        buffer.frames += 1
        buffer.frames_since_partial += 1
        self._elapsed[lane] += FRAME_MS / 1000

        window_s = buffer.frames * FRAME_MS / 1000
        if window_s >= self.max_window_s:
            return self._finalize(lane, reason="window full")
        if buffer.frames_since_partial * FRAME_MS / 1000 >= self.partial_interval_s:
            buffer.frames_since_partial = 0
            return self._partial(lane)
        return []

    def flush(self, lane: Lane) -> list[Segment]:
        if self._lanes[lane].frames == 0:
            return []
        return self._finalize(lane, reason="flush")

    # -- internals ------------------------------------------------------------ #

    def _samples(self, lane: Lane) -> np.ndarray:
        raw = np.frombuffer(bytes(self._lanes[lane].pcm), dtype=np.int16)
        return raw.astype(np.float32) / 32768.0

    def _transcribe(self, lane: Lane) -> tuple[str, bool]:
        """Return the text of the window and whether speech appears to have ended."""
        segments, _info = self.model().transcribe(
            self._samples(lane),
            language=self.language,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        segments = list(segments)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        window_s = len(self._lanes[lane].pcm) / 2 / SAMPLE_RATE
        ended = bool(segments) and (window_s - segments[-1].end) >= self.silence_s
        return text, ended

    def _partial(self, lane: Lane) -> list[Segment]:
        buffer = self._lanes[lane]
        text, ended = self._transcribe(lane)
        if ended and text:
            return self._close(lane, text)
        if not text or text == buffer.last_partial:
            return []
        buffer.last_partial = text
        return [Segment(lane=lane, text=text, t=buffer.started_at_s, final=False)]

    def _finalize(self, lane: Lane, *, reason: str) -> list[Segment]:
        text, _ended = self._transcribe(lane)
        log.debug("closing %s utterance on lane %s: %s", reason, lane, text[:60])
        if not text:
            self._reset_lane(lane)
            return []
        return self._close(lane, text)

    def _close(self, lane: Lane, text: str) -> list[Segment]:
        buffer = self._lanes[lane]
        duration = len(buffer.pcm) / 2 / SAMPLE_RATE
        buffer.seq += 1
        segment = Segment(
            lane=lane,
            text=text,
            t=buffer.started_at_s,
            final=True,
            dur=duration,
            seq=buffer.seq,
        )
        self._reset_lane(lane)
        return [segment]

    def _reset_lane(self, lane: Lane) -> None:
        buffer = self._lanes[lane]
        seq = buffer.seq
        self._lanes[lane] = _LaneBuffer(seq=seq)
