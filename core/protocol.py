"""Wire protocol for Susurro.

Two WebSocket endpoints, documented in `docs/architecture/protocol.md`:

* ``/capture`` — one connection per audio lane. A JSON hello, then binary audio
  frames.
* ``/console`` — bidirectional JSON. Transcript and hint events out, commands in.

This module is pure: it parses, validates and builds messages, and knows nothing
about sockets, sessions or models. That split exists so the protocol can be
tested exhaustively without standing anything up, and so a second capture client
can be written against a specification rather than against the server's
behaviour.

Validation is strict on purpose. Unknown fields and out-of-range values are
rejected rather than ignored, because the alternative is a capture client that
appears to work for an hour and then desynchronises in a way nobody can trace.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any, Final, Literal

PROTOCOL_VERSION: Final = 1

Lane = Literal["mic", "far"]
Language = Literal["en", "es"]
Mode = Literal["conversational", "technical", "business"]
HintTrigger = Literal["manual", "speculative"]
HintEndReason = Literal["complete", "cancelled", "error"]

LANES: Final[frozenset[str]] = frozenset({"mic", "far"})
LANGUAGES: Final[frozenset[str]] = frozenset({"en", "es"})
MODES: Final[frozenset[str]] = frozenset({"conversational", "technical", "business"})
HINT_TRIGGERS: Final[frozenset[str]] = frozenset({"manual", "speculative"})
HINT_END_REASONS: Final[frozenset[str]] = frozenset({"complete", "cancelled", "error"})

#: The only audio format the core accepts. Both values are fixed rather than
#: negotiated: 16 kHz is what the speech models consume, and a 20 ms frame is
#: what the voice activity detector steps on.
CODEC: Final = "opus"
SAMPLE_RATE: Final = 16_000
FRAME_MS: Final = 20

#: Timestamp plus Opus packet. An Opus frame at this rate is a few hundred bytes;
#: the cap exists to make a desynchronised stream fail loudly and early.
TIMESTAMP_STRUCT: Final = struct.Struct("<Q")
TIMESTAMP_BYTES: Final = TIMESTAMP_STRUCT.size
MAX_PAYLOAD_BYTES: Final = 4_000
MAX_TIMESTAMP_US: Final = 2**64 - 1


class ProtocolError(Exception):
    """Base class for anything the peer did wrong.

    ``code`` is what travels back in an ``error`` event, so it is part of the
    protocol surface and should stay stable.
    """

    code = "protocol_error"


class UnsupportedVersion(ProtocolError):
    code = "unsupported_version"


class MalformedMessage(ProtocolError):
    code = "malformed_message"


class MalformedFrame(ProtocolError):
    code = "malformed_frame"


def _require_mapping(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MalformedMessage(f"expected a JSON object, got {type(raw).__name__}")
    return raw


def _require(obj: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in obj:
        raise MalformedMessage(f"missing required field {key!r}")
    value = obj[key]
    # bool is a subclass of int; a boolean where a number belongs is a bug.
    if isinstance(value, bool) and kind is int:
        raise MalformedMessage(f"field {key!r} must be an int, got bool")
    if not isinstance(value, kind):
        raise MalformedMessage(f"field {key!r} has wrong type {type(value).__name__}")
    return value


def _require_choice(obj: dict[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _require(obj, key, str)
    if value not in allowed:
        raise MalformedMessage(
            f"field {key!r} must be one of {sorted(allowed)}, got {value!r}"
        )
    return value


def _reject_unknown(obj: dict[str, Any], known: frozenset[str], where: str) -> None:
    unknown = set(obj) - known
    if unknown:
        raise MalformedMessage(f"unknown field(s) in {where}: {sorted(unknown)}")


# --------------------------------------------------------------------------- #
# Capture endpoint
# --------------------------------------------------------------------------- #

_HELLO_FIELDS: Final = frozenset(
    {"type", "protocol", "session_id", "lane", "codec", "sample_rate", "frame_ms", "client"}
)


@dataclass(frozen=True, slots=True)
class Hello:
    """First message on a ``/capture`` connection. Establishes the lane."""

    session_id: str
    lane: Lane
    client: str
    protocol: int = PROTOCOL_VERSION
    codec: str = CODEC
    sample_rate: int = SAMPLE_RATE
    frame_ms: int = FRAME_MS

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": "hello",
                "protocol": self.protocol,
                "session_id": self.session_id,
                "lane": self.lane,
                "codec": self.codec,
                "sample_rate": self.sample_rate,
                "frame_ms": self.frame_ms,
                "client": self.client,
            }
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> Hello:
        try:
            obj = _require_mapping(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise MalformedMessage(f"invalid JSON: {exc.msg}") from exc

        if _require(obj, "type", str) != "hello":
            raise MalformedMessage("first capture message must be a hello")

        # Version is checked before anything else about the body, unknown fields
        # included: a future version may legitimately carry fields this parser
        # would reject, and "your client is too new" is a far more useful error
        # than a complaint about an unknown field.
        version = _require(obj, "protocol", int)
        if version != PROTOCOL_VERSION:
            raise UnsupportedVersion(
                f"protocol version {version} is not supported; core speaks {PROTOCOL_VERSION}"
            )

        _reject_unknown(obj, _HELLO_FIELDS, "hello")

        session_id = _require(obj, "session_id", str)
        if not session_id:
            raise MalformedMessage("session_id must not be empty")

        lane = _require_choice(obj, "lane", LANES)

        codec = _require(obj, "codec", str)
        if codec != CODEC:
            raise MalformedMessage(f"codec must be {CODEC!r}, got {codec!r}")

        sample_rate = _require(obj, "sample_rate", int)
        if sample_rate != SAMPLE_RATE:
            raise MalformedMessage(f"sample_rate must be {SAMPLE_RATE}, got {sample_rate}")

        frame_ms = _require(obj, "frame_ms", int)
        if frame_ms != FRAME_MS:
            raise MalformedMessage(f"frame_ms must be {FRAME_MS}, got {frame_ms}")

        client = _require(obj, "client", str)
        if not client:
            raise MalformedMessage("client must not be empty")

        return cls(
            session_id=session_id,
            lane=lane,  # type: ignore[arg-type]
            client=client,
            protocol=version,
            codec=codec,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
        )


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One 20 ms Opus packet, stamped by the client's monotonic clock.

    The timestamp is the client's, not the core's. The core uses it to order
    packets within a lane and to measure jitter; it never assumes two clients
    agree on a clock. See the open question in
    ``docs/architecture/protocol.md``.
    """

    timestamp_us: int
    payload: bytes

    def __post_init__(self) -> None:
        if self.timestamp_us < 0 or self.timestamp_us > MAX_TIMESTAMP_US:
            raise MalformedFrame(f"timestamp {self.timestamp_us} out of range")
        if not self.payload:
            raise MalformedFrame("empty payload")
        if len(self.payload) > MAX_PAYLOAD_BYTES:
            raise MalformedFrame(
                f"payload is {len(self.payload)} bytes, over the {MAX_PAYLOAD_BYTES} cap"
            )

    def encode(self) -> bytes:
        return TIMESTAMP_STRUCT.pack(self.timestamp_us) + self.payload

    @classmethod
    def decode(cls, data: bytes) -> AudioFrame:
        if len(data) <= TIMESTAMP_BYTES:
            raise MalformedFrame(
                f"frame is {len(data)} bytes, needs more than {TIMESTAMP_BYTES}"
            )
        (timestamp_us,) = TIMESTAMP_STRUCT.unpack_from(data)
        return cls(timestamp_us=timestamp_us, payload=bytes(data[TIMESTAMP_BYTES:]))


