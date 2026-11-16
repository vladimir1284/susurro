"""The whisper.cpp recognizer: what it sends, and what it makes of the answer.

The server is a mock transport. The windowing around it is shared with, and
tested through, ``WhisperRecognizer``.
"""

from __future__ import annotations

import io
import wave

import httpx
import pytest

from core.main import build_recognizer
from core.protocol import FRAME_MS
from core.stt.whispercpp import WhisperCppRecognizer

SILENCE = bytes(640)


def recognizer(handler) -> WhisperCppRecognizer:
    instance = WhisperCppRecognizer(language="es", base_url="http://stt:8081")
    instance._client = httpx.Client(
        base_url="http://stt:8081", transport=httpx.MockTransport(handler)
    )
    return instance


def feed_seconds(instance: WhisperCppRecognizer, seconds: float) -> list:
    out = []
    for _ in range(int(seconds * 1000 / FRAME_MS)):
        out.extend(instance.feed("far", 0, SILENCE))
    return out


def test_is_selected_by_name_and_loads_nothing() -> None:
    chosen = build_recognizer({"backend": "whispercpp", "base_url": "http://box:9"})
    assert isinstance(chosen, WhisperCppRecognizer)
    assert chosen.base_url == "http://box:9"
    assert chosen._client is None


def test_sends_the_window_as_a_wav_in_the_session_language() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen["path"] = request.url.path
        seen["language"] = b'name="language"\r\n\r\nes' in body
        start = body.index(b"RIFF")
        with wave.open(io.BytesIO(body[start:]), "rb") as wav:
            seen["rate"], seen["channels"] = wav.getframerate(), wav.getnchannels()
            seen["frames"] = wav.getnframes()
        return httpx.Response(200, json={"segments": []})

    feed_seconds(recognizer(handler), 1.0)
    assert seen["path"] == "/inference"
    assert seen["language"]
    assert (seen["rate"], seen["channels"]) == (16000, 1)
    assert seen["frames"] == 16000


def test_a_partial_carries_the_servers_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"segments": [{"text": " hola mundo", "end": 1.0}]}
        )

    segments = feed_seconds(recognizer(handler), 1.0)
    assert [(s.text, s.final) for s in segments] == [("hola mundo", False)]


def test_trailing_silence_closes_the_utterance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": [{"text": "listo", "end": 0.2}]})

    segments = feed_seconds(recognizer(handler), 1.0)
    assert [(s.text, s.final) for s in segments] == [("listo", True)]


def test_an_unreachable_server_raises_rather_than_returning_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        feed_seconds(recognizer(handler), 1.0)


def test_warmup_fails_at_session_start_when_the_server_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        recognizer(handler).warmup()


@pytest.mark.parametrize(
    "annotation",
    ["[BLANK_AUDIO]", " [ Silence ]", "(speaking in foreign language)", "(music)", "*coughs*"],
)
def test_non_speech_annotations_are_not_transcript(annotation: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": [{"text": annotation, "end": 1.0}]})

    assert feed_seconds(recognizer(handler), 1.0) == []


def test_speech_next_to_an_annotation_survives() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"segments": [{"text": " [BLANK_AUDIO]", "end": 0.5},
                               {"text": " hola (bien)", "end": 1.0}]},
        )

    segments = feed_seconds(recognizer(handler), 1.0)
    assert [s.text for s in segments] == ["hola (bien)"]


def test_the_language_sent_to_the_server_follows_the_session() -> None:
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(b'name="language"\r\n\r\nen' in request.read())
        return httpx.Response(200, json={"segments": []})

    instance = recognizer(handler)  # built as "es"
    instance.select_language("en")
    feed_seconds(instance, 1.0)
    assert seen == [True]


def test_a_prompt_is_sent_with_every_request_and_only_when_set() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return httpx.Response(200, json={"segments": []})

    plain = recognizer(handler)
    feed_seconds(plain, 1.0)
    primed = recognizer(handler)
    primed.prompt = "Modbus TCP, TimescaleDB"
    feed_seconds(primed, 1.0)

    assert b'name="prompt"' not in bodies[0]
    assert b'name="prompt"\r\n\r\nModbus TCP, TimescaleDB' in bodies[1]
