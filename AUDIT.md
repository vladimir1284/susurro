# Audit — 2026-11-16

Audit of Susurro at commit `8f430ff`. It records what the project has achieved, where it
falls short, and the work lines that follow. It is written for the agents who will execute
that work: each task names the files, the reason, and the condition that makes it done.

Read "Rules for agents" before starting any work line.

## How claims are labelled

- **Verified** — reproduced in this audit by running code. The reproduction is described.
- **Read** — follows directly from the source; not executed.
- **Inferred** — a consequence I expect but did not observe. Treat it as a hypothesis and
  confirm it before building on it.

The audit ran in the development container (aarch64, 2 cores, no GPU, no audio devices).
Nothing here is a measurement of the Mac mini.

## What "better" means for this project

Every work line below is judged against these five criteria, in this order.

1. **A hint arrives while it is still useful.** Time to first token under 1 s in the live
   system, and the hint answers the last thing said.
2. **It survives a real call.** Sixty minutes, two lanes, a reconnection, a slow model
   server — with bounded lag and no silent failure.
3. **Claims are backed by measurement on the target hardware.** A number is either
   measured on the Mac mini or labelled as an estimate.
4. **The privacy argument holds in practice.** Nothing leaves the user's machines, and
   what is stored is stored on purpose.
5. **Someone who did not build it can run it.** The documentation describes only what
   exists.

## Verdict

The engineering discipline is well above what a three-day-old public project usually has:
540 tests pass, the linter is clean, CI is green, documentation is checked against code by
tests, and every bug found on the first real call was reproduced offline before it was
fixed.

The product, however, is unproven. The project's own rule is "measure before you build",
and its roadmap says nothing gets built until the M0 benchmark runs. M0 has not run, and
milestones M1 to M6 were built anyway. All field evidence is one scripted Spanish call of
about 100 words per lane. The two claims the design rests on — a warm prompt cache, and a
first token in under a second — have never been measured on the reference machine, and
this audit found that the prompt-cache warm-up does not warm the prompt that hints
actually send (D1).

The priority is therefore not more features. It is: fix the confirmed defects, run M0,
and collect evidence from more than one call.

## Results achieved

All of the following are verified or read from the repository.

- **Test suite:** 540 passed, 1 skipped, in 64 s. `ruff check` clean. The `tests` workflow
  is green on the last four pushes.
- **Wire protocol** with strict validation (`core/protocol.py`), and tests that keep
  `docs/architecture/protocol.md` in step with it (`tests/test_docs_match_code.py`).
- **Core server** (`core/server.py`): two endpoints on one port, session state machine,
  per-lane recognition worker decoupled from the socket reader, hint deadlines, live
  measurement of time to first token.
- **Two speech backends:** in-process faster-whisper and a whisper.cpp server client.
  Vocabulary biasing from the profile cut the microphone lane's word error from 13.6% to
  5.5% on the recorded call (figure from `articles/12`, not re-measured here).
- **Voice activity detection** (Silero through onnxruntime, energy fallback), end-of-turn
  detection, speculative hints with cancellation when the far party resumes.
- **Loudspeaker bleed gate** (`core/bleed.py`).
- **Capture client** with WAV replay, PulseAudio (used on one real call) and PipeWire
  (never executed), plus reconnection.
- **Console** with profile picker and editor, session history, export; driven by browser
  tests.
- **Replay tool** (`bench/replay_call.py`) that feeds a recorded call through the real
  pipeline at real-time pace and scores word error and term recall. This is the most
  valuable test asset in the repository.
- **Honest status reporting.** The README states what was never run. That habit is worth
  keeping.

## Deficiencies

### A. Confirmed defects

**D1 — The session prefill warms a prompt that hints never send.** *Verified* (prompt
construction); *inferred* (cache effect).
`OpenAICompatGenerator.prefill` calls `self.messages(None, preamble)`. With no context,
`messages` uses the system prompt for `("en", "conversational")`. A hint uses the system
prompt for the session's language and mode. Comparing the two system messages for an
`es`/`business` session: they share zero leading characters. A prefix cache matches from
the first token, so the prefill should be useless for every session that is not English
conversational, and the first hint pays the full cold cost. Two related problems, both
*read*:

- `hint.request` carries its own `mode`. A manual hint in a different mode from the
  previous hint changes the system message and discards the cache again.
