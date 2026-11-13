"""M0: measure the machine before writing the code that assumes an answer.

Everything in `docs/architecture/inference.md` is currently an estimate derived
from published specifications. This replaces those estimates with measurements,
on the machine that will actually run the thing.

What it measures, and why each one matters:

``stt_realtime_factor``
    Seconds of compute per second of audio. At or above 1.0 the transcript
    falls behind and never catches up.
``cold_prefill_s``
    Processing a full prompt from scratch. The cost the prompt cache exists to
    avoid, measured at 1k and 4k tokens.
``warm_ttft_s``
    Time to first token when the static context is already cached and only the
    transcript delta is new. **This is the number the product lives on.**
``decode_tokens_s``
    Sustained generation rate. Decides how long a hint takes to finish
    streaming, and therefore how long a hint is allowed to be.
``resident_memory_gb``
    Peak memory with both models loaded. Decides whether the 8B assumption
    holds.

Both runtimes worth testing speak the OpenAI API, so there is one client rather
than two code paths:

    llama.cpp   llama-server -m model.gguf -c 8192 --port 8080
    MLX         mlx_lm.server --model <repo> --port 8080

Run each in turn and pass ``--label`` so the result files can be compared.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from bench.metrics import Samples, machine, peak_rss_gb
from bench.report import Report

#: Roughly four characters per token for English prose. Crude, and enough: the
#: point is to send a prompt of about the right size, not to count exactly.
CHARS_PER_TOKEN = 4

FILLER = (
    "The candidate replaced a legacy pricing engine without a big-bang rewrite, "
    "running both implementations against live traffic and comparing outputs "
    "nightly until the residual difference held under a tenth of a percent. "
)


def context_of(tokens: int) -> str:
    """A prompt of approximately ``tokens`` tokens."""
    target = tokens * CHARS_PER_TOKEN
    repeats = max(1, target // len(FILLER) + 1)
    return (FILLER * repeats)[:target]


@dataclass
class LlmBench:
    base_url: str
    model: str
    repeats: int = 5

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url.rstrip("/"), timeout=300.0)

    async def ttft(self, client: httpx.AsyncClient, system: str, user: str) -> float:
        """Seconds until the first content token arrives."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 16,
            "temperature": 0.0,
            "stream": True,
        }
        started = time.perf_counter()
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:") and '"content"' in line:
                    return time.perf_counter() - started
        raise RuntimeError("stream produced no content")

    async def decode_rate(self, client: httpx.AsyncClient, system: str) -> float:
        """Tokens per second after the first one, over a longer generation."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "Summarise the above in detail."},
            ],
            "max_tokens": 200,
            "temperature": 0.0,
            "stream": True,
        }
        first: float | None = None
        chunks = 0
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:") and '"content"' in line:
                    if first is None:
                        first = time.perf_counter()
                        continue
                    chunks += 1
        if first is None or chunks == 0:
            raise RuntimeError("stream produced no content")
        return round(chunks / (time.perf_counter() - first), 2)

    async def run(self, report: Report) -> None:
        async with self.client() as client:
            for tokens in (1000, 4000):
                samples = Samples(f"cold_prefill_{tokens}_s", "s")
                for index in range(self.repeats):
                    # A different system prompt every time, so the server's
                    # cache cannot help. A "cold" number measured against a warm
                    # cache is the easiest way to benchmark nothing at all.
                    system = f"run {index}\n" + context_of(tokens)
                    samples.add(await self.ttft(client, system, "Go."))
                report.llm[samples.name] = samples.summary()

            static = context_of(2000)
            warm = Samples("warm_ttft_s", "s")
            # One request to populate the cache, then measure the rest.
            await self.ttft(client, static, context_of(100))
            for index in range(self.repeats):
                warm.add(await self.ttft(client, static, context_of(100) + f" {index}"))
            report.llm["warm_ttft_s"] = warm.summary()

            rates = Samples("decode_tokens_s", "tok/s")
            for _ in range(max(1, self.repeats // 2)):
                rates.add(await self.decode_rate(client, static))
            report.llm["decode_tokens_s"] = rates.summary()


@dataclass
class SttBench:
    audio: Path
    model: str
    language: str = "en"
    compute_type: str = "int8"
    synthetic: bool = False

    def run(self, report: Report) -> None:
        import wave

        from faster_whisper import WhisperModel

        with wave.open(str(self.audio), "rb") as handle:
            seconds = handle.getnframes() / handle.getframerate()

        loaded = time.perf_counter()
        model = WhisperModel(self.model, compute_type=self.compute_type)
        report.stt["model_load_s"] = round(time.perf_counter() - loaded, 2)

        samples = Samples("stt_realtime_factor", "x")
        for _ in range(3):
            started = time.perf_counter()
            segments, _info = model.transcribe(
                str(self.audio), language=self.language, vad_filter=True
            )
            list(segments)  # the generator is lazy; nothing runs until drained
            samples.add((time.perf_counter() - started) / seconds)
        report.stt["stt_realtime_factor"] = samples.summary()
        report.stt["audio_seconds"] = round(seconds, 1)
        report.stt["model"] = self.model
        if self.synthetic:
            report.notes.append(
                "STT measured on synthetic audio: the real-time factor is optimistic, "
                "because a model given no speech to decode does less work. Re-run with "
                "a real recording before trusting it."
            )


def dry_run(report: Report) -> None:
    """Exercise the harness without any model.

    Lets the plumbing be tested on a machine that cannot host the models, and
    keeps the output unmistakably marked as meaningless.
    """
    report.dry_run = True
    for name, unit, values in (
        ("cold_prefill_1000_s", "s", [0.4, 0.42, 0.45]),
        ("cold_prefill_4000_s", "s", [1.5, 1.6, 1.7]),
        ("warm_ttft_s", "s", [0.2, 0.21, 0.25]),
        ("decode_tokens_s", "tok/s", [18.0, 18.5, 19.0]),
    ):
        samples = Samples(name, unit)
        for value in values:
            samples.add(value)
        report.llm[name] = samples.summary()
    stt = Samples("stt_realtime_factor", "x")
    for value in (0.3, 0.31, 0.33):
        stt.add(value)
    report.stt["stt_realtime_factor"] = stt.summary()
    report.llm["resident_memory_gb"] = 9.0
    report.notes.append(
        "DRY RUN: every number here is invented to exercise the harness. "
        "It measures nothing about any machine."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="susurro-bench",
        description="Measure the machine against M0's gates.",
    )
    parser.add_argument("--out", type=Path, help="where to write the JSON result")
    parser.add_argument("--label", default="", help="tag for this run, e.g. llama-cpp")
    parser.add_argument(
        "--llm-url", default="http://127.0.0.1:8080", help="OpenAI-compatible server"
    )
    parser.add_argument("--llm-model", default="local")
    parser.add_argument("--audio", type=Path, help="16 kHz mono WAV of real speech")
    parser.add_argument("--stt-model", default="large-v3-turbo")
    parser.add_argument("--stt-language", default="en")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument(
        "--server-pid",
        type=int,
        help="pid of the model server, to record its peak resident memory",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-stt", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the harness with invented numbers, to check the harness",
    )
    return parser


def default_out(label: str) -> Path:
    stamp = time.strftime("%Y-%m-%d")
    suffix = f"-{label}" if label else ""
    return Path("bench/results") / f"{stamp}{suffix}.json"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = Report(machine=machine())
    if args.label:
        report.notes.append(f"label: {args.label}")

    if args.dry_run:
        dry_run(report)
    else:
        if not args.skip_stt:
            if args.audio is None:
                print(
                    "error: --audio is required for the speech benchmark. It must be "
                    "16 kHz mono WAV of real speech; silence or a tone understates the "
                    "work and overstates the result. Pass --skip-stt to measure only "
                    "the language model.",
                    file=sys.stderr,
                )
                return 2
            SttBench(
                audio=args.audio,
                model=args.stt_model,
                language=args.stt_language,
                compute_type=args.compute_type,
            ).run(report)

        if not args.skip_llm:
            try:
                asyncio.run(
                    LlmBench(
                        base_url=args.llm_url,
                        model=args.llm_model,
                        repeats=args.repeats,
                    ).run(report)
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                print(f"error: language model benchmark failed: {exc}", file=sys.stderr)
                return 3

        memory = peak_rss_gb(args.server_pid)
        if memory is not None:
            report.llm["resident_memory_gb"] = memory
        elif args.server_pid is None:
            report.notes.append(
                "Peak memory not measured: pass --server-pid so the model server's "
                "resident memory is recorded rather than this process's."
            )

    out = args.out or default_out(args.label)
    report.write(out)
    print(report.summary())
    print(f"\nwritten to {out}")
    return 0 if report.gate_passed() else 1


if __name__ == "__main__":
    raise SystemExit(main())
