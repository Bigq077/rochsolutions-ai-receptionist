# Latency Baseline

**Filled 2026-08-18.** Mapping and gap-finding, as the template intended — not a
fresh measurement campaign. A locked baseline already existed in `LATENCY.md`;
this records whether it still holds, what is instrumented, and what the current
numbers say.

---

## Existing baseline (from `LATENCY.md`)

| Field | Value |
|---|---|
| Workstream / config | WS-A locked, all levers **OFF** |
| Sample size | n=28 turns across 4 calls |
| Date measured | ~July 2026 |
| Branch / service | `latency-eval`, eval Render service, `LATENCY_TIMING=on` |
| Conditions | Frankfurt, own Twilio number, isolated from live clinics |
| p50 perceived TTFA | **2164 ms** |
| p95 perceived TTFA | **3454 ms** |
| Max observed | 3701 ms |

**Metric** (unchanged, `LATENCY.md` §1): voice-to-voice = caller stops speaking →
first audio byte to Twilio. TTFA excludes the ~600 ms endpoint silence;
voice-to-voice ≈ TTFA + 600 ms.

### Is it still valid? — for perceived, yes. For content, no.

Measured on **`jv_v2`, build `66dd7a1a12bd`**, 14 turns across the 2 live calls of
the B1.2 suite, from Render log timestamps (`[ms_stt] FINAL → queue` → first
ElevenLabs response).

| metric (ms) | n | min | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|
| perceived TTFA — **today** | 14 | 109 | **2182** | 3040 | 3911 | 5396 |
| perceived TTFA — *July* | 28 | 1072 | *2164* | *3037* | *3454* | *3701* |
| content TTFA — **today** | 14 | 1724 | **3923** | 8152 | 9238 | 10878 |
| content TTFA — *July* | 28 | 1327 | *2182* | *3316* | *3663* | *4441* |

**Perceived is unchanged** — p50 within 18 ms of July, p90 within 3 ms. Two
different measurement methods, two services, six weeks apart, same distribution.
That cross-validates the July baseline; it is live and usable.

**Content has roughly doubled at p50 and is 2.5x worse at p95**, with a far
heavier tail (max 10.9 s vs 4.4 s). Direction: real. Magnitude: provisional —
n=14 vs n=28, and today's calls are production write flows that the July eval
calls may not have exercised equally.

---

## Against the §6.2 target

> *p95 caller-perceived turn latency under 1.5 s; no dead air over 3 s without a
> filler or acknowledgement.*

| | today | target | |
|---|---|---|---|
| voice-to-voice, perceived, p50 | 2782 ms | 1500 ms | FAIL |
| voice-to-voice, perceived, p95 | 4511 ms | 1500 ms | FAIL — 3x over |
| dead air > 3 s unmasked | none observed | none | PASS |

**The second clause passes and the first does not, and that is the whole story.**
Fillers reliably cover the wait — one turn shows perceived 109 ms, a pre-recorded
FillerGuard clip firing at 350 ms — so the caller is never in silence. They are
waiting up to 11 seconds for the actual answer.

This is a **standing gap, not a regression**. Perceived latency is where it was in
July. And §6.2's 1.5 s target contradicts `LATENCY.md` §1's own honest
expectation — *"Sub-1s is not on the table with a safety-first endpoint floor"*,
with ~2.6 s given as realistic. Those two documents have disagreed since both
were written. **Settle it by decision, not by measurement.**

---

## Where the time goes — the tail is tool turns

The five worst turns today are all tool-calling turns:

| ms to content | turn |
|---|---|
| 10878 | `um yes` → the reschedule write |
| 8355 | caller gives name → patient lookup |
| 7677 | "what's the soonest slot" → availability |
| 7385 | `go for it` → the booking write |
| 6366 | move intent → lookup |

Anatomy of the worst — **three sequential LLM round-trips, one per tool**:

```
11:27:46,386  iteration=1                    <- LLM round-trip 1
11:27:48,698  tool: lookup_patient           <- gcal round-trip
11:27:48,946  iteration=2                    <- LLM round-trip 2
11:27:51,662  tool: reschedule_appointment   <- gcal write
11:27:54,575  iteration=3                    <- LLM round-trip 3
11:27:57,126  content synthesised
```

Structural, not a slow provider.

### One concrete win: `lookup_patient` ran three times in one call

```
11:26:35,399  lookup_patient -> match 1/1 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80
11:27:02,383  lookup_patient -> match 1/1 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80
11:27:48,698  lookup_patient -> match 1/1 'Jonathan Moore' id=gsmelii4mu0g6sfs8q68efvv80
```

Same phone, same appointment, identical result. The third sits directly in front
of the write, on the turn the caller waits 10.9 s for. Each repeat costs a Google
Calendar round-trip **and** the extra LLM iteration that consumes it. Caching per
call is small, unflagged, and aimed at the tail — where `LATENCY.md` §1 says the
failures live.

---

## Per-hop instrumentation map

| Hop | Instrumented? | Code location | Notes |
|---|---|---|---|
| Twilio inbound → first audio frame | no | `routes/twilio.py`, `connection.py` | not timed |
| Audio frame → STT partial | partial | `stt_stream.py` | logs connect/Begin, not per-partial |
| STT final → endpoint decision | flag-gated | `latency_timing.py`, WS-C Phase 1 (`e7f64ff`) | shipped, **never measured on calls** |
| Endpoint → route decision | no | `router.py`, `fast_path.py` | |
| Route → LLM first token | flag-gated | `llm_stream.py` (`llm_ttft`) | needs `LATENCY_TIMING` |
| LLM first token → TTS first byte | flag-gated | `tts_stream.py` (`tts_first_byte`) | needs `LATENCY_TIMING` |
| TTS first byte → Twilio outbound | no | `connection.py`, chunker | |
| **Caller-perceived total** | flag-gated | `latency_timing.py` | the only number that matters |

### The gap that matters

`LATENCY_TIMING` defaults to **`false` on all four branches** and is not set in
`.env`. **No live clinic emits per-turn timing at all.** Every number above was
reconstructed from ordinary log lines, which is why n=14 and not 300.

---

## Lever status (from `LATENCY.md` §3 — unchanged)

| Lever | Verdict |
|---|---|
| WS-A chunk gate | null → shelved, do not reopen |
| WS-B streaming TTS | skipped, ceiling too low |
| WS-C semantic endpointing | shipped, gated OFF, **never measured** |
| Response length | deferred — prompt change, needs sign-off |

---

## What to do next

1. **Settle the target.** §6.2 says p95 < 1.5 s; `LATENCY.md` says ~2.6 s is the
   floor. Not a measurement question.
2. **Cache `lookup_patient` per call.** Smallest change with a real effect on the
   worst turns; needs no flag.
3. **Turn `LATENCY_TIMING` on for one clinic** long enough to collect ≥30 turns.
   Without it, every future latency claim is another hand-parsed log.
4. Only then WS-C, shipped and unmeasured since Phase 1.

> **Do not overwrite `LATENCY.md`'s locked baseline with the numbers above.**
> Different service, different method, n=14. A validity check beside the
> baseline, not a replacement for it.
