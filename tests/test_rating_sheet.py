"""The blind rating sheet: what the operator fills in, and what comes back."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bench.rating_sheet import build, summarize


def result(path: Path, hints: list[dict]) -> Path:
    path.write_text(json.dumps({"summary": {}, "finals": [], "hints": hints}))
    return path


def hint(text: str, question: str = "them: a question?", reason: str = "complete") -> dict:
    return {"hint_id": "h-1", "trigger": "speculative", "mode": "business",
            "reason": reason, "ttft_ms": 100, "text": text, "context": [question]}


def test_the_sheet_does_not_say_which_model_wrote_what(tmp_path: Path) -> None:
    a = result(tmp_path / "contention-gemma.json", [hint("- one")])
    b = result(tmp_path / "contention-lfm25.json", [hint("- two")])
    sheet, key = tmp_path / "sheet.csv", tmp_path / "key.json"
    build([a, b], sheet, key, seed=1)
    text = sheet.read_text()
    assert "gemma" not in text and "lfm25" not in text
    assert "- one" in text and "- two" in text
    assert set(json.loads(key.read_text()).values()) == {
        "contention-gemma", "contention-lfm25"}


def test_the_order_is_shuffled_but_reproducible(tmp_path: Path) -> None:
    files = [result(tmp_path / f"{name}.json", [hint(f"- {name}{i}") for i in range(6)])
             for name in ("a", "b")]

    def order(seed: int) -> list[str]:
        sheet, key = tmp_path / f"s{seed}.csv", tmp_path / f"k{seed}.json"
        build(files, sheet, key, seed=seed)
        with sheet.open() as handle:
            return [row["hint"] for row in csv.DictReader(handle)]

    assert order(1) == order(1)
    assert order(1) != order(2)


def test_cancelled_and_empty_hints_are_not_rated(tmp_path: Path) -> None:
    f = result(tmp_path / "x.json", [hint("- ok"), hint("- cut", reason="cancelled"),
                                      hint("")])
    sheet, key = tmp_path / "s.csv", tmp_path / "k.json"
    assert build([f], sheet, key, seed=1) == 1


def test_a_file_without_hints_is_an_error(tmp_path: Path) -> None:
    f = tmp_path / "old.json"
    f.write_text(json.dumps({"summary": {}, "finals": []}))
    with pytest.raises(SystemExit):
        build([f], tmp_path / "s.csv", tmp_path / "k.json", seed=1)


def test_summarize_groups_ratings_by_source_and_ignores_blanks(tmp_path: Path) -> None:
    a = result(tmp_path / "a.json", [hint("- a1"), hint("- a2"), hint("- a3")])
    b = result(tmp_path / "b.json", [hint("- b1"), hint("- b2")])
    sheet, key = tmp_path / "s.csv", tmp_path / "k.json"
    build([a, b], sheet, key, seed=3)
    rows = list(csv.DictReader(sheet.open()))
    ratings = {"- a1": "2", "- a2": "1", "- a3": "", "- b1": "0", "- b2": "1"}
    for row in rows:
        row["rating"] = ratings[row["hint"]]
    with sheet.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    out = summarize(sheet, key)
    assert out["a"] == {"rated": 2, "unrated": 1, "mean": 1.5, "counts": {0: 0, 1: 1, 2: 1}}
    assert out["b"]["mean"] == 0.5


def test_a_rating_outside_0_to_2_is_refused(tmp_path: Path) -> None:
    f = result(tmp_path / "a.json", [hint("- a1")])
    sheet, key = tmp_path / "s.csv", tmp_path / "k.json"
    build([f], sheet, key, seed=1)
    rows = list(csv.DictReader(sheet.open()))
    rows[0]["rating"] = "5"
    with sheet.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SystemExit):
        summarize(sheet, key)
