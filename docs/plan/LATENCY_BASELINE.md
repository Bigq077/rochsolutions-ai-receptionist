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
`.env`. Every number above was reconstructed from ordinary log lines, which is
why n=14 and not 300.

---

## Latency is now persisted (2026-08-19)

**The second gap, and the worse one: nothing was ever stored.** Turning
`LATENCY_TIMING` on only produces `[LAT]` log lines, and a log line has a
lifetime. obs held ~294 calls and no latency column, so a baseline could only be
assembled by exporting a Render log window — and at ~13 calls/day against a
retention measured in hours, an export can never contain more than the handful of
calls inside the window. Two sessions of exporting produced a largest sample of
**29 turns across 3 calls**, one caller, one clinic, mostly reschedules. The
second export was a superset of the first, and doubling the sample moved p50 by
0.6%: the numbers were stable, but stable *for that scenario*. There was no
bigger scroll to do.

What it did establish, and what still stands as directional:

- **~86% of turns miss the 1.5 s bar**, consistent across both samples.
- **`llm_ttft` dominates** — 6.5–7.6 s at p95, several times any other component.
  `chunk_gate` is second at ~3.2 s.
- So this is a **model-and-prompt problem before it is a pipeline problem**.
  Tuning timeouts or TTS would not touch the largest term.

Enough to direct the work. Not enough to size it, and it must not be written into
a plan as *the* baseline.

### What changed

Each call's turns are now stored on its obs row, so the sample accumulates
instead of sliding out of the window.

| Piece | Where |
|---|---|
| `TurnTiming.as_record()` — the `[LAT]` fields as a dict | `app/media_streams/latency_timing.py` |
| Per-call buffer, bounded on turns and on calls | same file (`_buffer` / `drain_call`) |
| `call_sid` on the turn — the one hot-path change | `connection.py`, one keyword argument |
| Drain at teardown into the call record | `app/call_logger.py` (`_latency_block`) |
| `calls.latency` JSON column + migration | `app/obs/models.py`, `app/obs/store.py` |
| Read it back as the usual table | `scripts/lat_baseline.py` |

`emit()` formats the log line **from** `as_record()`, so the stored figures and
the logged ones cannot drift; `lat_baseline.py` renders stored turns back into
`[LAT]` lines and hands them to `lat_parse.py`, so the table comes from the same
parser and the same percentile method as the locked baseline.

The earlier note in `call_logger._screening_summary` that latency needed
`connection.py` surgery was over-cautious: `emit()` already held every timing, and
the only real gap was that a turn did not know its `call_sid`.

### Two switches, not one

- `LATENCY_TIMING=true` — **measures**. Default `false`; with it off `new_turn`
  returns `None` and nothing is allocated, buffered or stored.
- `OBS_CAPTURE_ENABLED` + `OBS_DATABASE_URL` — **stores**. Without these the
  turns are logged and dropped, exactly as before.

Both must be on, on the service serving the clinic. Run
`python -m app.obs.migrate` once to add the column; it is idempotent and safe to
re-run.

**A NULL `latency` means "not measured", never "fast".** Every call before the
column existed is NULL by absence, and `lat_baseline.py` reports those as skipped
rather than counting them.

### What it buys

At ~13 calls/day: ~30 calls in two to three days, ~300 in a month, queryable —
with no exports, ever again.

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
3. **Turn `LATENCY_TIMING` on for one clinic and leave it on.** The storage
   half is now built (above), so this is the only remaining step between here
   and a real baseline — and unlike before, the sample now accumulates rather
   than expiring with the log window. Read it with
   `python scripts/lat_baseline.py --clinic <id>`.
4. **Do not go at latency blind.** `llm_ttft` dominating is directional enough to
   aim at the model/prompt cost first, but without stored turns there is no way
   to tell whether a change worked.
5. Only then WS-C, shipped and unmeasured since Phase 1.

> **Do not overwrite `LATENCY.md`'s locked baseline with the numbers above.**
> Different service, different method, n=14. A validity check beside the
> baseline, not a replacement for it.
