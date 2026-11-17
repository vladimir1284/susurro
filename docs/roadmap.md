# Roadmap

Each milestone ends in a **gate**: a measurable condition that decides whether the next
one starts. A gate that fails is not a reason to push on and hope; it is a reason to
change the design while changing it is still cheap.

M0 through M3 together are the minimum real product. Everything after is increment.

---

## M0 — Benchmark

**Status: measured. 14 rounds on 2026-11-16/17 (`bench/results/2026-11-17-overnight.md`, distorted by the idle Mac sleeping ~13 s every minute, see Hardware) and a clean rerun of 7 rounds with the Mac awake (`bench/results/2026-11-17-awake.md`: 0 failed steps, no stalls, lag of finals 0.8 s mean). Gate conditions met by gemma-4-E2B and LFM2.5-1.2B under llama.cpp, not by LFM2 2.6B. Memory with both models loaded, more than one speech clip and MLX not measured.**

**Half a day. Nothing else gets built until this is done.**

The entire architecture rests on inference numbers that are currently estimates. Writing
a capture client before knowing whether the model can answer in under a second is
building on a guess.

Measure on the reference Mac mini:

- Speech recognition real-time factor for `whisper-large-v3-turbo` at q8, and for
  Parakeet TDT as an English-only alternative.
- Language model prefill throughput at 1k and 4k context, cold.
- Prefill with a warm prefix cache, measuring only the delta.
- Decode throughput, sustained.
- Peak resident memory with both models loaded.
- All of the above for `llama.cpp` and for `mlx-lm`.

Deliverable: a script in `bench/` that produces a JSON result, and a results page in
these docs.

!!! success "Gate"
    Time-to-first-token with a warm cache, 2k of static context plus 100 tokens of
    delta: **under 1 second**. Both models resident in **under 12 GB**.

    If TTFT fails, the response is to shorten the prompt or drop to a smaller model —
    not to proceed and discover it later. If memory fails, the 8B assumption was wrong
    and the model tier moves down before any code depends on it.

---

## M1 — Spine

**Status: built on Linux (PulseAudio), used on one real call. Ten-minute lag gate not evaluated.**

**One to two days.**

End-to-end audio path with no language model at all: capture of both lanes (PulseAudio on
Linux; there is no macOS backend), Opus
over WebSocket, VAD, streaming recognition, console rendering two transcript lanes.

This proves the hard part of the plumbing — two independent streams, partial and final
transcript semantics, a console that updates without flicker — while the failure modes
are still about audio and not about prompts.

!!! success "Gate"
    Transcript lag under **1 second** behind live speech, both lanes, sustained over a
    ten-minute call without the recognizer falling behind.

---

## M2 — Manual hint

**Status: built. Time-to-first-token gate not evaluated; it needs M0's numbers.**

**One to two days.**

Hotkey or console button triggers a hint. Session preamble prefilled at
`session.start` and held in the prompt cache. Tokens stream to the console as they are
produced.

!!! success "Gate"
    TTFT under **1 second** in the live system, matching the M0 bench. The hint is
    short enough to read at a glance and relevant often enough to be worth glancing at.

    The second half of that gate is subjective and should be judged on real calls, not
    on demos.

---

## M3 — Linux client

**Status: PulseAudio built and used; PipeWire written, never executed. Cross-machine gate not evaluated.**

**One day.**

PipeWire capture on the N95, same protocol, no core changes. If the core needs changing
to accept a second client platform, the protocol was wrong and gets fixed here.

!!! success "Gate"
    A full session driven from the Linux machine with the Mac mini as core, latency
    within noise of a core running on the capture machine itself.

**This is the first genuinely useful version.**

---

## M4 — Speculative hint

**Built.** End-of-turn on the `far` lane starts generation before you ask for it, and the
other party resuming cancels it. A hint you asked for yourself is never cancelled this
way — you asked for it.

The recognizer is flushed when the turn ends, before generating. Without that, the hint
would be written against a transcript missing the sentence it is supposed to answer,
because the final for it has not been emitted yet.

!!! success "Gate"
    Perceived latency drops measurably against M2 on the same conversations, and false
    starts are not distracting in practice.

    **Not evaluated.** Both halves need real calls to judge. The detector did fire on
    speech in one recorded two-lane Spanish call, but one call says nothing about a
    variety of voices and lines.

---

## M5 — Session context

Profiles: résumé, job description or meeting agenda, notes. Preset modes — conversational,
technical, business meeting.

**Built.** Profiles load from disk, the console lists them, picks one, and edits one. The
chosen profile decides the language and the default hint style. The picker locks while a
session is live, because changing it would invalidate the prefilled cache.

Saving is refused for the profile a live session was built from, for the same reason, and
the editor says so instead of failing quietly. Writes are atomic: a crash mid-save leaves
the previous profile intact rather than a half-written one.

Profiles remain plain JSON files. The editor is a convenience, not a format.

!!! success "Gate"
    Hints reference your actual background. A profile switch takes effect with one
    preamble prefill and no measurable per-hint cost.

    **Unmeasured.** The second half needs M0's numbers.

---

## M6 — Polish

**Session history and transcript export: built.** Recording is off by default and on
purpose — this project's argument is that a conversation stays on machines you own, so
writing every meeting to disk unasked would undercut it. When it is on, a red indicator
sits in the console's header for the whole session. Each session is one JSON file, Markdown
is rendered on demand rather than stored twice, and the console lists, opens, downloads and
deletes them. Deleting takes two clicks.

Still to do: live translation, packaged installers, and setup documentation good enough for
someone who did not build this.

---

## Explicitly out of scope

- Proctoring or monitoring evasion, screen-share hiding, anti-detection of any kind.
- Retrieval over the static context — see
  [Inference](architecture/inference.md#no-retrieval-in-v1).
- A hosted version. The point of the project is that there is no server.
- Mobile capture clients. The phone is the console, not the microphone.
