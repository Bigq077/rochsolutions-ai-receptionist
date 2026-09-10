# Finishing slot presentation — plan of record, 2026-09-10

**Goal (owner, 10 Sep):** slot presentation is *finished* by the end of this week.
**Time available:** Thursday 10th (part), Friday 11th, weekend buffer 12–13th.
**Author's note:** written for the engineer who picks this up next. Read §1 and
§7 before touching anything.

---

## 0. The bar — because "perfect" is not a thing you can prove

You cannot demonstrate that a conversational system is perfect, and any plan
that promises it is lying about its evidence. What you *can* do by Friday, and
what this plan commits to, is all four of these:

1. **No known defect a caller can hear.** Every item in §2 is closed or has an
   owner decision recorded against it.
2. **The defect class cannot recur silently.** Every producer of a slot sentence
   goes through one owner, and a test fails if a new one does not.
3. **It is measured, not asserted.** Three replay harnesses run over the stored
   corpus and report zero on their gates.
4. **A defined call script sounds right**, twice, on two different diaries.

That is a finishable definition. Treat §0.1–0.4 as the exit criteria and do not
declare done without all four.

**What this explicitly does not promise:** that no new slot defect will ever be
found. Roughly 130 numbered defects have been filed against this area. The
change this week is that the next one will be found by a harness rather than by
a patient.

---

## 1. Where we are, as of this morning

```
production     337fbd9e   ← T1 + T1b, deployed 10 Sep 09:4x, all three clinics
latency-eval   337fbd9e   converged
revert target  fc47e508   the pre-T1 engine. Keep this to hand.
```

**Landed and call-verified today.** Three sites where a readout ignored what the
caller had already heard:

| | site | anchor | state |
|---|---|---|---|
| T1 | cross-turn: a fresh day read at another day's clock times | `slot_followup.py:3737` `_prefer_unheard_clock_times` | fixed, replay-verified |
| T1b-1 | a multi-day readout opened every day at the same time | `receptionist_tools.py:5790` `_cap_presented_slots` loop | fixed, **call-verified** 09:31 |
| T1b-2 | a named day re-read at the times just heard for it | `slot_followup.py:5460` `speak_one_day_from_payload` | fixed, **call-verified** 09:35 |

Corpus effect, multi-day readouts: **repeats 58/59 → 4/59, identical openers
16 → 0.** northgate 50 → 0. The four residuals are theorem_v3, whose rota holds
too few distinct times to vary — the documented stand-down, not a defect.

**T1b-2 is the one to internalise.** `speak_one_day_from_payload` handed
`build_slot_offer` an untrimmed day, and that function's docstring states the
contract it was being handed:

> *"PASS `more_times` when the days handed in have ALREADY been trimmed …
> `choose_presented_indices` … knowledge this function does not have and must
> not overrule."*

**A contract written in a docstring is not a contract.** It was violated at one
of five call sites and nothing noticed. §4 Stage 2 exists solely to make that
unrepresentable.

**Also landed today, documentation only:** `LATENCY_DISTRIBUTION_2026-09-10.md`
(T2), `STAGE_C_EVIDENCE_2026-09-10.md` (T3), and two new harnesses
(`replay_presented_times.py`, `replay_multi_day_spread.py`).

---

## 2. The register — every open item, with anchors

A row without `file:line` is a lead, not a finding. All of these have one.

### 2.1 Caller-audible, presentation

