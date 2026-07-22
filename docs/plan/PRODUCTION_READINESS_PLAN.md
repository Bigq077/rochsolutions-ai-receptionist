# Production Readiness Plan — 10 Working Days

**Baseline:** `latency-eval`
**Window:** Wed 22 Jul → Fri 31 Jul 2026 (Sat 25 / Sun 26 held as slack, not planned work)
**Hard date:** Hands On Money meeting, end of week commencing 27 Jul
**Author's stance:** this plan assumes the demo call is the thing that must not fail, and that everything else is triaged against that.

---

## 0. The honest position

Before the plan, the assessment, unvarnished.

**What is genuinely good.** The tenancy data model is real and mature — a 30 KB,
33-key `clinic.json` per clinic with knowledge, FAQ, pricing, insurance, STT
variants and tone. The latency work is deliberate (Frankfurt region chosen on
measured RTT, filler phrases, a fast path, streaming at every hop). There are 49
test files including a dedicated regression directory. There is a dedicated
clinical screening module. The observability subsystem is 18 modules deep,
flag-gated, with redaction, an LLM judge, replay and regression tooling — that
is more sophisticated than most funded startups have at this stage. This is not
a prototype; somebody has been doing real engineering here.

**What is not ready.** Three things, in order of severity:

1. **`handle_transcript()` is a single 15,734-line async method.** Every caller
   utterance passes through it. Nobody — human or model — can reason about the
   full state space of that function. Its behaviour under an unusual caller is
   not knowable by inspection, only by observation. And observation is exactly
   what this branch lacks.
2. **Failures are silent by construction.** 81 of 97 `except` clauses in
   `receptionist_tools.py` are broad. The realistic bad outcome is not a crash
   mid-call — it is a call that sounds flawless while the booking never lands,
   and nobody finds out until the patient turns up.
3. **Observability is built but switched off.** All four `OBS_*_ENABLED` flags
   default to `false` and no Postgres instance is provisioned. You currently
   cannot answer "did last night's calls go well" for the branch you are about
   to demo — not because the capability is missing, but because nobody turned it
   on. That is a one-day fix and it is the cheapest large risk reduction
   available to you.

**Verdict.** The system is *demo-ready* and is not *production-ready*. Those are
different claims and the gap between them is roughly this plan. Ten days is
enough to close it for a first cohort of ~10 clinics. It is not enough to close
it for 100, and no amount of effort in this window changes that — which is why
the cohort structure is a real engineering constraint and not a sales tactic.

**What this plan explicitly does not do:** refactor `flow.py`. That is a
multi-week project that would introduce more risk than it removes in this window.
It is deferred, deliberately, and scoped in Phase 6.

---

## Phase gates

Each phase has a gate. **Do not begin a phase until the previous gate passes.**
If a gate slips, the later phases are cut, not compressed — Phase 4 and Phase 5
are the designated sacrifices.

| Phase | Days | Theme | Gate |
|---|---|---|---|
| 0 | D1 (am) | Ground truth | Test suite green, latency baseline measured, branch state documented |
| 1 | D1–D2 | Stop the silent failures | No booking path can fail without an alert; all outbound calls have timeouts |
| 2 | D3 | Observability activation | Every call produces a record; failures visible within the hour |
| 3 | D4–D6 | Adversarial call hardening | 20-call adversarial suite passes; no dead air > 3 s |
| 4 | D7 (half) | SMS activation | Caller receives a confirmation; failure to send is alerted |
| 5 | D8 | Operational readiness | Runbook, rollback, on-call, deploy checklist exist and are rehearsed |
| 6 | D9–D10 | Demo hardening + roadmap | Live demo rehearsed 3× clean; post-meeting roadmap written |

---

## Phase −1 — Settle the branch (before anything else, ~2h)

**See `BRANCH_DECISION.md`. This blocks Phase 0.**

`latency-eval`'s own `LATENCY.md` declares it "a lab, not a release candidate…
never promoted by merging as-is." This plan was written assuming it is the
production base. One of those is wrong, and ten days of hardening on a branch
that was designed never to ship is the most expensive error available here.

Also: prune the ~15 stale worktrees under `AppData/Local/Temp/claude/` first.
A session has already measured the wrong tree once on this project.

**Gate −1:** the production base branch is named, written into
`BRANCH_DECISION.md` with reasoning, and every later document updated to match.

---

## Phase 0 — Ground truth (D1 morning, ~3h)

You cannot improve what you have not measured. Nothing else starts until this is
done, and it is deliberately timeboxed to half a day.

**Tasks**

1. **Green the suite.** `pytest` on `latency-eval`. Record pass/fail/skip counts
   and runtime. Any failing test is either fixed or explicitly quarantined with a
   written reason in `docs/plan/TEST_BASELINE.md`. A red suite for 10 days means
   you are flying blind.
