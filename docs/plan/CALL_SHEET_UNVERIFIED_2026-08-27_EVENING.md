# Call sheet — fixes deployed 27 Aug 2026 and NOT yet live-verified

Five fixes went out today that no real call has exercised. B-105 and B-106 are
already live-verified and are **not** on this sheet.

## Lines

| Clinic | Number | Notes |
|---|---|---|
| JV — demo | **+447366263180** | Sheets / EVAL_STAFF warnings are known-accepted here |
| JV — LIVE patient line | **+447367002651** | real patients; warnings here are NOT accepted |
| Theorem — LIVE patient line | **+447380841468** | Mark's line |
| Vital Edge | **+447426779875** | Jonathan; every booking is PENDING CONFIRMATION |

## Pre-flight — confirm what is actually running

`/health` returns a hardcoded `1.0.0` and proves nothing. The only deploy proof
is the Render log at call cleanup:

```
[build_info] running build <sha>
```

Expect: JV `36ad95e7`, Theorem `1ec52e26`, Vital Edge `4fc23481`,
latency-eval `ab0b3638`. **If the SHA is older, the call tested nothing** —
Render may still be building. Check before reading anything else.

## Housekeeping

- Cancel every test booking **through Susie**, not in the calendar. Reminders
  (24h/2h) are wired on all three live clinics and a calendar-side delete leaves
  them queued.
- Vital Edge has an 18+ gate. Do not present as under 18 unless testing that.

---

## Call 1 — B-107: the ack that claimed you spoke

**Line:** JV demo (+447366263180). **Fix:** all four branches.

The defect: a barge-in fires, the transcript comes back EMPTY, and Susie says
"Yes, go on." to a caller who said nothing. On CAf6a63145 the caller replied
*"oh i didn't say anything"* — word for word the CAfcb3130c complaint.

**Script**

1. "Hi, I'd like to book a physio assessment."
2. Answer the reason question normally ("my left ankle, nothing serious").
3. Answer the screening question ("no, nothing like that").
4. Give a day preference and let her start reading options or your number back.
5. **While she is mid-sentence, make a NON-WORD noise** — cough, tap the
   handset, a short "mm". Do not say a real word. The point is a barge-in that
   carries no transcript.

**PASS** — she re-asks the outstanding question. If she was reading your number
back, you hear "…is that the best number for the booking?" again.

**FAIL** — any of "Yes, go on.", "Sorry — go ahead.", "Sorry about that — you
were saying?" when you said nothing.

### 1b — the B-67 guard (do not skip)

Barge in the same way during a **filler** — the "Right with you…" / "One
moment…" while she is calling the calendar.

**PASS** — she still acks ("Yes, go on.") and then answers.
**FAIL** — she replays the filler and you are left waiting for an answer that
never comes. That is B-67 re-broken, and it is the specific thing the fix was
shaped to avoid.

---

## Call 2 — B-86 on Vital Edge: a day nobody looked at

**Line:** Vital Edge (+447426779875). **Fix:** `6c8eac49`.

Until today VE's diary reader had no widen at all. B-105 made the model send a
named day as `after_date` + `day_window: 1`, so a named weekday outside that
single day came back as "nothing free in that window" — about a day nobody
looked at.

**Script**

1. "Hi, I'd like to book a massage."
2. When asked about timing: **"Have you got anything on Wednesday?"**
   Use a weekday **4–7 days out**, not tomorrow — the whole point is a day
   outside a one-day window. On 27 Aug that is Wed 2 Sep or Thu 3 Sep.
3. Pick a weekday Jonathan actually has free, so a genuine empty is not mistaken
   for the defect.

**PASS** — she offers times on that weekday.

**PASS (also correct)** — "I can't see anything on that day in the next couple
of weeks" plus real alternatives. That is the honest answer when the day really
is empty.

**FAIL** — "that day is unavailable" / "fully booked" / "there's nothing free in
that window", for a day that has space. Any sentence asserting the clinic is
shut that day is a fail: nothing beyond the window was checked.