def ready() -> str:
    """Core's answer to a hello it accepted."""
    return json.dumps({"type": "ready", "protocol": PROTOCOL_VERSION})


# --------------------------------------------------------------------------- #
# Console endpoint — events out
# --------------------------------------------------------------------------- #


def transcript_partial(lane: Lane, text: str, t: float) -> str:
    """Current best guess for an open segment. Replaced on every pass.

    Partials never reach the language model: the prompt is append-only, and
    rewriting earlier tokens would invalidate the prefix cache.
    """
    _check_lane(lane)
    return json.dumps({"type": "transcript.partial", "lane": lane, "text": text, "t": t})


def transcript_final(lane: Lane, text: str, t: float, dur: float, seq: int) -> str:
    """Committed text for a closed segment. Never revised."""
    _check_lane(lane)
    if seq < 0:
        raise MalformedMessage(f"seq must not be negative, got {seq}")
    if dur < 0:
        raise MalformedMessage(f"dur must not be negative, got {dur}")
    return json.dumps(
        {
            "type": "transcript.final",
            "lane": lane,
            "text": text,
            "t": t,
            "dur": dur,
            "seq": seq,
        }
    )


def hint_start(hint_id: str, trigger: HintTrigger, mode: Mode) -> str:
    if trigger not in HINT_TRIGGERS:
        raise MalformedMessage(f"unknown hint trigger {trigger!r}")
    if mode not in MODES:
        raise MalformedMessage(f"unknown mode {mode!r}")
    return json.dumps(
        {"type": "hint.start", "hint_id": hint_id, "trigger": trigger, "mode": mode}
    )