| id | what a caller experiences | anchor | evidence |
|---|---|---|---|
| **S-1** | A three-day readout takes **17–19 seconds**. Callers barge in on nearly every turn. | `slot_offer.py:50-52` caps; `build_slot_offer` `:359` | 17.0 / 18.9 / 16.8 s (6 Sep register §2.8); 18.0 s measured 10 Sep on CAb5b52d95 |
| **S-2** | Once the caller names a day, the cross-day preference stops firing for the rest of the call — "twenty to ten" said for Monday and Tuesday **17 s apart**. | `slot_followup.py:3737`, the `today in heard days` stand-down | CA8214b75c, 10 Sep 09:35:46 → 09:36:03, reproduced offline |
| **S-3** | The filler's first rung fires at **~3.12 s**, just past the published 3-second dead-air bar, so every slow turn breaches it structurally. | `[LAT] ttfa_ms` 3118 / 3120 / 3128 on three consecutive turns, CA8214b75c | 10 Sep, and it is suspiciously constant |
| **S-4** | `last_bot_prompt` blows its 200-char cap and loses its "?" **3–4 times per call**, disarming clinical screening's orphan matcher until it recovers via `last_question`. | `clinical_screening.py:530` `_LAST_BOT_PROMPT_CAP` | both calls today, and §2.7 of the 6 Sep register |

**S-1 and S-4 are the same problem.** The readout is long, so it overruns the
cap. Shortening the readout reduces S-4 without touching the cap — which the
runbook correctly marks RED, because it has 34 writers.

### 2.2 Structural — no caller sees these today, they are how the next one gets in

| id | finding | anchor |
|---|---|---|
| **S-5** | **Five producers call `build_slot_offer`; four honour the selection rule.** Nothing enforces it. | `llm_stream.py:7097`, `:7175`; `slot_followup.py:5212`, `:5295`, `:5535` |
| **S-6** | Stage C's gate watches **one of two** reverse-parse sites. `_record_stood_down_slots` returns silently when it resolves nothing — "named no slots" is indistinguishable from "could not parse". | site A `llm_stream.py:4021` (logs); site B `llm_stream.py:3717` (silent) |
| **S-7** | **13 % of recorded offers were never spoken.** `record_offer` fires where the offer is BUILT, above the P6/P6b stand-downs. Any analysis reading `calls.slot_offers` as "what the caller heard" is wrong 13 % of the time. | `llm_stream.py:7353` |
| **S-8** | `presented_days` is populated on all 53 `multi_day` offers and **empty on all 24 `single_day`** ones, so B-95's presented-vs-bookable split is invisible in the mode a caller reaches by naming a day. | `record_offer` call site, `llm_stream.py:7353` |
| **S-9** | `calls.latency` carries **no tool-result marker**, so the tool-vs-plain latency split cannot be measured at all. One field on `TurnTiming`. | `latency_timing.py` |

### 2.3 Adjacent — real, not slot presentation, do not let them into this week

| id | finding | anchor |
|---|---|---|
| B-146 | A booking request that never says "book" gets *"Sorry, still with you —"* on the caller's first sentence. `classify_intent` cannot see `v3_treatment_mentioned`, which the engine set one line earlier. | `hold_speech.py:619` |
| — | STT drops numerals: `'the 30-minute session'` → `'the 5-minute session'`. Keyterm list carries no numbers. | 6 Sep register, secondary |
| — | jv_v1 wrote `ttfa_ms` as an absolute clock reading, 1,934 turns, 22–23 Aug, seven retired SHAs. **Historical, unrepairable, no action.** Filtered by `IMPOSSIBLE_MS`. | `latency_percentiles.py` |
| — | `GOOGLE_SERVICE_ACCOUNT_JSON` invalid → Sheets skipped; ElevenLabs 401 on `/v1/models`. **Both known-accepted on the demo line.** Not accepted on the live lines — verify separately. | log, every call |

---

## 3. The three plan documents — what is actually left

All three are live. None is superseded by the others; they are a lineage.

### 3.1 `DETERMINISTIC_SLOT_PRESENTATION.md` — 31 Aug

*Stop the model writing the sentence; stop parsing it back.*

Steps 1–4 done: `build_slot_offer` exists, corpus replay exists, single_day and
multi_day are both wired.

**Open: steps 5 and 6** — delete the ~900-line reverse-parse layer, re-aim the
pinned tests. Step 5 is what Stage C gates.

