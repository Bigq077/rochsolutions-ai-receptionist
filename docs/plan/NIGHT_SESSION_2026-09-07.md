# Overnight session — 7 September 2026

Worked from `PLAN.md` (65e8d79f). Everything is on **`work/night-2026-09-07`**,
branched from `406310a3`. **Nothing has been pushed to `latency-eval` or
`production`.** The instruction was that no fix reaches a live line without a
live call, and I cannot place one, so the branch is staged and waiting.

---

## 0. The two-line version

The P12 fix existed and was correct about its own defect. Merging it as written
would have shipped **three new wrong-slot defects**, because it was written on
2 Sep and the tree moved on 4–6 Sep. All three were caught before landing — one
by reading the merge, two by the replay harness that arrived in the same branch.

The plan's advice to adopt `4179e248`'s P8 fix wholesale was **wrong**, and the
test that proves it is in this branch.

---

## 1. What landed on the branch

| SHA | What |
|---|---|
| `5f4df3cb` | replay harness (`scripts/replay_slot_decisions.py`) |
| `3a4c7f3a` | harness clock-label fix |
| `1067602a` | **P12** — she reads "Number 2" and could not resolve "number two" |
| `94d6e4fd` | regression test for the two guards P12 needed |
| `3de15b2c` | **P8** — name WHICH closure shut the day, without losing the cause |
| `824cf112` | **replay-found** — a caller spelling their surname was booked in |

Also done, no code: `fix/p8-availability-cause` and `fix/asap-sparse-rota` are
now **on `origin`**. They existed only on this machine.

---

## 2. What the merge would have shipped, and did not

### 2a. B-138, twice, through P12's new door

`d1189be0` is dated 2 Sep. **B-138 landed 4 Sep.** The cherry-pick conflicts in
`slot_followup.py`, and the naive resolution re-opens it:

* its `if not date and single_day_offer: date = offered_dates[0]` duplicates the
  last-resort branch B-138 guards, but runs FIRST and unguarded — making
  `_names_a_different_weekday` unreachable;
* its positional early-return jumps the ladder entirely.

Measured on the unguarded merge:

```
"have you got number two on wednesday"  ->  2026-09-08T16:20:00
                                                 ^ a TUESDAY
```

That is B-138 verbatim — a question about one day resolving as acceptance on
another — reached by ordinal instead of by time. Both now decline, and
`_time_contradicts` is asked on that exit too, as step 3 already does.

### 2b. Three shapes that were never positions

Found by the harness over **905 stored calls**, not on a phone. None is
reachable before P12: a position used to select a DAY, step 3 then found no time
under it, and these resolved to nothing **by accident**.

```
"um yeah quentin roch um the last name is spelled r-o-c-h"  -> the LAST slot
"actually what's the soonest you've got"      (7 turns)     -> the FIRST slot
"i didn't catch that last bit can you repeat yourself"      -> the LAST slot
```

`_FIRST_POSITION_RE` carries `soonest`; `_LAST_POSITION_RE` carries `last`; both
matched as bare words with nothing checking they were being USED as positions.

These are the **wrong-slot** kind, not the no-slot kind — the read-back is
generated from the pin, so the caller hears a confident confirmation of a time
they never chose. Fixed with shape rules, never longer word lists.

### 2c. What the corpus caught inside the cure

Rule three was first written as `Intent.REPEAT_ASK in classify_intent(text)` —
reuse rather than a second matcher, which is the standing rule here. Replay
priced it: lost resolutions went 6 → 10, and the four extra were **real picks**:

```
"yeah i said 9 in the morning works"
"i said quarter past six please"
```

`REPEAT_ASK` also matches "i said", which there means the caller is RESTATING —
and restating is how a caller re-asserts a pick Susie missed. Denying it reads
them the list again: the P6 symptom, delivered to the callers who had already
hit it once. Only the didn't-hear half is taken.

**Not hearing is a request. Repeating yourself is not.**

---

## 3. P8 — the plan's recommendation was wrong

`PLAN.md` says `4179e248` is "strictly better" than production's `caa2514a`. It
is not. Each has one half.

* `4179e248` counts removals per filter, so a bank holiday and a closed weekday
  get their own codes and a full breakdown. **Real gain, adopted.**
* But it attributes the cause to whichever filter removed the **most**. On a day
  the clinic is shut, where a late-morning caller loses four slots to the
  two-hour window and one to the closure, it reports `lead_time_limited`.

That is P8 itself, surviving inside the fix written for P8. Demonstrated by
neutering this branch's gate back to it:

