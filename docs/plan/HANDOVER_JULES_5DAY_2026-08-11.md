# Handover to Jules — 5 days (Quentin away)

**Written:** 2026-08-11 (Quentin)  
**Working copy:** live for the week Quentin is at a festival  
**Owner this week:** Jules — bug-fixing, test-calling, deploys across all four branches

> If this document and the code disagree, **the code wins**. Update §Branch
> state and §Corrections as you go.

---

## Context

Today (11 Aug) closed the four-branch convergence plan and shipped nine fixes.
The booking **engine** is now in good shape — verified end-to-end on Theorem and
Vital Edge, with correct times and durations written to both diaries. Every
defect still open is in the *edges*: the last ten seconds of a call, the record
written afterwards, and how the system behaves when the caller is not
cooperative.

That last point is the whole job this week. Clean bookings are known to work.
What is **not** known is what happens when someone talks over Susie, changes
their mind, answers a different question, or says something that doesn't parse.

---

## Standing rules — not negotiable

1. **No fix is proven until a call proves it.** A green suite is necessary, not
   sufficient. Both of today's worst bugs — a booking written over a real
   patient, and no clinic writing call records — were invisible to the test
   suite and found only by reading live-call logs.
2. **Any defect found on ANY branch → check the other three.** If they have it,
   port it. Most of today's work was fixes that had been stranded on one branch
   while the other clinics quietly ran the bug.
3. **Read the code, never a commit listing.** `git log --cherry-pick` overstates
   divergence badly here — cross-clinic ports have different diffs, so a fix
   looks missing when it is present under another commit. This cost real time
   today, twice.
4. **Baseline before you change anything**, then diff the failing sets. Match on
   `^(FAILED|ERROR) tests/` — a bare `grep '^ERROR'` also catches captured log
   lines and inflates the count by 5.
5. **Canonical-first.** Engine fixes land on `latency-eval`, then port down. A
   fix that lands only on a clinic branch is a fix that will be lost.

**Deploy rights this week: Jules pushes anything he has verified**, including
Theorem. Verified means: full-suite diff clean, **and** a test call that
exercises the fix. **Name the revert target in every push.**

---

## Job 1 — SMS screenshots (interrupt-driven, highest priority)

Quentin will forward SMS screenshots as they arrive. Each one is a real call
that went wrong.

For each: find the call in the Render logs, work back from the SMS to the turn
that caused it, fix, port to the other branches if they share it, and verify
with a call.

**Do these first when they land** — they are real callers, not simulations.

---

## Job 2 — 50 adversarial calls (the main body of work)

**10–15 calls a day, ~50 total.** Not classical bookings. Those are known to
work and testing them again proves nothing.

The target is a caller actively making life difficult — the Jack Thompson shape:
sentences that trail off, answers to questions that weren't asked, corrections
mid-sentence, interruptions on every turn.

### How to run these

Ten scripted calls, each with **exact words**. Say them as written — the wording
is the test. Improvising a politer version tests nothing, because a cooperative
caller is the case we already know works.

Run all ten on `jv_v2`, then rotate: repeat the set with small mutations (a
different symptom, a different day, a heavier accent, faster delivery) to reach
10–15 a day. **The same script run twice is not a wasted call** — several of the
defects found today were intermittent and only showed on the second run.

Where a turn says *(interrupt)*, start talking **while Susie is still speaking**
— that is the point of the turn, not a stage direction.

### A1 — The interrupter

Never let her finish a sentence. Cut in mid-word, every single turn.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | *(interrupt the greeting at "I'm Susie, Joint—")* "yeah hi" | barge-in on a 1-word partial |
| 2 | *(interrupt her next sentence at ~1s)* "no listen, i need to see someone about me back" | teardown mid-synthesis |
| 3 | *(interrupt again)* "uh" — **then say nothing for 5 seconds** — then "sorry, it's been playing up for weeks" | ⚠️ known barge-in defect: teardown fires on the partial "uh", noise filter and TTS-resume only run on the final, 3s too late. A caller once said *"you got cut off"* |
| 4 | *(interrupt)* "just whenever, i don't care" | watchdog after a non-answer |

