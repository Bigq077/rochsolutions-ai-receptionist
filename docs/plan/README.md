# docs/plan — what's live

Housekeeping pass **2026-08-16**: Wave 1 adversarial complete; dated demo/session
docs from 5–8 Aug moved to [`archive/`](archive/). Working set below.

**If these documents and the code disagree, the code wins.** Record a correction
in §Corrections below.

---

## Start here

| # | Document | What it is |
|---|---|---|
| 1 | [`../../CLAUDE.md`](../../CLAUDE.md) | Repo context, architecture, hazards, conventions |
| 2 | [`BRANCH_DECISION.md`](BRANCH_DECISION.md) | Which branch is the production base (ADR) |
| 3 | [`PRODUCTION_READINESS_PLAN.md`](PRODUCTION_READINESS_PLAN.md) | Phased plan with gates |
| 4 | [`FAILURE_MODE_REGISTER.md`](FAILURE_MODE_REGISTER.md) | Ranked risk register (FM-nn) |
| 5 | [`REGISTER_B_U.md`](REGISTER_B_U.md) | **Live defect queue** — `B-nn` / `U-nn` |
| 6 | [`SKILL_PLAYBOOK.md`](SKILL_PLAYBOOK.md) | Which engineering skill when |
| 7 | [`KICKOFF_PROMPT.md`](KICKOFF_PROMPT.md) | Paste-ready first message |

Phase 0 templates: `TEST_BASELINE.md`, `DELETED_TEST_TRIAGE.md`,
`LATENCY_BASELINE.md`, `DEPLOYMENT_INVENTORY.md`.

> ⚠️ **ID collision.** `archive/DEFECT_REGISTER.md` uses `B1`/`B2`/`B3` (no
> hyphen) for an older obs sweep. Unrelated to `B-01`… in `REGISTER_B_U.md`.

---

## This week (Jules / Quentin away)

| Document | What it is |
|---|---|
| [`HANDOVER_JULES_5DAY_2026-08-11.md`](HANDOVER_JULES_5DAY_2026-08-11.md) | Standing rules, Jobs 1–3, adversarial scripts A1–A10 |
| [`JOB2_WAVE1_SYNTHESIS_2026-08-16.md`](JOB2_WAVE1_SYNTHESIS_2026-08-16.md) | **For Quentin** — Wave 1 + Job 1 Emma verdict |
| [`JOB2_WAVE1_FINDINGS_2026-08-16.md`](JOB2_WAVE1_FINDINGS_2026-08-16.md) | Batch 1 fix queue + SIDs |
| [`ADVERSARIAL_SESSION_2026-08-15.md`](ADVERSARIAL_SESSION_2026-08-15.md) | Live call sheet A1–A10 |
| [`JOB3_SYNTHESIS_2026-08-15.md`](JOB3_SYNTHESIS_2026-08-15.md) | Job 3 closed — JV call-proven |
| [`JOB3_STATUS_2026-08-14.md`](JOB3_STATUS_2026-08-14.md) | Job 3 SID/SHA tracker |

---

## Clinic work in flight

| Document | Clinic |
|---|---|
| [`THEOREM_PORT_PLAN.md`](THEOREM_PORT_PLAN.md) | Theorem → current engine |
| [`THEOREM_ACCEPTANCE_REGISTER.md`](THEOREM_ACCEPTANCE_REGISTER.md) | Theorem live defects / acceptances |
| [`VITALEDGE_PORT_PLAN.md`](VITALEDGE_PORT_PLAN.md) | Vital Edge convergence |
| [`VITALEDGE_ACCEPTANCE_SUITE.md`](VITALEDGE_ACCEPTANCE_SUITE.md) | VE accept cases |

---

## Archive

[`archive/`](archive/) — Jules briefs, old call suites, demo sweeps (incl.
`DEMO_SWEEP_2026-08-05`, `SESSION_2026-08-05`, `CANONICAL_BACKPORT_2026-08-08`),
superseded queues. See [`archive/README.md`](archive/README.md).

---

## Corrections log

| # | Date | Finding |
|---|---|---|
| 15 | 2 Aug | **`latency-eval` is not a live deploy** — gated branches are the clinic ones |
| 16 | 3 Aug | Live queue is `REGISTER_B_U.md` (`FIX_QUEUE_PRE_DEMO` archived) |
| 17 | 3 Aug | **`/health` is useless for SHA** — only `[build_info] running build <sha>` |
| 18 | 5 Aug | Standing "~95 failures" baseline is stale — measure your own |
| 19 | 5 Aug | Housekeeping — dated session docs → `archive/` |
| 20 | 15–16 Aug | Job 3 closed; Job 2 Wave 1 complete; Emma CA3b303f fixed (`ffceb94` / theorem `02fd991`); Batch 1 queued in findings |
| 21 | 16 Aug | Housekeeping — 5–8 Aug demo/session docs → `archive/`; live index is this file |
| 22 | 17 Aug | **A fix can be wrong inside the flow it repairs, and the suite stays green.** B1.2 shipped twice with a defect in the reschedule path it was written for — a re-ask the move gate could not recognise (`36a7e5b`), then a slot window armed and destroyed by one reply (`3b6695e`). Both found by *running* the predicates, neither by reading the code. Run a new phrase through the gate that will read it, and a new end-of-turn cleanup against the same turn's arming |

If you find another contradiction, the code wins — add a row above.
