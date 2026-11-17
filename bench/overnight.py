"""Unattended M0 rounds against the Mac mini, repeated until a deadline.

One round, in order: whisper.cpp speed, a replay of the recorded call through the
real recognizer, then the language-model bench against gemma-4-E2B (the server
already running), LFM2.5-1.2B and LFM2 2.6B under llama.cpp, and the same two
LFM models under Ollama's own server. Repeating it is the point: the Mac's speed
has varied about 3x over an evening, and one sample of that says little.

Rules the driver keeps, because nobody is watching it:

* every step has a timeout, and a step that fails is logged and skipped;
* it only ever signals processes it started itself, identified by pid;
* it never touches the servers it did not start (the gemma server on 8080, the
  whisper server on 8081);
* three Mac failures in a row end the run;
* no step starts if it could not finish before the deadline.

Run it from a checkout nobody is editing (a ``git worktree``): the steps import
this repository's code fresh every time.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

MAC = "vladimir@192.168.88.105"
SSH = [
    "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
    "-i", str(Path.home() / ".ssh" / "id_rsa"), "-o", "ConnectTimeout=8",
    "-o", "ServerAliveInterval=15",
]
BLOBS = "~/.ollama/models/blobs/sha256-"
MODELS = {
    "lfm25-1.2b": {
        "blob": "b1b3de114215d9507409a662a501a631095a479a419584e8a2ded6304b19b4f5",
        "ollama": "LiquidAI/LFM2.5-1.2B-Instruct",
        "desc": "LFM2.5-1.2B-Instruct Q4_K_M",
    },
    "lfm2-2.6b": {
        "blob": "1f1d46904e25f1b67b538bd658ee4e11ed311864e5e8247b22ea5ab7488c83ee",
        "ollama": "sam860/LFM2:2.6B",
        "desc": "sam860/LFM2:2.6B",
    },
}
#: Recordings are git-ignored, so they live in the main checkout, not in the
#: worktree the steps run from.
MAIN = Path("/home/vladimir/Documents/Trabajo/Nitza/susurro")
CALL = str(MAIN / "sessions/meet-test-20261115-193928/s1")
CLIP = str(MAIN / "sessions/clip_far_12s.wav")
SCRIPT = "bench/fixtures/call_script_es.json"
LLAMA_FLAGS = (
    "--gpu-layers 99 --flash-attn on --ctx-size 8192 --cache-type-k q8_0 "
    "--cache-type-v q8_0 --threads 4 --parallel 1 --jinja --host 127.0.0.1"
)
#: Worst-case seconds each step needs; a step is not started if the deadline is
#: closer than this.
BUDGET = {"stt": 240, "replay": 420, "model": 900}
#: Seconds to let the Mac release a stopped server before starting the next.
SETTLE_S = 20


def deadline_after(now: datetime, hour: int, minute: int) -> datetime:
    """The next occurrence of hour:minute strictly after ``now``."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def next_round_wait(now: float, started: float, interval_s: float, deadline: float) -> float | None:
    """Seconds to sleep before the next round, or None when no round fits.

    A round slot is ``started + interval_s``; it needs 600 s of margin before the
    deadline. A round that overran its slot starts the next one at once.
    """
    slot = started + interval_s
    if slot + 600 >= deadline:
        return None
    return max(0.0, slot - now)


def can_start(now: float, deadline: float, step: str) -> bool:
    return now + BUDGET[step] < deadline


