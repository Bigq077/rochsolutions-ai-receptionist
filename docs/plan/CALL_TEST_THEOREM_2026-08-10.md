# Call Test Sheet — Theorem, 2026-08-10

Scope: verify the ten fixes deployed to `theorem-onboarding` today. Fixes 1–6
all came out of a single call, **CA166de2a9**, in which Susie offered a caller
three appointment times that existed on no calendar, tried to book them four
times, texted Mark four times, and was rescued only because the caller himself
suggested a different day. Fixes 7–10 came out of running this sheet.

**Every caller turn below tests something.** The "Probes" column names the
commit the turn exists to verify. A turn with no probe was cut.

| Branch | Head | Role |
|---|---|---|
| `origin/theorem-onboarding` | `4896fe2` | LIVE — Mark, Theorem Health |

### What shipped

| # | Commit | Defect |
|---|---|---|
| 1 | `fbf68da` | "an earlier Wednesday" read as accepting the Wednesday on offer |
| 2 | `2c427c3` | hallucinated-slot backstop disarmed by a cleared cache |
| 3 | `117c56a` | the diary stayed shut after Acuity refused the slot |
| 4 | `3a31154` | he spelled his surname out; the calendar kept the wrong one |
| 5 | `0781138` | a refusal to look at the diary was spoken as a fact about it |
| 6 | `a844e14` | Mark told four times to chase a caller who got booked |
| 7 | `6759ad5` | she asked which clinic four times and the caller hung up |
| 8 | `2cc9cf1` | ten seconds of silence after the clinic ack |
| 9 | `1dc1037` | "August 19th" not read as a date — and its month replaced |
| 10 | `4896fe2` | a day missing from the payload spoken as "fully booked" |

`3081b4e` is test-only on top of `a844e14` — no runtime change.

Fix 7 was found **after** this sheet was first written, from call `CAc8f74ddf`
at 19:24 the same evening. It was **not** caused by fixes 1–6 — the only
`connection.py` change in that deploy is in the caller-*name* function. It has
its own call, §2a, and you will meet it in §2 regardless because the trigger is
naming a time in your opening sentence.

Fixes 8–10 came out of **Call 4** on the first run of this sheet, and neither
was caused by fixes 1–7. Both are opened by the same caller habit — naming a
clinic and a date in one breath — so §2a and §4a share a trigger.

> **What the first run of Call 4 did *not* prove.** There was no
> `book_appointment` anywhere in that log: the collision never reached Acuity,
> because the 3pm was never offered back. Probes `117c56a` and `a844e14` —
> "a failed write releases the blocks" and "one alert per failure" — are
> therefore **still unproven**. Call 4 has to be run again and has to reach a
> real refused write.

---

## 0. Pre-flight — do these BEFORE dialling

Three of these will make the whole suite lie.

| # | Check | How | Pass |
|---|---|---|---|
| **P0** | Deployed build matches head | Render log, at call cleanup: `[build_info] running build <sha>`. `/health` returns a hardcoded `1.0.0` and is **not** deploy proof | `4896fe2` |
| **P1** | `EVAL_STAFF_SMS_TO` = your mobile | Render, Theorem service | **Set it.** Theorem has `owner_alerts` ON for `manual_followup`, `booking`, `cancellation`, `reschedule` on **Mark's real number** (`+447870166861`). Call 4 deliberately forces a failed booking. Without this you will text Mark a false "chase this patient" alert |
| **P2** | Acuity is reachable | Any successful booking in Call 1 | Calls 2–4 are meaningless if writes are failing for an unrelated reason |
| **P3** | Note the clock | — | Bookings are **real**. Every one made below must be cancelled in §5 |

> **Dial:** `+44 7380 841468` (theorem_v3 patient line)
> **Bookable location:** Awlstuh. Redditch is deliberately not bookable — it
> redirects. Do not use Redditch in any call below; it tests a different fix.

---

## 1. Call 1 — a plain booking still works

**This is the most important call on the sheet.** Fix #2 made slot resolution
*fail-closed*: once the diary has been read in a call, an appointment time
matching nothing on record is now refused instead of sent to Acuity. If that
went wrong, the symptom is not a wrong booking — it is a **real** booking being
refused, and it would hit every caller.

Run this first. If it fails, stop and revert; nothing else matters.

