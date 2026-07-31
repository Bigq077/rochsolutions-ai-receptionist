# docs/plan — reading order

Written 21 Jul 2026. Ten-day window to the Hands On Money meeting.

**Read in this order:**

| # | Document | What it is |
|---|---|---|
| 1 | `../../CLAUDE.md` | Repo context, architecture map, hazards, working conventions. Start here. |
| 2 | `BRANCH_DECISION.md` | **Open question. Must be settled before Phase 0 runs.** Which branch is the production base. |
| 3 | `PRODUCTION_READINESS_PLAN.md` | The 10-day phased plan with gates and triage rules. |
| 4 | `FAILURE_MODE_REGISTER.md` | Ranked risk register. Tier 1 must be closed before any clinic goes live. |
| 5 | `SKILL_PLAYBOOK.md` | Which engineering skill to invoke at which phase, and which to skip. |
| 6 | `KICKOFF_PROMPT.md` | Paste-ready first message for Claude Code. |

**Phase 0 produces these** (templates provided, to be filled):

- `TEST_BASELINE.md`
- `DELETED_TEST_TRIAGE.md`
- `LATENCY_BASELINE.md` — note: substantial prior work already exists in
  `LATENCY.md`, `LATENCY_HARNESS.md`, `LATENCY_WS-C.md` and
  `app/media_streams/latency_timing.py` on `latency-eval`. Read those first;
  this is a mapping exercise, not a fresh measurement.
- `DEPLOYMENT_INVENTORY.md`

---

## Provenance and known corrections

These documents were drafted from a read of the repo on 21 Jul 2026 and then
corrected twice. Corrections already folded in:

1. **Observability is not missing.** `app/obs/` has 18 modules on `latency-eval`,
   flag-gated off via `OBS_CAPTURE_ENABLED` / `OBS_JUDGE_ENABLED` /
   `OBS_ALERTS_ENABLED` / `OBS_DIGEST_ENABLED`. Phase 2 is activation, not
   integration. Do not merge the `feat/obs-*` branches.
2. **SMS is not missing.** `SMS_ENABLED` defaults `false` in
   `app/notifications/booking_sms.py`. Phase 4 is a flag, not a build.
3. **Latency baseline work already exists** (see above). Phase 0 item 3 shrinks
   to a few hours.
4. **30 test files** differ between `main` and `latency-eval`, not the 7
   originally named. Triage scope is wider than first written.

**Branch question:** `BRANCH_DECISION.md` now carries a recommendation (ADR-001) —
**Option A, adopt the `latency-eval` engine as the production trunk** (re-chartered,
levers OFF, deploy per-clinic from a `release/*` cut) — pending owner confirmation
and the Render dashboard facts. **Not yet Accepted; Phase 0 stays blocked.**

## Corrections added 21 Jul (branch-decision analysis, all measured on `origin/*`)

5. **Two divergent clones.** These plan docs live only in the
   `…/OneDrive/Documents/Claude code free/…` clone at `jv-v1-onboarding@4630dda`,
   which is **unpushed**; that clone's `main`/`latency-eval`/`vitaledge` branches are
   1–8 days behind origin. Render deploys from origin. **Measure `origin/*`, never
   that clone's local branches** — doing so already produced `app/obs/` on `jv-v1`
   = 0 vs the true 2. Compounds FM-20.
6. **Divergence counts.** `latency-eval` is **200 ahead / 149 behind** `origin/main`
   (docs said 189 / 142); **53 / 6** vs `vitaledge`; **81 / 6** vs `jv-v1` — a
   near-superset of both live onboarding branches.
7. **Observability is not `latency-eval`-unique.** Full obs (18 modules) is on
   `vitaledge` too; `main` has 17; **`jv-v1` has 2**. Only `jv-v1` is bare.
8. **`clinical_screening.py` is 466 lines** (FM-04 says 299) and is referenced
   **only on `latency-eval`** (0 refs on `vitaledge`/`jv-v1`) — the live branches
   ship no clinical red-flag module at all. Raises FM-04's urgency.
