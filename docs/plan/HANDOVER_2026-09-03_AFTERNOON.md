# Handover — 3 Sep 2026, afternoon

**`production` = `ae97af1e`.** Promoted 13:20 after three verifying demo calls.
**Revert target: `eb6c8e4e`.**

`latency-eval` carries the afternoon's work on top. Nothing further has been
promoted — that is yours to do once you have made a call.

---

## 1. What went to production at 13:20

Nine commits, verified by calls at 13:06 / 13:10 / 13:12:

| verified live | |
|---|---|
| `'uh yeah monday works'` → `situational head (slot_picked): 'Monday it is —'` | 3b |
| `'uh what about tuesday'` → the **lookup** head, not "Tuesday it is —" | 3b's safety case |
| `'actually can you do wednesday'` → the lookup head | second phrasing, unplanned |
| `'uh just my left ankle'` / `"uh it's my knee"` → SYMPTOM head, no pain word | §2.3 |
| no duplicate apology on either call | — |

**The transfer call looked like a failure and was not one.** northgate's
transfer target is the handset that rings it, so `_usable()` refuses to dial the
caller back to themselves. Identical before and after the change —
`realtime.py`'s own docstring describes this exact case from 2026-08-29 — and
the recovery was correct: *"I can't put you through right this second… or take
a message"*. **A transfer on the demo line cannot be dial-tested from the target
handset. Use a second phone.**

---

## 2. What is waiting on `latency-eval`

### `979f6fb8` — Phase 1b, the generated-diary sweep

`scripts/sweep_slot_offer.py`. Phase 1's original replay design could not run:
obs held transcripts and not availability payloads until this morning, and that
column is forward-only. So rather than wait weeks for a corpus, this
**generates** the payload space — a clinic diary has finite structure — and
crosses it with generated caller utterances.

**528 generated diaries, zero violations.** Seven invariants, not expected
strings, so the wording stays free to change.

**It is proven to fail**, which is the only thing that makes a green sweep worth
reading. Blinding `_time_contradicts` — restoring the 8pm defect — produces 68
MERIDIEM violations including the live log line verbatim:

```
'yeah monday at 8 pm works' -> 2026-09-07T08:00:00+01:00
```

That defect reached two real callers before a phone call found it. The sweep
finds it offline in seconds. A pytest runs the bounded version on every commit.

> **The argument for generating rather than replaying, in one line.** The third
> defect of the week needed a day offering BOTH 08:00 and 20:00, where the
> meridiem is the only thing separating two labels that fold to the same digit.
> **The corpus contains no such day at all.** No replay could have found it.

### A time-only pick after the day is settled

Found on your own call 1 at 13:07:24, and it is the promised-work defect
reached by the most ordinary answer a caller can give:

```
13:07:12  'uh yeah monday works'        -> 'Monday it is —'
13:07:14  "Monday the 7th — I've got eight in the morning or ten past five…"
13:07:24  'um 10 past 5 in the evening works'
13:07:25  situational head (time_band): "Let me see what I've got in the evening —"
13:07:27  "So that's Monday the 7th of September at ten past five in the evening"
```

She promised a lookup and then confirmed instead. `slot_accepted_by_caller`
step 2 requires a DAY, and "10 past 5 in the evening" names none — so the pick
resolved to nothing, `_hs_picking` stayed false, and the TIME_BAND diary head
fired.

Declining was right in general and wrong there: **with one day on the table
there is nothing for the day to be.** The ladder gets a fourth and last step,
the offer's sole date, and it fires only when the offer holds exactly one.

Deny-by-default is untouched where it matters: a multi_day offer still needs the
day named. Guessing there would pin a slot on the wrong DAY, which is worse than
declining and is what the whole ladder exists to prevent. Both directions are
tested.

**One existing test was re-aimed, not rewritten to fit.**
`test_the_day_is_still_required_on_its_own` asserted `"20 past 12"` declines —
and its own docstring said why: *"the fold below is necessary but not sufficient
and **the day half is a separate defect**"*. That separate defect is now closed,
so the test points at the answer instead of the gap, and a new test keeps the
invariant it was really protecting (two days on offer → still declines).

---

## 3. Before you promote this

