"""Session recording and export.

**Off by default.** The point of this project is that a conversation stays on
machines you own; writing every meeting to disk unasked would undercut that. A
recording has to be switched on in configuration, it lands in one directory
that is easy to find and delete, and the console says when it is happening.

A recorded session is one JSON file: the profile it used, when it ran, the
committed transcript, and the hints with the metrics they came with. JSON
because it is the lossless form; Markdown is rendered from it on demand, since
a format you can regenerate is not worth storing twice.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class HistoryError(Exception):
    code = "history_failed"


@dataclass(frozen=True, slots=True)
class Line:
    lane: str
    text: str
    t: float
    dur: float
    seq: int


@dataclass(frozen=True, slots=True)
class Hint:
    hint_id: str
    trigger: str
    mode: str
    text: str
    reason: str
    #: Transcript time the hint was written at, so an export can put it after
    #: the question it answered rather than in a list at the end.
    at: float = 0.0
    tokens: int | None = None
    ttft_ms: int | None = None


@dataclass
class Recording:
    """One session, accumulating in memory until it is stopped."""

    session_id: str
    profile_id: str
    language: str
    mode: str
    started_at: str
    lines: list[Line] = field(default_factory=list)
    hints: list[Hint] = field(default_factory=list)
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "language": self.language,
            "mode": self.mode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "lines": [asdict(line) for line in self.lines],
            "hints": [asdict(hint) for hint in self.hints],
        }

    @classmethod
    def from_dict(cls, raw: Any, *, source: str = "<dict>") -> Recording:
        if not isinstance(raw, dict):
            raise HistoryError(f"{source}: expected a JSON object")
        try:
            return cls(
                session_id=raw["session_id"],
                profile_id=raw["profile_id"],
                language=raw["language"],
                mode=raw["mode"],
                started_at=raw["started_at"],
                ended_at=raw.get("ended_at"),
                lines=[Line(**line) for line in raw.get("lines", [])],
                hints=[Hint(**hint) for hint in raw.get("hints", [])],
            )
        except (KeyError, TypeError) as exc:
            raise HistoryError(f"{source}: not a readable recording: {exc}") from exc


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def render_markdown(recording: Recording) -> str:
    """A transcript a person can read, or paste into notes.

    Hints are interleaved with the conversation rather than listed at the end:
    read afterwards, what matters is which question a suggestion answered.
    """
    out = [
        f"# Session {recording.session_id}",
        "",
        f"- Profile: `{recording.profile_id}` ({recording.language}, {recording.mode})",
        f"- Started: {recording.started_at}",
        f"- Ended: {recording.ended_at or 'not recorded'}",
        "",
        "## Transcript",
        "",
    ]
    if not recording.lines and not recording.hints:
        out.append("_Nothing was said._")
        return "\n".join(out) + "\n"

    # Sorted by transcript time, with hints after the line they follow: the
    # second element of the key is 0 for speech and 1 for a hint, so a hint
    # written at the same moment lands below the sentence it answers.
    events: list[tuple[float, int, int, object]] = [
        (line.t, 0, index, line) for index, line in enumerate(recording.lines)
    ]
    events += [
        (hint.at, 1, index, hint) for index, hint in enumerate(recording.hints)
    ]

    for *_, event in sorted(events, key=lambda item: item[:3]):
        if isinstance(event, Line):
            who = "**Them:**" if event.lane == "far" else "**You:**"
            out.append(f"{who} {event.text}")
            out.append("")
        else:
            detail = f"{event.trigger}, {event.mode}"
            if event.ttft_ms is not None:
                detail += f", {event.ttft_ms} ms to first token"
            out.append(f"> _Hint ({detail}):_")
            for chunk in event.text.strip().splitlines():
                out.append(f"> {chunk}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


class HistoryStore:
    """Recorded sessions on disk, newest first."""

    def __init__(self, directory: Path | str, enabled: bool = False) -> None:
        self.directory = Path(directory)
        self.enabled = enabled

    def save(self, recording: Recording) -> Path:
        if not self.enabled:
            raise HistoryError("recording is disabled")
        self._check_id(recording.session_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{recording.session_id}.json"
        # Same atomic write as profiles: a crash must not leave half a
        # transcript where a whole one used to be.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            "w", encoding="utf-8", dir=self.directory,
            prefix=f".{recording.session_id}.", suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(recording.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(handle.name)
            raise
        return path

    def load(self, session_id: str) -> Recording:
        self._check_id(session_id)
        path = self.directory / f"{session_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HistoryError(f"no session {session_id!r}") from exc
        except json.JSONDecodeError as exc:
            raise HistoryError(f"{path}: invalid JSON: {exc.msg}") from exc
        return Recording.from_dict(raw, source=str(path))

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summaries, newest first. One unreadable file does not hide the rest."""
        if not self.directory.is_dir():
            return []
        summaries = []
        for path in self.directory.glob("*.json"):
            try:
                recording = self.load(path.stem)
            except HistoryError:
                continue
            summaries.append(
                {
                    "session_id": recording.session_id,
                    "profile_id": recording.profile_id,
                    "started_at": recording.started_at,
                    "ended_at": recording.ended_at,
                    "lines": len(recording.lines),
                    "hints": len(recording.hints),
                }
            )
        return sorted(summaries, key=lambda item: item["started_at"], reverse=True)

    def delete(self, session_id: str) -> None:
        """Remove a recording. Deleting has to be as easy as recording."""
        self._check_id(session_id)
        path = self.directory / f"{session_id}.json"
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise HistoryError(f"no session {session_id!r}") from exc

    @staticmethod
    def _check_id(session_id: str) -> None:
        if (
            "/" in session_id
            or "\\" in session_id
            or session_id in {"", ".", ".."}
        ):
            raise HistoryError(f"invalid session id {session_id!r}")
