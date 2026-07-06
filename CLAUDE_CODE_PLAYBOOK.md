# Claude Code Playbook — Building Susie's Observability System

How to use this: open Claude Code in this repo. Work **one phase at a time**, in order.
For each phase, create the branch, paste the prompt, review the plan it proposes
*before* it writes code, then let it implement, test, and open a PR. Do not start the
next phase until the current PR is merged and green.

**Ground rules live in `CLAUDE.md`. The full spec (schemas, acceptance criteria) is
`Susie_Call_Observability_Spec_for_Jules.md`. Claude Code should read both first.**

Global conventions:
- Branch per phase: `feat/obs-<phase>` (e.g. `feat/obs-1-capture`).
- Every subsystem behind an env flag, default OFF in prod.
- A phase is done only when: tests green + CI green + demonstrated on a REAL replayed call + no PII committed.

---

## Phase 0 — Ground the repo (do this first)

> Read `CLAUDE.md` and `Susie_Call_Observability_Spec_for_Jules.md` in full, then this task.
>
> Goal: make this repo safe to build in, before any feature work.
>
> 1. Confirm the CI workflow at `.github/workflows/ci.yml` runs and passes `pytest -q app/booking/tests`. Fix anything needed so it is green.
> 2. Audit the existing test suite: list every test file, and for each say whether it can run offline (no live creds/network). Mark ones that need credentials with a `@pytest.mark.integration` marker and add that marker to `pytest.ini` so CI can exclude them. Do NOT delete them.
> 3. Print a baseline: which `tests/auto` phases pass today (run `tests/auto/run_tests.py` locally if it runs without a live server; if it needs one, say so and stop — do not fake it).
> 4. Confirm `render.yaml` has no persistent disk and that `logs/*.jsonl` is therefore ephemeral. State this explicitly in the PR description.
>
> Deliver as a single PR. Do not add features. Restate your plan before editing.

Acceptance: CI green on the PR; integration tests marked and excluded from CI; baseline documented.

---

## Phase 1 — Durable capture + replay harness (KEYSTONE)

> Read the spec section 5.1. Branch: `feat/obs-1-capture`.
>
> Goal: persist every completed call durably, and be able to replay any real call offline.
>
> Build, behind env flag `OBS_CAPTURE_ENABLED` (default OFF):
> 1. A Postgres-backed store (reuse existing `sqlalchemy`/`psycopg2`; add a `calls` table via a migration). Schema: all current `call_logger.py` fields PLUS the full transcript (`session["turns"]` as ordered JSON) PLUS `clinic_id`, `call_sid`, timestamps, `final_state`.
> 2. Write into the existing teardown path where `CallLogger.flush()` is called, so capture is guaranteed on hangup AND on pipeline error. This must be async and must not add latency to the live flow.
> 3. A CLI **replay harness**: `python -m app.obs.replay <call_sid>` loads a stored transcript and re-runs it through the flow logic offline, printing the turn-by-turn trace. This is the tool every later phase depends on — make it clean.
> 4. Unit tests under `tests/capture/` (offline: use a fixture transcript, not a real one).
>
> Do not modify real-time behaviour. Restate your plan and the files you'll touch before coding.

Acceptance (from spec): a real call produces one durable Postgres row incl. transcript, surviving a Render redeploy; a stored call can be replayed from the CLI. Add `tests/capture` to `ci.yml`.

---

## Phase 2 — Failure alerting (ship in parallel with 1; solves the safety-net now)

> Read the spec section 5.2. Branch: `feat/obs-2-alerts`.
>
> Goal: know within seconds when something breaks on a live call.
>
> Build, behind env flag `OBS_ALERTS_ENABLED` (default OFF):
> 1. Initialise the `sentry-sdk[fastapi]` already in requirements (DSN from env `SENTRY_DSN`) in `app/main.py`. Capture unhandled exceptions in `app/routes/twilio.py` and `app/media_streams/**`.
> 2. Rule-based alerts to Quentin, reusing `app/notifications/sms.py` (and an optional Slack webhook `OBS_SLACK_WEBHOOK`). Implement exactly the conditions table in spec 5.2 (pipeline error, STT/TTS failure, booking API error, escalation-not-delivered → immediate; short-call and retry-storm → daily roll-up).
> 3. A tiny alert-router module so severity → channel is data-driven and testable. Unit tests under `tests/alerts/` that assert each condition fires the right channel (mock the senders — never send real SMS in tests).
>
> Do not spam: benign short calls must roll up daily, not alert per-call.

