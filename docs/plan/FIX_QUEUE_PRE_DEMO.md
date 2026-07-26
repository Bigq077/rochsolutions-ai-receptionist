# Pre-demo fix queue — 2026-07-26 → Hands On Money demo (~29 July)

Three days. Ranked by one question only: **does it break the demo booking call?**

The demo is a person phoning up and booking an appointment while ~100 clinics
watch. Everything below is ordered by how directly it damages that call, not by
how interesting the defect is.

Sources: Jules's 2026-07-25 sweep (`SUSIE_SWEEP_2026-07-25_FINDINGS.md`), the 25
captured obs rows, and the verification call `CA4969580082db5e757c3b1d04dd38e7ae`
(2026-07-26), whose transcript is quoted throughout — it is the cleanest example
we have of a *successful* booking and it still contains four defects in sixteen
turns.

---

## Reference: the verification call, annotated

Booking succeeded. `reason='booked'`, `calendar_event_id` set. And yet:

```
[ 4] ASSISTANT | …Number 1, Monday 27th July — half past four in the afternoon or
                 quarter past five in the evening. Number 2, Tuesday 28th July —
                 five in the evening or quarter past seven in the evening.
                 Number 3, Wednesday 29th July — quarter past six in the evening
                 or seven in the evening. Any of those suit you?     ← B1 (six options)
[ 5] USER      | in the evening
[ 6] ASSISTANT | I should clarify what you're after first — what's the
                 appointment for…                                    ← A2 (reason asked AFTER slots)
[ 7] USER      | i said the 29th 7 in the evening                    ← reason never answered
[ 8] ASSISTANT | …Could I take your first name and surname?
[ 9] USER      | rock
[10] ASSISTANT | And your first name?
[11] USER      | i said that would be quentin                        ← A3 (name friction)
[12] ASSISTANT | Is the number you're calling on the best one for your booking?
                 If so, just say use this number.                    ← A1 (asks for what it has)
[13] USER      | rock
[14] ASSISTANT | Thanks — I already have your number confirmed.      ← A1 (contradicts turn 12)
[16] ASSISTANT | I've just sent you a confirmation text.             ← A4 (no text was sent)
```

Two separate `"i said"` from the caller in one booking. That is the sound of a
demo going wrong.

---

## Block A · The collection sequence — **the single highest-value work**

All four defects live in one stretch of conversation: slot → reason → name →
phone → confirm. Fix the sequence, not four separate bugs.

### A1 · Phone: confirm, never ask · `BLOCKER`
**Observed:** turn 12 asks *"Is the number you're calling on the best one… just
say use this number"*, then turn 14 says *"I already have your number
confirmed."* She had it from caller ID the whole time.

**Two faults.** (a) A turn is spent asking for something already known. (b) The
caller must utter a magic phrase — *"use this number"* — to accept it. Jules had
to say it verbatim in rows 6, 22, 23; where he tried to read a number aloud
instead (rows 17, 19, 21) the call ran 150–261 s and ended in
`"yeah we were in the middle of a fucking booking"`.

**Wanted:** read it back for confirmation, don't request it.
> *"I've got you on 07502 211207 — is that the best number for the booking?"*

A plain yes/no. Only fall back to verbal capture on "no", and prefer keypad
entry there.

### A2 · Reason before slots, and never book without one · `BLOCKER`
**Observed:** slots offered at turn 4, reason asked at turn 6 *after* the caller
had started choosing, never answered, and the booking completed anyway with
`collected.reason = None`. Same on call 25 the night before.

**Why it matters more than it looks.** On a physio line the reason drives
appointment type, duration and price. A booking with no reason means the clinic
receives an appointment it cannot prepare for, and Susie cannot have booked the
right service because she never knew what it was.

**Wanted:** ask the reason *before* checking availability; do not reach
`book_appointment` with `reason` unset.

### A3 · Name in one pass · `WATCH`
**Observed:** "first name and surname" → `"rock"` → "And your first name?" →
`"i said that would be quentin"`.

Related and worse across the sweep: "Tom Green" transcribed as `home green`,
`hung green`, `homegreen` (rows 17, 21, 23, 24). Whether `Rock` here is a
mis-hear of *Roche* is unconfirmed — **ask Quentin what he actually said** before
treating it as STT.

### A4 · Do not claim a text was sent when none was · `BLOCKER`
**Observed:** turn 16, *"I've just sent you a confirmation text."* `SMS_ENABLED`
is off on this service — the 2026-07-25 log shows
`[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)`.

