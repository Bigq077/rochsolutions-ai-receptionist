# Scoping — one presentation layer for every booking provider

**Written** 2026-09-09, after a Theorem call read a caller's chosen 6pm slot back
to them as 1pm.
**Question it answers** what has to change so that adding Jane, Carepatron or
Cliniko is *an API call and a set of IDs*, and nothing else.
**Status** scoping only. No code changed. Nothing here is safe to start without
the decisions in §7.

---

> ## ⚠️ CORRECTION, 2026-09-09, same day
>
> **Two rows of the table below were wrong and the headline number does not
> measure what it claims.** Verified with an AST walk per function rather than
> line numbers:
>
> * `other_dates_for_requested_day` — the fall-through (Northgate, JV) and
>   `diary` (Vital Edge) BOTH call `_name_the_other_matching_dates`. Only
>   `published` lacks it, and no live clinic uses `published`. **The claim that
>   B-110 was regressed on Northgate and JV was FALSE.**
> * `day_requested*` and the window feedback are likewise present on the
>   fall-through and diary. Only `published` lacks them.
> * The 83%/14% "deterministic rate" used `slot_offers.presented` as its
>   discriminator. That field only ever exists for **multi_day**, so every
>   single_day offer was miscounted as model-written on every clinic. The
>   number is not usable and is withdrawn.
>
> **The real defect is one branch, and it is confirmed from logs rather than
> inferred.** `_check_availability_acuity` sets `first_day` (line 3736) so
> Theorem's SINGLE-day readouts do get the deterministic builder — seen live at
> 10:04:46, `deterministic single_day offer built: 1 chunk(s)`. It never sets
> `presented_days`, so MULTI-day always fails the gate — seen live twice, on two
> different builds:
>
> ```
> 10:03  NO deterministic offer built — mode='multi_day' has_presented_days=False
> 11:04  NO deterministic offer built — mode='multi_day' has_presented_days=False
> ```
>
> The cause is at line 3599, and the comment above it states the old contract
> out loud:
>
> ```python
> # The <=2-times-per-day cap is enforced by the slot formatter
> # (SLOT_FORMATTER_SYSTEM_PROMPT, multi_day).
> _present_days = days_data[:3] if _presentation_mode == "multi_day" else days_data
> ```
>
> Theorem's multi-day cap is enforced **by the prompt**, not by code. That is the
> pre-`slot_offer.py` architecture — the model writes the sentence and a repair
> layer reverse-parses it — which that module's own docstring says "cannot
> converge" and where "fifteen B-numbers live". Theorem's single_day path was
> migrated; its multi_day path never was.
>
> Everything in §5 (target shape) and §6 (stages) still stands. **Stage 1 is
> deleted** — it was fixing a gap that does not exist. The real first step is
> Stage 2.

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

## 6. Staged plan — as decided 2026-09-09

Owner decisions are in §7. Stage 1 of the original plan is **deleted**: it fixed
a gap that did not exist (see the correction at the top).

Each stage ships alone and reverts alone. Work happens on `feat/one-presentation-layer`,
lands on `latency-eval`, and reaches the live branches later by fast-forward.

**Stage A — Acuity multi_day joins the deterministic builder.** The narrow fix,
first and on its own, because it is what a caller actually heard and it is
testable against two stored Theorem calls without touching the architecture.
`_check_availability_acuity` stops capping and stops deciding
`presentation_mode`; the dispatcher routes its result through
`_cap_presented_slots` like the other three.

*Contract change to watch:* Acuity today puts the **trimmed** days in the
result's `available_days` and the **full** set in `session["available_days"]`.
Every other reader puts the full set in both and lets `_cap_presented_slots`
derive `presented_days`. Stage A aligns Acuity to that, so anything reading the
result's `available_days` as "what was spoken" changes meaning.

Gate: `deterministic multi_day offer built` on a Theorem call; the read-back
correction misfire unreproducible on the stored calls; suite clean.

