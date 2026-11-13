# Work plan — unattended weekend session

**Window:** 2026-11-13 → 2026-11-16
**Operator:** away. Session runs unattended in tmux on the remote container.

This file is the durable state of the autonomous session. It is committed, so progress
survives a session restart, a quota pause, or a container bounce. Any resumed session
should read this file first and continue at the first unchecked task.

## Environment constraints

| Fact | Consequence |
|---|---|
| `aarch64`, 2 cores, 11 GB RAM, no GPU | [M0](docs/roadmap.md) cannot run here. It measures an M4. |
| No `/dev/snd`, no PipeWire | Audio capture cannot be tested against real devices. |
| Network open (PyPI, HuggingFace, GitHub) | Toy models can be downloaded for integration tests. |

### The M0 problem, stated plainly

The roadmap says M0 gates everything, and that rule exists for a good reason: the
architecture rests on inference latency numbers that are currently estimates.

M0 is not executable on this hardware. Rather than fake it or ignore it, this session
builds only the parts whose **design does not depend on M0's results**:

- The wire protocol and its framing.
- Lane handling, session state machine, profile storage.
- The console UI.
- The test harness, including fake STT and LLM backends.

Deliberately **not** built here:

- Model tier selection, quantization choices, context budget.
- Prompt cache strategy beyond the interface that permits it.
- Anything whose correct shape is decided by a number M0 has not produced.

The benchmark harness itself (`bench/run.py`) is written here and dry-run against toy
models, so that it runs on the Mac mini the moment the operator is back.

## Work packages

Each package ends in: passing tests, a commit, docs updated if the design moved, and a
LinkedIn draft if there is a lesson worth writing down.

- [ ] **WP0 — Session language decision**
      One language per session, set per profile. English sessions may use a faster
      English-only recognizer. Update `docs/architecture/audio.md` and the profile schema
      in `docs/architecture/protocol.md`, both languages.

- [ ] **WP1 — Protocol**
      `core/protocol.py`: message schemas, binary frame encode/decode, version
      negotiation, validation errors. Full unit tests.

- [ ] **WP2 — Core server skeleton**
      `core/server.py`: both WebSocket endpoints, token auth, session state machine,
      lane registry. Backend interfaces `core/stt/base.py` and `core/llm/base.py` with
      fake implementations driven by fixtures. Integration test asserting the full event
      sequence from capture frames to console events.

- [ ] **WP3 — Capture client**
      `capture/backends/file.py` replaying a WAV in real time (testable here, and useful
      forever for deterministic tests). `capture/backends/pipewire.py` written but
      untested — no audio devices in this container. Opus round-trip tests.

- [ ] **WP4 — Console**
      Static HTML/CSS/JS. Two transcript lanes, partial vs final rendering, hint area,
      controls. Playwright test against the core running fake backends.

- [ ] **WP5 — End-to-end with real toy models** *(stretch)*
      `faster-whisper` tiny and a sub-1B GGUF on CPU. Proves the spine end to end.
      Any timing produced here is meaningless for M0 and must be labelled as such.

- [ ] **WP6 — Benchmark harness** *(stretch)*
      `bench/run.py` implementing the M0 procedure in `docs/hardware.md`. Dry-run here
      against toy models to prove the harness, not the hardware.

## Articles

English, self-contained, written for readers who do not know the project and are not
necessarily engineers. Drafts land in `articles/`. **They are not posted** — this session
has no LinkedIn access and will not acquire any. The operator reviews and posts.

1. Why a copilot that runs on your own machine (context, the slow-uplink problem)
2. Two microphones, zero machine learning (the unmixed-lanes decision)
3. Why the benchmark comes before the code (gates, and the cost of skipping them)
4. Prompt caching, explained without jargon
5. Designing a UI you read while keeping eye contact
6. What building against a 16 GB ceiling forces you to decide

## Rules for the unattended session

1. **Commit after every green package.** Conventional Commits, body explains the why.
2. **Never push a red build.** `mkdocs build --strict` and `pytest` before every push.
3. **Update this file** — tick the box, append to the log below — in the same commit.
4. **Do not invent measurements.** Any number from this container is labelled as coming
   from a 2-core ARM container with no GPU, or it is not written down.
5. **Stay inside this repository.** No other repo, no other service, nothing posted.
6. **When blocked, write it in the log and move to the next package.** Do not wait.

## Log

- `2026-11-13` — Session started. Environment surveyed, constraints recorded above.
  Plan written. CI is red: the `docs` workflow fails because `CLOUDFLARE_API_TOKEN` and
  `CLOUDFLARE_ACCOUNT_ID` are not set as repository secrets; writing them requires the
  operator.
