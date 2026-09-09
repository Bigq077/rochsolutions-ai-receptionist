# Work plan — slot presentation & open defects
**For:** Jules · **From:** the 2026-09-09 session · **Branch base:** `db0f6b62`

Read §1–§3 before opening a file. They are short, and every line of them is
there because it cost real time on 9 Sep.

---

## 1. Where things stand

```
latency-eval          db0f6b62    demo line only  (+447366263180, northgate)
production            db0f6b62    Vital Edge + JV + Theorem — REAL PATIENTS
feat/n5-third-rung    e49733ef    built, NOT merged, owner decision pending
```

Both deployed branches are identical. `autoDeploy` is on: **a push to
`production` is a deploy to three live clinics.**

**Revert targets, by blast radius:**

| to undo | reset `production` to |
|---|---|
| the clinical fix only | `c5d24da6` |
| everything from 9 Sep | `5f1003c9` |

`/health` returns a hardcoded 1.0.0 and always has. The **only** proof of what
is running is `[build_info] running build <sha>` at call cleanup in the Render
log.

### Verified by call, build `8e838f0f` (CA5e14516b, 9 Sep 23:08)

- clinical acknowledgement is general — *"ankles can be tricky to get fully
  right — worth having Priya take a proper look"* — no invented symptoms
- a named weekday returns that weekday (`named-day restored:` fired)
- a caller who asks for twelve is offered **ten past twelve**
  (`pinned the requested time back into the readout`)
- a different-day request re-looks-up rather than reusing the standing offer

Do not re-verify these. They are done.

---

## 2. The verification protocol — non-negotiable

The baseline is **red on purpose**. Do not look for green; diff the failing
sets. Today: **97 failed / 9668+ passed**, failing-set md5 `6b563465`.

```bash
# BASELINE — a SECOND worktree, at the base commit, that you never touch
git worktree add /tmp/base db0f6b62
cp .env /tmp/base/.env            # a tree without .env runs DIFFERENT tests
cd /tmp/base && python -m pytest -q -p no:randomly > /tmp/base.txt 2>&1
grep -E "^FAILED " /tmp/base.txt | sort > /tmp/B.txt

# CANDIDATE — freeze the tree, then run
md5sum <every file you changed>   # record BEFORE
find . -name __pycache__ -type d -prune -exec rm -rf {} +
python -m pytest -q -p no:randomly > /tmp/head.txt 2>&1
md5sum <every file you changed>   # MUST match BEFORE
grep -E "^FAILED " /tmp/head.txt | sort > /tmp/H.txt
diff /tmp/B.txt /tmp/H.txt        # must be EMPTY
```

**Three rules that are not optional:**

1. **Never run the suite in a worktree you are editing.** I did it twice on
   9 Sep. Both times ~55 tests using `inspect.getsource` failed — it reads the
   file from disk but resolves the block from the already-imported code
   object's line numbers. It reads as a catastrophic regression and is an
   artefact. A large failure count concentrated in `getsource` tests is this.
2. **Grep `^FAILED ` only.** `^(FAILED|ERROR)` also catches log lines starting
   with `ERROR` and silently pollutes the set.
3. **Reproduce a suspected regression in isolation before reporting it.**

### The slot replay — run it on EVERY slot change

```bash
cd /tmp/base   && python scripts/replay_slot_decisions.py --out /tmp/BASE.json
cd <candidate> && python scripts/replay_slot_decisions.py --out /tmp/CAND.json
python scripts/replay_slot_decisions.py --diff /tmp/BASE.json /tmp/CAND.json
```

~1,845 scored turns. Read the diff **by direction**: a pick changing to a
*different* slot is dangerous; a pick *lost* is usually a guard working.

**Read its header for what it cannot do.** obs stores speech, not availability
payloads, so `remaining_unspoken_on_current_day`, `choose_presented_indices`
and `all_remaining_on_next_day` are **NOT covered**. A green report says
nothing about them — and W1 below sits squarely in that blind spot. For those,
use a grid replay against `calls.slot_offers` (120 calls, forward-only from
3 Sep): pull `slot_offers[].payload[].slot_times`, cross them against real
caller utterances, and assert **exact-hit-nearest-missed == 0**. That method
caught a fix about to make things worse on 9 Sep before it shipped.

---

## 3. Traps that cost time on 9 Sep

**A change can ship completely inert.** Three times in one day:

- `\b` written into a regex as a literal backspace byte (`0x08`). Compiled
  fine, matched nothing, the guard did nothing.
