# Overnight run: overnight-20261116

14 rounds, 93 steps, 3 failed.

## contention-gemma4-e2b

| metric | median [min - max] n |
|---|---|
| `wer_far` | 0.115 [0.115 - 0.115] n=10 |
| `wer_mic` | 0.073 [0.064 - 0.109] n=10 |
| `hints_started` | 9 [9 - 9] n=10 |
| `hints_wasted` | 3 [2 - 4] n=10 |
| `hint_ttft_ms_median` | 344 [318 - 510] n=10 |
| `final_lag_mean_s` | 3 [0.8 - 4.5] n=9 |
| `final_lag_max_s` | 15 [1.5 - 27.3] n=9 |

## contention-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `wer_far` | 0.115 [0.115 - 0.115] n=9 |
| `wer_mic` | 0.073 [0.064 - 0.073] n=9 |
| `hints_started` | 9 [9 - 9] n=9 |
| `hints_wasted` | 2 [2 - 2] n=9 |
| `hint_ttft_ms_median` | 318 [280 - 373] n=9 |
| `final_lag_mean_s` | 1.55 [1 - 2] n=8 |
| `final_lag_max_s` | 9.1 [3.7 - 11.6] n=8 |

## llamacpp-gemma4-e2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.126 [0.106 - 0.203] n=11 |
| `cold_prefill_1000_s` | 1.07 [1.06 - 1.19] n=11 |
| `cold_prefill_4000_s` | 3.68 [3.67 - 3.73] n=11 |
| `decode_tokens_s` | 55.8 [27.1 - 56.3] n=11 |
| `d1_first_hint_ttft_cold_s` | 2.36 [2.34 - 2.41] n=11 |
| `d1_first_hint_ttft_prefilled_s` | 0.38 [0.34 - 0.409] n=11 |
| `d1_first_hint_ttft_legacy_s` | 2.25 [2.19 - 2.25] n=11 |
| `resident_memory_gb` | 2.72 [1.43 - 3.2] n=11 |

## llamacpp-lfm2-2.6b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 1.02 [0.988 - 1.07] n=9 |
| `cold_prefill_1000_s` | 1.38 [1.35 - 1.42] n=9 |
| `cold_prefill_4000_s` | 4.8 [4.73 - 4.83] n=9 |
| `decode_tokens_s` | 51 [31.4 - 51.4] n=9 |
| `d1_first_hint_ttft_cold_s` | 3.14 [3.06 - 3.24] n=9 |
| `d1_first_hint_ttft_prefilled_s` | 0.441 [0.418 - 0.508] n=9 |
| `d1_first_hint_ttft_legacy_s` | 2.97 [2.94 - 3.07] n=9 |
| `resident_memory_gb` | 1.83 [1.78 - 1.86] n=9 |

## llamacpp-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.403 [0.399 - 0.43] n=11 |
| `cold_prefill_1000_s` | 0.532 [0.53 - 0.548] n=11 |
| `cold_prefill_4000_s` | 1.94 [1.92 - 2.02] n=11 |
| `decode_tokens_s` | 117 [58.9 - 118] n=11 |
| `d1_first_hint_ttft_cold_s` | 1.26 [1.23 - 1.34] n=11 |
| `d1_first_hint_ttft_prefilled_s` | 0.178 [0.175 - 0.204] n=11 |
| `d1_first_hint_ttft_legacy_s` | 1.15 [1.14 - 1.23] n=11 |
| `resident_memory_gb` | 0.81 [0.81 - 0.82] n=11 |

## ollama-lfm2-2.6b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 1.84 [1.77 - 1.86] n=6 |
| `cold_prefill_1000_s` | 1.36 [1.33 - 1.42] n=6 |
| `cold_prefill_4000_s` | 5.02 [4.94 - 5.06] n=6 |
| `decode_tokens_s` | 48.1 [29.6 - 50] n=6 |
| `d1_first_hint_ttft_cold_s` | 3.09 [3.06 - 3.16] n=6 |
| `d1_first_hint_ttft_prefilled_s` | 2.15 [2.12 - 2.16] n=6 |
| `d1_first_hint_ttft_legacy_s` | 2.97 [2.9 - 3.05] n=6 |
| `resident_memory_gb` | 0.49 [0.18 - 2.11] n=6 |

## ollama-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.735 [0.701 - 0.818] n=6 |
| `cold_prefill_1000_s` | 0.538 [0.519 - 0.555] n=6 |
| `cold_prefill_4000_s` | 1.98 [1.95 - 2.04] n=6 |
| `decode_tokens_s` | 110 [108 - 111] n=6 |
| `d1_first_hint_ttft_cold_s` | 1.23 [1.2 - 1.24] n=6 |
| `d1_first_hint_ttft_prefilled_s` | 0.845 [0.823 - 0.92] n=6 |
| `d1_first_hint_ttft_legacy_s` | 1.21 [1.12 - 1.23] n=6 |
| `resident_memory_gb` | 1.05 [1.05 - 1.06] n=6 |

## replay

| metric | median [min - max] n |
|---|---|
| `wer_far` | 0.115 [0.115 - 0.115] n=14 |
| `wer_mic` | 0.073 [0.064 - 0.073] n=14 |
| `final_lag_mean_s` | 2.25 [0.8 - 4.5] n=12 |
| `final_lag_max_s` | 15.8 [1.5 - 24.8] n=12 |

