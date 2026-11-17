"""A blind sheet for rating hints by hand.

Keyword overlap is a floor, not a measure of whether a hint helps, and a hosted
model as judge would send call content off the machine, which the project exists
not to do. So a person rates: for each hint, what was just said, what the hint
said, and a 0 to 2 usefulness. The sheet carries no model or mode name, and is
shuffled, so the rater cannot favour a model they like.

    python -m bench.rating_sheet build RESULT.json... --sheet sheet.csv --key key.json
    (fill the ``rating`` column: 0 useless or wrong, 1 usable, 2 exactly what to say)
    python -m bench.rating_sheet summarize --sheet sheet.csv --key key.json

The inputs are the ``--out`` files of ``bench.replay_call --hints-url``. The
source of a hint is its file's name; keep the key file away from the rater.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

FIELDS = ["id", "question", "hint", "rating"]


def build(results: list[Path], sheet: Path, key: Path, seed: int = 0) -> int:
    rows: list[dict] = []
    sources: dict[str, str] = {}
    for path in results:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "hints" not in data:
            raise SystemExit(
                f"{path} has no hint texts: it predates them or was run without "
                "--hints-url"
            )
        for index, record in enumerate(data["hints"]):
            if record.get("reason") != "complete" or not record.get("text", "").strip():
                continue  # a cut-off hint is not what the user would have read
            ident = f"{path.stem}#{index}"
            sources[ident] = path.stem
            rows.append({"ident": ident, "question": " / ".join(record.get("context", [])),
                         "hint": record["text"].strip()})
    random.Random(seed).shuffle(rows)
    key_map: dict[str, str] = {}
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for number, row in enumerate(rows, start=1):
            blind = f"{number:03d}"
            key_map[blind] = sources[row["ident"]]
            writer.writerow({"id": blind, "question": row["question"],
                             "hint": row["hint"], "rating": ""})
    key.write_text(json.dumps(key_map, indent=2), encoding="utf-8")
    return len(rows)


def summarize(sheet: Path, key: Path) -> dict[str, dict]:
    key_map = json.loads(key.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    with sheet.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            source = key_map[row["id"]]
            entry = out.setdefault(
                source, {"rated": 0, "unrated": 0, "mean": None, "counts": {0: 0, 1: 0, 2: 0}}
            )
            raw = row["rating"].strip()
            if not raw:
                entry["unrated"] += 1
                continue
            if raw not in {"0", "1", "2"}:
                raise SystemExit(f"hint {row['id']}: rating {raw!r} is not 0, 1 or 2")
            entry["counts"][int(raw)] += 1
            entry["rated"] += 1
    for entry in out.values():
        total = sum(value * count for value, count in entry["counts"].items())
        entry["mean"] = round(total / entry["rated"], 2) if entry["rated"] else None
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("build")
    make.add_argument("results", nargs="+", type=Path)
    make.add_argument("--sheet", type=Path, required=True)
    make.add_argument("--key", type=Path, required=True)
    make.add_argument("--seed", type=int, default=0)
    read = sub.add_parser("summarize")
    read.add_argument("--sheet", type=Path, required=True)
    read.add_argument("--key", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "build":
        count = build(args.results, args.sheet, args.key, args.seed)
        print(f"{count} hints to rate in {args.sheet}; keep {args.key} away from the rater")
    else:
        for source, entry in sorted(summarize(args.sheet, args.key).items()):
            print(f"{source}: mean {entry['mean']} over {entry['rated']} rated "
                  f"({entry['unrated']} unrated), counts {entry['counts']}")
        print("Few hints: read the counts, not just the mean.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
