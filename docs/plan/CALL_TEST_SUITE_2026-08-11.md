# Call test suite — 2026-08-11 deploy

Validates today's fleet-wide port work. **Theorem first** — it is the only live
branch with real patient traffic.

Passing the Theorem block licenses this claim, and no more than it:

> *The fixes deployed on 2026-08-11 work, and none of them broke the paths a
> caller actually walks.*

It does **not** license "no new bugs anywhere" — see *What this cannot prove*.

---

## Pre-flight (2 min, do not skip)

| # | Check | Expected |
|---|---|---|
| 0.1 | Make any call, hang up, read the log tail | `[build_info] running build 74af903` |
| 0.2 | Same log | `[ms_conn] clinic_id resolved: theorem_v3` |

`/health` reports `1.0.0` on every build — it is **not** deploy proof. If 0.1
shows an older sha, Render has not finished; stop and wait.

---

## What actually changed on Theorem today

Eleven files, but most is inert on this clinic. Ranked by call risk:

| Change | Risk | Why |
|---|---|---|
| Reason requirement now **config-keyed** (`74af903`) | **HIGH** | If the predicate misreads, `book_appointment` refuses **every** booking |
| Gate 5b-r now runs behind a **config gate** (`2f95c75`) | **HIGH** | Was unconditional. If the gate flips, Susie starts asking what brings you in |
| `build_sms` identity + `sms_phone` fallback | MEDIUM | Changes the confirmation SMS text |
| `_find_service_def` isinstance guard (P7) | LOW | Closes a latent crash; Theorem's `services` are strings |
| Tenancy / Google tokens | NONE | Theorem books via **Acuity**; never reads Google tokens |
| `availability_mode`, cancel-restore gating | NONE | Both gate on Google-Calendar-only config |
| Sheets log wording, obs | NONE | Log text only |
| Theorem's own `clinic.json` | NONE | **Untouched today.** Mark's Friday was already live |

---

## Theorem calls

Run T1–T4 in order. T5–T6 are regression checks on subsystems today's work
touched indirectly.

### T1 — Book, never saying why  ★ highest risk

The A2 gate used to refuse any booking with no reason. It is now conditional.
If that predicate is wrong, this call cannot complete.

**Say:** "Hi, I'd like to book an appointment please." Then answer only what
you are asked — day, time, name, number. **Never say what it is for**, and if
pressed for a reason, deflect: *"I'd rather explain when I come in."*

- ✅ **PASS** — booking completes; Susie confirms a specific day/time; the
  appointment exists in Acuity at that time.
- ❌ **FAIL** — she stalls, re-asks, or says she cannot book yet.
- **Log must NOT contain:** `[book] BLOCKED — no reason on record (A2)`
- **Post-call:** confirm the Acuity event exists, and that its time matches
  what she said out loud.

### T2 — Provoke the reason question  ★ highest risk

Gate 5b-r is the owner rule *"Susie never asks what brings you in."* It now
runs behind a config gate that did not exist yesterday.

**Say:** "I need to see someone." Stay vague. If asked anything open, answer
"just need an appointment."

- ✅ **PASS** — she **never** asks what brings you in, what it is for, what is
  going on, or any variant. She moves to the next booking step.
