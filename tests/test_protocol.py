"""Protocol tests.

The protocol is the contract a second capture client will be written against, so
these tests lean on rejection cases rather than happy paths: a parser that
accepts a malformed hello is how a client ships a bug that only shows up as
desynchronised audio an hour into a call.
"""

from __future__ import annotations

import json

import pytest

from core import protocol as p


def hello_obj(**overrides: object) -> dict[str, object]:
    obj: dict[str, object] = {
        "type": "hello",
        "protocol": p.PROTOCOL_VERSION,
        "session_id": "2026-11-13T14:02:11Z-a4f1",
        "lane": "mic",
        "codec": "opus",
        "sample_rate": 16000,
        "frame_ms": 20,
        "client": "susurro-capture-linux/0.1.0",
    }
    obj.update(overrides)
    return obj


class TestHello:
    def test_round_trip(self) -> None:
        original = p.Hello(
            session_id="s-1", lane="far", client="susurro-capture-macos/0.1.0"
        )
        assert p.Hello.from_json(original.to_json()) == original

    def test_parses_a_well_formed_hello(self) -> None:
        hello = p.Hello.from_json(json.dumps(hello_obj()))
        assert hello.lane == "mic"
        assert hello.sample_rate == p.SAMPLE_RATE

    @pytest.mark.parametrize("lane", ["mic", "far"])
    def test_accepts_both_lanes(self, lane: str) -> None:
        assert p.Hello.from_json(json.dumps(hello_obj(lane=lane))).lane == lane

    def test_rejects_an_unknown_lane(self) -> None:
        with pytest.raises(p.MalformedMessage, match="lane"):
            p.Hello.from_json(json.dumps(hello_obj(lane="both")))

    def test_rejects_a_future_protocol_version(self) -> None:
        with pytest.raises(p.UnsupportedVersion, match="not supported"):
            p.Hello.from_json(json.dumps(hello_obj(protocol=2)))

    def test_version_is_checked_before_the_body(self) -> None:
        # A newer client may legitimately send fields this parser rejects.
        # "your client is too new" beats a complaint about an unknown field.
        raw = json.dumps(hello_obj(protocol=99, something_new=True))
        with pytest.raises(p.UnsupportedVersion):
            p.Hello.from_json(raw)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"codec": "pcm"},
            {"sample_rate": 48000},
            {"frame_ms": 40},
        ],
        ids=["codec", "sample_rate", "frame_ms"],
    )
    def test_audio_format_is_fixed_not_negotiated(self, overrides: dict) -> None:
        with pytest.raises(p.MalformedMessage):
            p.Hello.from_json(json.dumps(hello_obj(**overrides)))

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown field"):
            p.Hello.from_json(json.dumps(hello_obj(bitrate=24000)))

    @pytest.mark.parametrize("missing", ["protocol", "session_id", "lane", "client"])
    def test_rejects_missing_required_fields(self, missing: str) -> None:
        obj = hello_obj()
        del obj[missing]
        with pytest.raises(p.MalformedMessage, match="missing required field"):
            p.Hello.from_json(json.dumps(obj))

    @pytest.mark.parametrize("empty", ["session_id", "client"])
    def test_rejects_empty_strings(self, empty: str) -> None:
        with pytest.raises(p.MalformedMessage, match="must not be empty"):
            p.Hello.from_json(json.dumps(hello_obj(**{empty: ""})))

    def test_rejects_a_bool_where_a_number_belongs(self) -> None:
        with pytest.raises(p.MalformedMessage, match="bool"):
            p.Hello.from_json(json.dumps(hello_obj(protocol=True)))

    def test_rejects_a_non_hello_first_message(self) -> None:
        with pytest.raises(p.MalformedMessage, match="must be a hello"):
            p.Hello.from_json(json.dumps(hello_obj(type="session.start")))

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(p.MalformedMessage, match="invalid JSON"):
            p.Hello.from_json("{not json")

    def test_rejects_a_json_array(self) -> None:
        with pytest.raises(p.MalformedMessage, match="expected a JSON object"):
            p.Hello.from_json("[]")


