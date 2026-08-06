# Branch convergence — measured analysis

**Written:** 2026-08-06. **Settles:** `BRANCH_DECISION.md` (open, blocking).
**Feeds:** `COHORT_1_PLAN.md` §3–4.2.

All figures below are from `git` against `origin/*` refs on 2026-08-06, after
`git fetch --all`. Local `latency-eval` is stale — **do not measure against
local refs**, which is how the earlier 19/19/18/17/2 obs count was got wrong.

---

## 1. Ahead / behind

| Pair | ahead | behind | Merge base |
|---|---|---|---|
| `latency-eval` ↔ `vitaledge-onboarding` | 22 | 9 | `4f4803e` — 2026-08-04 |
| `latency-eval` ↔ `theorem-onboarding` | 19 | 54 | `56cd5d3` — 2026-08-04 |
| `vitaledge-onboarding` ↔ `theorem-onboarding` | 9 | 57 | `4f4803e` — 2026-08-04 |
| `latency-eval` ↔ `jv-v1-onboarding` | **352** | **16** | `95c4fdb` — **2026-07-12** |
| `main` ↔ `theorem-onboarding` | 155 | 506 | `a45d6ed` — 2026-06-24 |

Tips:

| Branch | Tip | Date |
|---|---|---|
| `theorem-onboarding` | `0200162` | 2026-08-05 |
| `vitaledge-onboarding` | `58ec8fe` | 2026-08-05 |
| `latency-eval` | `265d95e` | 2026-08-05 |
| `jv-v1-onboarding` | `2963ed8` | **2026-07-24** |
| `main` | `3da3a17` | **2026-07-24** |

---

## 2. What this actually shows

**There are not four diverged branches. There are three tightly-clustered live
branches, one frozen branch, and one abandoned branch.**

```
                    2026-06-24  main forks away ─────► main (ABANDONED, tip 07-24)
                                                        no FM commits at all
  latency-eval ──┬── 2026-07-12 ──► jv-v1-onboarding (FROZEN, tip 07-24)
                 │                   352 behind, 16 ahead
                 │
                 └── 2026-08-04 ──┬─► vitaledge-onboarding  (9 apart)
                                  └─► theorem-onboarding    (54 ahead)
```

**Was JV "the latency-eval branch"?** It was, on 12 July. It forked, then froze
on 24 July — which is exactly what `STATUS.md` instructed:

> *"jv-v1-onboarding and vitaledge-onboarding are FROZEN … no more pushes before
> the meeting unless it's a genuine live-patient emergency."*

The freeze worked as designed. What happened next is the problem: **Vital Edge
was un-frozen and re-converged on 4 August; Joint Venture never was.** JV is
still sitting where the July freeze left it, 352 commits behind, and has quietly
become the odd one out by omission rather than decision.

**Are Theorem and Vital Edge "the same apart from clinic content"?** No.
`app/` diff, `vitaledge-onboarding` → `theorem-onboarding`:

| File | Lines changed |
|---|---|
| `app/media_streams/connection.py` | **799** |
| `app/prompts/susie_system_prompt.py` | **768** |
| `app/media_streams/llm_stream.py` | 333 |
| `app/tools/receptionist_tools.py` | 271 |
| `app/prompts/clinic_template_prompt.py` | 212 |
| *(19 files, 1,862 insertions, 916 deletions)* | |

That is engine behaviour, not clinic config. But direction matters:
`latency-eval` → `vitaledge-onboarding` touches `connection.py` by only **9
lines**. So `vitaledge ≈ latency-eval`, and **`theorem-onboarding` is the most
advanced engine of the three** — it is 54 commits ahead of the branch the plan
docs call canonical.

---

## 3. The divergence tax, measured

The same fixes have been applied twice, by hand, on 2026-08-05:

| Fix | On `latency-eval` | On `theorem` | `git patch-id` |
|---|---|---|---|
| T-2 — operator paged about a *successful* booking | `e6fed61` | `c585fff` | `d46f709332` — **identical** |
| T-3 — bare FAQ answer left the caller in silence | `9f69b91` | `f35ba8a` | `4e1ca160aa` — **identical** |
| session synthesis doc | `0db6cbb` | `918b919` | `2908a63fda` — **identical** |

Byte-identical patches, different SHAs, two branches, same day. B-57 (Theorem
could not cancel) was likewise fixed on both lineages via different commits.

**This is the fix-once-per-branch tax, and it is already being paid at four
clinics.** It is the single strongest argument in this document. At ten clinics
it consumes the entire week and improvement stops permanently.

---

## 4. Observability — the earlier claim was wrong

| Branch | `app/obs/` modules |
|---|---|
| `theorem-onboarding` | 19 |
| `vitaledge-onboarding` | 19 |
| `latency-eval` | 19 |
| `main` | 17 |
| `jv-v1-onboarding` | **2** |

