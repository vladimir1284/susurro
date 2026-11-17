# M0 runbook

How to run the M0 benchmark on the reference machine (Mac mini M4, 16 GB). Budget: under an
hour on the machine, most of it starting servers. Every number this produces is a
measurement of **that machine at that moment**: the Mac's speed has varied by about 3x over
an evening, so run each runtime at least twice, at different times, and keep both files.

!!! warning "Nothing here has been run yet"
    The commands below are the intended procedure. The first operator to run them should
    fix this page where it is wrong.

## What you need

- The repository checked out on a machine that can reach the model server (the Mac itself,
  or another machine through an `ssh -L` tunnel).
- A GGUF model already on disk for llama.cpp, and for MLX the model's weights already
  downloaded. Do not plan on downloading during the run.
- Optional, for the speech part: a 16 kHz mono WAV of real speech. See the note on speech
  below.

## 0. Keep the Mac awake

Before anything else: an idle Mac mini sleeps and wakes about once a minute and freezes for
~13 s each time, which shows up as 15 s stalls in every number. Run `caffeinate -s &` on it
(or set `sudo pmset -a sleep 0`); see [Hardware](../hardware.md). Check that
`pmset -g log | grep -c DarkWake` does not grow while you wait.

## 1. llama.cpp

```bash
llama-server -m /path/to/model.gguf -c 8192 --port 8080
```

Wait for `server is listening`, then, from the repository:

```bash
.venv/bin/python -m bench.run --label llama-cpp --skip-stt \
    --llm-url http://127.0.0.1:8080 --server-pid $(pgrep -f '[l]lama-server') \
    --repeats 5
```

Stop the server before starting the next runtime, so the two never compete for memory.

## 2. MLX

```bash
mlx_lm.server --model <local path or repo> --port 8080
.venv/bin/python -m bench.run --label mlx --skip-stt \
    --llm-url http://127.0.0.1:8080 --server-pid $(pgrep -f '[m]lx_lm.server')
```

If `python3 -c "import mlx_lm"` fails, MLX is not installed: record that in the result
notes rather than installing it on a machine whose network cannot fetch it.

## 3. Speech

`bench.run` measures speech with `faster-whisper`, whose weights are not on the Mac. For
the speech number on the Mac, replay a recorded call through whisper.cpp instead:

```bash
.venv/bin/python -m bench.replay_call recordings/call1 \
    --stt-url http://127.0.0.1:8081 --profile <profile id>
```

and quote the real-time factor it reports, labelled with the backend. Pass `--skip-stt` to
`bench.run` as above, so the speech gate shows as *not measured* instead of failing.

## 4. What lands where, and how long

Each run writes `bench/results/<date>-<label>.json` and prints a summary. Expect on the
order of 5–10 minutes per runtime with `--repeats 5` (an estimate, not a measurement).
`bench/results/*.json` is git-ignored on purpose, so commit a short
`bench/results/<date>-<label>.md` next to it with: the model file, the quantization, the
runtime version, the machine state (was Chrome open? `mediaanalysisd` busy?), and the
summary block.

## 5. Reading the result

| Key | What it answers |
|---|---|
| `warm_ttft_s` | M0's gate: median under 1.0 s |
| `cold_prefill_*_s` | What the cache saves |
| `d1_first_hint_ttft_cold_s` | First hint of an es/business session, no prefill |
| `d1_first_hint_ttft_prefilled_s` | Same, after the current `prefill` |
| `d1_first_hint_ttft_legacy_s` | Same, after the pre-fix `prefill` (a reconstruction) |
| `resident_memory_gb` | Gate: under 12 |

**Does `max_tokens=0` populate the cache?** The code assumes it; servers differ. It does if
`prefilled` is close to `warm_ttft_s` and far below `cold`. If `prefilled` is close to `cold`,
that runtime ignores the zero-token request and the session start buys nothing. Record the
answer per runtime.

**Does the D1 fix work?** `prefilled` should beat `legacy`, which should look like `cold`.
If it does not, stop and report.

## 6. After the run

1. Replace the estimates in [Inference](../architecture/inference.md) and its Spanish twin
   with the measured values, and only then remove the estimate warning (a test guards it).
2. Record pass or fail for the M0 gate in the [roadmap](../roadmap.md).
3. If the gate fails, stop and report. Do not tune around it: the answer is a smaller model
   or a shorter prompt, and the later milestones change shape.
