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
│   └── backends/      # file.py (replay), pulse.py, pipewire.py (never executed)
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
.venv/bin/susurro-core --config core.toml
```

It prints the console URL, token included, and the profiles it found.

The core refuses to bind `0.0.0.0` without `--allow-public-bind`. It authenticates with a
shared token in a query parameter, which is enough for a trusted LAN and not enough for
anything else.

### Security model

One shared token, sent in the URL query string over plain `ws://` and `http://`, and passed
to `susurro-capture` on its command line, where other local users can read it. Nothing is
encrypted in transit. That is acceptable for the reference topology (two machines on a LAN
or a tailnet you control) and for nothing else: do not expose the core to an untrusted
network, and do not run it on a machine with untrusted local users. Nothing leaves your
machines, but on a shared network the audio and the hints are readable by anyone on it.

### Backends

Both are selected by configuration rather than by import, which is what lets the same
server run against fakes on a machine with no models and against real ones where they
exist.

| Setting | Values | Notes |
|---|---|---|
| `[stt] backend` | `fake`, `whisper`, `whispercpp` | `fake` replays a script and needs no model. `whispercpp` talks to a whisper.cpp `whisper-server` at `[stt] base_url`, so the model can run on another machine and nothing is downloaded. |
| `[session] record` | `false`, `true` | Off by default. When on, sessions are written to `[session] directory`. |
| `[vad] backend` | `silero`, `energy`, `off` | `off` leaves hints manual. |
| `[llm] backend` | `fake`, `openai` | `openai` means any OpenAI-compatible server — `llama.cpp`, Ollama, vLLM, LM Studio. |

Install only what you use: `.[core]` alone gives you the fakes, which is enough to work on
the console, the protocol or the capture client.

### Replaying a recorded call

Every fake here is kinder than a call. A call has two lanes speaking at once, one leaking into
the other's microphone, a model server that slows down for reasons nobody is watching, and no
pause button. `bench.replay_call` feeds two recorded lanes (`mic.wav` and `far.wav`, 16 kHz
mono) through the real routing, voice activity detection and recognizer at real-time pace and
reports how late the finals arrive, the word error rate against a script, and whether the key
terms came through:

```bash
.venv/bin/python -m bench.replay_call recordings/call1 --start 150 --end 300 \
    --script bench/fixtures/call_script_es.json --profile es-business-industrial
```

`--extra-latency 1.0` adds a second to every model request, to see how the pipeline behaves on
a slow server; `--max-lag 1e9` turns partial shedding off to compare. Scoring aligns each
scripted sentence on its own, so words said off-script cost nothing. It never overwrites an
existing `--out` file. The audio of a real call is private: keep it out of the repository.

## Running the capture client

```bash
.venv/bin/susurro-capture \
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

The backend is selected from the platform. On Linux, `--backend auto` (the default) uses
PulseAudio, or PipeWire's PulseAudio layer, when `pactl` answers, and native PipeWire
otherwise; `--backend pulse|pipewire` forces one. `--far` takes a source name on Linux
(`@DEFAULT_MONITOR@` is the monitor of the default sink); `--list-devices` lists them.
There is no macOS backend.

A monitor of an idle sink produces no data at all, so the PulseAudio backend pads a quiet
lane with silence at clock pace. Without it the detector never sees the pause that ends a
turn.

With no audio devices to hand — a container, a remote box, CI — replay a pair of WAV files
instead. They must be 16 kHz mono 16-bit:

```bash
.venv/bin/susurro-capture \
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
