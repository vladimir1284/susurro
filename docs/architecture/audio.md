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

[Silero VAD](https://github.com/snakers4/silero-vad) runs per lane on the core, not on
the client. Keeping it server-side means the capture client stays trivial to port, and
the VAD model costs almost nothing next to the models already resident.

VAD produces two things the rest of the pipeline needs:

1. **Segment boundaries** for the recognizer, so it transcribes speech rather than
   silence.
2. **End-of-turn events** on the `far` lane, which drive speculative hint generation.

## Speech recognition

The recognizer consumes a **sliding window with overlap**, not fixed independent chunks.
Fixed chunks cut words at boundaries, and Whisper-family models hallucinate confidently on
chunks that contain only silence or a fragment. The window advances roughly every second
and re-decodes an overlapping region, so a word split across the boundary is still seen
whole in the next pass.

Each lane produces two kinds of output:

- `transcript.partial` — the current best guess, replaced on every pass, rendered greyed
  out in the console.
- `transcript.final` — committed text for a closed segment, never revised.

Only finals enter the language model's context. Feeding partials in would mean rewriting
the prompt on every pass, which destroys the prefix cache that
[Inference](inference.md#prompt-cache-is-a-requirement-not-an-optimization) depends on.

### Model choice

| Model | Languages | Notes |
|---|---|---|
| `whisper-large-v3-turbo` | Multilingual | Default. Needed for Spanish and for mixed-language meetings. |
| Parakeet TDT 0.6B | English only | Substantially faster where only English is involved. Worth benchmarking in [M0](../roadmap.md#m0-benchmark) as an English-only option. |

The default is multilingual, because a copilot that mis-transcribes the one meeting that
switched to Spanish is not a copilot you can rely on.

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
