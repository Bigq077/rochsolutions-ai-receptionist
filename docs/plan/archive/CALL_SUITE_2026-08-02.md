# Call Suite — revised 2 Aug 2026 (evening)

**Build under test: `dc31c6c`.** This supersedes the 01:09 version, which was
written against **`7610f9a`** — **fifteen commits ago**. Nine of those changed
caller-visible behaviour. Dialling the old suite now would pass or fail for the
wrong reasons; the specific traps are listed in §0.3.

**Deploy `dc31c6c` before the first dial.** Render service
`low-latency-joint-venture` is Manual Deploy only, so it is almost certainly
serving something older.

**Number:** `+447366263180` → `jv_v1`, service `low-latency-joint-venture`.
Confirm both in the Render dashboard first — deployment topology is not knowable
from this repo (`README.md` correction 15) and `DEPLOYMENT_INVENTORY.md` is still
a blank template.

**Why this number:** `jv_v1` runs `prompt_engine=template_v1`, the same engine as
`vital_edge`. Every case below exercises the portable path. A pass here is
evidence about the next clinic; a pass on a hand-tuned prompt would not be.

---

## 0 · Rules

| # | Rule |
|---|---|
| R1 | **UK mobile, real handset.** Several cases branch on caller ID. Not a `+33` line. |
| R2 | **One call per case block.** Hang up between them. Never chain two blocks. |
| R3 | **Type at one digit per second.** Machine-gunning the keypad tests a timing path no caller uses. |
| R4 | **Fix nothing mid-run.** Log it and keep dialling. |
| R5 | **Nobody deploys during the window.** A push mid-run invalidates every call before it. |
| R6 | **Note wall-clock time per call** — it is how you find it in Render logs. |
| R7 | **Your ear is a channel the logs do not have.** Dead air, talk-over, and anything that merely *sounded* wrong all count. |
| R8 | **Have a second real mobile to hand.** Cases that only prove something when the given number ≠ caller ID are marked ⚠️≠. |
| R9 | **Score §0.4 on every call, not just the call that targets it.** Two of the three live defects show up anywhere. |

---

## 0.1 · What not to trust

`Booking confirmation SMS sent to ***NNNN` **is lying on every call.**
`SMS_ENABLED` defaults `false` here (deliberately — this service must never text
a real caller), `send_sms` returns `None`, and
[booking_sms.py:103](../../app/notifications/booking_sms.py) discards that return
and logs success regardless. Score SMS as **not sent** on every call, whatever
the log says. This is `B-17`, deliberately deferred — it cannot fire on this
branch.

Susie should **never say** a text has been sent: the spoken promise is gated on
`SMS_ENABLED` on both the booking and reschedule closings. **If you hear her
promise a text, that is a finding** — it means the gate leaked.

---

## 0.2 · How to score

**Channel 1 — Render logs.** Filter `[ms_conn`, `[ms_llm`, `[ms_gate5`, `[ms_tools`, `[book`.

| Line | Means |
|---|---|
| `[ms_llm] L1 verdict: '<utt>' -> yes\|no\|unsure` | **NEW** — caller-ID confirm judged, not phrase-matched |
| `[ms_llm] L2 classifier: '<utt>' -> yes` | colloquial affirmative escalated to the classifier |
| `[ms_conn v3] phone confirm unsettled (N)` | **NEW** — an `unsure` verdict, ladder rung N |
| `[ms_conn v3] phone confirm unsettled twice — handing off to the keypad` | **NEW** — the `dc5c89d` bound fired |
| `[ms_llm] v3_confirmed_slot_phrase refreshed <old> -> <new>` | date guard re-armed on a new day |
| `[ms_llm] DIFFERENT DAY REQUESTED steer applied` | **NEW** — caller's day change steered |
| `[ms_gate5] booking readback date NOT corrected` | guard stood down. Expected **at most once**, never after a refresh |
| `[ms_gate5] booking readback date corrected to confirmed slot` | guard caught a drifted date |
| `[ms_conn] same-breath straggler dropped — enqueued …` | **NEW** — B-18 bound; check the age it prints is < 2 s |
| `[ms_conn] same-breath straggler KEPT (name collection…)` | exemption arm fired |
| `[ms_conn v3] keypad phone committed — <num> + phone_confirmed=True` | typed number accepted |
| `[ms_conn v3] keypad number read back for confirmation: <num>` | C2 read-back fired |
| `[ms_conn] DTMF buffer '<digits>' is not a UK mobile — re-ask attempt N` | bad entry caught |
| `[ms_conn v3] keypad read-back REJECTED by caller — number cleared` | teardown ran |
| `[ms_conn v3] verbal phone confirm SKIPPED — keypad number already on record` | the load-bearing guard held |
| `[ms_tools] _match_gcal_event: refusing name fallback for …` | **NEW** — reschedule refused to guess |
| `[book] BLOCKED — phone not confirmed (A1)` | write refused for want of a confirmed number |
| `[book] BLOCKED — no reason on record (A2)` | write refused for want of a reason |
| `[book] A3 — booking phone corrected: model passed X, confirmed is Y` | **the model tried to book a different number.** Expected ABSENT everywhere |
| `[ms_tts] prewarm: ElevenLabs rejected the API key (401)` | **NEW** — if you see this at startup, stop: the voice will fall back mid-call |

