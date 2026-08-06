# CLAUDE.md — RochSolutions AI Receptionist ("Susie")

Context file for Claude Code. Read this before touching anything.

---

## 1. What this is

A voice AI receptionist for physiotherapy clinics. Callers phone a clinic number,
Twilio streams the audio to this FastAPI service over a WebSocket, and the
assistant answers questions, screens clinically, and books appointments into the
clinic's real booking system.

Real callers. Real bookings. A bug here is a missed patient, not a failed test.

**Commercial context (updated 2026-08-06):** the Hands On Money meeting
**happened on 2026-08-05 and went well** — live demo landed, open invitation to
their webinar mid-September. HOM is a financial-advice network of ~230–250
physiotherapy clinics, ~100 on the webinar list, **taking no cut**.

Client state:

| Client | State |
|---|---|
| Theorem Health | **Live since 2026-08-05** — real patients |
| Vital Edge | Live on voicemail; moving to its own number (decision taken) |
| Joint Venture | Go-live slipped from 2026-08-03, client-side, no hard deadline |

Plan: `docs/plan/COHORT_1_PLAN.md`. Planning number is **4–7 clinics** from the
webinar, cohort **capped at 6** — the constraint is onboarding throughput, not
demand. The near-term goal is not scale; it is that the first cohort is flawless,
because in a 250-clinic network that talks constantly, one missed booking is
contagious in a way one delighted clinic is not.

---

## 2. Canonical branch

> ⚠️ **Changed 2026-08-06 by ADR-002.** If you are reading a `CLAUDE.md` that
> says the branch decision is *"contested and must be settled before any work
> starts"* — that is a **stale untracked 224-line copy** in the working tree.
> This tracked file is the source of truth. Delete the other one.

**`engine/converged` is the engine branch**, cut from `theorem-onboarding`.
See `docs/plan/BRANCH_DECISION.md` (ADR-002) and
`docs/plan/BRANCH_CONVERGENCE_ANALYSIS.md`.

**Why it moved.** ADR-001 made `latency-eval` the trunk on 2026-07-21 under a
canonical-first-by-cherry-pick rule. That mechanism failed: **33 engine commits
landed on `theorem-onboarding` and never went to `latency-eval` first**, so
`latency-eval` is no longer the superset the rule assumed. The T-2 and T-3 fixes
were applied twice on 2026-08-05 with byte-identical `patch-id`s — the
fix-once-per-branch tax, paid in the open. ADR-002 ratifies where the code
actually went rather than replaying 33 commits backwards.

**Do not reuse the name `release/cohort-1`.** It was cut per ADR-001, merged into
`latency-eval`, and retired. A second branch under that name will be conflated
with the first.

**Canonical-first still applies until Stage 3 of `CONVERGENCE_RUNBOOK.md`
lands** — engine fixes go to `engine/converged` first, clinics inherit. But treat
it as a stopgap, not the destination: it is manual convergence performed by a
human forever, it broke after two weeks at four clinics, and it will break again
at six. One codebase + env-var tenancy is what retires it.

### Superseded topology note (kept for context)

The pre-ADR-002 arrangement was: one engine
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

> ✅ **`latency-eval` is not a live line — push whenever.** Corrected 2026-08-02
> by the repo owner. This block previously said a push here was a live deploy
> needing out-of-hours timing and coordination; that was wrong and it cost real
> time, because agents kept staging finished, suite-verified work overnight for
> a deploy window that does not exist.
>
> The gated branches are the two **deployment** branches — `jv-v1-onboarding`
> and `vitaledge-onboarding` — which serve live clinics. Apply out-of-hours
> timing, a revert commit in hand, and coordination **there**, not here.
>
> The canonical-first rule is unchanged: engine fixes land here first and the
> clinic branches inherit them by cherry-pick.

> ⚠️ **Check which branch and which worktree you are actually in before
> measuring anything.** **65** registered worktrees under
> `AppData/Local/Temp/claude/` as of 2026-08-06 — not ~15, and
> `git worktree prune` **fails on all of them** with *Permission denied*
> (OneDrive file locking). Pause OneDrive sync and close editors first, or the
> prune silently does nothing and the trap stays armed. Then
> `git rev-parse --abbrev-ref HEAD` before you trust a single number.
>
> **Measure against `origin/*`, never local refs, and `git fetch --all` first.**
> This has now produced a wrong number in three separate sessions. Most recently
> (2026-08-06): `app/obs/` file counts taken from a stale *local* `latency-eval`
> produced "four different versions of the observability engine" — false. The
> three live branches carry an **identical 19-module `app/obs/`**.
>
> Note also: the plan documents in `docs/plan/` may be **untracked**. Git history
> searches will not find them. Use `ls`. Eleven were untracked on 2026-08-06,
> including `STATUS.md` and the whole `JULES_*` handoff set.