2. **Deleted-test triage.** `git diff --stat main <base> -- tests/` shows **~30
   test files** differing — wider than first scoped. Classify each: **(a)**
   clinic-specific, correctly removed; **(b)** generic guard, wrongly removed —
   restore it; **(c)** superseded, name the replacement. Use
   `docs/plan/DELETED_TEST_TRIAGE.md`, which flags the four deletions most worth
   reading closely (`test_emergency_reask_suppression`, the transfer gates,
   `test_book_affirmative_gate`, and the deleted file from `tests/regression/`).
   This is a half-day job that could surface a regression you would otherwise
   meet live, on a call, in front of a client.
3. **Latency baseline** — *smaller than originally scoped.* Substantial prior
   work exists on `latency-eval`: `LATENCY.md` (master doc, locked WS-A baseline,
   n=28 across 4 calls), `LATENCY_HARNESS.md`, `LATENCY_WS-C.md`, and
   `app/media_streams/latency_timing.py`. Read those first. The job is to map the
   existing baseline onto per-hop code instrumentation, identify blind hops, and
   confirm the baseline is still valid at current HEAD. Fill
   `docs/plan/LATENCY_BASELINE.md`. Expected: a few hours, not a day. The point
   is a regression tripwire for Phase 2, not a research project.
4. **Deployment inventory.** For each of the four Render services: which branch,
   which clinic, which phone number, which Acuity calendar, last deploy, current
   commit. One table in `docs/plan/DEPLOYMENT_INVENTORY.md`. You are about to
   promise reliability for something you have not enumerated.

**Gate 0:** suite green (or every failure documented), latency baseline recorded,
deleted-test verdicts written, deployment table complete.

---

## Phase 1 — Stop the silent failures (D1 pm – D2)

The highest-value work in the entire plan. A call that fails loudly is an
inconvenience; a call that fails silently is a lost patient and a lost clinic.

**Tasks**

1. **Booking-path integrity audit.** Trace every path from "caller says yes" to
   "appointment exists in Acuity". At each step, answer: what happens if this
   throws? if this times out? if it returns a 2xx with an unexpected body?
   Produce `docs/plan/BOOKING_PATH_AUDIT.md`. Files:
   `app/tools/receptionist_tools.py`, `app/booking/booking/providers/acuity.py`,
   `app/booking/booking/utils.py`.
2. **Kill the broad excepts on the booking path only.** Not all 81 — scope this
   strictly to the booking and confirmation path. Each becomes either a specific
   exception with a defined recovery, or an explicit re-raise. Nothing on the
   booking path may swallow.
3. **Never confirm what you did not do.** Add a hard invariant: the assistant
   cannot utter a confirmation phrase unless the provider returned a booking ID.
   Test it by forcing Acuity failures. This is the single most important
   behavioural guarantee in the system.
4. **Timeouts everywhere.** Every outbound HTTP call gets an explicit, tuned
   timeout — aggressive for in-call paths (fail fast, speak a filler, degrade),
   generous for background paths. ~49 call sites currently lack one.
5. **Booking-failure escalation.** When a booking cannot be completed, the caller
   gets an honest outcome ("I'll have the clinic call you back within the hour")
   and a human gets notified with the caller's number and intent. Email via
   existing SMTP config is acceptable for the first cohort.

**Gate 1:** a deliberately broken Acuity credential produces — on a live test
call — an honest caller outcome, an operator alert, and zero false
confirmations. Demonstrated, not asserted.

**Skill:** `engineering:code-review` on the diff; `engineering:debug` for any
defect surfaced.

---

## Phase 2 — Observability activation (D3, ~1 day)

**Revised down from two days.** `app/obs/` is fully present on `latency-eval` —
18 modules covering capture, storage, LLM-judge scoring, alerts, redaction,
digests, replay and regression. It is switched off by environment flags, not
absent. This is provisioning and verification, not integration.

**Do not merge the `feat/obs-*` branches.** That work is already here.

**Tasks**

1. **Provision the database.** `OBS_DATABASE_URL` expects Postgres. Provision a
   dedicated instance (`fix/obs-dedicated-db-url` exists as a branch for a
   reason — obs writes must never contend with anything on the call path). Run
   `python -m app.obs.migrate`.
