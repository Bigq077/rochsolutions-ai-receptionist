# Call sheet — deterministic multi_day slot presentation

**Date:** 2 September 2026
**Branch:** `slots/multi-day-gate-2026-09-01` (worktree `C:\Users\quent\susie-slots`)
**Cut from:** `origin/latency-eval` @ `7d6837cf` — **3 ahead, 0 behind, clean fast-forward**
**Commits:** `c42bfebf` (gates) → `ae7f45bf` (pure) → `b7bde9e5` (wiring)

**What changed in one line:** on a multi-day availability readout, Python now
writes the sentence, the record of what it named, and the keypad map — together,
from the payload — instead of a model writing the sentence and the system trying
to parse it back.

**Why:** measured over the stored corpus on 1 Sept, **51 of 52 multi_day
readouts (98%) hand the positional resolver a DAY-only label**. It never failed
occasionally; it could not succeed.

---

## 0. Scope — which lines this actually touches

**Do NOT test this on Theorem. It cannot fire there.**

| Line | Executor | Reaches `_cap_presented_slots`? | Covered |
|---|---|---|---|
| Northgate (demo, **+447366263180**) | generic google_calendar | yes | ✅ |
| Joint Venture (**+447367002651**) | generic google_calendar | yes | ✅ |
| Vital Edge | `_check_availability_diary` | yes | ✅ |
| **Theorem (Mark)** | `_check_availability_acuity` | **no — returns at `receptionist_tools.py:5902`** | ❌ **untouched** |

Theorem's executor caps its own spoken list into `available_days` and never
writes `presented_days`, so the branch condition is false. Mark's line keeps the
model-composed sentence and the whole repair layer. Pinned by
`test_an_acuity_shaped_result_does_NOT_fire_the_branch`.

**One line is enough to verify.** This is shared engine code in
`llm_stream.py`; the build SHA proves the same code is on the others. Use the
demo line.

---

## 1. Deploy

Out of hours. `autoDeploy` is on — **a push is a deploy that reaches real
patients.**

```bash
git -C C:/Users/quent/susie-slots fetch origin && git -C C:/Users/quent/susie-slots log --oneline HEAD..origin/latency-eval
```

That must print **nothing**. If it prints commits, someone pushed overnight —
rebase before going further, do not merge.

```bash
git -C C:/Users/quent/susie-slots push origin slots/multi-day-gate-2026-09-01:latency-eval
```

**Rollback target — write it down before you push:**

```
7d6837cf
```

To revert: `git push origin 7d6837cf:latency-eval --force-with-lease`

---

## 2. Deploy proof

`/health` returns a hardcoded 1.0.0 and proves nothing. In the Render log for
`vitaledge` (`srv-d8va6cbtqb8s73fbpvag`), at call cleanup:

```
[build_info] running build <the SHA you pushed>
```

Get the SHA you are looking for with:

```bash
git -C C:/Users/quent/susie-slots rev-parse --short HEAD
```

(Not `b7bde9e5` — that is the wiring commit, and this sheet was committed on
top of it, so the branch tip is later.)

Also expect, in the first seconds of boot:

```
[deploy] SMS_ENABLED=… | APPOINTMENT_REMINDERS_ENABLED=…
```

`(DEFAULT)` on a live service means someone forgot. Not caused by this change,
but check it while you are looking.

---

## 3. The calls

All on **+447366263180**. Say the caller lines verbatim.

### Block 1 — the multi-day readout itself  ← **the one that matters**

> "Hi, I'd like to book an appointment please."
> — *(when asked when)* — "I don't mind, whenever's soonest."

"I don't mind" is what produces `date_hint: any`, which is what produces
multi_day. If you get a single-day readout you have not tested this change.

**PASS**
- Two days offered, **one time each**, as numbered options:
  *"Here's what we've got coming up — Number 1, Monday 7th September — ten in
  the morning. Number 2, Tuesday 8th September — nine in the morning. Either of
  those work?"*
- The two days are named clearly and the times belong to the right days.
- No "a few others that day".