```
"There are 5 slot(s) available at Alcester today but all start within
 2 hours - too soon to book."        ... on a day the clinic is CLOSED
```

The other seven P8 tests pass while it does that, so adopting it as advised
would have shipped it quietly.

`after_lead_time` settles what the totals cannot: slots that SURVIVED the
lead-time filter prove lead time is not what emptied the list. So production's
gate stays, and the counts are used only for the wording — which is all they can
honestly support.

Codes `closed_that_day` → `closed_on_day` / `bank_holiday`. Nothing in the
engine reads the old name: `flow.py` switches on `lead_time_limited` and routes
the rest through "or any other error", and the live clinics are free-form and
read `error_detail`, not the code.

---

## 4. Replay result, against `406310a3`

905 calls, 1971 turns after a readout, 1960 scored.

| | |
|---|---|
| turns **gained** a resolution | **38** — every one a genuine pick |
| turns **lost** a resolution | **6** — every one a request or a rejection |
| pick **changed** to another | **0** — the dangerous direction is empty |

All six losses read as requests on inspection ("what's the soonest slot you
have", "can you repeat the last day that you offered"). All 38 gains are
ordinary picks ("The first one", "um the second one", "okay that first slot
sounds great").

---

## 5. Closed by evidence: `fix/asap-sparse-rota`

`PLAN.md` lists it as "needs triage — may already be covered". **It is covered.**
Not by `git log`, which lies across cherry-picks here, but by content:

* `caller_wants_soonest` short-circuit — present, `slot_followup.py:3152`
* `acknowledge_sparse_rota` — present, `slot_followup.py:3397`
* `_caller_requests_different_location` — present, `llm_stream.py:1120`
* `tests/regression/test_asking_for_sooner_does_not_offer_later.py` — present

`git diff 0dfcf11e 406310a3` is +796/−4 on `slot_followup.py`, and none of the
four removed lines is asap-related. Production has the branch and more. **The
branch is spent — delete it whenever you like; it is on `origin` now.**

---

## 6. The 17-second readout — measured, deliberately NOT changed

A real three-day offer, built by the real producer:

> Here's what we've got coming up — Number 1, Tuesday 8th September — ten to
> nine in the morning, or twenty past four in the afternoon. Number 2,
> Wednesday 9th September — quarter past nine in the morning, or ten past five
> in the evening. Number 3, Thursday 10th September — eight in the morning, or
> half past three in the afternoon. Any of those work?

**66 words**, ~20–25 s at normal TTS rates — consistent with the 16.8 / 17.0 /
18.9 s measured on 6 Sep.

Where it goes: `"in the morning|afternoon|evening"` appears **six times — 18 of
the 66 words, 27% of the readout, ~6 seconds.** The date labels are another 9.

**I did not touch it, and I recommend not doing it blind either.** Two reasons,
and the second is the hard one:

1. Length is a product decision. `MULTI_DAY_MAX_DAYS = 3` × `TIMES_PER_DAY = 2`
   is six options; cutting to two days is a third off, and that is a call about
   what Susie offers, not a bug fix.
2. **The spoken label is the string the pick-resolver matches against.**
   `slot_accepted_by_caller` step 3 does containment against `slot["spoken"]`.
   Shorten "twenty past four in the afternoon" and you change what a caller has
   to say to book it. That is the same coupling that produced tonight's
   defects, and it must not change without a live call.

The safe version is worth a session of its own: shorten the label AND re-score
with the replay harness before anything is spoken.

---

## 7. What still needs a human

| | Item | Why |
|---|---|---|
| **Gate** | one call to **+447366263180**: let her read numbered slots, say "number two" | staging and production are identical, so any push is one fast-forward from three patient lines |
| **Debt** | one real Theorem booking through the Acuity path, cancelled via Susie | still exercised by no commit; the one place a silent booking failure can hide |
| Decision | 2.2 false completeness (B-97 vs B-99), 2.5 surname gate | blocked on a decision, not on work |
| Review | a practitioner hears the hold speech | live on all four lines, heard by none of them |

---

## 8. Notes against myself

* I killed a full-suite run believing it had hung. It had not — `pytest > file`
  block-buffers, so a live run looks identical to a dead one. `python -u`
  removes the ambiguity and I should have used it from the start.
* `tests/auto/test_acuity_live.py` really does hang sometimes: a live network
  call with no timeout, which is the repo's own known hazard biting a test
  rather than a caller. Deselected for the comparison run and subtracted from
  the baseline set.
* I lost a set of edits to `git checkout --` mid-neuter. The repo's own rule —
  commit before any before/after comparison — exists for exactly that, and I
  had read it that same night.