**Watch for:** Susie cut off mid-sentence and never resuming; the caller having
to repeat themselves; dead air over 3s after turn 3.

### A2 — The mind-changer

Accept, then un-accept, three times.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "hiya, can i book something" | |
| 2 | "it's me shoulder" | reason capture |
| 3 | "what've you got friday" | day-only hint — **the filler clip should arm here and currently does not** |
| 4 | *(after slots)* "yeah that one's fine" | |
| 5 | "actually no — hang on, what about monday instead" | slot cache clear, `v3_last_offered_day_iso` |
| 6 | *(after Monday slots)* "hmm. what've you got the week after" | |
| 7 | "no, go back to the friday one" | ⚠️ hallucinated-slot backstop — Friday slots are now stale. **She must re-check, not re-offer from memory** |

**Watch for:** a slot offered that no longer exists; being asked to accept the
same slot twice (duplicate-lookup defect); the wrong day written.

### A3 — The rambler

One 25-second sentence containing everything. Say it in a single breath, no pauses.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "right so it's me knee, i did it playing football saturday, i'm free tuesday or wednesday after four, name's Danny Whelan, oh and it's the left one" | soft-context extraction; name capture from a rambling turn; time parsing |
| 2 | *(if she asks anything she was already told)* "i just told you that" | ⚠️ re-asking a fact already given |

**Watch for:** her asking for the name, the day, or the reason after all three
were given. Check the diary name is `Danny Whelan`, not `Danny` or `Danny
Whelan Oh`.

### A4 — The non-answerer

Answer a *different* question every time she asks the reason.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "do you have any appointments" | |
| 2 | *(asked the reason)* "how much is it?" | FAQ mid-booking |
| 3 | *(asked again)* "do you do parking?" | |
| 4 | *(asked again)* "is Marcus any good?" | |
| 5 | "oh — me neck. it's me neck" | |

**Watch for:** the reason question asked **more than once in the same wording**
(it should latch after the first ask); a price volunteered that nobody asked
for; an answer followed by a second offer in the same turn.

### A5 — The mumbler

Every answer one word. **Leave 5–8 seconds of silence before each one.**

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "aye" | ⚠️ "aye" must register as yes — it was once deleted as mouth-noise |
| 2 | "back" | one-word reason |
| 3 | "nah" | |
| 4 | "go on then" | |
| 5 | "ta" | |

**Watch for:** a one-word answer dropped and re-asked. A short answer has to
survive **three** deny-by-default gates across two files — fixing one and
shipping leaves the others live.

### A6 — The correcter

Give a detail, then immediately correct it.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "morning, i'd like to book in" | |
| 2 | "it's me hip" | |
| 3 | *(asked the name)* "it's Steve — no sorry, Stephen. Stephen Marsh." | ⚠️ name correction mid-utterance |
| 4 | *(when the number is read back)* "no that's me old one" — then read out a **different** number, digit by digit | phone re-entry, DTMF |

**Watch for:** the calendar showing `Steve`, or `Steve No Sorry Stephen`. Two
live calls have written a wrong surname. **Check the diary, not the read-back.**

### A7 — The time-abuser

Numbers that are times, not durations.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "can i get in thursday at half four" | |
| 2 | "actually make it the 30th" | ⚠️ **must not** be read as a 30-minute session (fixed 11 Aug — this call verifies it) |
| 3 | "no — quarter past five, the 19th of September" | day-first date parsing; "September 19th" once resolved to 19 **August** |
| 4 | "and how long is it anyway" | |

**Watch for:** `[ms_conn v3] session length captured` in the log after turns 1–3.
**It must not appear.** If it does, the clock-time fix has a gap. Also check the
booked duration matches the service, not a number the caller happened to say.

### A8 — The impossible asker

Ask for things that cannot happen.

| Turn | Say exactly | Probes |
|---|---|---|
| 1 | "are you open sunday" | closed-day honesty |
| 2 | "have you got anything at eight in the evening" | ⚠️ outside working hours |
| 3 | "what about half nine at night" | |
| 4 | "alright, whenever then" | |

**Watch for:** a slot **outside the window you asked for**, offered with no
acknowledgement. A caller asked for 5:30–9pm and was offered half four with only
*"Does that work?"*. "We're closed then, but I've got…" is correct; silently
substituting is not.

