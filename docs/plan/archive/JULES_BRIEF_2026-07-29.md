# JULES BRIEF — 2026-07-29, evening

**Supersedes** `JULES_THE_PLAN_TONIGHT.md` (28 Jul). Demo is **2026-08-05** — six days.

Budget for this session: **~2 hours.** Time-boxes are in each block. If a block
overruns, stop it and record why. Do not silently absorb overrun by skipping the
verification calls — the calls are the point.

---

## 0. Read this first — a process failure to not repeat

Four commits were pushed to `latency-eval` tonight and **auto-deployed to the
demo service before a single live call was made against them**. That was the
wrong order and it was my recommendation, not Quentin's.

Everything was verified statically — full suite diff, 656 recorded transcript
turns replayed through the old and new gate, 23 new tests. None of that is a
live call. The build is live now, so the fastest route to a validated state is
to test it where it stands rather than churn two more deploys. **That is the only
reason we are not reverting.** It is not a precedent.

**Rule for tonight: no further deploys without written authorisation from
Quentin, and no fix leaves your hands without the verification call that proves
it.** If you find yourself about to write "looks fine" — stop, and write down
what you actually heard instead.

---

## 1. What is deployed

Live since **21:10 BST**, health confirmed (`ok:true, redis:true`):
<https://low-latency-joint-venture.onrender.com/health>

| commit | what it does | risk |
|---|---|---|
| `554ebb4` | `scripts/detect_defects.py` + frozen baseline. Tooling only. | none |
| `503a06f` | GPT-fallback replies now pass through Gate 5 before TTS. | low |
| `801152a` | Gate 5 class rules — the A1 fix. | **this is the one to watch** |
| `4cb7273` | obs records what the caller HEARD, not raw model tokens. | low |

**Rollback if anything is wrong: use the Render dashboard**, not git. Deploy
list → `dep-d9k618bl550s73aortug` (`02b63a9`) → Rollback. Instant, no branch
churn. Do that first and analyse second.

### 1.1 `801152a` — precisely what changed

Three rules added to Gate 5 (`app/media_streams/turn_handler.py`):

1. **`markdown_emphasis`** — strips `*` characters inline. Markers only, never
   the sentence, so a readback keeps its content. Fixes a caller hearing
   "**Patient name:** Jewel".
2. **`internal_identifier_token`** — strips any sentence containing a snake_case
   token. A caller heard the literal string `slot_iso`. This is a class rule: it
   catches every tool and field name that exists now or is added later.
3. **Gate 5g, `_strip_self_narration`** — drops a sentence when it matches a
   deliberation pattern **AND** contains no second-person word **AND** is not a
   question.

Those last two conditions are load-bearing. These two lines share a phrase:

```
"I have everything I need to get that booked — shall I go ahead?"   KEEP
"I need to book this in now — I have everything I need."            DROP
```

A blunt pattern strips both, and stripping the first hands a caller a dead end
mid-booking. **That exact failure abandoned a completed booking in June 2026.**

**Measured blast radius:** all 656 recorded assistant turns replayed through old
vs new gate → **5 turns change, all 5 the intended A1 calls, 0 emptied.**
Reproduce it yourself with `python scripts/audit_gate5_blast_radius.py`.

### 1.2 `4cb7273` — a boundary that will bite your scoring

obs used to store the model's **raw** output. It now stores the **spoken** text.

> **Calls before 21:10 tonight are RAW. Calls after are SPOKEN. They are two
> different measurements.** Never diff a post-deploy defect count against the
> frozen 95-call baseline — a number falling could be the fix working or just the
> instrument changing.

Freeze a fresh baseline only from post-deploy calls, and say so when you do.

This root cause was already documented in `2d553b6` (28 Jul). I re-derived it
today and wrote it up as new. Flagging so nobody re-derives it a third time.

---

## 2. BLOCK 1 — Validation gate (35 min) ⛔ HARD GATE

Nothing else in this document happens until these two calls pass.

The risk is **not** a leftover reasoning phrase. It is **Gate 5g eating a
confirmation and abandoning a booking.** That is what you are hunting.

⚠️ **These book real Acuity appointments. Record every SID and cancel them all
before you finish.**

### Call V1 — clean booking, straight through

Ask for an appointment, give name, phone, reason, accept one of the offered
slots, let her confirm and book.

Record, per turn, in writing:
- the SID, and the wall-clock start time
- **verbatim** what she said at the confirmation step — not a paraphrase
- whether the booking landed in Acuity (check it, do not assume)
- any turn that ended sooner than you expected, or a question that never came

### Call V2 — change your mind once

Accept a slot, then **before** confirming, ask for a different time. Let her
re-offer, accept, confirm, book.

This is the path with repeat confirmations, so it is where Gate 5g has the most
sentences to chew on. Same recording discipline.

### Pass / fail

| | |
|---|---|
| **PASS** | both calls book, both confirmations are complete sentences, nothing truncated |
| **FAIL — revert now** | a booking is abandoned, a confirmation is cut short, or a turn ends with no question where one is needed |
| **NOT a fail** | you hear "Wait — I need to offer a slot first". Documented residual, Block 3 covers it |

Then score both:

```bash
python scripts/detect_defects.py --since 2026-07-29T21:10
```

And do the one check no test can do: **compare the obs transcript against what
you actually heard.** If they differ, `4cb7273` is not recording correctly, and
that matters more than any defect in the register — everything downstream reads
that record. Only someone who was on the call can verify this.

**If FAIL: roll back, write it up, stop. Do not forward-fix at this hour.**

---

## 3. BLOCK 2 — Validate A4 (30 min, no code)

A4 — "confirmation loop" — is **n=19 of 95 calls, 20%**. Four times every other
defect combined, and nobody has examined it once. Before a line is written we
need to know whether that 19 is real.

