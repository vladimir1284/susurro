"""A hint generator that returns canned text instead of running a model.

Lets the server, the console and the protocol be developed and tested without
model weights, which matters because the machine this is often developed on
cannot host a useful model at all.

It also instruments the thing most worth asserting: ``prefill_count``. The whole
latency argument rests on the preamble being prefilled once per session rather
than once per hint, and that is a property of the *server*, not of the model.
A test can hold this backend and check it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.llm.base import HintContext

DEFAULT_HINTS: dict[str, tuple[str, ...]] = {
    "conversational": (
        "- Migration rollback, Q2\n",
        "- Disagreed on timing, not approach\n",
        "- Ran both in parallel, compared nightly\n",
    ),
    "technical": (
        "Approach: two-pointer over the sorted array.\n",
        "Edge cases: empty input, all duplicates.\n",
    ),
    "business": (
        "- Fases reversibles, no llave en mano\n",
        "- Modbus TCP sobre cable\n",
        "- Cerrar visita técnica, no alcance\n",
    ),
}


@dataclass
class CannedHintGenerator:
    """Streams fixed text, optionally with a delay between chunks."""

    hints: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_HINTS)
    )
    #: Seconds between chunks. Zero by default so tests do not wait.
    chunk_delay_s: float = 0.0

    prefill_count: int = field(default=0, init=False)
    prefilled: str | None = field(default=None, init=False)
    contexts: list[HintContext] = field(default_factory=list, init=False)

    async def prefill(self, preamble: str) -> None:
        self.prefill_count += 1
        self.prefilled = preamble

    async def stream(self, context: HintContext) -> AsyncIterator[str]:
        self.contexts.append(context)
        for chunk in self.hints.get(context.mode, ("- (no canned hint)\n",)):
            if self.chunk_delay_s:
                await asyncio.sleep(self.chunk_delay_s)
            yield chunk
