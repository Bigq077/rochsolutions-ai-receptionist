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
  those documents contradict each other and have been wrong repeatedly. The
  result records and the code are the evidence.
- **Every claim carries its evidence**: scenario ids, a check name, a flow step,
  one quoted transcript line. "Looks like slot selection" is not a finding.
- **Name a hypothesis, not a fix.** A cluster's cause line is where `susie-debug`
  should start looking. Do not edit any code in this skill.
- **Same input, same answer.** The collector script does the counting so two
  runs of this skill on one results file agree.

---

## Step 1 — Establish the window and the branch

```bash
git rev-parse --abbrev-ref HEAD
ls -t tests/auto/results/results_*.json | head -5
```

A results file (`results_<ts>.json`) is the input, not the `report_<ts>.txt`
beside it — the JSON carries checks, flow step, and transcripts; the text report
carries only the first failing check.

State the branch in the report. A cluster found on one branch may not exist on
another; `jv-v1-onboarding` and `engine/converged` are different engines.

## Step 2 — Collect

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py \
  tests/auto/results/results_<ts>.json
```

For a weekly sweep, pass the directory and `--since YYYY-MM-DD`.

The collector prints: the pass rate, candidate clusters grouped by earliest
failing check, a **stall-state table**, a pass-vs-fail `flow_step` table, and one
line per failing call with its last utterance and last three turn traces.

The stall table is usually the answer. It is built from `turn_traces`
(`state_before` → `state_after` per caller utterance), so it shows the state a
call could not leave — and `handled_by` shows which handler ate the turn without
advancing. A call stuck in one state was **heard and not understood**, which is a
different bug from one where nothing was heard. Read all of the output before
forming a view.

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
- **Compare against the passing calls in the same run.** The `flow_step` table
  makes this direct: if passes concentrate at the final step and failures pile
  up at one earlier step, that step is the break. If failures are spread evenly
  across steps, you are looking at something cross-cutting instead.

See `references/symptom-map.md` for each check's flow position and the subsystem
that produces it. Use the subsystem column only to phrase the hypothesis.

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
