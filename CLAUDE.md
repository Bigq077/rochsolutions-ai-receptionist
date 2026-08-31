# CLAUDE.md — RochSolutions AI Receptionist ("Susie")

Context file for Claude Code. Read this before touching anything.

---

## 1. What this is

A voice AI receptionist for physiotherapy clinics. Callers phone a clinic number,
Twilio streams the audio to this FastAPI service over a WebSocket, and the
assistant answers questions, screens clinically, and books appointments into the
clinic's real booking system.

Real callers. Real bookings. A bug here is a missed patient, not a failed test.

**Commercial context:** a partnership meeting with People to Hands On Money
(~230–250 physiotherapy clinics) lands end of July 2026, followed by a webinar to
roughly 100 clinics. The near-term goal is not scale — it is that a live demo
call and the first onboarding cohort are flawless.

---

## 2. Canonical branch

**`latency-eval` is THE engine branch.** Settled 2026-07-22 — no longer contested.
`release/cohort-1` was merged into it and retired; there is now exactly one engine
branch plus two deployment branches (`jv-v1-onboarding`, `vitaledge-onboarding`)
that inherit engine fixes by cherry-pick **from** `latency-eval`. `main` is a
separate historical lineage — leave it alone. See `docs/plan/BRANCH_DECISION.md`.

**Canonical-first rule:** every engine fix commits to `latency-eval` first; the
live clinics inherit it. Never fix on a clinic branch and port up — that strands
safety fixes at convergence.

`LATENCY.md` used to call this branch *"a lab, not a release candidate… never
promoted by merging as-is."* That line has been re-chartered: it now applies to
the **WS latency levers** (still experimental, still default OFF), not to the
branch. Do not cite it as a reason to avoid basing work here.

> 🔴 **`latency-eval` IS a live line as of 2026-08-31. A push here reaches real
> patients.** Vital Edge was folded onto it at 01:57 UTC — Render service
> `vitaledge` (`srv-d8va6cbtqb8s73fbpvag`) now tracks `latency-eval`, verified
> live at `3a3af7e` and confirmed by a real call five minutes later.
>
> **So: out-of-hours timing, a revert target in hand, and a real call after any
> engine change — HERE, not only on the clinic branches.** `autoDeploy` is on,
> so a push is a deploy. The rollback for Vital Edge is to point its service
> back at `vitaledge-onboarding`, which stays deployable and must not be
> deleted until VE has run a full week on canonical.
>
> <details><summary>What this block used to say, and why it changed</summary>
>
> From 2026-08-02 to 2026-08-30 it read *"`latency-eval` is not a live line —
> push whenever"*, and that was correct for that period: the branch served only
> the Northgate demo line. It was itself a correction of an earlier over-caution
> that cost real time, with agents staging finished work overnight for a deploy
> window that did not exist. **Neither version is a standing truth — the fold is
> what changed it, and it will change again as JV and Theorem fold.** Check
> which services track this branch before assuming either posture.
> </details>
>
> The demo line (**+447366263180**, `northgate`) is still on this branch too,
> which is the whole point of the design: `SMS_ENABLED` and
> `APPOINTMENT_REMINDERS_ENABLED` are per-SERVICE env vars, so the demo service
> leaves them at their `false` code default and sends nothing, while the Vital
> Edge service sets both explicitly. **Canonical's defaults must stay OFF** or a
> test call texts a real patient.
>
> The canonical-first rule is unchanged: engine fixes land here first. What has
> changed is that "here" is now also a deployment.

> ⚠️ **Check which branch and which worktree you are actually in before
> measuring anything.** There are ~15 registered worktrees under
> `AppData/Local/Temp/claude/`, most prunable. A previous session confidently
> measured the wrong tree. Run `git worktree prune`, then
> `git rev-parse --abbrev-ref HEAD`, before you trust a single number.
>
> Note also: the plan documents in `docs/plan/` may be **untracked**. Git history
> searches will not find them. Use `ls`.

Two subsystems are deliberately switched off on this branch — they are **fully
implemented and flag-gated**, not missing. This is much better news than it
sounds and it materially shrinks the work:

| Subsystem | Code | Off switch |
|---|---|---|
| Observability | `app/obs/` — 18 modules (`store`, `judge`, `alerts`, `digest`, `regress`, `replay`, `dashboard`, `redact`, `migrate`, `worker`, …) | `OBS_CAPTURE_ENABLED`, `OBS_JUDGE_ENABLED`, `OBS_ALERTS_ENABLED`, `OBS_DIGEST_ENABLED` all default `false`; needs `OBS_DATABASE_URL` + `python -m app.obs.migrate` |
| SMS | `app/notifications/` — `booking_sms`, `smart_sms_router`, `templates`, `scheduler`, `sms`, `owner_alert`, `digest`, `email` | `SMS_ENABLED` defaults `false` in `booking_sms.py` (with an explicit in-code warning not to flip the default) |

**Do not reimplement either, and do not merge the `feat/obs-*` branches into
this one.** The work is enabling, provisioning and verifying — not building.

### Branch topology (do not merge blindly)

- `main`, `jv-v1-onboarding`, `vitaledge-onboarding`, `latency-eval` — the four
  branches currently deployed as four separate Render services.
- `theorem-*`, `jv-*` — historical, already merged to their parent branches.
- `feat/obs-*`, `fix/obs-*` — the observability work, unmerged into `latency-eval`.

`latency-eval` is 189 commits ahead of `main` and 142 behind. That divergence is
mostly intentional (different clinic, different tuning). **Never do a blind
`merge main`.** Any integration is a deliberate, reviewed, file-by-file exercise.

---

## 3. Architecture

### Call flow

```
Caller
  └─ Twilio PSTN
       └─ app/routes/twilio.py          — webhook, TwiML, stream handshake
            └─ app/media_streams/connection.py   (11.8k LOC) — WS lifecycle, audio frames
                 ├─ stt_stream.py        — AssemblyAI streaming ASR
                 ├─ utterance_router.py  — endpointing / turn boundary
                 ├─ router.py            — routes utterance to fast path or LLM
                 ├─ fast_path.py         — canned/deterministic replies (latency win)
                 ├─ flow.py              (24.8k LOC) — FlowEngine, the conversation brain
                 │    └─ llm_stream.py   — Anthropic/OpenAI/Groq streaming completion
                 │    └─ app/tools/receptionist_tools.py (5.8k LOC) — tool calls
                 │         └─ app/booking/booking/providers/{acuity,google_calendar}.py
                 └─ tts_stream.py        — ElevenLabs streaming TTS → Twilio
```

Supporting: `app/knowledge/` (clinic knowledge retrieval), `app/prompts/`
(system prompts, per-clinic), `app/notifications/` (SMS/email), `app/storage/`,
`app/integrations/sheets.py`, `app/routes/admin.py`.

### Multi-tenancy — partially implemented

Clinic data lives in `app/clinics/<clinic_id>/clinic.json` (+ `knowledge.md`),
loaded via `app/clinic_loader.py` and `app/clinic_config.py`. The JV config is
~30 KB across 33 top-level keys — this layer is genuinely well developed.

**But tenant selection happens at deploy time, not runtime.** Evidence:

- `.env.example` contains `CLINIC_NAME`, `CLINIC_ADDRESS`, `CLINIC_PHONE` and
  `ACUITY_CALENDAR_ID_ALCESTER` / `_REDDITCH` / `_MARK` / `_LEANNE` — tenant
  identity and per-practitioner calendars live in environment variables.
- Clinic names are hardcoded in engine files: `app/fast_path.py`,
  `app/flows/brain.py`, `app/booking/booking/utils.py`,
  `app/booking/booking/providers/acuity.py`.
- `render.yaml` declares one service with `autoDeploy: true` and no branch pin —
  the branch is set per-service in the Render dashboard.

Result: one clinic = one branch = one deployment. This is the structural blocker
to onboarding at cohort scale. See `docs/plan/PRODUCTION_READINESS_PLAN.md`
Phase 4.

### Stack

FastAPI + uvicorn (Python 3.14), Render (Frankfurt — chosen for UK/EU RTT),
Redis, Twilio, AssemblyAI, ElevenLabs, Anthropic/OpenAI/Groq, Acuity
Scheduling, Google Calendar, Google Sheets, SMTP.

37 required environment variables. Missing-secret handling is a live risk — see
the failure-mode register.

---

## 4. Known hazards — read before editing

### `flow.py` is the danger zone

Measured on `latency-eval`:

- 24,820 lines, one `FlowEngine` class, 27 methods.
- **`handle_transcript()` is a single 15,734-line async method** (from line 5894).
  Every caller utterance flows through it.
- `ask_current_question()` is 2,109 lines.
- `_handle_mid_flow_interrupt()` is 1,059 lines.

