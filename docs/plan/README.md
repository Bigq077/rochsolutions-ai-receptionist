# docs/plan — what's live

Housekeeping pass **2026-08-05**: dated session briefs, expired countdown docs,
and one-off verify scripts moved to [`archive/`](archive/). If you need them,
they're there — they are not the working set.

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

Phase 0 templates (fill / keep current): `TEST_BASELINE.md`,
`DELETED_TEST_TRIAGE.md`, `LATENCY_BASELINE.md`, `DEPLOYMENT_INVENTORY.md`.

> ⚠️ **ID collision.** `archive/DEFECT_REGISTER.md` uses `B1`/`B2`/`B3` (no
> hyphen) for an older obs sweep. Those are **unrelated** to `B-01`… in
> `REGISTER_B_U.md`. Always write the hyphen.

---

## Today / this week

| Document | What it is |
|---|---|
| [`DEMO_SWEEP_2026-08-05.md`](DEMO_SWEEP_2026-08-05.md) | Demo call script — Sweep A (JV) + Sweep B (Theorem) |
| [`SESSION_2026-08-05.md`](SESSION_2026-08-05.md) | Today's synthesis — fixes + **Sweep A 6/6 PASS** |

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

[`archive/`](archive/) — Jules briefs, old call suites, pre–5 Aug countdown docs,
superseded queues (`FIX_QUEUE_PRE_DEMO`, `DEFECT_REGISTER`). See
[`archive/README.md`](archive/README.md).

---

## Corrections log

Kept so the next reader does not re-learn these the hard way. Older entries that
only mattered during drafting stay; the actionable ones are bolded.

### Provenance (21 Jul 2026)

Drafted from a repo read, then corrected repeatedly. Big ones already folded in:

1. **Observability is not missing** — `app/obs/` exists, flag-gated off. Do not
   merge `feat/obs-*`.
2. **SMS is not missing** — `SMS_ENABLED` defaults `false`.
3. **Latency baseline work already exists** — map it, don't re-measure from zero.
4. **`latency-eval` is THE engine branch** (settled); clinic branches inherit by
   cherry-pick.

### Later corrections (abridged)

| # | Date | Finding |
|---|---|---|
| 13 | 26 Jul | CONFIRM_PHONE bare-yes was a defect, not drift |
| 14 | 1 Aug | A4 phone-confirm was LLM-path only; flow already accepted |
| 15 | 2 Aug | **`latency-eval` is not a live deploy** — gated branches are the clinic ones |
| 16 | 3 Aug | **`FIX_QUEUE_PRE_DEMO` is stale** — live queue is `REGISTER_B_U.md` (now in archive) |
| 17 | 3 Aug | **`/health` is useless for SHA** — only `[build_info] running build <sha>` |
| 18 | 5 Aug | **Standing "~95 failures" baseline is stale** — measure your own; see `SESSION_2026-08-05.md` |
| 19 | 5 Aug | **Housekeeping** — dated session docs → `archive/` |

Full original correction prose for #5–#14 lived in earlier README revisions; git
history has it. Do not re-expand this file into another dump.

If you find another contradiction, the code wins — add a row above.