**One call to `+44 7366 263180`:**

1. "I'd like to book an appointment" → *(asked what it's for)* → "my knee"
2. "Next week" → readout
3. **"Yeah Monday works"** → expect *"Monday it is —"*
4. **"Ten past five in the evening works"** → expect **silence**, then the
   read-back. It must **not** say *"Let me see what I've got in the evening —"*

Step 4 is the new one. Silence there is the correct outcome — the 30 Aug
decision gives band-only picks silence, and the point of this change is that a
false promise is replaced by nothing rather than by a head.

Then `git push origin <head>:production`, revert target **`ae97af1e`**.

---

## 4. Still open, unchanged

* **§2.2 the false completeness claim** — *"the slots I have that day are…"*
  naming 2 of 12. **Needs your decision**, B-97 vs B-99.
* **§2.5 the surname gate** — **needs your decision**, warn-only or block.
* **§2.8 verbosity** — readouts still 17s, the largest caller-experience item.
* **B-127** — a barge-in destroys an answer. Substantial engine work.
* **JV `hold_speech` flag** — one key, its own call to `+44 7367 002651`.
* **Theorem's Acuity booking path** — exercised by none of this week's commits.
  Outstanding since 2 Sep.
* **B-31** — mis-diagnosed, deliberately untouched. Nothing truncates
  `last_bot_prompt`; `_LAST_BOT_PROMPT_CAP` is a reader-side assumption and the
  real mechanism is that the prompt holds one CHUNK while the question is in
  another. The behaviour is right and the explanation is wrong.
* **A second private-number default** — `triage_legacy.py:3826` defaults
  `THEOREM_NOTIFICATION_SMS` to Mark's staff number. Exempted in the grep test
  with its reasoning; the other two readers of that var already default to
  nothing.

---

## 5. Where Phase 1 stands now

| | |
|---|---|
| **1a** utterance replay | **DONE** — `05c3de1c`, and it found three defects on its first run |
| **1b** payload synthesis | **DONE** — `979f6fb8` |
| **1c** capture the payload | **DONE and in production** — `calls.slot_offers` |

1c is accumulating a replayable corpus from this morning onward. In a week
there will be enough stored offers to diff a change against real traffic rather
than generated shapes, which is what makes 1a worth building then rather than
now.

**Phase 2** — one producer, one record — is unchanged and is the next real
piece of work. It is a deletion refactor across ~30 pinned test files and wants
a full day, not a session.

---

## 6. Phase 1a, added after the handover was first written

`scripts/replay_day_picks.py` runs the day-pick discrimination over every
stored caller turn that follows a numbered readout. **828 calls, 157 such
turns, 18 scored ACCEPT — and three of those were REQUESTS:**

```
"yeah check for tuesday please"
"yeah i'll ask for you to present tuesday the 8th"
"what do you mean monday the 10th works"
```

Each scored ACCEPT only because "yeah" opens it and no request pattern matched.
The first two ask for a LOOKUP, so "Tuesday it is —" in front of them is the
promised-work defect — the exact failure 3b's design exists to prevent,
arriving through phrasings no test author reached for. **None had reached a
caller**: 3b shipped this morning and these turns are historical.

Fixed in the same commit; after it, 15 ACCEPT and all read as genuine.

> **Neither half of Phase 1 could have found these alone.** The generated sweep
> makes DIARIES; these are failures of LANGUAGE. The sweep in turn found the
> `am_and_pm_8` case, which the corpus does not contain. They answer different
> questions, and now that both exist it is worth not conflating them.

One asymmetry, deliberate: **"check" is blocked outright, "see" is not.** A
caller accepting a slot has no reason to say "check", but *"uh let's see that
saturday slot please"* is a genuine acceptance in the corpus and a bare "see"
would have eaten it. Both directions are pinned by tests taken verbatim.

### And 1c is verified in production

Two calls already carry `slot_offers`, captured since the 13:20 deploy:

```
mode=multi_day  payload_times=63  presented_times=6  named=6  more=True
```

The diary held **63** bookable times that week; Susie named **6**. From
transcripts alone the visible ratio was "2 of 12" — the real one is 10:1. That
is §2.2 quantified for the first time, and it needed no call to measure.
