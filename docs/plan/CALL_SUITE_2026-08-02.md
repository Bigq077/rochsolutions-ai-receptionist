# Call Suite — 2 Aug 2026

Six calls. Every turn tests something different; nothing is repeated for its own
sake. Designed to be dialled in one sitting, in order.

**Build under test:** `7610f9a` (`latency-eval`). **Deploy it before the first
dial** — Render service `low-latency-joint-venture` is Manual Deploy only, so it
is almost certainly still serving an older build. A call against `cf3be18` or
earlier passes C1 for the wrong reason.

**Number:** `+447366263180` → `jv_v1`, service `low-latency-joint-venture`.
Confirm both in the Render dashboard before dialling — `DEPLOYMENT_INVENTORY.md`
is still a blank template.

**Why this number is the right one for a demo port:** `jv_v1` runs
`prompt_engine=template_v1`, the same engine as `vital_edge`. Every case below
exercises the portable path, not a bespoke prompt. A pass here is evidence about
the next clinic; a pass on a hand-tuned prompt would not be.

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

---

## 0.1 · What not to trust

`Booking confirmation SMS sent to ***NNNN` **is lying on every call.**
`SMS_ENABLED` defaults to `false` on this branch (deliberately — this service must
never text a real caller), `send_sms` returns `None`, and
[booking_sms.py:103](../../app/notifications/booking_sms.py) discards that return
and logs the success line regardless. Same for `owner_alert`. Score SMS as **not
sent** on every call in this suite, whatever the log says. Fixing the line is
scheduled separately; until then it is not a channel.

---

## 0.2 · How to score

**Channel 1 — Render logs.** Filter `[ms_conn`, `[ms_llm`, `[ms_gate5`, `[book`.

| Line | Means |
|---|---|
| `[ms_llm] L2 classifier: '<utt>' -> yes` | colloquial affirmative resolved by the classifier |
| `[ms_llm] v3_confirmed_slot_phrase refreshed <old> -> <new>` | **the new C1 fix fired** — guard re-armed on the new day |
| `[ms_gate5] booking readback date NOT corrected` | guard stood down. Expected **once at most**, never after a refresh |
| `[ms_gate5] booking readback date corrected to confirmed slot` | guard caught a drifted date |
| `[ms_conn v3] booking verbal phone confirm — stored calling number` | caller ID accepted verbally |
| `[ms_conn v3] keypad phone committed — <num> + phone_confirmed=True` | typed number accepted |
| `[ms_conn v3] keypad number read back for confirmation: <num>` | C2 read-back fired |
| `[ms_conn] DTMF buffer '<digits>' is not a UK mobile — re-ask attempt N` | C3 caught a bad entry |
| `[ms_conn v3] keypad read-back REJECTED by caller — number cleared` | teardown ran |
| `[ms_conn v3] verbal phone confirm SKIPPED — keypad number already on record` | the load-bearing guard held |
| `[book] BLOCKED — phone not confirmed (A1)` | write refused for want of a confirmed number |
| `[book] A3 — booking phone corrected: model passed X, confirmed is Y` | **the model tried to book a different number.** Expected ABSENT everywhere below |

**Channel 2 — the calendar entry.** For any call that books, check the phone on
it **digit for digit** against what you gave. This is the only check a
clean-looking log cannot fake.

---

# Call 1 · The colloquial booker who changes their mind

The happy path, plus the two fixes that landed today. Speak naturally and
hesitantly throughout — the hesitation *is* the test.