### A9 — The withheld caller  ⚠️ never tested in production

**Dial `141` first**, so the number is withheld. Run twice.

| Call | Do | Probes |
|---|---|---|
| (a) | Book normally. When asked for a number, **type it on the keypad** | the withheld-caller booking path |
| (b) | Call back (141 again) and say "i need to move my appointment" | ⚠️ the reschedule fix shipped 11 Aug, **unproven by a call**. `lookup_patient` keys on phone, so with no caller ID there is no route to the appointment unless the keypad path arms |

**Watch for:** her offering to use "the number you're calling from" — there
isn't one. Any digits she reads back are invented.

### A10 — The quitter

Three separate calls. **Hang up without saying goodbye.**

| Call | Hang up at | Probes |
|---|---|---|
| (a) | the moment slots are read out | outcome labelling |
| (b) | after giving your name, before the number | orphan record |
| (c) | immediately after *"shall I go ahead and book that in?"* | drop-off ping |

**Watch for:** the `📊 Row built — outcome=…` line. A completed booking was
recently logged as `abandoned`, and that record is what a clinic reads to decide
whether to chase someone. If (c) produced a booking, the outcome must not say
abandoned.

### Polishing — free rein

Beyond the defect list, **Jules has open judgement to improve anything that
sounds wrong.** Quentin: *"this is a polishing of the system, you have free
range, I trust him."*

Specifically invited:

- **Filler phrases.** Too few and they repeat. `filler_guard` logs *"only one
  primary clip — every hold moment in every call will be the identical
  recording"* on every call. `scripts/synthesise_filler.py` cuts the variants.
- **Robotic wording.** *"That's a time preference noted"* is form-filling
  language. So is *"Number 1, nine in the morning."* when there are only two
  options and *"Either of those suit?"* would do.
- **Grammar that doesn't match the data** — *"I've a few others if **neither**
  suits"* when three slots were offered.
- **Anything that made you wince on the call.** If it sounded wrong to a human
  ear, it is wrong. Fix it, test it, port it.

Same rules: regression test, suite diff, and a call.

### For every call, record

- `call_sid`, clinic, and the `[build_info] running build <sha>` line at cleanup.
  **Without the sha the call proves nothing** — it may have run old code.
- What Susie *said* vs what she *should* have said.
- Whether the calendar write matches what she said out loud. **Check the diary,
  not the read-back.**
- The `📊 Row built — outcome=…` line, and whether it is true.

### Delete the test appointments

Every booked test call leaves a real event and schedules real reminders. Clear
them, or Marcus and Jonathan get reminder texts for patients who don't exist.

**Outstanding at handover (11 Aug):**

| Patient | Where | When |
|---|---|---|
| Jack Thompson | Theorem / Acuity | Fri 14 Aug 11:00 |
| Quentin Road | VE diary | Tue 18 Aug 12:00 (provisional) |

---

## Job 3 — `jv_v2` specifically

### 3a. Move the reason question to the start

Quentin wants "why are you coming in" asked **first**, right after the opening.
Today it lands late and inconsistently — on CAce1457d1 Susie asked for a day and
time *before* asking what the appointment was for, then doubled back.

Related: he likes how `latency-eval` offers to book straight after the caller's
opening query. **Both branches now run byte-identical engine code**, so any
behavioural difference comes from **clinic config**, not code. Start by
comparing `prompt_facts` between the clinic `latency-eval` serves and `jv_v1` —
do not change engine logic until that diff is understood.

`jv_v1` opts into the reason question via `prompt_facts.reason_question`. The
ordering lives in the template prompt's BOOKING STEPS block,
`app/prompts/clinic_template_prompt.py` — note `_spine()` takes no `session` and
lands in the **static, cacheable** half, so it cannot branch on call state.

### 3b. Configure the SMS on/off system

**The code is complete and already on `jv_v2`. Nothing in Python needs to
change.** A clinic texts `OFF` / `ON` / `STATUS` to their Susie number to switch
human-first routing (their phone rings first, 20s, then falls back to Susie).

Current state for `jv_v1` (`app/clinics/jv_v1/clinic.json`,
`operational.call_overflow`):

