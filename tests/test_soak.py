"""A long call against the fakes, at accelerated pace.

AUDIT.md Line 5, task 4. The audit's worry is what an hour does that two minutes
cannot: a transcript that only grows, tasks nobody awaited, dictionaries keyed by
hint id that are never emptied. Time here is a fake clock stepped one frame at a
time, so a simulated hour is a few seconds of real work; what is being tested is
bookkeeping, not speed.

By default a few simulated minutes run, to keep the suite fast. Set
``SUSURRO_SOAK_MINUTES=60`` for the full hour.
"""

from __future__ import annotations

import asyncio
import os
import tracemalloc
from pathlib import Path

from core.llm.fake import CannedHintGenerator
from core.protocol import FRAME_MS
from core.server import Core
from core.session.profiles import ProfileStore
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
MINUTES = int(os.environ.get("SUSURRO_SOAK_MINUTES", "4"))
BUDGET = 500
FRAME_BYTES = 320 * 2  # 20 ms of 16 kHz mono 16-bit


class Clock:
    now = 5000.0

    def __call__(self) -> float:
        return self.now


def script(minutes: int) -> list[ScriptedUtterance]:
    """An utterance of five seconds per lane, alternating, for the whole call."""
    frames = 5000 // FRAME_MS
    count = minutes * 60 // 5
    out: list[ScriptedUtterance] = []
    for index in range(count):
        out.append(ScriptedUtterance(lane="far", text=f"question number {index}", frames=frames))
        out.append(ScriptedUtterance(lane="mic", text=f"answer number {index}", frames=frames))
    return out


async def test_a_long_call_stays_bounded() -> None:
    clock = Clock()
    core = Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(script=script(MINUTES), partial_every=50),
        generator=CannedHintGenerator(),
        token="soak",
        clock=clock,
        transcript_budget_chars=BUDGET,
    )
    core.state.configure("en-technical-backend", "en", "business")
    await core.start_session()
    tasks_before = len(asyncio.all_tasks())

    frames = MINUTES * 60 * 1000 // FRAME_MS
    hint_every = 30 * 1000 // FRAME_MS  # a hint every simulated 30 s
    window_sizes: list[int] = []
    memory_marks: list[int] = []
    tracemalloc.start()
    try:
        for index in range(frames):
            clock.now += FRAME_MS / 1000
            for lane in ("far", "mic"):
                await core.route_audio(lane, bytes(FRAME_BYTES))  # type: ignore[arg-type]
            if index and index % hint_every == 0:
                await core.request_hint("business")
                task = core._hint_task
                if task is not None:
                    await task
                window_sizes.append(
                    sum(len(line) + 1 for line in core.generator.contexts[-1].transcript)
                )
            if index == frames // 2:
                memory_marks.append(tracemalloc.get_traced_memory()[0])
        memory_marks.append(tracemalloc.get_traced_memory()[0])
    finally:
        tracemalloc.stop()

    assert len(core.transcript) >= MINUTES * 60 // 5, "the call was not transcribed"
    assert window_sizes, "no hint ran"

    # The prompt stays inside its budget however long the call is. A single line
    # may exceed it; none here does.
    assert max(window_sizes) <= BUDGET
    # ...and it really was trimmed: the transcript itself is far longer.
    assert sum(len(line) + 1 for line in core.transcript) > 4 * BUDGET

    # Per-hint bookkeeping is emptied as hints end.
    assert core._hint_text == {}
    assert core._hint_started == {}
    assert core._hint_ttft == {}
    assert core.state.active_hint is None

    # No task was left running.
    await asyncio.sleep(0.05)
    assert len(asyncio.all_tasks()) <= tasks_before
    assert core._hint_task is None or core._hint_task.done()

    # Lane buffers do not accumulate.
    assert all(len(buffer) <= 15 for buffer in core._preroll.values())

    # What the call keeps is the transcript itself (needed for export), a line
    # per few seconds. Memory in the second half grows by about that much and no
    # more: a leak would show as growth well beyond the transcript's own size.
    midpoint, end = memory_marks
    transcript_bytes = sum(len(line) for line in core.transcript) * 4
    assert end - midpoint < max(transcript_bytes, 200_000) * 3


async def test_a_session_restarts_clean_after_a_long_call() -> None:
    clock = Clock()
    core = Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(script=script(1), partial_every=50),
        generator=CannedHintGenerator(),
        token="soak",
        clock=clock,
        transcript_budget_chars=500,
    )
    core.state.configure("en-technical-backend", "en", "business")
    await core.start_session()
    for _ in range(60 * 1000 // FRAME_MS):
        clock.now += FRAME_MS / 1000
        for lane in ("far", "mic"):
            await core.route_audio(lane, bytes(FRAME_BYTES))  # type: ignore[arg-type]
    assert core.transcript

    core.state.stop()
    core.state.configure("en-technical-backend", "en", "business")
    await core.start_session()
    assert core.transcript == []
    assert core._finals == []
    assert core.hint_window() == ()
