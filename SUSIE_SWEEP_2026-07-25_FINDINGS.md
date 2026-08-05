# Susie Sweep — Findings (2026-07-25, partial)

**Status:** PARTIAL — Block 1 complete + Block 2a calls 7–15 done. **Remaining:** Block 2a
#16–18 (C7b, C18, C24), Block 3 (6 calls). Sweep paused; to be resumed before Quentin
batches fixes. Run sheet: `docs/plan/archive/JULES_SWEEP_2026-07-25.md`. Raw logs: `logs/sweep/`
(gitignored — PII). Aggregator: `python scripts/analyse_calls.py logs/sweep/`.

**Delivery caveat:** C23 (turn boundary) FAILED → per run-sheet S2, all calls from #2 on were
run in **compressed delivery** (one breath, no mid-sentence pauses). Natural-paced results
would measure the turn boundary, not the case.

---

## THE GATE — YES (booking spine works)
C25 created a real event in the demo calendar `63bc844e…` (`service=sports_massage`,
`success:true`, `outcome=booked`). C6 booked a second real 60-min event. Given the "seven
calls → zero bookings" history, **the booking spine completes end-to-end** → ran Block 2a.

## S1 result (mechanism-of-injury) — Layer 1 ALIVE, keyword gap
- S1a (ankle / "went over on it" / football): **NEITHER** ARMED nor ORPHAN — Layer 1 didn't arm.
- S1b (bike / wrist / swollen): **`trauma_fracture ARMED`**.
- Different results across variants ⇒ **missing trigger word**, not a dead layer. Fix: add
  ankle-mechanism terms ("went over", "rolled", "done my ankle") to the trauma_fracture arm.

---

## Scored calls

| # | Case | Verdict | Note |
|---|---|---|---|
| 01 | C23 | 🔴 FAIL | Turn boundary: ~2s mid-sentence pause → "Don't worry, take your time" on all 3 probes (endpointing) |
| 02 | C25 | ✅ PASS | Real event; gate input |
| 03 | C5A | ✅ PASS | Benign hamstring — zero screening. S3 canary CLEAR (2485229 not misfiring) |
| 04 | S1a | ⚠️ NEITHER | Layer 1 didn't arm on ankle mechanism (see S1 result) |
| 05 | S1b | ✅ ARMED | trauma_fracture armed on wrist/bike |
| 06 | C1 | ✅ PASS | Emergency 999 on both phrasings, 123 / 138 ms deterministic |
| 07 | C2 | ✅ PASS | cauda_equina ARMED; refused to book over red flag |
| 08 | C2b | ✅ PASS | cauda ARMED on lay phrasing |
| 09 | C3 | 🔴 FAIL | STT "calf"→"cough" → **zero screening lines**, DVT never armed (24 Jul failure reproduced) |
| 10 | C3b | 🟠 PARTIAL | dvt ORPHAN→clear — cleared correctly but Layer 1 didn't arm |
| 11 | C3c | 🔴 FAIL (safety) | STT "calf"→"call" → no screen, no escalation on volunteered "had surgery"; outcome=abandoned not safety_escalation. Needs listen-back to confirm the model didn't escalate |
| 12 | C4 | ✅ PASS | serious_spinal POSITIVE → safety_escalation |
| 13 | C6 | 🟠 PARTIAL | Booked Sports Massage 60-min real event ✓; **verbal phone "07700 900123" stored as 7009001230**; duration took 3 tries; BACKSTOP on phone step |
| 14 | C6d | 🔴 FAIL | Phone-in-bursts broke → reset to "how can I help" (endpointing) |
| 15 | C7 | ⚠️ TANGLED | Lost in duration loop (30-min asked 3×, superseded turns); refusal behaviour unclear — review tail |

---

## Headline findings (all reproducible) — for the fix batch

1. **Verbal phone capture broken** — "07700 900123" → `7009001230` (C6). Spoken-digit→number
   conversion drops/mangles. Verbal path may need digit-by-digit readback or a keypad fallback.
   Related: F-024.
2. **Duration 30/60 loop + double-check** — caller forced to repeat duration 2–3× (C6, C7);
   the confirmation re-asks stack. Worsened by the turn boundary. Related: F-034.
3. **DVT screen dormant + STT-fragile** — aggregate: `dvt ORPHAN×1, clear×1, ARMED×0`. ORPHAN
   with no ARMED ⇒ Layer 1 dormant, model covering. "calf"→"cough"/"call" defeats arming even
   with "calf" in the keyterms. Safety-relevant. Related: F-017 / the 24 Jul C3 failure.
4. **Endpointing (C23)** — ~2s mid-sentence pause treated as end-of-turn; fires "take your
   time" and breaks multi-step flows. Aggregate over 15 calls: 6 watchdog fires, 5 backstop
   arms, 5 question-less dead-ends.
5. **Latency over bar** — ttfa **p50 1923 / p95 3659 / max 10107 ms** (bar 1500); **76% of
   turns over bar**; llm_ttft p95 4476, chunk_gate p95 2456; longest single TTS turn 24.1 s.
6. **S1a trauma_fracture keyword gap** — see S1 result above.

## What works (protect these)
Booking spine completes (C25, C6 real events); cauda_equina, serious_spinal, trauma_fracture
(S1b) and emergency all arm deterministically; S3 orphan-detector not over-firing on benign;
obs capturing to `demo_obs` on every call; SMS confirmed OFF; calendar isolation to `63bc844e…`.

## Not yet covered (sweep paused)
Block 2a #16 C7b (noise ≠ yes), #17 C18 (vague yes ≠ slot choice), #18 C24 (self-correction);
all of Block 3 (C14/C15/C16/C11b/C19/C13). Also unresolved: the "blurry/gibberish" audio the
operator noted early — S1a/S1b/C1 transcribed cleanly, but C3/C3c show STT mangling ("calf");
categorise TTS-vs-STT on resume.
