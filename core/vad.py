"""Voice activity detection, per lane, on the core.

Two jobs, and only the second one is new:

1. **Segment boundaries.** Deciding where an utterance ends, so the recognizer
   transcribes speech rather than silence.
2. **End-of-turn events on the `far` lane.** Knowing that the other party has
   stopped talking, promptly, as an event — which is what lets a hint start
   being written before anyone asks for one.

The second is why this exists as a separate component rather than staying
inside the recognizer. faster-whisper's own VAD filter answers the first
question well enough, but only as a side effect of a transcription pass that
runs about once a second. A speculative hint that waits for that has already
spent most of its latency budget.

It runs on the core rather than in the capture client for the same reason
everything else does: the client stays trivial to port, and these models cost
almost nothing next to the ones already resident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

import numpy as np

from core.protocol import SAMPLE_RATE

log = logging.getLogger("susurro.vad")

#: Silero is trained on exactly this window at 16 kHz. The energy detector uses
#: the same size so both produce events on the same grid.
WINDOW_SAMPLES = 512
WINDOW_MS = WINDOW_SAMPLES * 1000 // SAMPLE_RATE
#: Samples of the previous window that Silero expects in front of each new one
#: at 16 kHz.
CONTEXT_SAMPLES = 64


class VadEvent(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@runtime_checkable
class Detector(Protocol):
    """One detector per lane. Detectors carry state and are never shared."""

    def push(self, pcm: bytes) -> list[VadEvent]:
        """Accept PCM and return whatever boundaries it crossed."""
        ...

    def reset(self) -> None: ...

    @property
    def speaking(self) -> bool: ...


@dataclass
class _Hysteresis:
    """Turns a per-window speech probability into start and end events.

    Both directions need hysteresis, for different reasons. Entering speech
    needs a couple of windows of agreement so a door closing does not open an
    utterance. Leaving it needs considerably more, because the natural pauses
    inside a sentence are longer than people think — cutting on the first quiet
    window would chop every utterance into fragments and fire an end-of-turn
    mid-sentence, which is the single most annoying failure this component can
    have.
    """

    #: Consecutive speech windows before an utterance opens. 2 ≈ 64 ms.
    start_windows: int = 2
    #: Consecutive quiet windows before it closes. 24 ≈ 768 ms. 16 (512 ms) split
    #: a question at its comma on a recorded call; 31 and 47 merged nothing more
    #: of what mattered and delayed every hint by up to a second.
    end_windows: int = 24

    speaking: bool = field(default=False, init=False)
    _run: int = field(default=0, init=False)

    def update(self, is_speech: bool) -> VadEvent | None:
        if is_speech == self.speaking:
            self._run = 0
            return None
        self._run += 1
        if not self.speaking and self._run >= self.start_windows:
            self.speaking = True
            self._run = 0
            return VadEvent.SPEECH_START
        if self.speaking and self._run >= self.end_windows:
            self.speaking = False
            self._run = 0
            return VadEvent.SPEECH_END
        return None

    def reset(self) -> None:
        self.speaking = False
        self._run = 0


class _Windowed:
    """Shared plumbing: buffer PCM, hand out fixed windows, apply hysteresis."""

    def __init__(self, hysteresis: _Hysteresis) -> None:
        self._buffer = bytearray()
        self._hysteresis = hysteresis

    @property
    def speaking(self) -> bool:
        return self._hysteresis.speaking

    def push(self, pcm: bytes) -> list[VadEvent]:
        self._buffer.extend(pcm)
        events: list[VadEvent] = []
        width = WINDOW_SAMPLES * 2
        while len(self._buffer) >= width:
            window = bytes(self._buffer[:width])
            del self._buffer[:width]
            samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
            event = self._hysteresis.update(self._is_speech(samples))
            if event is not None:
                events.append(event)
        return events

    def _is_speech(self, samples: np.ndarray) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        self._buffer.clear()
        self._hysteresis.reset()


class EnergyDetector(_Windowed):
    """Root-mean-square threshold.

    Not a speech detector — it cannot tell a voice from a slammed door, and on
    a noisy line it will call the noise speech. It is here because it is
    deterministic, needs no model, and is therefore the thing the tests and the
    fake-backend setup can rely on. :class:`SileroDetector` is the default when
    it is available.
    """

    def __init__(self, threshold: float = 0.01, **kwargs) -> None:
        super().__init__(_Hysteresis(**kwargs))
        self.threshold = threshold

    def _is_speech(self, samples: np.ndarray) -> bool:
        return bool(np.sqrt(np.mean(samples**2)) >= self.threshold)


class SileroDetector(_Windowed):
    """Silero VAD, run directly through onnxruntime.

    The `silero-vad` package declares a hard dependency on torch, which is a
    large thing to install for a 2 MB model. The wheel ships the ONNX file, so
    this loads that file and runs it: ``pip install --no-deps silero-vad`` plus
    ``onnxruntime`` is the whole requirement.

    The model is stateful in two ways. ``state`` is the recurrent state, and the
    last 64 samples of the previous window must be prepended to each new one --
    the model sees 576 samples, not 512. Skipping the context is silent: it
    still runs and still returns a probability, but speech scores near 0.1
    instead of near 1.0, so nothing ever opens an utterance. Silence and noise
    score low either way, which is why a test of only the negative direction
    could not see it.

    Checked on synthesized speech: 166 of 173 windows above the threshold with
    the context, none without. Real speech over a real line is still to be
    heard.
    """

    def __init__(self, threshold: float = 0.5, model_path: str | None = None, **kwargs):
        super().__init__(_Hysteresis(**kwargs))
        self.threshold = threshold
        self._session = _load_silero(model_path)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)

    def _is_speech(self, samples: np.ndarray) -> bool:
        framed = np.concatenate([self._context, samples])
        self._context = samples[-CONTEXT_SAMPLES:]
        probability, self._state = self._session.run(
            None, {"input": framed[None, :], "state": self._state, "sr": self._sr}
        )
        return bool(probability[0][0] >= self.threshold)

    def reset(self) -> None:
        super().reset()
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)


def _load_silero(model_path: str | None):
    import importlib.util
    import os

    import onnxruntime as ort

    if model_path is None:
        spec = importlib.util.find_spec("silero_vad")
        if spec is None or spec.origin is None:
            raise FileNotFoundError(
                "silero-vad is not installed. `pip install --no-deps silero-vad "
                "onnxruntime` — --no-deps because the package declares torch, "
                "which this does not use."
            )
        model_path = os.path.join(
            os.path.dirname(spec.origin), "data", "silero_vad_16k_op15.onnx"
        )
    options = ort.SessionOptions()
    # One thread: this runs next to the models that actually need the cores,
    # and a 2 MB detector has no business competing with them.
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(
        model_path, sess_options=options, providers=["CPUExecutionProvider"]
    )


def build_detector(config: dict) -> Detector | None:
    """Build a detector from configuration. ``None`` means end-of-turn is off.

    With no detector the system still works: hints are requested by hand, which
    is the M2 behaviour. Speculative generation is the thing that is lost.
    """
    backend = config.get("backend", "silero")
    tuning = {
        key: config[key]
        for key in ("start_windows", "end_windows")
        if key in config
    }
    if backend == "off":
        return None
    if backend == "energy":
        return EnergyDetector(threshold=config.get("threshold", 0.01), **tuning)
    if backend == "silero":
        try:
            return SileroDetector(threshold=config.get("threshold", 0.5), **tuning)
        except (FileNotFoundError, ImportError) as exc:
            log.warning("silero unavailable (%s); falling back to energy detection", exc)
            return EnergyDetector(**tuning)
    raise SystemExit(
        f"unknown vad backend {backend!r}: expected 'silero', 'energy' or 'off'"
    )
