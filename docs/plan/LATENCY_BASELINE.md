# Latency Baseline

**Template — fill during Phase 0 item 3.**

> **Read the existing work first.** `LATENCY.md` (master doc, locked WS-A
> baseline, n=28 across 4 calls), `LATENCY_HARNESS.md`, `LATENCY_WS-C.md`, and
> `app/media_streams/latency_timing.py` already exist on `latency-eval`. This is
> a **mapping and gap-finding** exercise, not a fresh measurement campaign.
> Expected effort: a few hours, not a day.

---

## Purpose

Two things, and only these:

1. A number to defend the p95 < 1.5 s target against.
2. A **regression tripwire** for Phase 2. Enabling observability capture must not
   cost more than 50 ms at p95. Without a before-number, that budget is
   unenforceable and the whole reason this branch exists is lost.

---

## Existing baseline (from `LATENCY.md`)

| Field | Value |
|---|---|
| Workstream / config | WS-A (locked) |
| Sample size | n=28 turns across 4 calls |
| Date measured | |
| Branch / commit | |
| Conditions (network, region, time of day) | |
| p50 turn latency | |
| p95 turn latency | |
| Max observed | |

**Is this baseline still valid?** Check the commit it was taken at against
current HEAD. If the engine has moved materially since, it is a historical
record, not a baseline. Say so plainly rather than reusing a stale number.

---

## Per-hop instrumentation map

For each hop: is it instrumented, where in code, and can we get p50/p95 from
existing capture?

| Hop | Instrumented? | Code location | Notes |
|---|---|---|---|
| Twilio inbound → first audio frame | | `app/routes/twilio.py`, `connection.py` | |
| Audio frame → STT partial | | `stt_stream.py` | |
| STT final → endpoint decision | | `utterance_router.py`, `pause_detector.py` | Endpointing is often the largest and least-examined component |
| Endpoint → route decision | | `router.py`, `fast_path.py` | Fast path should be near-zero |
| Route → LLM first token | | `llm_stream.py` | |
| LLM first token → TTS first byte | | `tts_stream.py` | |
| TTS first byte → Twilio outbound | | `connection.py`, `chunker.py` | |
| **Caller-perceived total** (end of caller speech → start of audible reply) | | | The only number that matters to a patient |

Reference: `app/media_streams/latency_timing.py`.

---

## Blind hops

Any hop we cannot currently measure. Be explicit — an unmeasured hop is where
latency hides.

| Hop | Why blind | Cost to instrument | Worth doing now? |
|---|---|---|---|

---

## Tool-call latency

Not in the standard hop list and easy to forget: when the LLM calls a booking
tool, the caller waits on Acuity. Measure it separately.

| Path | p50 | p95 | Filler covers it? |
|---|---|---|---|
| Availability lookup | | | |
| Booking write | | | |

If p95 on either exceeds ~1.5 s, a filler phrase is mandatory, not optional.

---

## Post-Phase-2 re-measurement

Repeat after enabling `OBS_CAPTURE_ENABLED`. **Budget: 50 ms p95.**

| Metric | Before | After | Delta | Within budget? |
|---|---|---|---|---|
| p50 caller-perceived | | | | |
| p95 caller-perceived | | | | |

If the delta exceeds budget, capture is on the critical path and must be moved
off it before Phase 2's gate passes. Do not negotiate with this number.