**Its best claim is now confirmed at scale.** It argued from one call that
determinism is *also* the latency fix — 144 ms deterministic vs 5,872 ms LLM,
"40×… the single biggest item against the 1.5 s p95 bar". T2's corpus
measurement, n=3,566: `slot_followup` **0.13 s** vs `llm` **3.06 s** at p50.
Same finding, three orders of magnitude more evidence. And it was visible again
this morning inside a single call: **143 ms against 8,312 ms.**

### 3.2 `SLOT_PRESENTATION_CONVERGENCE.md` — 3 Sep

Phases 0 and 1 complete. **Phase 2 open** and unstarted. Phase 2 is:

* **one producer** — `slot_offer.py` builds every slot sentence; `_flush_slot_buf`
  sections 2–6 deleted, not extended
* **one record** — a single serialisable `Offer` replacing the ten session keys
* **fewer guards** — rules that exist only because two records could disagree
  become unreachable and should be *removed*, not left dead

**Its test-risk warning was already measured and withdrawn.** `PHASE2_TEST_SURFACE.md`
(3 Sep): `tests/auto/scenarios/regressions/` pins **nothing** — all 61 files are
generated, assert only `{'no_technical_error': True}`, and never run without
`RUN_LIVE_CALL_TESTS=1`. The real surface is **one literal pin**
(`test_the_offer_and_its_record_are_built_together.py:91`) plus one negative
assertion to preserve (`test_p9_more_times_that_day_is_numbered_and_recorded.py`,
carrying P10). **One line, not a day of reading.** Verify that claim still holds
before relying on it, but it is the reason Phase 2 is a week's work and not a
month's.

It also, a week ago, named today's bug: *"`choose_presented_indices` /
`_cap_presented_slots` (how many, primary) · `all_remaining_on_next_day` /
`next_slot_batch` (how many, follow-up — **a different answer**)"*. Two owners of
"how many", in writing. Today's was a third.

### 3.3 `ONE_PRESENTATION_LAYER.md` — 9 Sep

Provider-level convergence, five stages.

* **Stage A — DONE.** Acuity routes through `_cap_presented_slots`
  (`receptionist_tools.py:6602`). Verified in code, not taken from the doc.
* **Stage B — DONE**, plus D10 / D11 / D12, all three call-verified 9 Sep.
* **Stage C — evidence gathered 10 Sep**, gate not met. See S-6.
* **Stage D — not started.** The provider interface.
* **Stage E — not started.** Clinic policy above the seam.

**Stages D and E are NOT this week's work, and saying so is the most useful
thing in this section.** They buy onboarding speed — Jane and Cliniko become an
API call — and they buy no correctness. This week is bought with correctness.
Starting D now trades a finishable week for an unfinishable one.

---

## 4. The work, in the order I recommend

Four stages. Each ships alone, reverts alone, and has a gate. **Do not start a
stage before its predecessor's gate has passed** — that rule is the only reason
the last two days converged.

---

### Stage 1 — the ear (today PM → Friday AM)

*Everything a caller can hear. Highest value per hour, smallest diffs.*

#### 1.1 · S-1 — the readout is too long **← start here**

This is now the largest audible presentation defect. The times finally vary;
the sentence is still 17–19 seconds, and callers barge in on nearly every turn.

Measured shape, from CAb5b52d95 (10 Sep): three chunks completing at 6.0 s,
11.0 s, 17.1 s — **~5.5 s per day**, for a line like *"Number 2, Tuesday 15th
September — ten to nine in the morning, or twenty past four in the afternoon."*
Seventeen words per day, of which the date is six.

Three levers, and they are **an owner decision, not an engineering one**,
because two of them change what the caller is offered:

| lever | change | saving | changes the choice set? |
|---|---|---|---|
| **(a)** drop the month after the first day | *"Tuesday the 15th"* | ~1.5 s/day, **~4.5 s** | **no** |
| **(b)** shorter time forms in the second slot | *"…or twenty past four"* | ~1 s/day, **~3 s** | **no** |
| **(c)** `MULTI_DAY_MAX_DAYS` 3 → 2 | one fewer day | **~5.5 s** | **yes** |