**FAIL**
- A time named against the wrong day.
- The same day twice.
- Any day or time you were not read out.
- Dead air after "Here's what we've got coming up".

**In the log, expect:**
```
[ms_gate5] deterministic multi_day offer built: 2 chunk(s), 2 slot(s) recorded
[ms_gate5] deterministic offer in force — 2 chunk(s); the model's N buffered chunk(s) are discarded
```
The second line is the point: the model still ran and its words were thrown away.

> ⚠️ **LISTEN FOR THIS FIRST — the one deliberate caller-audible change.**
> Live multi_day readouts today are bimodal: about half are 2 days × 1 time and
> half are 3 days × 2 times, because the model has been reading `available_days`
> and ignoring the cap. **Every multi_day readout now becomes 2 days × 1 time** —
> `_cap_presented_slots`' actual cap. If two options sounds too thin on a real
> call, the fix is **one line**: `per_day` in `_cap_presented_slots`
> (`receptionist_tools.py:5296`), which moves the model path identically and
> keeps one owner. **That is your decision, not a defect.**

### Block 2 — press a digit

Immediately after block 1, **press 1**.

**PASS** — she takes Monday, the day she read as Number 1, and moves on to the
time or to your name.
**FAIL** — she takes the wrong day, or asks what you meant.

The keypad stays **day-keyed** on multi_day, deliberately: `1` means
"Monday 7th September", not a time. That is what it meant before this change too.

### Block 3 — "what else have you got?"

After block 1, say: **"What else have you got?"**

**PASS** — the times she offers next are **different** from the two you just
heard.
**FAIL** — she re-reads a time she has already given you.

This is the B-116 property. It is why the change is fed `presented_days` and not
`available_days`, and it is the thing that would break if someone "simplified"
that later.

### Block 4 — single_day did not move

> "Do you have anything on Wednesday?"

**PASS** — a normal single-day readout, up to three times, exactly as it has
been since `7d6837cf`. This block exists only to prove step 4 did not disturb
step 3.
**FAIL** — anything different from last week.

### Block 5 — book it through

Take an option from block 1, give a name and number, let it book.

**PASS** — the calendar entry's **time and duration** match what you were told.
Check `event created` against the call's `duration=Nm`.
**FAIL** — any mismatch between the spoken time and the diary.

Cancel the test booking **through Susie**, not in the calendar.

---

## 4. Known open, not a defect

**A truncated payload now speaks two options with no hint more exist.** With the
tail suppressed (B-99: "a few others that day" names no day after a multi-day
readout), a caller hearing two options is not told the diary holds more. 50 of
the 52 corpus readouts carry no tail today, so this is close to current
behaviour — but it interacts with the 2×1 cap above. If it reads badly, the fix
is a **day-plural** sentence ("I've also got other days if none of those suit"),
verifiable from `more_times`. **Never "that week"** — that is a referent claim
the payload does not support.

---

## 5. Two follow-ups, each with its own gate test and its own call

Neither is in this deploy. Both are small.

1. **Decision B — the more-times tail on multi_day**, day-plural wording, driven
   from `more_times`. Gate: the sentence appears only when the payload says more
   days exist, and never names a day.
2. **Decision C — `v3_last_offered_day_iso` adopts `SlotOffer.first_spoken_date`
   on both paths.** The divergence is real and already pinned by a passing test
   (`test_the_payloads_first_day_is_not_the_first_day_spoken`). It is deferred
   because `CA6e1024db` ran four turns with the staleness gate blind for a whole
   call the one time a reader reinterpreted that scalar, and four readers sit on
   it. `first_spoken_date` is built and tested, ready for that change.

A third, larger one: **extend step 4 to the Acuity executor** so Theorem gets it.
That is where the plan doc's "24% unresolvable" actually lives.

---

## 6. If something is wrong

Every failure mode above falls back the same way: the `try/except` at both build
sites logs

```
[ms_gate5] deterministic multi_day offer failed — falling back to the model's presentation
```

and the call continues on the old path. A caller mid-booking does not lose the
offer to a formatter fault. If you see that line on a real call, the deploy is
still safe — send me the SID.

Full revert is section 1.
