# Overnight runbook — 2026-09-10
**Base:** `fc47e508` · **Constraint:** nobody is awake to make a call.

---

## 0. The one rule that shapes everything else

**No phone tonight. Therefore `production` is FROZEN.**

The standing rule is *a real call after any engine change*. Nobody can make one
until morning, so nothing engine-shaped reaches a patient line tonight. The
furthest anything travels is `latency-eval`, which serves the demo line only
(+447366263180) and no patients.

```
production     fc47e508    DO NOT PUSH. Not once, not "it's only a docs commit".
latency-eval   fc47e508    the only landing zone tonight
```

If a task cannot be fully verified **without a phone**, it does not land — it
gets built, pushed as its own branch, and left unmerged with a note. That is a
successful outcome, not a failure. `feat/n5-third-rung` is the template.

**Revert targets, if something has already gone out and needs undoing:**

| undo | reset `production` to |
|---|---|
| the clinical fix only | `c5d24da6` |
| everything from 9 Sep | `5f1003c9` |

---

## 1. What is already true — do not re-verify

Verified by call on build `8e838f0f` (CA5e14516b, 9 Sep 23:08):

- clinical acknowledgement is general, no invented symptoms
- a named weekday returns that weekday
- a caller who asks for twelve is offered ten past twelve
- a different-day request re-looks-up rather than reusing the standing offer

These are done. Spending the night re-proving them is the main way to waste it.

---

## 2. The verification loop — non-negotiable

The suite is **red on purpose**. Do not look for green; diff the failing sets.
Current: **97 failed**, failing-set md5 `6b563465`.

```bash
# BASELINE — a SECOND worktree at the base commit, never touched
git worktree add /tmp/base fc47e508
cp .env /tmp/base/.env            # a tree without .env runs DIFFERENT tests
cd /tmp/base && python -m pytest -q -p no:randomly > /tmp/base.txt 2>&1
grep -E "^FAILED " /tmp/base.txt | sort > /tmp/B.txt

# CANDIDATE — freeze the tree, then run
md5sum <every file you changed>   # BEFORE
find . -name __pycache__ -type d -prune -exec rm -rf {} +
python -m pytest -q -p no:randomly > /tmp/head.txt 2>&1
md5sum <every file you changed>   # MUST equal BEFORE
grep -E "^FAILED " /tmp/head.txt | sort > /tmp/H.txt
diff /tmp/B.txt /tmp/H.txt        # must be EMPTY
```

1. **Never run the suite in a worktree you are editing.** This happened twice on
   9 Sep. Both times ~55 `inspect.getsource` tests failed — it reads the file
   from disk but resolves the block from the already-imported code object's line
   numbers. It reads as catastrophe and is an artefact.
2. **Grep `^FAILED ` only.** `^(FAILED|ERROR)` also catches log lines and
   silently pollutes the set.
3. **Reproduce any suspected regression in isolation before believing it.**

### Slot changes also need the replay

```bash
cd /tmp/base   && python scripts/replay_slot_decisions.py --out /tmp/BASE.json
cd <candidate> && python scripts/replay_slot_decisions.py --out /tmp/CAND.json
python scripts/replay_slot_decisions.py --diff /tmp/BASE.json /tmp/CAND.json
```

~1,845 turns. Read by **direction**: a pick changing to a *different* slot is
dangerous; a pick *lost* is usually a guard working.

**Its blind spot matters tonight.** obs stores speech, not payloads, so
`remaining_unspoken_on_current_day`, `choose_presented_indices` and
`all_remaining_on_next_day` are **NOT covered** — and T1 below is inside it. For
those, replay against `calls.slot_offers` (120 calls, forward-only from 3 Sep):
pull `slot_offers[].payload[].slot_times`, cross them against real caller
utterances, and assert **exact-hit-nearest-missed == 0**. That method caught a
fix about to make things *worse* on 9 Sep, before it shipped.

---

