# Overview

Susurro is three processes, deliberately separated. Each one can live on a different
machine, and in the reference setup two of them do.

```
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  capture client  │      │      core        │      │     console      │
│                  │      │                  │      │                  │
│  mic lane  ──────┼─ WS ─┼─▶ VAD ─▶ STT     │      │  transcript      │
│  far lane  ──────┼─────▶│      │           ├─ WS ─┤  hints           │
│                  │ Opus │      ▼           │ JSON │  controls        │
│  (meeting host)  │      │   context + LLM  │      │  (phone / 2nd    │
│                  │      │                  │      │   screen)        │
└──────────────────┘      └──────────────────┘      └──────────────────┘
  Linux N95 or Mac          Mac mini M4 16 GB          any browser
```

## Why three processes and not one

**The meeting and the inference compete for the same resources.** On 16 GB of unified
memory, a browser running a video call alongside a 5 GB model and a 1.6 GB speech model
will page to swap, and swap on the inference path is fatal to latency. Keeping the core
on a dedicated machine removes the contention by construction.

**The console has to be elsewhere.** If the hints render on the screen you are sharing,
they are visible to everyone in the call. A separate device is the only arrangement that
works, and it falls out naturally once the core is already a network service.

**The capture client is portable, the core is not.** Capture is a few hundred lines per
platform — PulseAudio and PipeWire on Linux are built; other platforms (macOS, say) are
not, but would not touch the core. The core is a pile of model weights and
accelerator-specific runtimes. Separating them means a new client platform is a small
amount of work and does not touch inference at all.

## Components

### Capture client

Opens two audio streams, encodes each to Opus, and ships them over a WebSocket to the
core. Runs on the machine hosting the meeting. Deliberately dumb: no VAD, no buffering
policy, no model. See [Audio pipeline](audio.md).

### Core

Owns everything expensive. Receives both lanes, decodes them, runs voice activity
detection, streams
each lane through speech recognition, maintains session context, and drives the language
model. Also serves the console as static files, so there is one address to remember. See
[Inference](inference.md).

### Console

A browser page. Two transcript lanes, a hint area, a few controls. No framework — it is a
scrolling list of text and a WebSocket. Runs on a phone, a tablet, or a second monitor.

## Network assumptions

All three processes are expected to be on the same LAN, or on the same
[Tailscale](https://tailscale.com/) tailnet. The core binds to a LAN interface, not to
`0.0.0.0` by default, and requires a shared token on both WebSocket endpoints. This is
enough for a trusted home network; it is not a substitute for real authentication, and
the core should not be exposed to the public internet.

That matters more than it used to. The console can **write** profiles, so the token is a
write credential, not just a read one: whoever holds it can create and overwrite the
context a session is built from.

LAN round-trip is on the order of a millisecond and Opus at 16 kHz mono costs roughly
24 kbit/s per lane, so the transport contributes nothing meaningful to the latency budget.
That is the entire point of the design: the expensive link in a hosted copilot is the one
Susurro does not have.