9. **`flow.py` is byte-identical** across `latency-eval`/`vitaledge`/`jv-v1`. The
   engine divergence is entirely in the supporting modules, not `handle_transcript`.
10. **Tests differing `main`↔`latency-eval` = 38** (8 added / 20 deleted / 10
    modified), not "~30" — an approximation, not an error.
11. **`except`-clause counts disagree between the docs** (plan §0: 81/97; FM-01 &
    `CLAUDE.md`: 87/104). Re-measure in Phase 1 before citing a number.
12. Minor: this note said "corrected twice"; `CLAUDE.md` and the brief say three
    times. `BRANCH_DECISION.md`'s "how to decide" list omitted `jv-v1`; the ADR
    compares all four.

## Correction added 26 Jul

13. **The CONFIRM_PHONE phone-gate failure was a defect, not drift.**
    `TEST_BASELINE.md` diagnosed it on 21 Jul as *"DRIFT — gate intact, stricter
    than the test… re-point/quarantine the test"*, on the grounds that a bare
    "yes" is deliberately ambiguous and no booking can fire on an unconfirmed
    number. The safety half of that is right; the conclusion is not. Reproduced
    on 26 Jul: the gate asks a **plain yes/no question** — *"Just to check — is
    that 0 7 7 0 0, 9 0 0, 4 5 6?"* — and the ambiguous branch it fell into
    re-emits that identical question with **no retry counter, no escalation and
    no transfer**, so a caller who answers "yes" is asked the same thing for the
    rest of the call and never books. Bare `\bno\b` *was* matched, so the gate
    accepted a bare no and refused a bare yes on a yes/no question.
    Root cause: `5c7ea4e` (24 Apr) replaced yes/no phone confirmation with
    explicit phrase commands; `3bbe4f0` (10 Jun) reversed that on the LLM path
    (`connection.py._PHONE_CONFIRM_AFFIRMATIVES`) for exactly this reason and left
    the deterministic gate behind. It is the surviving cause of
    `FIX_QUEUE_PRE_DEMO.md` A1's magic-phrase friction (Jules rows 17/19/21:
    150–261 s, no booking). Fixed in `flow.py` behind `phone_confirm_armed`, with
    `tests/regression/test_confirm_phone_bare_yes.py`. **Baseline is now 95, not
    96** — `test_critical_flows.py` is green and its row in `TEST_BASELINE.md`
    should be struck. Note the ID collision: FM-21 in
    `FAILURE_MODE_REGISTER.md` is *screening double-ask*, a different thing; this
    one has no FM number.

## Correction added 1 Aug

14. **A4's root-cause writeup was wrong about the deterministic gate.**
    `DEFECT_REGISTER.md` claimed, of *"um yes that's a good number"*: "The
    parallel gate `flow._HG_YES` (flow.py:10614) **also** misses this phrase, so
    this is not only the known list-divergence — it is a genuine vocabulary gap
    in every copy." The second half is false. `_HG_YES` is only one of three
    accept routes at that gate; `_hg_bare_yes` is a **word-bounded regex** for
    `yes|yeah|yep|yup` anywhere in the turn, so it matches the "yes" in turn 18
    and the gate accepts. Verified by running both predicates against the literal
    transcript: flow accepts, `connection._is_use_this_number` rejects.
    **The A4 defect was confined to the LLM path.** That matters for more than
    tidiness — it means the deterministic gate already had the right shape
    (affirmative token anywhere, guarded by negatives) and the LLM path was the
    one that had drifted, which is the opposite of the 26 Jul correction above.
    What flow *did* miss is the same phrase with no affirmative word at all
    ("that's a good number"), which `_SEMANTIC_YES_PHRASES` now covers.
    Fixed 1 Aug; see `tests/regression/test_phone_confirm_adjective_slot.py`,
    which asserts BOTH gates and asserts they agree.

If you find another contradiction between these documents and the code, the code
wins. Record the correction here.
