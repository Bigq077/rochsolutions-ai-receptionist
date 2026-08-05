# Sprint to the demo — Wed 5 August

**Written 2026-07-29. Demo confirmed: Hands On Money, Wed 5 Aug.**
Seven days. Six of them are working days; the seventh is the demo.

---

## What the data says we are actually fixing

69 calls since Monday, bucketed by the build that was live:

| Build | calls | booked | reached name | name=NULL | name re-ask | 2+ confirms |
|---|---|---|---|---|---|---|
| pre-27th | 31 | 11 | 21 | 9.5% | 33% | **35%** |
| `b405017` (live) | 21 | 8 | 14 | **21%** | **43%** | 9.5% |

**Confirmation looping was fixed** — 35% → 9.5%, worst case 7 asks → 2.
**Name capture was never touched** — it was ruled out of the fix queue twice and
mitigated by script instead. It is the only metric that did not improve.

Three severity-1 classes trace to it:

**1 · Wrong identity written to a real booking — invisible to the ear**

- `CA325372e5` — she said **"Thanks Quentin"** aloud and stored **`Quinton Rock`**.
  Real calendar event `s84l596frmfge32puvstfei6a4`
- `Quinton Rock` · `Quentin Rock` · `Quentin Rook` · `Benton Rock` — four manglings
  of *Quentin Roch* across four calls; three booked with real event IDs
- `Jewel Decorps` for *Jules Decorps*

> **She almost never reads the surname back.** The surname is written to a clinical
> record with no read-back, no confirmation and no audit trail. The field most
> likely to be wrong is the one field nothing can check. A human scoring by ear
> cannot detect this defect at all.

**2 · Wrong date/time booked**

- `CAc64a05f1` — mis-heard name → correction → confirmation loop → **booked Wed 29
  Jul 17:30 when the caller had agreed Tue 4 Aug 18:30**, on a day it had called
  fully booked
- `CAfe6a4162` — **booked Saturday** having only ever said Friday

**3 · Confirmation loop → no booking at all** (`CA2f0b0707`)

> **The thesis for this week: name capture is the root cause, the booking write is
> the last line of defence, and neither can be fixed safely until we can measure.**

---

## The governing constraint has changed

It is no longer "protect the demo." It is **"can we tell whether a fix worked?"**
For five days two people changed a 24k-line engine, scored by ear, against 96 red
tests. That is why the same defect keeps resurfacing.

`tests/auto/` is a complete automated call harness — real Twilio calls, scripted
TTS responses, Claude-evaluated pass/fail. `app/obs/judge.py` is implemented and
already points at `claude-opus-5`. **Both are switched off.** Turning them on is
the highest-leverage day of the week.

---

# Day 0 · Wed 29 Jul — build the loop, change no behaviour

**No engine code today.** Ship nothing that alters a caller's experience.

| # | Task | Done when |
|---|---|---|
| 1 | `OBS_JUDGE_ENABLED=true`, verify it scores a call | one judged row in `calls` |
| 2 | Get `tests/auto/run_tests.py` running end to end | 2 scenarios execute and report |
| 3 | Build scenarios from real evidence (below) | 8 scenarios committed |
| 4 | Slot-integrity **detector** in obs (compare booked slot vs slot agreed) | flags `CAc64a05f1` and `CAfe6a4162` retrospectively |
| 4b | Name-fidelity **detector** (spoken name vs stored name, first **and** surname) | flags `CA325372e5` retrospectively |
| 5 | Collapse 4 plan docs into one ranked register | `DEFECT_REGISTER.md` exists |
| 6 | From Jules: `python scripts/analyse_calls.py logs/sweep/` — latency only | percentiles recorded |

**Scenario set (each is a real, reproduced defect):**
`name-lead-in` · `name-correction` · `name-hard` (Sarah-Jane Okonkwo) ·
`wrong-date-repro` (from `CAc64a05f1`) · `two-services` · `reject-offer` ·
`screening-order` · `clean-happy-path`

> **Gate:** the harness runs `wrong-date-repro` and it **FAILS**. A repro that does
> not fail is not a repro. Nothing proceeds to Day 1 until this is red.

---

# Day 1 · Thu 30 Jul — make the name path safe to touch