## 3. Traps — every one of these cost real time on 9 Sep

**A change can ship completely inert.** Three times in one day:

- `\b` written into a regex as a literal backspace byte (`0x08`) — compiled
  fine, matched nothing
- a third hold phrase added to `WorkKind.UNKNOWN_SLOW` — silently refused,
  because `_second_filler_text` rejects a candidate whose `head_families`
  intersect the phrase just spoken
- new config constants defined but never imported — `NameError` at 16s inside a
  background task, where it may never reach a log

**Test BEHAVIOUR, never presence.** `assert "X" in source` proves nothing. Drive
the real function and assert what comes back. All three above passed a
presence-style check.

**Prompt hashes live in TWO tables under different names.** Any edit to
`clinic_template_prompt.py` moving `jv_v1` fails both:

```
tests/regression/test_b55_provisional_reschedule_closing.py   UNCHANGED_CLINIC_PROMPTS
tests/regression/test_b57_theorem_cancel_gate.py              UNMOVED_PROMPTS
```

Each has its **own** `_sha`. Recompute per table; never copy a value across.

**A safety property can live in an idiom.** `len(hits) == 1` also silently meant
"decline when the same time sits on several days". Replacing that code kept the
named meaning and lost the implied one — a caller who asked for Wednesday was
offered Monday.

**Enabling a dormant path exposes guards that were never load-bearing.** Before
making a path live, grep what it will newly reach for `Tier 2` / `out of scope`
/ `needs its own corpus` / `nothing reads this`.

**`git stash` does not revert here** (OneDrive locks) — back changes out by
hand. Use `git -C <path>`, never `cd X; cmd`.

---

## 4. Tonight's work, by whether it can be verified without a phone

### 🟢 GREEN — fully verifiable offline. May land on `latency-eval`.

---

#### T1 — Identical times read out for every day  ← the one that matters

**Anchors**
- `app/tools/slot_followup.py:3715` `choose_presented_indices` — the ONE owner
  of "how many, and which", four readers on it
- `app/tools/slot_followup.py:2068` `spoken_starts_for_offer`
- `app/tools/receptionist_tools.py:286` `_spoken_starts_for`

**Exhibit** — CA5e14516b, 9 Sep 23:08, judge 2, `dead_end` + `booking_error`:

```
caller: "do you have anything wednesday around 12"
Susie : Wednesday 16th — eight in the morning / ten past twelve / twenty past four
caller: "what about monday at 12"
Susie : Monday 14th   — eight in the morning / ten past twelve / twenty past four
```

**Diagnosis is done — do not re-derive it.** "Already heard" is a set of
**dated ISO starts**, so `2026-09-16T08:00` and `2026-09-14T08:00` are different
members and B-116's "prefer times not yet heard" never carries across days.
northgate's grid is uniform 50-minute steps, so every day reads identically.
**Pre-existing**, not caused by the 9 Sep fixes: the raw selection was
`[0, 10, 11]` on *both* days before the D8 pin touched it.

**Shape.** When the day being read differs from a day already heard, prefer
clock times not yet heard. Leave the same-day rule exactly alone — it is
load-bearing for "what else have you got".

**Do NOT widen B-116 in place.** Four readers, and the file's own comment says
that is how you break a readout nobody was looking at. Add a wrapper beside
`_pin_accepted_index` and `_pin_requested_time_index` — the established shape,
twice over.

**Acceptance, all offline:**
- two consecutive different-day readouts share at most one clock time
- a *same-day* "what else have you got" is byte-identical to today
- D8 still wins: a caller who asks for twelve hears ten past twelve on **both**
  days, because they asked
- `replay_slot_decisions --diff` — no change outside intended turns, read by
  direction
- grid replay over `calls.slot_offers` — no day loses a slot it used to offer
- failing-set diff EMPTY

**Risk: high.** Single owner, every readout, every clinic. Land on
`latency-eval` only. **Promotion waits for a morning call.**

---

