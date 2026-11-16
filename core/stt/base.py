"""Recognizer interface.

A recognizer is fed 20 ms frames of **decoded 16-bit mono PCM** for one lane and
emits segments. It owns no sockets and no session state; the server decodes,
routes frames to it, and forwards what comes back.

Decoding deliberately happens in the server rather than here: every recognizer
wants PCM and none wants an Opus packet, so a new speech backend never has to
think about the wire format.

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

    def feed(self, lane: Lane, timestamp_us: int, pcm: bytes) -> list[Segment]:
        """Accept one 20 ms PCM frame, return whatever segments it completed.

        Returning a list rather than yielding keeps the caller synchronous and
        makes "this frame produced nothing" the common, cheap case.
        """
        ...

    def flush(self, lane: Lane) -> list[Segment]:
        """Close any open segment on a lane, e.g. when its client disconnects."""
        ...

    def select_language(self, language: Language) -> None:
        """Take the session's language from its profile.

        Called once per session, before ``warmup``, so that what gets loaded
        is what will be spoken. The configured language is only a default: a
        recognizer left on the wrong one transcribes Spanish as English
        without complaint, which is worse than failing.
        """
        ...

    def select_vocabulary(self, terms: list[str]) -> None:
        """Tell the recognizer which words to expect, from the session's profile.

        Called once per session, with ``select_language``. A recognizer that
        cannot use them ignores them.
        """
        ...

    def warmup(self) -> None:
        """Load whatever is expensive, before audio starts arriving.

        Called once per session, off the event loop. Without it the first
        frame pays for loading the model -- minutes on a first run, while the
        download happens -- and the whole server is unresponsive for it.
        """
        ...

    def reset(self) -> None: ...