| # | Task |
|---|---|
| 1 | **Identity read-back** — say the **full name, surname included**, at the confirmation step and require a yes before writing |
| 2 | **Slot-integrity guard** — refuse to write a booking whose slot differs from the one last confirmed aloud. Additive, fails closed, `guards.slot_mismatch_blocked` |
| 3 | **Re-pin `test_name_collector.py`** — 36 of 96 failures. Rewrite assertions against *current intended* behaviour |

Both guards ship first because they are cheap and they convert **silent wrong
data** into **something a caller can correct or a failure someone can see** — the
severity trade-down the whole production bar is built on.

The identity read-back is the highest value-per-line change available this week:
it makes the invisible defect audible, without touching the name state machine.

The re-pin is unglamorous and decisive: until those 36 assert something, the name
path has no safety net and must not be modified.

> **Gate:** `wrong-date-repro` now **PASSES**. Every booked call reads the full
> name back before writing. `name_collector` failures: 36 → 0. Full suite ≤ 96
> failures, no new failing file.

---

# Day 2 · Fri 31 Jul — name capture, part 1

Target the **correction path** — the shape that caused both incidents.

1. Mis-hear → caller corrects → corrected name must be stored.
2. `collected.name` must never be `None` after the caller has stated a name.
3. Failing test first, smallest diff, one commit.

> **Gate:** `name-correction` and `name-lead-in` scenarios pass. `name=NULL` rate
> **0%** across a 10-call automated sweep.

---

# Day 3 · Sat 1 Aug — name capture, part 2 + screening order

1. Hard names — hyphenated, non-English, surname-first (`name-hard`).
2. **S2** — screening must never fire after the booking confirmation.
3. Re-run the full scenario set.

> **Gate:** all 8 scenarios pass on the automated harness.

---

# Day 4 · Sun 2 Aug — residuals, and the last unknown

1. **B1 rejection path** — add rejection phrases to `utterance_requests_more_slots`
   so "none of those" routes to the deterministic capped batch.
2. **F-021** — run `two-services` for real. It is the last `BLOCKER`-rated defect
   whose status is genuinely unknown. Fix only if it reproduces.
3. **S3** — hallucinated presenting complaint.

> **Gate:** no scenario regressions. This is the last day code may change
> behaviour.

---

# Day 5 · Mon 3 Aug — regression sweep, no new code

1. Full automated scenario set, 3 runs.
2. Full pytest suite — diff the failing **set**, not the count.
3. Jules runs the 7-call manual sheet on top (`JULES_CALL_SHEET_2026-07-28.md`).
4. Log everything found. **Fix nothing** unless it breaks a booking.

> **Gate:** 3 consecutive clean automated runs + Jules's call 7 passes.

---

# Day 6 · Tue 4 Aug — freeze at noon

- **No code after 12:00.** Any push after that is an incident, not a plan.
- Three clean manual runs, one at demo time of day, UK mobile.
- Fresh fallback recording.
- Rollback SHA written down. Named on-call human.
- Pre-flight checklist for Wednesday.

---

# Day 7 · Wed 5 Aug — demo

**No pushes. None.** Pre-flight call 60 min before. Fallback cued.

---

## Rules for the week

1. **Every behavioural fix ships with a scenario in `tests/auto/` and a regression
   test in `tests/regression/`.** No exceptions.
2. **Canonical-first** — everything lands on `latency-eval`; clinics inherit by
   cherry-pick. Never fix on a clinic branch.
3. **Do not refactor `flow.py`.** `handle_transcript` changes only for a
   reproduced defect, smallest possible diff.
4. **One concern per commit**, so it can be reverted without its neighbours.
5. **Two cooks:** Quentin owns code, Jules owns validation. No push lands without
   a scenario covering it.
6. **Do not enable SMS.** Do not merge `feat/obs-*`. Do not chase multi-tenancy.
7. **The bar is not "perfect."** It is the five-point definition in CLAUDE.md: no
   silent wrong bookings, name capture reliable, every failure visible same-day.

## What is explicitly out

Multi-tenancy · `flow.py` refactor · SMS · concurrency (FM-17) · keypad cluster
(F-2) · the 8-hour suite runtime · dead `app/booking/tests`.

All real. None of them decide this demo.