- A third hold phrase added to `WorkKind.UNKNOWN_SLOW` — silently refused,
  because `_second_filler_text` rejects any candidate whose `head_families`
  intersect the phrase just spoken.
- New config constants defined but never imported — a `NameError` at 16s
  inside a background task, where it may never reach a log.

**So test BEHAVIOUR, never presence.** `assert "X" in source` proves nothing.
Drive the real function and assert what comes back.

**Prompt hashes live in TWO tables under different names.** Any edit to
`clinic_template_prompt.py` that moves `jv_v1` fails both:

```
tests/regression/test_b55_provisional_reschedule_closing.py   UNCHANGED_CLINIC_PROMPTS
tests/regression/test_b57_theorem_cancel_gate.py              UNMOVED_PROMPTS
```

Each has its **own** `_sha` — recompute per table, never copy a value across.
b57 is the one to read: it pins `vital_edge` too, so it turns a scope claim
into a measurement. `northgate` is in neither — assert its prompt by rendered
text instead.

**A safety property can live in an idiom.** `len(hits) == 1` meant "found it"
*and*, silently, "decline when the same time sits on several days". Replacing
that code kept the meaning that was named and lost the one that was implied —
a caller who asked for Wednesday was offered Monday. When you replace a
matcher, write down what its **return shape** guaranteed.

**Enabling a dormant path exposes guards that were never load-bearing.** Before
making a path live, grep the functions it will newly reach for
`Tier 2` / `out of scope` / `needs its own corpus` / `nothing reads this`.

**`git stash` does not revert in this repo** (OneDrive locks) — back changes
out by hand or your baseline is a lie. Use `git -C <path>`, never `cd X; cmd`:
a failed `cd` runs the next command in the wrong tree and has clobbered the
primary worktree before.

---

## 4. The work, in order

Each item states its anchor, its acceptance test, and its risk. **Do them in
order and stop when you run out of confidence, not when you run out of items.**
One concern per commit; each ships and reverts alone.

---

### W1 — Identical times read out for every day  🔴 highest value

**Anchors**
- `app/tools/slot_followup.py:3715` — `choose_presented_indices`, the ONE owner
  of "how many, and which", with four readers on it
- `app/tools/slot_followup.py:2068` — `spoken_starts_for_offer`
- `app/tools/receptionist_tools.py:286` — `_spoken_starts_for`

**The exhibit** — CA5e14516b, 9 Sep 23:08, judge 2, `dead_end` + `booking_error`:

```
caller: "do you have anything wednesday around 12"
Susie : Wednesday 16th — eight in the morning / ten past twelve / twenty past four
caller: "what about monday at 12"
Susie : Monday 14th   — eight in the morning / ten past twelve / twenty past four
```

**Diagnosis, already done — do not re-derive it.** "Already heard" is a set of
**dated ISO starts**, so `2026-09-16T08:00` and `2026-09-14T08:00` are different
members and B-116's "prefer times not yet heard" never carries across days.
northgate's grid is uniform (50-minute steps, identical every day), so every day
reads the same. Confirmed **pre-existing** and not caused by the 9 Sep fixes:
the raw selection was `[0, 10, 11]` on *both* days before the D8 pin touched it.

**Shape of the fix.** When the day being read differs from the day already
heard, prefer clock times the caller has not heard. Keep the same-day rule
exactly as it is — that one is load-bearing for "what else have you got".

**Do NOT widen B-116's rule in place.** Four readers, and the file's own comment
says widening it in place is how you break a readout nobody was looking at. Add
a wrapper beside `_pin_accepted_index` and `_pin_requested_time_index` — that is
the established shape for precisely this, twice over.

**Acceptance**
- two consecutive different-day readouts share at most one clock time
- a *same-day* "what else have you got" is byte-identical to today
- the D8 requested-time pin still wins: a caller who asks for twelve still hears
  ten past twelve on **both** days, because they asked for it
- `replay_slot_decisions --diff` shows no change outside the intended turns
- grid replay over `calls.slot_offers`: no day loses a slot it used to offer

**Risk: high.** It is the single owner of every readout on every clinic. This is
the item most likely to produce a regression — it is first because you are
fresh, not because it is safe.

---

### W2 — Close the Stage C gate  🟢 cheap; do it while W1's suite runs

**Anchors** `app/media_streams/llm_stream.py:3993` (`resolve_spoken_options`),
`:4021` (the refusal log line), and `app/tools/slot_offer.py`'s module docstring.

