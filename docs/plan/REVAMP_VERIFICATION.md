# Revamp verification — OPERATING_SYSTEM_REVAMP.md §0

**Written:** 2026-08-22
**Method:** `git fetch --all`, then measured against `origin/*` refs only.
**Measured from:** `C:\Users\quent\OneDrive\Documents\GitHub\rochsolutions-ai-receptionist`,
branch `vitaledge-onboarding`, with cross-checks in the second clone (below).

---

## The thing that has to be said first: there are two clones

Both point at the same GitHub remote.

| Clone | Branch | Role |
|---|---|---|
| `OneDrive/Documents/GitHub/rochsolutions-ai-receptionist` | `vitaledge-onboarding` | where the last five sessions of engine work happened |
| `OneDrive/Documents/Claude code free/rochsolutions-ai-receptionist` | `jv-v1-onboarding` | where the revamp plan was written |

`jv-v1-onboarding` is the **retired** branch: 536 behind `latency-eval`, 2 obs
modules, last commit 7 Aug. The plan read `CLAUDE.md` from a checkout sitting on
it, and that copy is **uncommitted** (`git status` → ` M CLAUDE.md`).

That single fact produces the plan's central error. Nothing was lost — one
remote, and all engine work is on `origin/latency-eval` — but the diagnosis was
taken from a dead branch's uncommitted working copy.

---

## The seven claims

| # | Claim | Verdict |
|---|---|---|
| 1 | `engine/converged` exists locally only, never pushed | **CONFIRMED** |
| 2 | Divergence table vs `origin/latency-eval` | **CONFIRMED** (numbers drifted) |
| 3 | `origin/jv_v2` exists, 20 obs modules, absent from CLAUDE.md | **CONFIRMED** |
| 4 | `tests/auto` last modified 2026-07-23 on the live branches | **CONFIRMED** |
| 5 | Newest run 2026-04-07, 71.2% (84/118), gate 0.97 | **CONFIRMED**, one caveat |
| 6 | Four `OBS_*` default false; 20 modules, 2 on jv-v1 | **CONFIRMED** |
| 7 | `app/obs/cost.py` RATES unpopulated | **CONFIRMED**, wrong location |

### 1 — CONFIRMED

```
$ git branch --list "*converged*"          # in the "Claude code free" clone
+ engine/converged
$ git ls-remote --heads origin "*converged*"
(empty)
```

Present in one clone, absent from the other, never on origin. The `+` means it is
checked out in a worktree there.

### 2 — CONFIRMED, numbers have moved

Ahead / behind `origin/latency-eval`, measured 22 Aug:

| Branch | plan said | measured now |
|---|---|---|
| `theorem-onboarding` | 175 ahead / 193 behind | **183 / 203** |
| `vitaledge-onboarding` | 149 / 196 | **158 / 206** |
| `jv_v2` | 92 / 119 | **101 / 129** |
| `jv-v1-onboarding` | 17 / 526 | **17 / 536** |

Consistent drift of ~10 commits. The table was right when taken.

### 3 — CONFIRMED

`origin/jv_v2` exists, last commit 22 Aug, and carries 20 `app/obs` modules. It
is not named in any branch's `CLAUDE.md`.

### 4 — CONFIRMED

`tests/auto` was last touched by the same commit on all four live branches:

```
latency-eval / jv_v2 / vitaledge-onboarding / theorem-onboarding
  2026-07-23  10d88652  fix(tests): call-runner refuses any non-demo target
```

### 5 — CONFIRMED, but the evidence lives in one clone

`results_20260407_110303.json` verifies exactly: **118 scenarios, 84 passed,
71.2%**, against `MIN_PASS_RATE = 0.97` (`tests/auto/config.py:48`).

**Caveat:** the 2,874 artefacts existed **only in the "Claude code free" clone**.
The GitHub clone had 520, newest 23 March. `tests/auto/results/` is untracked, so
the entire test history was one folder deletion from gone. It has now been copied
into the surviving clone (3,137 artefacts).

**Second caveat, and it matters for triage:** `test_said` is **empty on all 118
scenarios**, passed and failed alike — while Susie produced 9–23 turns per call
over 60–214 seconds. The conversations happened; the artefact simply never
recorded the caller side. So the stored results show *what Susie said* and
*whether a check passed*, but not what she was answering. **The 34 failures
cannot be fully diagnosed from disk — they need a re-run.**

What the failures cluster into, for what it is worth:

| Failed check | n | Phases |
|---|---|---|
| `booking_confirmed` | 18 | 5, 11, 12, 13, 14, 16, 18, 19 |
| `no_question_asked_twice` | 7 | 2, 8 (all five end-to-end runs) |
| `slot_confirmed` | 5 | 4 (every slot-selection scenario) |
| `no_state_corruption` | 2 | 18 |

Phase 4 failing in its entirety, and all five Phase 8 end-to-end runs failing on
a repeated question, are the two shapes worth looking at first.

### 6 — CONFIRMED

```
app/config.py:57  OBS_CAPTURE_ENABLED = os.getenv("OBS_CAPTURE_ENABLED", "false")...
app/config.py:74  OBS_ALERTS_ENABLED  = os.getenv("OBS_ALERTS_ENABLED",  "false")...
app/config.py:109 OBS_JUDGE_ENABLED   = os.getenv("OBS_JUDGE_ENABLED",   "false")...
app/obs/worker.py:25  OBS_DIGEST_ENABLED  "true" to start the worker at all (default false)
```

