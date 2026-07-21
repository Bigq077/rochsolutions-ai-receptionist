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

**Still open:** the branch question in `BRANCH_DECISION.md`.

If you find another contradiction between these documents and the code, the code
wins. Record the correction here.