| Turn | You say | What it tests | Pass |
|---|---|---|---|
| 1 | *"Hi, um, I think I need to see someone about my back"* | vague opener, no service named | she asks a clarifying question, does not guess a service |
| 2 | *"yeah it's been going on a few weeks"* | FAQ/triage turn with no booking intent | **at most one** booking CTA in the whole call so far |
| 3 | *"what have you got Thursday?"* | day-specific availability | `check_availability` for Thursday only |
| 4 | *"um yeah quarter to 7"* — **then stop talking** | trailing-digit endpointer | ⏱️ **time the gap from your last word to her first.** >1.5 s is the known 2.5 s defect — note the seconds |
| 5 | *"um yes but do you have any availability Friday by any chance"* | DIFFERENT-DAY steer mid-confirmation | she calls `check_availability` for **Friday**. ❌ FAIL if she books Thursday or reads Friday's name onto a Thursday time |
| 6 | pick a Friday slot | day change accepted | slot offered is genuinely Friday's |
| 7 | give first name, then surname on a **separate later turn** | surname straggler | surname captured, never re-asked or spelled back |
| 8 | *"yeah that's the one"* to the caller ID | verbal phone confirm | `booking verbal phone confirm — stored calling number` |
| 9 | *"um, go for it"* | **L2 classifier** on a colloquial yes | `L2 classifier: 'um go for it' -> yes`, round-trip **<1.5 s**, filler audible over the wait |
| 10 | — | **C1 date guard (today's fix)** | ✅ `v3_confirmed_slot_phrase refreshed` appears after turn 6 ✅ **no** `NOT corrected` after it ✅ every spoken date from turn 6 on is **Friday**, and the calendar entry is Friday |

> Turn 10 is the whole reason this call is first. Pre-fix, the guard stood down at
> turn 6 and stayed down. Two `NOT corrected` lines = the fix did not deploy.

---

# Call 2 · "No — it's actually oh seven…" ⚠️≠

**The untested branch, and the most likely one on a real demo.** She reads the
caller ID aloud, and a caller who declines will almost always say the new number
in the same breath rather than waiting to be told what to do.

Book normally to the phone step, then:

| Turn | You say | Watch for |
|---|---|---|
| 1 | *"No, it's actually oh seven seven one two, three four five six seven eight"* (a real second mobile, spoken in one breath with the decline) | which of the four outcomes below |

Four outcomes. Record **which one**, verbatim:

| # | Outcome | Verdict |
|---|---|---|
| A | Keypad line, spoken digits ignored — you type it, it books correctly | **Acceptable.** Safe but costs a turn; note whether it felt rude to be asked to type what you just said |
| B | She reads the **spoken** number back, you confirm, it books on it | **Best UX — but check the calendar digit for digit.** STT digit errors are the risk this path carries and the keypad does not |
| C | She books on the **caller ID** you just rejected | ❌ **BLOCKER.** Look for `[book] A3 — booking phone corrected` — that is A3 overwriting the spoken number with the confirmed caller ID |
| D | Loop or dead end — re-asks, or `[book] BLOCKED — phone not confirmed (A1)` with no recovery | ❌ **BLOCKER.** Note how many turns before it recovers or you give up |

Then, **same call**, one more probe:

| Turn | You say | Pass |
|---|---|---|
| 2 | if she asked you to type it, type a **different** number again | the booking carries the **typed** number, not the spoken one and not the caller ID |

> C and D are both plausible from a reading of the code and neither is currently
> pinned by a test. This call is the highest-information call in the suite.

---

# Call 3 · Keypad rejects what it should, accepts what it should ⚠️≠

Decline the caller ID with a bare *"no, a different number"* — no digits spoken.

| Turn | You do | Pass | Fail |
|---|---|---|---|
| 1 | type **nine digits**, stop, stay silent | after ~5 s: *"That doesn't look like a complete number…"* + `is not a UK mobile — re-ask attempt 1` | ❌ she reads back or books **any** 11-digit number — that is the fabrication bug. Note it; it will appear in no DTMF log line |
| 2 | type **`0` + nine more** (drop one digit), stop | re-ask again, attempt 2 | ❌ a number starting `00…`, or 11 digits you did not type |
| 3 | type a valid mobile **without its leading zero** (`7…`, ten digits) | **accepted**, read back in `07…` form | ❌ re-asked — that is a false reject of a number typed a common way |
| 4 | *"yes"* | books on the `07…` form | |

> Turns 2 and 3 are the pair that matters: the old code could not tell them apart
> and padded both. They must now behave differently.

---

# Call 4 · The read-back, accepted then rejected ⚠️≠

| Turn | You do | Pass | Fail |
|---|---|---|---|
| 1 | decline caller ID, type a full valid mobile | `keypad number read back for confirmation` — digits **grouped**, *"oh seven seven double-oh, nine…"*, not run together | ❌ digits machine-gunned or run together |
| 2 | *"no, that's wrong"* | *"No problem — go ahead and type the number on your keypad…"* + `read-back REJECTED by caller — number cleared` | ❌ she carries on to the booking readback without re-collecting |
| 3 | type a **different** valid mobile | read back again, cleanly | |
| 4 | *"hang on"* / *"sorry, what?"* | ⚠️ **the number survives** — not cleared, keypad not re-armed | ❌ an ambiguous answer wipes a good number |
| 5 | *"yes"* | straight to the warm readback (name, day, date, time) + `verbal phone confirm SKIPPED — keypad number already on record` | ❌ she asks about the number a second time |
| 6 | let it book | calendar carries the **second** number | ❌ the first number, or the caller ID |

---

# Call 5 · The ladder terminates

Proves the re-ask cannot loop forever. Fail on purpose.

| Turn | You do | Expected wording |
|---|---|---|
| 1 | 9 digits, stop | *"That doesn't look like a complete number — could you double-check it and type it again on your keypad?"* |
| 2 | 9 digits, stop | *"I'm still not getting a full number. I can use the number you're calling from instead — just say 'use this number', or type it again on your keypad."* |
| 3 | 9 digits, stop | *"I'm still not getting a full number — could you read it out to me instead?"* — **keypad now closed** |
| 4 | read a number out loud | she takes it verbally. Check the calendar digit for digit — this is the one path with no machine capture at all |

❌ **FAIL** if rung 1 wording repeats a fourth time (unbounded loop), or there is
dead silence at any rung.

**Second call, if time:** fail once, then at rung 2 say *"use this number"* —
caller ID accepted, booking carries the number you are calling from.

---

# Call 6 · Scope — the number as a search key

The read-back must **not** fire where the number is a lookup key, and the
template's non-booking paths must survive everything above.

| Turn | You say | Pass | Fail |
|---|---|---|---|
| 1 | *"I need to move an appointment"* | EXACTLY *"Of course, let's get that moved for you."* and **stop** — no question tacked on, no tool call | ❌ she asks a question on the same turn |
| 2 | — | *"Was your original appointment booked under the number you're calling from?…"* | |
| 3 | *"no, it was a different one"* → type a valid mobile | she looks it up. **No** *"Thanks — I've got… is that correct?"*, and **no** `keypad number read back for confirmation` line | ❌ she reads the number back before looking it up |
| 4 | *"actually, can you just cancel it instead"* | intent switch mid-flow, still one `lookup_patient` | ❌ a second lookup, or a transfer |
| 5 | *"how much is an appointment?"* mid-flow | FAQ detour does not lose the flow | ❌ she restarts, or loses the appointment she found |

---

## Minimum set if time is short

**Call 1 · Call 2 · Call 3.** Today's fix, the untested verbal-number branch, and
the fabrication guard. Call 4 next.

---

## Recording

One file per call, `logs/sweep/C<N>-<name>.txt`, in dial order. `logs/` is
gitignored and stays that way — real numbers in there. **Never paste raw logs
into chat.**

One row per call:

| Call | Pass/Fail | Number given (how) | Number on booking | Slot spoken | Slot booked | Turn-4 gap (s) | Notes |
|---|---|---|---|---|---|---|---|

The columns that settle everything are **number given vs number on booking** and
**slot spoken vs slot booked**. If either pair differs on any call, that is the
finding, whatever the audio sounded like.
