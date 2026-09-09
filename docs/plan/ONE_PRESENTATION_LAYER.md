# Scoping — one presentation layer for every booking provider

**Written** 2026-09-09, after a Theorem call read a caller's chosen 6pm slot back
to them as 1pm.
**Question it answers** what has to change so that adding Jane, Carepatron or
Cliniko is *an API call and a set of IDs*, and nothing else.
**Status** scoping only. No code changed. Nothing here is safe to start without
the decisions in §7.

---

## 1. The finding in one table

There are **four** availability readers. They do not agree, and it is not a
two-way split.

| | Acuity | fall-through | diary | published |
|---|---|---|---|---|
| **used by** | Theorem | Northgate, JV | Vital Edge | provisional |
| **lines** | **933** | 686 (dispatcher) | 317 | 160 |
| `_cap_presented_slots` | ✗ | ✓ | ✓ | ✓ |
| deterministic slot sentence | ✗ | ✓ | ✓ | ✓ |
| offer record + DTMF map written | ✗ | ✓ | ✓ | ✓ |
| `operational.slot_presentation` caps (D2) | ✗ | ✓ | ✓ | ✓ |
| `other_dates_for_requested_day` | ✓ *(own)* | **✗** | ✓ | ✓ |
| `lead_in` ("the earliest I have is…") | ✓ *(own)* | ✗ | ✗ | ✗ |
| named-weekday contract (`day_requested*`) | ✓ *(own)* | ✗ | ✗ | ✗ |
| window-widening feedback | ✓ *(own)* | ✗ | ✗ | ✗ |
| `band_spent_label` | ✓ *(own)* | ✗ | ✗ | ✗ |

Read the last five rows before concluding Acuity is simply behind. **It is
ahead on five features and behind on four.** Convergence is a merge in both
directions, not a deletion.

`other_dates_for_requested_day` is the sharpest example: `_name_the_other_matching_dates`
is called at two sites only (7:6924 diary, 7:7340 published). Northgate and JV —
the two clinics on the "good" path — never get it. So a JV caller asking about
Tuesdays is told about two of four and never hears the rest exist. That is B-110
regressing on the one path nobody checked.

---

## 2. What it cost, measured

**Deterministic sentence rate**, offers recorded since 25 Aug, discriminated by
whether the record carries a capped payload (only `_cap_presented_slots` writes
one):

    northgate    47 offers, 39 deterministic   (83%)
    theorem_v3   14 offers,  2 deterministic   (14%)

**The live damage**, Theorem `CAba2e3d8eb282bb`, 9 Sep 11:05, build `ba315832`:

```
11:05:09  Susie : "Wednesday the 16th — I've got one o'clock or six in the evening."
11:05:22  caller: "uh yeah 6 in the evening works"
11:05:24  [ms_gate5] read-back time corrected: 'six in the evening' ->
          'one in the afternoon' (the offer had it right; the model crossed two options)
11:05:30  Susie : "So that's Wednesday the 16th of September at one in the afternoon…"
```

The model read back **correctly**. The guard overrode it. Its input is the offer
record, and on Acuity that record is never written —
`slot buf: could not resolve spoken option(s) … offer record left unchanged` —
so it compared against a stale projection and corrupted a correct read-back.
Caller abandoned; judge score 2.

**A guard that only has trustworthy input on three of four paths is worse than
no guard on the fourth.** That is the argument for doing this, and it is
stronger than the consistency argument.

---

## 3. Where the seam actually is

The dispatch tree today:

```
_exec_check_availability                          app/tools/receptionist_tools.py:6240
├─ uses_acuity()                    → _check_availability_acuity        :2966-3899
│                                     then _filter_same_day_slots ONLY  :6377
├─ booking_system == "google_calendar_provisional"
│   ├─ availability_mode "diary"    → _check_availability_diary         :7025-7342
│   └─ else                         → _check_availability_published     :7345-7505
└─ fall-through (generate_candidate_slots / filter_free_slots)          :6538,6605,6712
```

Line 6377 is the whole defect in one line: the Acuity branch returns after
`_filter_same_day_slots` and never reaches `_cap_presented_slots`.

**The contract the other three already honour** — a provider returns:

```python
{"available_days": [...], "total_days": N}      # plus error/message on failure
```

`_check_availability_diary` and `_check_availability_published` emit exactly
that: `available_days, total_days, slots, start, error, message`. Nothing else.
Then the shared layer adds `presentation_mode`, `first_day` | `presented_days`,
`more_times` and `sparse_rota_note`.

**Acuity emits 22 fields and builds its own payload.** The seam is
**line 3522**, where `days_data` is complete after the same-day drop. Above it:
fetch, the 90s cache, lead-time, working hours, bank holidays, error returns.
Below it: 376 lines that are presentation.

That is the cut. Everything below 3522 has an owner elsewhere or should have.

---

## 4. Classifying the 933 lines

| block | lines | verdict |
|---|---|---|
| 2966–2998 args, appointment-type / calendar id resolution | 32 | **provider** — stays |
| 2998–3138 90s availability cache | 140 | **generic**, Acuity-only today |
| 3138–3220 B-86 window widening | 82 | **generic**, Acuity-only today |
| 3220–3499 lead-time, working hours, bank holidays, error returns | 279 | **mixed** — see below |
| 3499–3522 `_build_days_data`, same-day drop | 23 | **the seam** |
| 3523–3545 session writes + cache populate | 23 | **generic** |
| 3546–3600 `presentation_mode`, `days_data[:3]` | 54 | **presentation — delete, it is `_cap_presented_slots`** |
| 3601–3700 `day_requested*`, `guidance`, window feedback | 99 | **promote** to the shared layer |
| 3703–3899 `first_day`, `more_times`, `band_spent_label`, `other_dates`, `lead_in` | 196 | **mixed** — some duplicate, some promote |