- `LAST_THING` is appended after the transcript in business mode. The next hint's
  transcript replaces that suffix. This is correct (the suffix is at the end), but no test
  asserts it.

There is no test that the prefill request is a prefix of a hint request, which is why this
survived. This defect contradicts the project's central design claim and should be fixed
first.

**D2 — `session.stop` does not cancel the hint in flight.** *Verified.*
In `Core.dispatch`, `self.state.stop()` clears `active_hint` before `cancel_hint()` runs.
`cancel_hint` returns early when `active_hint` is `None`, after it has already dropped the
task reference. Reproduction with a slow generator: 4 chunks before stop, 25 `hint.delta`
events broadcast after stop, no `hint.end` ever sent. The orphan task keeps the model busy
and its text is not recorded.

**D3 — A `hint.cancel` with a stale id makes the live hint uncancellable.** *Verified.*
`cancel_hint` swaps `self._hint_task` to `None` before checking that `hint_id` matches. A
mismatched id returns early with the reference lost. A later correct cancel finds no task
and returns; the hint keeps streaming. The console can produce this when a cancel crosses
with a superseding hint.

**D4 — A gap on a lane permanently disables its partials, and lanes do not share a
timeline.** *Verified* (recognizer); *read* (server).
`Core.route_audio` stamps frames with a per-lane frame counter and ignores the client's
timestamp. `WhisperRecognizer.lag_s` compares wall time against that counter. Any frames
that never arrive — a reconnection, or frames the client dropped — become lag that is never
recovered. Reproduction: a 10 s gap, then 30 s of audio exactly at real-time pace, yields
`lag_s = 10.0`, zero partials, and the lane still shedding. Because each lane counts from
its own first frame, a lane that connects late is also offset against the other one, so
`render_markdown`, which sorts by `t`, can order the exported conversation wrongly.

**D5 — A speech backend that fails to warm up crashes the console connection.**
*Verified* (exception escapes); *inferred* (connection closes).
`start_session` lets an exception from `recognizer.warmup()` propagate. `dispatch` catches
only `TransitionError`, `ProfileError` and `HistoryError`. Reproduction with a recognizer
whose `warmup` raises `ConnectionError`: the exception leaves `dispatch`, no `error` event
is sent, and the session stays `configured`. The language model's equivalent failure is
handled properly a few lines below; the speech one is not. With `whispercpp`, this is the
"server not running" case — the most likely first-run mistake.

### B. Design risks

**D6 — End-of-turn detection waits behind recognition.** *Read*; latency effect *inferred*.
Voice activity runs inside `_recognize_lane`, in the same queue as recognition. While a
partial decode is running (0.3–0.5 s typical on the Mac mini per `articles/12`, seconds on
a bad hour), the detector is not seeing new frames, so `SPEECH_END` — the event that starts
a speculative hint — is late by up to one decode. The same queueing delays
`BleedGate.far()`, so the gate compares microphone frames against a far level that may be
stale under load. Detection costs almost nothing and should run where frames arrive.

**D7 — The transcript grows without bound.** *Read.*
`Core.transcript` is only appended to and cleared at session start and stop. Every hint
sends all of it. `docs/architecture/inference.md` says overflow is handled "by starting a
new cache branch at a segment boundary"; no such code exists. On a long call the prompt
will exceed the model server's context window. What happens then depends on the server
(truncation that silently breaks the cache, or an error) and has not been tried.

**D8 — Every far-lane pause starts a hint.** *Read.*
`speculate` fires on each `SPEECH_END` on the far lane, with no minimum length and no check
that anything worth answering was said. A backchannel ("ajá") triggers a generation. The
design assumes discarded generations are free, but the language model and the speech model
share one machine, and `bench/replay_call.py` deliberately disables hints. Contention
between the two has never been measured.

**D9 — A recording exists only in memory until `session.stop`.** *Read.*
`finish_recording` is called only from the `session.stop` branch. A crash, a `Ctrl-C`
(`main` returns 130 without stopping the session) or a power cut loses the whole session.

**D10 — A console that reconnects starts blank.** *Read.*
`handle_console` sends `profiles` and `state` on connect, nothing else. A phone whose
screen locked mid-call reconnects with no transcript and no current hint.

