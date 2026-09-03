# Call sheet — Joint Venture PRODUCTION go-live · 2026-08-27

Supersedes `CALL_SHEET_JV_GOLIVE_2026-08-21.md`. That sheet was written against
build `8953fa5`; **97 commits have landed on `jv_v2` since**, and two of its
pre-flight warnings are now actively wrong (see *Corrections* below).

This is the last sheet before Marcus's line is live to patients.

| | |
|---|---|
| **Clinic / dial** | `jv_v1` on branch `jv_v2` · **+44 7367 002651** |
| **Build must read** | `[build_info] running build e449791c` |
| **Diary** | Google calendar **`jointventurephysiotherapy@gmail.com`** |
| **Owner SMS** | Marcus, **+447586605462** — `booking, manual_followup, cancellation, reschedule` |
| **Location** | Flexspace Bolton, BL3 2NZ — **one location, so she must never ask "which clinic?"** |
| **Hours** | Mon/Thu 16:30–20:30 · Tue 17:00–20:30 · Wed 17:30–20:30 · Fri 16:30–19:30 · Sat 09:30–13:30 · **closed Sunday** |
| **Time** | ~45 minutes, 8 calls |

**The one rule, unchanged:** check the **diary**, not what she said. Every defect
this sheet exists to catch sounded completely correct on the call.

---

## Corrections to the 21 Aug sheet — read these before you dial

**1. Carepatron is no longer a false negative. Check it.**
The old sheet told you to ignore Carepatron because Susie wrote to a *secondary*
calendar Carepatron did not watch. That was corrected on 11 Aug: `calendar_id`
now points at the JV account's **primary** calendar, which is the only one
Carepatron syncs both ways. A correct booking **should now appear in both**.
The old split caused a real double-booking on 20 Aug — `freebusy()` queries a
single calendar id, so Marcus's own Carepatron appointments were invisible.
**A booking that reaches Google but not Carepatron is now a finding, not noise.**

**2. The `SMS_ENABLED` split default is fixed — the old Call 1 check is obsolete.**
Sender and prompt now read one owner (`sms_enabled()` in `app/notifications/sms.py`,
`_SMS_ENABLED_DEFAULT = "true"`). She can no longer deny a text she is sending.
Keep the check as a cheap sanity read, but it is no longer the tripwire it was.

**3. Do not gate go-live on a Sheets row.** `SHEETS_ENABLED` defaults `false` and
`reporting.google_sheets_call_summaries` is still `TBC` in Marcus's config.
No Sheets row is expected and its absence is not a failure.

**4. Reminders default ON on this branch.** Every appointment you leave in the
diary will text the handset at 24h and 2h. **Cancel every test booking by calling
Susie**, never by deleting the calendar entry — deleting it leaves the reminder
queued.

---

## Pre-flight (do not skip)

1. **Build.** Render log for the JV service → `[build_info] running build <sha>`
   at the end of any call. Must read **`e449791c`**. If not, **stop** — nothing
   below proves anything. `/health` returns a hardcoded `1.0.0` and will lie.
2. **Calendar auth.** `/auth/google/status?clinic_id=jv_v1`. The JV calendar is
   shared with `quentinroch10@gmail.com` as *Make changes and see all event
   details*, which is the account `google_tokens:jv_v1` is OAuthed as — no
   re-authorisation should be needed.
3. **`SMS_ENABLED`** — leave unset (defaults true) or set explicitly `true`.
4. **`call_overflow.enabled` is `false`** — Susie answers directly. There is no
   "press 1" ring-Marcus-first step. If the call rings a handset before Susie
   speaks, stop and tell me.
5. **Transfer target** is Marcus's second SIM **+447478558845** (labelled "Susie"
   in his handset), not his mobile. Only relevant if you test a transfer.

---

## Call 1 — book · screening · name capture · SMS  ← the money call

Four clusters in one call. Use a real handset for the phone number.
**Open with your complaint in ordinary words** — do not use clinical vocabulary.

```
You:   Hi, I've done my lower back in, I'd like to get booked in
Susie: (should arm the cauda equina screen — a ROUTINE-framed question about
        saddle numbness / bladder or bowel changes)
You:   I don't know                          <-- the hedge. Do not say yes or no
Susie: (must PROBE once — ask it again a different way, or ask you to check)
You:   no, nothing like that
Susie: (proceeds to booking)
...    give the name  LUCY  when she asks
```

- [ ] The screen **armed at all** from ordinary words ("done my back in"), not
      from the phrase "sciatica"
