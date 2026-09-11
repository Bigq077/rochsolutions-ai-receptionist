# Overnight handover — 11→12 Sep 2026

**Branch** `feat/slot-spec-and-verifier`, cut from `origin/latency-eval` at
`66f7dec3`. **Four commits. Nothing pushed. Nothing deployed.**

**Worktree** `AppData/Local/Temp/claude/slotspec` (baseline for the failing-set
diff: `AppData/Local/Temp/claude/slotbase`, detached at `66f7dec3`).

---

## The 60-second version

Three weeks of slot work was treated as a string of selection bugs. The thing
underneath is that **nothing said what the right answer is**, so every fix was
correct for its own call and compositions of correct fixes failed on the next
one. Tonight produced the missing statement, an instrument that runs it, and the
two changes that close the gap the 00:04 call went through.

| # | what | state |
|---|---|---|
| 1 | `SLOT_PRESENTATION_SPEC.md` — acts, ledger, **precedence**, 34-row decision table, 20 invariants | written |
| 2 | `scripts/score_slot_spec.py` — runs the table against the engine over 6 diary shapes | **58 pass, 0 FAIL, 29 unreachable** |
| 3 | `app/tools/slot_fact_guard.py` — invariant 1, enforced before TTS | shipped in mode `log` |
| 4 | prompt Steps 5–7 brought into line with the engine | done, hashes re-pinned |
| 5 | whole-suite failing-set diff against baseline | **98 before, 98 after, zero new** |

**Test evidence.** `tests/regression`: 5 failures before, 5 after, identical set,
8,658 passing. Rest of `tests/`: 93 before, 93 after, identical set. So the
standing red baseline is 98 and **none of it is mine**. Two new test files, 28
new tests, all green.

---

## What needs you — about 20 minutes

### 1. One call on the demo line (+447366263180), then the flip

The guard ships in `log` mode: it detects and records, and **does not change a
word**. That is deliberate — the same commit reaches three live patient lines by
fast-forward, and a new interceptor that can replace a sentence should not arrive
there ahead of a real call.

```
push feat/slot-spec-and-verifier -> latency-eval     (demo line only)
confirm [build_info] running build <sha> in the Render log
make one booking call, including "have you got anything around twelve"
grep the log for [slot_guard]
```

* **No `[slot_guard]` lines** — the call was clean. Expected on a good call.
* **`slot fact on the WRONG DAY (log, recorded only)`** — a real time attributed
  to the wrong day. Recorded, never enforced; send me the line.
* **`SPOKEN SLOT FACT NOT IN THE DIARY`** — the 00:04 defect, caught. The
  sentence still went out, because the mode is `log`.

Then, on the **demo service only**, set `SLOT_FACT_GUARD=enforce` and call again.
In `enforce` the offending chunk becomes *"Sorry — let me just double-check that
one for you."* and the rest of that turn is dropped. Only after that call reads
right should `production` get the flip, per service.

> `production` is untouched and should stay where it is. Nothing here needs to
> reach a patient this week.

### 2. Four decisions — the rows I could not take for you

All four are in `SLOT_PRESENTATION_SPEC.md` §9.2. Short version:

| | question | my recommendation |
|---|---|---|
| **DT-4b** | After a caller has heard all three of Monday's times, *"what about Monday"* currently reads three **different** times — zero overlap, measured on two diary shapes. Is that right? | **No.** "What about Monday" is "tell me about Monday". Keep at least one time they heard, whatever the count. Two lines in `_keep_times_heard_on_named_day`. Not changed tonight because it is a live selection rule. |
| **DT-21** | Caller names a time matching no offered slot ("10 to 12" against 12:10). Guess the nearest, or re-read and ask? | **Re-read and ask.** One extra turn against the 00:04 defect. Already in the prompt. |
| **DT-31** | `REPEAT` with no offer on the table: rebuild from the last payload, or re-query? | **Rebuild.** Faster, and it cannot change the times under the caller. |
| **D-n** | The engine as the *only* author of a slot day/date/time. | Confirm. It removes a contradiction rather than adding a policy — two of three prompts already said it. Reversible in one commit. |

### 3. N6 is still open, and I deliberately did not touch it

