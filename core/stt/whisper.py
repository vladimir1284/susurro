"""Speech recognition with faster-whisper.

Fed 20 ms PCM frames, it accumulates a per-lane window and re-decodes it
periodically. Two kinds of output come out, matching the interface: a partial
every ``partial_interval_s`` while an utterance is open, and a final when the
utterance closes.

Each partial re-decodes the **whole open window**, so a word at the edge is seen
whole on the next pass; the cost of a partial grows with the utterance, up to
``max_window_s``, which is the ceiling on it. When an utterance runs past that
ceiling without a pause, it is closed at the last phrase boundary the model
found and the audio after it is carried into the next window, so a long speech
is not cut through a word. Only if the whole window is one unbroken phrase is it
cut where it stands.

Not benchmarked. The machine this was written on is a two-core ARM container
with no accelerator, where the tiny model runs far slower than real time and the
numbers mean nothing for the target hardware. Model size, compute type and
window length are all configuration, and M0 is what picks them.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from core.protocol import FRAME_MS, SAMPLE_RATE, Lane, Language
from core.session.vocabulary import as_prompt
from core.stt.base import Segment

log = logging.getLogger("susurro.stt")

#: Recognizers are selected from the profile's language. English gets a
#: dedicated fast path if one is configured; see docs/architecture/audio.md.
DEFAULT_MODELS: dict[str, str] = {"en": "small.en", "es": "large-v3-turbo"}


#: Speech does not exceed this many words per second in any language the project
#: targets; a transcription that does is a model looping, not a person talking.
MAX_WORDS_PER_S = 7.0
#: A rate means little for a handful of words: a short clip with a long word in
#: it can look fast for a moment.
MIN_WORDS_FOR_RATE = 8
#: A run of this many words that appears twice in one utterance is a loop.
LOOP_WORDS = 6

_WORD = re.compile(r"[\w']+", re.UNICODE)


def plausible(text: str, seconds: float) -> bool:
    """Whether a transcription could have been spoken in that much audio.

    Whisper-family models fall into loops on some audio, and the result is
    confident, fluent and wrong: on a recorded call, twenty-two words out of
    two seconds, a sentence about "redes sociales" said twice, in a meeting about
    industrial telemetry. Fed to the language model as something the user said,
    it would steer every hint after it. Two language-independent checks catch
    the loops: a speech rate nobody can reach, and a long run of words repeated
    within the same utterance.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    if seconds > 0 and len(words) >= MIN_WORDS_FOR_RATE and len(words) / seconds > MAX_WORDS_PER_S:
        return False
    seen: set[tuple[str, ...]] = set()
    for start in range(len(words) - LOOP_WORDS + 1):
        run = tuple(words[start : start + LOOP_WORDS])
        if run in seen:
            return False
        seen.add(run)
    return True


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
    #: Past this much lag behind real time, a lane stops asking for partials
    #: until it has caught up; finals are never withheld. The interval above
    #: counts audio, which keeps arriving while a slow decode runs, so on a
    #: loaded or distant model server the partials pile up back to back and
    #: recognition falls further behind with every second -- a recorded call
    #: ended a hundred seconds late. A partial is a courtesy to the screen;
    #: the final is what the hint is written from, so under pressure it is the
    #: partials that go.
    partial_max_lag_s: float = 1.5
    #: Words read before decoding, to bias it toward the vocabulary of the
    #: session. Set from the profile at session start.
    prompt: str = ""
    #: Wall clock, injectable so a test can hold time still.
    clock: Callable[[], float] = time.monotonic

    _model: object | None = field(default=None, init=False)
    _lanes: dict[str, _LaneBuffer] = field(default_factory=dict, init=False)
    #: Per lane: wall time and caller clock at the first frame, so that audio
    #: processed can be compared with time elapsed. And whether the lane is
    #: currently shedding partials; it resumes only once well caught up, so it
    #: does not flap on the threshold.
    _origin: dict[str, tuple[float, int]] = field(default_factory=dict, init=False)
    _shedding: dict[str, bool] = field(default_factory=dict, init=False)
    #: Per lane, the phrases of the last decode as (start s, end s, text), for
    #: cutting a window that ran out of room at a phrase boundary.
    _spans: dict[str, list[tuple[float | None, float, str]]] = field(
        default_factory=dict, init=False
    )

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

    def select_language(self, language: Language) -> None:
        """Switch language, dropping a loaded model that no longer fits.

        The default model depends on the language, so a model loaded for one is
        wrong for the other. An explicit ``model_size`` is the same either way.
        """
        previous = self.model_name
        self.language = language
        if self.model_name != previous:
            self._model = None

    def select_vocabulary(self, terms: list[str]) -> None:
        self.prompt = as_prompt(terms)

    def warmup(self) -> None:
        """Load the weights now rather than on the first frame.

        A first run downloads the model, which takes minutes. Doing that while
        audio is already arriving stalls everything and looks like a hang.
        """
        self.model()

    def reset(self) -> None:
        self._lanes = {"mic": _LaneBuffer(), "far": _LaneBuffer()}
        self._origin = {}
        self._shedding = {}
        self._spans = {}

    def feed(self, lane: Lane, timestamp_us: int, pcm: bytes) -> list[Segment]:
        buffer = self._lanes[lane]
        self._origin.setdefault(lane, (self.clock(), timestamp_us))
        if buffer.frames == 0:
            # The caller's clock, not a count of what was fed: the caller may
            # withhold silence, and segment times must keep counting through it.
            buffer.started_at_s = timestamp_us / 1_000_000
        buffer.pcm.extend(pcm)
        buffer.frames += 1
        buffer.frames_since_partial += 1

        window_s = buffer.frames * FRAME_MS / 1000
        if window_s >= self.max_window_s:
            return self._finalize(lane, reason="window full")
        if (
            buffer.frames_since_partial * FRAME_MS / 1000 >= self.partial_interval_s
            and not self._is_shedding(lane, timestamp_us)
        ):
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
            initial_prompt=self.prompt or None,
        )
        segments = list(segments)
        self._spans[lane] = [
            (getattr(segment, "start", None), segment.end, segment.text.strip())
            for segment in segments
        ]
        text = " ".join(segment.text.strip() for segment in segments).strip()
        window_s = len(self._lanes[lane].pcm) / 2 / SAMPLE_RATE
        ended = bool(segments) and (window_s - segments[-1].end) >= self.silence_s
        return text, ended

    def lag_s(self, lane: Lane, timestamp_us: int) -> float:
        """How far behind real time this lane's recognition is, in seconds."""
        wall0, ts0 = self._origin[lane]
        return (self.clock() - wall0) - (timestamp_us - ts0) / 1_000_000

    def _is_shedding(self, lane: Lane, timestamp_us: int) -> bool:
        lag = self.lag_s(lane, timestamp_us)
        shedding = self._shedding.get(lane, False)
        if shedding and lag < self.partial_max_lag_s / 3:
            shedding = False
        elif not shedding and lag > self.partial_max_lag_s:
            shedding = True
            log.warning(
                "lane %s is %.1fs behind real time: dropping partials until it catches up",
                lane, lag,
            )
        self._shedding[lane] = shedding
        return shedding

    def _decode(self, lane: Lane) -> tuple[str, bool]:
        """Transcribe the lane's window, discarding what could not have been said."""
        text, ended = self._transcribe(lane)
        seconds = len(self._lanes[lane].pcm) / 2 / SAMPLE_RATE
        if text and not plausible(text, seconds):
            log.warning(
                "lane %s: discarded an implausible transcription (%d words in %.1fs): %s",
                lane, len(text.split()), seconds, text[:60],
            )
            return "", ended
        return text, ended

    def _partial(self, lane: Lane) -> list[Segment]:
        buffer = self._lanes[lane]
        text, ended = self._decode(lane)
        if ended and text:
            return self._close(lane, text)
        if not text or text == buffer.last_partial:
            return []
        buffer.last_partial = text
        return [Segment(lane=lane, text=text, t=buffer.started_at_s, final=False)]

    def _finalize(self, lane: Lane, *, reason: str) -> list[Segment]:
        text, _ended = self._decode(lane)
        log.debug("closing %s utterance on lane %s: %s", reason, lane, text[:60])
        if not text:
            self._reset_lane(lane)
            return []
        if reason == "window full":
            cut = self._phrase_cut(lane, text)
            if cut is not None:
                return self._close_at(lane, *cut)
        return self._close(lane, text)

    def _phrase_cut(self, lane: Lane, text: str) -> tuple[str, float] | None:
        """Where a full window can be closed without cutting a phrase, if anywhere.

        The last phrase the model found is the one most likely to be unfinished
        (a word, or a clause, still being said at the edge), so everything
        before it is closed and it stays in the window to be decoded again with
        what follows. ``None`` when there is no earlier phrase to close, or the
        backend gave no start times.
        """
        spans = self._spans.get(lane, [])
        if len(spans) < 2:
            return None
        start = spans[-1][0]
        if start is None or start <= 0:
            return None
        head = " ".join(t for _s, _e, t in spans[:-1]).strip()
        if not head or head == text or not plausible(head, start):
            return None
        return head, start

    def _close_at(self, lane: Lane, text: str, cut_s: float) -> list[Segment]:
        """Close the window up to ``cut_s`` and keep the rest for the next one."""
        buffer = self._lanes[lane]
        frame_bytes = FRAME_MS * SAMPLE_RATE // 1000 * 2
        cut_frames = max(1, int(cut_s * 1000 / FRAME_MS))
        cut_bytes = min(cut_frames * frame_bytes, len(buffer.pcm))
        carried = bytes(buffer.pcm[cut_bytes:])
        buffer.seq += 1
        segment = Segment(
            lane=lane,
            text=text,
            t=buffer.started_at_s,
            final=True,
            dur=cut_bytes / 2 / SAMPLE_RATE,
            seq=buffer.seq,
        )
        self._lanes[lane] = _LaneBuffer(
            pcm=bytearray(carried),
            frames=len(carried) // frame_bytes,
            started_at_s=buffer.started_at_s + cut_bytes / 2 / SAMPLE_RATE,
            seq=buffer.seq,
        )
        return [segment]

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