Two subsystems are deliberately switched off on this branch — they are **fully
implemented and flag-gated**, not missing. This is much better news than it
sounds and it materially shrinks the work:

| Subsystem | Code | Off switch |
|---|---|---|
| Observability | `app/obs/` — **19 modules** (`store`, `judge`, `alerts`, `digest`, `regress`, `replay`, `dashboard`, `redact`, `migrate`, `worker`, `cost`, …) — identical on `latency-eval`, `vitaledge-onboarding`, `theorem-onboarding` | `OBS_CAPTURE_ENABLED`, `OBS_JUDGE_ENABLED`, `OBS_ALERTS_ENABLED`, `OBS_DIGEST_ENABLED` all default `false`; needs `OBS_DATABASE_URL` + `python -m app.obs.migrate` |
| SMS | `app/notifications/` — `booking_sms`, `smart_sms_router`, `templates`, `scheduler`, `sms`, `owner_alert`, `digest`, `email` | `SMS_ENABLED` defaults `false` in `booking_sms.py` (with an explicit in-code warning not to flip the default) |

**Do not reimplement either, and do not merge the `feat/obs-*` branches into
this one.** The work is enabling, provisioning and verifying — not building.

> ⚠️ **`jv-v1-onboarding` has only 2 of the 19 obs modules** (`__init__`,
> `alerts`) and **no `clinical_screening.py` at all.** It pages an operator on
> hard failure but captures nothing: no call record, no quality score, no
> digest. It contributes nothing to the improvement flywheel and has no
> deterministic red-flag intercept.
>
> **Do not take Joint Venture live on `jv-v1-onboarding`.** It is 352 commits
> behind with a tip dated 2026-07-24 (frozen per the old `STATUS.md` rule, while
> Vital Edge was un-frozen and re-converged on 08-04 — JV was left behind by
> omission, not decision). Go live on `engine/converged` instead:
> `CONVERGENCE_RUNBOOK.md` Stage 4, which also makes JV the migration pilot.

**Per-call cost of goods** (`app/obs/cost.py`, added 2026-08-06) estimates COGS
in integer pence per call. **`RATES` are unfilled placeholders and the module
refuses to produce a number until they are populated from real vendor
invoices** — not list prices. See `docs/plan/COST_ROLLUP_SPEC.md`. This blocks
the flat-vs-tiered pricing decision for cohort 1.

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

**Current, as of 2026-08-06 — read these first:**

- `docs/plan/COHORT_1_PLAN.md` — what we sell, to how many, by when. Pricing,
  the cap at 6, dated milestones. **Go/no-go on the webinar date: 2026-08-28.**
- `docs/plan/BRANCH_CONVERGENCE_ANALYSIS.md` — measured branch topology. §8 is
  the root cause of the repeated wrong-measurement errors.
- `docs/plan/CONVERGENCE_RUNBOOK.md` — six stages, each gated, to one engine.
- `docs/plan/COST_ROLLUP_SPEC.md` — per-call COGS. Code built, rates unfilled.
- `docs/plan/BRANCH_DECISION.md` — ADR-001 (superseded) + **ADR-002 (proposed,
  awaiting owner sign-off)**.
- `docs/plan/PRODUCTION_READINESS_PLAN.md` — phased roadmap with gates.
- `docs/plan/FAILURE_MODE_REGISTER.md` — ranked risk register.
- `docs/plan/SKILL_PLAYBOOK.md` — which engineering skill to invoke when.
- `docs/plan/KICKOFF_PROMPT.md` — paste-ready starting prompt.
- Templates to fill in Phase 0: `TEST_BASELINE.md`, `DELETED_TEST_TRIAGE.md`,
  `LATENCY_BASELINE.md`, `DEPLOYMENT_INVENTORY.md`.

Work the phases in order. Do not start a phase before its gate has passed.

**If these documents and the code disagree, the code wins** — record the
correction in `docs/plan/README.md`. They have already been wrong five times.

**And if two documents disagree, check which one is tracked.** On 2026-08-06 an
untracked `CLAUDE.md` in the working tree contradicted the tracked one and cost
a full session's analysis. `git ls-tree <branch> -- <file>` settles it. Trust
tracked over untracked, and `origin/*` over local.