Roughly **250 lines delete**, **300 promote**, **380 stay**. Those are estimates
from section boundaries, not a line-by-line audit — the audit is step 1 of §6.

The 279-line filter block is the awkward one. Lead-time, working hours and bank
holidays are *clinic policy*, not Acuity policy, and the other three readers
handle them differently or not at all. Whether they move above the seam is a
correctness question about the other three clinics, and it is the part of this
work most likely to change behaviour somewhere nobody is looking.

---

## 5. The target shape

```
provider adapter                 ← the ONLY thing a new clinic adds
  fetch_free_slots(window, ids) -> [(start, end)]
        │
        ▼
shared availability pipeline     ← one owner, four clinics, every future one
  _filter_same_day_slots         same-day policy
  clinic policy filters          lead time, working hours, closed dates
  _build_days_data               grouping, spoken forms, band handling
  _cap_presented_slots           how many, and which          (D2 lives here)
  _name_the_other_matching_dates the dates held back          (B-110)
  build_slot_offer               THE SENTENCE                 (one owner)
  apply_offer_to_session         the record + the DTMF map
        │
        ▼
   speech, keypad, read-back guard — all reading one record
```

Adding Jane becomes: write `fetch_free_slots`, put the IDs in `clinic.json`,
done. That is the outcome you asked for, and it is not reachable by patching
Acuity to emit `presented_days`.

---

## 6. Staged plan

Each stage ships alone, reverts alone, and has a gate. **The measurable one is
Theorem's deterministic rate: 14% → 83%+.**

**Stage 0 — audit (no code).** Line-by-line classification of 2966–3899 into
provider / presentation / generic-but-only-here. Output is a table, not a diff.
The 279-line filter block is the deliverable; the rest is confirmation.

**Stage 1 — promote what Acuity has and the others lack.** Move
`day_requested*`, the window feedback and `lead_in` into the shared layer, and
call `_name_the_other_matching_dates` on the fall-through path too. *This
changes Northgate and JV*, which is the point — they are missing B-110 today.
Gate: replay the stored JV/Northgate calls; a caller who asked about one weekday
should now hear the other dates exist.

**Stage 2 — route Acuity through `_cap_presented_slots`.** Delete the
`days_data[:3]` and the bespoke `presentation_mode`, return
`{available_days, total_days}` from the seam, and let the dispatcher do what it
does for the other three. Gate: Theorem's deterministic rate, plus the
read-back-correction misfire must be unreproducible on the stored calls.

**Stage 3 — one offer record everywhere.** With Stage 2 in, `build_slot_offer`
and `apply_offer_to_session` run on Theorem, so the reverse-parse and its ~900
lines of repair become dead on that path too. Gate: no
`could not resolve spoken option(s)` on any clinic.

**Stage 4 — extract the provider interface.** Only now is `fetch_free_slots` a
small function. Doing it before Stage 2 means extracting an interface around
logic that is about to move.

**Stage 5 — clinic policy above the seam.** The 279-line filter block. Last,
because it is the one that can change behaviour on a clinic nobody is testing.

---

## 7. Decisions needed before Stage 1

1. **Stage 1 changes Northgate and JV.** Giving them `other_dates_for_requested_day`
   is a fix, but it is a change to what live callers hear on two lines. Ship it,
   or hold the whole thing until Theorem is done?
2. **Is `lead_in` wanted everywhere?** "The earliest I have is…" is a ranking
   claim, guarded by `earliest_lead_in_is_true`. Today only Theorem can make it.
3. **The 279-line filter block.** Lead-time, working hours and closed dates
   differ per clinic today — partly by design, partly because nobody unified
   them. Unifying may change what a clinic offers. Which of the three is policy
   you want identical, and which is genuinely per-clinic?
4. **Where does this run?** Theorem is on `production` and it is Mark's line.
   Stages 2–3 want a Theorem call each. Out-of-hours, or a two-site fixture on
   the demo service first? The handover's Phase 5 argued for the fixture and it
   would pay for itself here.

---

## 8. What I would not do

- **Not patch Acuity to emit `presented_days`.** It removes the symptom, leaves
  the seam wrong, and makes the next provider harder rather than easier.
- **Not start with the provider interface.** Extracting an interface around
  logic that is about to move is wasted work.
- **Not touch the 279-line filter block early.** It is the highest-risk part and
  the least related to what you actually heard on the call.
- **Not do this without the replay harness.** 136 stored Theorem calls and 119
  Northgate ones are the only test that means anything for slot presentation —
  the 9,400-test suite pins engine logic and almost none of it pins what a
  caller hears.

---

## 9. Honest note on how this was missed

The `NO deterministic offer built — branch=none-matched` line was in the
2026-09-09 10:03 Theorem log and I read it, and reported it as *"pre-existing on
the Acuity path rather than something D2 caused"* — using it to clear my own
change and then filing it. It was in front of me twice before the read-back
misfire made it unignorable.

It also means D2, shipped that morning as a clinic-configurable cap, **does not
reach Theorem at all**. I wrote a test asserting Theorem could not opt in and
read that as a config-loader quirk. It was this, and the test is now evidence
for the case rather than against it.
