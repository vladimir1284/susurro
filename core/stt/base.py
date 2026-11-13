"""Recognizer interface.

A recognizer is fed 20 ms frames for one lane and emits segments. It owns no
sockets and no session state; the server routes frames to it and forwards what
comes back.

Two kinds of output, and the distinction matters beyond rendering:

``Segment(final=False)``
    The current best guess for an open segment. Replaced on every pass. The
    console greys it out.
``Segment(final=True)``
    Committed text, never revised.

Only finals reach the language model. The prompt is append-only so that the
prefix cache survives; feeding partials would rewrite earlier tokens on every
pass and throw the cache away each time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.protocol import Lane, Language


@dataclass(frozen=True, slots=True)
class Segment:
    lane: Lane
    text: str
    t: float
    final: bool
    dur: float = 0.0
    seq: int = 0


@runtime_checkable
class Recognizer(Protocol):
    """What the server needs from a speech backend."""

    @property
    def language(self) -> Language: ...

    def feed(self, lane: Lane, timestamp_us: int, payload: bytes) -> list[Segment]:
        """Accept one frame, return whatever segments it completed.

        Returning a list rather than yielding keeps the caller synchronous and
        makes "this frame produced nothing" the common, cheap case.
        """
        ...

    def flush(self, lane: Lane) -> list[Segment]:
        """Close any open segment on a lane, e.g. when its client disconnects."""
        ...

    def reset(self) -> None: ...
