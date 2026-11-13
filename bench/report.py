"""Result file and gate evaluation.

The gates come from `docs/roadmap.md`. They are evaluated here rather than read
off a table by a human, because a gate a person has to remember is a gate that
gets quietly relaxed when the number comes back disappointing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: M0's gate conditions. Changing a threshold here is a design decision and
#: should be argued for in the commit message, not adjusted to make a run pass.
GATES: dict[str, dict[str, Any]] = {
    "warm_ttft_s": {
        "limit": 1.0,
        "direction": "below",
        "what": "time to first token, warm cache, 2k static context plus 100 token delta",
    },
    "resident_memory_gb": {
        "limit": 12.0,
        "direction": "below",
        "what": "peak resident memory with the speech and language models both loaded",
    },
    "stt_realtime_factor": {
        "limit": 1.0,
        "direction": "below",
        "what": "seconds of compute per second of audio; at or above 1.0 the transcript falls behind permanently",
    },
}


@dataclass
class Verdict:
    name: str
    value: float | None
    limit: float
    passed: bool
    what: str

    def line(self) -> str:
        if self.value is None:
            return f"  SKIP  {self.name}: not measured ({self.what})"
        mark = "PASS" if self.passed else "FAIL"
        return f"  {mark}  {self.name}: {self.value} (limit {self.limit}) — {self.what}"


@dataclass
class Report:
    machine: dict[str, Any]
    stt: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False

    def measurement(self, key: str) -> float | None:
        for section in (self.llm, self.stt):
            if key in section:
                value = section[key]
                return value.get("median") if isinstance(value, dict) else value
        return None

    def verdicts(self) -> list[Verdict]:
        out = []
        for name, gate in GATES.items():
            value = self.measurement(name)
            passed = value is not None and value < gate["limit"]
            out.append(
                Verdict(name, value, gate["limit"], passed, gate["what"])
            )
        return out

    def gate_passed(self) -> bool:
        verdicts = self.verdicts()
        # A gate with no measurement is not a pass. Silence is not evidence.
        return bool(verdicts) and all(v.passed for v in verdicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            "machine": self.machine,
            "stt": self.stt,
            "llm": self.llm,
            "notes": self.notes,
            "gates": {
                v.name: {"value": v.value, "limit": v.limit, "passed": v.passed}
                for v in self.verdicts()
            },
            "gate_passed": self.gate_passed(),
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def summary(self) -> str:
        lines = ["", "M0 benchmark", "=" * 40]
        machine = self.machine
        lines.append(
            f"machine: {machine.get('machine')} · {machine.get('cpu_count')} cores · "
            f"{machine.get('total_memory_gb')} GB · accelerator: "
            f"{machine.get('accelerator')}"
        )
        if self.dry_run:
            lines.append("DRY RUN — stubs, not models. These numbers measure nothing.")
        if machine.get("accelerator") == "none":
            lines.append(
                "WARNING: no accelerator detected. Results from this machine do not "
                "transfer to the target hardware and must not be quoted as if they do."
            )
        for section, title in ((self.stt, "speech"), (self.llm, "language model")):
            if not section:
                continue
            lines.append(f"\n{title}:")
            for key, value in sorted(section.items()):
                if isinstance(value, dict) and "median" in value:
                    lines.append(
                        f"  {key}: median {value['median']} {value.get('unit', '')} "
                        f"(p90 {value.get('p90')}, n={value.get('n')})"
                    )
                else:
                    lines.append(f"  {key}: {value}")
        lines.append("\ngates:")
        lines.extend(v.line() for v in self.verdicts())
        lines.append("")
        lines.append("GATE PASSED" if self.gate_passed() else "GATE NOT PASSED")
        for note in self.notes:
            lines.append(f"note: {note}")
        return "\n".join(lines)
