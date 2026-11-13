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

- [x] **WP0 — Session language decision**
      One language per session, set per profile. English sessions may use a faster
      English-only recognizer. Update `docs/architecture/audio.md` and the profile schema
      in `docs/architecture/protocol.md`, both languages.

- [x] **WP1 — Protocol**
      `core/protocol.py`: message schemas, binary frame encode/decode, version
      negotiation, validation errors. Full unit tests.

- [x] **WP2 — Core server skeleton**
      `core/server.py`: both WebSocket endpoints, token auth, session state machine,
      lane registry. Backend interfaces `core/stt/base.py` and `core/llm/base.py` with
      fake implementations driven by fixtures. Integration test asserting the full event
      sequence from capture frames to console events.

- [x] **WP3 — Capture client**
      `capture/backends/file.py` replaying a WAV in real time (testable here, and useful
      forever for deterministic tests). `capture/backends/pipewire.py` written but
      untested — no audio devices in this container. Opus round-trip tests.

- [x] **WP4 — Console**
      Static HTML/CSS/JS. Two transcript lanes, partial vs final rendering, hint area,
      controls. Playwright test against the core running fake backends.

- [x] **WP5 — Real backends** *(was: end-to-end with toy models)*
      `faster-whisper` tiny and a sub-1B GGUF on CPU. Proves the spine end to end.
      Any timing produced here is meaningless for M0 and must be labelled as such.

- [x] **WP6 — Benchmark harness** *(stretch)*
      `bench/run.py` implementing the M0 procedure in `docs/hardware.md`. Dry-run here
      against toy models to prove the harness, not the hardware.

### Added after WP6, while the Mac mini is still unavailable

- [x] **WP7 — Test CI**
      The repository runs 257 tests and nothing runs them on push. A GitHub Actions
      workflow for `pytest`, with the Playwright browser cached, matching the strictness
      of the docs workflow.

- [ ] **WP8 — Repository front door**
      `README.md` still says "nothing is implemented", which stopped being true four
      commits ago. Update it, and add a `CONTRIBUTING.md` that says what a useful first
      contribution is — a benchmark result from hardware that is not the reference Mac
      mini.

- [ ] **WP9 — Review pass**
      Read the whole repository as a stranger would. Find the places where the code and
      the documentation have drifted apart, and the claims that are no longer true.

## Articles

English, self-contained, written for readers who do not know the project and are not
necessarily engineers. Drafts land in `articles/`. **They are not posted** — this session
has no LinkedIn access and will not acquire any. The operator reviews and posts.

1. Why a copilot that runs on your own machine (context, the slow-uplink problem) — drafted
2. Two microphones, zero machine learning (the unmixed-lanes decision) — drafted
3. Prompt caching, explained without jargon — drafted
4. Building a system you cannot run (fakes, honest labels, measuring your own code) — drafted
5. Designing a UI you read while keeping eye contact — drafted
6. The interface decides whether the feature is fast (prefill/stream, decode placement) — drafted
7. Writing the benchmark before the feature (gates in code, ways to measure nothing) — drafted

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
- `2026-11-13` — Repository secrets set. CI build step passes; the deploy step fails with
  `No access to the specified resource` because the Cloudflare API token carries
  Workers Scripts **Read** but not **Edit**. A pivot to Cloudflare Pages was attempted and
  abandoned: Pages requires the project to exist first, and this token cannot create one
  (`wrangler pages project create` needs `User -> Memberships -> Read`; the REST create
  returns a generic 8000000). Workers needs no pre-created resource, so the pipeline stays
  on Workers and the blocker is a single token with `Edit Cloudflare Workers` scope,
  pending the operator. **This blocks only the published docs site, not any work package.**
  Treat a red `docs` run with a passing build step and a failing deploy step as this known
  blocker, not a regression.
- `2026-11-13` — WP0 done. One language per session, declared in the profile, fixed for
  the session's lifetime. Documented in `docs/architecture/audio.md` (new section, replaces
  the multilingual-by-default reasoning) and in the profile schema in
  `docs/architecture/protocol.md`, both languages. Consequence recorded: Parakeet becomes
  the English fast path rather than a footnote, and M0 must measure both recognizers as
  first-class options. Article 1 drafted.