```json
{ "enabled": false, "dial_phone": "+447586605462", "ring_timeout": 20,
  "whisper_text": "…Press 1 to take it…", "greeting": "…" }
```

- All five authorised-number candidates resolve to **+447586605462** (Marcus's
  mobile), so he can already toggle it — no config change needed for that.
- The Redis override **beats** config, so with `enabled: false` Marcus can text
  `OFF` and get front-desk mode until London midnight, auto-reverting. That is
  probably the behaviour Quentin wants; setting `enabled: true` would instead
  make human-first the *resting* state and require a redeploy to change.
- Requires **Redis connected** — `set_mode` refuses the write otherwise and
  Marcus gets the failure copy.

⚠️ **`SMS_ENABLED` is OFF and Quentin is deliberately leaving it off — it burns
Twilio credit and he's away.** With SMS off, the toggle **writes the Redis key,
gets no SID back from the confirmation SMS, and deliberately reverts itself.**
So it cannot be exercised live this week.

Verify via the suite: `tests/regression/test_sms_call_mode_toggle.py` (23 tests).
`test_no_sid_reverts_override` is the direct proxy for the SMS-off path. If a
live test is genuinely required, message Quentin — **don't flip `SMS_ENABLED`
unilaterally.**

Also: **`EVAL_STAFF_SMS_TO` must be unset** before any live use, or Marcus's
confirmation gets redirected away from him.

### 3c. Open defects on `jv_v2` (all from CAce1457d1, 11 Aug, unfixed)

1. **Duplicate `check_availability`.** Caller accepted a slot; engine re-ran the
   identical lookup and read the same slot back — they had to say "that works
   for me" **twice**. ~24 seconds. Establish first whether the second call is
   the model re-deciding or the engine re-dispatching.
2. **A slot outside the requested window, offered silently.** Caller asked for
   5:30–9pm, was offered **half four**, Susie said only "Does that work?" —
   never acknowledging it wasn't what they asked for.
3. **The physio-knowledge line is generated then thrown away.** Quentin's "JV
   should know more about physio" complaint — **not** a knowledge gap:
   ```
   [ms_tts] pre-slot chunk suppressed — check_availability detected this turn:
            "I'm sorry to hear that — ankle problems can really stop you "
   ```
   The model produced the right line; `_pre_slot_cancelled` dropped it.
   ⚠️ Suppression is **engine-wide** (stops callers hearing partial text before
   the slot list) — a JV-only change needs a clinic gate, and thinking, not just
   deleting.
4. **The hold clip doesn't arm on a day-only hint.** Caller says "friday" →
   `timing_preference_known` is False → clip stays holstered → **3.05s of
   silence**. Anchor: `connection.py:~11614`, `expect_slot_presentation(...)`
   reads `time_of_day_preference` (set only from a time-of-day) or
   `v3_last_presented_date_hint` (only set *after* a presentation). Confirmed
   on Theorem too — engine-general.
5. **Robotic phrasing**: "That's a time preference noted — but could you tell
   me what…". "Noted" is form-filling language, not speech.
6. **`SHEETS_ENABLED` unset on `jv_v2` and `vitaledge-onboarding`** — neither
   writes any call record. Theorem proves the plumbing works. Env var per
   service, no code change. Quentin still needs to finish JV's sheet.
7. **`digest.email_to` empty on `jv_v2`** — only route from a calendar booking
   to a real Carepatron appointment. Needs a recipient + SMTP.

### 3d. Shipped 11 Aug but never exercised by a call

The **booking-outcome fallback** (`f4cbc92` and ported): when the turn that
should announce a booking produces nothing, the fallback now speaks the outcome
from the tool result instead of "Sorry, I didn't quite catch that". It fired
nowhere yet because it needs a provider stall to trigger. Persona 10 is the best
chance of reproducing it.

---

## Other branches — open items

- **Theorem** — a caller's osteopathy question was swallowed by the location
  intercept: they asked about osteo, Susie replied "did you mean Alcester?" and
  never answered. Also: four `b55` prompt-hash pins are **stale and permanently
  red**; each move needs attributing to a commit before re-pinning or deleting.
  And Quentin owes a decision on whether Mark should send 24h/2h reminders at
  all — Theorem currently sends them unconditionally.