> **MEASURED 10 Sep, and the table above is wrong about (a).** Reproduced in
> synthesis at **18.59 s**, calibrated at 18.2 chars/sec against CAb5b52d95's
> own chunk timings -- independently the figure this repo already carries as
> `SLOWEST_REAL_CHARS_PER_SEC` (17.9). On that readout:
>
> | lever | estimated | **measured** | result |
> |---|---|---|---|
> | (a) month after day one | ~4.5 s | **0.66 s** | 17.93 s |
> | (b) suffix on the 2nd time | ~3 s | **2.69 s** | 15.89 s |
> | (a)+(b) | ~7.5 s | **3.35 s** | **15.23 s** |
> | `speak_part_of_day: false`, all times | -- | **5.17 s** | 13.42 s |
>
> (a) was over-credited about sevenfold: it drops ONE word of the six this
> document counts in a date. **So (a)+(b) does not meet this item's own
> "under 12 s" gate, and cannot.** The floor for three days at two times,
> keeping the part-of-day suffix, is **14.85 s** -- with no opener at all,
> weekday-only dates and minimal punctuation. Time labels are 47 % of the
> readout and the suffix alone is 27.8 %, which independently reproduces the
> LAT-1 measurement in `speaks_part_of_day` ("4.7 s of 17.3 s").
>
> **Under 12 s therefore requires an owner decision** -- the suffix or a day:
>
> * `operational.speak_part_of_day: false` is **already built and wired**
>   (7 call sites, default true, a 2262-label corpus study behind it). One
>   key in `clinic.json`, no engine change, 5.17 s. It is deliberately a
>   clinic decision because what is lost is the caller's confirmation.
> * `MULTI_DAY_MAX_DAYS` 3 -> 2, lever (c), ~6 s.
>
> **(b) as specified is NOT the neutral trim this table claims.**
>   `_pick_times_for_day` deliberately picks the second slot in a DIFFERENT
>   part of the day (3 of 4 representative rotas), so (b) strips the band
>   from exactly the slot whose band the caller cannot carry over. It is
>   also half of a wording decision this codebase already assigned to
>   `clinic.json`, for less than half the saving. **Not taken.**
>
> (a) is landed as `40a67ea8`, with the two traps it turned up: the month-end
> ("Tuesday the 1st" after "Monday 30th September" is heard as September)
> and B-111's dedupe, which matched the payload label against the RENDERED
> SENTENCE and went silently inert the moment the wording changed.

**Recommendation: (a) and (b) first, together.** ~7.5 s off a 17 s readout — a
44 % cut — with no change to what is offered and no owner decision required.
Hold (c) in reserve; it is one constant (`slot_offer.py:50`) and can be taken
later if the readout is still long.

**Anchor:** `slot_offer.py:359` `build_slot_offer`, the day-line formatter.
Do **not** touch `slot_times_spoken` — those labels are built upstream and the
formatter must use them verbatim (it invented slots when it did not).

**Watch:** the "Number N" scaffolding is load-bearing for DTMF. Shorten the
date and the time tail, never the numbering.

**Gate:** a three-day readout under 12 s in synthesis; `replay_multi_day_spread`
unchanged; failing-set diff empty; one call.

**Bonus, free:** S-4 improves without being touched. B-31 fires because the
readout overruns the 200-char cap; a 44 % shorter readout stops most of it. Do
not go near `_LAST_BOT_PROMPT_CAP` itself — 34 writers, and the runbook marks it
RED for good reason.

#### 1.2 · S-2 — the cross-day preference stands down too early

Once a day has been heard, `_prefer_unheard_clock_times` returns unchanged and
B-116 owns the readout — and B-116's rule is "unheard **on this day**". So from
the moment a caller names their first day, the cross-day preference never fires
again.

**The refinement, and it is a strict one:** keep B-116's pool exactly as-is —
never offer a time already heard on that day — but *within* that pool prefer
clock times not heard on **any** day. Same all-or-nothing guard as T1: apply it
only when the fresh-anywhere subset can fill the readout alone, otherwise stand
down.