**The detector is crude and probably over-counts.** It flags any call where more
than one bot turn matches `shall i book|book that in|get that booked`. Asking
again after a caller correction is *legitimate* — V2 above will likely trip it
by design.

List them:

```bash
python scripts/detect_defects.py --since 2026-07-25 2>&1 | grep -A40 "^A4"
```

For each of the 19, read the transcript and classify into exactly one bucket:

- **REAL** — she asked to confirm, the caller said yes, and she asked again
  anyway. Caller confusion, no new information between the asks.
- **LEGITIMATE** — the caller changed something, so re-confirming was correct.
- **AMBIGUOUS** — say why, in one line.

Deliverable: a table of 19 rows, SID → bucket → one-line reason. That is the
whole deliverable. **Do not fix anything in this block.**

If REAL comes out high, A4 becomes the rest of the week and everything else
waits. If it comes out low, we withdraw the number and the register gets more
honest. Both are useful; guessing is not.

---

## 4. BLOCK 3 — A1 residual (35 min, then a call)

Gate 5g is a net, not a wall. Four phrasings still pass, confirmed by direct
test:

```
Wait — I need to offer a slot first.
I need to check what was agreed.
Let me review the call state.
Let me check what was agreed.
```

**The fix:** extend `_SELF_NARRATION_RE` in
`app/media_streams/turn_handler.py`. Nothing else. Do not touch the second-person
or question guards, do not move it to chunk level.

**Before you commit, run the over-strip audit** — this is not optional:

```bash
python scripts/audit_gate5_blast_radius.py
```

It replays all 656 real assistant turns through old vs new and **exits non-zero
if any turn is emptied**, so it works as a gate and not just a report. Current
state: 5 changed, 0 emptied. **If your change empties even one turn, it is
wrong.** If it changes more than ~8, you have caught something legitimate — read
the diffs and find out what before going further.

When you add a rule, add its name to `NEW_BANNED_RULES` at the top of that
script. Omit it and the audit silently compares the new gate against itself and
always reports 0 — a green light that means nothing.

Then:

```bash
python -m pytest tests/regression/ -q          # expect 869 passed
python -m pytest tests/ -q                      # expect exactly 95 failed
```

95 is the baseline and it is **meant to be red**. Verify by diffing the failing
set, never by looking for green. A different number — up *or* down — means stop.

### Verification call V3 (required, after the fix)

Book end-to-end again, and in the same call ask a question that makes her check
availability twice. You are looking for the same thing as V1: nothing truncated,
booking lands. Record the SID.

**Deploy only with Quentin's written authorisation.** If unauthorised, commit
locally and hand back.

---

## 5. BLOCK 4 — A2 evidence only (20 min, no fix)

A2 is recorded as n=3 and it is **more serious than that number suggests.**

```
CAcd8b36e198aa  25 Jul 23:11  "All booked — you're in for Wednesday the 30th of
                               July at half past five"     30 Jul is a THURSDAY
CAfe6a41626d0b  27 Jul 02:43  "All booked — you're in for Friday the 1st of
                               August at six in the evening"  1 Aug is a SATURDAY
CAaf76d3b0983e  27 Jul 23:53  "Saturday the 9th of August"    9 Aug is a SUNDAY
```

The wrong day-name **survived all the way to the post-booking confirmation.** If
the calendar event is on the date and the caller heard the day, the caller shows
up on the wrong day — or not at all. That is a missed patient, which is the top
line of the production-ready bar in `CLAUDE.md`.

**Why the existing guard did not catch it.** `sanitise_response` has a booking-
readback date enforcement, but it (a) only runs once `phone_confirmed` is set,
and (b) forces the spoken date to `v3_confirmed_slot_phrase`. So if the wrong
day-name is already in the confirmed slot phrase, **enforcement copies the error
forward rather than correcting it.**

Your job in this block is the diagnostic that tells us which of two things is
happening — do **not** write the fix:

1. **Is the day-name generated by us, or free-texted by the model?** Find where
   the slot phrase "Wednesday the 30th of July" is constructed. If we build it
   from a date, the day-name derivation is wrong and it is a small deterministic
   fix. If the model writes it, it is a gate/enforcement fix.
2. **Was the Acuity event on the date or on the day-name?** For `CAcd8b36e198aa`,
   look up the actual booking. This tells us whether the caller was told the
   wrong day for a correct booking, or the booking itself moved.

Deliverable: which of (1) it is, with the file and line; and the answer to (2).
That determines the fix and I will write it against your evidence.

---

## 6. Hand-back (10 min)

Write `docs/plan/JULES_HANDBACK_2026-07-29.md` containing:

- every SID, with wall-clock time and outcome (booked / abandoned / other)
- the V1/V2 gate verdict, and the obs-vs-heard comparison result
- the 19-row A4 table
- A1 residual: audit numbers before and after, commit SHA if you committed
- A2: the two answers from Block 4
- anything you could not finish, and why

### Standard of evidence

- **Verbatim or nothing.** "She confirmed correctly" is not evidence. The
  sentence she said is evidence.
- **Every claim carries a SID.** A finding without one cannot be checked.
- **Say what you did not do.** An untested path recorded as untested is fine.
  An untested path implied as tested is how four planning documents ended up
  contradicting each other last week.
- **If code and this document disagree, the code wins** — and record the
  correction in `docs/plan/README.md`. These documents have been wrong at least
  four times already, including twice by me today.

---

## Priority if you run out of time

1. Block 1. Non-negotiable. An unvalidated build on the demo number six days out
   is the worst state to leave this in.
2. Block 2. Largest unknown in the register.
3. Block 4. Highest severity per instance.
4. Block 3. Real, but the mild half of a defect whose worst forms are fixed.

Skip from the bottom, never the top.
