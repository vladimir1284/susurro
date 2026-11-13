# Contributing

## The most useful thing you can do

**Run the benchmark on hardware that is not the reference Mac mini, and send the result.**

The whole design rests on how fast one small machine runs two models. Every memory figure,
every latency budget, the choice of model size — all of it comes from measurements on one
piece of hardware, or at the moment from estimates about it. Knowing where it breaks on
other machines is worth more than any feature.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[bench]"
# start llama-server or mlx_lm.server on port 8080, then:
.venv/bin/python -m bench.run --label <your-hardware> --audio <30s of real speech>.wav
```

Open a pull request with the JSON file in `bench/results/`. Do not edit the numbers, and do
not leave out a run that failed a gate — a failure on your hardware is the result.

## Running the tests

```bash
sudo apt-get install -y libopus0          # opuslib binds it through ctypes
python3 -m venv .venv
.venv/bin/pip install -e ".[core,openai,whisper,dev]"
.venv/bin/pip install --no-deps silero-vad   # ONNX model only; the package declares torch
.venv/bin/playwright install chromium     # for the console tests
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

The suite runs without any language model and takes well under a minute. Tests that need
something absent skip with a message saying what to install, rather than failing: the
Silero detector tests if you skipped the `--no-deps` line above, and one test that loads a
real speech model, enabled with `SUSURRO_SLOW_TESTS=1`.

## Conventions

These are not style preferences. Each one exists because breaking it has a specific cost.

**Do not invent measurements.** Any number in the documentation that was not measured is
labelled as an estimate, with the milestone that will replace it. A number from the wrong
machine is worse than no number, because it looks like evidence. If you quote a result,
say what hardware produced it — the benchmark records that in every result file for exactly
this reason.

**Label code you have not run.** `capture/backends/pipewire.py` says in its first lines
that it has never been executed, and why. Unrun code that looks finished is a trap for
whoever touches it next. Unrun code that says so is a task.

**The prompt is append-only.** Anything that rewrites earlier tokens invalidates the
model's prefix cache and turns a sub-second hint into a multi-second one, with no error
anywhere. This is why only finalized transcript text reaches the model, why the profile
cannot change mid-session, and why there is a test asserting that the same profile produces
byte-identical text twice. That test is not pointless.

**Documentation is both languages or neither.** Pages live as `page.md` (English) and
`page.es.md` (Spanish). A half-translated site is worse than a monolingual one, because a
reader cannot tell which pages they are missing. Anchors differ between languages; the
strict build catches broken links.

**Commit messages explain why.** What changed is in the diff. The message is for the
decision: what was considered, what was rejected, what breaks if someone undoes it. Use
[Conventional Commits](https://www.conventionalcommits.org/) for the subject line.

**Tests assert behaviour, with a comment when the behaviour is surprising.** A cancelled
hint is removed from the console rather than greyed out, and the test says why — otherwise
someone will reasonably "fix" it.

## Scope

Pull requests implementing proctoring evasion, screen-share hiding, or anti-detection of
any kind will be closed. The console runs on a second device because hints should not be in
your screen share; that is a consequence of the design, and it is not going to be developed
into a stealth feature.