- `2026-11-13` — Profile fixtures added in `tests/fixtures/profiles/`: one English
  technical screen, one Spanish business meeting, so both recognizer-selection paths are
  covered. The committed fixtures are synthetic. The operator offered a real public
  profile page as test material, and the first attempt to commit it was correctly blocked:
  a public test fixture is not the place for a real person's employment history, whatever
  its original publication status. Real profiles belong in `tests/fixtures/profiles/local/`,
  which is gitignored. First check of the context-budget claim: the synthetic profiles
  carry roughly 750 and 530 tokens of static context by a chars/4 estimate, well inside
  the 2-3k assumed in `docs/architecture/inference.md`, which supports skipping retrieval
  in v1. The estimate understates Spanish and no real tokenizer has been run.
- `2026-11-13` — WP1 done. `core/protocol.py` implements both endpoints' messages with
  stdlib only: hello parsing with version negotiation, the binary audio frame (little-endian
  uint64 microsecond timestamp plus Opus packet), console events, and console command
  parsing. Validation is strict — unknown fields and out-of-range values are rejected
  rather than ignored. 67 tests pass. One real bug caught by its own test: the version
  check ran *after* unknown-field rejection, so a newer client carrying a new field would
  have been told about the field instead of about the version. Fixed, and the test that
  found it is the one asserting the ordering. Article 2 drafted, on the unmixed-lanes
  decision.
- `2026-11-13` — WP2 done. `core/server.py` serves both endpoints over one session, with
  recognition and hint generation injected so the whole server runs against fakes and needs
  no model weights. Added `core/session/` (profile loading and a pure state machine),
  `core/stt/` (recognizer interface plus a scripted fake) and `core/llm/` (hint generator
  interface plus a canned fake that counts prefills). 126 tests pass. The load-bearing one
  asserts `prefill_count == 1` after three hints: the latency budget rests on the preamble
  being processed once per session, and that is a property of the server rather than of any
  model, so it is testable here and worth locking down before a real backend exists.
  Design decisions made while implementing, now reflected in code and tests: a new hint
  supersedes one still in flight rather than being refused, because a speculative hint is
  usually still streaming when the next end-of-turn arrives; frames arriving before
  `session.start` are dropped rather than buffered, since there is no prefilled cache to
  transcribe into; profile ids are rejected if they could walk out of the profile
  directory. Article 3 drafted, on prompt caching.
- `2026-11-13` — WP3 done. Capture client: `capture/codec.py` (real Opus via libopus,
  installed in this container), `capture/backends/file.py` (WAV replay, real-time paced,
  `speed=0` for tests), `capture/backends/pipewire.py` (**written, never executed** — no
  `/dev/snd` and no PipeWire daemon here; its module docstring says so and M3 exists to
  run it), `capture/client.py` and a CLI. 148 tests pass, including an end-to-end path with
  every component real except the recognizer: WAV, Opus, WebSocket, core, console. Stereo
  WAVs are downmixed; a trailing partial frame is dropped rather than zero-padded, since
  padding would put 20 ms of invented silence into a transcript; wrong sample rates are
  refused with the ffmpeg command that fixes them rather than resampled, because a
  resampler in the capture path is a quality decision that belongs to the OS audio server.
  Docs updated with the replay flags. Article 4 drafted.
- `2026-11-13` — WP4 done. Console is static HTML, CSS and JavaScript with no build step,
  served by the core from the same port as the WebSocket endpoints. The view model lives in
  `console/state.js`, pure and DOM-free, so the behaviour that is easy to get wrong is
  testable on its own: a partial replaced rather than appended, a final clearing only its
  own lane, a delta for a superseded hint discarded, a cancelled hint removed rather than
  greyed out. 167 tests pass, 19 of them driving a real Chromium.
  Two things found while writing the tests: a `file://` page cannot dynamically import a
  module, so the view-model tests load the page from the running core like a browser really
  does; and a JavaScript module served with the wrong content type fails as a blank page
  rather than an error, so there is now a test asserting the content type. Design decisions
  recorded in the article: largest type goes to the hint because it is the only thing read
  without turning one's head, the transcript is deliberately quieter because its job is to
  prove the system is keeping up, and provisional text is shown dimmed rather than hidden
  because a two-second blank looks identical to a crash. Article 5 drafted.