**Stage B — `lead_in` everywhere. DONE, and it hit a ceiling worth reading.**
Owner decision 2. `_cap_presented_slots` now sets `lead_in="earliest"` for the
three readers that come through it, guarded by two conditions rather than one:
the caller asked for the soonest (`caller_wants_soonest`, the same predicate
B-137 and B-142 already steer by — not a fourth ASAP matcher), AND the day being
read out is the soonest in the payload (`_earliest_available_date`). Acuity is
untouched, and the containment is structural: stage A routes it through this
function only on multi_day, and multi_day never carries a lead-in (B-125).
`earliest_lead_in_is_true` still has the last word on the within-day half.

**THE CEILING.** Verified against both real backends, no call and no deploy:

```
northgate  "as soon as possible"  -> multi_day, no first_day, no lead-in
theorem    "as soon as possible"  -> single_day, first_day, lead_in=earliest
```

The two paths decide `presentation_mode` by different rules — Acuity from the
CALLER'S REQUEST, the shared layer from the DATA — which stage A documented and
deliberately did not converge. A lead-in is a claim about ONE day, so it can only
ride on `single_day`; and on the shared readers an ASAP request stays multi_day
whenever more than one day survives the filters. **So decision 2 is delivered as
far as the current mode rule allows, and no further.**

Measured, offers in the 21 days to 9 Sep: northgate reaches `single_day` on
**17%** (8 of 48), theorem on **80%** (12 of 15), JV n=1. So on the busiest of
the three the opener can fire on at most one offer in six — and only the subset
of those where the caller asked for the soonest.

**Closing the rest of it means converging the mode rule, which is a behavioural
change to three clinics (two of them live patient lines) and belongs to stage C
or D with a call behind it — not to a lead-in.** Adopting Acuity's
request-derived rule looks right, since it honours what was asked, but it would
change which callers hear one day instead of three. That is the same change
stage A refused in the other direction, and for the same reason.

**Stage C — one offer record everywhere, and the MODE RULE.** Stage B raised the
mode rule from a documented divergence to the thing blocking a shipped decision,
so it joins this stage: one rule for `presentation_mode`, owner-chosen, applied
on all four paths. Do it with a call behind it — it decides whether a caller
asking for the soonest hears one day or three. With Stage A in, `build_slot_offer`
and `apply_offer_to_session` run on Theorem, so the reverse-parse and its repair
layer go dead there. Gate: no `could not resolve spoken option(s)` on any clinic.

**Stage D — extract the provider interface.** Only now is `fetch_free_slots` a
small function. Four acquisition strategies, one signature — see §7 decision 5
for why they are all legitimate.

**Stage E — clinic policy above the seam, values per clinic.** Owner decision 3.
Lead-time, working hours and closed dates get ONE mechanism applied at one
place, reading each clinic's own values from config. Explicitly NOT unified
behaviour: clinics keep different hours and different closed dates. Same shape
as D2's caps — shared code path, per-clinic numbers. Last, because it is the
stage that can change what a clinic offers.

## 7. Decisions — ANSWERED 2026-09-09

1. **Ship the fix.** ✅ Go ahead. (Moot as originally posed — the gap it
   addressed did not exist. Stage A is the real first step.)
2. **`lead_in` everywhere?** ✅ **Yes, wanted on every clinic.** Stage B.
3. **The filter block.** ✅ **Stays per clinic** — clinics have different hours
   and different closed dates. Read as: *one mechanism, per-clinic values.* The
   code path is shared; the numbers come from each clinic's own config. Not
   "make every clinic behave identically".
4. **Where does it run?** ✅ Own working branch → `latency-eval` → live branches
   later. No direct work on `production`.
5. **Four acquisition strategies are legitimate and stay.** Recorded because it
   was asked and the answer shapes Stage D:

   | clinic | `booking_system` | how availability is obtained |
   |---|---|---|
   | Northgate, JV | `google_calendar` | generate candidates from working hours, subtract freebusy |
   | Vital Edge | `google_calendar_provisional` + `diary` | working envelope MINUS everything in the diary |
   | Theorem | `acuity` | ask the provider API for free slots |

   Northgate and JV are genuinely identical. Vital Edge differs because the
   practitioner's calendar records work he is DOING rather than slots he is
   offering — the inversion that once offered a caller his flight to Ibiza.
   All four reduce to one `fetch_free_slots() -> [(start, end)]`, which is why
   the seam works.

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