Acceptance (from spec): a deliberately broken booking call and a forced exception each alert within seconds; benign short calls roll up. Add `tests/alerts` to `ci.yml`.

---

## Phase 3 — LLM-as-judge + calibration (the improvement engine)

> Read the spec section 5.3. Branch: `feat/obs-3-judge`. Depends on Phase 1.
>
> Goal: an automated, trustworthy quality score for every call.
>
> Build, behind env flag `OBS_JUDGE_ENABLED` (default OFF):
> 1. An async post-call step that sends the stored transcript to Claude and returns EXACTLY the JSON schema in spec 5.3 (outcome, quality_score 1-5, intent_resolved, failure_tags[], evidence, rubric_version). Runs after teardown; store the result on the call row.
> 2. The judge prompt with the rubric anchors from the spec, and a `rubric_version` constant so scores stay comparable.
> 3. A **calibration harness**: `python -m app.obs.calibrate` runs the judge over a set of hand-labelled calls and reports agreement (exact + within-1) between judge and human score. Provide a `calibration/labels.csv` template (call_sid, human_score, notes) — with NO real PII committed; labels reference call_sids only.
> 4. Wire the alert bridge: `quality_score <= 2` OR failure_tags contains `missed_escalation`/`wrong_info` → review alert (via Phase 2 router).
> 5. Tests under `tests/judge/` using fixture transcripts with expected tags.
>
> Report the judge-vs-human agreement number in the PR. Do not claim the scores are trustworthy until agreement is measured.

Acceptance (from spec): every captured call scored within a minute; low scores raise a review alert; calibration agreement measured and reported. Add `tests/judge` to `ci.yml`.

---

## Phase 4 — Failure → regression pipeline (closes the loop)

> Read the spec section 5.4. Branch: `feat/obs-4-regression`. Depends on Phases 1 & 3.
>
> Goal: turn any real bad call into a permanent regression test in minutes.
>
> Build:
> 1. `python -m app.obs.to_scenario <call_sid>`: loads a judged-bad real call, **redacts all PII** (names, phone numbers, clinical detail — assert none remain), and emits a scenario into `tests/auto/scenarios/` in the existing format.
> 2. Make `tests/auto/run_tests.py` runnable in CI (a `--ci` mode that doesn't need a live server, using the Phase 1 replay harness). Wire it into `ci.yml` so a fixed failure can never silently regress.
> 3. Tests proving the redactor removes PII and the generated scenario loads.
>
> The redaction assertion must be hard: fail loudly if any PII pattern survives.

Acceptance (from spec): a real failure becomes a committed, PII-free scenario in minutes; the scenario suite runs in CI and blocks regressions.

---

## Phase 5 — Dashboard + weekly ritual

> Read the spec section 5.5. Branch: `feat/obs-5-dashboard`. Depends on Phases 1 & 3.
>
> Goal: see trends, and make the weekly improvement loop possible.
>
> Build:
> 1. Either stand up self-hosted Langfuse (EU) and pipe scores to it, OR a simple read-only dashboard over the Postgres `calls` table: volume, booking rate, mean quality_score, failure-tag frequency — sliced by clinic and by week.
> 2. A `python -m app.obs.weekly` command that prints the week's bottom-decile calls by score with their failure_tags and call_sids — the input to the Monday ritual.
>
> Keep it internal-only for now (no client-facing view yet).

Acceptance: weekly trend visible per clinic; `weekly` command lists the calls to review.

---

## The weekly ritual (the actual end goal — a habit, not code)

Every Monday, ~30 minutes:
1. Run `python -m app.obs.weekly`.
2. Review the bottom-decile calls (replay any you're unsure about).
3. Convert each NEW failure mode into a regression scenario (`to_scenario`).
4. Ship the fix on a branch → CI green → merge.
5. Confirm mean `quality_score` trended up vs last week.

Phases 1–4 exist only to make this 30-minute loop fast and evidence-based. That loop,
running on real call volume, is the system that "continuously improves week on week."
