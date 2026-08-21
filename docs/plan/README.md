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
| [`HANDOVER_JULES_5DAY_2026-08-11.md`](HANDOVER_JULES_5DAY_2026-08-11.md) | **Quentin away — Jules owns the week.** Standing rules, Jobs 1–3, adversarial scripts A1–A10 |
| [`DEMO_SWEEP_2026-08-05.md`](DEMO_SWEEP_2026-08-05.md) | Demo call script — Sweep A (JV) + Sweep B (Theorem) |
| [`SESSION_2026-08-05.md`](SESSION_2026-08-05.md) | 5 Aug synthesis — fixes + **Sweep A 6/6 PASS** |

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
| 20 | 15–16 Aug | Job 3 closed; Job 2 Wave 1 complete; Emma CA3b303f fixed (`ffceb94` / theorem `02fd991`); Batch 1 queued in findings |
| 21 | 16 Aug | Housekeeping — 5–8 Aug demo/session docs → `archive/`; live index is this file |
| 22 | 17 Aug | **A fix can be wrong inside the flow it repairs, and the suite stays green.** B1.2 shipped twice with a defect in the reschedule path it was written for — a re-ask the move gate could not recognise (`36a7e5b`), then a slot window armed and destroyed by one reply (`3b6695e`). Both found by *running* the predicates, neither by reading the code. Run a new phrase through the gate that will read it, and a new end-of-turn cleanup against the same turn's arming |
| 23 | 21 Aug | **A screening trigger must never gate on the screen question’s own answer.** Phase 3 of the screening plan proposed narrowing cauda equina and DVT to `trigger_all_groups` (region AND a neuro/acuity signal). Measured, 13/25 and 15/16 of those second groups *are* the question’s own answer — which turns a screen into a confirmation that can only fire once the caller has volunteered the red flag. It reversed F-032 (P1) and turned 29 tests red. Rejected; `trigger_all_groups` stays on `vbi_neck` only. The over-screening complaint is a **tone** problem — Phase 4 framing, not lost recall. `168e0d2` |
| 24 | 21 Aug | **The replay corpus cannot be split by `build_sha`.** 36% of calls carry no sha, and 50 of 58 shas present are on a JV live branch because the demo line runs the same builds. The **caller** is the discriminator: two dev handsets are 204 of 214 calls, and 37 of the 38 screen-touching calls are ours. There is no real-traffic screening corpus, so replay can detect change but cannot validate a trigger narrowing. `fe839fb` |
| 25 | 21 Aug | **A replay harness counts re-asks as arms unless told not to.** The bounded stranded re-ask and the hedge probe both return `ask_screen` for a screen armed turns earlier, so the before-table listed “please book that in” as a cauda equina arming utterance — it matches no keyword at all. Four of six were Layer-2 arms or re-asks. Correct the ruler before measuring with it. `604db7c` |

Full original correction prose for #5–#14 lived in earlier README revisions; git
history has it. Do not re-expand this file into another dump.

If you find another contradiction, the code wins — add a row above.