2. **Enable in stages, verifying each.** Do not flip all four at once.
   - `OBS_CAPTURE_ENABLED=true` — call records and transcripts. **Mandatory.**
     Verify a test call lands with a full transcript.
   - `OBS_ALERTS_ENABLED=true` + `OBS_ALERT_SMS_TO` — operator alerting.
     **Mandatory.** This is what closes FM-02.
   - `OBS_JUDGE_ENABLED=true` — LLM call-quality scoring. **High value.** Check
     `OBS_JUDGE_MODEL` cost before enabling on every call; `claude-haiku-4-5` may
     be the right first-cohort choice.
   - `OBS_DIGEST_ENABLED` and the dashboard — **defer** unless time is free.
     Keep `OBS_DIGEST_INCLUDE_TRANSCRIPTS=false` until the redaction path in
     `app/obs/redact.py` has been read and understood (see FM-19).
3. **Read the design docs first:** `CALL_OBSERVABILITY_RESEARCH.md` and
   `Susie_Call_Observability_Spec_for_Jules.docx`. Understanding the intended
   design costs an hour and will save you misreading the flags.
4. **Verify latency cost.** Re-run the Phase 0 baseline with capture on. Capture
   must be asynchronous and off the critical path. **50 ms p95 regression budget,
   enforced** — the entire purpose of this branch was latency.
5. **Verify the alert actually reaches a human.** An alert written to a database
   nobody opens is not an alert. Send one to a real handset and confirm receipt.

**Gate 2:** test call retrievable with full transcript within 60 s; a forced
booking failure produces an alert on a real device; p95 within 50 ms of baseline.

**The day this frees goes to Phase 3.** Do not bank it as slack.

**Skill:** `engineering:architecture` to record the integration decision as an
ADR — you will need to explain this choice again in three weeks.

---

## Phase 3 — Adversarial call hardening (D4–D6, three days)

Up to now the work has been structural. This is where the demo is won.

**Tasks**

1. **Build the adversarial suite.** Extend `JV_V1_8CALL_TEST_SUITE.md` to at
   least 20 scenarios. The eight happy-path calls are not the risk. Required
   scenarios:
   - Caller interrupts mid-sentence, repeatedly (barge-in).
   - Caller gives a name the STT will mangle; caller spells it; caller corrects it.
   - Caller changes their mind about the slot after confirming.
   - Caller asks something the knowledge base does not cover.
   - Caller is silent for 10 s mid-flow.
   - Background noise / speakerphone / poor line.
   - Caller describes a red-flag clinical symptom (cauda equina, chest pain).
   - Caller is angry, or is a supplier, or is a wrong number.
   - Caller asks for a human.
   - Caller gives a phone number with digits run together, or an 07 number read
     as words.
   - Two people talking on the caller's end.
   - Caller asks the price and then a follow-up that depends on the answer.
   Existing modules to lean on: `pause_detector.py`, `silence_handler.py`,
   `vagueness_detector.py`, `tone_detector.py`, `caller_classifier.py`,
   `name_collector.py`, `sidebar_handler.py`.
2. **Run them live.** Real phone calls to the deployed branch, not unit tests.
   Score each against the Phase 0 definition of ready. Log every deviation.
3. **Fix by blast radius, not by ease.** Rank defects by *would this lose the
   clinic the patient*. Fix in that order. Stop fixing when the top tier is
   clear — do not gold-plate.
4. **Dead-air guarantee.** No gap over 3 s without a filler or acknowledgement,
   under any failure condition. `filler_phrases.py` and
   `tests/test_dead_air_safety_net.py` exist; verify they hold when a provider
   hangs, not just when it is fast.

**Gate 3:** all 20 scenarios run live; every top-tier defect closed with a
regression test in `tests/regression/`; no dead air over 3 s observed.

**Skill:** `engineering:testing-strategy` to design the suite;
`engineering:debug` per defect.

---

## Phase 4 — SMS activation (D7, ~half a day)

**Revised down.** This is a flag, not a build: `SMS_ENABLED` defaults to `false`
in `app/notifications/booking_sms.py`. Note the in-code warning next to it —
whoever wrote it explicitly did not want that default flipped casually. Enable it
by environment, never by editing the default.

Still the **first thing to cut** if Phases 1–3 slip. A cohort of 10 clinics can
survive a week without SMS confirmations. It cannot survive silent booking
failures.

**Tasks**

1. Set `SMS_ENABLED=true` in the deployment environment. Code exists:
   `app/notifications/` (`booking_sms.py`, `smart_sms_router.py`, `templates.py`,
   `scheduler.py`, `sms.py`, `owner_alert.py`), `app/sms_templates.py`,
   `tests/test_sms_templates.py`.
2. Confirmation SMS must be triggered by a *confirmed booking ID*, never by the
   assistant's intent to book. Same invariant as Phase 1.
3. SMS send failure alerts an operator. A confirmation the caller never received
   is worse than no confirmation, because the clinic believes it went out.
4. Verify per-clinic sender identity and opt-out handling. UK regulatory basics —
   do not skip this because it is boring.

**Gate 4:** live test call produces a correct SMS to a real handset; a forced
send failure raises an alert.

