# M0, language-model half — Mac mini M4 16 GB, llama.cpp — 2026-11-16

**Partial. Not the M0 gate.** Speech was not measured, memory is a resident-set reading
rather than a peak with both models loaded, and each model was run once. JSON files sit
next to this one locally (`*.json` is git-ignored).

## Setup

- Server: Homebrew `llama-server` b10240 on the Mac, `--gpu-layers 99 --flash-attn on
  --ctx-size 8192 --cache-type-k q8_0 --cache-type-v q8_0 --threads 4 --parallel 1 --jinja`,
  bound to 127.0.0.1 and reached through an `ssh -L` tunnel. **Times include the tunnel's
  round trip.**
- Client: `bench.run --skip-stt --repeats 5` from the Ubuntu machine.
- Models, used straight from Ollama's blobs (read-only, Ollama not running):
  `LiquidAI/LFM2.5-1.2B-Instruct` (Q4_K_M), `sam860/LFM2:2.6B` (2.57B, quantization not
  recorded in its manifest), and `gemma-4-E2B-it-qat-UD-Q4_K_XL` (the server already running).
- Machine state: the Mac had rebooted 3 to 20 minutes before; load average 1 to 4 during
  the runs, nothing else heavy. Its speed has varied about 3x over an evening before, so
  one run per model is one sample of that.
- `resident_memory_gb` is the server's RSS read with `ps` after one 4k-token request, not a
  peak, and without the speech model loaded.

## Results (medians; p90 in brackets where it matters)

| | LFM2.5-1.2B | LFM2 2.6B | gemma-4-E2B |
|---|---|---|---|
| `warm_ttft_s` (gate: < 1.0) | 0.40 | **0.99** (p90 1.09) | 0.11 |
| `cold_prefill_1000_s` | 0.53 | 1.37 (p90 14.9) | 1.07 |
| `cold_prefill_4000_s` | 1.96 | 4.81 | 3.74 (p90 17.2) |
| `decode_tokens_s` | 117 | 51 | 56 |
| `resident_memory_gb` | 1.16 | 1.84 | 2.59 |
| first hint, es/business, no prefill | 1.33 | 3.07 | 2.46 |
| first hint after the **old** prefill | 1.16 | 2.88 | 2.26 |
| first hint after the **current** prefill | 0.20 | 0.41 | 0.36 |

## What this shows

- **D1 is fixed on this runtime.** After the old prefill the first hint cost 88 to 94% of
  a cold one, i.e. the prefill bought nothing. After the current one it costs 0.2 to 0.4 s,
  six to seven times less than cold, on all three models.
- **`max_tokens=0` populates the cache on llama.cpp b10240.** That is the same
  observation. Not tested on MLX or on Ollama's own server.
- **LFM2 2.6B sits on the one-second line.** Its median passes by 0.01 s and its p90 does
  not. Treat it as failing until a second run says otherwise.
- **Unexplained stalls.** Three requests took 15 to 17 s where their siblings took 1 to 5 s
  (LFM2 2.6B `cold_prefill_1000` and the cold first hint; gemma `cold_prefill_4000`). The
  same kind of stall appeared on earlier days. Cause unknown; the Mac's background
  activity is a suspect, not a finding. A 15 s stall in a live call is a failure of
  criterion 2, whatever the median says.
- LFM2 2.6B's first load hung for minutes at 0% CPU and its process then ignored SIGTERM;
  a second start on another port loaded in 6 s. Cause unknown.

## Not shown

Hint quality: these are speed numbers. Whether LFM2 writes better hints than gemma is what
Line 3 must find out on recorded calls. Speech real-time factor. Peak memory with both
models. MLX. Ollama's server. A second run at a different time of day.
