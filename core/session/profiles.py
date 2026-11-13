"""Profile loading.

A profile is the static context for a session: the résumé, the role or agenda,
and the operator's own notes, plus the language and mode. It is prefilled into
the prompt cache once at ``session.start`` and never changes while the session
runs.

Profiles are plain JSON files in a directory. There is no database because
there is no scale problem: a user has a handful of profiles, not thousands.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.protocol import LANGUAGES, MODES, Language, Mode

#: Order matters: it is the order the sections are concatenated into the
#: preamble, and the preamble must be byte-stable across a session or the
#: prompt cache is invalidated.
CONTEXT_SECTIONS: tuple[str, ...] = ("resume", "role", "notes")

_SECTION_HEADINGS: dict[str, str] = {
    "resume": "BACKGROUND",
    "role": "THIS SESSION",
    "notes": "NOTES",
}


class ProfileError(Exception):
    """A profile file is missing, unreadable, or does not describe a profile.

    ``code`` is declared rather than defaulted by the caller, so that every
    code reaching the console comes from exactly one place and can be checked
    against the protocol documentation.
    """

    code = "bad_request"


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    label: str
    language: Language
    mode: Mode
    context: dict[str, str]

    def preamble(self) -> str:
        """The static half of the prompt, assembled deterministically.

        Everything the model sees before the transcript lives here. Two calls
        with the same profile must produce byte-identical output: the prefix
        cache is keyed on this text, and a reordered dictionary would silently
        cost a full prefill.
        """
        parts = []
        for section in CONTEXT_SECTIONS:
            body = self.context.get(section, "").strip()
            if body:
                parts.append(f"## {_SECTION_HEADINGS[section]}\n{body}")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "language": self.language,
            "mode": self.mode,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, raw: Any, *, source: str = "<dict>") -> Profile:
        if not isinstance(raw, dict):
            raise ProfileError(f"{source}: expected a JSON object")

        def field(name: str) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value:
                raise ProfileError(f"{source}: {name!r} must be a non-empty string")
            return value

        language = field("language")
        if language not in LANGUAGES:
            raise ProfileError(
                f"{source}: language must be one of {sorted(LANGUAGES)}, got {language!r}"
            )
        mode = field("mode")
        if mode not in MODES:
            raise ProfileError(
                f"{source}: mode must be one of {sorted(MODES)}, got {mode!r}"
            )

        context = raw.get("context")
        if not isinstance(context, dict):
            raise ProfileError(f"{source}: 'context' must be an object")
        unknown = set(context) - set(CONTEXT_SECTIONS)
        if unknown:
            raise ProfileError(f"{source}: unknown context section(s) {sorted(unknown)}")
        for key, value in context.items():
            if not isinstance(value, str):
                raise ProfileError(f"{source}: context.{key} must be a string")
        if not any(value.strip() for value in context.values()):
            raise ProfileError(f"{source}: context is empty")

        return cls(
            id=field("id"),
            label=field("label"),
            language=language,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            context={k: v for k, v in context.items()},
        )


class ProfileStore:
    """Profiles on disk, loaded on demand."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def load(self, profile_id: str) -> Profile:
        self._check_id(profile_id)
        path = self.directory / f"{profile_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProfileError(f"no profile {profile_id!r} in {self.directory}") from exc
        except json.JSONDecodeError as exc:
            raise ProfileError(f"{path}: invalid JSON: {exc.msg}") from exc
        profile = Profile.from_dict(raw, source=str(path))
        if profile.id != profile_id:
            raise ProfileError(
                f"{path}: declares id {profile.id!r} but is filed as {profile_id!r}"
            )
        return profile

    def save(self, profile: Profile) -> Path:
        """Write a profile, replacing any file already there.

        Written to a temporary file in the same directory and renamed over the
        target, so a crash or a full disk leaves the previous profile intact
        rather than a half-written one. A profile is the context a whole
        session is built from; losing it to an interrupted write would be a
        miserable way to start a call.
        """
        self._check_id(profile.id)
        # Round-trip through validation: a profile that cannot be loaded back
        # must not reach the disk.
        Profile.from_dict(profile.to_dict(), source="<save>")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{profile.id}.json"
        # Opened outside a context manager on purpose: the file has to survive
        # being closed so it can be renamed over the target, and it has to be
        # unlinked by hand if anything fails before that. It is still written
        # inside a `with` below.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            "w", encoding="utf-8", dir=self.directory, prefix=f".{profile.id}.",
            suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(profile.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(handle.name)
            raise
        return path

    @staticmethod
    def _check_id(profile_id: str) -> None:
        # Profile ids come from the console and index the filesystem, so an id
        # containing a path separator must not be able to walk out of the
        # directory. This matters more now that ids also name files to write.
        if "/" in profile_id or "\\" in profile_id or profile_id in {"", ".", ".."}:
            raise ProfileError(f"invalid profile id {profile_id!r}")

    def list_ids(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))