class TestAudioFrame:
    def test_round_trip(self) -> None:
        frame = p.AudioFrame(timestamp_us=1_234_567, payload=b"\x01\x02\x03")
        assert p.AudioFrame.decode(frame.encode()) == frame

    def test_timestamp_is_little_endian_unsigned_64_bit(self) -> None:
        encoded = p.AudioFrame(timestamp_us=1, payload=b"\xff").encode()
        assert encoded[:8] == b"\x01\x00\x00\x00\x00\x00\x00\x00"
        assert encoded[8:] == b"\xff"

    @pytest.mark.parametrize("timestamp", [0, 1, p.MAX_TIMESTAMP_US])
    def test_accepts_the_full_unsigned_range(self, timestamp: int) -> None:
        frame = p.AudioFrame(timestamp_us=timestamp, payload=b"x")
        assert p.AudioFrame.decode(frame.encode()).timestamp_us == timestamp

    @pytest.mark.parametrize("timestamp", [-1, p.MAX_TIMESTAMP_US + 1])
    def test_rejects_timestamps_outside_the_range(self, timestamp: int) -> None:
        with pytest.raises(p.MalformedFrame, match="out of range"):
            p.AudioFrame(timestamp_us=timestamp, payload=b"x")

    def test_rejects_an_empty_payload(self) -> None:
        with pytest.raises(p.MalformedFrame, match="empty payload"):
            p.AudioFrame(timestamp_us=0, payload=b"")

    def test_rejects_an_oversized_payload(self) -> None:
        with pytest.raises(p.MalformedFrame, match="over the"):
            p.AudioFrame(timestamp_us=0, payload=b"x" * (p.MAX_PAYLOAD_BYTES + 1))

    @pytest.mark.parametrize("size", [0, 1, p.TIMESTAMP_BYTES])
    def test_rejects_a_frame_with_no_payload_bytes(self, size: int) -> None:
        with pytest.raises(p.MalformedFrame, match="needs more than"):
            p.AudioFrame.decode(b"\x00" * size)

    def test_decode_copies_the_payload(self) -> None:
        buffer = bytearray(p.AudioFrame(timestamp_us=7, payload=b"ab").encode())
        frame = p.AudioFrame.decode(bytes(buffer))
        buffer[8] = 0x00
        assert frame.payload == b"ab"


