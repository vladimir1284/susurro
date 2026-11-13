# Setup

!!! info "Nothing to run yet"
    Susurro is pre-M0. This page describes the intended layout and the commands that
    will exist, so that the structure is agreed before code lands. Treat it as a
    contract for [M0](../roadmap.md#m0-benchmark) and [M1](../roadmap.md#m1-spine),
    not as working instructions.

## Repository layout

```
susurro/
├── core/              # inference server: VAD, STT, LLM, session state
│   ├── audio/         # lane handling, VAD, resampling
│   ├── stt/           # recognizer backends
│   ├── llm/           # runtime backends, prompt cache, presets
│   ├── session/       # profiles, context assembly, state machine
│   └── server.py      # WebSocket endpoints + static console
├── capture/           # thin client
│   ├── backends/      # file.py (replay), pipewire.py, coreaudio.py
│   └── main.py
├── console/           # static web UI (no build step)
├── bench/             # M0 benchmark harness
│   └── results/       # committed measurements
├── docs/              # this documentation (en + .es.md)
└── mkdocs.yml
```

## Tooling

Python for core and capture. The speech recognition ecosystem lives in Python, and
fighting that to win a few milliseconds of startup is a bad trade.

The capture client stays Python in v1 as well. Opus encoding of two 16 kHz mono streams
is cheap enough that an N95 will not notice. If measurement says otherwise, the client is
a few hundred lines and rewriting it in Rust is a contained job — which is itself a
reason to keep the protocol boundary clean.

The console is plain HTML, CSS and JavaScript with no build step. It renders a scrolling
list and talks to a WebSocket; a framework would add a toolchain and solve nothing.

## Running the core

```bash
cd susurro
python3 -m venv .venv
.venv/bin/pip install -e ".[core]"
.venv/bin/python -m core.server --config core.toml
```

Configuration covers bind address, shared token, model paths and runtime selection. The
core refuses to bind `0.0.0.0` without an explicit override, because an inference server
with a shared-secret query parameter is not something to expose by accident.

## Running the capture client

```bash
.venv/bin/python -m capture.main \
  --core ws://mac-mini.local:8723 \
  --token "$SUSURRO_TOKEN" \
  --mic default \
  --far @DEFAULT_MONITOR@
```

The backend is selected from the platform. `--far` takes a PipeWire node on Linux or a
CoreAudio device name on macOS; both accept `--list-devices` to find the right one.

With no audio devices to hand — a container, a remote box, CI — replay a pair of WAV files
instead. They must be 16 kHz mono 16-bit:

```bash
.venv/bin/python -m capture.main \
  --core ws://localhost:8723 --token "$SUSURRO_TOKEN" \
  --replay-mic me.wav --replay-far them.wav
```

Replay is paced in real time by default, so the core sees the same arrival pattern it
would see from a device. `--speed 0` replays as fast as the core accepts, which is what
the tests use.

## Opening the console

`http://mac-mini.local:8723/?token=…` from the phone or second screen.

## Contributing

The project is AGPL-3.0 and public from the first commit. The most useful contributions
early are benchmark results from hardware other than the reference Mac mini — the design
is built around one machine's numbers, and knowing where it breaks on other hardware is
worth more than features.