**Channel 2 — the calendar entry.** For any call that books, check the phone
**digit for digit** and the date **day-name and number** against what you gave.
The only check a clean-looking log cannot fake.

---

## 0.3 · Traps carried over from the old suite

Do not reuse the 01:09 expectations. Specifically:

- **Old Call 1 turn 10** tested a date guard that `7b698f6` has since rewritten —
  staleness is now judged against *every* offered day, not just day one, and
  `100b561` added a CALL STATE assertion of the agreed slot. New wording below.
- **Old Call 2/3/4** predate `8d152f0` (verdict-based caller-ID confirm) and
  `dc5c89d` (the two-strike bound). Both change what a hesitant answer does.
- **Old Call 6** predates `48d9e57`, `ad938cf` and `e5a8ee9` — all three changed
  the reschedule path.
- **Nothing in the old suite covers `B-15`**, which changed what Susie says on
  dead air at the phone step. That wording has **never fired on any build**.

---

## 0.4 · Score these on EVERY call

Three defects are live and known. They appear anywhere, so they are scored
per-call rather than given a call of their own.

| ID | What to listen for | Status |
|---|---|---|
| **B-20** | An unwarranted **screening question**. She has six red-flag screens and no knee screen; an aching knee, a sore shoulder or "I'd like to book an appointment" warrant **none**. Record the screen asked and your presentation, verbatim | Live, root-caused, awaiting an authority decision |
| **B-23** | She asks *"what's the appointment for?"* **after** you have already said. Record whether she named the complaint herself first | Live. Extractor hardened but **not yet wired** |
| **B-21** | *"Redditch"* mispronounced (doubled-d). Only if she says it at all | Unknown — the dictionary that covered it was removed today |

> **A B-20 sighting is the most valuable thing this sweep can produce**, because
> the fix choice (A/B/C) depends on how often it fires and on what presentation.

---

# Call 1 · The colloquial booker who changes their mind

The happy path plus four of today's changes. Speak naturally and hesitantly —
the hesitation *is* the test.

| Turn | You say | What it tests | Pass |
|---|---|---|---|
| 1 | *"Hi, um, I think I need to see someone about my knee"* | vague opener, complaint given | ✅ she does **not** ask what the appointment is for (`B-23`) ✅ she does **not** ask a screening question (`B-20`) |
| 2 | *"yeah it's been going on a few weeks"* | FAQ/triage turn, no booking intent | **at most one** booking CTA in the call so far |
| 3 | *"what have you got Thursday?"* | day-specific availability | `check_availability` for Thursday only |
| 4 | *"um yeah quarter to 7"* — **then stop talking** | trailing-digit endpointer | ⏱️ **time the gap from your last word to her first.** Note the seconds |
| 5 | *"...in the evening"* after ~1 s | **B-18 same-breath bound** | the straggler is folded in, not dropped. Log prints an age **< 2 s** |
| 6 | *"um yes but do you have any availability Friday by any chance"* | DIFFERENT-DAY steer mid-confirmation | `DIFFERENT DAY REQUESTED steer applied`, `check_availability` for **Friday**. ❌ FAIL if she books Thursday or reads Friday's name onto a Thursday time |
| 7 | pick a Friday slot | day change accepted | slot offered is genuinely Friday's |
| 8 | give first name, then surname on a **separate later turn** | surname straggler | surname captured, never re-asked or spelled back |
| 9 | *"yeah that's the one"* to the caller ID | **verdict-based confirm (`8d152f0`)** | `L1 verdict: 'yeah thats the one' -> yes`. ❌ FAIL if she re-asks — that is the `CAcb4a11b90` abandonment defect returning |
| 10 | *"um, go for it"* | L2 classifier on a colloquial yes | `L2 classifier: … -> yes`, round-trip **< 1.5 s**, filler audible over the wait |
| 11 | — | **date guard (`7b698f6` + `100b561`)** | ✅ `v3_confirmed_slot_phrase refreshed` after turn 7 ✅ **no** `NOT corrected` after it ✅ every spoken date from turn 7 on is **Friday** ✅ calendar entry is Friday, and the **day name matches the date** |

