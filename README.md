# Susurro

**A local-first, real-time copilot for meetings and interviews.** Speech recognition and
language model inference run on hardware you own. Audio never leaves your network.

📖 **[Documentation](https://susurro-docs.workers.dev/)** · [Español](https://susurro-docs.workers.dev/es/)

---

## Why

Hosted interview copilots upload your audio, run the models remotely, and stream hints
back. On a slow uplink that round trip takes long enough that the hint arrives after the
conversation has moved on. Susurro removes the upload: a thin capture client sends Opus
audio over your LAN to a machine on the same network, which transcribes, reasons, and
streams hints to a console on your phone or second screen.

The secondary benefit is privacy. Interviews and business calls are exactly the kind of
conversation that should not sit in a third party's logs.

## How it is put together

```
capture client  ──Opus/WS──▶  core  ──JSON/WS──▶  console
(mic + loopback)              (VAD, STT, LLM)     (phone / 2nd screen)
 Linux or macOS               Mac mini M4 16 GB    any browser
```

Three key decisions, explained in the docs:

- **[Two audio lanes, never mixed](https://susurro-docs.workers.dev/architecture/audio/)** —
  microphone and system loopback stay separate, which makes speaker attribution exact and
  free, with no diarization model.
- **[Prompt cache as a requirement](https://susurro-docs.workers.dev/architecture/inference/)** —
  the static context is prefilled once per session so each hint only pays for the new
  transcript lines.
- **[Speculative generation](https://susurro-docs.workers.dev/architecture/inference/)** —
  hints start generating when the other party stops talking, not when you press a key.

## Scope

Susurro is a copilot, not a cheating tool. It will not implement proctoring evasion,
screen-share hiding, or any other anti-detection feature. The console runs on a second
device because hints should not be in your screen share — that is a design consequence,
not a stealth feature.

## Status

**Pre-M0.** Nothing is implemented. The architecture is documented, the plan is public,
and the first milestone is a benchmark whose job is to replace the current estimates with
measurements. See the [roadmap](https://susurro-docs.workers.dev/roadmap/).

The most useful early contribution is a benchmark result from hardware other than the
reference Mac mini.

## License

[AGPL-3.0](LICENSE).