#### T2 — Latency: measure, do not touch

**Anchor** the `[LAT]` line and `calls.latency` (per-turn JSON).

CA5e14516b turn 3: `llm_ttft 9529 + chunk_gate 3648 + tts_first_byte 435 = 13612`.

**The filler ladder is NOT at fault** — the first token arrived at 9.5s, under
the 10s deadline, so it correctly stood down. A third rung would not have fired.
Do not reach for N5 here.

**Task:** percentile both components across the corpus, split by turn kind
(tool-result vs plain). Write the distribution down. **No code.**

**Deliverable:** a doc committed to `latency-eval`. **Risk: none.**

---

#### T3 — Stage C evidence

**Anchors** `app/media_streams/llm_stream.py:3993`, `:4021`.

Stage C's gate is *"no `could not resolve spoken option(s)` on any clinic"*.
Offer records are now written on all four clinics (of 120 calls carrying
`slot_offers`: northgate 83, theorem_v3 23, vital_edge 10, jv_v1 4).

Confirming the log line is gone needs the Render dashboard, which is a morning
job. What **can** be done tonight: establish from the corpus and the code which
paths can still reach the reverse-parse, and write it up.

**Do NOT delete the repair layer tonight.** ~900 lines, fifteen B-numbers.
That is a separate change with its own call gate.

---

### 🟠 AMBER — build it, push a branch, do NOT merge

#### T4 — Stage D: extract the provider interface

The payoff item: adding Jane/Carepatron/Cliniko becomes `fetch_free_slots(window,
ids) -> [(start, end)]` plus IDs in `clinic.json`. Four readers, §7 decision 5
says all four acquisition strategies are legitimate — unify the **signature**,
never the strategies.

**Only if T1 landed clean and there is real time left.** It touches every
clinic and cannot be call-verified tonight, so it is branch-and-park by
definition. Push it, leave a note, do not merge.

---

### 🔴 RED — do not start tonight

- **`feat/n5-third-rung`** — owner decision, and CA5e14516b is evidence against
  it. It stays parked.
- **P2 playback residual** — the failure mode is audible only; a call is the
  only meaningful verification.
- **Anything touching `_LAST_BOT_PROMPT_CAP` (B-31)** — 34 writers, and it is
  working as designed.
- **P8** — already closed (`4179e248`). Its summary-table row is stale.
- **D7** — deprioritised by the owner; a Render env var, not code.

---

## 5. When to stop

Stop at the first of these:

1. T1 has landed on `latency-eval` with an empty failing-set diff. **That alone
   is a good night.**
2. A failing-set diff is non-empty and the cause is not understood within one
   reproduction attempt. Push the branch unmerged and write down what moved.
3. Confidence runs out. Confidence, not items.

A parked branch with a clear note is a good outcome. A half-verified change
sitting on `latency-eval` at 4am, with the reasoning lost, is not.

---

## 6. Morning handback — what needs the phone

Write the answers to these into the handback note; they are the only things
tonight cannot settle.

1. **T1 call.** Book, ask for one day, then ask about a second day. The two
   readouts must not be the same three times. Then promote to `production`.
2. **Stage C.** Check the Render log for `could not resolve spoken option(s)`
   across all four clinics.
3. **N5.** Owner decision: a third stall phrase, or fourteen seconds of silence.
   Branch is ready either way.

Build SHA is the only proof of what ran: `[build_info] running build <sha>` at
call cleanup. `/health` returns a hardcoded 1.0.0 and always has.

---

## 7. Definition of done

1. Failing-set diff against `fc47e508` is **empty** — not "small".
2. Tests drive the real function and assert behaviour, not presence.
3. `replay_slot_decisions --diff` read by direction; plus a grid replay for
   anything touching `choose_presented_indices`.
4. Commit message carries the exhibit — call SID, what the caller said, what
   Susie said. Rules here get re-softened once the call behind them is lost.
5. Landed on `latency-eval`. **`production` untouched.**
