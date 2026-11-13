"""Command line entry point for the capture client."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from capture.backends.base import AudioSource, SourceError
from capture.backends.file import WavSource
from capture.client import stream_session
from core.protocol import Lane


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="susurro-capture",
        description="Capture two audio lanes and stream them to a Susurro core.",
    )
    parser.add_argument("--core", default="ws://localhost:8723", help="core WebSocket URL")
    parser.add_argument("--token", default="", help="shared token for the core")
    parser.add_argument("--session-id", default="", help="defaults to a UTC timestamp")
    parser.add_argument("--mic", help="device or node for the microphone lane")
    parser.add_argument(
        "--far", help="device or node for the far lane (the system output monitor)"
    )
    parser.add_argument("--replay-mic", type=Path, help="replay a WAV on the mic lane")
    parser.add_argument("--replay-far", type=Path, help="replay a WAV on the far lane")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="replay speed; 1.0 is real time, 0 is as fast as possible",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="print available audio nodes and exit"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def build_sources(args: argparse.Namespace) -> dict[Lane, AudioSource]:
    sources: dict[Lane, AudioSource] = {}
    if args.replay_mic:
        sources["mic"] = WavSource(args.replay_mic, speed=args.speed)
    if args.replay_far:
        sources["far"] = WavSource(args.replay_far, speed=args.speed)
    if sources:
        return sources

    from capture.backends.pipewire import PipeWireSource

    if args.mic:
        sources["mic"] = PipeWireSource(args.mic, label="mic")
    if args.far:
        sources["far"] = PipeWireSource(args.far, label="far")
    if not sources:
        raise SourceError(
            "nothing to capture: pass --mic/--far for devices, or "
            "--replay-mic/--replay-far for files. Use --list-devices to see "
            "what is available."
        )
    return sources


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    if args.list_devices:
        from capture.backends.pipewire import list_devices

        print(list_devices())
        return 0

    session_id = args.session_id or datetime.now(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        sources = build_sources(args)
        sent = asyncio.run(stream_session(args.core, args.token, session_id, sources))
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    for lane, frames in sent.items():
        print(f"{lane}: {frames} frames sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
