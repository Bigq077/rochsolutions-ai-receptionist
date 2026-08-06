# Convergence runbook — `engine/converged`

**Written:** 2026-08-06. **Basis:** `BRANCH_CONVERGENCE_ANALYSIS.md`.
**Target:** one engine codebase, clinic identity by env var, before Week 3 gate
(2026-08-28) in `COHORT_1_PLAN.md` §5.

Every command below is copy-pasteable. Every stage has a gate. **Do not proceed
past a failed gate** — the whole point of this exercise is that a fix verified
on one clinic should exist on all of them, and a half-done convergence is worse
than none.

---

> **Naming:** the base is `engine/converged`. **Not** `release/cohort-1` — that
> name was already cut, merged into `latency-eval` and retired in July. See
> `BRANCH_CONVERGENCE_ANALYSIS.md` §7.1.
>
> **This supersedes ADR-001**, which accepted `latency-eval` as the engine
> branch on 2026-07-21 under a canonical-first-by-cherry-pick rule. That rule
> has since been violated by 33 engine commits landing on `theorem-onboarding`
> first. Record the supersession as ADR-002 in `BRANCH_DECISION.md` — do not
> deviate silently.

---

## Stage 0 — Preconditions

`CLAUDE.md` warns of stale worktrees and that a previous session measured the
wrong tree. Clear that first — and note the warning understates it: **65** stale
registrations were found on 2026-08-06, and `prune` failed on all of them with
*Permission denied* (OneDrive locking). Close editors and pause OneDrive sync
first, or this step silently does nothing and the trap stays armed.

```bash
git worktree prune && git fetch --all --prune && git rev-parse --abbrev-ref HEAD
```

**Also fix the two-`CLAUDE.md` problem before anything else** — it is the root
cause in `BRANCH_CONVERGENCE_ANALYSIS.md` §8. The working tree holds an
untracked 224-line `CLAUDE.md` that still calls the branch decision "contested";
the tracked version on the live branches (247 lines) settled it on 2026-07-22.
Delete the untracked copy and take the tracked one, or the next agent to start
here repeats every mistake this document exists to prevent.

Establish the baseline suite on the convergence base **before** touching it:

```bash
git checkout -b engine/converged origin/theorem-onboarding && pytest -q 2>&1 | tail -20
```

**Gate 0:** record pass/fail/skip counts in `TEST_BASELINE.md`. If the suite is
not green, green-or-quarantine it now. A red baseline makes every later gate
meaningless — you cannot tell your breakage from pre-existing breakage.

> **Why `theorem-onboarding` and not `latency-eval`:** it is 54 commits ahead,
> carries the full 19-module obs set, has the FM gates, and is the engine
> currently answering a real clinic's phone. `latency-eval`'s own `LATENCY.md`
> says it is *"a lab, not a release candidate."* See analysis §7.1.

---

## Stage 1 — Port the delta from `latency-eval`

Seven commits, verified by `git patch-id` as genuinely absent from
`theorem-onboarding`. Ordered **safest first** — prompt-only changes, then
single-file engine, then multi-file engine, then the one that needs a human.

Conflict risk is derived from overlap between the commit's files and the files
touched by Theorem's own 54 commits.

| # | Commit | What it fixes | Files | Risk |
|---|---|---|---|---|
| 1 | `94fc900` | Deposit policy was unspeakable | `clinic_template_prompt.py` | **Low** (1 theorem commit touches it) |
| 2 | `500282d` | Both clinics refused home visits they sell | `clinic_template_prompt.py` | **Low** |
| 3 | `3d5d0b8` | B-39 retention question scoped to cancel, asked once | `clinic_template_prompt.py`, `susie_system_prompt.py` | **High** — 14 theorem commits touch `susie_system_prompt.py` |
| 4 | `57d5b67` | Caller who led with a condition was never offered a booking | `clinic_template_prompt.py` | **Low** |
| 5 | `98be4c5` | Promised callback texted as an abandoned booking | `connection.py`, `call_summary.py`, `receptionist_tools.py` | **High** — 9 + 5 theorem commits |
| 6 | `4eb1e0c` | Cancel/reschedule waits sounded like a hold | `filler_phrases.py`, `media_streams/config.py`, `llm_stream.py` | **Medium** |
| 7 | `265d95e` | Three hold phrases in 3.4s from three producers | `filler_phrases.py`, `connection.py`, `llm_stream.py` | **Medium** |