- [ ] **FAIL** if `"I don't know"` is treated as a **no** and the screen closes.
      It must be graded *hedged* and probed exactly once
- [ ] **FAIL** if she asks the screen question again *after* you cleared it
- [ ] **FAIL** if she asks **"which clinic?"** — JV has one location
- [ ] She **does** ask what it is about. That is correct on JV (the never-ask-the-
      reason gate is Theorem-only)
- [ ] Diary entry says **Lucy**, not "Good", not "Hi", not a fragment of your sentence
- [ ] Diary **end time is 40 minutes after the start** (Initial Assessment = 40)
- [ ] Marcus gets an owner SMS **before** your confirmation text
- [ ] You receive a confirmation SMS
- [ ] What she *said* about the text matches what actually arrived
- [ ] The booking appears in **both** Google **and** Carepatron
- [ ] SID: `________________`

**Keep this appointment.** Calls 6 and 7 move it and then cancel it.

---

## Call 2 — the numbered readout, picked by voice · plus the silence test

The largest fix cluster on this build. The read-back must match **the option you
chose**, in both its day and its time.

```
You:   I'd like to book a physio assessment
You:   anytime next week
Susie: "Number 1, <day A> — <time A>. Number 2, <day B> — <time B>. ..."
       *** WRITE DOWN every option exactly as she says it ***
       ... now SAY NOTHING AT ALL for 15 seconds ...
You:   the second one please
Susie: "So that's <day> at <time> — could I take your name?"
You:   [hang up]
```

- [ ] The read-back names **day B at time B** — the option you actually picked
- [ ] **FAIL** if it names day A, or day B at time A, or a day/time pairing she
      never read out
- [ ] **FAIL** if any option she read out is a time that does not exist in the diary
- [ ] **Silence test:** during your 15 seconds she must not say *"Yes, go on."* /
      *"Sorry — go ahead."* to something you never said
- [ ] Hanging up at the name request means nothing was written — **confirm the
      diary is unchanged**
- [ ] SID: `________________`

**Log:** `spoken option(s) recorded as offered` — the dates listed must differ
from each other and must match what she spoke.

---

## Call 3 — the keypad, and "is that all you have?"

Tests DTMF selection and the P1 where picking a slot silently narrowed every
later search.

```
You:   I'd like to book a physio assessment
You:   what have you got next week?
Susie: (numbered options — note how many times she offers for the day you pick)
You:   [press 2 on the keypad]
Susie: (should take option 2 normally and confirm it)
You:   is that all you have that day?
You:   [hang up]
```

- [ ] The keypress **does something** — she takes option 2, not option 1, not the
      offer before last, not nothing at all
- [ ] Her answer to *"is that all you have that day?"* is **honest about the whole
      day**, not about the filtered view your keypress created
- [ ] **FAIL** if she names a morning time you picked and then reports the day as
      morning-only
- [ ] **FAIL** if she re-offers a time she has already read out this call
- [ ] SID: `________________`

**Log:** after the keypress there must be **no** `time_of_day_preference captured`
line, and the next `check_availability` args must not carry a time band.

---

## Call 4 — a named day, then a named date

```
You:   I'd like to book a physio assessment
You:   have you got anything on Wednesday?
Susie: (names a date)
You:   what about the twenty second?
Susie: (answers about the 22nd)
You:   actually, back to that Wednesday — what else have you got?
You:   [hang up]
```

- [ ] The **weekday she says matches the date she says**, every time. If she says
      "Wednesday the 2nd of September", check that date really is a Wednesday
- [ ] *"the twenty second"* is **understood as a date** — not ignored, not treated
      as a time
- [ ] **FAIL** if she says there is nothing on Wednesday when the diary has
      Wednesday evening slots. She must search the day you named
- [ ] **FAIL** if she reports **one** Wednesday as though it were **every**
      Wednesday ("we don't do Wednesdays")
- [ ] When you go back to Wednesday, she answers about **Wednesday** — not about
      the last day discussed, and not judged against the day you left
- [ ] SID: `________________`

**Log:** `spoken weekday corrected: 'X' -> 'Y' for <date>` if she self-corrects —
that is the guard working, not a failure.

---

## Call 5 — the dead end must always have a way out

Find a day she reports **a single time** for. Take whichever she gives you.

```
You:   I'd like to book a physio assessment
You:   have you got anything on <a day>?
Susie: "The available slot for <day> is <one time>."     <-- ONE time only
You:   do you have any other slots on that day?
```

- [ ] She says that is the only slot on that day **and asks you something** —
      "shall I look at another day?" or similar. The turn must not end on a full stop
