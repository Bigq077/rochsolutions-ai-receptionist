# Call sheet — verifying the unphoned deploys

**Date:** 28 August 2026
**Why this exists:** a run of availability and barge-in fixes shipped to all
three live lines over the last week and almost none has been heard on a phone.
Several of the newest depend on the *model acting on payload guidance* rather
than on code refusing to say something, which is the failure mode this codebase
has the worst record with. This sheet walks **backwards** from the newest deploy.

---

## 0. How much of this has to be repeated per line

Not all of it. Scoped by where each fix actually lives, not by branch count.

**There are THREE availability executors, not two.** This is the one place where
"one Acuity line and one Calendar line" under-tests:

| Line | `booking_system` | Executor that runs |
|---|---|---|
| Theorem | `acuity` | `_check_availability_acuity` |
| Vital Edge | `google_calendar_provisional` + `availability_mode: diary` | `_check_availability_diary` |
| JV | `google_calendar` | generic path in `_exec_check_availability` |

Vital Edge and JV are both "Google" and **share no availability code**. B-110
had to hook two separate seams for exactly that reason. So anything about
*which days and times are offered* needs all three.

**Everything else is shared engine code** and only needs one line, because the
build SHA already proves the same code is deployed on the others:

| Block | Lives in | Run on |
|---|---|---|
| 1 — bare weekday | three executors | **all three** |
| 2 — pick by number | Acuity only (B-108b) | **Theorem** |
| 3 — day, then another day | `app/tools/slot_followup.py` (shared) | **any one** |
| 4 — named date / window | three executors | **all three** |
| 5 — barge-in | `app/media_streams/connection.py` (shared) | **any one** |
| 6 — cancel/reschedule SMS | shared dispatcher, but Theorem short-circuits | **Theorem + one Google line** |
| 7 — ring-first delay | JV config | **JV** |

That is 12 block-runs instead of 19, and every cut is justified by the file the
fix landed in rather than by assuming the branches match.

**The one caveat:** shared code can still behave differently per clinic when a
prompt or a clinic gate sits in front of it. If a shared-code block fails on the
line you chose, run it on the other two before concluding the fix is broken —
the difference may be config, not the fix.

---

## 1. Before you dial

**Confirm the build actually deployed.** `/health` returns a hardcoded `1.0.0`
and tells you nothing. The only proof is in the Render log at call cleanup:

```
[build_info] running build <sha>
```

| Line | Branch | Expected SHA |
|---|---|---|
| Theorem | `theorem-onboarding` | `e28c55aa` |
| Vital Edge | `vitaledge-onboarding` | `99dd7ffd` |
| JV | `jv_v2` | `e734d53e` |

If the SHA is older, **stop** — you are testing yesterday's build, and the
whole "shared code only needs one line" argument above collapses, because it
rests on the deploy having landed.

**Numbers.** JV live line is `+447367002651` (`TWILIO_PHONE_NUMBER` in `.env`);
`+447366263180` is the demo line, where the Sheets / EVAL_STAFF warnings are
known and accepted. Theorem and Vital Edge inbound numbers are set per-service
in Render/Twilio and are **not** in the repo — fill in before starting:

- Theorem inbound: `________________`
- Vital Edge inbound: `________________`

**Housekeeping.** A completed booking writes a real diary entry and may send a
real SMS. Cancel test bookings **by calling back and asking Susie**, never by
deleting from the calendar — the cancel path is itself under test in block 6.

---

## Block 1 — bare weekday · ALL THREE LINES

The newest change and the least proven. Three separate implementations.

**Deploys:** `e28c55aa` (Theorem, B-109) · `2a8953a7` / `24efb26e` (VE, JV,
B-110) · underneath them `7ec980af` / `1581ab2d` / `45433a7e` (B-108).

> "Hi, have you got anything on a Tuesday?"
>
> *(use whichever weekday genuinely has several free dates)*

**Must happen**

- Times for **one date** (Theorem) or **up to two dates** (VE, JV), and then she
  **names the other matching dates** — "I've also got times on the 8th, 15th and
  22nd".
- She must **not** state a *time* on any later date. Naming the date is the
  whole change; times for them were deliberately withheld.

**Must NOT happen**

- "That's the only slot on Tuesday the 1st" while later Tuesdays have times.
  That is B-108 — the sentence that ended a real call.
- "We're fully booked on Tuesdays" / "we don't do Tuesdays".
- A date she was never given. If she offers one, ask for a time on it and check
  the diary. An invented date is a P1 — stop and report.

**If she says nothing about the other dates:** do not assume the guidance was
ignored. Check the Render log for `other_dates_for_requested_day` in the
payload. If the dates are there, the sentence was generated and something
downstream deleted it — Gate 5 is the first suspect. That distinction has cost
four misaimed fixes before.

---

## Block 2 — picking a slot by number · THEOREM ONLY

B-108b changed the Acuity path only. On VE and JV the aligner was already there
and this deploy changed nothing, so testing it there proves nothing new.