| # | You say | Expected | Probes |
|---|---|---|---|
| 1.1 | "Hi, I'd like to book a physio appointment please" | Asks your name, or day/time | — |
| 1.2 | Give a full name — "Quentin Roch" | Reads back the **first name only** | — |
| 1.3 | "Sometime next week if possible" | Offers real slots, a specific day and times | — |
| 1.4 | Accept one — "yeah, the first one's good" | Confirms; asks for your number | — |
| 1.5 | Give your mobile | Reads back the full summary: name, day, date, time | — |
| 1.6 | "Yes please, go ahead" | **"All booked"** — a real Acuity appointment | `2c427c3` |

**PASS:** the booking lands in Acuity and you get a confirmation SMS.
**FAIL:** she refuses, stalls, or says she can't book that time. That is fix #2
over-firing → revert immediately (see §6).

**Log check:** `event created` should appear, and there must be **no**
`_resolve_slot_iso: ISO ... not resolvable` line.

---

## 2. Call 2 — "can you do an earlier Wednesday?"

The headline fix. On CA166de2a9 the tool offered Wednesday the 19th, the caller
asked for an earlier Wednesday, and because both days are called "Wednesday" the
system read him as *accepting the day already on offer*. No lookup ran. Susie
answered from the 19th's times, re-badged onto the 12th: "Wednesday the 12th —
two, three and four in the afternoon." Those existed nowhere.

| # | You say | Expected | Probes |
|---|---|---|---|
| 2.1 | "Hi, do you have anything on a Wednesday afternoon?" | Offers a Wednesday with specific times. **Note which Wednesday** | — |
| 2.2 | "Are you free earlier — on a Wednesday?" | **She checks the diary again.** Then either offers real times on the *earlier* Wednesday, or says there is nothing on it | `fbf68da` |
| 2.3 | If she offered times: "what's the date for that one?" | A date on the **earlier** Wednesday, not the one from 2.1 | `fbf68da` |
| 2.4 | Take one and complete the booking | Books, or refuses cleanly | `2c427c3` |

**PASS:** a lookup runs at 2.2 and any times she reads are for the earlier
Wednesday.

**FAIL (the original bug):** she answers 2.2 instantly with the *same* times
from 2.1, attached to a different date. If she does, do **not** let it book —
say "actually, leave it, thanks" and hang up.

