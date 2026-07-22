# Kickoff prompt for Claude Code

Paste the block below as your first message, from the repo root.

**Before you paste it, in your terminal:**

```
git worktree prune
git worktree list                     # confirm you are where you think you are
git status --short | head             # the plan docs may show as untracked (??)
git add CLAUDE.md docs/plan/
git commit -m "docs: production readiness plan and phase 0 templates"
```

That last step matters. The docs were untracked the first time and a Claude Code
session searched all of git history for them, correctly found nothing, and
concluded they had never existed. Commit them.

---

```
Read docs/plan/README.md first — it gives the reading order and a log of
corrections already applied to these documents. Then read CLAUDE.md,
docs/plan/BRANCH_DECISION.md, docs/plan/PRODUCTION_READINESS_PLAN.md,
docs/plan/FAILURE_MODE_REGISTER.md and docs/plan/SKILL_PLAYBOOK.md.

If you cannot find these files, run `ls docs/plan/` — they may be untracked, in
which case git history searches will not find them. Also run `git worktree list`
and confirm which working copy you are in before measuring anything.

These documents contain an architecture map, a ranked failure-mode register and
a phased plan, already grounded in this codebase. Do not re-derive them. Verify
them against the code and tell me where they are wrong — they have been wrong
three times already and I would rather find the fourth now than in Phase 3.

Context: this is a live voice AI receptionist for physiotherapy clinics. Real
callers, real bookings. I have a partnership meeting end of next week with a
group representing ~230-250 clinics, followed by a webinar to ~100. The bar is
production-ready for a first cohort of ~10 clinics, not a good demo.

Constraints you must respect:

- SMS and observability are fully implemented and flag-gated off (SMS_ENABLED,
  OBS_*_ENABLED). Do not reimplement them. Do not merge the feat/obs-* branches.
- flow.py is frozen. handle_transcript() is 15,734 lines. Change it only to fix
  a specific reproduced defect, in the smallest possible diff, with a regression
  test that fails before and passes after. No restructuring, no opportunistic
  cleanup.
- Every behavioural fix ships with a regression test in tests/regression/.
- Clinic-specific behaviour belongs in app/clinics/*/clinic.json, never in
  engine code. If you find yourself writing `if clinic == "..."` in app/, stop.
- Ignore the client deliverable documents in the repo root (Vital Edge, Theorem,
  SEO audits). The system is app/, config/, scripts/, tests/, workflows/.

YOUR FIRST TASK IS PHASE -1, NOT PHASE 0.

docs/plan/BRANCH_DECISION.md documents an unresolved contradiction: the plan
assumes latency-eval is the production base, but latency-eval's own LATENCY.md
declares it "a lab, not a release candidate... never promoted by merging as-is."

Resolve it. Compare latency-eval, main and vitaledge-onboarding on:
  1. Deployment reality — which Render service, which clinic, which numbers.
     Tell me what you need from the dashboard; I have it open.
  2. Divergence — commits ahead/behind, and whether the divergence is clinic
     config, latency tuning, or engine behaviour. Only the third kind matters.
  3. Cost to harden each — what exists only on latency-eval that would need
     porting (latency_timing.py, clinical_screening.py, the obs subsystem), and
     vice versa.
  4. Your recommendation, with reasoning stated plainly.

Write it into docs/plan/BRANCH_DECISION.md and stop. Do not start Phase 0 until
I confirm the base branch.
```

---

## Notes for you (Ismael), not for Claude Code

- **Phase −1 is new and it is the right first move.** Ten days of hardening on a
  branch chartered never to ship is the one error in this plan that cannot be
  recovered from inside the window.
- Phase 0 item 4 needs the Render dashboard open. Claude Code cannot see it.
- Phase 0 item 3 is now much smaller — `LATENCY.md` already has a locked
  baseline. Read it before you spend an hour making test calls.
- Prune the worktrees before you start. Fifteen stale copies of this repo is how
  a session ends up measuring the wrong tree, which has already happened once.