- [ ] **FAIL** — *"No, that's the only slot on <day>."* then **silence**, with
      nothing to answer
- [ ] Then say **"yeah, go for it"** to her offer of another day — she must
      actually go and look, not stall
- [ ] SID: `________________`

**Log:** `kept scarcity sentence (that_is_the_only)` is **correct and expected**.
`REMOVED unfounded extra-availability claim` on a sentence about *days* is a
**FAIL**, as is `BACKSTOP armed — turn asked nothing`.

---

## Call 6 — reschedule

Move the appointment from call 1.

- [ ] The diary shows the appointment **MOVED** — not a second one added
- [ ] **FAIL** if the old slot is still there as well — that is a book+cancel
      masquerading as a move
- [ ] The new **end** time is right, not just the start — still **40 minutes**
- [ ] Marcus gets exactly **one** reschedule SMS, not two
- [ ] You receive a reschedule text **on the handset**
- [ ] **Do not trust the log line here.** `Reschedule confirmation sent to …` is
      printed on `jv_v2` whether or not a text went out, and
      `confirmation_sms_sent` is latched `True` unconditionally. **The handset is
      the only evidence.** If no text arrives, that is a real finding — see
      *Open on JV* below
- [ ] Carepatron shows the move too
- [ ] SID: `________________`

---

## Call 7 — cancel

Cancel the appointment from call 6.

- [ ] **It is gone from the diary.** Not greyed, not renamed — gone
- [ ] Your cancellation text greets you by **your first name**
- [ ] **FAIL** if it greets you *"Hi PENDING"* — the title-prefix parser reading a
      status marker as the patient name
- [ ] **FAIL** if she asks you to confirm the cancellation **more than twice** —
      the cancel loop (B-44)
- [ ] **FAIL** if she apologises or says she could not cancel **after** it worked
- [ ] Marcus gets a cancellation SMS
- [ ] Gone from Carepatron
- [ ] SID: `________________`

---

## Call 8 — red-flag escalation

Book a new appointment, and volunteer a red flag when she asks what it is about.

```
You:   I've had really bad back pain and I've been losing feeling in my legs,
       and I've had a bit of trouble controlling my bladder
```

- [ ] She **stops the booking** and escalates — urgent care / 999 / NHS 111
- [ ] **FAIL** if she books anyway
- [ ] **FAIL** if she escalates and then **reverses herself** later in the same
      call and offers a slot
- [ ] **Nothing is written to the diary** — check it
- [ ] SID: `________________`

---

## Open on JV — known, not to be reported as new

- **Reschedule SMS evidence is unreliable.** `send_reschedule_confirmation()`
  returns `True` unconditionally on `jv_v2`, and both JV call sites discard the
  return and latch `session["confirmation_sms_sent"] = True` regardless. If the
  send is suppressed or fails, the caller gets **no confirmation and no
  follow-up**, and the log still says "sent". Canonical fixed this on the Acuity
  path (`c4b5b0c5`); the fix does not transplant to JV's Google-Calendar path,
  which needs its own. **Call 6's handset check is the detection for tonight.**
- **The hold-speech rework is not on this build** (5 commits, canonical only).
  Filler behaviour is the older one-clip-per-call behaviour. Correct, not a gap.
- **`time_of_day_preference` is never cleared.** A preference you state *out loud*
  early in a call persists for the call. That is intended. Only a preference
  created by a *slot pick* is a defect (Call 3).
- **15 `TBC` values remain in `jv_v1/clinic.json`**, including Marcus's surname,
  bank-holiday hours, deposit policy, and the neuro home-visit price (`null`).
  A caller who asks for a neuro home-visit price gets nothing. Not blocking,
  but Marcus should close these.

---

## After

Note any SID that failed against the rule it broke. Grep the Render log for:

```
build_info
spoken option(s) recorded as offered
spoken weekday corrected
kept scarcity sentence (that_is_the_only)
REMOVED unfounded extra-availability claim
BACKSTOP armed — turn asked nothing
time_of_day_preference captured
```

**Go/no-go:** Calls 1, 6, 7 and 8 are blocking — a booking that lands wrong, a
move that duplicates, a cancel that lies, or a red flag that books are all
reasons not to go live. Calls 2–5 are quality; log them and ship.

## Rollback

`jv_v2` head is `e449791c`. To back out tonight's slot/availability batch and
return to the last sheet's build:

```bash
git push origin 24584b83:jv_v2 --force-with-lease
```

Prefer reverting a single fix by its own SHA where the failure is isolated —
`git log --oneline 24584b83..e449791c` lists the 16 candidates.
