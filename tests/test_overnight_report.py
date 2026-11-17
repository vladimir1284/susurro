"""The morning summary, on made-up results."""

from __future__ import annotations

import json
from pathlib import Path

from bench.overnight_report import collect, load, report


def write(out: Path, round_no: int, step: str, rc: int, payload: dict | None) -> None:
    path = None
    if payload is not None:
        folder = out / f"round{round_no:02d}"
        folder.mkdir(exist_ok=True)
        path = folder / f"{step}.json"
        path.write_text(json.dumps(payload))
    with (out / "results.jsonl").open("a") as handle:
        handle.write(json.dumps({"round": round_no, "step": step, "rc": rc,
                                 "json": str(path) if path else None}) + "\n")


def llm(warm: float, p90: float | None = None, cold: float = 1.0) -> dict:
    return {"llm": {"warm_ttft_s": {"median": warm, "p90": p90 or warm, "n": 5},
                    "cold_prefill_1000_s": {"median": cold, "p90": cold, "n": 5}}}


def test_it_spreads_a_metric_across_rounds(tmp_path: Path) -> None:
    for round_no, warm in enumerate((0.4, 0.5, 0.9), start=1):
        write(tmp_path, round_no, "llamacpp-x", 1, llm(warm))
    text = report(tmp_path)
    assert "0.5 [0.4 - 0.9] n=3" in text


def test_a_failed_step_is_counted_and_kept_out_of_the_numbers(tmp_path: Path) -> None:
    write(tmp_path, 1, "llamacpp-x", 1, llm(0.4))
    write(tmp_path, 2, "llamacpp-x", 91, None)
    write(tmp_path, 3, "llamacpp-x", 124, None)
    data = collect(load(tmp_path))
    assert len(data["failures"]) == 2
    assert data["metrics"]["llamacpp-x"]["warm_ttft_s"] == [0.4]
    assert "llamacpp-x: rc=91 x1" in report(tmp_path)


def test_rc_1_is_a_finished_bench_not_a_failure(tmp_path: Path) -> None:
    # bench.run exits 1 when the M0 gate is not passed, which a run that
    # skipped speech on purpose never can.
    write(tmp_path, 1, "llamacpp-x", 1, llm(0.4))
    assert collect(load(tmp_path))["failures"] == []


def test_a_stall_is_flagged_and_ordinary_noise_is_not(tmp_path: Path) -> None:
    write(tmp_path, 1, "a", 1, llm(0.4, p90=16.0))   # 40x the median, over 5 s
    write(tmp_path, 2, "a", 1, llm(0.4, p90=0.9))    # noise
    write(tmp_path, 3, "a", 1, llm(2.0, p90=5.5))    # over 5 s but under 3x
    stalls = collect(load(tmp_path))["stalls"]
    assert [(s["round"], s["metric"]) for s in stalls] == [(1, "warm_ttft_s")]


def test_replay_and_contention_summaries_are_read(tmp_path: Path) -> None:
    summary = {"summary": {"final_lag_max_s": 2.1, "wer_far": 0.09,
                           "hints": {"started": 10, "wasted": 3, "ttft_ms_median": 300}}}
    write(tmp_path, 1, "contention-gemma", 0, summary)
    metrics = collect(load(tmp_path))["metrics"]["contention-gemma"]
    assert metrics["hints_wasted"] == [3]
    assert metrics["hint_ttft_ms_median"] == [300]


def test_an_empty_directory_still_reports(tmp_path: Path) -> None:
    text = report(tmp_path)
    assert "0 rounds" in text
    assert "mac_state.csv" in text


def test_the_mac_state_log_is_summarized(tmp_path: Path) -> None:
    (tmp_path / "mac_state.csv").write_text(
        "ts,load1,free_pages,swap,top1,top2,top3\n"
        "1,1.0,100,0.00M,mediaanalysisd:90.0,Chrome:5.0,x:1.0\n"
        "2,3.0,100,0.00M,mediaanalysisd:80.0,Chrome:5.0,x:1.0\n"
    )
    text = report(tmp_path)
    assert "2 samples" in text
    assert "mediaanalysisd: 2 samples" in text


def test_gaps_in_the_sampler_are_reported_as_freezes(tmp_path: Path) -> None:
    # The sampler writes every 10 s; a 24 s hole means the Mac did not run it.
    stamps = [100, 110, 134, 144, 154, 178]
    lines = ["ts,load1,free_pages,swap,top1,top2,top3"] + [
        f"{t},1.0,100,0.00M,x:1.0,y:1.0,z:1.0" for t in stamps]
    (tmp_path / "mac_state.csv").write_text("\n".join(lines) + "\n")
    text = report(tmp_path)
    assert "2 freezes" in text
    assert "about 28 s" in text  # (24-10) + (24-10)


def test_lag_from_early_rounds_can_be_excluded(tmp_path: Path) -> None:
    # Rounds 1-2 of the first night were taken with a replay that hid its lag.
    for round_no, lag in ((1, -6.0), (2, -4.0), (3, 2.0), (4, 3.0)):
        write(tmp_path, round_no, "replay", 0,
              {"summary": {"final_lag_max_s": lag, "wer_far": 0.1}})
    rows = load(tmp_path)
    everything = collect(rows)["metrics"]["replay"]
    assert everything["final_lag_max_s"] == [-6.0, -4.0, 2.0, 3.0]
    trusted = collect(rows, lag_from_round=3)["metrics"]["replay"]
    assert trusted["final_lag_max_s"] == [2.0, 3.0]
    assert trusted["wer_far"] == [0.1, 0.1, 0.1, 0.1]  # only lag is excluded
