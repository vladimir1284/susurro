# Audio pipeline

## Two lanes, never mixed

The capture client opens two independent streams and keeps them separate all the way
through transcription:

| Lane | Source | Speaker |
|---|---|---|
| `mic` | Microphone input | You |
| `far` | System output loopback (what the meeting app plays) | Everyone else |

This is the single most valuable decision in the pipeline, because it makes speaker
diarization free and exact. There is no neural diarizer, no clustering, no embedding
model, and no class of error where the system attributes the interviewer's question to
you. The operating system already knows who is speaking; capturing the lanes separately
just avoids throwing that information away.

It also supplies the trigger for automatic hints. "The other party stopped talking" is a
voice-activity event on the `far` lane alone — trivially detectable, and impossible to
express cleanly on a mixed stream.

### Platform backends

| Platform | `mic` | `far` |
|---|---|---|
| Linux | PipeWire source | PipeWire monitor of the default sink |
| macOS | CoreAudio input | Loopback device ([BlackHole](https://github.com/ExistentialAudio/BlackHole) or equivalent) |

macOS has no system loopback of its own, so a virtual audio device is a setup
prerequisite, not something Susurro can avoid. On Linux the monitor source exists by
default and needs no extra software.

## Format and transport

Each lane is resampled to **16 kHz mono**, encoded with **Opus** at roughly 24 kbit/s, and
sent as binary WebSocket frames. 16 kHz is what the speech models consume; sending
anything higher wastes bandwidth and gets downsampled anyway.

Each frame carries a 20 ms packet prefixed with the client's monotonic capture timestamp,
so the core can order and align lanes without trusting arrival time. See
[Protocol](protocol.md#capture-endpoint).

## Voice activity detection

[Silero VAD](https://github.com/snakers4/silero-vad) runs per lane on the core, in
`core/vad.py`, ahead of the recognizer. It runs on the core rather than in the capture
client for the same reason everything else does: the client stays trivial to port, and a
2 MB detector costs nothing next to the models already resident.

It produces two things:

1. **Segment boundaries**, so a transcription pass is given speech rather than silence.
2. **End-of-turn events on the `far` lane**, which start a hint before anyone asks for
   one.

The second is why this is a separate component rather than something left to the
recognizer. faster-whisper has its own VAD filter and it answers the first question well
enough, but only as a side effect of a transcription pass that runs about once a second.
A speculative hint that waits for that has already spent most of its latency budget.

### Hysteresis in both directions

A raw per-window probability is not usable as an event. Both edges are smoothed, for
different reasons:

| Edge | Default | Why |
|---|---|---|
| Opening an utterance | 2 windows (~64 ms) | A door closing is not speech. |
| Closing one | 16 windows (~512 ms) | The pauses inside a sentence are longer than people expect. |

The closing number is the one that matters. Set it too low and end-of-turn fires during a
breath, mid-sentence — the single most annoying failure this component can have, because
it produces a confident hint about half a question.

### Running it without torch

The `silero-vad` package declares a hard dependency on torch, which is an enormous thing
to install for a 2 MB model, on a machine already short of memory. Its wheel also ships
the ONNX file, so Susurro loads that directly through `onnxruntime`:

```bash
pip install --no-deps silero-vad onnxruntime
```

An energy threshold is available as `backend = "energy"` for setups without the model. It
is honest about what it is: a loudness test that cannot tell a voice from a slammed door.
It exists because it is deterministic, which makes it what the tests use, and it is a
reasonable fallback on a quiet line. `backend = "off"` disables end-of-turn detection
entirely, which leaves hints manual and everything else working.

## Speech recognition

The recognizer consumes a **sliding window with overlap**, not fixed independent chunks.
Fixed chunks cut words at boundaries, and Whisper-family models hallucinate confidently on
chunks that contain only silence or a fragment. The window advances roughly every second
and re-decodes an overlapping region, so a word split across the boundary is still seen
whole in the next pass.

### Recognition runs off the event loop

A transcription pass takes hundreds of milliseconds. Run inline on the server's event
loop, that stalls audio ingestion and the console along with it — frames queue up behind
the recognizer and the whole thing looks frozen while it works.

So `Recognizer.feed` and `flush` are called on a worker thread, serialized by a lock: a
model is not safe to call from two threads at once, and serializing also keeps the two
lanes' segments in order.

`warmup()` loads the weights once at `session.start`, before anything reports the session
live. A first run downloads the model, which takes minutes; doing that once audio is
already arriving is indistinguishable from a hang. While it happens the core reports
`stt: "loading"` in its state, so the console can say so.

This was wrong for a while and no test caught it, because the scripted recognizer used in
tests returns instantly. A fake that is faster than the real thing hides exactly this
class of bug.

Each lane produces two kinds of output:

- `transcript.partial` — the current best guess, replaced on every pass, rendered greyed
  out in the console.
- `transcript.final` — committed text for a closed segment, never revised.

Only finals enter the language model's context. Feeding partials in would mean rewriting
the prompt on every pass, which destroys the prefix cache that
[Inference](inference.md#prompt-cache-is-a-requirement-not-an-optimization) depends on.

### One language per session

A session is in exactly one language, declared in the profile before the session starts.
Sessions are sometimes in Spanish and sometimes in English; a single call never switches
between them.

That assumption is worth stating explicitly, because it buys something real. A system that
has to survive mid-sentence language switching is forced to run a multilingual recognizer
at all times and to pay its cost on every session. A system that knows the language up
front can pick the cheapest recognizer that handles it.

### Model choice

| Model | Languages | Role |
|---|---|---|
| Parakeet TDT 0.6B | English only | Fast path for `language: "en"`. Substantially faster than Whisper where only English is involved. |
| `whisper-large-v3-turbo` | Multilingual | Used for `language: "es"` and as the fallback for anything Parakeet does not cover. |

The recognizer is selected from the profile's `language` field at `session.start` and does
not change while the session runs. Changing it would mean swapping a loaded model
mid-conversation, which costs seconds on a machine with no memory to spare for keeping
both.

[M0](../roadmap.md#m0-benchmark) measures both recognizers as first-class options rather
than treating the English one as a footnote. If Parakeet turns out to be meaningfully
faster, English sessions get that headroom back and can spend it on a larger language
model or a longer context.

!!! note "What happens if the assumption breaks"
    A speaker who switches language mid-call will be transcribed badly rather than
    detected and handled. This is a deliberate trade, not an oversight. The recovery is
    manual: stop the session, change the profile, start again.

## Latency budget

End-to-end, from the other party finishing a sentence to the first useful token on your
screen:

| Stage | Target |
|---|---|
| Capture + Opus + LAN | < 50 ms |
| VAD end-of-turn detection | ~100 ms |
| Recognizer final for the last segment | 300–500 ms |
| Model time-to-first-token (warm cache) | < 1000 ms |
| **Total** | **< 2 s** |

Two seconds is the ceiling for a hint that is still worth reading. The recognizer and the
model each own roughly half the budget, and the model half is the one at risk — see
[Inference](inference.md).