def hint_delta(hint_id: str, text: str) -> str:
    return json.dumps({"type": "hint.delta", "hint_id": hint_id, "text": text})


def hint_end(
    hint_id: str,
    reason: HintEndReason,
    *,
    tokens: int | None = None,
    ttft_ms: int | None = None,
) -> str:
    """End of a hint.

    ``reason="cancelled"`` is the normal outcome of a speculative generation the
    other party interrupted, not an error. The console discards the partial hint
    instead of showing it.
    """
    if reason not in HINT_END_REASONS:
        raise MalformedMessage(f"unknown hint end reason {reason!r}")
    event: dict[str, Any] = {"type": "hint.end", "hint_id": hint_id, "reason": reason}
    if tokens is not None:
        event["tokens"] = tokens
    if ttft_ms is not None:
        event["ttft_ms"] = ttft_ms
    return json.dumps(event)


def state(session: str, stt: str, llm: str, lanes: dict[str, str]) -> str:
    unknown = set(lanes) - LANES
    if unknown:
        raise MalformedMessage(f"unknown lane(s) {sorted(unknown)}")
    return json.dumps(
        {"type": "state", "session": session, "stt": stt, "llm": llm, "lanes": lanes}
    )


def error(code: str, detail: str) -> str:
    return json.dumps({"type": "error", "code": code, "detail": detail})


def error_for(exc: ProtocolError) -> str:
    return error(exc.code, str(exc))


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise MalformedMessage(f"unknown lane {lane!r}")


# --------------------------------------------------------------------------- #
# Console endpoint — commands in
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HintRequest:
    mode: Mode


@dataclass(frozen=True, slots=True)
class HintCancel:
    hint_id: str


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Static context for a session.

    Must arrive before :class:`SessionStart`: it decides the preamble that gets
    prefilled into the prompt cache, and the recognizer that gets loaded. The
    server rejects it mid-session rather than honouring it silently.
    """

    profile_id: str
    language: Language
    mode: Mode


@dataclass(frozen=True, slots=True)
class SessionStart:
    pass


@dataclass(frozen=True, slots=True)
class SessionStop:
    pass


ConsoleCommand = (
    HintRequest | HintCancel | SessionConfig | SessionStart | SessionStop
)

_COMMAND_FIELDS: Final[dict[str, frozenset[str]]] = {
    "hint.request": frozenset({"type", "mode"}),
    "hint.cancel": frozenset({"type", "hint_id"}),
    "session.config": frozenset({"type", "profile_id", "language", "mode"}),
    "session.start": frozenset({"type"}),
    "session.stop": frozenset({"type"}),
}


def parse_console_command(raw: str | bytes) -> ConsoleCommand:
    try:
        obj = _require_mapping(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise MalformedMessage(f"invalid JSON: {exc.msg}") from exc

    kind = _require(obj, "type", str)
    if kind not in _COMMAND_FIELDS:
        raise MalformedMessage(f"unknown console command {kind!r}")
    _reject_unknown(obj, _COMMAND_FIELDS[kind], kind)

    if kind == "hint.request":
        return HintRequest(mode=_require_choice(obj, "mode", MODES))  # type: ignore[arg-type]
    if kind == "hint.cancel":
        hint_id = _require(obj, "hint_id", str)
        if not hint_id:
            raise MalformedMessage("hint_id must not be empty")
        return HintCancel(hint_id=hint_id)
    if kind == "session.config":
        profile_id = _require(obj, "profile_id", str)
        if not profile_id:
            raise MalformedMessage("profile_id must not be empty")
        return SessionConfig(
            profile_id=profile_id,
            language=_require_choice(obj, "language", LANGUAGES),  # type: ignore[arg-type]
            mode=_require_choice(obj, "mode", MODES),  # type: ignore[arg-type]
        )
    if kind == "session.start":
        return SessionStart()
    return SessionStop()
