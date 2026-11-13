"""Guards against the repository's front door going stale.

The README claimed "nothing is implemented" for four commits after it stopped
being true, and its documentation links pointed at a site that had never
deployed. Both are the kind of rot nobody notices from inside the project,
because nobody reads their own README. These tests notice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONT_DOOR = ["README.md", "CONTRIBUTING.md"]

#: Markdown links that are not absolute URLs, anchors, or mail links.
LINK = re.compile(r"\[[^\]]+\]\((?!https?://|#|mailto:)([^)\s]+)\)")


def links_in(path: Path) -> list[tuple[str, int]]:
    out = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        out.extend((match.group(1), number) for match in LINK.finditer(line))
    return out


@pytest.mark.parametrize("name", FRONT_DOOR)
def test_relative_links_resolve(name: str) -> None:
    path = ROOT / name
    broken = [
        f"{name}:{number} -> {target}"
        for target, number in links_in(path)
        if not (ROOT / target.split("#")[0]).exists()
    ]
    assert not broken, f"broken relative links: {broken}"


@pytest.mark.parametrize("name", FRONT_DOOR)
def test_no_links_to_an_undeployed_site(name: str) -> None:
    # The documentation site has never deployed: the Cloudflare token lacks
    # Workers Scripts: Edit. Linking to it hands a reader a dead URL. Put the
    # links back when a deploy has actually succeeded.
    assert "workers.dev" not in (ROOT / name).read_text(encoding="utf-8")


def test_the_readme_does_not_claim_nothing_is_built() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "nothing is implemented" not in text


def test_the_readme_names_the_unrun_backend() -> None:
    # PipeWire capture has never been executed. A reader who cannot see that
    # from the README will find out the hard way.
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pipewire" in text.lower()
    assert "never executed" in text.lower()


def test_contributing_asks_for_the_thing_that_is_actually_missing() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").lower()
    assert "benchmark" in text


@pytest.mark.parametrize(
    "path",
    [
        "core/protocol.py",
        "core/server.py",
        "capture/backends/pipewire.py",
        "console/state.js",
        "bench/run.py",
        "core.toml.example",
        "tests/fixtures/profiles/README.md",
    ],
)
def test_paths_named_in_the_readme_exist(path: str) -> None:
    # The README's status table points at files. A rename that misses it turns
    # the table into fiction.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if path in readme:
        assert (ROOT / path).exists(), f"README names {path}, which does not exist"