```bash
for c in 94fc900 500282d 3d5d0b8 57d5b67 98be4c5 4eb1e0c 265d95e; do
  echo "=== $c $(git log -1 --format=%s $c) ==="
  git cherry-pick -x "$c" || { echo "CONFLICT on $c — resolve, then: git cherry-pick --continue"; break; }
  pytest -q 2>&1 | tail -3
done
```

**One at a time. Run the suite after each.** If you batch these and the suite
goes red, you will not know which of seven commits did it — and three of them
touch `flow.py`-adjacent engine code that `CLAUDE.md` has under a freeze order.

### 1b — `7090e4c` (B-57) — **review, do not cherry-pick**

`7090e4c "fix(b57): Theorem could not cancel"` exists on `latency-eval`, but
Theorem fixed the same defect independently via `d2a3338 "fix(cancel): open the
gate on a direct CTA…"`. Both touch `test_b57_theorem_cancel_gate.py`.

Cherry-picking risks **double-fixing** — two guards on the same path, which on a
cancel flow means a caller who cannot cancel at all.

```bash
git show 7090e4c -- app/media_streams/llm_stream.py
git show d2a3338 -- app/media_streams/llm_stream.py
git diff origin/latency-eval origin/theorem-onboarding -- tests/regression/test_b57_theorem_cancel_gate.py
```

Read both. Decide which guard survives. **Verify with a live cancel call, not
just the test** — this is a booking-write path.

### 1c — Do NOT port these three

`57bc90e`, `bec1b5e`, `ecca460` — all `fix(ve):`. These are Vital Edge
*clinic behaviour* (under-18 gating, reason-question wording), currently encoded
as engine code. Porting them as code repeats the original sin.

> `CLAUDE.md`: *"If you find yourself writing `if clinic == "..."` in `app/`,
> stop — that is the bug, not the fix."*

Move them into `app/clinics/vital_edge/clinic.json` at Stage 3. Log them in
`FAILURE_MODE_REGISTER.md` as config-debt so they are not silently dropped.

`c69ec2c` (eval staff-SMS redirect) is eval-harness only — optional. It is a
safety net that stops test runs texting real practitioners, so porting it is
cheap insurance, but it is not on the critical path.

---

## Stage 2 — Gate: the FM safety gates

Non-negotiable. These three guard the worst failure mode in the system — the
call sounds perfect and the booking silently never happened.

```bash
pytest -q tests/regression/test_book_affirmative_gate.py \
          tests/regression/test_cancel_reschedule_gate.py \
          tests/regression/test_write_ack_filler_gate.py
```

Confirm the files are still the known-good versions, unchanged by the ports:

```bash
for f in test_book_affirmative_gate test_cancel_reschedule_gate test_write_ack_filler_gate; do
  echo "$f $(md5sum tests/regression/$f.py | cut -c1-8)"
done
```

Expected: `0dc9ff54`, `af338084`, `caf787d3`.

**Gate 2:** all three pass AND hashes match. If a hash moved, a cherry-pick
touched a safety gate — stop and read the diff before going further.

---

## Stage 3 — Clinic identity by env var

The structural change. One codebase, N Render services.

Hardcoded clinic names currently live in `app/fast_path.py`,
`app/flows/brain.py`, `app/booking/booking/utils.py`,
`app/booking/booking/providers/acuity.py`. Find them all:

```bash
grep -rn "theorem\|vital_edge\|jv_v1\|Alcester\|Redditch" --include="*.py" app/ | grep -v "^app/clinics/" | grep -vi test
```

There is already an audit script for exactly this on Theorem —
`scripts/audit_theorem_literals.py`. Generalise it rather than writing a new one,
and wire it into the suite so a new hardcoded clinic name fails CI.

1. `CLINIC_ID` env var → `app/clinic_loader.py` selects the config.
2. Replace each hardcoded literal with a `clinic.json` lookup.
3. Move the three Vital Edge behaviours from §1c into
   `app/clinics/vital_edge/clinic.json`.
4. Per-practitioner calendar vars (`ACUITY_CALENDAR_ID_ALCESTER` / `_REDDITCH` /
   `_MARK` / `_LEANNE`) move into `clinic.json` under the clinic that owns them.

**Gate 3:** the audit script reports zero hardcoded clinic literals outside
`app/clinics/`, and the full suite is green with `CLINIC_ID` set to each of
`theorem`, `vital_edge`, `jv_v1` in turn.