So every caller is currently told they will receive a text that will never
arrive. On the demo call that is a promise made to ~100 watching clinics that
the system does not keep. Either flip SMS on for the demo, or make the closing
line conditional on the send actually happening.

> Related, separate: `confirmation_sms_sent` is overloaded — the provisional
> path sets it to *suppress* a send, so `success` is partly derived from a
> "deliberately didn't send" flag. Wrong for a different reason; not urgent.

---

## Block B · Perceived speed

### B1 · The slot readout is too long · `BLOCKER`
Six options across three days in one breath — measured at **24.1 s** on the
worst turn, 16.1 s on the 2026-07-25 test call, where the caller hung up nine
seconds later.

**Wanted:** offer two, then ask. *"I've got Wednesday at seven, or Tuesday at
five — either of those work?"* This is a content change, far cheaper than a
latency engineering pass, and probably recovers most of the perceived slowness.

### B2 · Turn latency · `WATCH`
`ttfa p50 1923 / p95 3659 / max 10107 ms` against a 1500 ms bar; **76% of turns
over**. Real engineering, not a three-day job. B1 is the part that fits.

---

## Block C · Natural speech

### C1 · Endpointing (C23) · `BLOCKER for confidence, not for the demo`
A ~2 s mid-sentence pause is treated as end-of-turn. Jules ran calls 2–15 in
compressed delivery because of it, which means **every PASS in his sweep carries
an asterisk**. Fixing it is what makes the whole sweep re-measurable.

Judgement call: a rehearsed demo caller can speak in compressed bursts. So this
is the highest-value fix for *knowing where we stand*, and not strictly required
for the demo itself. Sequence it after Block A.

---

## Block D · Clinical safety

Screening is a selling point to a room of clinics, so these are demo-relevant
even though a demo call is unlikely to trigger them.

### D1 · `trauma_fracture` trigger keywords · 10 minutes, config only
Row 8, verbatim: `"i've done my ankle went over on sunday playing football"`.
Neither `went over` nor `done my ankle` is in `trigger_keywords`. Add those plus
`rolled`, `twisted`, `rolled my ankle`. Pure `clinic.json` — the cheapest item
on this page.

### D2 · `calf` → `cough` / `call` · needs a new approach
Confirmed three times (rows 13, 14, 15) **with `calf` already in the keyterms**.
The 24 July keyterm fix did not solve it, so retrying it is not the answer.
Layer 1 cannot arm a DVT screen on a word that never arrives.

Mitigation to consider: treat the known mis-hears as aliases on the trigger side
only — never on the answer-classification or emergency side.

---

## Closed

- ✅ **`booking_confirmed` never set** — `55451e0`. Bookings landed in the
  calendar and every one recorded as `caller_hung_up`; obs held no evidence a
  booking existed for any Google Calendar clinic. Verified live on
  `CA4969580082db5e757c3b1d04dd38e7ae`.
- ✅ **Orphan screen detection** — `2485229`, validated in production
  (`dvt ORPHAN×1, ARMED×0`).
- ✅ **Truncated safety answers** — `188e478`.
- ✅ **Twilio recording duplicate (21220)** — `c7ef0fd`, confirmed gone.
- ✅ **Screening + booking id captured in obs** — `ba195e8`, `55451e0`.

---

## Sequence

| When | Work | Gate |
|---|---|---|
| **Today** | D1 (10 min), then Block A as one change | A booking call with no `"i said"` from the caller, and `collected.reason` populated |
| **Tomorrow** | B1, then C1 | Three consecutive clean bookings in **natural** delivery |
| **Day before** | D2 if C1 landed cleanly; otherwise freeze | Rehearsal only — no new code |

**Freeze rule:** nothing merges on demo day. The last code change lands the day
before, with a rehearsal after it.

## What not to do

- **Do not run the remaining 9 sweep cases** (C7b, C18, C24, Block 3). They are
  the most turn-boundary-sensitive in the matrix, so under compressed delivery
  they produce passes that will not survive a real caller. Re-run a natural
  delivery set after C1 instead.
- **Do not chase B2 as an engineering project** before the demo. B1 is the part
  that fits in three days.

## Do we still need Jules's logs?

Mostly no — obs now covers behaviour directly (transcripts, `collected`,
`booking_confirmed`, `calendar_event_id`, `screening`), and that is what Block A
and D are built from.

Still logs-only: **latency percentiles** and **endpointing evidence**. So the ask
narrows to one thing, which is PII-free and pasteable:

```
python scripts/analyse_calls.py logs/sweep/
```

The raw files and the zip are no longer needed.
