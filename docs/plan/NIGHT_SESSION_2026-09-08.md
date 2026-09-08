# Night session — 8 September 2026

`latency-eval` = **`648ad5a5`** (pushed). `production` = **`08e99fab`**,
untouched — no live line has been deployed to, and nothing here has had a call.

Everything below came out of one call: `CA4215ab7f`, theorem_v3, 01:15, build
`08e99fab`, reported as *"horrible behavior … clearly not the same behaviour as
on the demo line"*.

---

## 1. What shipped to the demo line

| commit | |
|---|---|
| `8b97f1e4` | Gate 5 stood down on a fresh three-day list because the accepted THREE o'clock folded to a bare `3` and matched `"Number 3"` |
| `73a8c973` | tests making the bare-hour guard earn its place |
| `88e0df08` | the same bug through a second door: clinics with `speak_part_of_day: false` — **northgate** — where the o'clock label IS a bare number |
| `4efbdc95` | **the other stand-down never recorded what it spoke** |
| `3875c629` | the P6b replay harness + the 8 Sep call sheet |
| `648ad5a5` | the replay result, and a defect it did NOT find |

**Full suite: 96 failed, 9026 passed.** The failing-set diff against the 94-row
baseline is **two additions and zero losses**, and both additions are
`tests/auto/test_acuity_live.py` — live-Acuity smoke tests that fail
**identically at the baseline commit** (`Slot` has `start_time`, the test asserts
`start`). Verified by running them in a worktree at `0d7cb329`. **Zero
regressions.**

### The answer to "are these bugs also on latency-eval?"

It was the opposite of the guess. northgate is **worse**, and my first fix did
not cover it. `speak_part_of_day: false` — shipped to northgate the afternoon
before — makes every o'clock label a bare number, so the FULL label is the bare
number, matched by the first comparison and never reaching the fallback that
`8b97f1e4` guarded. The wording change made the defect reachable through a
second door on exactly the clinics it was made for.

### The second defect, which outlives the first

`_flush_slot_buf` has **two** branches that suppress the deterministic offer and
speak the model instead. B-134 taught P6 to record the slots its sentence spoke.
**P6b was never given the same treatment.** On the live call six genuine payload
times were read out and none recorded, so "let me if the last one works for me"
resolved against the previous offer — the slot he had already accepted.

`8b97f1e4` stops P6b firing on that call and does not close this: a confirmation
may also carry an alternative (*"that's Wednesday at three, or I've also got
Friday at nine"*), which names the accepted slot, fires P6b **correctly**, and
strands the alternative identically.

---

## 2. Evidence, not just tests

`scripts/replay_p6b_stand_downs.py` — new. Scores the P6b decision over 921
stored calls; the existing harness could not, because everything it measures
takes CALLER text and this takes the MODEL's.

```
333 measurable decisions
stand-downs ADDED   (was False, now True): 0
stand-downs REMOVED (was True, now False): 7
```

All seven read individually: one is the reported call, three are a genuine
Wednesday→Tuesday confusion the guard is right to refuse, three are harness
artefacts. **No confirmation is lost.** Detail in
`docs/plan/P6B_REPLAY_2026-09-08.md`.

That document also closes a defect this **nearly manufactured** — a first scan
claimed 12 day-mismatched confirmations across all four clinics as recently as
5 September; a scanner bug. The honest count is 2, both from the first week of
August, both fixed on 2 August. Please read that section before re-opening
anything the replay output points at.

---

## 3. Two things checked and deliberately NOT changed

**The multi-day lead-in (~1.8 s, LAT-1's last piece).** Measured over the
corpus: 357 multi-day readouts, and in **252 of them (71%) a preamble had
already been spoken** — "Let me see —", "Let me see what we have available…" —
immediately before "Here's what we've got coming up —". Two preambles back to
back. But **105 (29%) have none**, so dropping it unconditionally makes a third
of readouts start cold on "Number 1, Monday 7th September".

Doing it properly means telling the formatter whether a hold phrase was just
spoken (`_hold_head_spoken` is the signal). That is a **pacing** change, and
pacing cannot be judged from a test — it needs an ear. Scoped, not shipped.

**`UNKNOWN_SLOW` apologising on a turn that answered.** Not reproducible as the
audit states it. Over the corpus: `"Sorry, still with you —"` appears 92 times
and is **alone in its turn every single time** — 0 instances glued to an answer.
The non-apologetic head appears 89 times, 22 of them with an answer attached.
So the apology never lands on top of a successful answer in the way the row
describes. Whether the apology is the right tone at 3.5 s is a judgement for you,
and belongs with the hold-speech review that has been outstanding since it went
live.

**One correction against myself:** I recorded the new `"I've got a few days —"`
lead as dead code because it has 0 corpus instances and did not fire in my first
test. Both were wrong — the corpus predates it, and my test had exactly
`max_days` days so there were none left over to be "a few". It is live and
correct with four days. I checked before changing it, which is the only reason
this is a footnote rather than a fifth commit.

---

## 4. What needs you

1. **The two calls in `docs/plan/CALL_SHEET_2026-09-08.md`** — Theorem against
   production as it stands (turn 4 is the reproduction), then the demo line for
   all three fixes. Theorem's Acuity path has now been unexercised for six days
   and carries three unverified changes.
2. **§2.5, the surname gate** — warn-only or block the write. Still yours.
3. **The hold speech**, still never heard by a practitioner.

Nothing here reaches a patient until you promote, and I would not promote before
Call A.
