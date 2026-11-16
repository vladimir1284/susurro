"""Replay a recorded two-lane call through the core, at real-time pace.

Why this exists: every fake the project has is kinder than a call. A call has
two lanes speaking at once, one of them leaking into the other's microphone, a
model server that gets slower for reasons nobody is watching, and no pause
button. The failures that mattered -- a transcript a minute behind, a lane that
died mid-call -- appear only when both lanes arrive together, on the clock, at
the speed the audio was spoken. This feeds the two recordings that way.

It runs the real routing, voice activity detection and recognizer. Only the
network between a capture client and the core is absent. Hints are not
generated: a hint competes with recognition for the same machine, and this
isolates recognition.

    python -m bench.replay_call SESSION_DIR --start 150 --end 300 \\
        --script bench/fixtures/call_script_es.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from bench.wer import score, term_recall
from core.llm.fake import CannedHintGenerator
from core.main import build_detectors
from core.protocol import FRAME_MS, SAMPLE_RATE
from core.server import Core
from core.session import vocabulary
from core.session.profiles import ProfileStore
from core.stt.base import Recognizer
from core.stt.whispercpp import WhisperCppRecognizer

FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2
PROFILES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "profiles"


@dataclass
class Replay:
    seconds: float
    finals: list[dict] = field(default_factory=list)
    partials: dict[str, int] = field(default_factory=lambda: {"mic": 0, "far": 0})
    #: Per lane, the most the lane fell behind the clock while feeding.
    worst_backlog_s: dict[str, float] = field(default_factory=lambda: {"mic": 0.0, "far": 0.0})

    def lag(self) -> list[float]:
        return [f["lag_s"] for f in self.finals]

    def text(self, lane: str) -> str:
        return " ".join(f["text"] for f in self.finals if f["lane"] == lane)


def load_lane(directory: Path, lane: str, start_s: float, end_s: float) -> bytes:
    with wave.open(str(directory / f"{lane}.wav")) as source:
        source.setpos(int(start_s * SAMPLE_RATE))
        return source.readframes(int((end_s - start_s) * SAMPLE_RATE))


async def replay(
    recognizer: Recognizer, directory: Path, start_s: float, end_s: float,
    end_windows: int = 16,
) -> Replay:
    core = Core(
        profiles=ProfileStore(PROFILES),
        recognizer=recognizer,
        generator=CannedHintGenerator(),
        token="replay",
        detectors=build_detectors(
            {"backend": "silero", "threshold": 0.5, "start_windows": 2, "end_windows": end_windows}
        ),
    )
    audio = {lane: load_lane(directory, lane, start_s, end_s) for lane in ("mic", "far")}
    frames = min(len(pcm) for pcm in audio.values()) // FRAME_BYTES
    result = Replay(seconds=frames * FRAME_MS / 1000)
    origin = time.monotonic()
    emit = core.emit_segment

    async def record(segment) -> None:
        if segment.final:
            elapsed = time.monotonic() - origin
            result.finals.append(
                {
                    "lane": segment.lane,
                    "t": round(segment.t, 2),
                    "dur": round(segment.dur, 2),
                    "text": segment.text,
                    "lag_s": round(elapsed - (segment.t + segment.dur), 2),
                }
            )
        else:
            result.partials[segment.lane] += 1
        await emit(segment)

    core.emit_segment = record

    async def feed(lane: str) -> None:
        for index in range(frames):
            wait = origin + index * FRAME_MS / 1000 - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            else:
                result.worst_backlog_s[lane] = max(result.worst_backlog_s[lane], -wait)
            chunk = audio[lane][index * FRAME_BYTES : (index + 1) * FRAME_BYTES]
            await core.route_audio(lane, chunk)  # type: ignore[arg-type]

    await asyncio.gather(feed("mic"), feed("far"))
    for lane in ("mic", "far"):
        for segment in await core.flush_lane(lane):  # type: ignore[arg-type]
            await core.emit_segment(segment)
    return result


def report(result: Replay, script: dict | None) -> dict:
    lags = result.lag()
    summary: dict = {
        "audio_s_per_lane": round(result.seconds, 1),
        "finals": len(result.finals),
        "partials": result.partials,
        "final_lag_mean_s": round(sum(lags) / len(lags), 1) if lags else None,
        "final_lag_max_s": round(max(lags), 1) if lags else None,
        "worst_backlog_s": {k: round(v, 1) for k, v in result.worst_backlog_s.items()},
    }
    if script:
        for lane in ("far", "mic"):
            scored = score(script[lane], result.text(lane))
            summary[f"wer_{lane}"] = round(scored.wer, 3)
        every = result.text("far") + " " + result.text("mic")
        found = term_recall(script["terms"], every)
        summary["terms_heard"] = f"{sum(found.values())}/{len(found)}"
        summary["terms_missed"] = [t for t, ok in found.items() if not ok]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("session", type=Path, help="directory holding mic.wav and far.wav")
    parser.add_argument("--start", type=float, default=0.0, help="seconds into the recording")
    parser.add_argument("--end", type=float, default=None, help="seconds into the recording")
    parser.add_argument("--language", default="es")
    parser.add_argument("--stt-url", default="http://127.0.0.1:8081")
    parser.add_argument("--prompt", default="", help="initial prompt sent with every request")
    parser.add_argument("--profile", help="profile id whose vocabulary becomes the prompt, as a session would")
    parser.add_argument("--extra-latency", type=float, default=0.0,
                        help="seconds added to every request, to simulate a slower server")
    parser.add_argument("--max-lag", type=float, default=1.5,
                        help="seconds behind real time before partials are shed; 1e9 turns it off")
    parser.add_argument("--partial-interval", type=float, default=1.0,
                        help="seconds of new audio between partial transcriptions")
    parser.add_argument("--end-windows", type=int, default=16,
                        help="32 ms windows of silence that end a turn (16 is about 0.5 s)")
    parser.add_argument("--script", type=Path, help="JSON script to score against")
    parser.add_argument("--out", type=Path, help="write the finals and summary here (new file)")
    args = parser.parse_args(argv)

    with wave.open(str(args.session / "mic.wav")) as probe:
        total = probe.getnframes() / probe.getframerate()
    recognizer = WhisperCppRecognizer(
        language=args.language, base_url=args.stt_url, partial_max_lag_s=args.max_lag,
        partial_interval_s=args.partial_interval, prompt=args.prompt,
    )
    if args.extra_latency:
        inner = recognizer._transcribe

        def slowed(lane):
            result = inner(lane)
            time.sleep(args.extra_latency)
            return result

        recognizer._transcribe = slowed  # type: ignore[method-assign]
    if args.profile:
        profile = ProfileStore(PROFILES).load(args.profile)
        recognizer.select_vocabulary(vocabulary.extract(profile.context))
    recognizer.warmup()

    result = asyncio.run(
        replay(recognizer, args.session, args.start, args.end or total, args.end_windows)
    )
    script = json.loads(args.script.read_text(encoding="utf-8")) if args.script else None
    summary = report(result, script)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        if args.out.exists():
            raise SystemExit(f"{args.out} exists; this tool never overwrites")
        args.out.write_text(
            json.dumps({"summary": summary, "finals": result.finals}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