---

## Stage 4 — Migrate JV first

JV is 352 commits behind and has no deadline pressure. Biggest win, safest
pilot. **This is also where you time the onboarding runbook** (`COHORT_1_PLAN`
§4.5) — that number sets your cohort cap.

JV's 16 unique commits are mostly already upstream. Genuinely JV-only:

| Commit | Keep? |
|---|---|
| `a57ab78` public live-transcript feed (`app/routes/demo.py`) | **Yes** — this is the website demo call |
| `f959212` block body-parts/practitioner/filler in name recovery | **Yes** — port as engine, it is not JV-specific |
| `4464591` first-turn silence re-ask 4.5s | **No** — superseded by `b8ddd8b` (6s) |
| `1fd7b20` escape literal braces in `get_system_prompt` f-strings | **Check** — verify the bug cannot recur on the new base |

Everything else is config → `app/clinics/jv_v1/clinic.json`, or already present
upstream. Verify by patch-id before assuming:

```bash
git log --format=%H --no-merges origin/latency-eval..origin/jv-v1-onboarding | while read c; do
  p=$(git show $c | git patch-id --stable | cut -d' ' -f1)
  echo "$(git log -1 --format='%h %s' $c | cut -c1-70) :: $p"
done
```

**JV also gains full observability here** — it goes from 2 obs modules to 19.
Provision `OBS_DATABASE_URL`, run `python -m app.obs.migrate`, enable
`OBS_CAPTURE_ENABLED`. That is the flywheel finally switching on for JV.

**Gate 4:** JV live on `engine/converged`, three consecutive clean live calls
including one real booking verified present in the booking system, obs capturing
rows. Onboarding elapsed time recorded.

---

## Stage 5 — Vital Edge, then Theorem

**Vital Edge** is 9 commits from base — nearly trivial. This is also the
voicemail → own-number move, so it is the first real exercise of the onboarding
runbook on a clinic that is changing its telephony at the same time. Do the
convergence and the number move as **two separate deploys**, not one.

**Theorem** *is* the base. It should be a no-op plus `CLINIC_ID=theorem`.
It is also the only clinic live with real patients as of 2026-08-05 — deploy it
last, when the path has been proven twice.

**Gate 5:** all three clinics on one codebase. A trivial one-line change
deploys to all three from a single commit. **That is the definition of done for
this runbook.**

---

## Stage 6 — Clean up the trap

`main` is 506 commits behind, has **zero** FM safety-gate commits, and is the
default PR target. Someone will eventually converge onto it because of its name.

```bash
git push origin engine/converged:main --force-with-lease   # only after Gate 5
```

Then set `engine/converged` (or `main`) as the default branch in GitHub, and
**pin the branch per service in the Render dashboard** — `render.yaml` has
`autoDeploy: true` and no branch pin, which is FM-20: a push to the wrong branch
changes what answers a real clinic's phone with no review step.

Archive `latency-eval` as a lab branch. Do not delete it — `LATENCY.md` and the
baseline measurements live there.

---

## Rollback

At every stage the previous branch still exists and Render can be pointed back
at it in under a minute. That is the rollback path — **there is no clever
in-place recovery, and you should not build one.**

| Stage | Rollback |
|---|---|
| 1–3 | Nothing is deployed. Delete `engine/converged`, start again. |
| 4 | Point JV's Render service back at `jv-v1-onboarding`. |
| 5 | Point the affected service back at its own branch. |
| 6 | `main` is force-pushed — **tag the old tip first**: `git tag archive/main-2026-08-06 origin/main && git push origin archive/main-2026-08-06` |

**Named on-call human for the migration window: Quentin.** Do not run Stage 4 or
5 during clinic opening hours.

---

## Do not

- **Merge `main` into anything.** 506 behind, no safety gates.
- **Blind `git merge`** between any two of these branches. Every step here is
  cherry-pick or deliberate file-by-file. `CLAUDE.md` is explicit.
- **Refactor `flow.py` while doing this.** It is 24,820 lines with a
  15,734-line `handle_transcript()`, and it is under a freeze order. Convergence
  is already the largest change this codebase will absorb before September.
- **Attempt full runtime multi-tenancy.** Stage 3 is env-var tenancy, which is
  the 80% win. Self-serve onboarding is post-cohort.