Checked against the live call: Tuesday's unheard-on-Tuesday pool held six
clock times unheard anywhere, so the rule would have applied and given roughly
10:30 / 12:10 / 14:40 instead of 08:00 / 09:40 / 15:30.

**This changes a rule I explicitly protected and wrote a test for**
(`test_the_same_day_rule_is_untouched`). That test must be re-aimed
deliberately, with the reason recorded — it is not a stale test, it is a
superseded decision.

**Anchor:** `slot_followup.py:3737`, the `today in heard days` early return.
**Gate:** replay diff read by direction; failing-set diff empty; the §5 call
script.

#### 1.3 · S-3 — the filler fires at the dead-air bar, not before it

`ttfa` of 3118 / 3120 / 3128 ms on three consecutive turns is a constant, not a
coincidence. The published bar is *no dead air over 3 s without a filler*, and
the first rung lands just the wrong side of it, so **every slow turn breaches
the bar by construction**.

**Scope it before changing it.** Find the constant, confirm it is a single
threshold, and check what else reads it. This is a number, not a redesign — but
it sits on the hot path and it interacts with barge-in
([[barge-in-tears-down-before-the-noise-filter]]).

**Gate:** one call, `ttfa` p95 under 3 s in the next corpus pull.

---

### Stage 2 — the structure (Friday)

*Make today's defect class unrepresentable. This is the stage that stops you
spending hours correcting.*

#### 2.1 · S-5 — enforce the trim contract

Five producers, four correct, nothing enforcing it. Two options:

* **(i) `build_slot_offer` performs the trim itself**, taking the session. One
  owner, no contract to violate. Cleanest, larger diff.
* **(ii) `build_slot_offer` refuses a day it can tell is untrimmed** — assert or
  log-and-trim. Smaller, and it turns a silent violation into a loud one.

**Recommendation: (ii) this week, (i) inside Phase 2.** (ii) is an afternoon and
it closes the class; (i) is the right end state and belongs with the one-record
work, where the signature is changing anyway.

Add a test that walks the module for callers of `build_slot_offer` and asserts
each is reached through the owner. A census test is the only thing that catches
producer number six.

**Anchor:** `slot_offer.py:359`.
**Gate:** the deliberately-untrimmed case is refused in a unit test; failing-set
diff empty.

#### 2.2 · S-6/S-7/S-8 — make the harnesses see what they claim to

Three one-line-ish changes, each closing a blind spot that already cost time:

* **S-6** — give `_record_stood_down_slots` a failure line. Stage C's gate
  currently cannot observe half of what it covers, and a gate like that is not
  a gate. **Amend the gate wording too.**
* **S-8** — pass `presented_days` on the single_day path.
* **S-9** — carry a tool-call count on `TurnTiming`, so T2's central question
  becomes answerable.

**S-7 needs no code** — it is a fact about `record_offer` that every future
harness author must know. It is already handled in `replay_presented_times.py`
(`_was_spoken`) and recorded in memory.

**Gate:** each verified from the corpus, not from the diff.

---

### Stage 3 — Phase 2 (weekend buffer, or next week)

*Only if Stages 1 and 2 are closed and called.*

Take `SLOT_PRESENTATION_CONVERGENCE.md` Phase 2 in its own order — **one record
before deleting guards**, never the reverse. Then step 5 of the 31 Aug document:
delete the reverse-parse layer.

**Do not delete the repair layer on a clean Render grep.** Site A is reachable
only when no deterministic offer was built, and `slot_offers` records only the
turns where one *was* — so the population that reaches it leaves no row.
Instrument that first. (`STAGE_C_EVIDENCE_2026-09-10.md` §5.)

This is the stage most likely to slip past Friday. **That is acceptable.**
Stages 1 and 2 deliver the goal; Stage 3 is what stops the goal decaying.

---

### Explicitly not this week

Stage D (provider interface) · Stage E (clinic policy) · B-146 · the STT numeral
gap · anything touching `_LAST_BOT_PROMPT_CAP` · the general latency work beyond
S-1 and S-3.