*"What else have you got on Monday"* is still answered with Thursday–Saturday.
The named-day producer, called directly, handles it perfectly on all six diary
shapes — so **N6 is a routing defect, not a producer defect**: the utterance
never arrives. Fixing it means changing which producer claims the turn, which is
a selection change, and §7 of the analysis says stop shipping those until the
table is agreed. It is DT-14, and it is the first row I would take after your
sign-off.

---

## What I changed, in detail

### `app/tools/slot_fact_guard.py` (new, wired in `_tts_loop`)

Invariant 1: no clock time is spoken that no payload on the call holds. It reads
every outgoing chunk at the one seam every utterance passes through — **after**
every suppression check, so it cannot log speech nobody heard, and **before** the
obs transcript, so that cannot claim words that were replaced.

* Two severities. `unknown_time` (on no day's grid) is high confidence and the
  only one enforced. `wrong_day` rests on attributing a day to a clause from
  text, which this project has measured as unreliable — recorded, never enforced.
* The extractor is built from `_spoken_slot_time`'s **closed grammar**, not from
  English, and works in the folded (digit) domain. Mentions are candidate sets,
  so an ambiguous one is self-acquitting — the correct bias when a false positive
  replaces a sentence.
* Exemptions, each a real shape: negated clauses (via `offer_clauses`, B-139),
  opening hours, durations, a window the caller named, and any time **the caller**
  asked for — `note_caller_speech` folds those in at turn start, because Step 5
  *requires* naming a window back before offering outside it.
* Fails open everywhere. The worst thing it can do is nothing.

**It does not check selection, and the docstring says so in capitals.** The 00:04
call's *first* defect — "the nearest to twelve o'clock is twenty to three" — named
two genuinely bookable times and answered the wrong question. The guard passes it,
correctly. That is invariant 4, and it is a table row.

### `app/prompts/clinic_template_prompt.py` Steps 5–7

The outlier, found by **rendering all four clinic prompts** rather than reading
the module. `SLOT_FORMATTER_SYSTEM_PROMPT` and theorem_v3's own section were
already numbered and engine-aligned; only this template said "exactly TWO times
… no numbered list", and it renders on the demo line plus jv_v1 and vital_edge.

Rendered sizes either side: the three `template_v1` clinics each **+1,295 bytes,
identical diff**; theorem_v3 **byte-identical**. Hash pins re-computed in **both**
tables with each file's own `_sha` — the file itself records that a previous
re-pin left the suite +1 by updating only one of them.

---

## Where things are

```
docs/plan/SLOT_PRESENTATION_SPEC.md          the specification  <- start here
docs/plan/SLOT_PRESENTATION_ANALYSIS_...md   the evidence it rests on
docs/plan/SLOT_HANDOVER_2026-09-12.md        this file
scripts/score_slot_spec.py                   python scripts/score_slot_spec.py
app/tools/slot_fact_guard.py                 the guard
tests/regression/test_a_spoken_slot_time_exists_in_the_diary.py   24 tests
tests/regression/test_the_spec_scorer_still_runs.py               4 tests
docs/plan/README.md                          corrections 28-31
```

Superseded by the spec, kept for history: `SLOT_PRESENTATION_CONVERGENCE`,
`SLOT_PRESENTATION_FINISH_2026-09-10`, `DETERMINISTIC_SLOT_PRESENTATION`, the
eight `OPEN_DEFECTS_*` registers, the call sheets. Listed in spec §10.

---

## Two things I got wrong, recorded so they are not re-learnt

1. **I wrote regexes through a bash heredoc** and put twelve literal backspace
   characters into the guard where `\b` should have been. There is a standing note
   not to do this. Use `Edit`/`Write` for anything containing a backslash.
2. **The scorer manufactured four findings before it measured anything** — a
   non-round probe time that invariant 18 declines by design, a bare string where
   `nearest_time_index` declines by contract, three invented session keys where
   the engine reads the spoken record, and a row asserting past the documented
   boundary of N1's fix. All four looked exactly like defects. Corrections 30 and
   31 in `docs/plan/README.md`; the scorer's own docstring carries the warning.

Neither reached a caller, and the second is the reason the scorer's zero-FAIL
result is reported with its 29 unreachable checks next to it rather than on its
own.