**Deploys:** `ad1b2545` (B-108b) · `6beae413` ("the second day").

> "What have you got this week?"
> *(let her read the options)*
> "I'll take the second one."

**Must happen**

- She confirms **the time she read out second**, on the day she read it out.
- Follow with "Sorry, which day was that?" — she names the **same** date.

**Must NOT happen**

- Confirming a date never spoken aloud. That was the live risk: the ordinal
  resolved by index into a list holding three *different* dates while only one
  was read out.

**Also in this call:** say **"the second day"** rather than "the second one" —
it must be answered about the second *day*.

---

## Block 3 — one day, then another · ANY ONE LINE

All four of these landed in `app/tools/slot_followup.py`, which every clinic
shares. One line is enough.

**Deploys:** `9e2d74e8` ("what else on Wednesday?" answered about Friday) ·
`813c8df9` (the day you came back to judged against the day you left) ·
`d84a6b14` (looking at one day erased every other) · `c63fec61` ("the twenty
second" unrecognised).

> "What have you got on Wednesday?"
> *(she reads Wednesday)*
> "And what about Friday?"
> *(she reads Friday)*
> "Sorry, what else was there on Wednesday?"
> *(then)* "Have you got anything on the twenty second?"

**Must happen**

- The Wednesday answer is about **Wednesday**, by name, consistent with the
  first time she said it.
- "The twenty second" is understood as a **date**.

**Must NOT happen**

- Friday's times returned under Wednesday's name.
- "That's all I have on Wednesday" when she listed more earlier.
- "The twenty second" read as a time, or ignored.

---

## Block 4 — a date the scan may not have reached · ALL THREE LINES

Each executor has its **own** window-widen — `1ec52e26` (Acuity), `6c8eac49`
(VE diary), `7cfc8425` (generic). Different code, so all three.

> "Have you got anything on the [date three or four weeks out]?"

**Must happen**

- She answers about that date, or says plainly it is **beyond how far ahead
  bookings are taken**.

**Must NOT happen**

- "That date is fully booked" / "we're closed then" for a day the scan never
  reached. A gap in a payload can never be spoken as a fact about the clinic.
  Cross-check the diary — this is the failure that reached a caller on 10 Aug.

---

## Block 5 — interrupting her · ANY ONE LINE

Both landed in `app/media_streams/connection.py`, shared by every clinic.

**Deploys:** `c02946df` (the re-ask that replaced a false ack was never spoken)
· `65b829a0` ("Yes, go on." to a caller who had not said anything).

**5a — talk over her** while she reads out slots: *"sorry, can I just ask…"*

- She must stop and then **say something**. Silence after a barge-in is the
  defect. No dead air beyond about three seconds without a filler.

**5b — cough or say "uh", then stay quiet.**

- She must **not** reply "Yes, go on." to a caller who never spoke. She should
  carry on, or re-ask her question.

---

## Block 6 — cancel and reschedule, and the text · THEOREM + ONE GOOGLE LINE

**Correction to an earlier draft of this sheet:** `9a301e5e` is not about the
*booking* confirmation. It landed in `_exec_cancel_appointment` and
`_exec_reschedule_appointment` — this is the **cancel/reschedule** text. Testing
a fresh booking would not have exercised it.

Two lines because Theorem short-circuits to its Acuity executors and takes a
different route through this code than the Google clinics.

> Book an appointment (gives you something to cancel), then call back:
> "I need to move my appointment" — then, on a third call, "I need to cancel it."

**Must happen**

- The text arrives, **or** she does not claim one was sent. The defect was the
  record saying "sent" when nothing went out, so log and handset must agree.
- The cancellation is confirmed **once**. No apologising for a cancellation that
  already succeeded, no asking you to confirm repeatedly.
- On the reschedule: the diary shows the **new** time and not both.

**While you are here** (these are older but cheap to observe): the name on the
diary entry matches what you gave, **including the surname**, and the **end**
time matches the service length — not just the start.

---

## Block 7 — ring-first delay · JV ONLY

**Deploy:** `c64c7fc8` (twenty seconds of ringing before anyone speaks).

> Call and let it ring without pressing anything.

- Human-first ring for about **twenty seconds** before Susie picks up. Time it.
- Pressing `1` during the ring must still reach screening.

---

## Recording results

Note the SHA from the Render log, pass/fail, and **her exact sentence** when it
fails. Wording matters more than a summary here — several of these defects are
invisible in a paraphrase, because the sentence was true about one date and
false about the weekday.

| # | Block | Theorem | Vital Edge | JV |
|---|---|---|---|---|
| 1 | Bare weekday | | | |
| 2 | Pick by number | | — | — |
| 3 | Day, then another day | *(any one)* | | |
| 4 | Date out of window | | | |
| 5 | Barge-in (a and b) | *(any one)* | | |
| 6 | Cancel / reschedule SMS | | *(one Google line)* | |
| 7 | Ring-first delay | — | — | |

Blocks 1 and 2 sit on the newest deploys — report failures there before running
the rest. Everything below them has been live longer and is better evidenced.
