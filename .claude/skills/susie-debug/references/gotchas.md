# Gotchas — the traps, and how to not fall in

Every claim here was measured on 2026-09-14, with the command that produced it.
**Re-run the command rather than trusting the number.** These numbers move, and
this repo's own documents have been wrong about them repeatedly.

---

## 1. Measure against `origin/*`, never local refs

```bash
git fetch --all
git rev-list --left-right --count origin/latency-eval...origin/jv-v1-onboarding
```

This has produced a wrong answer in several sessions. Local refs go stale
silently. Right now, in this very checkout:

```
$ git rev-list --left-right --count origin/jv-v1-onboarding...HEAD
13      2
```

The local working branch is **13 commits behind its own remote**. Any measurement
taken here without fetching first is wrong by 13 commits.

## 2. The tracked documents disagree with the repository

Measured branch state (`git rev-list --left-right --count origin/main...origin/<b>`):

| Branch | vs `origin/main` | Tip |
|---|---|---|
| `origin/latency-eval` | main +155 / branch **+1200** | **2026-09-14** |
| `origin/theorem-onboarding` | main +155 / branch +725 | 2026-08-31 |
| `origin/vitaledge-onboarding` | main +155 / branch +696 | 2026-08-31 |
| `origin/jv-v1-onboarding` | main +155 / branch +136 | 2026-08-07 |
| `origin/main` | — | 2026-07-24 |

`CLAUDE.md` states `latency-eval` is "189 commits ahead of `main` and 142
behind", and that `jv-v1-onboarding` is "352 commits behind with a tip dated
2026-07-24". **Neither matches.** Measured: `latency-eval` is 1,200 ahead and 155
behind; `jv-v1-onboarding` is 1,081 behind `latency-eval` with a tip of 2026-08-07.

Use the table as a method, not as a fact: run the command.

## 3. Engine work lands on `origin/latency-eval` — settled 2026-09-14

**Decided by the repo owner: `origin/latency-eval` is where engine work lands.**
Measure against it, derive references from it, and commit engine fixes to it.

This supersedes ADR-002, which named `engine/converged` canonical. That branch
exists **only in local clones** and its tip is the ADR commit itself:

```bash
$ git rev-parse --verify origin/engine/converged
fatal: Needed a single revision
$ git log -1 --date=short --pretty='%ad %h %s' engine/converged
2026-08-06 7f239ef8 docs: ADR-002 — ratify engine/converged, retire ...
```

Meanwhile `origin/latency-eval` is still taking commits. ADR-002 was ratified on
paper and never enacted; `docs/plan/BRANCH_DECISION.md` should be updated to say
so.

Clinic branches still inherit engine fixes by cherry-pick. A fix that would have
to be repeated per clinic belongs in a shared module instead — see §6.

## 4. `git worktree` — check where you are before measuring anything

```bash
$ git worktree list | wc -l
19
$ git rev-parse --abbrev-ref HEAD
```

19 registered worktrees in this clone. `git worktree prune` has failed here with
*Permission denied* (OneDrive file locking) — pause OneDrive sync and close
editors first, or the prune silently does nothing.

Run `git rev-parse --abbrev-ref HEAD` before you trust a single measurement.

## 5. Commit dates in this repo are not reliable

```bash
$ git log -2 --date=iso --pretty='%ad %h %s'
2026-07-21 14:13:06 +0100 c34984cc ...
2026-07-21 14:13:06 +0100 4630dda6 ...
```

Two commits made weeks apart carry a byte-identical timestamp, and both predate
the system clock (2026-09-14). No `GIT_*_DATE` override is set in the environment
or config — cause unknown.

