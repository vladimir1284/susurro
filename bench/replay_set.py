"""Replay every recorded call in a directory and print one table.

    python -m bench.replay_set recordings/ --stt-url http://127.0.0.1:8081 \\
        [--hints-url http://127.0.0.1:8090] --out-dir sessions/set-2026-11-17

Each call is a folder ``recordings/<name>/`` holding ``mic.wav``, ``far.wav``
(16 kHz mono, the two lanes) and ``meta.json``; a scripted call also has
``script.json``. See docs/development/recording-protocol.md. Recordings are real
conversations and stay out of git.

One call is one sample: the tuned defaults (``end_windows``, ``bleed_ratio``,
``far_floor``, the prompts) were fitted to a single call. This prints what a
change does across the set, so a default can cite a table instead of an anecdote.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Call:
    name: str
    folder: Path
    language: str
    profile: str
    mode: str
    setup: str
    scripted: bool
    minutes: float


def plan(root: Path) -> list[Call]:
    calls = []
    for meta_path in sorted(root.glob("*/meta.json")):
        folder = meta_path.parent
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for lane in ("mic", "far"):
            if not (folder / f"{lane}.wav").exists():
                raise SystemExit(f"{folder.name}: {lane}.wav is missing")
        if meta.get("scripted") and not (folder / "script.json").exists():
            raise SystemExit(f"{folder.name}: marked scripted but has no script.json")
        calls.append(Call(
            name=folder.name, folder=folder, language=meta["language"],
            profile=meta["profile"], mode=meta.get("mode", "business"),
            setup=meta.get("setup", "?"), scripted=bool(meta.get("scripted")),
            minutes=float(meta.get("minutes", 0)),
        ))
    return calls


def command(call: Call, *, stt_url: str, hints_url: str | None, out: Path,
            end_windows: int = 24, bleed_ratio: float = 2.0) -> list[str]:
    argv = [sys.executable, "-m", "bench.replay_call", str(call.folder),
            "--language", call.language, "--profile", call.profile,
            "--stt-url", stt_url, "--end-windows", str(end_windows),
            "--bleed-ratio", str(bleed_ratio), "--out", str(out)]
    if call.scripted:
        argv += ["--script", str(call.folder / "script.json")]
    if hints_url:
        argv += ["--hints-url", hints_url, "--hints-mode", call.mode]
    return argv


def cell(value, fmt: str = "{}") -> str:
    return "-" if value is None else fmt.format(value)


def table(calls: list[Call], results: dict[str, dict | None]) -> str:
    head = ["call", "lang", "setup", "min", "lag max s", "WER far", "WER mic",
            "terms", "hints/wasted", "ttft ms"]
    rows = [head, ["---"] * len(head)]
    for call in calls:
        data = results.get(call.name)
        if data is None:
            rows.append([call.name, call.language, call.setup, cell(call.minutes),
                         "failed", "-", "-", "-", "-", "-"])
            continue
        s = data["summary"]
        hints = s.get("hints")
        rows.append([
            call.name, call.language, call.setup, cell(call.minutes),
            cell(s.get("final_lag_max_s")), cell(s.get("wer_far")), cell(s.get("wer_mic")),
            cell(s.get("terms_heard")),
            f"{hints['started']}/{hints['wasted']}" if hints else "-",
            cell(hints.get("ttft_ms_median")) if hints else "-",
        ])
    return "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recordings", type=Path)
    parser.add_argument("--stt-url", default="http://127.0.0.1:8081")
    parser.add_argument("--hints-url")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--end-windows", type=int, default=24)
    parser.add_argument("--bleed-ratio", type=float, default=2.0)
    args = parser.parse_args(argv)

    calls = plan(args.recordings)
    if not calls:
        print(f"no calls (folders with meta.json) in {args.recordings}", file=sys.stderr)
        return 2
    out_dir = args.out_dir or Path("sessions") / f"set-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=False)
    results: dict[str, dict | None] = {}
    for call in calls:
        out = out_dir / f"{call.name}.json"
        print(f"replaying {call.name} ({call.minutes:g} min, real time)...", flush=True)
        done = subprocess.run(
            command(call, stt_url=args.stt_url, hints_url=args.hints_url, out=out,
                    end_windows=args.end_windows, bleed_ratio=args.bleed_ratio),
            stdout=(out_dir / f"{call.name}.txt").open("w"), stderr=subprocess.STDOUT,
        )
        results[call.name] = (
            json.loads(out.read_text(encoding="utf-8"))
            if done.returncode == 0 and out.exists() else None
        )
    text = table(calls, results)
    (out_dir / "table.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(f"results in {out_dir}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