- ❌ **FAIL** — any form of the reason question is **spoken aloud**.
- ❌ **FAIL** — a dangling half-sentence then silence (e.g. *"Just so we've got
  a reason on the booking."* followed by nothing).
- **Log:** `[ms_gate5] reason question removed` appearing is **correct** — it
  means the gate fired. The failure is the question being *heard*.

### T3 — Volunteer a condition unprompted

The other half: a reason offered freely must still be captured, once.

**Say:** "I've done my back in — can I get in this week?"

- ✅ **PASS** — she books without ever asking for a reason, and does not ask
  you to repeat what is wrong.
- ❌ **FAIL** — she asks a reason question anyway (that is the double-ask).

### T4 — Confirmation SMS content

`build_sms` and the `sms_phone` fallback both changed today.

**Do:** complete any booking above and read the text.

- ✅ **PASS** — names **Theorem Health and Wellness**, and "call us on" shows
  **07380 841468** (the line the text came from).
- ❌ **FAIL** — "at **the clinic**", or "call us on ." with nothing after —
  that is the exact bug this fixed.
- ⚠️ **Note** — 07870 166861 is the team's direct number and is what Susie
  quotes *on a call*. In the **SMS** it should be 07380 841468.
- If no SMS arrives at all, check `SMS_ENABLED=true` on the service before
  calling it a regression.

### T5 — Location ladder (regression)

Not changed today, but it is Theorem's largest subsystem and worth one pass.

**Say:** book an appointment; when asked which clinic, say **"Redditch"**.

- ✅ **PASS** — handled as before; the clinic question is asked **once**.
- ❌ **FAIL** — asked which clinic more than twice, or dead air after your answer.

### T6 — Availability for a named day (regression)

`_find_service_def` sits on this path and gained a type guard today.

**Say:** "What have you got on Friday?"

- ✅ **PASS** — real slots offered for a day that has them.
- ❌ **FAIL** — "fully booked" for a day with free slots, or a stall.
- **Log must NOT contain:** `AttributeError`

### T7 — Cancel (optional)

**Say:** call back and cancel the appointment from T1.

- ✅ **PASS** — cancelled, gone from Acuity, cancellation SMS correct per T4.

---

## Joint Venture (`d1f6be7`) — 2 calls

JV had the most fixes and is pre-live, so lighter coverage is acceptable.

**J1 — Book end to end.** This is the call that matters: the reason fix
unblocked `book_appointment`, which had been refusing **every** JV booking.
✅ PASS: a booking completes and the event lands in the diary at the right time.
**Check the calendar, not the read-back** — a wrong write survives every
spoken confirmation.

**J2 — Ask for a day the practitioner has blocked out.** If any day is blocked
as an **all-day** event, it must **not** be offered. This is the fix that stops
Susie booking patients into time off.

---

## Vital Edge (`48096a8`) — 1 call

**V1 — Book, and confirm the reason question is still asked** in VE's own
wording (*"is there a particular area or reason for the massage…"*). VE is the
clinic that deliberately **does** ask; today's work must not have suppressed it.

---

## What this cannot prove

Be precise about the boundary — a green suite is not a clean bill of health.

1. **The all-day-events fix (JV/canonical)** only fires when a day is blocked as
   an all-day event. J2 covers it *only* if such an event exists in the diary.
   Create one first, or the call proves nothing.
2. **The `_find_service_def` guard (P7)** closes a crash that no current caller
   can reach. T6 cannot trigger it; it is insurance against the next caller.
3. **The withheld-caller reschedule fix** needs a call from a **withheld
   number**. Not in this suite — add `141` before dialling if you want it.
4. **Call quality** — duplicate `check_availability`, dead air on availability
   turns, and barge-in cutting Susie off are all still open and need an audio
   harness. Do not read a smooth call as evidence they are fixed.
5. **Sheets** — nothing writes call records until `SHEETS_ENABLED=true` is set
   per service. Every call in this suite will silently produce no Sheets row
   until then, and that is expected, not a new failure.

---

## Result

| Call | Pass | Notes |
|---|---|---|
| 0.1 build sha | ☐ | |
| T1 book without reason | ☐ | |
| T2 reason never asked | ☐ | |
| T3 volunteered reason | ☐ | |
| T4 SMS content | ☐ | |
| T5 location ladder | ☐ | |
| T6 availability | ☐ | |
| T7 cancel | ☐ | |
| J1 JV booking | ☐ | |
| J2 JV all-day | ☐ | |
| V1 VE reason asked | ☐ | |

**T1, T2 and T4 are the ones that matter.** If those three pass, today's
Theorem deploy is sound. If T1 or T2 fails, revert Theorem to `e02dd3d` and
send me the log.