> Turn 11 is why this call is first. Pre-fix the guard stood down at the day
> change and stayed down for the rest of the call. Two `NOT corrected` lines mean
> the fix did not deploy.

---

# Call 2 · The caller who refuses the caller ID ⚠️≠

**The highest-information call in the suite.** `8d152f0` was written because
`_is_use_this_number("don't use that one")` returned **True** — a caller
explicitly refusing the caller ID would have had it stored as confirmed and
**booked on**. That is a wrong booking, not a missed one.

Book normally to the phone step, then:

| Turn | You say | Pass | Fail |
|---|---|---|---|
| 1 | *"no, don't use that one"* — nothing else | `L1 verdict: … -> no`, and she moves to collect a different number | ❌ **BLOCKER** — she treats it as confirmation and books the caller ID. Look for `[book] A3 — booking phone corrected` |
| 2 | *"No, it's actually oh seven seven one two, three four five six seven eight"* (a real second mobile, spoken in one breath) | one of the four outcomes below | |

Four outcomes for turn 2. Record **which one**, verbatim:

| # | Outcome | Verdict |
|---|---|---|
| A | Keypad line, spoken digits ignored — you type it, it books correctly | **Acceptable.** Safe but costs a turn; note whether being asked to type what you just said felt rude |
| B | She reads the **spoken** number back, you confirm, it books on it | **Best UX — check the calendar digit for digit.** STT digit errors are this path's risk |
| C | She books on the **caller ID** you just rejected | ❌ **BLOCKER** |
| D | Loop or dead end, or `[book] BLOCKED — phone not confirmed (A1)` with no recovery | ❌ **BLOCKER.** Note how many turns before recovery |

---

# Call 3 · The unsettled answer — the two-strike bound ⚠️≠

**`U-05`, never verified on a call.** `dc5c89d` bounds the verbal confirm: two
unsettled answers hand off to the keypad rather than looping.

| Turn | You say | Pass |
|---|---|---|
| 1 | *"hmm, maybe"* to the caller-ID question | `L1 verdict: … -> unsure`, `phone confirm unsettled (1)`, she re-asks **once**, differently |
| 2 | *"I'm not sure really"* | `phone confirm unsettled twice — handing off to the keypad` + the keypad invitation, spoken |
| 3 | type a valid mobile | committed and read back |

❌ **FAIL** if she asks the same question a third time — that is the unbounded
loop the bound exists to stop.

---

# Call 4 · Dead air at the phone step — NEVER FIRED ON ANY BUILD

**`B-15`, shipped today.** Until this build, `capture_phase` returned `"name"`
for the whole call once a caller gave a first name only, so dead air here
answered *"could I take your first name and surname again?"* — a question you had
already answered, about a step you had already passed. The phone re-ask wording
exists but has **never been reachable on the booking path**.

| Turn | You do | Pass | Fail |
|---|---|---|---|
| 1 | book to the phone step; give **first name only** when asked | — | — |
| 2 | at *"is that the best number…"*, **say nothing for ~10 s** | *"Sorry — is the number you're calling on the best one to reach you? Just say use this number."* | ❌ she asks for your **name** again — B-15 did not deploy ❌ dead silence |
| 3 | *"use this number"* | accepted, booking proceeds | |

**Second call if time:** same setup, but go quiet at the **booking-confirmation**
step instead. Expected: a neutral re-ask, **not** a name question and **not** a
phone question.

---

# Call 5 · Keypad rejects what it should, accepts what it should ⚠️≠

Decline the caller ID with a bare *"no, a different number"* — no digits spoken.

| Turn | You do | Pass | Fail |
|---|---|---|---|
| 1 | type **nine digits**, stop, stay silent | after ~5 s: *"That doesn't look like a complete number…"* + `is not a UK mobile — re-ask attempt 1`. **Digits spoken as words, not run together** (`d0a0d8a`) | ❌ she reads back or books **any** 11-digit number — the fabrication bug, and it appears in no DTMF log line |
| 2 | type **`0` + nine more**, stop | re-ask, attempt 2 | ❌ a number starting `00…`, or 11 digits you did not type |
| 3 | type a valid mobile **without its leading zero** (`7…`, ten digits) | **accepted**, read back in `07…` form | ❌ re-asked — a false reject of a number typed a common way |
| 4 | *"yes"* | books on the `07…` form | |

