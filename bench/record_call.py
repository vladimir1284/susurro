"""Record one call of the evaluation set: both lanes, meta.json, script, and a verdict.

    python -m bench.record_call 1                 # call 1 of docs/development/recording-protocol.md
    python -m bench.record_call 5 --language en   # call 5 is free: language and setup can change
    python -m bench.record_call --list-devices

It records what the capture client records (the microphone source and the monitor of the
sink you hear the far person on), at 16 kHz mono, into ``recordings/<call>/``. It refuses
to overwrite a folder. After stopping it measures both files and says whether to keep the
take or record it again. Nothing leaves the machine; ``*.wav`` is git-ignored.

Linux with PulseAudio or PipeWire-pulse only (``parec``, ``pactl``, ``ffmpeg``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16_000
FIXTURES = Path(__file__).parent / "fixtures"
FRAME_S = 0.1  # envelope resolution

#: The calls of the protocol's table. Call 5 and 6 are the ones with a free choice.
PRESETS: dict[str, dict] = {
    "1": {"language": "es", "profile": "es-business-industrial", "mode": "business",
          "setup": "headphones", "scripted": True, "minutes": 5,
          "script": "call_script_es.json"},
    "2": {"language": "es", "profile": "es-business-industrial", "mode": "business",
          "setup": "loudspeakers", "scripted": False, "minutes": 10},
    "3": {"language": "en", "profile": "en-technical-backend", "mode": "technical",
          "setup": "headphones", "scripted": True, "minutes": 5,
          "script": "call_script_en.json"},
    "4": {"language": "en", "profile": "en-technical-backend", "mode": "technical",
          "setup": "loudspeakers", "scripted": False, "minutes": 10},
    "5": {"language": "es", "profile": "es-business-industrial", "mode": "business",
          "setup": "headphones", "scripted": False, "minutes": 30},
    "6": {"language": "es", "profile": "es-business-industrial", "mode": "business",
          "setup": "headphones", "scripted": True, "minutes": 3,
          "script": "call_script_es_interrupt.json"},
}

#: Defaults for the profile and mode when call 5 is switched to the other language.
LANGUAGE_DEFAULTS = {
    "es": {"profile": "es-business-industrial", "mode": "business"},
    "en": {"profile": "en-technical-backend", "mode": "technical"},
}


# --------------------------------------------------------------------------- analysis


@dataclass(frozen=True)
class Finding:
    level: str  # "error" re-record, "warn" judge it yourself, "info" for the record
    text: str


def _db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def envelope(samples: np.ndarray) -> np.ndarray:
    """RMS per 100 ms frame, 0..1 full scale."""
    n = int(RATE * FRAME_S)
    usable = len(samples) // n * n
    if usable == 0:
        return np.zeros(0)
    frames = samples[:usable].astype(np.float64).reshape(-1, n)
    return np.sqrt((frames**2).mean(axis=1))


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16 kHz mono 16-bit")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


SPEECH_DB = -42.0  # a frame above this counts as someone talking
SILENT_DB = -60.0


def analyze(mic: np.ndarray, far: np.ndarray, *, setup: str, minutes: float,
            scripted: bool, interrupts: bool = False) -> list[Finding]:
    """Say whether a take fits the protocol. Pure: takes samples, returns findings."""
    out: list[Finding] = []
    dm, df = len(mic) / RATE, len(far) / RATE
    out.append(Finding("info", f"mic {dm:.0f} s, far {df:.0f} s"))

    if abs(dm - df) > 1.0:
        out.append(Finding("error", f"the lanes differ by {abs(dm - df):.1f} s (limit 1 s): "
                           "they were not started and stopped together"))
    mins = min(dm, df) / 60
    if minutes:
        floor = 25.0 if minutes >= 30 else minutes * 0.7
        if mins < floor:
            out.append(Finding("error", f"{mins:.1f} min recorded, call needs about "
                               f"{minutes:g} (at least {floor:g})"))
        elif mins > minutes * 1.6:
            out.append(Finding("warn", f"{mins:.1f} min is much longer than the {minutes:g} planned"))

    env_m, env_f = envelope(mic), envelope(far)
    n = min(len(env_m), len(env_f))
    env_m, env_f = env_m[:n], env_f[:n]

    for name, x, env in (("mic", mic, env_m), ("far", far, env_f)):
        if n == 0:
            continue
        peak = float(np.abs(x).max()) if len(x) else 0.0
        talk = float((_db_arr(env) > SPEECH_DB).mean())
        out.append(Finding("info", f"{name}: peak {_db(peak):.0f} dBFS, speech in {talk:.0%} of the time"))
        if peak < 10 ** (SILENT_DB / 20):
            out.append(Finding("error", f"{name} is silent: wrong source, muted, or the far "
                               "person was never heard on this machine"))
        elif peak < 10 ** (-30 / 20):
            out.append(Finding("warn", f"{name} is very quiet (peak {_db(peak):.0f} dBFS): "
                               "raise the input gain so speech peaks near -12 dBFS"))
        clipped = float((np.abs(x) > 0.99).mean())
        if clipped > 0.001:
            out.append(Finding("error", f"{name} clips ({clipped:.2%} of samples): lower the gain"))
        if talk < 0.15 and peak >= 10 ** (SILENT_DB / 20):
            out.append(Finding("warn", f"{name} has speech only {talk:.0%} of the time: "
                               "did this person talk enough?"))

    if n >= 20:
        mic_on, far_on = _db_arr(env_m) > SPEECH_DB, _db_arr(env_f) > SPEECH_DB
        both = float((mic_on & far_on).mean())
        out.append(Finding("info", f"both talking at once {both:.0%} of the time"))
        if interrupts and both < 0.02:
            out.append(Finding("warn", "an interruption call needs the near person to talk over "
                               "the far one; almost no overlap was heard"))
        if not interrupts and both > 0.25:
            out.append(Finding("warn", f"both lanes talk at once {both:.0%} of the time: more "
                               "than a normal call. Alternate; do not read together"))
        # The near mic hearing the far lane: while the far side talks, does the mic's
        # loudness move with the far lane's? Own speech is uncorrelated with it, a leak is not.
        if far_on.sum() >= 20:
            a, b = _db_arr(env_m)[far_on], _db_arr(env_f)[far_on]
            r = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0.5 and b.std() > 0.5 else 0.0
            heard = float((a > SPEECH_DB).mean())
            out.append(Finding("info", f"while far talks: mic follows it r={r:.2f}, "
                               f"mic active {heard:.0%} of those frames"))
            if setup == "headphones" and r > 0.5 and heard > 0.5:
                out.append(Finding("error", f"mic follows the far lane (r={r:.2f}) but the setup is "
                                   "headphones: the far voice leaks into the mic. Check the headphones "
                                   "are on and the mic source is not the sink monitor"))
            if setup == "loudspeakers" and r < 0.2 and heard < 0.3:
                out.append(Finding("warn", f"setup is loudspeakers but the mic hardly hears the far "
                                   f"lane (r={r:.2f}): was the volume up? That bleed is what "
                                   "this call is for"))
    return out


def _db_arr(env: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(env, 1e-9))


def verdict(findings: list[Finding]) -> str:
    if any(f.level == "error" for f in findings):
        return "RE-RECORD"
    if any(f.level == "warn" for f in findings):
        return "KEEP IF THE WARNINGS ARE EXPECTED"
    return "KEEP"


# --------------------------------------------------------------------------- devices


def pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, check=True).stdout


def default_devices() -> tuple[str, str]:
    mic = pactl("get-default-source").strip()
    sink = pactl("get-default-sink").strip()
    return mic, sink + ".monitor"


def list_devices() -> None:
    mic, far = default_devices()
    print("sources (microphones and sink monitors):")
    for line in pactl("list", "short", "sources").splitlines():
        name = line.split("\t")[1]
        mark = " <- default mic" if name == mic else " <- far (default sink monitor)" if name == far else ""
        print(f"  {name}{mark}")


def parec_cmd(device: str) -> list[str]:
    return ["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}",
            "--channels=1", "--raw"]


def level_check(mic_dev: str, far_dev: str, seconds: float = 4.0) -> tuple[float, float]:
    """Peak dBFS of each lane over a few seconds."""
    procs = [subprocess.Popen(parec_cmd(d), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
             for d in (mic_dev, far_dev)]
    time.sleep(seconds)
    peaks = []
    for p in procs:
        p.terminate()
        raw = p.stdout.read() if p.stdout else b""
        a = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
        peaks.append(_db(float(np.abs(a).max())) if len(a) else -120.0)
        p.wait()
    return peaks[0], peaks[1]


# --------------------------------------------------------------------------- recording


def record(folder: Path, mic_dev: str, far_dev: str) -> float:
    """Record until Enter or Ctrl-C. Returns the wall-clock seconds."""
    pipelines = []
    for lane, dev in (("mic", mic_dev), ("far", far_dev)):
        src = subprocess.Popen(parec_cmd(dev), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
             "-i", "-", str(folder / f"{lane}.wav")],
            stdin=src.stdout, stderr=subprocess.PIPE)
        if src.stdout:
            src.stdout.close()
        pipelines.append((src, enc))
    t0 = time.monotonic()
    try:
        import select

        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            el = time.monotonic() - t0
            print(f"\r  recording {int(el // 60):02d}:{int(el % 60):02d}   (Enter to stop)",
                  end="", flush=True)
            if ready:
                sys.stdin.readline()
                break
            if any(src.poll() is not None for src, _ in pipelines):
                print("\n  a capture process died; stopping")
                break
    except KeyboardInterrupt:
        pass
    elapsed = time.monotonic() - t0
    print()
    for src, _ in pipelines:
        src.terminate()
    for src, enc in pipelines:
        src.wait()
        enc.wait(timeout=20)
    return elapsed


# --------------------------------------------------------------------------- main


def build_meta(call: str, args: argparse.Namespace) -> dict:
    meta = dict(PRESETS[call])
    meta.pop("script", None)
    if call == "5":
        if args.language:
            meta.update(LANGUAGE_DEFAULTS[args.language], language=args.language)
        if args.setup:
            meta["setup"] = args.setup
    return meta


def instructions(call: str, meta: dict, folder: Path) -> str:
    lines = [f"Call {call}: {meta['language']}, {meta['setup']}, "
             f"{'scripted' if meta['scripted'] else 'unscripted'}, about {meta['minutes']} min", ""]
    lines.append("  Need: a real call (or any app) where the far person's voice comes out of this "
                 "machine. That output is the far lane; your microphone is the mic lane.")
    if meta["setup"] == "headphones":
        lines.append("  Headphones ON for the whole call. The far voice must not reach your mic.")
    else:
        lines.append("  Loudspeakers, volume as you would use it in a call. The far voice "
                     "reaching your mic is what this call tests: do not mute or reduce it.")
    if meta["scripted"]:
        lines.append(f"  Read from docs/development/recording-scripts.md (call {call}). Alternate "
                     "far, mic, far, mic. Say the exchange number first. Normal pace. Do not read the "
                     "stage directions aloud. Do not talk at the same time unless the script says so.")
    else:
        lines.append("  No script: talk about something you both know, as in a real call. "
                     "Interrupt each other now and then. Pauses are fine.")
        if meta["minutes"] >= 30:
            lines.append("  Keep going the full half hour; the last 10 minutes are the point. "
                         "Use a far voice not used in calls 1 to 4 if you can.")
    lines.append("  Ask the far person for consent to be recorded before you start.")
    lines.append("  Then: wait 3 s of silence, start talking, and at the end wait 3 s before Enter.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("call", nargs="?", choices=sorted(PRESETS), help="call number of the protocol")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--mic", help="mic source name (default: pactl default source)")
    ap.add_argument("--far", help="far source name (default: monitor of the default sink)")
    ap.add_argument("--out", type=Path, default=Path("recordings"))
    ap.add_argument("--name", help="folder name (default: call<N>)")
    ap.add_argument("--language", choices=sorted(LANGUAGE_DEFAULTS), help="call 5 only")
    ap.add_argument("--setup", choices=["headphones", "loudspeakers"], help="call 5 only")
    ap.add_argument("--skip-level-check", action="store_true")
    args = ap.parse_args(argv)

    for tool in ("parec", "pactl", "ffmpeg"):
        if not shutil.which(tool):
            print(f"{tool} not found", file=sys.stderr)
            return 2
    if args.list_devices:
        list_devices()
        return 0
    if not args.call:
        ap.error("give a call number (1-6) or --list-devices")
    if args.call != "5" and (args.language or args.setup):
        ap.error("--language and --setup apply to call 5 only; the others are fixed by the protocol")

    meta = build_meta(args.call, args)
    folder = args.out / (args.name or f"call{args.call}")
    if folder.exists():
        print(f"{folder} exists; not overwriting. Use --name or move it.", file=sys.stderr)
        return 2

    d_mic, d_far = default_devices()
    mic_dev, far_dev = args.mic or d_mic, args.far or d_far
    print(instructions(args.call, meta, folder))
    print(f"\n  mic source: {mic_dev}\n  far source: {far_dev}")
    if mic_dev.endswith(".monitor"):
        print("  WARNING: the mic source is a monitor. Pick the real microphone with --mic.")
    print("  (the far source must be the monitor of the output you hear the far person on)")

    if not args.skip_level_check:
        input("\nLevel check: 4 s. Talk, and have the far side talk or play audio. Enter to start. ")
        pm, pf = level_check(mic_dev, far_dev)
        print(f"  mic peak {pm:.0f} dBFS, far peak {pf:.0f} dBFS (aim for about -12; below -40 is wrong)")
        if pm < -50 or pf < -50:
            if input("  A lane is nearly silent. Continue anyway? [y/N] ").strip().lower() != "y":
                return 1

    input("\nEnter to START recording. ")
    folder.mkdir(parents=True)
    elapsed = record(folder, mic_dev, far_dev)

    mic, far = read_wav(folder / "mic.wav"), read_wav(folder / "far.wav")
    findings = analyze(mic, far, setup=meta["setup"], minutes=meta["minutes"],
                       scripted=meta["scripted"], interrupts=args.call == "6")
    meta["recorded_seconds"] = round(min(len(mic), len(far)) / RATE)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
    if "script" in PRESETS[args.call]:
        shutil.copy(FIXTURES / PRESETS[args.call]["script"], folder / "script.json")

    print(f"\n{folder}  ({elapsed / 60:.1f} min wall clock)")
    for f in findings:
        print(f"  [{f.level:5}] {f.text}")
    v = verdict(findings)
    print(f"\nVerdict: {v}")
    if v == "RE-RECORD":
        print(f"  Move or delete {folder} before recording again (the script will not overwrite it).")
    return 1 if v == "RE-RECORD" else 0


if __name__ == "__main__":
    raise SystemExit(main())