The three live branches carry an **identical module list**. There is one
observability codebase, not four.

`jv-v1-onboarding` has `__init__.py` and `alerts.py` only — from
`f4d6071 "Joint Venture: operator failure-alerting, ported from Theorem obs
(§5.2)"`. So JV **does** page an operator on failure, but has **no `store.py`
(no call capture), no `judge.py`, no `digest.py`, no `models.py`.**

Practical meaning: JV alerts on hard failures but produces no call record, no
quality score, and no daily digest. It contributes nothing to the improvement
flywheel. That is a real gap, but a narrower and more fixable one than "JV has
no observability."

---

## 5. `main` is abandoned

Tip 2026-07-24. 506 commits behind `theorem-onboarding`. **Zero FM-01/25/23
commits** — the only branch missing the booking-safety gates entirely.

`main` is the default branch and the PR target. Nothing has been merged to it in
two weeks and no live clinic runs from it. It must not be a convergence base,
and the risk that someone treats it as one — because it is called `main` — is
exactly the kind of mistake this document exists to prevent.

---

## 6. Carry-forward trap: CLOSED

`STATUS.md` warns FM-01/25/23 live only on the clinic lineage. **Verified false
as of today.** All three regression tests are byte-identical across all four
live/frozen branches, and the guard code is present in `flow.py` on each:

| Gate test | md5 (jv-v1 = latency-eval = theorem) |
|---|---|
| `test_book_affirmative_gate.py` | `0dc9ff54` |
| `test_cancel_reschedule_gate.py` | `af338084` |
| `test_write_ack_filler_gate.py` | `caf787d3` |

Convergence must still **assert** this — run the three as a merge gate — but
there is nothing to port. This was the single scariest item on the board and it
is already handled.

---

## 7. Recommendation

### 7.1 The rule already broke. Ratify reality rather than unwind it.

> **Revised 2026-08-06, second pass.** An earlier draft framed this as
> "converge on `theorem-onboarding`, not `latency-eval`, contradicting ADR-001."
> That framing was wrong, and it was wrong because it was written against a
> **stale, untracked `CLAUDE.md`** in the working tree (224 lines) rather than
> the tracked 247-line version on the live branches. See §8.

The repo has **already decided this**, and the decision is not in dispute:

- `BRANCH_DECISION.md` on the live branches reads **Status: Accepted
  (2026-07-21)** — not "Proposed", not "blocking".
- Tracked `CLAUDE.md`: *"`latency-eval` is THE engine branch. Settled
  2026-07-22 — no longer contested."*
- A branch named `release/cohort-1` was already cut, **merged into
  `latency-eval`, and retired.** The name is burnt; do not reuse it.
- The governing convention is the **canonical-first rule**: *"every engine fix
  commits to `latency-eval` first; the live clinics inherit it. Never fix on a
  clinic branch and port up — that strands safety fixes at convergence."*

**The rule is sound. It is also being violated at scale.**

> **33 engine commits (`app/`) exist on `theorem-onboarding` that never landed
> on `latency-eval` first.**

That is the finding. Not "which branch should be canonical" — that was settled
a fortnight ago — but that the canonical-first *process* has broken down, and
`latency-eval` is consequently no longer the superset the rule assumes it is.
The byte-identical T-2/T-3 duplicate patches in §3 are the symptom: someone
applying the rule by hand, twice, on the same day.

So the choice is not theorem-vs-latency-eval on the merits. It is:

| Option | Cost |
|---|---|
| **Unwind** — replay 33 engine commits onto `latency-eval`, then re-inherit | High, and every one is a re-merge into files Theorem has since moved |
| **Ratify** — take `theorem-onboarding` as the base, port `latency-eval`'s 7-commit delta back | Low — see §7.2 |

**Ratify.** `theorem-onboarding` carries the full 19-module obs set, the FM
gates, `clinical_screening.py` present *and wired* (`connection.py:7628`), and
it is the engine answering a real clinic's phone since 2026-08-05. The 33
commits already exist and are already live; unwinding them buys nothing.

This supersedes ADR-001 and needs recording as **ADR-002**, not as a silent
deviation. The failure to document it is how the repo got here.

Cut the base as **`engine/converged`**. Not `release/cohort-1` — that name was
used and retired, and reusing it guarantees a future reader conflates the two.

### 7.1b The real fix is to make the rule unnecessary

Canonical-first-by-cherry-pick is manual convergence, performed by a human,
forever. It broke here after roughly two weeks. It will break again at six
clinics regardless of who is being careful.

Stage 3 of the runbook — **one codebase, clinic identity by env var** — is what
retires the rule. Until then, every fix is a cherry-pick someone has to
remember.

### 7.2 Port the small delta from `latency-eval`

Of the 19 commits `latency-eval` has that `theorem` lacks:

