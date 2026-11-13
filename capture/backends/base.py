"""What the client needs from an audio source.

A source yields raw PCM frames of exactly ``FRAME_BYTES``. It does not encode,
it does not timestamp, and it does not know what a lane is — the client does
that, so the logic lives in one place rather than once per platform.

Sources are expected to deliver 16 kHz mono. Device backends ask the operating
system's audio server to do that conversion rather than resampling in Python:
PipeWire and CoreAudio both have good resamplers, and a hand-rolled one in the
capture path would be a quality regression nobody would notice until the
transcripts got worse.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable


class SourceError(Exception):
    pass


@runtime_checkable
class AudioSource(Protocol):
    @property
    def description(self) -> str:
        """Human-readable, for logs and `--list-devices`."""
        ...

    def frames(self) -> Iterator[bytes]:
        """Yield 20 ms PCM frames until the source ends or is closed."""
        ...

    def close(self) -> None: ...
