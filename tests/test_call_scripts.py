"""The recording scripts must load the way bench.replay_call reads them."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "bench" / "fixtures"
SCRIPTS = sorted(FIXTURES.glob("call_script_*.json"))


def test_the_scripts_exist() -> None:
    assert {p.name for p in SCRIPTS} >= {
        "call_script_es.json",
        "call_script_en.json",
        "call_script_es_interrupt.json",
    }


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_script_has_both_lanes_and_terms(path: Path) -> None:
    script = json.loads(path.read_text(encoding="utf-8"))
    for key in ("far", "mic", "terms"):
        assert script[key] and all(isinstance(s, str) and s.strip() for s in script[key])


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_term_is_said_in_the_script(path: Path) -> None:
    script = json.loads(path.read_text(encoding="utf-8"))
    spoken = " ".join(script["far"] + script["mic"]).lower()
    missing = [t for t in script["terms"] if t.lower() not in spoken]
    assert not missing


def test_the_reading_pages_quote_every_sentence() -> None:
    docs = Path(__file__).parent.parent / "docs" / "development"
    for page in ("recording-scripts.md", "recording-scripts.es.md"):
        text = (docs / page).read_text(encoding="utf-8")
        for path in SCRIPTS:
            script = json.loads(path.read_text(encoding="utf-8"))
            for sentence in script["far"] + script["mic"]:
                assert sentence in text, (page, sentence)