## stt

| metric | median [min - max] n |
|---|---|
| `stt_realtime_factor` | 0.0501 [0.0468 - 0.0567] n=14 |

## Failed steps

- contention-lfm25-1.2b: rc=91 x1
- llamacpp-lfm2-2.6b: rc=91 x2

## Slow outliers (p90 over 3x the median and over 5 s)

- round 1 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.41 s, p90 28.2 s
- round 1 `llamacpp-lfm25-1.2b` `cold_prefill_4000_s`: median 1.93 s, p90 15.3 s
- round 1 `ollama-lfm25-1.2b` `warm_ttft_s`: median 0.802 s, p90 15.7 s
- round 1 `ollama-lfm2-2.6b` `cold_prefill_4000_s`: median 4.94 s, p90 16.6 s
- round 2 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_legacy_s`: median 2.22 s, p90 15.9 s
- round 2 `llamacpp-lfm25-1.2b` `d1_first_hint_ttft_legacy_s`: median 1.14 s, p90 15.6 s
- round 2 `llamacpp-lfm2-2.6b` `cold_prefill_1000_s`: median 1.42 s, p90 15 s
- round 2 `llamacpp-lfm2-2.6b` `d1_first_hint_ttft_legacy_s`: median 2.95 s, p90 16.4 s
- round 2 `ollama-lfm2-2.6b` `cold_prefill_1000_s`: median 1.34 s, p90 15 s
- round 3 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.37 s, p90 24.1 s
- round 3 `llamacpp-lfm2-2.6b` `cold_prefill_1000_s`: median 1.36 s, p90 14.9 s
- round 3 `llamacpp-lfm2-2.6b` `d1_first_hint_ttft_cold_s`: median 3.17 s, p90 15.7 s
- round 4 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.35 s, p90 17.9 s
- round 4 `llamacpp-lfm2-2.6b` `cold_prefill_1000_s`: median 1.42 s, p90 15.8 s
- round 4 `llamacpp-lfm2-2.6b` `d1_first_hint_ttft_cold_s`: median 3.09 s, p90 15.8 s
- round 4 `ollama-lfm2-2.6b` `cold_prefill_4000_s`: median 5.06 s, p90 17.4 s
- round 4 `ollama-lfm2-2.6b` `d1_first_hint_ttft_prefilled_s`: median 2.15 s, p90 17 s
- round 4 `ollama-lfm2-2.6b` `d1_first_hint_ttft_legacy_s`: median 2.97 s, p90 15.6 s
- round 6 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.35 s, p90 21.8 s
- round 6 `llamacpp-lfm25-1.2b` `cold_prefill_4000_s`: median 1.92 s, p90 15.6 s
- round 6 `llamacpp-lfm2-2.6b` `cold_prefill_4000_s`: median 4.76 s, p90 17.9 s
- round 6 `ollama-lfm2-2.6b` `d1_first_hint_ttft_cold_s`: median 3.16 s, p90 16.7 s
- round 6 `ollama-lfm2-2.6b` `d1_first_hint_ttft_legacy_s`: median 3.05 s, p90 15.7 s
- round 7 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.41 s, p90 21.9 s
- round 7 `llamacpp-lfm25-1.2b` `cold_prefill_4000_s`: median 1.94 s, p90 15.6 s
- round 7 `llamacpp-lfm2-2.6b` `cold_prefill_4000_s`: median 4.73 s, p90 16.4 s
- round 7 `llamacpp-lfm2-2.6b` `d1_first_hint_ttft_cold_s`: median 3.2 s, p90 16 s
- round 8 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.34 s, p90 15.9 s
- round 8 `llamacpp-lfm2-2.6b` `cold_prefill_4000_s`: median 4.81 s, p90 19.5 s
- round 8 `ollama-lfm25-1.2b` `d1_first_hint_ttft_prefilled_s`: median 0.847 s, p90 15.7 s
- round 9 `llamacpp-lfm25-1.2b` `cold_prefill_1000_s`: median 0.532 s, p90 15.7 s
- round 9 `llamacpp-lfm2-2.6b` `cold_prefill_4000_s`: median 4.81 s, p90 15.7 s
- round 10 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.37 s, p90 20.2 s
- round 10 `llamacpp-lfm25-1.2b` `cold_prefill_4000_s`: median 1.96 s, p90 15.5 s
- round 10 `ollama-lfm25-1.2b` `warm_ttft_s`: median 0.818 s, p90 15.7 s
- round 10 `ollama-lfm2-2.6b` `cold_prefill_4000_s`: median 5.02 s, p90 19.3 s
- round 11 `llamacpp-gemma4-e2b` `d1_first_hint_ttft_cold_s`: median 2.36 s, p90 16.1 s
- round 11 `llamacpp-lfm25-1.2b` `cold_prefill_4000_s`: median 2.02 s, p90 15.7 s
- round 11 `llamacpp-lfm2-2.6b` `d1_first_hint_ttft_legacy_s`: median 2.95 s, p90 15.6 s

## The Mac meanwhile

2138 samples, 10 s apart. Load average: median 1.19, max 19.79.

388 freezes (a sampler gap over 14 s), about 5179 s in all.

Most often among the three busiest processes:

- Stats: 1778 samples
- Google: 1130 samples
- whisper-server: 368 samples
- Surfshark: 312 samples
- airportd: 290 samples
- WindowServer: 271 samples

