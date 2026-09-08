# Call sheet — 8 September 2026

`production` = **`08e99fab`** (the revert target — write it down before any
promotion). `latency-eval` carries all six fixes below.

Two calls. **Do them in this order** — the first one is the only one that
touches a line real patients ring.

---

## What changed since the last call sheet

| # | commit | what it fixes | reachable on |
|---|---|---|---|
| 1 | `8b97f1e4` | Gate 5 stood down on a fresh three-day list because the caller's THREE o'clock folded to a bare `3` and matched `"Number 3"` | all four clinics |
| 2 | `88e0df08` | the same bug through a second door — clinics with `speak_part_of_day: false`, i.e. **northgate**, where the o'clock label IS a bare number | northgate |
| 3 | `4efbdc95` | the P6b stand-down never recorded the slots its sentence spoke, so the caller's next pick resolved against the previous offer | all four clinics |
| 4 | **`197e0bae`** | **the actual root cause** — the guard that blocks a repeat `check_availability` was gated on a phrase list that misses every specific pick | all four clinics |
| 5 | `7aa07f4a` | the model is now TOLD the caller has picked (both prompt builders), plus a dispatch-level backstop | all four clinics |
| 6 | `49b72699` | "monday doesn't work" no longer resolves to a slot on Monday | all four clinics |

All six come out of one call: `CA4215ab7f` (theorem_v3, 01:15, build
`08e99fab`), the one reported as horrible behaviour.

> **#4 is the one that matters, and #1–#3 nearly weren't worth the trip.**
> `calls.slot_offers` for that call holds two entries with an **identical**
> twenty-day payload, and the deterministic offer Gate 5 would have spoken is
> **91% token-identical** to what the caller actually heard. So #1–#3 only
> change which *wording* of the wrong list gets spoken. The list existed because
> the model called `check_availability` a second time after the pick had already
> resolved, and the guard that exists to stop exactly that was wired to a reader
> that misses every specific pick. Full write-up:
> `docs/plan/RE_QUERY_AFTER_A_PICK_2026-09-08.md`.

---

## CALL A — Theorem, on production as it stands  ·  +447380841468

**Do not promote first.** This call is worth more against `08e99fab` than
against anything newer, because three changes already live on that branch have
never been exercised on Theorem's Acuity path: P8 (closed day vs lead time), the
read-back name steer, and Phase A's `uses_acuity` routing. A failure here with
no new code in the way names its own cause.

| turn | say | what it exercises |
|---|---|---|
| 1 | "Hi, I'd like to book an appointment." | `_build_theorem_v3` |
| 2 | **"Alcester"** | the location ladder — Theorem only |
| 3 | "What have you got?" | P8 + `uses_acuity` → `_check_availability_acuity` |
| 4 | pick a slot **by its time**, e.g. "three in the afternoon works" | the defect above, on the clinic it happened to |
| 5 | **listen** | see the pass/fail line below |
| 5b | if she confirms, say **"actually, that doesn't work"** | `49b72699` — she must go and look again, NOT treat it as a pick |
| 6 | "Quentin, surname R-O-C-H, Roch" | surname parse + read-back steer |
| 7 | confirm the number, then **"Yes, book it"** | `_book_appointment_acuity` — a REAL write |
| 8 | "Actually, can you cancel that?" | `_cancel_appointment_acuity` |

**Cancel through Susie, not in Acuity.** The delete path is part of what is
being verified.

### The pass/fail line, turn 5

* **PASS** — she confirms the slot you named and asks for your name.
* **FAIL** — she reads out a list of *other days*. That is the live defect,
  reproduced. Say so and stop; it is fixed on `latency-eval` and Call B proves
  the fix.

### Then, in the Render log for the Theorem service

```
[build_info] running build 08e99fab
Row built — ... name=Quentin Roch            <- not "Quentin R-O-C-H"
```

and on turn 7, that it reached the **Acuity** executor. If `uses_acuity` were
wrong the symptom is a refusal, not a wrong write — both guard families fail
closed. Loud, by design.

---

## CALL B — the demo line  ·  +447366263180

The demo line, and nothing else, so it is safe to push to. This call proves all
six.

**One honest caveat on #2.** I described northgate as the *worst* case for it.
That is a claim about the code — the guard genuinely had a hole there, proven by
test — and **not** an observed risk: the stand-down defect fires zero times on
northgate across 921 stored calls, and `speak_part_of_day: false` is barely a day
old with one numbered readout since. Today's northgate offers were 08:50, 12:10,
17:10 — no o'clock times, so the digit collision was arithmetically impossible.
Turn 3 exists to *create* the collision on purpose.

| turn | say | what it exercises |
|---|---|---|
| 1 | "Hi, can I book an appointment?" | |
| 2 | "What have you got this week?" | the offer |
| 3 | **pick the THIRD option by its time** — if she offers three, say e.g. "three works" or "four works", naming the time of option 3 | #1 and #2: the number of the option and the hour of the slot are the same digit |
| 4 | **listen** | PASS = she confirms it. FAIL = she reads a new list of days |
| 5 | *if* she does read a new list: **"the last one"** | #3: it must resolve to the last slot she just READ, not to the one you picked before |
| 6 | give a name, confirm, book | nothing new, but it ends the call cleanly |

Turn 3 is the whole test and it needs the digits to line up: **option N whose
time is N o'clock**. If the offer does not give you one, ask "anything in the
afternoon?" and try again — an afternoon list usually numbers 1/2/3 against
one/two/three o'clock.

### What to check in the log

```
[build_info] running build <the sha you pushed>
[ms_conn v3] caller ACCEPTED 2026-09-..T15:00:00+01:00 — pinned ... (P6b)
```

and then, on the turn after the pick, **this line is the one that matters**:

```
[ms_llm] check_availability BLOCKED - caller is accepting an already-offered slot
```

That is #4 working, and it is what stops the 16 seconds. If instead you see

```
[ms_tools] availability re-queried after the caller had already chosen ... - narrowed
```

the guard was bypassed and the backstop caught it — tell me, because it means
the guard's outer conditions have a hole the corpus did not show.

There should be **no** `deterministic offer STOOD DOWN ... (P6b)` line. If one
appears anyway, the next line should now be `B-134 (P6b): the stood-down
sentence named N payload slot(s)` — fix #3 doing its job.

---

## Why #3 is not made redundant by #1

Fix #1 stops the stand-down firing on *this* call. But P6b standing down is a
legitimate thing that will keep happening, and a confirmation may also carry an
alternative:

> "That's Wednesday at three — or I've also got Friday at nine."

That names the accepted slot, so P6b fires **correctly**, and the alternative is
stranded in exactly the same way. Turn 5 of Call B is the cheap way to see the
recording work; the shape above is the reason it has to.

---

## After both calls

If Call A passes and Call B passes, promote `latency-eval` → `production` by
fast-forward, out of hours, with `08e99fab` written down as the revert target.
Then re-run **turns 3, 7 and 8 of Call A** against the new build — those are the
three that go through `uses_acuity`.

If Call A fails at turn 5, promote anyway (the fix is what it needs) but re-run
Call A in full afterwards rather than the three-turn subset.