Each is real. None is slot presentation, and this week is bought with focus.

---

## 5. Verification protocol — non-negotiable

**The suite is red on purpose. Do not look for green; diff the failing sets.**
Baseline as of `337fbd9e`: **98 failed**.

```bash
# BASELINE — a SECOND worktree at the base commit, never touched
git worktree add /tmp/base 337fbd9e
cp .env /tmp/base/.env && cp tests/auto/.env /tmp/base/tests/auto/.env
cd /tmp/base && python -m pytest -q -p no:randomly > /tmp/base.txt 2>&1
grep -E "^FAILED " /tmp/base.txt | sort > /tmp/B.txt

# CANDIDATE — freeze the tree, then run
md5sum <every file you changed>          # BEFORE
find . -name __pycache__ -type d -prune -exec rm -rf {} +
python -m pytest -q -p no:randomly > /tmp/head.txt 2>&1
md5sum <every file you changed>          # MUST equal BEFORE
grep -E "^FAILED " /tmp/head.txt | sort > /tmp/H.txt
diff /tmp/B.txt /tmp/H.txt                # must be EMPTY
```

**Never run the suite in a worktree you are editing** — ~55 `inspect.getsource`
tests fail as an artefact and it reads as catastrophe.

### The three harnesses, and what each is blind to

| harness | covers | blind to |
|---|---|---|
| `replay_slot_decisions.py` | slot decisions, 1,849 turns | **the whole selection** — `choose_presented_indices`, `remaining_unspoken_on_current_day`, `all_remaining_on_next_day`. It said `CHANGED: 0` on both T1 and T1b. |
| `replay_presented_times.py` | the per-day selection, 404 day-readouts | the **loop** around it. It reported 0 changed while the live readout was broken. |
| `replay_multi_day_spread.py` | the multi-day loop, 59 readouts | the named-day producers |

```bash
python scripts/replay_slot_decisions.py   --out BASE.json   # then --diff
python scripts/replay_presented_times.py  --out BASE.json   # then --diff
python scripts/replay_multi_day_spread.py                   # before / after
```

Gates that must read 0: `lost_a_slot`, `invented_a_slot`, `changed_a_heard_day`.
Read the rest **by direction** — a pick changing to a *different* slot is
dangerous; a pick *lost* is usually a guard working.

**No single harness covers slot presentation.** Run all three, every time. That
sentence is the whole lesson of 9–10 September.

### The call script — the exit criterion

Two calls, two different clinics, at least one on a **non-grid diary** (Vital
Edge or JV — neither has been called since T1b, and northgate's uniform grid is
the easy case).

1. *"I'd like to book an appointment — my ankle."*
2. *"Anytime next week."* → **three days, no shared clock time, readout under 12 s**
3. *"Tell me about Monday."* → **nothing you already heard for Monday**
4. *"And what about Tuesday?"* → **nothing you heard for Tuesday, and no time
   repeated from step 3**
5. *"Anything around midday on Tuesday?"* → **midday is offered** (D8)
6. Take a slot, and check the diary entry matches what you were told.

Step 4's second clause is S-2. Step 6 is the only step that proves the readout
and the booking agree.

**Build SHA is the only proof of what ran:** `[build_info] running build <sha>`
at call cleanup. `/health` returns a hardcoded 1.0.0 and always has.

---

## 6. Deploy discipline

`production` and `latency-eval` are converged at `337fbd9e`. Promotion is
**fast-forward, one direction**, `latency-eval` → `production`. A merge commit
here means someone fixed something in the wrong place.

```bash
# before every promotion
git log --oneline origin/production ^origin/latency-eval        # MUST be empty
git rev-parse origin/production                                 # WRITE THIS DOWN
git diff origin/production..origin/latency-eval -- app/ | \
  grep -E "^[+-].*(SMS_ENABLED|APPOINTMENT_REMINDERS_ENABLED|SHEETS_ENABLED|OBS_.*_ENABLED)"
git push origin origin/latency-eval:production
```

