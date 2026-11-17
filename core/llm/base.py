"""Hint generation interface.

The shape of this interface is dictated by one constraint from
``docs/architecture/inference.md``: the prompt must be **append-only**, so that
the static preamble is prefilled once per session and every hint afterwards
only pays for the new transcript lines.

Hence two separate calls rather than one:

``prefill(preamble, language)``
    Once, at ``session.start``. Expensive. Its KV cache is retained. What it
    processes must be exactly the start of what every ``stream`` call sends, in
    that session's language: a prefix cache matches from the first token.
``stream(context)``
    Per hint. Appends the transcript delta and generates.

A backend that re-processes the preamble on every ``stream`` call is correct but
useless: it turns a sub-second hint into a multi-second one. The fake backend
counts prefills so tests can assert the server is not doing that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from core.protocol import Language, Mode


@dataclass(frozen=True, slots=True)
class HintContext:
    """Everything the model is given for one hint.

    ``transcript`` holds only committed lines, oldest first. Partials never
    appear here: they are revised on every recognizer pass, and revising text
    already in the prompt is what invalidates the prefix cache.
    """

    mode: Mode
    language: Language
    transcript: tuple[str, ...] = field(default=())


@runtime_checkable
class HintGenerator(Protocol):
    async def prefill(self, preamble: str, language: Language) -> None:
        """Process the static context once and keep its cache.

        ``language`` is the session's. The mode is deliberately not a
        parameter: a hint may ask for a different mode than the session's, so
        nothing mode-specific can be part of the cached prefix.
        """
        ...

    def stream(self, context: HintContext) -> AsyncIterator[str]:
        """Yield hint text as it is produced.

        Streaming rather than returning is not a nicety. At the token rates a
        small machine sustains, a complete hint takes seconds; the user has to
        be able to start reading the first line while the rest arrives.
        """
        ...
