"""Summarize an overnight run: ``python -m bench.overnight_report OUT_DIR``.

Reads ``results.jsonl`` and the JSON each step wrote, and prints Markdown: per
step and metric the spread across rounds (min, median, max), which steps failed
and why they are not in it, the slow outliers (a p90 far above its median is a
stall, not noise), and what the Mac was doing, from ``mac_state.csv``.

Never quote one round. The Mac's speed has varied about 3x over an evening; the
median and the spread are the finding.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

#: rc 0 and 1 are a finished bench (1 only says the M0 gate was not passed, which
#: it cannot be when speech or memory was deliberately skipped). Anything else is
#: the step failing: 2 or 3 an error, 9x the driver, 124 a timeout.
OK_CODES = {0, 1}
STALL_RATIO = 3.0
STALL_MIN_S = 5.0

LLM_KEYS = [
    "warm_ttft_s", "cold_prefill_1000_s", "cold_prefill_4000_s", "decode_tokens_s",
    "d1_first_hint_ttft_cold_s", "d1_first_hint_ttft_prefilled_s",
    "d1_first_hint_ttft_legacy_s", "resident_memory_gb",
]


def median_of(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("median")
    return float(value) if isinstance(value, (int, float)) else None


def load(out: Path) -> list[dict]:
    rows = []
    path = out / "results.jsonl"
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def spread(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (f"{statistics.median(values):.3g} "
            f"[{min(values):.3g} - {max(values):.3g}] n={len(values)}")


def collect(rows: list[dict], lag_from_round: int = 1) -> dict:
    """Group the finished steps' numbers by step label and metric."""
    metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    failures: list[dict] = []
    stalls: list[dict] = []
    for row in rows:
        step = row["step"]
        if row["rc"] not in OK_CODES:
            failures.append(row)
            continue
        data = read_json(row["json"])
        if data is None:
            failures.append({**row, "rc": "no-json"})
            continue
        if step == "stt":
            value = median_of(data.get("stt", {}).get("stt_realtime_factor"))
            if value is not None:
                metrics[step]["stt_realtime_factor"].append(value)
        elif step == "replay" or step.startswith("contention"):
            summary = data.get("summary", {})
            for key in ("final_lag_mean_s", "final_lag_max_s", "wer_far", "wer_mic"):
                if key.startswith("final_lag") and row["round"] < lag_from_round:
                    continue
                if isinstance(summary.get(key), (int, float)):
                    metrics[step][key].append(summary[key])
            hints = summary.get("hints")
            if hints:
                metrics[step]["hints_started"].append(hints["started"])
                metrics[step]["hints_wasted"].append(hints["wasted"])
                if hints.get("ttft_ms_median") is not None:
                    metrics[step]["hint_ttft_ms_median"].append(hints["ttft_ms_median"])
        else:
            for key, value in data.get("llm", {}).items():
                median = median_of(value)
                if median is None:
                    continue
                metrics[step][key].append(median)
                if isinstance(value, dict) and key.endswith("_s"):
                    p90 = value.get("p90")
                    if (isinstance(p90, (int, float)) and p90 > STALL_MIN_S
                            and p90 > STALL_RATIO * max(median, 1e-9)):
                        stalls.append({"round": row["round"], "step": step, "metric": key,
                                       "median": median, "p90": p90})
    return {"metrics": metrics, "failures": failures, "stalls": stalls}


def mac_summary(path: Path) -> list[str]:
    if not path.exists():
        return ["No `mac_state.csv` (the sampler log was not fetched)."]
    loads, tops, stamps = [], Counter(), []
    with path.open(encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0] == "ts":
                continue
            try:
                loads.append(float(row[1]))
                stamps.append(int(row[0]))
            except (ValueError, IndexError):
                continue
            for cell in row[4:7]:
                name = cell.split(":")[0].strip()
                if name:
                    tops[name] += 1
    if not loads:
        return ["`mac_state.csv` has no samples."]
    # The sampler sleeps 10 s between rows. A longer gap is time the Mac did not
    # run it: on the first night it was a freeze of about 13 s once a minute.
    holes = [b - a - 10 for a, b in zip(stamps, stamps[1:], strict=False) if b - a > 14]
    out = [f"{len(loads)} samples, 10 s apart. Load average: median "
           f"{statistics.median(loads):.2f}, max {max(loads):.2f}.",
           "", f"{len(holes)} freezes (a sampler gap over 14 s), about {sum(holes)} s "
           f"in all.", "", "Most often among the three busiest processes:", ""]
    out += [f"- {name}: {count} samples" for name, count in tops.most_common(6)]
    return out


def report(out: Path, lag_from_round: int = 1) -> str:
    rows = load(out)
    data = collect(rows, lag_from_round)
    rounds = sorted({row["round"] for row in rows})
    lines = [f"# Overnight run: {out.name}", "",
             f"{len(rounds)} rounds, {len(rows)} steps, {len(data['failures'])} failed.", ""]
    for step in sorted(data["metrics"]):
        lines += [f"## {step}", "", "| metric | median [min - max] n |", "|---|---|"]
        keys = [k for k in LLM_KEYS if k in data["metrics"][step]]
        keys += [k for k in data["metrics"][step] if k not in keys]
        lines += [f"| `{k}` | {spread(data['metrics'][step][k])} |" for k in keys]
        lines.append("")
    lines += ["## Failed steps", ""]
    if data["failures"]:
        counts = Counter((f["step"], f["rc"]) for f in data["failures"])
        lines += [f"- {step}: rc={rc} x{n}" for (step, rc), n in sorted(counts.items(),
                                                                       key=str)]
    else:
        lines.append("None.")
    lines += ["", "## Slow outliers (p90 over 3x the median and over 5 s)", ""]
    if data["stalls"]:
        lines += [f"- round {s['round']} `{s['step']}` `{s['metric']}`: median "
                  f"{s['median']:.3g} s, p90 {s['p90']:.3g} s" for s in data["stalls"]]
    else:
        lines.append("None.")
    lines += ["", "## The Mac meanwhile", ""] + mac_summary(out / "mac_state.csv")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    lag_from = 1
    if "--lag-from-round" in args:
        index = args.index("--lag-from-round")
        lag_from = int(args[index + 1])
        del args[index : index + 2]
    if len(args) != 1:
        print(__doc__.split("\n\n")[0] + "\n[--lag-from-round N ignores replay lag "
              "before round N]", file=sys.stderr)
        return 2
    print(report(Path(args[0]), lag_from))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
