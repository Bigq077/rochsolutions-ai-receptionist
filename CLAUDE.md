# CLAUDE.md — Operating rules for this repo

Susie is a **live, production** AI voice receptionist (FastAPI + Twilio Media Streams)
answering real patient calls for paying clinics. Treat every change accordingly.

## Current initiative
We are building **call observability, QA & alerting** (see
`Susie_Call_Observability_Spec_for_Jules.md` — that spec is the source of truth for
scope, schemas and acceptance criteria). Work through it phase by phase using
`CLAUDE_CODE_PLAYBOOK.md`.

## Non-negotiable guardrails
1. **Never break the live call path.** The observability layer is **additive** and runs
   **async, after** the call completes. Do not add latency or new failure modes to the
   real-time flow (`app/media_streams/**`, `app/routes/twilio.py`).
2. **Feature-flag everything new.** Gate each new subsystem behind an env flag
   (default OFF in production until verified). No flag → not merged.
3. **Tests first.** Write/adjust tests before the implementation. A slice is not done
   until tests are green AND it has been demonstrated on a **real replayed call**, not a
   synthetic one.
4. **`autoDeploy: true` is on.** A merge to `main` ships to production immediately. So:
   never merge a red branch; every PR must pass CI (`.github/workflows/ci.yml`).
5. **One workstream = one branch = one PR.** Do not batch phases. Keep diffs small and
   reviewable. Stop and hand back at each phase boundary.
6. **PII is health data (UK GDPR / special category).** Never commit real names, phone
   numbers, or clinical details into the repo, tests, or logs. Redact before any transcript
   becomes a committed fixture. EU data residency only (Render is Frankfurt — keep it that way).
7. **Reuse what exists.** Postgres tooling (`sqlalchemy`, `psycopg2`) and `sentry-sdk` are
   already dependencies. Do not add new infra stacks without asking.

## Definition of done (every slice)
- [ ] Behind a feature flag, default OFF in prod.
- [ ] Unit tests written and green locally.
- [ ] CI green on the PR.
- [ ] Demonstrated on a real replayed call.
- [ ] No change to real-time latency or the live flow's behaviour.
- [ ] No PII committed.

## How to work
- Read the spec section for the phase, restate the acceptance criteria, then propose a
  short plan and the files you'll touch **before** writing code.
- Prefer the smallest change that satisfies the acceptance criteria. No scope creep.
- If a decision is ambiguous, stop and ask rather than guessing.

## Key paths
- Live flow: `app/media_streams/**`, `app/routes/twilio.py`
- Per-call logging today: `app/call_logger.py` (writes ephemeral `logs/*.jsonl`)
- Human summary → Sheet: `app/tools/actionable_summary.py`
- Owner SMS: `app/notifications/owner_alert.py`, `app/notifications/sms.py`
- Regression scenarios: `tests/auto/scenarios/`, runner `tests/auto/run_tests.py`
- Deploy config: `render.yaml` (Frankfurt EU, autoDeploy on)
