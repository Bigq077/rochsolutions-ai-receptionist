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

## 2. Branches and deployment

**One lineage, two branches.** Settled 2026-09-01 by ADR-002 —
`docs/plan/RELEASE_PROMOTION_DECISION.md`. Read it before pushing anything.

| Branch | Role | Services tracking it | Phones it can reach |
|---|---|---|---|
| `latency-eval` | **staging** — engine work lands here | the demo service (`srv-d9ac6bfaqgkc739dstsg`) | **+447366263180** (`northgate`) only |
| `production` | **the live line** | the three patient services, incl. `vitaledge` (`srv-d8va6cbtqb8s73fbpvag`) | `jv_v1`, `vital_edge`, `theorem_v2`, `theorem_v3` |

```bash
git push origin latency-eval                # deploys the DEMO line only
#   ... call +447366263180, read the cleanup log for [build_info] ...
git push origin latency-eval:production     # deploys the three PATIENT lines
```

**`production` is always an ancestor of `latency-eval`.** Promotion is a
fast-forward, never a cherry-pick — so there is no port, no divergence, no
per-branch prompt-hash re-pinning, and the commit a clinic runs is bit-identical
to the one that was called. Rollback is moving the pointer, not reverting code:

```bash
git push --force-with-lease origin <last-good-sha>:production
```

> ✅ **The gate is LIVE as of 2026-09-01** (owner-confirmed; repointing is a
> dashboard action this repo cannot see). The demo service is the only thing on
> `latency-eval`; the three patient services track `production` with autoDeploy
> on. So **a push to `latency-eval` reaches only +447366263180** — push freely.
> A push to `production` reaches patients — that one is out-of-hours work with a
> revert target in hand.
>
> First promotion through the gate: `cda304a3` → `1d85d13e`, verified by call
> `CAc119b8838f556ac20f9552dee2e4021f` on the demo line before promoting.
> **Do not take this as permanent** — the posture on this branch has now reversed
> four times. Confirm which branch each service tracks before you push.

**The canonical-first rule is retired.** It existed because engine fixes had to
be ported to per-clinic branches. There is nothing left to port to — one build
serves every clinic, and tenancy is resolved at **runtime** from the Twilio
`to=` number via `clinic_config.TWILIO_TO_CLINIC` (five numbers, four clinics).
That fold closed **FM-14** ("engine drift across the four deployed branches",
likelihood 5, the highest in the register).

What the fold also did, silently, was delete the staging gate — branch-per-clinic
had been providing one as a side effect. ADR-002 is the replacement. It is not a
free win: **a green demo call validates the ENGINE, not the tenants.** `northgate`
is a faithful `jv_v1` proxy, but `theorem_v3` renders a hardcoded Python prompt
that `clinic.json` never reaches and short-circuits to the Acuity executor, and
`vital_edge` has no condition library and uses the diary reader. Do not read a
green demo call as clearance for all four clinics.

`LATENCY.md` used to call this branch *"a lab, not a release candidate… never
promoted by merging as-is."* That line has been re-chartered: it now applies to
the **WS latency levers** (still experimental, still default OFF), not to the
branch. Do not cite it as a reason to avoid basing work here.

> **Defaults must stay OFF on this lineage.** `SMS_ENABLED` and
> `APPOINTMENT_REMINDERS_ENABLED` are per-SERVICE env vars. One build now serves
> the demo line and the patient lines, so the code default is the only thing
> stopping a test call from texting a real patient — the demo service leaves them
> at `false`, the patient services set them explicitly. Never flip a default to
> make something work locally.

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

- `latency-eval`, `production` — the only two branches that are deployed.
- `main`, `jv-v1-onboarding`, `vitaledge-onboarding`, `theorem-onboarding`,
  `jv_v2` — the retired per-clinic deployment branches. They are **frozen
  rollback targets, not deploy branches**: each still runs, and repointing a
  service back at one is the escape hatch if the fold turns out badly. **Do not
  delete them**, and do not commit to them. `main` in particular is no longer
  "historical lineage to leave alone" — it was Mark's Theorem clinic, and it was
  folded, so it is a rollback target like the rest.
