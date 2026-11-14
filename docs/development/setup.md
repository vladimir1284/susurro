# Setup

!!! info "Nothing to run yet"
    Susurro is pre-M0. This page describes the intended layout and the commands that
    will exist, so that the structure is agreed before code lands. Treat it as a
    contract for [M0](../roadmap.md#m0-benchmark) and [M1](../roadmap.md#m1-spine),
    not as working instructions.

## Repository layout

```
susurro/
├── core/
│   ├── protocol.py    # messages, framing, validation. No I/O.
│   ├── audio.py       # Opus decoding, one decoder per lane
│   ├── vad.py         # per-lane voice activity; end-of-turn on the far lane
│   │                  # session/ also holds history.py: recording and export
│   ├── server.py      # both WebSocket endpoints + the console as static files
│   ├── main.py        # entry point; backends chosen by configuration
│   ├── stt/           # recognizer interface, scripted fake, faster-whisper
│   ├── llm/           # generator interface, canned fake, OpenAI-compatible client
│   └── session/       # profiles, preamble assembly, state machine
├── capture/
│   ├── codec.py       # Opus encoding
│   ├── client.py      # one WebSocket per lane
│   ├── main.py
│   └── backends/      # file.py (replay), pipewire.py (never executed)
├── console/           # static web UI, no build step; state.js is pure
├── bench/             # M0 harness: metrics.py, report.py, run.py
│   └── results/       # committed measurements, one file per run
├── tests/
│   └── fixtures/profiles/   # synthetic; real ones go in local/, gitignored
├── docs/              # this documentation (page.md + page.es.md)
├── core.toml.example
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
.venv/bin/pip install -e ".[core,whisper,openai]"
cp core.toml.example core.toml   # then edit it
.venv/bin/python -m core.main --config core.toml
```

It prints the console URL, token included, and the profiles it found.

The core refuses to bind `0.0.0.0` without `--allow-public-bind`. It authenticates with a
shared token in a query parameter, which is enough for a trusted LAN and not enough for
anything else.

### Backends

Both are selected by configuration rather than by import, which is what lets the same
server run against fakes on a machine with no models and against real ones where they
exist.

| Setting | Values | Notes |
|---|---|---|
| `[stt] backend` | `fake`, `whisper` | `fake` replays a script and needs no model. |
| `[session] record` | `false`, `true` | Off by default. When on, sessions are written to `[session] directory`. |
| `[vad] backend` | `silero`, `energy`, `off` | `off` leaves hints manual. |
| `[llm] backend` | `fake`, `openai` | `openai` means any OpenAI-compatible server — `llama.cpp`, Ollama, vLLM, LM Studio. |

Install only what you use: `.[core]` alone gives you the fakes, which is enough to work on
the console, the protocol or the capture client.

## Running the capture client

```bash
.venv/bin/python -m capture.main \
  --core ws://mac-mini.local:8723 \
  --token "$SUSURRO_TOKEN" \
  --mic default \
  --far @DEFAULT_MONITOR@
```

The client reconnects when a lane drops — a core restart or a Wi-Fi blip should not end a
session, and nobody watches the capture terminal during a meeting, so a client that exits
quietly looks exactly like one that is working. A refused lane or a bad token is **not**
retried: that is a configuration mistake, and retrying would bury the message saying so.
`--no-reconnect` turns it off.

When the link cannot keep up, the oldest queued frame is dropped rather than blocking the
capture thread. The device keeps producing regardless, so stalling loses the same audio
anyway — silently, later, and in a burst. The count is logged at the end of the lane.

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

`http://mac-mini.local:8723/?token=…` from the phone or second screen. The core serves the
console from the same port as the WebSocket endpoints, so there is one address to remember.

The console asks the core which profiles exist and offers them in a picker, so the only
parameter you actually need is the token. The rest preselect a session, which is useful for
a bookmark straight into the right one:

| Parameter | Meaning |
|---|---|
| `token` | The core's shared token. Required. |
| `profile` | Profile id to configure on **Start**. |
| `language` | `en` or `es`. Must match the profile. |
| `mode` | `conversational`, `technical` or `business`. |

**Space** requests a hint — the largest target on any keyboard, and the one you can hit
without looking down.

A **latency** indicator appears once hints have been measured: the median time to first
token over the last few, green under a second and amber over it. That threshold is M2's
gate, and watching it during a call is the only way to know a session is inside the budget
rather than finding out afterwards that every hint arrived too late to read.

**History** lists recorded sessions, shows a transcript as Markdown, and downloads or
deletes one. Deleting takes two clicks: it cannot be undone, and a dialog would be heavier
than the decision deserves. When the core is recording, a red indicator sits in the header
for the whole session — the one thing a user must not have to go looking for.

## Contributing

The project is AGPL-3.0 and public from the first commit. The most useful contributions
early are benchmark results from hardware other than the reference Mac mini — the design
is built around one machine's numbers, and knowing where it breaks on other hardware is
worth more than features.
