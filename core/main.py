"""Command line entry point for the core.

Backends are chosen by configuration, not by import. That is what lets the same
server run against fakes on a laptop with no models and against real ones on the
machine that has them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
from pathlib import Path
from typing import Any

from core.bleed import BleedGate
from core.llm.base import HintGenerator
from core.server import CONSOLE_ROOT, DEFAULT_PORT, Core, load_config, run
from core.session.history import HistoryStore
from core.session.profiles import ProfileStore
from core.stt.base import Recognizer
from core.vad import Detector, build_detector

log = logging.getLogger("susurro.core")

DEFAULT_PROFILES = Path("profiles")


def build_recognizer(config: dict[str, Any]) -> Recognizer:
    kind = config.get("backend", "fake")
    if kind == "fake":
        from core.stt.fake import ScriptedRecognizer, ScriptedUtterance

        script = [
            ScriptedUtterance(lane=item["lane"], text=item["text"],
                              frames=item.get("frames", 25))
            for item in config.get("script", [])
        ]
        return ScriptedRecognizer(script=script, language=config.get("language", "en"))
    if kind == "whisper":
        from core.stt.whisper import WhisperRecognizer

        return WhisperRecognizer(
            language=config.get("language", "en"),
            model_size=config.get("model"),
            device=config.get("device", "auto"),
            compute_type=config.get("compute_type", "int8"),
        )
    if kind == "whispercpp":
        from core.stt.whispercpp import WhisperCppRecognizer

        return WhisperCppRecognizer(
            language=config.get("language", "en"),
            base_url=config.get("base_url", "http://127.0.0.1:8081"),
        )
    raise SystemExit(
        f"unknown stt backend {kind!r}: expected 'fake', 'whisper' or 'whispercpp'"
    )


def build_generator(config: dict[str, Any]) -> HintGenerator:
    kind = config.get("backend", "fake")
    if kind == "fake":
        from core.llm.fake import CannedHintGenerator

        return CannedHintGenerator(chunk_delay_s=config.get("chunk_delay_s", 0.05))
    if kind == "openai":
        from core.llm.openai_compat import OpenAICompatGenerator

        return OpenAICompatGenerator(
            base_url=config.get("base_url", "http://127.0.0.1:8080"),
            model=config.get("model", "local"),
            api_key=config.get("api_key", ""),
            temperature=config.get("temperature", 0.3),
        )
    raise SystemExit(f"unknown llm backend {kind!r}: expected 'fake' or 'openai'")


def build_detectors(config: dict[str, Any]) -> dict[str, Detector] | None:
    """One detector per lane, or None when end-of-turn detection is off.

    Detectors carry state across windows, so the lanes cannot share one.
    """
    far = build_detector(config)
    if far is None:
        return None
    return {"far": far, "mic": build_detector(config)}


def build_bleed(config: dict[str, Any]) -> BleedGate | None:
    """The loudspeaker rule for the mic lane. ``bleed_ratio = 0`` turns it off."""
    ratio = float(config.get("bleed_ratio", BleedGate.ratio))
    return BleedGate(ratio=ratio) if ratio > 0 else None


def build_history(config: dict[str, Any]) -> HistoryStore:
    """Recorded sessions. Off unless asked for.

    The project's argument is that a conversation stays on machines you own.
    Writing every meeting to disk by default would undercut it, so this
    defaults to disabled and the console shows when it is not.
    """
    return HistoryStore(
        directory=Path(config.get("directory", "sessions")),
        enabled=bool(config.get("record", False)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="susurro-core",
        description="Run the Susurro core: speech recognition, hints, and the console.",
    )
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    parser.add_argument("--host", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help=f"port (default: {DEFAULT_PORT})")
    parser.add_argument("--token", help="shared token; generated if absent")
    parser.add_argument("--profiles", type=Path, help="directory of profile JSON files")
    parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help="permit binding 0.0.0.0; refused otherwise",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def resolve(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    server = config.get("server", {})
    return {
        "host": args.host or server.get("host", "127.0.0.1"),
        "port": args.port or server.get("port", DEFAULT_PORT),
        "token": args.token or server.get("token", ""),
        "profiles": args.profiles or Path(server.get("profiles", DEFAULT_PROFILES)),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    config = load_config(args.config)
    settings = resolve(args, config)

    if settings["host"] in ("0.0.0.0", "::") and not args.allow_public_bind:
        print(
            "refusing to bind "
            f"{settings['host']}: the core answers on a shared token in a query "
            "parameter, which is not an authentication scheme to expose to a "
            "network you do not control. Pass --allow-public-bind if you meant it.",
            file=sys.stderr,
        )
        return 2

    token = settings["token"] or secrets.token_urlsafe(16)
    if not settings["token"]:
        log.warning("no token configured; generated one for this run")

    core = Core(
        profiles=ProfileStore(settings["profiles"]),
        recognizer=build_recognizer(config.get("stt", {})),
        generator=build_generator(config.get("llm", {})),
        token=token,
        detectors=build_detectors(config.get("vad", {})),
        bleed=build_bleed(config.get("vad", {})),
        history=build_history(config.get("session", {})),
    )

    url = f"http://{settings['host']}:{settings['port']}/?token={token}"
    print(f"console: {url}")
    print(f"profiles: {settings['profiles']} {core.profiles.list_ids()}")
    try:
        asyncio.run(run(core, settings["host"], settings["port"], CONSOLE_ROOT))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