- **Vital Edge** — `SHEETS_ENABLED` (above). Otherwise healthy at handover.
- **All** — the call-quality cluster (dead air on availability turns, barge-in
  cutting Susie off mid-sentence) needs an **audio harness** to reproduce
  properly. Building one unlocks three findings at once rather than three
  separate investigations; worth considering if the calls keep surfacing it.

---

## Branch state

### At handover (11 Aug, Quentin)

| Branch | Head | Notes |
|---|---|---|
| `latency-eval` | `24f19de` | canonical; **not** a live line, push freely |
| `jv_v2` | `85947b1` | live JV |
| `vitaledge-onboarding` | `4089adb` | live, Jonathan |
| `theorem-onboarding` | `05c990f` | live, Mark — **real patient traffic, most care** |

### Current (update as you push)

| Branch | Head | Notes |
|---|---|---|
| `latency-eval` | `34becd6` | + `request_callback` owner ping (CAc36368cbeb) |
| `jv_v2` | `85947b1` | unchanged since handover |
| `vitaledge-onboarding` | `becbaf0` | `request_callback` ported; Dylan remediate + live tool path proven |
| `theorem-onboarding` | `05c990f` | unchanged since handover |

Deliberate differences to preserve: `SMS_ENABLED`/reminders defaults differ
between canonical and the live branches; VE runs the diary reader; Theorem has
the location ladder and its own `theorem_v3` prompt; **`jv_v1.calendar_id` is
the live JV calendar on `jv_v2` and the demo calendar everywhere else** — never
normalise that one.

---

## Verification protocol

1. Worktree at the branch head, **copy `.env` in** (without it a different set
   of tests runs), `python -m pytest -q`, save the sorted failing set.
2. Apply, re-run, diff. The suite is meant to be red — the only acceptable
   result is an identical set, or one that shrinks for a reason you can name.
   At handover: **99** on canonical/jv_v2/VE, **103** on Theorem.
3. Every behavioural fix ships a regression test in `tests/regression/`, and you
   must **prove it fails before and passes after** — by removing only the wiring
   and leaving the symbols in place. Deleting the source only yields a
   collection error, which proves nothing.
4. Then the call. Then port. Then the call on the ported branch.

### Traps that have each cost time

- Prompt-hash pins are **per-branch** — the same test pins a different hash on
  each. Recompute on the target; never copy a value across.
- **Two** pin tables (`test_b55_…` and `test_b57_…`), each with its **own**
  `_sha` helper. Checking one with the other's helper produces plausible,
  entirely wrong numbers.
- `git checkout --theirs <file>` takes the **whole file** — on VE that destroys
  the diary reader. Resolve conflicts hunk-by-hunk in place.
- Theorem short-circuits to **Acuity** before the Google Calendar call sites, so
  anything added only to the gcal path never runs for Mark.
- `git stash` does not reliably revert in this tree (OneDrive locks). Back out
  by hand or the baseline is a lie.

---

## Corrections / progress log (Jules)

| Date | Note |
|---|---|
| 2026-08-13 | VE Dylan Wilson callback: waitlist SMS *did* deliver at call time; remediate was a duplicate. Shipped `request_callback` on `latency-eval` + VE. Live tool path proven on CA5956120c…; Twilio FR geo blocked `EVAL_STAFF_SMS_TO=+33…`. **Remove `EVAL_STAFF_SMS_TO` from live VE when done testing.** |
| 2026-08-14 | This file created from Quentin's handover text. |
| 2026-08-14 | Job 3c.4 in progress on `latency-eval`: day-only hint (`friday`) now arms the hold clip via `day_preference` + `_extract_day_preference`. Tests green locally; needs call proof + port. |
| 2026-08-14 | Test booking IDs located in obs: Jack Thompson Acuity `1752726653`; Quentin Road gcal `21jornld8dvoqk5nov9jl74d3g`. Cleanup script: `scripts/cleanup_handover_test_bookings.py`. |
| 2026-08-14 | Jack Thompson cancelled (Acuity needs `admin=true` — client cancel hit policy). Quentin Road already gone (`410 Resource has been deleted`). |
