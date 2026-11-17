# M0, repeated rounds — Mac mini M4 16 GB — night of 2026-11-16 to 17

14 rounds, about 45 minutes apart (22:56 to 06:22), driven by `bench/overnight.py`. Same
setup as [2026-11-16-mac-llamacpp.md](2026-11-16-mac-llamacpp.md): llama.cpp b10240 and
Ollama's own server (ctx 8192), through an `ssh -L` tunnel, times include the tunnel. Full
tables: `python -m bench.overnight_report <out dir> --lag-from-round 3`. The raw files stay
local (`sessions/` and `*.json` are git-ignored). Medians across rounds, with the range.

## Language model (the `warm_ttft_s` gate is < 1.0 s)

| runtime / model | warm TTFT s | first hint, cold | first hint, after prefill | decode tok/s | server RSS GB |
|---|---|---|---|---|---|
| llama.cpp gemma-4-E2B | **0.13** [0.11-0.20] | 2.36 | 0.38 | 56 | 2.7 |
| llama.cpp LFM2.5-1.2B | **0.40** [0.40-0.43] | 1.26 | 0.18 | 117 | 0.8 |
| llama.cpp LFM2 2.6B | **1.02** [0.99-1.07] | 3.14 | 0.44 | 51 | 1.8 |
| Ollama LFM2.5-1.2B | 0.74 [0.70-0.82] | 1.23 | **0.85** | 110 | 1.1 |
| Ollama LFM2 2.6B | **1.84** [1.77-1.86] | 3.09 | **2.15** | 48 | 0.5-2.1 |

- gemma-4-E2B and LFM2.5-1.2B pass the gate under llama.cpp with a wide margin; **LFM2
  2.6B fails it** (median 1.02 s over nine rounds, never below 0.99 s). Under Ollama only
  LFM2.5-1.2B passes.
- **The prefill works under llama.cpp (first hint 6-7x faster than cold) and barely under
  Ollama** (0.85 s against 1.23 s cold, 2.15 s against 3.09 s). `max_tokens=0` does not
  warm Ollama's cache the way it does llama.cpp's; a session start there buys little.
- The measurements are tight when nothing stalls: ranges of a few percent.

## Speech (whisper.cpp `small`, one 12 s clip)

`stt_realtime_factor` 0.050 [0.047-0.057] over 14 rounds: about 20x faster than real time.
One clip of one voice; not the 30-minute case.

## Replaying the call with real hints (150 s of the 2026-11-15 call)

- Word error is identical every round: far 0.115, mic 0.073; 9 of 9 key terms.
- With a real model generating hints during the replay: **9 hints started, 2-3 wasted**
  (cancelled), a quarter to a third of all generations (AUDIT D8). Time to first token while
  the speech model was busy: median 318 ms (LFM2.5) and 344 ms (gemma).
- Lag of finals: median of the per-round max is **15.8 s without hints**, 9.1 s with LFM2.5
  and 15 s with gemma. These say nothing about contention: see the next section. Rounds 1
  and 2 are excluded (the replay hid its own lag then).

## The Mac sleeps for about 13 s once a minute

388 gaps of 14 to 40 s in a sampler that should write every 10 s, 5179 s in all over 8.5
hours: **about 17% of the night the Mac did not run userland code.** A `time.sleep(1)` probe
overshot by 14.0 s and 12.0 s exactly one minute apart. This is the source of the 15-17 s
stalls in every bench (the "slow outliers" in the report, 39 of them) and of the late
finals in the replay. Ping loses 0.6% (123 of 21967), p99 214 ms, max 1035 ms.

**Cause found the next morning: the Mac was sleeping.** `pmset -g log` shows "Maintenance
Sleep" followed by "DarkWake" about every 70 s, 60 cycles an hour, around the clock
whenever nobody used the machine (also all of 2026-11-16 from 11:00); a network packet
wakes it. The sampler and the probe were simply stopped while it slept. It was not the
Stats app (the probe saw no freeze with it closed or open), and `caffeinate -i`, which the
night ran under, did not prevent it. Idle, with `caffeinate -dimsu` or with `caffeinate -s`
alone, there were **no sleep cycles in 12 and 10 minutes**. The fix is a power setting:
`sudo pmset -a sleep 0` (the Mac's `pmset` showed `sleep 1`), see
[Hardware](../hardware.md). The night's numbers all include these sleeps; a rerun with the
Mac kept awake is needed for clean stall-free numbers, and every earlier "15 s stall" in
this project (including the first real call) most likely had this cause.

## What this says about M0

- **Numbers:** the three gate conditions hold for gemma-4-E2B and LFM2.5-1.2B under
  llama.cpp: warm TTFT under 1 s, speech 20x real time, server memory about 3 GB. LFM2 2.6B
  does not hold the first.
- **Not shown:** memory with both models loaded (each measured alone), speech on more than
  one clip, MLX (not installed), hint quality, and anything about a 60-minute call.
- **The gate as written ignores criterion 2 of the audit.** A machine that sleeps for 13 s
  every minute passes it and still fails a real call: a hint requested at the wrong moment
  arrives 13 s late, and the lag of finals reaches 15-25 s. Keep it awake first (see
  above); no model choice changes this.

## Other things the night found

- LFM2 2.6B under llama.cpp hung at load in 3 of its 14 starts (it ignores SIGTERM then);
  LFM2.5-1.2B hung once. A 20 s pause before each start and a retry did not remove it.
  Under Ollama the same weights loaded every time.
- Two defects in the replay tool itself, fixed during the night (arrival-time stamps hid
  its lag; hints needed a real session) and a flaky test from D4.
- Client load during the run stayed below 2 on 4 cores apart from test runs; the first two
  rounds had some of my own.
