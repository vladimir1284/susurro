"""The unattended driver's decisions, without a Mac."""

from __future__ import annotations

from datetime import datetime

from bench.overnight import BUDGET, can_start, deadline_after, next_round_wait


def test_the_deadline_is_the_next_occurrence_of_the_time() -> None:
    night = datetime(2026, 11, 16, 23, 0)
    assert deadline_after(night, 6, 30) == datetime(2026, 11, 17, 6, 30)
    early = datetime(2026, 11, 17, 2, 0)
    assert deadline_after(early, 6, 30) == datetime(2026, 11, 17, 6, 30)


def test_a_time_that_just_passed_means_tomorrow() -> None:
    now = datetime(2026, 11, 17, 6, 30)
    assert deadline_after(now, 6, 30) == datetime(2026, 11, 18, 6, 30)


def test_no_step_starts_that_cannot_finish() -> None:
    assert can_start(0, BUDGET["model"] + 1, "model")
    assert not can_start(0, BUDGET["model"], "model")
    assert not can_start(100, 100 + BUDGET["stt"] - 1, "stt")


def test_the_next_round_waits_for_its_slot() -> None:
    # round started at t=0, interval 45 min, deadline far away
    assert next_round_wait(now=1000, started=0, interval_s=2700, deadline=20000) == 1700


def test_a_slow_round_starts_the_next_one_at_once() -> None:
    assert next_round_wait(now=3000, started=0, interval_s=2700, deadline=20000) == 0


def test_no_round_starts_when_its_slot_leaves_no_time() -> None:
    # slot at 2700 + the 600 s margin is past the deadline: stop, do not rush.
    assert next_round_wait(now=1000, started=0, interval_s=2700, deadline=3200) is None