- `2026-11-13` — WP5 done, with a deliberate change of shape. The package said "end-to-end
  with real toy models on CPU". Running a toy LLM here would have produced a demo whose
  only honest conclusion is "a 0.5B model on two ARM cores is slow", which nobody needs.
  What was built instead is the **real** backends, tested without needing the models to be
  fast: `core/llm/openai_compat.py` (any OpenAI-compatible server — llama.cpp, Ollama,
  vLLM, LM Studio) tested against a mock transport, and `core/stt/whisper.py`
  (faster-whisper) whose windowing logic is tested against a stubbed model. The real
  whisper model *was* run once as an opt-in smoke test (`SUSURRO_SLOW_TESTS=1`), which
  loads and transcribes in about 14 seconds here — a number that says the wiring works and
  nothing at all about the target hardware.
  Also refactored: Opus decoding moved out of the recognizers and into the server, since
  every recognizer wants PCM and none wants a wire format. One decoder per lane, because
  Opus carries predictor state and sharing one would corrupt both streams. And
  `core/main.py` plus `core.toml.example` make the core actually runnable, with backends
  chosen by configuration rather than by import, and a refusal to bind `0.0.0.0` without
  `--allow-public-bind`. 226 tests pass, 1 skipped by design. Article 6 drafted.
- `2026-11-13` — WP6 done. `bench/` implements the M0 procedure: STT real-time factor,
  cold prefill at 1k and 4k, warm time-to-first-token, sustained decode rate, peak resident
  memory. Both runtimes worth comparing speak the OpenAI API, so there is one client rather
  than two code paths — `llama-server` and `mlx_lm.server` are each measured by running
  them in turn with `--label`.
  Three refusals built in, each guarding against a way to measure nothing: `--audio` is
  required and must be real speech, because silence gives the recognizer less work and a
  flattering factor; the cold-prefill prompt varies per repeat, because repeats against a
  warm server cache measure the cache; and the memory figure needs `--server-pid`, because
  the model runs in another process and measuring the harness would report a few hundred
  megabytes and look like good news. Gates live in `bench/report.py` with the exit code
  reflecting them, so a disappointing number cannot be talked down to a pass, and a gate
  with no measurement behind it explicitly fails. Every result records the machine it came
  from. 257 tests pass. One test was wrong rather than the code: a p90 over ten samples
  with one outlier legitimately excludes it, so the test now uses two outliers to
  demonstrate what it claims. Article 7 drafted.

**WP0 through WP6 are done.** What remains of the original plan needs the Mac mini: run
`python -m bench.run` against `llama-server` and against `mlx_lm.server`, commit both
result files, then replace the estimates in `docs/architecture/inference.md` and
`docs/hardware.md` with the measurements and delete the estimate warnings. M3 — running
`capture/backends/pipewire.py` on real hardware for the first time — also waits for a
machine with audio devices.
- `2026-11-13` — WP7 done. `.github/workflows/tests.yml` runs ruff and the full suite on
  every push and pull request, with the Playwright browser cached on the resolved
  Playwright version — a looser cache key serves a browser the client then refuses to
  drive. `libopus0` is installed explicitly because opuslib binds it through ctypes and the
  wheel does not carry it. The opt-in real-model test runs on `workflow_dispatch` rather
  than never: it says nothing about the target hardware, but it is the only thing that
  would catch faster-whisper changing its API underneath us.
  Ruff added at the same time, configured in `pyproject.toml` and clean. Twelve findings,
  ten auto-fixed; the two left were decisions rather than slips. `SessionPhase` now
  inherits `StrEnum`. `WavSource` keeps its `wave.open` outside a context manager with a
  `noqa` and a comment: a source is read incrementally and closed by its owner, which is
  what `close()` is for. `E501` is ignored on purpose — the comments in this project
  explain why decisions were made, and a hard wrap that splits a sentence badly is worse
  than a long line.
