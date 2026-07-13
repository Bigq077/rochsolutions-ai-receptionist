# WS-A Baseline — LOCKED

**This is the reference the WS-A A/B is measured against.** Flag OFF (`flags=-`),
`LATENCY_TIMING=ON`, service `low-latency-joint-venture`. Do not overwrite — append
a WS-A-ON table beside it.

- **Captured:** 2026-07-13, 4 booking calls (14:12–14:49), branch `latency-eval` @ `65371fd` (pre-WS-A).
- **Turns:** 29 `path=llm`; 28 completed, 1 abandoned (surname straggler, excluded), 4 tool/slot turns (no first-chunk split).
- **Method:** numpy type-7 (linear-interpolation) percentiles via `lat_parse.py`. PII-free.
- **Raw data:** `lat_baseline_29turns.txt` (the exact `[LAT]` lines). Reproduce with `python lat_parse.py lat_baseline_29turns.txt`.
- **Status:** locked at n=28 (2 short of the ≥30 target, but metrics converged 23→28: perceived-TTFA p50 moved 2100→2164, chunk_gate p50 712→682 — stable).

## Baseline table (ms)

| metric | n | min | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|
| perceived TTFA (t4−t0) | 28 | 1072 | **2164** | 3037 | 3454 | 3701 |
| content TTFA (unmasked) | 28 | 1327 | 2182 | 3316 | 3663 | 4441 |
| llm_ttft | 28 | 1047 | 1210 | 1607 | 1636 | 2921 |
| **chunk_gate [WS-A target]** | 24 | 107 | **682** | 934 | 1048 | 1567 |
| tts_first_byte [WS-B] | 24 | 103 | 128 | 144 | 146 | 205 |

Per capture_phase (perceived TTFA p50 / p90 / chunk_gate p50):
- conversation (n=19): 2147 / 3276 / 662
- name (n=4): 2212 / 2353 / **905**  ← worst gate; long "Thanks X — is the number…" replies
- phone (n=5): 2014 / 2389 / 680

## What WS-A must beat
- **chunk_gate p50 682ms = 31% of perceived-TTFA p50.** WS-A ON should shift chunk_gate p50/p90 sharply left; watch perceived TTFA follow.
- Guardrails that must stay flat: abandoned rate (baseline 3.4%), tts_first_byte, and no clipped/orphaned openers on listen-back (the one real risk).
- `name`-phase gate is the biggest opportunity (905ms).

## Notes / gaps
- Tool/slot turns (check_availability) emit `chunk_gate=-1` — the slot-buffer path has no first-chunk split, so WS-A is not measured on them (their first audio is a filler anyway). Out of WS-A scope.
- barge-in rate is not yet a `[LAT]` field; count from full logs via `barge-in #N confirmed` if needed.