That last grep is not optional. `SMS_ENABLED` and
`APPOINTMENT_REMINDERS_ENABLED` are per-service env vars whose **code defaults
must stay OFF on both branches**, or a test call texts a real patient.

A push to `production` reaches three live clinics with `autoDeploy` on. Real
call after any engine change.

---

## 7. Traps — every one of these cost real time this week

**A change can ship completely inert.** Three times in one day on 9 Sep: a `\b`
written as a literal backspace byte; a hold phrase silently refused by
`_second_filler_text`; config constants defined but never imported, raising
`NameError` at 16 s inside a background task where it may never reach a log.

**Test BEHAVIOUR, never presence.** `assert "X" in source` proves nothing. All
three of the above passed a presence-style check. Drive the real function.

**Verifying the function is not verifying the system.** T1 was correctly
verified against `choose_presented_indices` and reported as fixed. The live
call went through two other sites and heard the identical defect. If you fix a
selection rule, **enumerate its callers before you claim coverage.**

**A mock of the loop cannot see the loop's bug.** The first version of the
multi-day measurement simulated `_cap_presented_slots` and passed while the
live path was broken. Drive the real entry point.

**Built is not spoken.** 13 % of recorded offers were never said out loud
(P6/P6b stood them down). Cross-check against the transcript or your
measurement flatters itself.

**A safety property can live in an idiom.** `len(hits) == 1` also silently
meant "decline when the same time sits on several days". Replacing that code
kept the named meaning and lost the implied one.

**`_spread` outranks new preferences.** The owner decided on 1 Sep that two
slots fifty minutes apart are not a choice. T1b's first cut filled a short pool
back up and produced 08:00 + 08:50; two tests caught it within a minute. Any
new selection rule must be all-or-nothing against it.

**Prompt hashes live in TWO tables under different names**, each with its own
`_sha`: `test_b55_provisional_reschedule_closing.py` (`UNCHANGED_CLINIC_PROMPTS`)
and `test_b57_theorem_cancel_gate.py` (`UNMOVED_PROMPTS`). Recompute per table;
never copy a value across.

**`git stash` does not revert here** (OneDrive locks) — back changes out by
hand. Use `git -C <path>`, never `cd X; cmd`. Run `git worktree prune` and
`git rev-parse --abbrev-ref HEAD` before you trust a single number: there are
160+ registered worktrees and a previous session confidently measured the wrong
one.

**`receptionist_tools.py` is CRLF.** A whole-file rewrite lands as a 16k-line
diff. Preserve line endings when scripting an edit.

---

## 8. Order of work, on one page

| # | item | stage | gate | est. |
|---|---|---|---|---|
| 1 | **S-1** readout length — (a)+(b) | 1 | readout < 12 s, spread unchanged, call | ½ day |
| 2 | **S-2** cross-day preference stands down | 1 | replay by direction, call script step 4 | ½ day |
| 3 | **S-3** filler at the dead-air bar | 1 | `ttfa` p95 < 3 s, call | ¼ day |
| 4 | **S-5** enforce the trim contract + census test | 2 | untrimmed day refused in a test | ½ day |
| 5 | **S-6/S-8/S-9** harness blind spots | 2 | verified from the corpus | ¼ day |
| 6 | **Phase 2** one record, then delete the guards | 3 | its own plan's gates | ≥ 2 days |

Items 1–5 are the week. Item 6 is what stops the week decaying.

**If you have time for exactly one thing: item 1.** It is the largest audible
defect left, it needs no owner decision if you take (a) and (b), and it takes
S-4 down with it for free.

---

## 9. Handover rules

* **Commit messages carry the exhibit** — call SID, what the caller said, what
  Susie said. Rules get re-softened once the call behind them is lost.
* **Every behavioural fix ships with a regression test** in `tests/regression/`.
* **Clinic-specific behaviour belongs in `clinic.json`**, never in engine code.
* **If these documents and the code disagree, the code wins.** Record the
  correction here. This family of documents has been wrong before, and the one
  thing that has consistently worked is measuring rather than believing.
