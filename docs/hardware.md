# Hardware

## Reference setup

| Role | Machine | Notes |
|---|---|---|
| Core | Mac mini M4, 16 GB unified memory | Runs both models. Dedicated — does not host the meeting. |
| Capture | Intel N95 mini PC, Linux | Hosts the meeting. Capture and Opus encoding only. |
| Console | Phone or second monitor | Any browser. |

The split exists for a reason. 16 GB is enough for the models *or* for a video call with
a browser, not comfortably for both at once — see
[Inference](architecture/inference.md#the-binding-constraint-is-memory). The N95 would be
hopeless as a core and is entirely adequate as a capture client, because Opus encoding of
two 16 kHz mono streams is a rounding error of CPU.

## Core requirements

**Minimum viable:** Apple Silicon with 16 GB of unified memory, or an x86 machine with
8 GB of VRAM. Below that, an 8B model at q4 plus a speech model will not both stay
resident, and the reload cost per hint breaks the latency budget.

**Comfortable:** 24 GB or more, which opens the 14B class and leaves real headroom.

### Keep the Mac mini awake

**An idle Mac mini sleeps and wakes about once a minute**, and every time it does,
everything on it stops for roughly 13 seconds: a request in flight waits it out, so hints
and transcript lines arrive 15 seconds late. It happens whenever nobody is using the
machine, which is the normal state of a headless core. On the reference Mac it was
measured at about 60 sleep/wake cycles an hour around the clock, and it stopped completely
under `caffeinate -s` (also `-dimsu`; `-i` alone was not enough). To fix it for good, once:

```bash
sudo pmset -a sleep 0
```

or turn on "Prevent automatic sleeping when the display is off" in System Settings. As a
stopgap without `sudo`, run `caffeinate -s &` before a session. Check with
`pmset -g log | grep -c DarkWake`: it should not keep growing while the machine is idle.

Susurro is not CUDA-specific. The core talks to `llama.cpp` or an OpenAI-compatible
endpoint, so an NVIDIA box works; it is simply not what the project is being designed and
measured against.

## Capture client requirements

Anything that can run Python and open two audio streams. An N95, a Raspberry Pi 5, an old
laptop. The client does no inference by design.

On macOS a loopback audio device is required — [BlackHole](https://github.com/ExistentialAudio/BlackHole)
or equivalent — because macOS provides no system audio capture of its own. On Linux the
PipeWire monitor source exists by default.

## Network

LAN, or a [Tailscale](https://tailscale.com/) tailnet if the core lives elsewhere.
Bandwidth is negligible: two Opus lanes at roughly 24 kbit/s each. Latency matters more
than throughput, which is why Wi-Fi to a congested access point is worth checking before
blaming the model.

## M0 benchmark procedure

Run before writing any other code. See [Roadmap](roadmap.md#m0-benchmark) for the gate
conditions.

Both runtimes worth comparing speak the OpenAI API, so there is one harness rather than
two code paths. Start one, measure it, then start the other and measure again:

```bash
# llama.cpp
llama-server -m model.gguf -c 8192 --port 8080

# or MLX
mlx_lm.server --model <repo> --port 8080
```

```bash
# on the core machine
git clone https://github.com/vladimir1284/susurro
cd susurro
python3 -m venv .venv && .venv/bin/pip install -e ".[bench]"
.venv/bin/susurro-bench \
  --label llama-cpp \
  --audio samples/interview-30s.wav \
  --server-pid "$(pgrep -f llama-server)"
```

What it reports:

| Metric | Why it matters |
|---|---|
| STT real-time factor | Must be well under 1.0 or transcription falls behind permanently |
| Cold prefill, 1k / 4k tokens | The cost the prompt cache exists to avoid |
| Warm time to first token | The cost actually paid per hint. **The number the product lives on.** |
| Decode tokens/s | Sets how long a hint takes to finish streaming |
| Peak resident memory | Decides whether the 8B assumption holds |

Three things the harness insists on, because each one is an easy way to measure nothing:

- **Real speech audio.** `--audio` is required. Silence or a tone gives the recognizer
  less work than real speech and produces a flattering real-time factor.
- **A different prompt per cold repeat.** A "cold" prefill measured against a server whose
  cache is already warm is not a measurement.
- **The server's pid.** The model runs in another process; without `--server-pid` the
  memory figure would describe the harness instead.

The exit code is the gate: `0` if every threshold in
[Roadmap](roadmap.md#m0-benchmark) is met, `1` if any is missed or unmeasured. A gate with
no measurement behind it does not pass — silence is not evidence.

`--dry-run` exercises the harness with invented numbers, to check the harness rather than
the machine. Its output says so on every line that matters.

Each result is committed to `bench/results/`. Every file records the machine it came from
— architecture, cores, memory, accelerator — so a number from a laptop can never be quoted
as a number from the target. The numbers in these docs are updated from them, and the
estimate warnings removed as they are replaced by measurements.
