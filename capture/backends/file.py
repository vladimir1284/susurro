"""Replay a WAV file as if it were a live microphone.

This exists for three reasons, and only one of them is testing:

* The integration tests need a deterministic audio source, and a file is the
  only kind that produces the same bytes every run.
* Development machines often have no audio devices at all — a container, a
  remote box, CI.
* Reproducing a transcription bug needs the same audio twice, which a live
  microphone cannot give you.

Pacing is real time by default: a 20 ms frame is yielded every 20 ms, so the
rest of the system sees the same arrival pattern it would see from a device.
``speed=0`` disables pacing and yields as fast as the caller consumes, which is
what the tests want.
"""

from __future__ import annotations

import time
import wave
from collections.abc import Iterator
from pathlib import Path

from capture.backends.base import SourceError
from capture.codec import BYTES_PER_SAMPLE, FRAME_BYTES, FRAME_SAMPLES
from core.protocol import FRAME_MS, SAMPLE_RATE


class WavSource:
    """A 16 kHz mono 16-bit WAV file, yielded in 20 ms frames.

    Stereo is downmixed, because a stereo recording of a meeting is common and
    the fix is trivial. Other sample rates are refused rather than resampled:
    a resampler in the capture path is a quality decision that belongs in the
    operating system's audio server, not here.
    """

    def __init__(self, path: Path | str, *, speed: float = 1.0, loop: bool = False):
        self.path = Path(path)
        self.speed = speed
        self.loop = loop
        self._closed = False
        try:
            # Held open for the life of the source rather than wrapped in a
            # context manager: a source is read incrementally and closed by
            # whoever owns it, which is what `close()` is for.
            self._wav = wave.open(str(self.path), "rb")  # noqa: SIM115
        except FileNotFoundError as exc:
            raise SourceError(f"no such file: {self.path}") from exc
        except wave.Error as exc:
            raise SourceError(f"{self.path}: not a readable WAV file: {exc}") from exc

        if self._wav.getsampwidth() != BYTES_PER_SAMPLE:
            raise SourceError(
                f"{self.path}: expected 16-bit samples, got "
                f"{self._wav.getsampwidth() * 8}-bit"
            )
        if self._wav.getframerate() != SAMPLE_RATE:
            raise SourceError(
                f"{self.path}: expected {SAMPLE_RATE} Hz, got "
                f"{self._wav.getframerate()} Hz. Convert it first, for example "
                f"`ffmpeg -i {self.path.name} -ar 16000 -ac 1 out.wav`."
            )
        self._channels = self._wav.getnchannels()
        if self._channels not in (1, 2):
            raise SourceError(
                f"{self.path}: expected mono or stereo, got {self._channels} channels"
            )

    @property
    def description(self) -> str:
        return f"{self.path.name} (replay, speed={self.speed or 'unpaced'})"

    def frames(self) -> Iterator[bytes]:
        period = FRAME_MS / 1000 / self.speed if self.speed else 0.0
        next_due = time.monotonic() + period
        while not self._closed:
            raw = self._wav.readframes(FRAME_SAMPLES)
            if len(raw) < FRAME_SAMPLES * BYTES_PER_SAMPLE * self._channels:
                if self.loop and not self._closed:
                    self._wav.rewind()
                    continue
                # A trailing partial frame is dropped rather than zero-padded:
                # padding would put 20 ms of invented silence into a transcript.
                return
            frame = _downmix(raw) if self._channels == 2 else raw
            yield frame
            if period:
                delay = next_due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_due += period

    def close(self) -> None:
        self._closed = True
        self._wav.close()


def _downmix(stereo: bytes) -> bytes:
    out = bytearray(FRAME_BYTES)
    for i in range(FRAME_SAMPLES):
        left = int.from_bytes(stereo[4 * i : 4 * i + 2], "little", signed=True)
        right = int.from_bytes(stereo[4 * i + 2 : 4 * i + 4], "little", signed=True)
        out[2 * i : 2 * i + 2] = ((left + right) // 2).to_bytes(
            2, "little", signed=True
        )
    return bytes(out)


def write_wav(path: Path | str, pcm: bytes, *, channels: int = 1,
              sample_rate: int = SAMPLE_RATE) -> Path:
    """Write PCM to a WAV file. Used by tests and by `--record`."""
    path = Path(path)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(BYTES_PER_SAMPLE)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return path