**D11 — The recognizer window does not overlap, though its docstring says it does.**
*Read.* `core/stt/whisper.py` promises that "a word split across a boundary is still seen
whole on the next pass". `_reset_lane` discards the whole buffer on every final, including
the forced cut at `max_window_s = 12`. A speaker who talks for more than 12 s without a
pause gets a cut mid-word with no carry-over. Separately, each partial re-decodes the whole
growing window, so the cost of partials grows with utterance length; `articles/12` already
names this as the suspected root cause of slowness.

**D12 — The bleed gate has an absolute floor tuned on two calls.** *Read.*
`BleedGate.far_floor = 250` is raw RMS, depends on capture gain, and is not exposed in
`core.toml`. Only `bleed_ratio` is configurable.

**D13 — Transport security is thin.** *Read.*
One shared token, sent in the query string over plain `ws://`, passed to
`susurro-capture` on the command line where other local users can read it. The refusal to
bind `0.0.0.0` without a flag is good. For the reference topology (two machines on a LAN
or tailnet) this is acceptable; the limits should be stated in one place in the docs.

### C. Evidence gaps

- **M0 has not run.** Every figure in `docs/architecture/inference.md` is an estimate.
  Model tier, context budget and runtime (llama.cpp versus MLX) are undecided.
- **No milestone gate has been formally evaluated.** M1's ten-minute lag gate, M2's live
  time-to-first-token gate and M3's cross-machine gate have no recorded result.
- **One call.** One script, two voices, one room, Spanish, about 100 words per lane. The
  values `end_windows = 24`, `bleed_ratio = 2.0`, `far_floor = 250` and the business
  prompt were all tuned on it. That is fitting to one sample.
- **English has no field evidence** (*inferred* from the README and articles, which
  mention only the Spanish call).
- **Hint quality has no evaluation in the repository.** `articles/11` describes a
  keyword-count rubric; I found no hint-scoring code under version control. Two of three
  modes (`conversational`, `technical`) have never been evaluated at all.
- **PipeWire backend never executed.**
- **Twelve article drafts against one real call.** The writing is ahead of the evidence.

### D. Documentation drift and hygiene

- **macOS capture is documented but does not exist.** The README diagram says "Linux or
  macOS", `docs/guides/getting-started.md` has a macOS tab telling the user to pass
  BlackHole as `--far`, and roadmap M1 is defined as "macOS capture of both lanes".
  `capture/backends/` contains `file`, `pulse` and `pipewire` only.
- `docs/roadmap.md` M4 says the detector's positive direction "has never been exercised";
  the README says it fired on a real call. M1 to M3 carry no status.
- `docs/architecture/inference.md` lists two presets; the code has three.
- `WORKPLAN.md` ends with "Stopping here" on 2026-11-13. More than twenty commits followed.
  It no longer describes the state of the work.
- **The `docs` workflow fails on every run** (Cloudflare token lacks the Workers edit
  permission, per `WORKPLAN.md`). The documentation site has never deployed.
- `.github/workflows/tests.yml` says the real-model smoke test "runs on a schedule". There
  is no `schedule` trigger; it runs only on manual dispatch.
- `.coverage` and `.claude/scheduled_tasks.lock` are committed.
- Dependencies are unpinned lower bounds with no lock file. `silero-vad` is installed by a
  separate documented command rather than declared.

## Work lines

Ordered by priority. "Where" says whether the work can be done in the development
container or needs the Mac mini and real audio.

### Line 1 — Fix the confirmed defects

*Where: container. Criteria 1 and 2. Do this first; every later measurement depends on it.*

Each task follows the project's own rule: write the test, watch it fail, then fix.

1. **D1, prefill.** Make the prefill request a strict prefix of every hint request in the
   session. Pass the session's language and mode to `prefill`. Decide what a per-hint mode
   override means: either keep the mode-specific instruction out of the system message
   (place it after the transcript, where `LAST_THING` already sits) or accept one re-prefill
   on a mode change and say so in the protocol docs. Ask the operator before changing the
   `HintGenerator.prefill` signature in `core/llm/base.py`; it is a public seam.
   *Done when:* a test asserts, for every `(language, mode)` pair, that the serialized
   prefill messages are a prefix of the hint messages, and that two consecutive hints with
   a grown transcript share everything up to the old transcript's end.
2. **D2 and D3, hint cancellation.** In `cancel_hint`, do not drop `_hint_task` before the
   id check; in the `SessionStop` branch, cancel the hint before `state.stop()`.
   *Done when:* tests show no `hint.delta` after `session.stop`, exactly one `hint.end`
   with reason `cancelled`, the partial text recorded, and a stale `hint.cancel` followed
   by a correct one stops the stream.