---

## Phase 5 — Operational readiness (D8)

You are about to ask clinics to route their phone line through this. That
carries obligations beyond code.

**Tasks**

1. **Runbook** — `docs/RUNBOOK.md`. What to do when: Acuity is down, ElevenLabs
   is down, AssemblyAI is down, the LLM is rate-limited, Render is redeploying,
   a clinic reports a missed booking. Each with detection, immediate mitigation,
   and escalation.
2. **Rollback** — a documented, *rehearsed* procedure to return a Render service
   to the previous commit in under 5 minutes. Rehearse it once. An unrehearsed
   rollback is not a rollback.
3. **Emergency bypass** — a per-clinic switch that forwards all calls straight to
   the clinic's own line. This is your seatbelt. It must be operable from a phone,
   at speed, by a non-engineer. Nothing else in this plan matters as much on the
   day something genuinely goes wrong.
4. **Secret and config validation on boot** — 37 env vars. The service should
   refuse to start, loudly, rather than boot half-configured and fail on call
   three.
5. **On-call reality check.** For the first cohort, who answers the phone when a
   clinic calls at 08:30 saying the receptionist is broken? Write the answer
   down. If the answer is "you", write down what happens when you are asleep.

**Gate 5:** rollback rehearsed and timed; bypass tested on a live number;
runbook complete; boot-time config validation in place.

**Skill:** `engineering:deploy-checklist`, `engineering:incident-response`,
`engineering:documentation`.

---

## Phase 6 — Demo hardening and forward roadmap (D9–D10)

**Tasks**

1. **Rehearse the demo three times, end to end, on the real number**, on the
   deployment you will actually use, at the time of day you will use it. Cold
   starts, DNS, Render spin-up — all of these have ruined demos. Any failure
   resets the count to zero.
2. **Prepare a fallback.** A recorded call of the assistant performing well. If
   the live line has a bad moment, you continue the meeting rather than
   debugging in front of the client. Not defeatism — professionalism.
3. **Freeze.** No code changes after the final clean rehearsal. None. The urge to
   improve one more phrase the night before is how demos die.
4. **Write the post-meeting roadmap** — `docs/plan/POST_MEETING_ROADMAP.md`,
   covering:
   - **Runtime tenancy** (the big one). Move `CLINIC_NAME`, `CLINIC_ADDRESS`,
     `CLINIC_PHONE` and the `ACUITY_CALENDAR_ID_*` family out of env vars and
     into `clinic.json`; select the tenant from the inbound Twilio number;
     remove hardcoded clinic references from `fast_path.py`, `flows/brain.py`,
     `booking/utils.py`, `providers/acuity.py`; collapse four Render services to
     one. Estimated 1–2 weeks. **This is what makes cohort onboarding possible.**
   - **`flow.py` decomposition.** Carve `handle_transcript()` into named,
     testable handlers behind a characterization test suite recorded from real
     calls. Multi-week. Do not start it without the tests first.
   - **Self-serve onboarding** — a clinic config wizard, so onboarding is a form
     rather than an engineer.
5. **Prepare the scale answer.** Someone at the webinar will ask how fast you can
   onboard. The credible answer is a cohort model — "ten clinics per cohort, each
   properly configured" — plus a genuine engineering roadmap behind it. Have both
   ready.

**Gate 6:** three consecutive clean rehearsals; code frozen; roadmap written.

---

## Triage rules if you fall behind

Decide this now, while calm, not on D8 at midnight.

- **Never cut:** Phase 0, Phase 1, Phase 5 item 3 (emergency bypass).
- **Cut first:** Phase 4 (SMS).
- **Cut second:** Phase 2's judge/dashboard components — keep capture and alerts.
- **Cut third:** the lower tier of Phase 3 scenarios — keep barge-in, silence,
  name capture, red-flag clinical, and booking failure.
- **Never add:** any new feature, for any reason, including because the client
  asked in the meeting. Write it down, ship it after.

---

## Success criteria for the window

By EOD Fri 31 Jul:

- [ ] Test suite green; baseline documented
- [ ] No path exists where the assistant confirms a booking that did not occur
- [ ] Every booking failure produces an operator alert within 5 minutes
- [ ] Every call produces a retrievable record with transcript
- [ ] 20 adversarial scenarios run live, top-tier defects closed
- [ ] Every outbound HTTP call has an explicit timeout
- [ ] Rollback rehearsed under 5 minutes
- [ ] Per-clinic emergency bypass tested live
- [ ] Runbook written
- [ ] Three consecutive clean demo rehearsals
- [ ] Post-meeting roadmap written, with runtime tenancy scoped

Ten of eleven is a good outcome. All eleven with a rushed Phase 3 is worse than
nine with a thorough one.