---

## Call 3 — B-86 on Theorem: same defect, Acuity path

**Line:** Theorem live (+447380841468). **Fix:** `1ec52e26`.

This path scanned 30 days by default and was safe — until `day_window: 1`
started replacing that scan.

**Script**

1. "Hi, I'd like to book an appointment."
2. Timing: **"Do you have anything on Tuesday?"** — again a weekday several days
   out.
3. **Avoid Monday 31 August** — UK summer bank holiday, and Theorem has a
   bank-holiday filter, so an empty answer there would be correct and would tell
   you nothing.

**PASS / FAIL** — as Call 2.

### 3b — the guarded sentence

After she offers times on a single date, ask **"is that all you have?"**

**PASS** — she may say that is all there is *on that date*, and offers to look
at the following one.

**FAIL** — "that's all we have on Tuesdays", or any claim about the WEEKDAY. She
read one date; every later Tuesday is unknown to her. This is the pre-existing
false claim the fix guarded, and it became easy to reach only once the widen
started supplying other days.

---

## Call 4 — the SMS latch: a text recorded as sent

**Lines:** JV live (+447367002651) **and** Vital Edge. **Fix:** all four.

`session["confirmation_sms_sent"]` stands the end-of-call follow-up router down.
Both Google-Calendar executors set it unconditionally, so a text that was
suppressed, failed, or never attempted still reported "sent" — and the caller
got no confirmation AND no follow-up.

**SMS_ENABLED is off by default on the live branches**, so on most of these the
correct outcome is *no confirmation text*. That is expected. What changed is
that the system now knows it.

**The primary check is the Render log, not your handset.**

**Script**

1. Book an appointment through to confirmation.
2. Ring back and **reschedule it** to another time.
3. Then ring back and **cancel it** (this also exercises the second site,
   `_exec_cancel_appointment`, which the audit had missed).

**PASS** — the log reads:

```
Reschedule confirmation NOT sent (suppressed or failed) to ***1207
```

with the number masked, and **no** "confirmation already sent during call,
skipping" afterwards.

**FAIL** — "Reschedule confirmation sent to …" while SMS is off, or the
follow-up router standing down over a text that never went out. The 23 Aug
Theorem log `CAc9b44a5e` is the shape to look for: "SMS_ENABLED is off —
outbound SMS suppressed", then "Cancellation confirmation sent to …" one
millisecond later.

If SMS is ON for the clinic you are testing, the handset check is simply: a text
arrives, or the log says it did not. The two must agree — that is the fix.

---

## Call 5 — OPTIONAL probe: finding 3 (day-pick under-offering)

**Line:** JV demo. **Nothing was changed for this** — it is open-but-quiet, and
this call is to find out whether it is reachable at all.

Across 733 stored calls it never fired. The mechanism is real: `multi_day`
speaks ONE time per day, and a day-pick only escapes it because B-105 collapses
the payload to a single day.

**Script**

1. Ask something vague: **"what have you got next week?"** — this forces
   `multi_day`, and you should hear several days, one time each.
2. Then pick a DAY out of that list: **"what about the Tuesday?"** — pick one you
   have reason to think holds 2+ slots.

**Quiet (expected)** — she reads out several times for that day, and adds "a few
others that day if none of those suit" when there are more.

**REPRODUCED** — she offers exactly ONE time for that day while others exist. If
this happens, capture the call SID: it is the evidence needed to justify forcing
`single_day` whenever `search_narrowed_to` is a single date.

---

## Not on this sheet, deliberately

**Theorem `minimum_age_years` dedupe (`a0256351`)** needs no call. Two
declarations of the same key in one dict, both `7`; the later silently won.
Verified by reading config back — `theorem`, `theorem_v2` and `theorem_v3` all
still resolve to 7, and the full suite moved by exactly nothing (102/6142 on
both sides). There is no caller-visible behaviour to test.