3. **D5, warm-up failure.** Catch the failure in `start_session`, send an `stt_failed`
   error (or a new documented code), leave the session `configured`.
   *Done when:* a test with a raising `warmup` receives an error event and the console
   connection stays open. Update the error-code table in both protocol docs; a test
   enforces it.
4. **D4, lane clock.** Put both lanes on one session timeline that advances through gaps.
   The simplest correct option is arrival time relative to `session.start`, taken in the
   socket reader. Reset the recognizer's lag origin when a lane reconnects.
   *Done when:* the reproduction above (10 s gap, then real-time audio) yields partials
   again, and a test with a late-connecting lane exports lines in spoken order.

### Line 2 — Run M0 and evaluate the gates

*Where: Mac mini. Operator required. Criterion 3.*

An agent cannot do this from the container. Prepare it so the operator's time on the
machine is under an hour.

1. Write a single runbook page: the exact commands for llama.cpp and for MLX, the models
   to download, expected run time, and where the JSON lands.
2. Add to `bench/run.py` a case for D1: time to first token for the first hint after
   prefill, in a non-English, non-conversational session, before and after the fix. This is
   the direct measurement of whether the cache works.
3. Verify on the real server that `max_tokens=0` populates the cache. The code assumes it;
   server behaviour differs. Record the answer per runtime.
4. After the run: replace the estimates in `docs/architecture/inference.md` and its
   Spanish twin, record pass or fail for the M0 gate in `docs/roadmap.md`, and remove the
   estimate warning only then (a test guards it).

*Done when:* `bench/results/` documents a result per runtime and the roadmap states the
M0 gate outcome. If the gate fails, stop and report; do not tune around it.

### Line 3 — Build an evaluation set larger than one call

*Where: recording needs the operator; tooling in the container. Criteria 1 and 3.*

1. Define a recording protocol the operator can follow: at least five calls, both
   languages, headphones and loudspeakers, at least one unscripted, at least one of 30
   minutes. Recordings stay out of git (`.gitignore` already covers them).
2. Extend `bench/replay_call.py` to generate hints during replay through the real
   generator, so speech and language models contend as they do live. Report final lag,
   time to first token, and the count of hints started, completed and cancelled.
3. Add hint scoring to the repository. Keyword overlap is a floor, not a measure; add a
   blind human rating sheet (hint, question, 0–2 usefulness) that the operator fills in.
   Do not use a hosted model as judge: it would send call content off the machine.
4. Re-tune `end_windows`, `bleed_ratio`, `far_floor` and the three prompts on the set, and
   report the spread across calls, not one number.

*Done when:* one command replays the whole set and prints a table per call, and the
tunable defaults cite that table.

### Line 4 — Latency architecture

*Where: container for the code, Mac mini to confirm. Criterion 1. After Line 1; measure
with Line 3's tool.*

1. **D6:** run the bleed gate and voice activity in the socket reader, queue only the
   frames recognition should hear, and deliver `SPEECH_END` without waiting for a decode.
   Keep the ordering guarantee that the final for a turn is emitted before the speculative
   hint is built.
2. **D8:** gate speculation. Candidates: minimum far-turn duration, minimum words in the
   final, skip when the user is speaking. Choose by measurement on the evaluation set.
3. **D11:** stop re-decoding the whole window for each partial, or cap the partial window;
   carry an overlap across a forced cut. Correct the docstring to match whatever is true.

*Done when:* on the replay set, end-of-turn to `hint.start` is reported and does not grow
with decode time, and wasted generations per call are counted.

### Line 5 — Long-call robustness

*Where: container. Criterion 2.*

1. **D7:** implement a context budget. When the transcript approaches it, start a new
   branch at a segment boundary (a summary or the last N lines) and re-prefill once. The
   budget itself comes from M0; make it configuration and pick a conservative default.
2. **D9:** write the recording incrementally, or at least on shutdown signals.
3. **D10:** replay the current transcript and any live hint to a console on connect. This
   changes the protocol; update both protocol docs.
4. Add a soak test: 60 minutes of synthetic two-lane audio against the fakes at
   accelerated pace, asserting bounded memory and no orphan tasks.

### Line 6 — Make the documentation true

*Where: container. Criterion 5. Small; can run in parallel with Line 1.*