- **~7 are genuine engine work to port** — fillers (`265d95e`, `4eb1e0c`),
  condition-led opening (`57d5b67`), B-39 retention scope (`3d5d0b8`),
  home visits (`500282d`), deposit policy (`94fc900`), callback-vs-abandoned
  SMS (`98be4c5`)
- **3 are Vital Edge clinic-specific** (`57bc90e`, `bec1b5e`, `ecca460`) — these
  belong in `clinic.json`, not the engine. Porting them as code repeats the
  original sin.
- **3 are already-duplicated patches** (T-2, T-3, session doc) — skip, verify
  by patch-id
- **5 are docs**, 1 is eval-harness only (`c69ec2c`)

So the real port is roughly seven commits, not nineteen.

### 7.3 Then, in order

1. Cut `engine/converged` from `theorem-onboarding`.
2. Port the ~7 engine commits from `latency-eval`. Verify by patch-id that
   nothing already present is applied twice.
3. **Gate:** the three FM tests pass on `engine/converged`.
4. Migrate **JV first** — it is 352 commits behind and has no deadline pressure,
   so it is both the biggest win and the safest pilot. Its 16 unique commits are
   already listed and mostly duplicated upstream; the genuinely JV-only ones are
   the live-transcript demo feed (`a57ab78`), the name-recovery blocklist
   (`f959212`) and the greeting re-ask timing (`4464591`, superseded by
   `b8ddd8b`). Everything else is config.
5. Move clinic identity to env var per Render service. One codebase, N services.
6. Migrate Vital Edge (which is 9 commits from base — trivial), then Theorem
   (which *is* the base).
7. Delete or archive `latency-eval` as a lab branch. **Retire `main` or reset it
   to `release/cohort-1`** so the default branch stops being a trap.

### 7.4 What not to do

- **Do not merge `main` into anything.** 506 commits behind, no safety gates.
- **Do not port the three `fix(ve):` commits as engine code.** They are clinic
  behaviour and belong in `clinic.json` — `CLAUDE.md` is explicit that
  `if clinic == "..."` in `app/` is the bug, not the fix.
- **Do not attempt full runtime multi-tenancy now.** One codebase + env-var
  tenant selection is the 80% win in the time available. Self-serve tenancy is
  post-cohort.
- **Do not reuse the name `release/cohort-1`.** It was cut, merged into
  `latency-eval`, and retired. Reusing it guarantees a future reader conflates
  the two lineages. Use `engine/converged`.

---

## 8. Root cause: two `CLAUDE.md` files disagree

This is the most important finding in the document, because it is the mechanism
that produced every other error here — including two in this analysis's own
first draft.

| | Lines | Tracked? | Says |
|---|---|---|---|
| Working tree, `jv-v1-onboarding` | 224 | **No — untracked** | Branch decision *"contested and must be settled before any work starts"*; `BRANCH_DECISION.md` *"open, blocks everything"* |
| `theorem-onboarding` / live branches | 247 | Yes | *"`latency-eval` is THE engine branch. Settled 2026-07-22 — no longer contested."* |

`CLAUDE.md` is not tracked on `origin/jv-v1-onboarding` at all. The copy in the
working tree is a **stale fork of a document that was superseded on 2026-07-22**,
and it is the copy an agent starting work in this directory reads first.

Consequences observed in a single session:

1. A whole analysis written on the premise that the branch decision was open,
   when it had been Accepted for a fortnight.
2. A recommendation framed as "contradict ADR-001" when the correct framing was
   "ADR-001's process broke; ratify and supersede it."
3. A new branch cut under a **retired name**.
4. Earlier: `app/obs/` file counts taken from a stale *local* `latency-eval`,
   producing "four different versions of the improvement engine" — false.

The same class of error is already logged in `BRANCH_DECISION.md` §Measurement
provenance ("it already produced a wrong number this session") and in
`CLAUDE.md`'s own worktree warning. **It keeps happening because the warning
lives in the document that is itself stale.**

### Fix, in order

1. **Delete the untracked 224-line `CLAUDE.md` from the working tree** and
   replace it with the tracked version. One file, one source of truth.
2. **Track `docs/plan/`.** 11 plan documents — including `STATUS.md` and the
   whole `JULES_*` handoff set — are untracked in the working tree. The plan for
   the migration currently lives only on the branch being migrated.
3. **Consolidate the two clones.** `BRANCH_DECISION.md` records a second clone
   under `…/GitHub/…` which is *"= origin, = what Render deploys"*, while this
   one holds unpushed work. Two clones with divergent pointers is FM-20 waiting
   to happen.
4. **`git worktree prune` cannot complete** — 65 stale registrations fail with
   *Permission denied* (OneDrive file locking), not the *"~15, most prunable"*
   `CLAUDE.md` claims. Close editors/OneDrive sync and re-run, or the
   wrong-tree trap stays armed.
