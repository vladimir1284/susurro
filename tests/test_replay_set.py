"""The whole evaluation set in one command: planning and the table, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.replay_set import Call, command, plan, table


def make_call(root: Path, name: str, **meta) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "mic.wav").write_bytes(b"")
    (folder / "far.wav").write_bytes(b"")
    base = {"language": "es", "profile": "es-business-industrial", "mode": "business",
            "setup": "headphones", "scripted": False, "minutes": 5}
    (folder / "meta.json").write_text(json.dumps({**base, **meta}))
    return folder


def test_every_call_with_a_meta_file_is_planned_in_name_order(tmp_path: Path) -> None:
    make_call(tmp_path, "b-call")
    make_call(tmp_path, "a-call", language="en", profile="en-technical-backend")
    (tmp_path / "stray").mkdir()  # no meta.json: not a call
    calls = plan(tmp_path)
    assert [c.name for c in calls] == ["a-call", "b-call"]
    assert calls[0].language == "en"


def test_a_call_with_missing_audio_is_an_error(tmp_path: Path) -> None:
    folder = make_call(tmp_path, "c")
    (folder / "far.wav").unlink()
    with pytest.raises(SystemExit):
        plan(tmp_path)


def test_a_scripted_call_passes_its_script_and_an_unscripted_one_does_not(
    tmp_path: Path,
) -> None:
    scripted = make_call(tmp_path, "s", scripted=True)
    (scripted / "script.json").write_text("{}")
    make_call(tmp_path, "u")
    by_name = {c.name: c for c in plan(tmp_path)}
    assert "--script" in command(by_name["s"], stt_url="http://stt", hints_url=None,
                                 out=tmp_path / "o.json")
    assert "--script" not in command(by_name["u"], stt_url="http://stt", hints_url=None,
                                     out=tmp_path / "o.json")


def test_a_scripted_call_without_a_script_file_is_an_error(tmp_path: Path) -> None:
    make_call(tmp_path, "s", scripted=True)
    with pytest.raises(SystemExit):
        plan(tmp_path)


def test_hints_are_requested_only_when_a_server_is_given(tmp_path: Path) -> None:
    make_call(tmp_path, "c")
    call = plan(tmp_path)[0]
    without = command(call, stt_url="http://stt", hints_url=None, out=tmp_path / "o.json")
    with_hints = command(call, stt_url="http://stt", hints_url="http://llm",
                         out=tmp_path / "o.json")
    assert "--hints-url" not in without
    assert with_hints[with_hints.index("--hints-url") + 1] == "http://llm"
    assert with_hints[with_hints.index("--hints-mode") + 1] == "business"
    assert with_hints[with_hints.index("--language") + 1] == "es"


def test_the_table_has_a_row_per_call_and_marks_missing_numbers(tmp_path: Path) -> None:
    calls = [Call("a", tmp_path, "es", "p", "business", "headphones", False, 5),
             Call("b", tmp_path, "en", "p", "business", "loudspeakers", True, 30)]
    results = {
        "a": {"summary": {"final_lag_max_s": 2.1, "hints": {
            "started": 8, "wasted": 3, "ttft_ms_median": 300}}},
        "b": None,  # the replay failed
    }
    text = table(calls, results)
    lines = text.strip().splitlines()
    assert len(lines) == 2 + 2  # header, rule, two calls
    assert "2.1" in lines[2] and "8/3" in lines[2] and "300" in lines[2]
    assert "failed" in lines[3]