@dataclass
class Driver:
    out: Path
    code: Path
    python: str
    deadline: float
    interval_s: float
    mac_failures: int = 0
    next_port: int = 8100
    log: list[str] = field(default_factory=list)

    # -- plumbing ---------------------------------------------------------- #

    def say(self, message: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {message}"
        print(line, flush=True)
        with (self.out / "driver.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def mac(self, command: str, timeout: float = 60) -> subprocess.CompletedProcess:
        try:
            done = subprocess.run(
                [*SSH, MAC, command], capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            done = subprocess.CompletedProcess([], 255, "", "timeout")
        # 255 is ssh's own failure; the remote command's status is its own.
        self.mac_failures = self.mac_failures + 1 if done.returncode == 255 else 0
        return done

    def run(self, argv: list[str], log: Path, timeout: float) -> int:
        """Run a step with this repository's code, bounded in time."""
        with log.open("w", encoding="utf-8") as handle:
            try:
                return subprocess.run(
                    argv, cwd=self.code, stdout=handle, stderr=subprocess.STDOUT,
                    timeout=timeout,
                ).returncode
            except subprocess.TimeoutExpired:
                handle.write("\n[driver] timed out\n")
                return 124

    def record(self, round_no: int, step: str, rc: int, path: Path | None) -> None:
        row = {"round": round_no, "step": step, "rc": rc,
               "json": str(path) if path else None, "at": time.time()}
        with (self.out / "results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        self.say(f"round {round_no} {step}: rc={rc}")

    def tunnel(self, port: int) -> subprocess.Popen | None:
        proc = subprocess.Popen(
            [*SSH, "-N", "-o", "ExitOnForwardFailure=yes",
             "-L", f"{port}:127.0.0.1:{port}", MAC],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            if proc.poll() is not None:
                return None
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return proc
            time.sleep(0.25)
        proc.terminate()
        return None

    def stop_remote(self, pid: str, port: int) -> None:
        """End a server this driver started, and only that one."""
        if not pid:
            return
        check = f"ps -o command= -p {pid} | grep -q -- '--port {port}'"
        self.mac(f"{check} && kill {pid}", 15)
        for _ in range(8):
            time.sleep(1)
            if self.mac(f"kill -0 {pid} 2>/dev/null", 15).returncode != 0:
                return
        # Our own process, confirmed by its port, and it ignored SIGTERM: this
        # happened once on the first night (a load that hung).
        self.say(f"pid {pid} (port {port}) ignored SIGTERM; killing it")
        self.mac(f"{check} && kill -9 {pid}", 15)

    # -- one server, one bench -------------------------------------------- #

    def bench_llm(self, round_no: int, label: str, port: int, desc: str,
                  model: str = "local", remote_rss: str | None = None) -> None:
        tun = self.tunnel(port)
        if tun is None:
            self.record(round_no, label, 90, None)
            return
        try:
            warm = (
                "import httpx, bench.run as r;"
                f"httpx.post('http://127.0.0.1:{port}/v1/chat/completions', timeout=180,"
                f" json={{'model': {model!r}, 'messages': ["
                "{'role':'system','content':'warm '+r.context_of(4000)},"
                "{'role':'user','content':'Go.'}], 'max_tokens': 4})"
            )
            subprocess.run([self.python, "-c", warm], cwd=self.code, timeout=240,
                           capture_output=True)
            rss = self.mac(remote_rss or "", 20).stdout.strip() if remote_rss else ""
            gb = round(int(rss) / 1024 / 1024, 2) if rss.isdigit() else None
            out = self.out / f"round{round_no:02d}" / f"{label}.json"
            argv = [self.python, "-m", "bench.run", "--label", label, "--skip-stt",
                    "--llm-url", f"http://127.0.0.1:{port}", "--llm-model", model,
                    "--repeats", "5", "--server-host", desc, "--out", str(out)]
            if gb is not None:
                argv += ["--server-memory-gb", str(gb)]
            rc = self.run(argv, out.with_suffix(".txt"), 900)
            self.record(round_no, label, rc, out)
        finally:
            tun.terminate()

    def start_llama(self, key: str, extra: str = "") -> tuple[str, int] | None:
        """Start a llama-server for ``key`` and wait for it. None if it never came up.

        LFM2 2.6B twice hung forever at "loading model" (0% CPU, ignoring
        SIGTERM) when started soon after another server was stopped, and loaded
        in 6 s after a pause. Unproven, so: settle first, retry once, and give
        the retry a fresh port because a killed process can keep its socket.
        """
        spec = MODELS[key]
        for attempt in (1, 2):
            time.sleep(SETTLE_S if attempt == 1 else SETTLE_S * 2)
            port, self.next_port = self.next_port, self.next_port + 1
            cmd = (f"nohup /opt/homebrew/bin/llama-server --model {BLOBS}{spec['blob']} "
                   f"{LLAMA_FLAGS} {extra} --port {port} "
                   f"> /tmp/susurro-night-{key}.log 2>&1 < /dev/null & echo $!")
            pid = self.mac(cmd, 20).stdout.strip()
            ready = self.mac(
                f"for i in $(seq 1 45); do curl -s -m 2 http://127.0.0.1:{port}/health "
                "| grep -q '\"ok\"' && exit 0; sleep 2; done; exit 1", 120)
            if ready.returncode == 0:
                return pid, port
            self.say(f"{key}: server not ready (attempt {attempt})")
            self.stop_remote(pid, port)
        return None

    def llama_model(self, round_no: int, key: str) -> None:
        spec = MODELS[key]
        started = self.start_llama(key)
        if started is None:
            self.record(round_no, f"llamacpp-{key}", 91, None)
            return
        pid, port = started
        try:
            self.bench_llm(round_no, f"llamacpp-{key}", port,
                           f"Mac mini M4 16 GB (llama.cpp b10240, {spec['desc']}, tunnel)",
                           remote_rss=f"ps -o rss= -p {pid}")
        finally:
            self.stop_remote(pid, port)

    def contention(self, round_no: int, name: str, port: int, desc: str,
                   model: str = "local") -> None:
        """The recorded call replayed with real hints from this model server
        while whisper.cpp does the speech: the two models share one machine."""
        tun = self.tunnel(port) if port != 8080 else None
        out = self.out / f"round{round_no:02d}" / f"contention-{name}.json"
        try:
            rc = self.run(
                [self.python, "-m", "bench.replay_call", CALL, "--start", "150",
                 "--end", "300", "--script", SCRIPT, "--profile", "es-business-industrial",
                 "--end-windows", "24", "--bleed-ratio", "2.0",
                 "--stt-url", "http://127.0.0.1:8081",
                 "--hints-url", f"http://127.0.0.1:{port}", "--hints-model", model,
                 "--out", str(out)],
                out.with_suffix(".txt"), 480)
            self.record(round_no, f"contention-{name}", rc, out)
        finally:
            if tun is not None:
                tun.terminate()

    def contention_llama(self, round_no: int, key: str) -> None:
        started = self.start_llama(key)
        if started is None:
            self.record(round_no, f"contention-{key}", 91, None)
            return
        pid, port = started
        try:
            self.contention(round_no, key, port, MODELS[key]["desc"])
        finally:
            self.stop_remote(pid, port)

    def ollama_model(self, round_no: int, key: str) -> None:
        port, self.next_port = self.next_port, self.next_port + 1
        spec = MODELS[key]
        cmd = (f"OLLAMA_HOST=127.0.0.1:{port} OLLAMA_CONTEXT_LENGTH=8192 "
               "OLLAMA_KEEP_ALIVE=24h OLLAMA_NUM_PARALLEL=1 nohup /usr/local/bin/ollama "
               f"serve > /tmp/susurro-night-ollama-{key}.log 2>&1 < /dev/null & echo $!")
        pid = self.mac(cmd, 20).stdout.strip()
        ready = self.mac(
            f"for i in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:{port}/api/version "
            "| grep -q version && exit 0; sleep 2; done; exit 1", 90)
        try:
            if ready.returncode != 0:
                self.say(f"ollama {key}: server did not become ready")
                self.record(round_no, f"ollama-{key}", 91, None)
                return
            self.bench_llm(
                round_no, f"ollama-{key}", port,
                f"Mac mini M4 16 GB (Ollama serve, {spec['desc']}, ctx 8192, tunnel)",
                model=spec["ollama"],
                remote_rss="ps -axo rss,command | grep '[o]llama' | awk '{s+=$1} END {print s}'",
            )
        finally:
            self.mac(f"pkill -P {pid}", 15)  # its runner children, ours by parentage
            self.stop_remote_ollama(pid, port)

    def stop_remote_ollama(self, pid: str, port: int) -> None:
        if not pid:
            return
        self.mac(f"ps -o command= -p {pid} | grep -q ollama && kill {pid}", 15)
        time.sleep(3)
        if self.mac(f"kill -0 {pid} 2>/dev/null", 15).returncode == 0:
            self.say(f"ollama pid {pid} ignored SIGTERM; killing it")
            self.mac(f"ps -o command= -p {pid} | grep -q ollama && kill -9 {pid}", 15)

    # -- the whole night --------------------------------------------------- #

    def one_round(self, round_no: int) -> None:
        (self.out / f"round{round_no:02d}").mkdir(exist_ok=True)
        rdir = self.out / f"round{round_no:02d}"
        if can_start(time.time(), self.deadline, "stt"):
            out = rdir / "stt.json"
            rc = self.run(
                [self.python, "-m", "bench.run", "--label", "stt", "--skip-llm",
                 "--audio", CLIP, "--stt-url", "http://127.0.0.1:8081",
                 "--stt-language", "es", "--repeats", "5", "--server-host",
                 "Mac mini M4 16 GB (whisper.cpp small, tunnel)", "--out", str(out)],
                rdir / "stt.txt", 240)
            self.record(round_no, "stt", rc, out)
        if self.mac_failures >= 3:
            return
        if can_start(time.time(), self.deadline, "replay"):
            out = rdir / "replay.json"
            rc = self.run(
                [self.python, "-m", "bench.replay_call", CALL, "--start", "150",
                 "--end", "300", "--script", SCRIPT, "--profile", "es-business-industrial",
                 "--end-windows", "24", "--bleed-ratio", "2.0",
                 "--stt-url", "http://127.0.0.1:8081", "--out", str(out)],
                rdir / "replay.txt", 420)
            self.record(round_no, "replay", rc, out)
        steps = [
            ("gemma", lambda: self.bench_llm(
                round_no, "llamacpp-gemma4-e2b", 8080,
                "Mac mini M4 16 GB (llama.cpp b10240, gemma-4-E2B qat Q4_K_XL, tunnel)",
                remote_rss="ps -o rss= -p $(pgrep -f '[p]ort 8080')")),
            ("contention-gemma", lambda: self.contention(
                round_no, "gemma4-e2b", 8080, "gemma-4-E2B")),
            *[(f"llamacpp-{k}", lambda k=k: self.llama_model(round_no, k)) for k in MODELS],
            ("contention-lfm25", lambda: self.contention_llama(round_no, "lfm25-1.2b")),
        ]
        # Ollama takes the longest and answers a secondary question: every
        # other round is enough.
        if round_no % 2 == 0:
            steps += [(f"ollama-{k}", lambda k=k: self.ollama_model(round_no, k))
                      for k in MODELS]
        for name, step in steps:
            if self.mac_failures >= 3:
                return
            if not can_start(time.time(), self.deadline, "model"):
                self.say(f"not enough time left for {name}")
                return
            step()

    def main_loop(self, start_round: int = 1) -> int:
        self.say(f"start; deadline {datetime.fromtimestamp(self.deadline):%H:%M}")
        round_no = start_round - 1
        while time.time() < self.deadline and self.mac_failures < 3:
            round_no += 1
            started = time.time()
            self.one_round(round_no)
            wait = next_round_wait(time.time(), started, self.interval_s, self.deadline)
            if wait is None:
                self.say(f"round {round_no} done; the next slot leaves no time, stopping")
                break
            if wait > 0:
                self.say(f"round {round_no} done; waiting {wait / 60:.0f} min")
                time.sleep(wait)
        self.say(f"finished after {round_no} rounds; mac_failures={self.mac_failures}")
        return 0


SAMPLER = r"""#!/bin/sh
# Every 10 s: time, load, free pages, swap used, the three busiest processes.
END=$1
echo "ts,load1,free_pages,swap,top1,top2,top3"
while [ "$(date +%s)" -lt "$END" ]; do
  L=$(sysctl -n vm.loadavg | awk '{print $2}')
  F=$(vm_stat | awk '/Pages free/ {gsub("\\.","",$3); print $3}')
  S=$(sysctl -n vm.swapusage | awk '{print $6}')
  T=$(ps -Arcwwo pcpu,comm | sed 1d | head -3 | awk '{printf "%s:%s;", $2, $1}' )
  echo "$(date +%s),$L,$F,$S,$(echo "$T" | tr ';' ',' | sed 's/,$//')"
  sleep 10
done
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.overnight")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--code", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--until", default="06:30", help="local HH:MM, the hard stop")
    parser.add_argument("--interval-min", type=float, default=45.0)
    parser.add_argument("--start-round", type=int, default=1)
    parser.add_argument("--no-setup", action="store_true",
                        help="caffeinate and the state sampler are already running")
    args = parser.parse_args(argv)

    hour, minute = (int(part) for part in args.until.split(":"))
    deadline = deadline_after(datetime.now(), hour, minute).timestamp()
    args.out.mkdir(parents=True, exist_ok=True)
    driver = Driver(args.out, args.code, args.python, deadline, args.interval_min * 60)

    if not args.no_setup:
        seconds = int(deadline - time.time()) + 120
        # -s, not -i: with only -i the Mac mini still slept and dark-woke about
        # every 70 s (measured: 60 cycles an hour), freezing everything for ~13 s
        # each time; -s ended it. It holds only on AC power, which a mini has.
        driver.mac(f"nohup caffeinate -s -t {seconds} > /dev/null 2>&1 < /dev/null &", 20)
        sampler = args.out / "sampler.sh"
        sampler.write_text(SAMPLER, encoding="utf-8")
        subprocess.run(["scp", *SSH[1:], str(sampler), f"{MAC}:/tmp/susurro-sampler.sh"],
                       capture_output=True, timeout=30)
        driver.mac(
            f"nohup sh /tmp/susurro-sampler.sh {int(deadline) + 120} "
            "> /tmp/susurro-overnight-state.csv 2>&1 < /dev/null &", 20)
    try:
        return driver.main_loop(args.start_round)
    finally:
        done = subprocess.run(
            ["scp", *SSH[1:], f"{MAC}:/tmp/susurro-overnight-state.csv",
             str(args.out / "mac_state.csv")], capture_output=True, timeout=60)
        driver.say(f"state log fetched: rc={done.returncode}")


if __name__ == "__main__":
    raise SystemExit(main())