`ONE_PRESENTATION_LAYER.md` Stage C's gate is *"no `could not resolve spoken
option(s)` on any clinic"*. Stage A put `build_slot_offer` /
`apply_offer_to_session` on the Theorem path, and the corpus shows offer records
now written on all four clinics — of 120 calls carrying `slot_offers`:
northgate 83, theorem_v3 23, vital_edge 10, jv_v1 4.

**Task:** confirm the line no longer fires, then either close Stage C in the
plan doc or write up exactly which path still reaches the reverse-parse.

**If it is genuinely dead, deleting the repair layer is a SEPARATE commit** with
its own suite run. It is ~900 lines and fifteen B-numbers live there.

**Acceptance:** Stage C closed with evidence, or a named live path.
**Risk:** low while measuring; high the moment you delete. Split them.

---

### W3 — Latency: 13.6s to content  🟠 measure first, do not guess

**Anchor** the `[LAT]` log line and `calls.latency` (per-turn JSON; needs
`LATENCY_TIMING` on *and* obs capture on).

CA5e14516b turn 3:

```
llm_ttft_ms 9529  +  chunk_gate_ms 3648  +  tts_first_byte 435  =  13612
```

Two separable costs. **The filler ladder is NOT at fault** — the first token
arrived at 9.5s, under the 10s second-filler deadline, so it correctly stood
down. A third rung would not have fired either. Do not reach for N5 here.

**Task:** percentile both components across the corpus, split by turn kind
(tool-result turns vs plain). Only then propose. The 3.6s gate is the slot
buffer waiting for a complete response before it can build the deterministic
offer — that is by design, and trading it away costs correctness.

**Acceptance:** a written distribution and a named proposal. **No code this
pass.** **Risk: none while measuring.**

---

### W4 — Stage D: extract the provider interface  🟡 the actual payoff

**Anchors** the four readers: `_exec_check_availability` (gcal fall-through),
`_check_availability_acuity`, `_check_availability_diary`,
`_check_availability_published`.

Goal, from §5 of the plan: adding Jane / Carepatron / Cliniko becomes
`fetch_free_slots(window, ids) -> [(start, end)]` plus IDs in `clinic.json`.
§7 decision 5 records why all four acquisition strategies are legitimate and
stay — unify the **signature**, never the strategies.

**Only start this if W1 shipped clean and you have real time.** It is the
largest item here. If you start and stop, leave the branch pushed and unmerged
with a note, exactly as `feat/n5-third-rung` is.

**Risk: high, and it touches every clinic.**

---

### W5 — P2 residual: the playback case  🟢 small

Synthesis finished, audio still playing, barge-in window. Deliberately left open
on 1 Sep; written up under P2 in
`OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01.md`. A good filler item if W1 lands
early.

---

## 5. Do NOT do these

- **Do not merge `feat/n5-third-rung`.** It reverses a product decision pinned
  by `test_b19_filler_rearm.py::test_the_rearm_is_not_a_loop`: *"a second at
  ~5s, then stop. A continuing cadence was considered and rejected — three or
  four phrases on one slow turn sounds anxious."* Owner decision, still open,
  and CA5e14516b is evidence against it.
- **Do not fix D7.** Owner has explicitly deprioritised it. Render dashboard
  env var, not code.
- **Do not touch `_LAST_BOT_PROMPT_CAP` (B-31).** Investigated 9 Sep: the
  truncation warning is its own recovery working, both branches are
  test-covered, 45 tests pass, and the key has **34 writers**.
- **Do not re-open P8.** The summary table in the 1 Sep doc says OPEN; line 99
  of the same file says *"Done, `4179e248`"*. The table is stale.
- **Do not promote to `production` without a call.** Push to `latency-eval`,
  dial +447366263180, confirm the build SHA in the log, then promote.

---

## 6. Definition of done, per item

1. Failing-set diff against `db0f6b62` is **empty** — not "small".
2. New regression tests drive the real function and assert behaviour, not
   presence.
3. `replay_slot_decisions --diff` read by direction, for anything touching
   slots — plus a grid replay if it touches `choose_presented_indices`.
4. A commit message carrying the exhibit: the call SID, what the caller said,
   what Susie said. Rules here get re-softened once the call behind them is
   forgotten.
5. Pushed to `latency-eval`. `production` only after a call.

If an item cannot meet 1–3, **push the branch unmerged and write down why.**
A parked branch with a clear note is a good outcome. A half-verified change on
three patient lines is not.
