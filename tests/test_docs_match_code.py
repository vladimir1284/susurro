"""Guards against the documentation and the code drifting apart.

Every one of these corresponds to drift that was actually found by reading the
repository as a stranger would: a protocol document describing a `ready` message
the core does not send, an error code in an example that nothing emits, an
architecture page describing a voice activity detector that does not exist, and
a repository layout listing a package that was never created.

Prose cannot be tested. Specific, checkable claims can, and those are the ones
that mislead when they rot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core import protocol as p

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_DOC = ROOT / "docs" / "architecture" / "protocol.md"
SETUP_DOC = ROOT / "docs" / "development" / "setup.md"

#: `code = "..."` on an exception class, or a literal passed to p.error(...).
EMITTED = re.compile(r'code = "([a-z_]+)"|p\.error\(\s*"([a-z_]+)"')
#: A row of the error table: | `code` | meaning |
DOCUMENTED = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)


def emitted_codes() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "core").rglob("*.py"):
        for match in EMITTED.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) or match.group(2))
    return found


def documented_codes() -> set[str]:
    return set(DOCUMENTED.findall(PROTOCOL_DOC.read_text(encoding="utf-8")))


class TestErrorCodes:
    def test_every_emitted_code_is_documented(self) -> None:
        missing = emitted_codes() - documented_codes()
        assert not missing, f"codes emitted but not in the protocol doc: {sorted(missing)}"

    def test_every_documented_code_is_emitted(self) -> None:
        # A code in the documentation that nothing sends is worse than a
        # missing one: a client author writes a handler for an event that will
        # never arrive. `stt_overrun` sat in an example for days.
        extra = documented_codes() - emitted_codes()
        assert not extra, f"codes documented but never emitted: {sorted(extra)}"

    def test_the_table_is_not_empty(self) -> None:
        # Guards the regex itself: a formatting change that stopped matching
        # would make both tests above pass vacuously.
        assert len(documented_codes()) >= 8


class TestDocumentedExamples:
    """JSON examples in the protocol document must parse and be real messages."""

    def examples(self) -> list[dict]:
        text = PROTOCOL_DOC.read_text(encoding="utf-8")
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('{"type"') and line.endswith("}"):
                out.append(json.loads(line))
        return out

    def test_there_are_examples_to_check(self) -> None:
        assert len(self.examples()) >= 8

    def test_every_example_has_a_type_the_code_knows(self) -> None:
        known = {
            "transcript.partial", "transcript.final", "hint.start", "hint.delta",
            "hint.end", "state", "error", "ready", "hello", "profiles", "profile",
            "hint.request", "hint.cancel", "session.config", "session.start",
            "session.stop", "profiles.request", "profile.request", "profile.save",
        }
        unknown = {e["type"] for e in self.examples()} - known
        assert not unknown, f"documented message types the code does not know: {unknown}"

    def test_console_command_examples_parse(self) -> None:
        outbound = {"hint.start", "hint.delta", "hint.end", "profile"}
        commands = [
            e
            for e in self.examples()
            if e["type"].startswith(("hint.", "session.", "profiles.", "profile."))
            and e["type"] not in outbound
        ]
        for example in commands:
            p.parse_console_command(json.dumps(example))

    def test_the_documented_ready_message_matches_what_the_core_sends(self) -> None:
        # The document said {"type":"ready"} while the core sends a version
        # alongside it, which a client author would only discover at runtime.
        assert json.dumps(json.loads(p.ready()), separators=(",", ":")) in (
            PROTOCOL_DOC.read_text(encoding="utf-8").replace(" ", "")
        ).replace("\n", "")

    def test_the_documented_payload_cap_matches_the_code(self) -> None:
        assert str(p.MAX_PAYLOAD_BYTES) in PROTOCOL_DOC.read_text(encoding="utf-8")


class TestRepositoryLayout:
    """Paths drawn in the setup document must exist."""

    def drawn_paths(self) -> list[str]:
        """Flatten the tree drawing into repository-relative paths.

        Depth comes from the column the branch marker sits in: the drawing uses
        four columns per level.
        """
        block = SETUP_DOC.read_text(encoding="utf-8").split("```", 2)[1]
        out: list[str] = []
        parents: dict[int, str] = {}
        for line in block.splitlines():
            match = re.search(r"[├└]──\s+([A-Za-z0-9_./]+)", line)
            if not match:
                continue
            depth = line.index(match.group(0)) // 4
            name = match.group(1).rstrip("/")
            prefix = parents.get(depth - 1, "") if depth else ""
            path = f"{prefix}/{name}" if prefix else name
            parents[depth] = path
            out.append(path)
        return out

    def test_the_layout_was_parsed(self) -> None:
        assert len(self.drawn_paths()) >= 10

    def test_every_drawn_path_exists(self) -> None:
        # The layout showed a `core/audio/` package with VAD and resampling in
        # it. None of the three ever existed.
        missing = [path for path in self.drawn_paths() if not (ROOT / path).exists()]
        assert not missing, f"layout draws paths that do not exist: {missing}"


class TestUnbuiltThingsAreMarked:
    @pytest.mark.parametrize(
        "doc", ["docs/architecture/audio.md", "docs/architecture/audio.es.md"]
    )
    def test_the_voice_detector_is_no_longer_flagged_as_absent(self, doc: str) -> None:
        # This test used to assert the opposite. The detector was documented in
        # the present tense before it existed, the warning was added when that
        # was caught, and it comes off now that core/vad.py is real. Keeping
        # the test inverted rather than deleting it is the point: the next
        # person to describe something unbuilt has to come here.
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "Partly built" not in text
        assert "Construido a medias" not in text
        assert "core/vad.py" in text

    @pytest.mark.parametrize(
        "doc", ["docs/architecture/audio.md", "docs/architecture/audio.es.md"]
    )
    def test_the_energy_detector_limitation_is_stated(self, doc: str) -> None:
        # It cannot tell a voice from a door. A reader choosing a backend has
        # to be told that before they pick it for a noisy room.
        text = (ROOT / doc).read_text(encoding="utf-8").lower()
        assert "energy" in text or "energía" in text
        assert "door" in text or "portazo" in text

    @pytest.mark.parametrize(
        "doc", ["docs/architecture/inference.md", "docs/architecture/inference.es.md"]
    )
    def test_the_estimate_warning_survives_until_m0_runs(self, doc: str) -> None:
        # These pages carry numbers derived from specifications. The warning
        # comes off when bench/results/ has a real run in it, not before.
        text = (ROOT / doc).read_text(encoding="utf-8")
        real_results = [
            path
            for path in (ROOT / "bench" / "results").glob("*.json")
            if not json.loads(path.read_text()).get("dry_run", False)
        ]
        if real_results:
            pytest.skip("a real benchmark result exists; the warning can be revisited")
        assert "!!! warning" in text
