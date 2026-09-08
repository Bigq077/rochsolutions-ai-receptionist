# Call sheet — 8 September 2026

`production` = **`08e99fab`** (the revert target — write it down before any
promotion). `latency-eval` carries all six fixes below.

**ONE call, after promoting.** An earlier version of this sheet asked for a
call against production *before* promoting, to "separate the existing work from
the routing change". That was wrong and it is worth saying why, because the
reasoning looked sound:

* it would have **reproduced a defect we already hold a complete obs record
  of** (`CA4215ab7f`, transcript + `slot_offers` + latency) — no new information,
  at the cost of a real Acuity write, a cancel, and your Twilio spend;
* `uses_acuity` (Phase A) **is not on production**, so the two turns annotated
  as exercising it could not have done. The sheet contradicted its own table;
* what remained testable there — P8 and the read-back steer — are both already
  on production, and the steer was exercised on four northgate calls on 7 Sep.

The thing with no evidence behind it is the **fix**. So: promote, then call.
That is also the workflow CLAUDE.md sets out — out of hours, revert target
written down, a real call after any engine change — and calling first inverted
it.

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
| 7 | **`0f6dd055`** | **a garbled surname flipped Susie into cancelling a real booking** — found by the 07:52 demo call | all four clinics |
| 8 | `f7e26b9`+ | the first day-ish phrase of the call no longer orders every later readout | all four clinics |

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

## STEP 1 — promote

`latency-eval` (`f0dcea98`) → `production`, fast-forward, **out of hours**.

> **Revert target: `08e99fab`.** Write it down before you push —
> [[build-sha-only-in-render-log]] means the only proof of what is running is
> `[build_info] running build <sha>` in the Render log.

## STEP 2 — the call · Theorem · +447380841468

Theorem's Acuity path has had no live call since 2 September and now carries
five unverified changes: P8, the read-back steer, Phase A's `uses_acuity`
routing, the Gate 5 repairs, and the re-query guard. This one call reaches all
of them, which the pre-promotion version could not.

| turn | say | what it exercises |
|---|---|---|
| 1 | "Hi, I'd like to book an appointment." | `_build_theorem_v3` |
| 2 | **"Alcester"** | the location ladder — Theorem only |
| 3 | "What have you got?" | P8 + `uses_acuity` → `_check_availability_acuity` |
| 4 | pick a slot **by its time**, e.g. "three in the afternoon works" | **the fix**, on the clinic and the phrasing it failed on |
| 5 | **listen** | see the pass/fail line below |
| 5b | if she confirms, say **"actually, that doesn't work"** | `49b72699` — she must go and look again, NOT treat it as a pick |
| 6 | "Quentin, surname R-O-C-H, Roch" | surname parse + read-back steer |
| 7 | confirm the number, then **"Yes, book it"** | `_book_appointment_acuity` — a REAL write |
| 8 | "Actually, can you cancel that?" | `_cancel_appointment_acuity` |

**Cancel through Susie, not in Acuity.** The delete path is part of what is
being verified.

### The pass/fail line, turn 5

* **PASS** — she confirms the slot you named and asks for your name.
* **FAIL** — she reads out a list of *other days*. The fix did not hold; revert
  to `08e99fab` and tell me, with the Render log for that turn.

### Then, in the Render log for the Theorem service

```
[build_info] running build f0dcea98
[ms_llm] check_availability BLOCKED - caller is accepting an already-offered slot
Row built — ... name=Quentin Roch            <- not "Quentin R-O-C-H"
```

That middle line is the whole point of the call.

and on turn 7, that it reached the **Acuity** executor. If `uses_acuity` were
wrong the symptom is a refusal, not a wrong write — both guard families fail
closed. Loud, by design.

---

## STEP 3 (optional) — the demo line  ·  +447366263180

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
| 6 | **give the name as "Quentin Roch"** and let STT mangle it | **`0f6dd055`** — a garbled surname must NOT start a cancellation |
| 7 | confirm, book | ends the call cleanly |

### The line that proves turn 6

There must be **no** `situational head (cancel_req)` after the name question.
If one appears, the gate did not arm — send me the `capture_phase` value from
the `[LAT]` line for that turn, because that is what decides it.

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

## If the call fails

Revert `production` to **`08e99fab`** and send me the Render log for the turn
after the pick. The two lines that name the cause are

```
[ms_llm] check_availability BLOCKED ...        <- the guard fired
[ms_tools] availability re-queried ... narrowed <- the guard was bypassed, backstop caught it
```

Neither appearing means the pick never resolved at all, which is a different
defect and a different fix.

Step 3 is only worth doing if you want the band-less door (#2) exercised
deliberately — northgate's diary has to hand you an option number matching its
own hour, which it did not on 7 September.
