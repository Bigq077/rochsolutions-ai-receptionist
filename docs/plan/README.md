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
| 2a | [`RELEASE_PROMOTION_DECISION.md`](RELEASE_PROMOTION_DECISION.md) | **ADR-002 (1 Sep 2026).** All four services now track one branch, so a push reached every patient line at once. `latency-eval` = staging (demo line), `production` = the three patient lines, promoted by fast-forward after a demo call. Amends ADR-001's one-branch-per-clinic model. **Inert until the Render services are repointed.** |
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
| 23 | 21 Aug | **A screening trigger must never gate on the screen question’s own answer.** Phase 3 of the screening plan proposed narrowing cauda equina and DVT to `trigger_all_groups` (region AND a neuro/acuity signal). Measured, 13/25 and 15/16 of those second groups *are* the question’s own answer — which turns a screen into a confirmation that can only fire once the caller has volunteered the red flag. It reversed F-032 (P1) and turned 29 tests red. Rejected; `trigger_all_groups` stays on `vbi_neck` only. The over-screening complaint is a **tone** problem — Phase 4 framing, not lost recall. `168e0d2` |
| 24 | 21 Aug | **The replay corpus cannot be split by `build_sha`.** 36% of calls carry no sha, and 50 of 58 shas present are on a JV live branch because the demo line runs the same builds. The **caller** is the discriminator: two dev handsets are 204 of 214 calls, and 37 of the 38 screen-touching calls are ours. There is no real-traffic screening corpus, so replay can detect change but cannot validate a trigger narrowing. `fe839fb` |
| 26 | 11 Sep | **"The 12:00 pin proves it was still trying" (SLOT_PRESENTATION rev. 7, N4) was not supported by the code.** No `already_retrieved` refusal ever wrote `REQUESTED_TIMES_KEY`, and on CA91d1f123 its last writer was the named-day producer's `[]`. Thursday's 12:10 is what S-2 chooses unaided. Fixed anyway: the refusal now writes the key from the caller's words. `88f801f5` |
| 27 | 11 Sep | **One defect was encoded as the rule in THREE measurements plus a call script.** N1 ("what about Monday" withholds Monday's offered times) was asserted as correct by `replay_presented_times`' gate, two `test_t1b` tests, two `test_s13` tests, and step 3 of the slot call script. Rev. 7 named only the gate. Also: **N-ids collide** — `OPEN_DEFECTS_2026-09-09.md` has its own N1–N5; grep every plan doc before minting one. `822d1ed4` |
| 25 | 21 Aug | **A replay harness counts re-asks as arms unless told not to.** The bounded stranded re-ask and the hedge probe both return `ask_screen` for a screen armed turns earlier, so the before-table listed “please book that in” as a cauda equina arming utterance — it matches no keyword at all. Four of six were Layer-2 arms or re-asks. Correct the ruler before measuring with it. `604db7c` |

| 28 | 11 Sep | **The `template_v1` prompt told the model a slot format the engine's own tests forbid, for ten days.** BOOKING STEPS 5 asked for "exactly TWO times … with no numbered list"; the producers read up to three days of two times each, NUMBERED and parsed for keypad selection. Both were correct decisions three weeks apart and the prompt was never brought with the second. `SLOT_FORMATTER_SYSTEM_PROMPT` and theorem_v3's own section were ALREADY numbered — so the outlier was one file, rendering on the demo line and two of the three live clinics. **Verified by RENDERING all four clinic prompts, not by reading the module**, and the containment re-proved by hashing all five either side. `fa4dca45` |
| 29 | 11 Sep | **A "two slots, not six" test was pinning a superseded owner decision.** `test_collection_sequence_prompt`'s B1 tests asserted the unnumbered two-time rule as correct, ten days after the owner replaced it (1 and 9 Sep). That is correction 27's mechanism again, on a different defect: a correct test of an earlier decision becomes a defect pin. When a decision is superseded, **record the supersession in the test** — deleting it loses the reason and leaving it blocks the fix. `fa4dca45` |
| 30 | 11 Sep | **Four of the first eight "failures" from the new spec scorer were the scorer's own bugs, and every one looked like a finding**: a non-round probe time that invariant 18 declines *by design*; a bare string where `nearest_time_index` declines *by contract*; three invented session keys where `choose_presented_days` actually reads the spoken record; and a row asserting past the documented boundary of N1's fix. Correct the ruler before measuring with it — correction 25, one layer up. `f860938a` |
| 31 | 11 Sep | **A harness that scores producers cannot see a routing defect, and must not report PASS.** The named-day producer, called directly, answers "what else on Monday" perfectly on every diary shape — N6 is still open, because the utterance never reaches it (B-137 takes it). `scripts/score_slot_spec.py` therefore reports DT-14 as UNREACHABLE, not PASS: a PASS there retires the only thing catching N6, which is a phone call. `handle_transcript` being undrivable offline is the real cost of invariant 20. `f860938a` |

If you find another contradiction, the code wins — add a row above.
