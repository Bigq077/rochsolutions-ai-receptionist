# Pre-demo fix queue — 2026-07-26 → Hands On Money demo, Wed 29 July

Three days. Ranked by one question only: **does it break the demo booking call?**

The demo is a person phoning up and booking an appointment while ~100 clinics
watch. Everything below is ordered by how directly it damages that call, not by
how interesting the defect is.

Sources: Jules's 2026-07-25 sweep (`SUSIE_SWEEP_2026-07-25_FINDINGS.md`), the
14-call clinical campaign (`SUSIE_CAMPAIGN_LOG.md`, F-014…F-036), the 25 captured
obs rows, and the verification call `CA4969580082db5e757c3b1d04dd38e7ae`
(2026-07-26), whose transcript is quoted throughout — it is the cleanest example
we have of a *successful* booking and it still contains four defects in sixteen
turns.

> **Status at 15:30 Sun 26 Jul.** `origin/latency-eval == d60041d`, in sync, so
> **A4 and D1 are live**. Two further fixes are **built and tested but NOT yet
> committed or deployed** (collection gate, CONFIRM_PHONE bare-yes). Everything
> else below is open.

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
[16] ASSISTANT | I've just sent you a confirmation text.             ← A4 (no text was sent) ✅ CLOSED
```

Two separate `"i said"` from the caller in one booking. That is the sound of a
demo going wrong.

---

## What the 25 Jul sweep adds that was not in this queue

Read `SUSIE_SWEEP_2026-07-25_FINDINGS.md` in full; these are the items that
change the plan.

1. **Verbal phone capture mangles the number — and books it anyway.** C6:
   caller said `"07700 900123"`, stored `7009001230`. This is the **second**
   occurrence: F-024 (CALL 7, 22 Jul) booked `01392255` — eight digits, not a
   phone number. No length or format validation anywhere, and no digit readback
   on the verbal path. → new item **A6**, and it is the most *severe* thing on
   this page even though it is not the most demo-likely.
2. **Duration asked 2–3× (C6, C7), and C7 got lost in the loop entirely.** Pairs
   with F-034 (*"shall I go ahead and book that in?"* asked 3× — caller: *"yes
   it's the third time you've asked me"*). → new item **A5**.
3. **C3c is a safety miss, not just an STT miss.** `"calf"→"call"`, no screen —
   and then the caller *volunteered* "had surgery" and there was **no
   escalation**; `outcome=abandoned`, not `safety_escalation`. D2 was written as
   a transcription problem; it is also a missed-escalation problem. Needs a
   listen-back to confirm what the model did.
4. **The sweep is PARTIAL and every PASS from call 2 on carries an asterisk** —
   C23 failed, so calls 2–15 were run in compressed delivery. 9 cases never ran.
   Aggregate over 15 calls: **6 watchdog fires, 5 backstop arms, 5 question-less
   dead-ends**. That is the endpointing tax, measured.
5. **The gate is green:** C25 and C6 both created real calendar events. The
   booking spine completes end-to-end. Protect it.

Also carried over from the clinical campaign and **still open** — not new, but
absent from this queue and demo-relevant:

- **F-035** — filler `.ulaw` clips missing from the deploy →
  `[filler_guard] clip not found: audio_clips/filler_checking.ulaw` → clinical
  turns run 3–4 s of **dead air**. Flagged in the log as the best low-risk
  weekend fix. Cheap: it is an asset add. **Verify it is still true first.**
- **F-036** — the booking-confirmation SMS path logged *"already sent"* with no
  `SMS_ENABLED is off` line. A4's fix reads the env var, so it is consistent by
  construction, but **confirm no SMS actually leaves the service** before the demo.
- **F-021** (wrong service booked, was 4/4), **F-017** (deterministic screens
  don't arm), **F-028** (invented price, partial fix), **F-029** (cauda
  over-fires on "behind my back"), **F-019** (surname dropped from the summary),
  **F-020** (first complete DTMF entry discarded).

---

## Block A · The collection sequence — **the single highest-value work**

The defects live in one stretch of conversation: slot → reason → name → phone →
confirm. Fix the sequence, not six separate bugs.

### A1 · Phone: confirm, never ask · `BLOCKER` · **half closed**
**Observed:** turn 12 asks *"Is the number you're calling on the best one… just
say use this number"*, then turn 14 says *"I already have your number
confirmed."* She had it from caller ID the whole time.

**Two faults.** (a) A turn is spent asking for something already known. (b) The
caller must utter a magic phrase — *"use this number"* — to accept it. Jules had
to say it verbatim in rows 6, 22, 23; where he tried to read a number aloud
instead (rows 17, 19, 21) the call ran 150–261 s and ended in
`"yeah we were in the middle of a fucking booking"`.

**(b) is CLOSED** (built, not yet deployed). Root cause found 26 Jul: `5c7ea4e`
(24 Apr) replaced yes/no phone confirmation with explicit phrase commands and
deleted bare *yes/yeah/yep* from the CONFIRM_PHONE accept list; `3bbe4f0` (10
Jun) reversed that on the LLM path only. On the deterministic path a caller who
answered *"yes"* hit `HARD GATE CONFIRM_PHONE: ambiguous 'yes' — tight re-ask`,
re-asked **with no retry counter and no escalation, forever**. Bare `no` was
matched; bare `yes` was not — on a yes/no question. Fixed behind
`phone_confirm_armed`; regression test `test_confirm_phone_bare_yes.py`.

**(a) is OPEN and is the remaining work.** Read it back, don't request it:
> *"I've got you on 07502 211207 — is that the best number for the booking?"*

A plain yes/no — which the gate now accepts. Only fall back to verbal capture on
"no", and prefer keypad entry there (see **A6**). Nine strings carry the old
wording: prompt Step 8 (`clinic_template_prompt.py:2038`), the PHONE STEP
OUTSTANDING steer (`:2452`), flow steps 3/8/11 (`flow.py:2683/2735/2764`),
`phrases.py:244`, and three silence re-asks (`connection.py:2483/3631/4147`).

### A2 · Reason before slots, and never book without one · `BLOCKER` · **backstop built**
**Observed:** slots offered at turn 4, reason asked at turn 6 *after* the caller
had started choosing, never answered, and the booking completed anyway with
`collected.reason = None`. Same on call 25 the night before.

**Why it matters more than it looks.** On a physio line the reason drives
appointment type, duration and price. A booking with no reason means the clinic
receives an appointment it cannot prepare for, and Susie cannot have booked the
right service because she never knew what it was.

**Backstop CLOSED** (built, not yet deployed): `_exec_book_appointment` now
refuses without a reason, resolving `args.reason → session.reason →
collected.reason` and committing it both ways so the call record carries it. An
optional `reason` argument was added to the tool schema so the model can satisfy
the gate from what it collected — without it the gate deadlocks, because after
the first turn nothing on the LLM path writes `session["reason"]`.

**OPEN:** the prompt still asks the reason *after* `check_availability`. Move it
before. The backstop stops a reasonless booking; it does not fix the ordering.

### A3 · Name in one pass · `WATCH`
**Observed:** "first name and surname" → `"rock"` → "And your first name?" →
`"i said that would be quentin"`.

Related and worse across the sweep: "Tom Green" transcribed as `home green`,
`hung green`, `homegreen` (rows 17, 21, 23, 24); and **F-019** — the booking tool
got "Quentin Rock" but the persisted name and summary row kept "Quentin" only.
Whether `Rock` here is a mis-hear of *Roche* is unconfirmed — **ask Quentin what
he actually said** before treating it as STT.

### A4 · Do not claim a text was sent when none was · `BLOCKER` · ✅ **CLOSED**
`d60041d`, deployed. The closing line now reads the same `SMS_ENABLED` env var
the send path gates on, and when SMS is off the model is explicitly told never to
claim a text is coming. 17 regression cases across both flag states.
**Still verify F-036** — that no SMS leaves the service on a real booking.

> Related, separate: `confirmation_sms_sent` is overloaded — the provisional
> path sets it to *suppress* a send, so `success` is partly derived from a
> "deliberately didn't send" flag. Wrong for a different reason; not urgent.

### A5 · Stop asking the same question three times · `BLOCKER` · **NEW**
**Observed:** duration asked 2–3× (C6, C7); C7 lost in the loop entirely,
refusal behaviour unclear. F-034: *"shall I go ahead and book that in?"* asked
3× — after the slot, after the surname, after the phone — caller: *"yes it's the
third time you've asked me"*.

**Root cause (F-034, to confirm):** the confirmation is re-triggered at each
collection step by the `surname_required` and phone gates instead of being asked
once, after all details are gathered. Worsened by the turn boundary (C1).

**Wanted:** one confirmation, after the last collection step. Any gate that
rejects a `book_appointment` must steer the model back to the *missing item*,
not to the confirmation question.

> ⚠️ **This interacts with the new tool-boundary gate.** The gate adds two more
> `success: False` paths, and F-023 (LLM fabricating *"All booked"* on a tool
> rejection) was **intermittent** before `8631fc3` closed it. Re-verify Gate 5f
> holds against the two new refusals — that is a call-test item, not a code item.

### A6 · A phone number that isn't a phone number · `BLOCKER for cohort`, `WATCH for demo` · **NEW**
**Observed twice:** `"07700 900123"` → `7009001230` (C6, 25 Jul);
`01392255` — eight digits — booked with no readback (F-024, CALL 7, 22 Jul).
Related **F-020**: a complete 11-digit DTMF entry was discarded and a *different*
number got booked.

This is the worst failure class in `CLAUDE.md` §6.1: the call sounds perfect, the
booking exists, and the patient is uncontactable. It is low-probability on a
rehearsed demo (the caller will accept the caller-ID number) and near-certain to
recur across a 230-clinic cohort.

**Wanted, cheapest first:** (1) the demo script never reads a number aloud —
free; (2) validate at the tool boundary: a phone that does not normalise to a
valid UK number is refused with a steer to keypad entry — the collection gate is
already the right place; (3) digit-by-digit readback on the verbal path — the
playbook already requires it and it would have caught both instances.

---

## Block B · Perceived speed

### B1 · The slot readout is too long · `BLOCKER` · **the cheapest big win**
Six options across three days in one breath — measured at **24.1 s** on the
worst turn, 16.1 s on the 2026-07-25 test call, where the caller hung up nine
seconds later.

**Wanted:** offer two, then ask. *"I've got Wednesday at seven, or Tuesday at
five — either of those work?"* This is a content change, far cheaper than a
latency engineering pass, and probably recovers most of the perceived slowness.

**Do this first on Sunday.** It is the only item that damages *every* turn of
the demo and it is a prompt edit.

### B2 · Turn latency · `WATCH`
`ttfa p50 1923 / p95 3659 / max 10107 ms` against a 1500 ms bar; **76% of turns
over**. Real engineering, not a three-day job. B1 is the part that fits.

Cheap adjacent win: **F-035**, the missing filler `.ulaw` clips — clinical turns
currently run 3–4 s of silence where a filler should play. Asset add, low risk.
Verify it is still failing on the current deploy before spending time on it.

---

## Block C · Natural speech

### C1 · Endpointing (C23) · `BLOCKER for confidence, not for the demo`
A ~2 s mid-sentence pause is treated as end-of-turn. Jules ran calls 2–15 in
compressed delivery because of it, which means **every PASS in his sweep carries
an asterisk**. Fixing it is what makes the whole sweep re-measurable. Measured
cost over 15 calls: 6 watchdog fires, 5 backstop arms, 5 question-less dead-ends.

Judgement call: a rehearsed demo caller can speak in compressed bursts. So this
is the highest-value fix for *knowing where we stand*, and not strictly required
for the demo itself. Sequence it after Block A, **timeboxed** — see Monday.

---

## Block D · Clinical safety

Screening is a selling point to a room of clinics, so these are demo-relevant
even though a demo call is unlikely to trigger them.

### D1 · `trauma_fracture` trigger keywords · ✅ **CLOSED**
`0fd1961`, deployed. Added the mechanisms that were missing — *went over on,
gone over on, turned/twisted/rolled/done my [ankle|knee|wrist|shoulder],
sprained* — as config only, with 26 regression cases including S1a and C5A
verbatim. Two loose forms (`rolled my`, `went over`) were measured as false
fires and are pinned as negatives so nobody widens recall by reintroducing them.

### D2 · `calf` → `cough` / `call` · needs a new approach
Confirmed three times (rows 13, 14, 15) **with `calf` already in the keyterms**.
The 24 July keyterm fix did not solve it, so retrying it is not the answer.
Layer 1 cannot arm a DVT screen on a word that never arrives.

**Upgraded by C3c:** this is not only a missed screen. The caller volunteered
*"had surgery"* and there was no escalation — `outcome=abandoned`, not
`safety_escalation`. Listen back to C3c before deciding the fix.

Mitigation to consider: treat the known mis-hears as aliases on the trigger side
only — never on the answer-classification or emergency side.

---

## Closed

- ✅ **A4 · false confirmation-text promise** — `d60041d` (26 Jul), deployed.
- ✅ **D1 · trauma mechanism keywords** — `0fd1961` (26 Jul), deployed.
- ✅ **A1(b) · CONFIRM_PHONE rejected a plain "yes"** — built 26 Jul, **not yet
  deployed**. Unbounded re-ask loop; root-caused to `5c7ea4e`/`3bbe4f0`.
  Correction recorded in `README.md` #13 — `TEST_BASELINE.md` called this drift;
  it was a defect. Baseline is now **95**, not 96.
- ✅ **A2/A1 backstop · tool-boundary collection gate** — built 26 Jul, **not yet
  deployed**. `book_appointment` refuses without a reason or without
  `phone_confirmed is True`.
- ✅ **`booking_confirmed` never set** — `55451e0`. Verified live on
  `CA4969580082db5e757c3b1d04dd38e7ae`.
- ✅ **Orphan screen detection** — `2485229`, validated in production
  (`dvt ORPHAN×1, ARMED×0`).
- ✅ **Truncated safety answers** — `188e478`.
- ✅ **Twilio recording duplicate (21220)** — `c7ef0fd`, confirmed gone.
- ✅ **Screening + booking id captured in obs** — `ba195e8`, `55451e0`.

---

# The schedule

**Governing principle (from `DEMO_COUNTDOWN.md`, unchanged):** every code change
is a deploy that must be re-validated, so **code stops Monday**. Tuesday is
clean runs and freeze. Wednesday is confidence, not changes.

> **Correction:** an earlier version of this queue put D2 on the day before the
> demo. That contradicts the freeze. **Monday 27 is the last code day.**

**Open question that drives Tuesday:** what hour is the demo on Wednesday?
Tuesday's clean runs must be at that time of day.

## Sun 26 — from 15:30 · fix window
| Time | Work | Notes |
|---|---|---|
| 15:30 | **Deploy what is already built** — commit + push the collection gate and the CONFIRM_PHONE fix as two commits, one deploy | Both are tested; sitting uncommitted helps nobody |
| 16:00 | **B1** — two slots, then ask | Prompt edit. Biggest visible win per minute spent |
| 16:45 | **A1(a)** — read the number back instead of asking for it | Nine strings, listed under A1. The gate already accepts the plain yes this asks for |
| 17:30 | **A2 ordering** — ask the reason before `check_availability` | Prompt edit; the backstop is already behind it |
| 18:15 | Push as one deploy · wait for Render green · **do not call before it is green** | |
| **~19:00** | **CALL WINDOW — 1 verification call**, natural delivery, happy-path booking | |

**Sunday gate:** one booking call with **no `"i said"` from the caller**,
`collected.reason` populated, ≤2 slots offered per turn, the phone read back and
accepted on a plain "yes", and no promise of a text. If that call is clean,
Sunday is done — resist adding more.

## Mon 27 — **LAST CODE DAY**
| Time | Work | Notes |
|---|---|---|
| Morning | Fix whatever Sunday's call exposed — first priority, always | |
| Morning | **A5** — one confirmation, after the last collection step | Includes re-verifying F-023's Gate 5f against the two new gate refusals |
| Midday | **A6(2)** — reject a non-UK-format phone at the tool boundary, steer to keypad | Small; the gate is already there |
| 13:00 | **C1 endpointing — TIMEBOX to 16:00.** Not landed and validated by 16:00 → drop it and coach the demo caller to speak in bursts | This is the one item that can eat the day. Do not let it |
| 16:00 | Last push of the cycle · Render green | After this, code is done |
| **17:00–20:00** | **CALL WINDOW — clean-run candidate #1**, full matrix, **natural delivery** | If C1 landed, this is the first sweep whose passes are not asterisked |

**Monday gate:** three consecutive clean bookings in natural delivery, or a
punch-list short enough to clear before lunch Tuesday and containing nothing
structural. **Confirm Jules has stopped touching `latency-eval`.**

## Tue 28 — clean runs → FREEZE
No code unless a clean run fails — and then it is not a clean run.

| Time | Work |
|---|---|
| Morning | Clean run #2 |
| **Demo hour** | Clean run #3 **at the demo's time of day** |
| After #3 | **FREEZE** — tag the commit, record both SHAs (frozen + rollback target), tell Jules |
| Afternoon | **Record the fallback call** — a clean end-to-end booking to play if the live line dies |
| Afternoon | **Operator rehearsal ×2** — whoever demos runs the script themselves |

**Tuesday gate:** 3 clean runs logged · frozen commit tagged · fallback recorded
· rollback command in pocket · operator comfortable.

## Wed 29 — DEMO
- One **smoke call in the morning**: line up, deploy green, calendar reachable.
- In pocket: the fallback recording, the frozen SHA, the rollback command.
- **Zero changes.** The temptation is "one more small fix". It is the single
  highest-risk move available.

---

## Call protocol — run every verification call this way

1. **Never call within 5 minutes of a push.** Wait for the Render deploy to go
   green, or you are testing the old build and will draw the wrong conclusion.
2. **One caller at a time.** One number, one session store — two people calling
   at once produces garbage logs and false defects.
3. **Natural delivery from Monday on.** Compressed delivery measures the turn
   boundary, not the case. Every asterisked PASS has to be re-earned.
4. **After each call, check obs, not memory:** `outcome`, `booking_confirmed`,
   `calendar_event_id`, `collected.reason`, `collected.phone`, `screening`. A
   call that *sounded* fine and wrote `reason=None` is a failure.
5. **Score it in the sweep doc the same hour.** Unscored calls become opinions.

## Deploy protocol

- One deploy per fix cluster, not per commit. Each deploy costs a validation call.
- Push, wait for green, then call. Keep the previous known-green SHA in hand
  before every push, not after.
- From the Monday 16:00 push onward, treat any further push as an incident,
  not a plan.

## What not to do

- **Do not run the remaining 9 sweep cases** (C7b, C18, C24, Block 3) in
  compressed delivery. They are the most turn-boundary-sensitive in the matrix,
  so they produce passes that will not survive a real caller. Re-run a natural
  delivery set after C1 instead — or not at all, if C1 is dropped.
- **Do not chase B2 as an engineering project** before the demo. B1 is the part
  that fits in three days.
- **Do not script an ambiguous multi-service request into the demo.** F-021
  (wrong service booked) is open and its remaining variant is semantic — the
  model picks the wrong service at `check_availability` and the booking guard
  faithfully books it. Mitigate by script, not by code, this week.
- **Do not read a phone number aloud in the demo** — see A6.
- **Do not start C1 without the 16:00 Monday timebox.** It is the item most
  likely to consume the last code day and leave nothing validated.

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
