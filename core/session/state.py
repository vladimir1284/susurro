"""Session state machine.

Pure: no sockets, no models, no I/O. The server owns one of these per session
and asks it what is allowed; keeping the rules here means they can be tested
exhaustively without standing a server up.

Phases:

``IDLE``
    Nothing configured. Only ``session.config`` makes progress.
``CONFIGURED``
    A profile is chosen. The preamble is known but not prefilled.
``LIVE``
    Prefilled, lanes accepted, hints allowed.

``session.config`` is rejected in ``LIVE`` rather than honoured. It decides the
preamble that was prefilled into the prompt cache and the recognizer that was
loaded; accepting a change mid-session would mean silently throwing both away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from core.protocol import Lane, Language, Mode


class SessionPhase(StrEnum):
    IDLE = "idle"
    CONFIGURED = "configured"
    LIVE = "live"


class TransitionError(Exception):
    """A command arrived in a phase that does not allow it."""

    code = "bad_transition"


@dataclass(slots=True)
class SessionState:
    phase: SessionPhase = SessionPhase.IDLE
    profile_id: str | None = None
    language: Language | None = None
    mode: Mode | None = None
    lanes: dict[Lane, str] = field(default_factory=lambda: {"mic": "down", "far": "down"})
    _hint_counter: int = 0
    active_hint: str | None = None

    # -- lifecycle ---------------------------------------------------------- #

    def configure(self, profile_id: str, language: Language, mode: Mode) -> None:
        if self.phase is SessionPhase.LIVE:
            raise TransitionError(
                "session.config is not allowed while live: it decides the prefilled "
                "preamble and the loaded recognizer. Stop the session first."
            )
        self.profile_id = profile_id
        self.language = language
        self.mode = mode
        self.phase = SessionPhase.CONFIGURED

    def start(self) -> None:
        if self.phase is SessionPhase.IDLE:
            raise TransitionError("session.config must arrive before session.start")
        if self.phase is SessionPhase.LIVE:
            raise TransitionError("session is already live")
        self.phase = SessionPhase.LIVE

    def stop(self) -> None:
        if self.phase is not SessionPhase.LIVE:
            raise TransitionError("session is not live")
        self.phase = SessionPhase.CONFIGURED
        self.active_hint = None

    # -- lanes -------------------------------------------------------------- #

    def lane_up(self, lane: Lane) -> None:
        if self.lanes[lane] == "up":
            raise TransitionError(f"lane {lane!r} is already connected")
        self.lanes[lane] = "up"

    def lane_down(self, lane: Lane) -> None:
        self.lanes[lane] = "down"

    @property
    def accepts_audio(self) -> bool:
        return self.phase is SessionPhase.LIVE

    # -- hints -------------------------------------------------------------- #

    def begin_hint(self) -> str:
        """Allocate a hint id, superseding any hint still in flight.

        Superseding rather than refusing is deliberate: a speculative hint is
        often still streaming when the next end-of-turn arrives, and the newer
        question is always the one worth answering.
        """
        if self.phase is not SessionPhase.LIVE:
            raise TransitionError("hints require a live session")
        self._hint_counter += 1
        self.active_hint = f"h-{self._hint_counter}"
        return self.active_hint

    def end_hint(self, hint_id: str) -> bool:
        """Close a hint. False if it had already been superseded or cancelled."""
        if self.active_hint != hint_id:
            return False
        self.active_hint = None
        return True