class TestConsoleEvents:
    def test_transcript_partial(self) -> None:
        event = json.loads(p.transcript_partial("far", "so tell me about", 12.4))
        assert event == {
            "type": "transcript.partial",
            "lane": "far",
            "text": "so tell me about",
            "t": 12.4,
        }

    def test_transcript_final_carries_a_sequence_number(self) -> None:
        event = json.loads(p.transcript_final("mic", "I did.", 12.4, 3.1, 41))
        assert event["type"] == "transcript.final"
        assert event["seq"] == 41
        assert event["dur"] == 3.1

    @pytest.mark.parametrize(
        "kwargs", [{"seq": -1, "dur": 1.0}, {"seq": 0, "dur": -1.0}]
    )
    def test_transcript_final_rejects_negative_numbers(self, kwargs: dict) -> None:
        with pytest.raises(p.MalformedMessage):
            p.transcript_final("mic", "x", 0.0, kwargs["dur"], kwargs["seq"])

    def test_transcript_rejects_an_unknown_lane(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown lane"):
            p.transcript_partial("both", "x", 0.0)  # type: ignore[arg-type]

    def test_hint_start(self) -> None:
        event = json.loads(p.hint_start("h-93", "speculative", "conversational"))
        assert event["trigger"] == "speculative"
        assert event["mode"] == "conversational"

    @pytest.mark.parametrize(
        "args",
        [("h", "guessed", "technical"), ("h", "manual", "poetic")],
        ids=["trigger", "mode"],
    )
    def test_hint_start_rejects_unknown_values(self, args: tuple) -> None:
        with pytest.raises(p.MalformedMessage):
            p.hint_start(*args)  # type: ignore[arg-type]

    def test_hint_end_omits_absent_metrics(self) -> None:
        event = json.loads(p.hint_end("h-93", "cancelled"))
        assert event == {"type": "hint.end", "hint_id": "h-93", "reason": "cancelled"}

    def test_hint_end_carries_metrics_when_given(self) -> None:
        event = json.loads(p.hint_end("h-93", "complete", tokens=38, ttft_ms=780))
        assert event["tokens"] == 38
        assert event["ttft_ms"] == 780

    def test_hint_end_rejects_an_unknown_reason(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown hint end reason"):
            p.hint_end("h-93", "gave_up")  # type: ignore[arg-type]

    def test_state(self) -> None:
        event = json.loads(
            p.state("live", "ready", "ready", {"mic": "up", "far": "down"})
        )
        assert event["lanes"] == {"mic": "up", "far": "down"}

    def test_state_rejects_an_unknown_lane(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown lane"):
            p.state("live", "ready", "ready", {"mic": "up", "ambient": "up"})

    def test_error_for_carries_the_exception_code(self) -> None:
        event = json.loads(p.error_for(p.UnsupportedVersion("too new")))
        assert event == {
            "type": "error",
            "code": "unsupported_version",
            "detail": "too new",
        }

    def test_ready_announces_the_version(self) -> None:
        assert json.loads(p.ready()) == {
            "type": "ready",
            "protocol": p.PROTOCOL_VERSION,
        }


class TestConsoleCommands:
    def test_hint_request(self) -> None:
        command = p.parse_console_command('{"type":"hint.request","mode":"technical"}')
        assert command == p.HintRequest(mode="technical")

    def test_hint_cancel(self) -> None:
        command = p.parse_console_command('{"type":"hint.cancel","hint_id":"h-93"}')
        assert command == p.HintCancel(hint_id="h-93")

    def test_session_config(self) -> None:
        command = p.parse_console_command(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "backend-role-acme",
                    "language": "es",
                    "mode": "business",
                }
            )
        )
        assert command == p.SessionConfig(
            profile_id="backend-role-acme", language="es", mode="business"
        )

    @pytest.mark.parametrize(
        "kind, expected",
        [("session.start", p.SessionStart()), ("session.stop", p.SessionStop())],
    )
    def test_session_lifecycle_commands(self, kind: str, expected: object) -> None:
        assert p.parse_console_command(json.dumps({"type": kind})) == expected

    @pytest.mark.parametrize("language", ["en", "es"])
    def test_both_session_languages_are_accepted(self, language: str) -> None:
        command = p.parse_console_command(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "p",
                    "language": language,
                    "mode": "conversational",
                }
            )
        )
        assert isinstance(command, p.SessionConfig)
        assert command.language == language

    def test_rejects_a_language_outside_the_supported_set(self) -> None:
        # Sessions are single-language by design; an unsupported value must fail
        # loudly rather than fall back to a default recognizer.
        with pytest.raises(p.MalformedMessage, match="language"):
            p.parse_console_command(
                json.dumps(
                    {
                        "type": "session.config",
                        "profile_id": "p",
                        "language": "fr",
                        "mode": "conversational",
                    }
                )
            )

    def test_rejects_an_unknown_command(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown console command"):
            p.parse_console_command('{"type":"session.pause"}')

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown field"):
            p.parse_console_command('{"type":"hint.cancel","hint_id":"h","why":"x"}')

    def test_rejects_an_empty_identifier(self) -> None:
        with pytest.raises(p.MalformedMessage, match="must not be empty"):
            p.parse_console_command('{"type":"hint.cancel","hint_id":""}')

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(p.MalformedMessage, match="invalid JSON"):
            p.parse_console_command("not json")


class TestDocumentedExamples:
    """The examples in docs/architecture/protocol.md must actually parse."""

    def test_capture_hello_example(self) -> None:
        hello = p.Hello.from_json(
            json.dumps(
                {
                    "type": "hello",
                    "protocol": 1,
                    "session_id": "2026-11-13T14:02:11Z-a4f1",
                    "lane": "mic",
                    "codec": "opus",
                    "sample_rate": 16000,
                    "frame_ms": 20,
                    "client": "susurro-capture-linux/0.1.0",
                }
            )
        )
        assert hello.session_id == "2026-11-13T14:02:11Z-a4f1"

    @pytest.mark.parametrize(
        "raw",
        [
            '{"type":"hint.request","mode":"conversational"}',
            '{"type":"hint.cancel","hint_id":"h-93"}',
            '{"type":"session.config","profile_id":"backend-role-acme",'
            '"language":"en","mode":"conversational"}',
            '{"type":"session.start"}',
            '{"type":"session.stop"}',
        ],
    )
    def test_console_command_examples(self, raw: str) -> None:
        assert p.parse_console_command(raw) is not None


class TestProfileListing:
    """A console should not need to be told profile ids out of band."""

    def test_builds_a_listing(self) -> None:
        import json as _json

        event = _json.loads(
            p.profiles(
                [{"id": "a", "label": "A role", "language": "en", "mode": "technical"}]
            )
        )
        assert event["type"] == "profiles"
        assert event["profiles"][0]["label"] == "A role"

    def test_an_empty_listing_is_valid(self) -> None:
        import json as _json

        assert _json.loads(p.profiles([]))["profiles"] == []

    @pytest.mark.parametrize("missing", ["id", "label", "language", "mode"])
    def test_an_incomplete_entry_is_refused(self, missing: str) -> None:
        # The console renders every field. A listing missing one produces an
        # option labelled "undefined", which is worse than an error.
        entry = {"id": "a", "label": "A", "language": "en", "mode": "technical"}
        del entry[missing]
        with pytest.raises(p.MalformedMessage, match=missing):
            p.profiles([entry])

    def test_the_request_parses(self) -> None:
        assert p.parse_console_command('{"type":"profiles.request"}') == (
            p.ProfilesRequest()
        )

    def test_the_request_takes_no_arguments(self) -> None:
        with pytest.raises(p.MalformedMessage, match="unknown field"):
            p.parse_console_command('{"type":"profiles.request","language":"en"}')
