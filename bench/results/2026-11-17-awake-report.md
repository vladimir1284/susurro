# Overnight run: awake-20261117

7 rounds, 43 steps, 0 failed.

## contention-gemma4-e2b

| metric | median [min - max] n |
|---|---|
| `final_lag_mean_s` | 0.8 [0.8 - 1] n=5 |
| `final_lag_max_s` | 1.8 [1.6 - 2.4] n=5 |
| `wer_far` | 0.115 [0.115 - 0.115] n=5 |
| `wer_mic` | 0.073 [0.073 - 0.073] n=5 |
| `hints_started` | 9 [9 - 9] n=5 |
| `hints_wasted` | 2 [2 - 2] n=5 |
| `hint_ttft_ms_median` | 318 [312 - 330] n=5 |

## contention-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `final_lag_mean_s` | 0.9 [0.8 - 0.9] n=5 |
| `final_lag_max_s` | 1.8 [1.6 - 2.4] n=5 |
| `wer_far` | 0.115 [0.115 - 0.115] n=5 |
| `wer_mic` | 0.073 [0.073 - 0.073] n=5 |
| `hints_started` | 9 [9 - 9] n=5 |
| `hints_wasted` | 2 [2 - 2] n=5 |
| `hint_ttft_ms_median` | 309 [280 - 329] n=5 |

## llamacpp-gemma4-e2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.131 [0.108 - 0.203] n=5 |
| `cold_prefill_1000_s` | 1.09 [1.07 - 1.29] n=5 |
| `cold_prefill_4000_s` | 3.68 [3.68 - 3.72] n=5 |
| `decode_tokens_s` | 55.5 [54.1 - 56] n=5 |
| `d1_first_hint_ttft_cold_s` | 2.42 [2.35 - 2.46] n=5 |
| `d1_first_hint_ttft_prefilled_s` | 0.407 [0.407 - 0.408] n=5 |
| `d1_first_hint_ttft_legacy_s` | 2.25 [2.25 - 2.25] n=5 |
| `resident_memory_gb` | 0.13 [0.11 - 0.17] n=5 |

## llamacpp-lfm2-2.6b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 1.02 [1.01 - 1.02] n=5 |
| `cold_prefill_1000_s` | 1.38 [1.35 - 1.43] n=5 |
| `cold_prefill_4000_s` | 4.81 [4.73 - 4.81] n=5 |
| `decode_tokens_s` | 51.3 [50.8 - 51.4] n=5 |
| `d1_first_hint_ttft_cold_s` | 3.21 [3.16 - 3.27] n=5 |
| `d1_first_hint_ttft_prefilled_s` | 0.51 [0.453 - 0.51] n=5 |
| `d1_first_hint_ttft_legacy_s` | 3.07 [3.07 - 3.12] n=5 |
| `resident_memory_gb` | 1.8 [1.63 - 1.82] n=5 |

## llamacpp-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.406 [0.403 - 0.43] n=5 |
| `cold_prefill_1000_s` | 0.541 [0.535 - 0.562] n=5 |
| `cold_prefill_4000_s` | 1.94 [1.93 - 1.96] n=5 |
| `decode_tokens_s` | 115 [111 - 116] n=5 |
| `d1_first_hint_ttft_cold_s` | 1.27 [1.25 - 1.33] n=5 |
| `d1_first_hint_ttft_prefilled_s` | 0.204 [0.202 - 0.22] n=5 |
| `d1_first_hint_ttft_legacy_s` | 1.23 [1.21 - 1.23] n=5 |
| `resident_memory_gb` | 0.79 [0.78 - 0.81] n=5 |

## ollama-lfm2-2.6b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 1.89 [1.84 - 1.94] n=2 |
| `cold_prefill_1000_s` | 1.33 [1.33 - 1.33] n=2 |
| `cold_prefill_4000_s` | 5.09 [5.07 - 5.11] n=2 |
| `decode_tokens_s` | 47 [46 - 48.1] n=2 |
| `d1_first_hint_ttft_cold_s` | 3.2 [3.19 - 3.2] n=2 |
| `d1_first_hint_ttft_prefilled_s` | 2.25 [2.25 - 2.25] n=2 |
| `d1_first_hint_ttft_legacy_s` | 3.07 [3.07 - 3.07] n=2 |
| `resident_memory_gb` | 0.525 [0.45 - 0.6] n=2 |

## ollama-lfm25-1.2b

| metric | median [min - max] n |
|---|---|
| `warm_ttft_s` | 0.715 [0.715 - 0.716] n=2 |
| `cold_prefill_1000_s` | 0.523 [0.523 - 0.523] n=2 |
| `cold_prefill_4000_s` | 2 [1.98 - 2.02] n=2 |
| `decode_tokens_s` | 112 [111 - 113] n=2 |
| `d1_first_hint_ttft_cold_s` | 1.27 [1.23 - 1.3] n=2 |
| `d1_first_hint_ttft_prefilled_s` | 0.92 [0.92 - 0.92] n=2 |
| `d1_first_hint_ttft_legacy_s` | 1.17 [1.12 - 1.23] n=2 |
| `resident_memory_gb` | 1.05 [1.05 - 1.05] n=2 |

## replay

| metric | median [min - max] n |
|---|---|
| `final_lag_mean_s` | 0.8 [0.8 - 1.5] n=7 |
| `final_lag_max_s` | 1.8 [1.7 - 3.9] n=7 |
| `wer_far` | 0.115 [0.115 - 0.115] n=7 |
| `wer_mic` | 0.073 [0.073 - 0.073] n=7 |

## stt

| metric | median [min - max] n |
|---|---|
| `stt_realtime_factor` | 0.0534 [0.0471 - 0.105] n=7 |

## Failed steps

None.

## Slow outliers (p90 over 3x the median and over 5 s)

None.

## The Mac meanwhile

1019 samples, 10 s apart. Load average: median 1.30, max 3.33.

0 freezes (a sampler gap over 14 s), about 0 s in all.

Most often among the three busiest processes:

- Stats: 720 samples
- Google: 624 samples
- WindowServer: 322 samples
- whisper-server: 210 samples
- com.apple.AppleUserHIDDrivers: 133 samples
- llama-server: 82 samples