**Consequence:** `known-bugs.md` reasons about "was this fixed before or after
that". When the answer matters, order by **topology** (`git merge-base
--is-ancestor A B`), not by date.

## 6. Clinic behaviour belongs in `clinic.json`, never in engine code

If you are about to write `if clinic == "..."` in `app/`, stop — that is the bug,
not the fix. Clinic data lives in `app/clinics/<clinic_id>/clinic.json` and
`knowledge.md`.

This is not style advice. Class 2 in `known-bugs.md` was fixed **ten times in
twenty-five days** because name capture was being repaired per clinic; the cure
was one canonical module shared byte-identically
(`name_collector.py`, blob `c5c74efa` on all four live branches). Ask of every
fix: *would this have to be repeated on another branch?* If yes, it is in the
wrong place.

## 7. Latency is a correctness requirement

The bar: **p95 caller-perceived turn latency under 1.5 s**, and no dead air over
3 s without a filler or acknowledgement. On a live call, a hanging provider call
is silence in a real patient's ear.

- Outbound HTTP call sites largely have **no explicit timeout**. On
  `origin/latency-eval` a crude single-line grep finds 33 call sites with a
  timeout named on just 1 (21 client constructions may set one centrally):

  ```bash
  git grep -nE "(httpx|requests|aiohttp)\.(get|post|put|delete)\("       origin/latency-eval -- 'app/**/*.py'
  ```

  That undercounts (multi-line calls, clients configured elsewhere) — but the
  direction is not in doubt. If you touch a call site, give it a timeout.
- **Do not add dependencies** without asking. Cold start on Render affects
  first-call latency.
- Timing changes in `connection.py` are the easiest place in this codebase to
  trade one silence bug for another — see `known-bugs.md` class 4, where the
  first barge-in fix was reverted within 24 hours. Measure both directions.

## 8. Broad exception handling hides the worst failure mode

Measured on **`origin/latency-eval`** (`grep -c "except"` and
`grep -cE "except Exception|except:"`):

| File | `except` clauses | broad (`except Exception` / bare) |
|---|---|---|
| `app/tools/receptionist_tools.py` | 163 | **127** |
| `app/media_streams/flow.py` | 95 | 42 |

The same files on `jv-v1-onboarding` show 97/81 and 94/41 — a clinic branch
understates this by half. Measure on the canonical branch.

This is the most likely cause of the worst outcome this system has: **the call
sounds perfect and the booking silently never happened.**

When you touch one of these, "add logging" is not the fix. Decide whether the
failure is recoverable. If it is not, surface it to the caller *and* to an
operator.

## 9. `flow.py` is frozen, not refactorable

On `origin/latency-eval`, `handle_transcript()` is a single **16,010-line** async
method (line 5894 → 21904). `ask_current_question()` is 2,109 (3785).
`_handle_mid_flow_interrupt()` is 1,167 (22552).

Policy: **freeze, don't refactor.** Change `handle_transcript` only to fix a
specific reproduced defect, in the smallest possible diff, with a regression test
that fails before and passes after. No restructuring, no "while I'm here"
cleanup.

Corollary from `known-bugs.md` class 8 — five NameError/UnboundLocalError bugs
reached production, two labelled ship-blockers. In a file this size, removing a
symbol is not a local change:

```bash
grep -rn "<symbol>" app/ | grep -v __pycache__
python -c "import app.media_streams.flow, app.media_streams.connection"
```

## 10. The suite, and what "green" means

```python
# tests/auto/config.py
MIN_PASS_RATE = 0.97          # 97% to be clinic ready
USE_DIRECT_WS = True          # default: free, no real Twilio calls
MAX_CALL_DURATION_SECONDS = 480
MAX_TURNS_PER_CALL = 20
```

- `USE_DIRECT_WS` defaults to **true** — runs are free. Set `USE_DIRECT_WS=false`
  or pass `--real-calls` for real Twilio calls, which cost money.
- Results land in `tests/auto/results/` as a **pair**: `results_<ts>.json` (full
  records, checks, transcripts, `turn_traces`) and `report_<ts>.txt` (summary).
  The JSON is the one worth reading.
- Every behavioural fix ships with a regression test in `tests/regression/`.

## 11. Two call paths exist

The legacy webhook/`<Gather>` path in `app/routes/twilio.py` and the Media
Streams WS path. `tests/auto/config.py` documents three numbers, routed
differently — `+447426779875` → `/ms/incoming`, `+447367002651` →
`/twilio/voice`, `+447366530580` → the two-clinic test line.

A fix applied to only one path will look correct in tests and be absent on a real
call, or the reverse. When a fix touches turn-taking or greetings, check both.

## 12. Plan documents are sometimes untracked

Some files in `docs/plan/` are not in git, so history searches will not find them.
Use `ls`. And when two documents disagree, `git ls-tree <branch> -- <file>`
settles which one is tracked — trust tracked over untracked, `origin/*` over
local, and **the code over both**.