> Turns 2 and 3 are the pair that matters: the old code could not tell them apart
> and padded both.

---

# Call 6 · The read-back, accepted then rejected ⚠️≠

| Turn | You do | Pass | Fail |
|---|---|---|---|
| 1 | decline caller ID, type a full valid mobile | `keypad number read back for confirmation` — digits **grouped**, *"oh seven seven double-oh, nine…"* | ❌ digits machine-gunned |
| 2 | *"no, that's wrong"* | *"No problem — go ahead and type the number on your keypad…"* + `read-back REJECTED by caller — number cleared`, **and the keypad is armed again** (`b922675`) | ❌ she carries on to the booking readback ❌ your retyped digits are ignored |
| 3 | type a **different** valid mobile | read back again, cleanly | |
| 4 | *"hang on"* / *"sorry, what?"* | ⚠️ **the number survives** — not cleared, keypad not re-armed | ❌ an ambiguous answer wipes a good number |
| 5 | *"yes"* | straight to the warm readback + `verbal phone confirm SKIPPED — keypad number already on record` | ❌ she asks about the number a second time |
| 6 | let it book | calendar carries the **second** number | ❌ the first number, or the caller ID |

---

# Call 7 · Reschedule — three changes landed here today

| Turn | You say | Pass | Fail |
|---|---|---|---|
| 1 | *"I need to move an appointment"* | EXACTLY *"Of course, let's get that moved for you."* and **stop** — no question, no tool call | ❌ a question on the same turn |
| 2 | — | **REWORDED 3 Aug 2026.** She **reads your number back in three digit groups** and asks a plain yes/no: *"I've got you on oh seven five oh two, two one one, two oh seven — is that the number the appointment was booked under?"* Confirm with **"go for it"** or **"that's the number"** — both were falling through the deterministic gate until 3 Aug and are now pinned | ❌ the old set-phrase wording (*"…just say 'use this number'"*), which booking's step 8 explicitly bans; ❌ no digits spoken; ❌ a re-ask after "go for it" |
| 3 | *"no, it was a different one"* → type a valid mobile, then confirm ("yes" / "go for it" / "that's the number") | **REVERSED 3 Aug 2026 — owner decision.** She **DOES** read it back, in the booking wording: *"Thanks — I've got 0 7…. Is that correct?"*, then looks it up on your confirmation. Log: `keypad number read back for confirmation`, then `lookup keypad number CONFIRMED by caller … queueing digits for lookup_patient` | ❌ no read-back; ❌ read-back but no `lookup_patient` afterwards — **that second one is the failure mode no unit test can catch** |
| 4 | *"yeah that's right"* to any consent question | **`48d9e57` / `U-06`** — judged, not phrase-matched | ❌ a refusal is read as consent |
| 5 | give a **common** first name only, no surname | **`e5a8ee9`** — `_match_gcal_event: refusing name fallback` when the name matches several | ❌ she moves someone else's appointment |
| 6 | complete the move | **`ad938cf`** — the closing confirms the move and **does not promise a text** | ❌ *"I've sent you a confirmation text"* (see §0.1) |
| 7 | *"actually, can you just cancel it instead"* | intent switch mid-flow, still one `lookup_patient` | ❌ a second lookup, or a transfer |

---

## Minimum set if time is short

**Call 1 · Call 2 · Call 4.**

Call 1 covers four of today's fixes at once. Call 2 is the only one that can
catch a **wrong booking** rather than a missed one. Call 4 is wording that has
never been spoken on any build. Call 3 next, then 7.

And score **§0.4 on every call you make**, even the ones you abandon — B-20 is
the finding this sweep is most likely to produce.

---

## Recording

One file per call, `logs/sweep/C<N>-<name>.txt`, in dial order. `logs/` is
gitignored and stays that way — real numbers in there. **Never paste raw logs
into chat.**

| Call | Pass/Fail | Number given (how) | Number on booking | Slot spoken | Slot booked | Turn-4 gap (s) | B-20? | B-23? | Notes |
|---|---|---|---|---|---|---|---|---|---|

The columns that settle everything are **number given vs number on booking** and
**slot spoken vs slot booked** — and, new this round, **day name vs date** on the
calendar entry, since `A2` (a weekday that does not match its date) has never had
a deterministic guard and the Gate 5 rewrite propagates whatever was first said.

If any of those pairs disagree on any call, that is the finding, whatever the
audio sounded like.