Module counts: `latency-eval` 20 · `jv_v2` 20 · `vitaledge-onboarding` 20 ·
`theorem-onboarding` 20 · `jv-v1-onboarding` **2**.

### 7 — CONFIRMED on content, CORRECTED on location

The RATES really are unfilled — `RATE_TABLE_VERSION = "unset"`, every rate
`None`, and the module refuses to produce a number until they are filled.

But the file was **absent from every live origin branch**. It existed only in
commit `dd5ea9ed` on the local-only `engine/converged`. Stage 4c could not have
been done as written. **Now rescued** to `origin/rescue/obs-cost`.

---

## F2 is wrong, and it inverts

ADR-002 and `engine/converged` appear in the `CLAUDE.md` of **no origin branch**:

```
origin/latency-eval          ADR-002:0   engine/converged:0
origin/theorem-onboarding    ADR-002:0   engine/converged:0
origin/vitaledge-onboarding  ADR-002:0   engine/converged:0
origin/jv_v2                 ADR-002:0   engine/converged:0
origin/jv-v1-onboarding      ADR-002:0   engine/converged:0
```

Every live branch says **"`latency-eval` is THE engine branch"**.

The plan's implication — *"sixteen days of work went onto the old branches
instead"* — is backwards. The work went exactly where the shared, committed
instruction pointed. ADR-002 was never published to the repo, so no agent and no
session could have followed it.

`engine/converged` is also not a stranded body of work. **54 of its 56 commits
were already on origin.** Only two were not, and both are now rescued:

```
7f239ef8  docs: ADR-002 — ratify engine/converged
dd5ea9ed  feat(obs): per-call cost of goods        ← app/obs/cost.py
```

**Consequence: Stage 1 is not blocking anything.** There is no conflict to
resolve. What remains is real but schedulable branch convergence.

---

## A. Which branch should be canonical

**`latency-eval`** — a confirmation, not a new decision, and against the plan's
recommendation of `theorem-onboarding`.

- It is what every live branch's committed `CLAUDE.md` already says. Keeping it
  costs nothing and re-orients no one.
- 20/20 obs modules; most recently committed.
- It is the reference the other three are measured against, and all three are
  behind it. Convergence means catching them up, not moving the target.
- **It serves no live clinic — which is the argument for it, not against.** The
  plan treats "carries the live clinic" as a point in `theorem-onboarding`'s
  favour. An engine branch with no patients on it is precisely where a change can
  be proven before it reaches anyone, and that is the arrangement already in
  place and working.

Retire `engine/converged` (rescued) and archive `jv-v1-onboarding` (536 behind,
2 obs modules, superseded by `jv_v2`).

## B. How large the env-var tenancy job really is

Measured on `origin/latency-eval`, `app/**/*.py`:

- **53 files** name a clinic or practitioner.
- **4 files** hold a hard clinic conditional (`clinic_id ==`, `booking_system ==`).
- **~622 hits**, ~514 outside obvious comments.

CLAUDE.md's four named offenders are all still offenders — `fast_path.py` (12),
`booking/providers/acuity.py` (6), `booking/booking/utils.py` (5),
`flows/brain.py` (1) — but they are **not the bulk**:

| File | hits |
|---|---|
| `app/tools/receptionist_tools.py` | 155 |
| `app/prompts/susie_system_prompt.py` | 104 |
| `app/prompts/jv_system_prompt.py` | 46 |
| `app/prompts/clinic_template_prompt.py` | 36 |
| `app/routes/admin.py` | 23 |
| `app/routes/twilio.py` | 22 |

`jv_system_prompt.py` and `susie_system_prompt.py` are **dead** — legacy
fallbacks no live clinic reaches. Deleting them removes ~150 hits with no
behaviour change. `clinic_template_prompt.py` is already the parameterised engine;
its hits are mostly token substitution.

**Estimate: 4–6 days.** And it should come *after* the verifier is running:
`receptionist_tools.py` is ~6,000 lines with ~87 bare `except Exception`
handlers, and it cannot be refactored safely against manual test calls.

---

## What should change in the plan

1. **Rewrite F2.** There is no ADR-002 conflict on any shared branch.
   `latency-eval` is canonical and uncontested.
2. **Stage 1 stops being blocking.** It becomes housekeeping (done: 2 commits
   rescued) plus a schedulable convergence job.
3. **Stage 4c was impossible as written** — `cost.py` was not on any live branch.
   Now on `origin/rescue/obs-cost`; merge it before filling the RATES.
4. **Add the two-clone problem to Stage 0.** It is the environment defect that
   actually caused harm, it is cheaper than the OneDrive move, and the plan does
   not mention it. The 2,874 untracked artefacts were a live data-loss risk.
5. **Open question 2 answers itself.** `jv_v2` is the live JV line;
   `jv-v1-onboarding` is retired. `CLAUDE.md` should say so.
6. **Open question 4 is only half-answerable.** The April run is on disk and
   names its 34 failures, but `test_said` is empty throughout, so the caller side
   was never recorded. Re-run rather than mine it.

## What is unchanged and still correct

**F1 stands, and it is the finding that matters.** `tests/auto` is a complete
harness that has not run since April, at 71.2% against a 97% gate. Since then the
only regression detector in the business has been a human phoning in. That is the
constraint worth attacking first, and nothing above weakens it.