This is the highest-risk artefact in the codebase. It is also **not refactorable
before the meeting**. Policy for the next 10 days:

> **Freeze, don't refactor.** Change `handle_transcript` only to fix a specific
> reproduced defect, in the smallest possible diff, with a regression test that
> fails before and passes after. No restructuring, no "while I'm here" cleanup.
> The refactor is a post-meeting project with its own plan.

### Broad exception handling

On `latency-eval`, `app/tools/receptionist_tools.py` (6,135 lines) has 104
`except` clauses, **87 of which are `except Exception` or bare**. `flow.py` has
41 of 94. `connection.py` is 12,172 lines. This is the most likely
cause of the worst failure mode in this system: *the call sounds perfect and the
booking silently never happened.*

When you touch any of these, the fix is not "add logging" — it is to decide
whether the failure is recoverable, and if it is not, to surface it to the caller
and to an operator.

### Missing timeouts

~49 outbound HTTP call sites have no explicit timeout against ~20 that do. On a
live call, a hanging provider call is dead air.

---

## 5. Working conventions

- **Tests:** `pytest` (`pytest.ini`, `asyncio_mode = auto`, `testpaths = tests`).
  49 test files across `tests/`, plus `tests/{regression,capture,judge,alerts,dashboard,auto}/`
  and `app/booking/tests/`.
- **Establish a green baseline before changing anything.** If the suite is not
  green on `latency-eval` today, that is Day 1 task one and everything else waits.
- **Every behavioural fix ships with a regression test** in `tests/regression/`.
  No exceptions during this 10-day window.
- **Clinic-specific behaviour belongs in `clinic.json`, never in engine code.**
  If you find yourself writing `if clinic == "..."` in `app/`, stop — that is the
  bug, not the fix.
- **Small diffs.** One concern per commit. This codebase has no safety net large
  enough to absorb a big change.
- **Do not add dependencies** without asking. Cold-start time on Render affects
  first-call latency.
- Repo root contains ~60 unrelated client deliverables (`Vital Edge - *.docx`,
  `Theorem_*`, SEO audits). **Ignore them.** The system is `app/`, `config/`,
  `scripts/`, `tests/`, `workflows/`.

### Repo docs worth reading

`docs/archive/SUSIE_AUDIT_REPORT.md`, `JVP_IMPLEMENTATION_PLAN.md`,
`docs/archive/handoff.md`, `CALL_OBSERVABILITY_RESEARCH.md`,
`docs/archive/JV_V1_8CALL_TEST_SUITE.md`, `docs/archive/JV_V1_TEST_CALL_SCRIPT.md`,
`docs/archive/CALL_TEST_SCRIPT.md`. (Older working docs were moved to
`docs/archive/` on 2026-07-27 — see `docs/archive/README.md`.)

---

## 6. Definition of production-ready

Not "sounds good on a demo call." The bar for this system:

1. **Correctness** — every booking the caller believes was made exists in Acuity,
   and every booking that fails is escalated to a human within minutes.
2. **Latency** — p95 caller-perceived turn latency under 1.5 s; no dead air over
   3 s without a filler or acknowledgement.
3. **Graceful degradation** — Acuity, ElevenLabs, AssemblyAI or the LLM being
   slow or down produces a controlled outcome (take a message, promise a
   callback, transfer), never silence or a hallucinated confirmation.
4. **Visibility** — every call produces a record; failures alert an operator
   the same day.
5. **Recoverability** — a clear rollback path and a named on-call human.

Anything that does not move one of those five is not on the critical path.

---

## 7. The plan

Start at `docs/plan/README.md` — it gives the reading order and a log of
corrections already applied.

- `docs/plan/BRANCH_DECISION.md` — **open, blocks everything.**
- `docs/plan/PRODUCTION_READINESS_PLAN.md` — phased roadmap with gates.
- `docs/plan/FAILURE_MODE_REGISTER.md` — ranked risk register.
- `docs/plan/SKILL_PLAYBOOK.md` — which engineering skill to invoke when.
- `docs/plan/KICKOFF_PROMPT.md` — paste-ready starting prompt.
- Templates to fill in Phase 0: `TEST_BASELINE.md`, `DELETED_TEST_TRIAGE.md`,
  `LATENCY_BASELINE.md`, `DEPLOYMENT_INVENTORY.md`.

Work the phases in order. Do not start a phase before its gate has passed.

**If these documents and the code disagree, the code wins** — record the
correction in `docs/plan/README.md`. They have already been wrong three times.