1. Remove or clearly mark as unbuilt every macOS capture claim (README, getting-started,
   setup, overview, audio, roadmap M1), in both languages. Whether to build a macOS
   backend is the operator's decision; do not build it unasked.
2. Bring `docs/roadmap.md` into line with the README and give each milestone a status.
3. Replace `WORKPLAN.md` with a short pointer to this audit, or archive it. Ask the
   operator first: it was committed deliberately as session state.
4. Fix the `tests.yml` comment or add the schedule. Untrack `.coverage` and
   `.claude/scheduled_tasks.lock` and ignore them.
5. The docs deployment needs a Cloudflare token with the right permission. Only the
   operator can supply it. Until then, consider disabling the workflow so the repository
   does not show a permanent red run.
6. Add a short "security model" section: shared token, plain WebSocket, trusted network
   only (D13). Expose `far_floor` in configuration (D12).

### Line 7 — Deferred

Not before Lines 1 to 3 are done: live translation, installers, a macOS capture backend,
streaming speech models, more articles. Each adds surface to a product whose core claim is
still unmeasured.

## Open decisions for the operator

These are not an agent's to make. Until each is answered, the task that depends on it
stops at the point named.

1. **D1 and the `prefill` signature.** Fixing D1 properly means `HintGenerator.prefill` in
   `core/llm/base.py` receives the session's language and mode, or the mode instruction
   moves out of the system message. Both change a backend interface. Line 1, task 1 waits
   for the choice; tasks 2 to 4 do not.
2. **`WORKPLAN.md`.** Archive it, or replace it with a pointer to this file. It was
   committed on purpose as the state of an unattended session, so it is not deleted
   without a yes.
3. **Order of M0 against Line 4.** If M0 fails the one-second gate even after D1 is
   fixed, the response is a smaller model or a shorter prompt, and Lines 4 and 5 change
   shape. Recommendation: run Line 1, then M0, and only then start Line 4. Lines 3 and 6
   are safe to run in parallel with either.
4. **macOS capture.** Build a backend, or remove the claim from the documentation. Line 6
   removes the claim by default.
5. **Cloudflare token for the docs site.** Supply one with the Workers edit permission, or
   approve disabling the `docs` workflow.
6. **Recording the evaluation calls** (Line 3, task 1). Only the operator can make them.

## How this audit was produced

- Read: every module under `core/`, `capture/` and `bench/replay_call.py`; the README,
  roadmap, inference page, `WORKPLAN.md` and articles 11 and 12. `core/protocol.py`,
  `bench/run.py` and `console/app.js` were skimmed, not read line by line, and the test
  files were not reviewed for quality. Findings in those areas are therefore incomplete.
- Ran: `pytest -q` (540 passed, 1 skipped), `ruff check .` (clean), `gh run list` for CI
  status.
- Reproduced D1 to D5 with throwaway scripts that drove `Core.dispatch`,
  `OpenAICompatGenerator.messages` and `WhisperRecognizer.feed` directly with stub
  backends. The scripts are not in the repository; Line 1 turns each into a permanent
  test.
- Coverage was not measured: `pytest-cov` is not installed. The 90% figure in
  `WORKPLAN.md` is from 2026-11-13 and was not re-checked.

## Rules for agents

1. **Do not reopen settled decisions:** two lanes never mixed; only `transcript.final`
   enters the model context; no retrieval in v1; 8B model ceiling; one language per
   session; no proctoring evasion or anti-detection of any kind.
2. **No performance number from the container.** Timings taken there say nothing about
   the Mac mini. If you must report one, label its origin.
3. **Reproduce first.** For every defect: a test that fails without the fix, then the fix.
   State in the commit that you saw it fail.
4. **Documentation is bilingual and tested.** Change `X.md` and `X.es.md` together. Run
   `pytest -q` and `ruff check .` before every commit; both must pass.
5. **Never commit recordings, real profiles, `core.toml` or tokens.**
   `tests/fixtures/profiles/local/` holds real personal profiles and is ignored on purpose.
6. **One work line per branch**, conventional commit messages as in the existing history.
7. **Stop and ask** when a task needs the Mac mini, real audio, a credential, or a change
   to the wire protocol or a backend interface that this audit did not already specify.
8. **Verify before trusting this audit.** Items marked *read* or *inferred* are not
   proven. If one turns out to be wrong, correct this file in the same commit.