**FAIL (fix #5):** she says anything like *"it looks like Wednesday afternoon
has filled up"* or *"that slot doesn't seem to be available any more"* without
having checked. Those two sentences are verbatim from the bad call and are what
`0781138` exists to stop.

**Log check — this is where the proof is:**
- a `check_availability` call **after** turn 2.2 (not just before 2.1)
- `_caller_requests_different_day → True`
- absence of `check_availability BLOCKED`

> If the earlier Wednesday genuinely has no free afternoon, that is still a
> **pass** — provided she looked before saying so. The fix is "consult the
> diary", not "find a slot".

---

## 2a. Call 2a — the clinic question, asked once, then no silence

Call `CAc8f74ddf`, 19:24 the same evening. He opened with a Wednesday
afternoon, was asked which clinic, answered — and was asked **four times**. The
resolver had him right on the first answer and said "Awlstuh."; the answer just
never reached the model, so it re-asked what it had already been told. He hung
up on turn 12 having said *"i said the osteo clinic"*.

The trigger is naming a time in your **opening sentence** — that skips the
day/time question, and the branch that skipped it recorded nothing.

**Now also covers fix 8.** Fix 7 stopped the re-ask but left a hole directly
behind it. That clinic-ack turn is ack-only *by design* — it answers and runs
no model turn — so the time preference it re-queues **is** the rest of the turn.
On Call 4 it was dropped 2ms after being queued, the turn was one word long
("Awlstuh."), and the line went dead for ten seconds until the watchdog said
*"Sorry, I didn't catch that…"*. So the two fixes are two halves of the same
turn, and **turn 2a.2 is now a stopwatch test**.

| # | You say | Expected | Probes |
|---|---|---|---|
| 2a.1 | "Hi, have you got anything Thursday morning?" — a time, in the first sentence | "Which clinic were you thinking of — Awlstuh or Redditch?" | — |
| 2a.2 | Answer normally: "the Awlstuh one" | Acks the clinic, then **goes straight to availability**. **Count the seconds.** | `6759ad5` `2cc9cf1` |
| 2a.3 | — | She does **not** ask which clinic again | `6759ad5` |

**PASS:** the clinic question is asked **once**, and Thursday-morning times
follow the ack **without a pause of more than about three seconds**.

**FAIL (fix 7):** any second "which clinic". If it repeats, hang up — that is
the loop, and it will keep going.

**FAIL (fix 8):** the ack is a bare "Awlstuh." followed by silence, then
*"Sorry, I didn't catch that…"*. Note that this failure **self-rescues** — the
watchdog fires and she then does go to availability — so it is easy to score as
a pass. It is not one. Ten seconds of dead air on the most common booking
opening is the defect.

**Log check — fix 8:**
- `time_pref already known (…) — timing Q skipped, re-queued pref`
- and then **no** `same-breath straggler dropped` for that same text
- and **no** `WATCHDOG_FIRE` in the ten seconds after the ack

**Worth a second run:** mumble the clinic name, or say something STT will
mangle. That takes the *defaulting* path, which is the one the live call hit —
you should hear "Awlstuh." and then availability, still with no re-ask.

> Note what you hear at 2a.2 when you mumble. A bare **"Awlstuh."** with no
> offer to correct it is current, deliberate behaviour, not a bug — but it is
> an open question (see §7), and your ear on it is the evidence needed.

---

## 3. Call 3 — the surname he spelled out

STT heard "jack told me to call" and wrote **Jack Told** to the calendar and to
the patient's confirmation text. He corrected it, spelled it, and it never took:
the surname already had a space in it, so it looked complete and the correction
was dropped at the first line of the check.

Say turn 3.2 exactly as written — the wording is what produced the wrong capture.

| # | You say | Expected | Probes |
|---|---|---|---|
| 3.1 | "Hello, I'd like to book an appointment" | Asks your name | — |
| 3.2 | **"It's Jack — Jack told me to call about my knee"** | Reads back "Jack". May have silently stored "Jack Told" | — |
| 3.3 | Continue: pick a day and a time | Normal slot flow | — |
| 3.4 | **"Sorry — my surname is Thompson. T-H-O-M-P-S-O-N."** | Accepts it without fuss | `3a31154` |
| 3.5 | Give your number, let her read the summary back | Summary says **"Jack Thompson"** | `3a31154` |
| 3.6 | "Yes, go ahead" | Books | — |

**PASS — all three must agree:**
1. the spoken summary says **Jack Thompson**
2. the **Acuity appointment** says Jack Thompson
3. the **confirmation SMS** says Jack Thompson

**FAIL:** any of the three says "Jack Told", or she reverts to it after 3.4.
Reverting-after-two-turns is the exact live signature: the model used the right
name from its own memory, then the forced readback re-injected the stored one.

> Also worth watching: she should not ask what the appointment is for. On
> Theorem the reason question is deliberately switched off — asking is correct
> on JV and Vital Edge, not here.

---

## 4. Call 4 — a booking Acuity will refuse

Forces the failure path on purpose. On CA166de2a9, after Acuity rejected the
slot, seven further diary checks were **blocked** — each one telling the model
"do NOT ask for the day or time again" and pointing it back at the slot that had
just been refused. Three more doomed writes, four alerts to Mark, four minutes.

**Method:** book a slot in Call 1, then call back and ask for **the same slot**.
Acuity rejects the second write with *"is not an available time slot"*.

> ⚠️ Confirm P1 first. This call is designed to trigger owner alerts.

| # | You say | Expected | Probes |
|---|---|---|---|
| 4.1 | "Hi, I'd like to book an appointment" | Asks your name | — |
| 4.2 | Give a **different** name from Call 1 | Reads back first name | — |
| 4.3 | Ask for **the exact slot you booked in Call 1** | She may offer it or say it's gone | — |
| 4.4 | If she offers it, accept and confirm | The write fails behind the scenes | — |
| 4.5 | "Okay — what else have you got that day?" | **She checks the diary again** and offers alternatives | `117c56a` |
| 4.6 | Take an alternative | Books successfully | `117c56a` |

**PASS:** after the failure she is able to look at the diary again and recover
the call within one or two turns.

**FAIL (the original bug):** she loops — re-reads the same dead slot, re-asks
you to confirm it, or repeats the summary without ever checking availability.
If she does this more than twice, hang up; that is the four-minute loop.

**PASS — SMS, and count them:** you (via `EVAL_STAFF_SMS_TO`) should get
**exactly one** "Booking needs manual entry" alert for this call, even if the
write was attempted more than once.

**FAIL:** two or more identical alerts. That is `a844e14` not holding.

**Log check:** `BOOKING_WRITE_FAILED` set; `check_availability BLOCKED` should
**not** appear after the failure.

---

## 4a. Call 4a — the day she said was full

**The most valuable call on this sheet after Call 1.** On Call 4 Susie said
*"Wednesday the 19th of August is fully booked, I'm afraid"*. It was not. The
same log, eight lines earlier: `2026-08-19 — 6 raw slot(s)`. Only the 3pm
booked on Call 1 was gone; 2pm and 4pm were free. She then offered a different
week.

Cause: the hint was `'August 19th at 3 pm'` and the date filter was **bypassed**
— its date-matcher only recognised `19th August`, not `August 19th`. The tool
swept 30 days, the spoken list was capped to the soonest three, and the 19th was
simply absent. Absence was spoken as clinic state.

Underneath that sat a worse one, invisible on the night only because August was
the current month: the parser never read the month word at all. `September 19th`
resolved to **19 August**; `December 1st` to **1 September**.

Say these **month-first** — "August the nineteenth", not "the nineteenth of
August". Day-first always worked; month-first is the fixed path.

| # | You say | Expected | Probes |
|---|---|---|---|
| 4a.1 | "Hi — have you got anything on August the nineteenth?" | She talks about **the 19th of August specifically** — either real times on it, or that it has nothing left | `1dc1037` |
| 4a.2 | "What about August the nineteenth at three?" | Same day, a time-of-day answer for it. She must not silently switch weeks | `1dc1037` |
| 4a.3 | "Could you do September the sixteenth instead?" | She says **September**. Most likely "I haven't got anything on the 16th of September — the next I have is …" (it is past the 30-day window) | `1dc1037` |
| 4a.4 | "Leave it for now, thanks" | Closes politely | — |

**PASS 4a.1/4a.2:** everything she says is about the 19th of August. If the day
genuinely has nothing left, saying so is a **pass** — the fix is "look at the
day you were asked about", not "find a slot".

**FAIL:** she answers with the soonest few days and never mentions the 19th, or
calls it full while offering another week.

**PASS 4a.3 — this is the sharp one:** she must say **September**. Under the old
code that phrase resolved to 16 *August* and she would have answered about
August without ever noticing.

**FAIL 4a.3:** any answer that talks about August dates as though they were what
you asked for.

**Log check:**
- **no** `week filter bypassed — no week anchor in date_hint`
- `week filter applied: 2026-08-19 to 2026-08-19`
- at 4a.3, `2026-09-16` — not `2026-08-16`

---

## 4b. Call 4b — "fully booked" about a day she never looked at

The other half of 4a. When your wording genuinely does *not* name a day
("evenings", "as soon as you can"), the sweep still returns only the soonest
three days — that is deliberate, so the caller is not read a wall of dates. What
was wrong is that the payload gave the model no way to tell a **trimmed** list
from a **complete** one, so a missing day looked like a full day.

The result now carries `search_narrowed_to` (null when no specific day was
searched) plus how many days were found and withheld, and the prompt forbids
calling a day full unless the tool actually looked at it.

| # | You say | Expected | Probes |
|---|---|---|---|
| 4b.1 | "Hi, what have you got coming up?" — deliberately vague | Offers about three days. **Write them down** | — |
| 4b.2 | Name a **weekday one to two weeks later** than anything she just offered — "how about the Wednesday after that?" | She **re-checks** and answers about that day | `4896fe2` |
| 4b.3 | "Is that day full then?" (only if she said it had nothing) | She may confirm it is empty — but only having looked | `4896fe2` |
| 4b.4 | "Okay, thanks — I'll ring back" | Closes politely | — |

**PASS:** at 4b.2 a fresh diary check runs, and any claim she makes about that
day comes after it.

**FAIL:** she calls the day full, or says she has nothing then, **without** a
lookup between your question and her answer. That is the bug — she is reporting
the shape of her last payload as the state of the clinic.

**Log check:**
- a `check_availability` call **after** turn 4b.2
- before it, `week filter bypassed — no week anchor in date_hint` on the 4b.1
  sweep. That line is the state in which the old code guessed

> The new payload fields (`days_found_in_window`, `days_not_shown`) are **not
> logged** — they go to the model, not to Render. So the Render log can show you
> *that* the sweep was unnarrowed, but not how many days were withheld. If you
> want the counts, they are in the obs turn record for the call, not the log.
> Worth adding a log line for them if §4b turns out to need re-running.

---

## 5. Cleanup — do not skip

1. **Cancel every appointment** made in Calls 1–4b, in Acuity directly. 4a and
   4b are written to end without booking, but if a slot got taken, it is real.
2. Check the calendar for entries under **"Jack Told"** — if one exists, Call 3
   failed and the fix needs another look.
3. Cancelling may itself text Mark. Leave `EVAL_STAFF_SMS_TO` set until you are
   finished, then unset it.

---

## 6. Rollback

If Call 1 fails, or any call produces a booking the caller did not agree to:

```bash
git push origin 6759ad5:theorem-onboarding --force-with-lease
```

That drops fixes 8–10 only, returning Theorem to the build this sheet was first
run against. Prefer it: fixes 1–7 are call-proven, 8–10 are not yet. Confirm via
`[build_info] running build 6759ad5` on the next call.

To go all the way back to this morning, before any of it:

```bash
git push origin 117c56a:theorem-onboarding --force-with-lease
```

> Reverting to `6759ad5` restores the ten-second silence in §2a and the
> "fully booked" claim in §4a. Both are caller-visible. If you revert for an
> unrelated reason, expect those back.

---

## 7. Known-open — not fixed, do not raise as new

**The bare clinic ack.** When Susie has to guess your clinic she says just
**"Awlstuh."** — she does not offer you a way to correct it. The correction
phrase exists but is inert here: it only names *bookable* alternatives, and
Redditch is `bookable=False`, so there is nothing for it to offer.

That is a pinned owner decision from 2026-08-06, whose argument is that with
only one bookable site there is no wrong-clinic outcome to protect against.
Worth re-opening, because there is one: a garbled **Redditch** caller is now
silently booked at Awlstuh instead of being redirected to Mark, and never
learns Redditch was an option. Your call — flagged, not changed.

**The DTMF keypad ladder still has the loop.** Fix 7 covers the three verbal
paths. The fourth copy lives in `_handle_dtmf`, where your "answer" is a
keypress rather than speech, so the fix's shape genuinely differs and there is
no reproduced call for it yet. If you reach the keypad ladder during this suite
and the clinic question repeats afterwards, that is this — note it and move on.

> Fix 8 *did* reach that ladder, but only its dead-air half: the keypad's
> time-preference re-queue had the same 2-tuple shape and is now flagged too.
> The **re-ask loop** there is untouched. So if you press a key and then get
> silence, that is a fix-8 regression worth reporting; if you press a key and
> get asked the clinic again, that is this known-open.

**Fix 10 is a signal plus a prompt rule, not a hard block.** Fixes 2 and 5 stop
a bad slot mechanically. Fix 10 gives the model the facts (`search_narrowed_to`,
`days_not_shown`) and tells it not to guess — so a single odd sentence in §4b is
a prompt-adherence miss, not a broken guard. Report it with the wording verbatim
rather than as a fix failure; the wording is what a stronger rule would be built
from.



Observe and record only.

**The watchdog can talk over a speaking caller.** At 15:02:26 on CA166de2a9 it
fired `"Sorry, I didn't catch that"` across the caller with `voice_gap=0.0s` —
i.e. while he was audibly mid-sentence — and he then said "yes please" into it.

It is left alone deliberately. The probe's own docstring says voice_gap is for
logging and must never drive the decision, after a July incident where a tuned
threshold produced a false apology; and that fire was the *designed* recovery
for an utterance STT dropped, which exists so a caller is never left in
permanent silence. Trading a spoken-over caller for dead air is the wrong
direction, and the fix needs a live call to tune rather than a green suite.

If you hear it during this suite, note **the timestamp and what you were
saying** — that is the data the fix needs.

---

## 8. One-line results

| Call | Probes | Pass? | Notes |
|---|---|---|---|
| 1 — plain booking | `2c427c3` | | |
| 2 — earlier Wednesday | `fbf68da` `0781138` | | |
| 2a — clinic asked once | `6759ad5` | | |
| 2a — …and no silence after the ack | `2cc9cf1` | | seconds counted: ____ |
| 3 — spelled surname | `3a31154` | | |
| 4 — refused booking | `117c56a` `a844e14` | | **unproven on run 1** — must reach a real refused write |
| 4a — "August the nineteenth" honoured | `1dc1037` | | |
| 4a.3 — she says *September* | `1dc1037` | | the sharp one |
| 4b — no "full" without a lookup | `4896fe2` | | |
| Watchdog heard? | — | | timestamp + what you were saying |
| Bare "Awlstuh." heard? | — | | §7 — evidence for the open question |

### Run log

| Run | Build | Date | Outcome |
|---|---|---|---|
| 1 | `6759ad5` | 2026-08-10 eve | Calls 1–3 + 2a run. Call 4 reached no write, so `117c56a`/`a844e14` unproven. Produced fixes 8, 9, 10 |
| 2 | `4896fe2` | | |
