# Susurro

**A local-first, real-time copilot for meetings and interviews.** Speech recognition and
language model inference run on hardware you own. Audio never leaves your network.

[![tests](https://github.com/vladimir1284/susurro/actions/workflows/tests.yml/badge.svg)](https://github.com/vladimir1284/susurro/actions/workflows/tests.yml)

📖 Documentation: [`docs/`](docs/) — English and Spanish, built with mkdocs-material.

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
(mic + loopback)              (STT, hints)        (phone / 2nd screen)
 Linux or macOS               Mac mini M4 16 GB    any browser
```

Three decisions, each explained in the docs:

- **[Two audio lanes, never mixed](docs/architecture/audio.md)** — microphone and system
  loopback stay separate, which makes speaker attribution exact and free, with no
  diarization model, and supplies the end-of-turn signal for speculative hints.
- **[Prompt cache as a requirement](docs/architecture/inference.md)** — the static context
  is prefilled once per session, so each hint only pays for the new transcript lines. The
  prompt is append-only, which is why only finalized transcript text ever reaches the
  model.
- **[Measure before you build](docs/roadmap.md)** — every performance number in the docs is
  currently an estimate, labelled as one, and the first milestone exists to replace them.

## Status

The plumbing is built and tested. The measurements are not taken.

| Piece | State |
|---|---|
| Wire protocol | Done — `core/protocol.py` |
| Core server, session state, profiles | Done — `core/server.py`, `core/session/` |
| Capture client, Opus, WAV replay | Done — `capture/` |
| PipeWire capture | **Written, never executed.** No audio devices on the development machine. |
| Console | Done — `console/`, driven by browser tests |
| Speech backend (faster-whisper) | Done — `core/stt/whisper.py` |
| Hint backend (any OpenAI-compatible server) | Done — `core/llm/openai_compat.py` |
| Voice activity, end-of-turn, speculative hints | Done — `core/vad.py`. Verified on silence, noise and tones; **never exercised on real speech.** |
| M0 benchmark harness | Done — `bench/` |
| **M0 measurements** | **Not taken.** Needs the Mac mini. |

Everything can run without models: the core ships fake speech and hint backends, and the
capture client can replay a WAV instead of opening a device. That is how the console, the
protocol and the client were developed on a machine that cannot host a model at all.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[core,dev]"
.venv/bin/python -m core.main --profiles tests/fixtures/profiles
```

For the real thing, see [`docs/development/setup.md`](docs/development/setup.md).

## Scope

Susurro is a copilot, not a cheating tool. It will not implement proctoring evasion,
screen-share hiding, or any other anti-detection feature. The console runs on a second
device because hints should not be in your screen share — a design consequence, not a
stealth feature.

## Contributing

The most useful contribution right now is a benchmark result from hardware that is not the
reference Mac mini. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[AGPL-3.0](LICENSE). The hosted equivalent of this tool is sold as a subscription; the
license is meant to keep any derivative offered as a service open as well.
