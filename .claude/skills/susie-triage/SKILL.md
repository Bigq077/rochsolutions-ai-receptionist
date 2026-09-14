---
name: susie-triage
description: Cluster many failing Susie test calls into a short ranked list of root causes. Use after a tests/auto suite run finishes with failures, for a weekly sweep over recent runs, or when asked "what is actually wrong with Susie right now". Decides WHAT to fix; susie-debug then fixes one thing.
---

# susie-triage

**This skill clusters. It does not debug.** It turns thirty-four failing calls
into four or five root causes, ranked by patient impact, and hands them to
`susie-debug`. If you find yourself opening `flow.py` to read logic, you have
left triage and started debugging — stop, finish the list, then switch.

Output: `docs/triage/TRIAGE_<YYYY-MM-DD>.md`.

---

## Constraints

- **Read the named files only.** Do not bulk-read `docs/` or the repo root —
  those documents contradict each other and have been wrong repeatedly.
- **Every claim carries evidence**: call sid or scenario id, a signal name, a
  stall state, one quoted line. "Looks like slot selection" is not a finding.
- **Name a hypothesis, not a fix.** Do not edit code in this skill.
- **Same input, same answer.** The collector does the counting.

---

## Step 1 — Establish the window and the branch

```bash
git rev-parse --abbrev-ref HEAD
ls -t tests/auto/results/results_*.json | head -5
```

For suite runs the input is `results_<ts>.json`, never the `report_<ts>.txt`
beside it — only the JSON carries checks, flow step and transcripts.

State the branch in the report: a cluster on one branch may not exist on another.
Engine work lands on `origin/latency-eval`.

## Step 2 — Collect

**Real calls (prefer this)** — they carry a judge score, failure tags and the
caller's actual words, which a synthetic run cannot give you:

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py --obs --days 7
#   --clinic <id>   one clinic   |   --since YYYY-MM-DD   explicit window
```

Needs `OBS_DATABASE_URL` (or `DATABASE_URL`) — **not** `OBS_CAPTURE_ENABLED`,
which only gates *writing*. Run from the repo root on a branch with the full
`app/obs/` package: `origin/latency-eval` has 22 modules, `jv-v1-onboarding` has
2 and cannot be used.

**Suite results.** Same script, same discipline:

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py   tests/auto/results/results_<ts>.json
```

For a weekly sweep over suite runs, pass the directory and `--since YYYY-MM-DD`.

The collector prints the pass rate, candidate clusters, a **stall-state table**,
and one line per failing call with its last utterance and turn traces.

The stall table is usually the answer. Built from `turn_traces`
(`state_before` → `state_after` per utterance), it shows the state a call could
not leave, and `handled_by` names the handler that ate the turn without
advancing. A call stuck in one state was **heard and not understood** — a
different bug from one where nothing was heard.

## Step 3 — Cluster by earliest failing check, in flow order

The collector has grouped by earliest check already. Your job is to correct it
with judgement:

- **`booking_confirmed` is terminal.** It fails whenever anything upstream
  failed, so it will usually be the biggest group and it means nothing on its
  own. **Split it by stall point** — the `flow_step` and the last Susie turn —
  and redistribute those calls into the real clusters.
- **Cross-cutting checks are not locations.** `no_question_asked_twice`,
  `no_state_corruption`, `flow_order_correct`, banned phrases. A repeated
  question is very often the *re-ask loop* of an upstream cluster, not a bug of
  its own. Look at the transcript before granting one its own cluster.
- **Cluster by where the call stopped, not by scenario name.** Phase 4 and Phase
  8 stuck in the same state are **one** cluster, not two.
- **Compare against the passing calls in the same run.** If passes concentrate at
  the final step and failures pile up at one earlier step, that step is the
  break. Failures spread evenly across steps mean something cross-cutting.

See `references/symptom-map.md` for each check's flow position and the subsystem
that produces it. Use the subsystem column only to phrase the hypothesis.

**`--obs` mode has a different vocabulary** — judge `failure_tags` plus derived
signals, ranked by patient impact rather than flow order. Two derived signals are
the point of the mode, because nothing else reports them:

- **`PHANTOM_BOOKING`** — `booking_confirmed` set, but neither Acuity nor Google
  Calendar returned an id: a caller who believes they have an appointment that
  does not exist. Such a call usually reports `success=true` with a top judge
  score. **Always rank it first.**
- **`DORMANT_SCREENING`** — a red-flag clinical screen armed and never fired
  (`screening.arm_paths` has an `orphan` with no `trigger` anywhere).

Cluster obs calls by `final_state` and `build_sha`. Grouping defects by build is
the question that table is indexed to answer: a cluster confined to one
`build_sha` is a regression with a known blast radius.

## Step 4 — Attribute a candidate cause

For each cluster, name one subsystem and say why the evidence points there.
Where a cluster is ambiguous, say so and name the one command or file that would
settle it. An honest "two candidates, here is the discriminator" is worth more
than a confident wrong attribution.

## Step 5 — Separate stale scenarios from real regressions

Not every failure is Susie's. A scenario is **stale** when it asserts behaviour
the flow no longer has. `tests/auto/fixer.py` already makes this call for some
cases — read its `_SUSIE_BUG_MAP` and its greeting-wait auto-fix before
deciding. Known stale shapes:

- `duration_question_asked` — removed from the standard flow; the evaluator
  prompt says to return null for it.
- Any check asserting a greeting or question the current `BOOKING_FLOW` in
  `app/media_streams/flow.py` does not contain.
- `turns == 0` with `end_reason == "completed"` — harness greeting-wait, not Susie.

Report stale clusters separately and never rank them against real ones.

## Step 6 — Detect cascades

Flag cluster B as downstream of A when **every** call in B also shows A, and A
has calls of its own. Say it plainly: "B never occurs without A — fix A first
and re-run before treating B as real." Do not rank a downstream cluster.

## Step 7 — Rank by patient impact, not by count

In order:

1. **A booking the caller believes exists but does not** — silent failure.
2. A booking that failed to complete at all.
3. A call the caller had to abandon, or that reached a human unnecessarily.
4. Wrong information given (price, hours, insurance, location).
5. Clumsy phrasing, banned words, minor order issues.

A five-call cluster at severity 1 outranks a twenty-call cluster at severity 5.
Say the count, but never rank on it.

## Step 8 — Write the report

`docs/triage/TRIAGE_<YYYY-MM-DD>.md`, using
`references/report-template.md`. Per cluster: rank, severity, one-line cause
hypothesis, affected scenario ids, the stall evidence, **one** representative
transcript excerpt, and the suggested next action (usually: hand this cluster to
`susie-debug`).

End with what was run and what it returned — the results file, the call count,
the pass rate, the branch.

---

## Done when

- The report names **4–6 root causes**, not a list of every failure. More than
  about eight means the clustering was too weak — go back to step 3 and look
  for the cascade you missed.
- Every cluster carries evidence a reader can check without rerunning anything.
- The largest terminal group has been split, not reported as a cause.