- `theorem-*`, `jv-*`, `port/*` — historical, already folded or abandoned.
- `feat/obs-*`, `fix/obs-*` — the observability work, unmerged into `latency-eval`.

**Never do a blind `merge main`** — or a blind merge of any retired branch. Any
integration is a deliberate, reviewed, file-by-file exercise. Note also that
`git cherry` cannot audit port status between these branches (patch-ids differ
after a cherry-pick, so it calls ported work unported and hides real gaps) —
grep for the added code instead.

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

### Multi-tenancy — runtime, with residual deploy-time couplings

Clinic data lives in `app/clinics/<clinic_id>/clinic.json` (+ `knowledge.md`),
loaded via `app/clinic_loader.py` and `app/clinic_config.py`. The JV config is
~30 KB across 33 top-level keys — this layer is genuinely well developed.

**Tenant selection is a runtime lookup on the Twilio `To` number.**
`app/routes/twilio.py:247` calls `clinic_id_from_twilio_to(to_number)` on the
inbound webhook and pins `clinic_id` onto the session; the map is
`clinic_config.TWILIO_TO_CLINIC` — five numbers, four clinics. One build serves
all of them. **An unmapped number falls back to the `demo` clinic** rather than
failing (`app/routes/realtime.py:504`), so a typo in that map is inaudible on the
call and shows up in someone else's calendar.

This is what made the 2026-09-01 fold possible and closed FM-14. It is also why
adding a clinic is a config change, not a branch — see `validate_all_clinics()`
in `clinic_config.py`, the onboarding checklist as code, covered by
`tests/tenancy/`.

**What is still deploy-time, and therefore still a trap:**

- **Per-service env vars.** `SMS_ENABLED`, `APPOINTMENT_REMINDERS_ENABLED`,
  `OBS_*`, `SHEETS_ENABLED` are set per Render service, not per clinic. With one
  build serving the demo line and the patient lines, these are the only thing
  separating them — see the defaults warning in §2.
- **`.env.example`** still carries `CLINIC_NAME`, `CLINIC_ADDRESS`,
  `CLINIC_PHONE` and `ACUITY_CALENDAR_ID_ALCESTER` / `_REDDITCH` / `_MARK` /
  `_LEANNE`. Tenant identity and per-practitioner calendars in environment
  variables is the leftover of the old model; moving them into `clinic.json` is
  `PRODUCTION_READINESS_PLAN.md` Phase 6.
- **Clinic names hardcoded in engine files:** `app/fast_path.py`,
  `app/flows/brain.py`, `app/booking/booking/utils.py`,
  `app/booking/booking/providers/acuity.py`. Theorem goes further — its prompt is
  built in Python (`_build_theorem_v3`) and `clinic.json` never reaches the model.
- **`render.yaml`** declares one service with `autoDeploy: true` and no branch
  pin — the branch is set **per-service in the Render dashboard**, which is why
  "what is actually live?" cannot be answered from this repo. That is FM-20.

If you find yourself writing `if clinic == "..."` in `app/`, that is the bug, not
the fix.

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

- `docs/plan/RELEASE_PROMOTION_DECISION.md` — **ADR-002, the deploy gate.**
  Read before any push. `docs/plan/BRANCH_DECISION.md` (ADR-001) is closed —
  its one-branch-per-clinic model was superseded by the fold; keep it for the
  reasoning, do not follow its workflow.
- `docs/plan/PRODUCTION_READINESS_PLAN.md` — phased roadmap with gates.
- `docs/plan/FAILURE_MODE_REGISTER.md` — ranked risk register.
- `docs/plan/SKILL_PLAYBOOK.md` — which engineering skill to invoke when.
- `docs/plan/KICKOFF_PROMPT.md` — paste-ready starting prompt.
- Templates to fill in Phase 0: `TEST_BASELINE.md`, `DELETED_TEST_TRIAGE.md`,
  `LATENCY_BASELINE.md`, `DEPLOYMENT_INVENTORY.md`.

Work the phases in order. Do not start a phase before its gate has passed.

**If these documents and the code disagree, the code wins** — record the
correction in `docs/plan/README.md`. They have already been wrong three times.
