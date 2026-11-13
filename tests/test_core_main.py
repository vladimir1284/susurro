"""Core entry point: configuration, backend selection, and the bind guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm.fake import CannedHintGenerator
from core.llm.openai_compat import OpenAICompatGenerator
from core.main import build_generator, build_parser, build_recognizer, main, resolve
from core.stt.fake import ScriptedRecognizer
from core.stt.whisper import WhisperRecognizer

EXAMPLE = Path(__file__).resolve().parent.parent / "core.toml.example"


class TestBackendSelection:
    def test_defaults_to_fakes(self) -> None:
        # A core with no configuration must still start, so that the console
        # and the protocol can be worked on without models.
        assert isinstance(build_recognizer({}), ScriptedRecognizer)
        assert isinstance(build_generator({}), CannedHintGenerator)

    def test_whisper_is_selected_by_name(self) -> None:
        recognizer = build_recognizer(
            {"backend": "whisper", "language": "es", "model": "tiny"}
        )
        assert isinstance(recognizer, WhisperRecognizer)
        assert recognizer.model_name == "tiny"
        # Constructing must not load weights; a mistake here would make
        # starting the core take minutes.
        assert recognizer._model is None

    def test_openai_backend_takes_its_url(self) -> None:
        generator = build_generator(
            {"backend": "openai", "base_url": "http://box:9000", "model": "qwen"}
        )
        assert isinstance(generator, OpenAICompatGenerator)
        assert generator.base_url == "http://box:9000"

    def test_a_scripted_recognizer_can_be_configured(self) -> None:
        recognizer = build_recognizer(
            {"backend": "fake", "script": [{"lane": "far", "text": "hello"}]}
        )
        assert recognizer.script[0].text == "hello"

    @pytest.mark.parametrize("builder", [build_recognizer, build_generator])
    def test_an_unknown_backend_fails_loudly(self, builder) -> None:
        with pytest.raises(SystemExit, match="unknown"):
            builder({"backend": "telepathy"})


class TestConfiguration:
    def test_the_example_config_parses_and_selects_real_backends(self) -> None:
        # The shipped example must work; an example that does not is worse
        # than none.
        from core.server import load_config

        config = load_config(EXAMPLE)
        assert isinstance(build_recognizer(config["stt"]), WhisperRecognizer)
        assert isinstance(build_generator(config["llm"]), OpenAICompatGenerator)

    def test_command_line_overrides_the_file(self) -> None:
        args = build_parser().parse_args(["--port", "9999"])
        settings = resolve(args, {"server": {"port": 8723, "host": "10.0.0.1"}})
        assert settings["port"] == 9999
        assert settings["host"] == "10.0.0.1"

    def test_defaults_are_loopback(self) -> None:
        settings = resolve(build_parser().parse_args([]), {})
        assert settings["host"] == "127.0.0.1"


class TestBindGuard:
    @pytest.mark.parametrize("host", ["0.0.0.0", "::"])
    def test_a_public_bind_is_refused_by_default(self, host: str, capsys) -> None:
        # The core authenticates with a shared token in a query parameter.
        # That is enough for a trusted LAN and not enough for anything else.
        assert main(["--host", host]) == 2
        assert "refusing to bind" in capsys.readouterr().err

    def test_a_public_bind_is_allowed_when_asked_for(self, monkeypatch) -> None:
        started: list[tuple] = []

        def fake_run(core, host, port, console_root):
            started.append((host, port))

        monkeypatch.setattr("core.main.run", fake_run)
        monkeypatch.setattr("core.main.asyncio.run", lambda result: result)
        assert main(["--host", "0.0.0.0", "--allow-public-bind", "--port", "1"]) == 0
        assert started == [("0.0.0.0", 1)]
