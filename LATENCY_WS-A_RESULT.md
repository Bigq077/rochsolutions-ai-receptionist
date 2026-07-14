# WS-A Result — NULL. Shelve (leave flag OFF).

**Date:** 2026-07-13. **Branch:** `latency-eval` (isolated). **Live untouched.**
**Verdict: WS-A produces no measurable latency win. Do NOT promote. Keep `WS_A_FAST_FIRST_CHUNK` default OFF.**

Companion to `LATENCY_WS-A_CHUNK_GATE_SPEC.md`, `LATENCY_BASELINE_LOCKED.md`. Data: `lat_parse.py` on the raw `[LAT]` lines.

---

## 1. The A/B

Both arms on the eval service, `LATENCY_TIMING=on`. Baseline = flag OFF (locked, 4 calls, n=28 completed). WS-A = `WS_A_FAST_FIRST_CHUNK=true`, `WS_A_MIN_WORDS_FIRST=6` (3 calls, n=25 completed, every turn tagged `flags=A`).

| metric (ms) | Baseline (OFF, n=28) | WS-A ON (n=25) | Δ |
|---|---|---|---|
| perceived TTFA p50 | 2164 | 2055 | −109 (noise) |
| perceived TTFA p90 | 3037 | 3325 | +288 (worse) |
| **chunk_gate p50** (WS-A target) | **682** | **724** | **+42 (no change)** |
| chunk_gate p90 | 934 | 1577 | +643 (worse) |
| tts_first_byte p50 | 128 | 121 | flat |

**chunk_gate — the metric WS-A directly moves — did not move.** No leftward shift.

## 2. Why it's null (the real lesson)

`chunk_gate` is floored by **the time for the LLM to stream the first full sentence to a hard boundary (`.`/`!`/`?`)**. WS-A's two levers don't touch that floor:

- **The 15→6 word-gate almost never bites.** A first sentence rarely contains a hard boundary *between* word 6 and word 15 — it's one clause that ends in a period around word 10–15. So lowering the gate changes nothing for most turns.
- **Hold-one-behind removal** should have helped, but the measured reduction is absent in aggregate.

**The dead-config `MIN_CHUNK_WORDS=8` was a red herring.** Fixing it can't help because first-sentence *generation* is the bottleneck, not the gate.

**Confound (honest):** the WS-A call set was heavier on long FAQ/explanatory openers (shockwave, needle fears, deep-massage explanation, session length) where WS-A structurally *cannot* help — no early sentence boundary exists. The short-opener turns WS-A *should* help do show low gates (266–464ms), but there are too few matched pairs to prove it and the aggregate is flat. Even generously, this is not a win.

## 3. "Didn't understand me" moments — NOT caused by WS-A

WS-A only changes *when the first TTS chunk is emitted*; it does not touch STT, endpointing, barge-in, or answer parsing. The same stumbles appear in the baseline OFF runs. Four buckets, in impact order:

1. **System-logic bug (worst).** Caller said "anytime"; STT transcribed it perfectly. A guard logged `non-scheduling single word 'anytime' — silence timer re-armed`, discarded it, and re-asked twice → **10s dead air** (call 3 seq 19, ttfa=10118) → frustrated repeat. STT heard it; the code threw it away. **Fix:** accept bare scheduling words (anytime / any time / whenever / next week) as `date_hint=any`.
2. **Echo/overlap capture.** Answers given while Susie's long TTS was still playing get front-clipped — only the tail transcribed ("please" / "session please" arrived `source=final` with no barge-in partials) → repeated re-asks.
3. **Susie's turns are long (10–17s on slot lists / FAQ).** This drives both wasted time *and* bucket 2. **Shortening responses beats WS-A on both latency and comprehension — highest-value, lowest-risk lever.**
4. **Start-of-call dead-air** (STT warm-up, known) + **name stragglers** ("Quentin"+"Rook" split into two finals, recovered via back-fill). Pre-existing.

## 4. Recommendation — where to go from here

1. **Shelve WS-A.** Leave `WS_A_FAST_FIRST_CHUNK` OFF. Code stays flag-gated and inert; do not promote to live.
2. **Shorten Susie's responses** (prompt-level: terser FAQ answers, tighter slot presentation). Biggest real lever for perceived latency *and* comprehension.
3. **Fix the "anytime" reject** (bucket 1) — cheap, concrete, high annoyance-reduction.
4. Only then consider **WS-B** (streaming TTS) or **WS-C** (semantic endpointing).

All of 2–4 belong on a clinic branch under normal review — **not** this eval branch.
