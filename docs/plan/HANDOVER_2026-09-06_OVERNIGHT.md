# Handover — 6 September 2026, overnight

Three defects from the 5 Sep 23:05–23:14 demo-line calls, fixed and pushed.
Read `OPEN_DEFECTS_2026-09-06.md` first for how they were found.

---

## 0. State in one screen

| | |
|---|---|
| `latency-eval` | **`ca23c0a8`** — three fixes on top of the evening state |
| `production` | **`e2c3c2c5`** — **3 commits behind, deliberately.** Revert target. |
| Suite | **97 failed / 8601 passed / 22 skipped** |
| Baseline | 97 failed / 8521 passed at `e2c3c2c5`, run in a pristine worktree |
| Failing set | **byte-identical**, diffed with digits preserved, 97 = 97 |
| Worktree | `C:/Users/quent/AppData/Local/Temp/claude/b127-latency-eval` |

+80 passing = the three new regression files (26 + 26 + 28). No test was
edited, deleted or re-pinned.

> The first two baseline runs were **contaminated** — a background `pytest` was
> still collecting while I was neutering files for the red-then-green checks,
> and it produced three phantom failures (`under_age` ×2, `stall_phrases`).
> Both numbers above come from runs with no concurrent edits, in two separate
> worktrees. A regression-only run on each tree gives the same 4 known
> failures. **Do not run a baseline and edit at the same time.**

---

## 1. What shipped

| SHA | What a caller notices |
|---|---|
| `4886f865` | **B-145** — "yeah Monday works" now gets Monday's times from a producer, three of them, with the "a few others that day" tail and a keypad pointing at TIMES |
| `6ef2d3f5` | **B-145b** — a caller who has just chosen is never told "let me look" |
| `ca23c0a8` | **B-146** — "can I have a sports massage" gets "Let's get you booked in —" at 600ms instead of "Sorry, still with you —" at 3s |

Each was proven by **neutering it and watching the tests go red** — three
neuters for B-145 (6 / 9 / 1 tests), one for B-145b, one for B-146 (9 tests) —
and each failed only its own file.

### `4886f865` — B-145, and the three symptoms it closes at once

`named_day_speech` answers a day REQUEST from the payload. It declines an
ACCEPTANCE on purpose, and that decision is right — a caller who accepts must
be acknowledged, not read a list. But nothing else built the offer, so after
"Monday it is —" the **model** narrowed the day in prose: 2 of 12 times, no
tail, keypad still on days.

`day_acceptance_speech` is the other half of that decision. Same two functions
the request path uses — `build_slot_offer` + `apply_offer_to_session`,
extracted into `speak_one_day_from_payload` rather than copied, so there is
still exactly one answer to "what did she just offer?".

The acknowledgement moves with the answer: this producer replies before the
streaming call and returns, so the head is rendered here through
`render_intent_head`, gated on `hold_speech_enabled`. A clinic that has not
opted into hold speech gets the offer and no head.

> **Also fixed, because the producer makes it dangerous.** `_DAY_ACCEPT_RE`
> matches `works?`, so **"monday doesn't work" was an ACCEPTANCE**. That cost a
> wrong head while a head was the only consumer; behind a producer it costs
> Monday read out to a caller who has just refused Monday. Any negator now
> declines, wholesale.

`_slot_presentation_mode` is deliberately untouched — it records what the last
`check_availability` decided, not what the last offer said, and no producer
writes it. The D-B path leaves it reading "multi_day" after narrowing too.
Separate concern, recorded not fixed.

### `6ef2d3f5` — B-145b, and why a fourth reader was needed

All three existing inputs to `slot_selection` **resolve**. Each can decline for
a reason that has nothing to do with whether the caller picked, and on
23:10:24 all three did. B-145 stops that particular state arising; this stops
the next decline, whatever produces it, from promising a lookup again.

Deny-by-default, and in the direction this family must not fail: an acceptance
word, no negator anywhere, corroborated by a DAY or a CLOCK TIME. **A band
alone is excluded** — "mornings work better" is a preference whose lookup
really does happen, and deleting that head would be a regression. Pinned.

Asked only while `v3_dtmf_slot_map` is set — guard on the MAP, and a test pins
that so a later edit cannot quietly move it to the flag.

### `ca23c0a8` — B-146, and what it deliberately does not touch

`Intent.BOOK_NEW` could only see `book|booking|appointment`. Adding "massage"
to the trigger is the trap this file warns about twice; the matcher SHAPE was
right and the corroborator was the half that was blind. The service verdict is
now asked of the engine — the same `_is_treatment_specific_booking` that writes
`v3_treatment_mentioned` — read off the **utterance**, never off that
call-scoped latch.

First person only. "can I have a sports massage" is a request; "can you do
sports massage", "how much is a sports massage" and "i'd like to know about
sports massage" are questions, and the head would promise work nobody asked for.

**It does not touch `booking_flow_active`.** That flag reaches the write gates
and its FAQ false-positive is BUG-7, an owner-signed decision. Whether that
sentence should also open the booking flow is a separate call and was not made.

---

## 2. Call before promoting

`production` is 3 commits behind on purpose. Every change here is caller-
audible on the most common turn in the call, so it wants ears on it first.

1. **The acceptance.** Book, let her read three days, say **"yeah Monday
   works"**. Expect: *"Monday it is —"* then **three** Monday times and *"and
   I've a few others that day"*. Then **press 1** — it must select the first
   TIME she just said, not Monday.
   Log: `[slot_followup] 'Monday 7th September' answered from the payload … (B-145)`.
2. **The refusal, immediately after a readout: "Monday doesn't work."** She
   must NOT say "Monday it is —" and must NOT read Monday out. This is the
   negation hole, and it is the one thing in this batch that could go wrong
   loudly.
3. **The pick.** After the Monday times, say **"ten past five in the evening
   suits"**. Expect silence, then the confirmation. She must NOT say *"Let me
   see what I've got in the evening —"*.
4. **The band preference must survive.** After a readout, say **"mornings work
   better"**. She SHOULD still say *"Let me see what I've got in the morning
   —"*, because that lookup really happens. If that head has gone, B-145b is
   over-suppressing and I want to know.
5. **The service request.** Open the call with **"can I have a sports massage
   please"**. Expect *"Let's get you booked in —"* inside a second. Then a
   separate call: **"how much is a sports massage"** — she must NOT say it.
6. **D-B is unchanged** — "check for Tuesday" must still work exactly as it did
   on the 23:05 call. It shares the extracted producer now.

Then fast-forward `production` and make one real call. Revert target
**`e2c3c2c5`**; the only proof of what is running is `[build_info] running
build <sha>` in the Render log.

---

## 3. Still open from those calls — NOT fixed

- **The 30-minute session was heard as "the 5-minute session"** (call 5,
  23:13:22), so she re-asked the 30/60 question and the caller got a second
  "Still with you —". The keyterm list carries **no numerals**. This is the STT
  lever (§2.6 of the 3 Sep register) and it is blocked on pulling a wav off
  Render — every replay harness works on transcripts, not audio.
  Adding "5-minute" to the duration vocabulary would be wrong: he never said it.
- **`UNKNOWN_SLOW` still apologises on a slow turn that ANSWERED a question
  Susie just asked.** That is the general form of the two "Still with you —"s
  in call 5, and it is a change to the fallback itself rather than to a
  matcher. Deliberately not attempted overnight.
- **Reason `None` reached the record on call 5** — northgate never asked, and a
  service name is not a reason. Harmless on the demo line; on JV that is the A2
  gate.
- B-31's 200-char cap, and the 17–19s readouts. Both known, both §2.7/§2.8.
