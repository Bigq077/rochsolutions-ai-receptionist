# Call Observability, QA & Alerting for Susie

**RochSolutions — Engineering Brief · Technical specification & recommendations**

| Field | Detail |
|---|---|
| Owner | Quentin Roch (RochSolutions) |
| Assigned to | Jules (engineering) |
| System | Susie — FastAPI + Twilio Media Streams voice receptionist |
| Date | 5 July 2026 |
| Status | Proposal for build — awaiting decisions in §9 |

---

## 1. Objective

Susie is now live with real, paying clinics (Theorem/Mark, Joint Venture/Marcus and others). Two operational gaps block us from running the service safely and improving it at scale:

- **Problem A — the improvement loop.** We log calls to a Google Sheet, but nothing turns those calls into system improvements. We cannot see, at a glance, where Susie is failing or whether she is getting better or worse over time.
- **Problem B — the safety net.** When a call goes badly for a client, no one is alerted. Today we would only find out if the clinic complains — which, for a referral-led, trust-based business, is the most damaging way to find out.

**Root cause (single).** Both problems reduce to one thing: **the system cannot currently distinguish a good call from a bad one.** The existing `success` / `reason` fields are self-reported by the flow logic — a `caller_hung_up` could be a benign wrong number or Susie confusing a patient into giving up. Fixing this one thing unlocks both A and B.

**Goal of this work:** give us a durable record of what happens on every call, an automated judgement of call quality, an alert when things break, and a feedback loop that turns real failures into regression tests.

---

## 2. Current state (audit)

What exists in the codebase today, and why each piece is insufficient for the two goals:

| Component | What it does | Limitation |
|---|---|---|
| `call_logger.py` → `logs/*.jsonl` | One structured JSON record per call (timing, `success` bool, `reason` label, collected slots, booking id, retries, turn count, tone). | Written to **local disk on Render — ephemeral**. Almost certainly lost on redeploy. No durable transcript. `success`/`reason` are flow-reported, not a quality judgement. |
| `tools/actionable_summary.py` → Google Sheet | LLM-enhanced one-liner + follow-up note per call, for the clinic. | Human-facing, per-clinic. Not machine-readable as an improvement signal. No score, no aggregation, no trend. |
| `notifications/owner_alert.py` | Real-time SMS to owner on **success** events (booking confirmed / manual follow-up). | Fires on good news only. **Nothing fires when a call fails.** |
| `notifications/digest.py` | End-of-day booking email digest. | Bookings only — not failures or quality. |
| `tests/auto/` (Phases 1–17) | Scripted regression scenarios, run manually pre-deploy. | Strong asset, but hand-written and manual — not fed by real production failures. |

**Immediate risk to verify first:** confirm whether Render has a persistent disk mounted. If not, we are already losing call records on every deploy, and step 5.1 becomes urgent.

---

## 3. Target architecture

Five layers. We do not need all five at once; naming them keeps the build ordered and the dependencies explicit.

| # | Layer | Purpose | Solves |
|---|---|---|---|
| 1 | Capture | Durably store every call's transcript + metadata (audio optional/deferred). | Foundation |
| 2 | Evaluate | Score each call automatically (LLM-as-judge) against a rubric + failure taxonomy. | A + B |
| 3 | Alert | Push a notification when (a) something breaks or (b) a call scores badly. | B |
| 4 | Regress | Mine real failures into the test suite; run before every deploy. | A |
| 5 | Report | Per-clinic dashboard + trends over time (internal, later client-facing). | A |

---

## 4. Recommended approach — hybrid

**Buy the backbone, build the glue.** Adopt a self-hostable observability/eval backbone for storage, scoring and dashboards; build the thin integration and alert logic ourselves against Susie's existing session model.

**Why not a pure voice-native QA platform (Hamming / Coval / Cekura / Maxim)?**

- They are powerful (audio-native eval, simulated-caller fleets, regression CI) but assume you run on Vapi / Retell / LiveKit. Susie is a custom FastAPI + Twilio stack, so integration is via transcript/event ingestion anyway — we lose most of the turnkey benefit.
- They place patient (special-category) data in a US vendor. Usable only with a DPA + safeguards, and it is a harder client conversation.
- Keep them on the radar for simulated-caller load/regression testing later (Coval and Hamming carry HIPAA/BAA).

**Why a self-hosted backbone (Langfuse, EU region)?**

- One system for capture + LLM-as-judge evals + scoring + dashboards; fits a custom stack via SDK, not platform lock-in.
- Self-hostable in an EU/UK region → patient data never leaves our control — the cleanest GDPR posture (see §7).
- Open-source (MIT); well-funded (ClickHouse acquisition, Jan 2026) — low abandonment risk.
- Trade-off: text/transcript layer, not audio-native. Acceptable — transcripts are enough for scoring, and audio carries the heavier compliance load.

---

## 5. Workstreams for Jules

Ordered by dependency and value. Effort estimates assume one engineer familiar with the codebase.

### 5.1 — Durable capture (foundation)

**Effort: ~2 days. Blocks: everything below.**

- Stop treating `logs/*.jsonl` as the source of truth. Persist each completed call to a real store: managed Postgres (EU region) or self-hosted Langfuse as the store.
- Persist the full turn list (`session["turns"]`) as the transcript, plus all existing `call_logger` fields. Do not drop the transcript — it is the input to every downstream layer.
- Add call outcome + transcript write into the existing teardown path (where `CallLogger.flush()` is called), so capture is guaranteed on hangup and on pipeline error.
- **Acceptance:** every inbound call produces one durable row containing transcript + metadata, surviving a Render redeploy; verified by triggering a deploy and re-querying.

### 5.2 — Rule-based failure alerting (fastest win — solves Problem B now)

**Effort: ~2–3 days. Depends on: nothing (can ship in parallel with 5.1).**

Two channels: (a) technical exceptions via Sentry, (b) call-level failure conditions via SMS/Slack to Quentin. Alert conditions (v1):

| Condition | Signal | Severity → channel |
|---|---|---|
| Pipeline / unhandled exception | Sentry captures in `twilio.py` / `media_streams` | High → Sentry + SMS |
| STT or TTS stream failure mid-call | stream error events | High → Sentry + SMS |
| Booking API (Acuity/GCal) error | `calendar_error` set / non-2xx | High → SMS |
| Escalation requested but SMS not delivered | `transfer_attempted` true + send failure | Critical → SMS |
| Call reaches Susie then ends < 15s | `duration_s < 15` and `turn_count ≤ 1` | Medium → daily roll-up |
| Retry storm (≥ 3 retries on any slot) | `slot_retry_counts` | Medium → daily roll-up |

- **Acceptance:** a deliberately broken booking call and a forced exception each produce an alert to Quentin within seconds; benign short calls roll up into a daily summary, not per-call noise.

### 5.3 — LLM-as-judge scoring (engine for Problem A)

**Effort: ~1 week. Depends on: 5.1.**

Add one post-call step that sends the transcript to Claude (we already call it) and returns a structured judgement. Run it async after teardown so it never adds call latency. Store the result on the call row and push to Langfuse as a score.

**Suggested output schema** (store verbatim; keep the prompt and rubric versioned so scores are comparable over time):

```json
{
  "call_sid": "CA...",
  "clinic_id": "theorem",
  "outcome": "booked | resolved | no_booking | abandoned | misrouted",
  "quality_score": 1-5,          // overall caller experience
  "intent_resolved": true|false, // did the caller get what they called for?
  "failure_tags": [              // empty if clean
    "hallucination", "wrong_info", "dead_end", "caller_frustration",
    "wrong_service_fit", "booking_error", "missed_escalation", "loop"
  ],
  "evidence": "1-2 sentences quoting the turns that justify the score",
  "rubric_version": "v1"
}
```

- **Rubric anchors (define explicitly in the prompt):** 5 = booked/resolved cleanly, natural; 3 = resolved but clumsy or slow; 1 = caller left worse off / given wrong info / missed a clinical escalation.
- **Calibration:** hand-label ~30 real calls with Quentin, measure agreement with the judge, tune the rubric before trusting scores. Re-run calibration whenever `rubric_version` changes.
- **Wire into alerting:** `quality_score ≤ 2` OR `failure_tags` contains `missed_escalation` / `wrong_info` → immediate review alert (bridges into 5.2).
- **Acceptance:** every captured call has a judge score within a minute of ending; low-score calls raise a review alert; scores are queryable by clinic and date.

### 5.4 — Failure → regression pipeline (closes the improvement loop)

**Effort: ~2–3 days initial, then ongoing. Depends on: 5.3.**

- Build a one-command path to convert a judged-bad real call into a scenario in `tests/auto/scenarios/` (redacting PII — see §7).
- Run the scenario suite in CI before every deploy so a fixed failure can never silently regress.
- **Acceptance:** a real failure can be turned into a passing/failing test in minutes; the suite runs automatically pre-deploy and blocks on regressions.

### 5.5 — Dashboard & trends

**Effort: ~2–3 days. Depends on: 5.1, 5.3.**

- Use Langfuse's built-in dashboards for internal views: volume, booking rate, mean quality score, failure-tag frequency — sliced by clinic and over time.
- Defer a client-facing view until internal scores are calibrated and trusted. When built, it doubles as a retention/trust asset for clinics.

---

## 6. Sequencing

| Order | Workstream | Effort | Unblocks |
|---|---|---|---|
| 0 | Verify Render persistence (§2) | 1 hr | Sets urgency of 5.1 |
| 1 | 5.1 Durable capture | ~2 days | 5.3, 5.5 |
| 2 | 5.2 Failure alerting | ~2–3 days | Solves B immediately |
| 3 | 5.3 LLM-as-judge scoring | ~1 week | A, quality alerts |
| 4 | 5.4 Failure → regression | ~2–3 days | Auto-improve loop |
| 5 | 5.5 Dashboard & trends | ~2–3 days | Visibility |

**Ship 5.2 in parallel with 5.1** — it is the highest-ROI item and independently removes the biggest operational risk (silent failures for live clients).

---

## 7. Compliance constraints (UK healthcare — binding)

These are not optional and they shape the technical choices above.

- **Special-category data.** Patient call transcripts (and any audio) are health data under UK GDPR / DPA 2018 — stricter handling: lawful basis, encryption, access control, defined retention, secure deletion.
- **EU/UK data residency.** Prefer self-hosted store (Langfuse EU / Postgres EU) over US SaaS. Any US processor requires a DPA + safeguards.
- **Transcripts over audio for v1.** Do not enable Twilio recording yet. Audio requires informing the caller before recording and a lawful basis (implied consent is not enough); ICO fines reach £17.5M / 4% of turnover. Transcripts are sufficient for scoring and lower-risk.
- **PII redaction before test fixtures.** The 5.4 pipeline must strip names, phone numbers and clinical details before a call becomes a committed scenario.
- **Retention policy.** Set and enforce a retention window on stored transcripts/scores; auto-delete past it.

---

## 8. Out of scope (v1)

- Audio recording and audio-native evaluation.
- Simulated-caller load testing / third-party QA platform (revisit once the in-house loop is proven).
- Client-facing dashboard (internal first).

---

## 9. Decisions needed before build

1. **Render persistence:** is a persistent disk mounted, or are we already losing call logs? (Verify — gates urgency of 5.1.)
2. **Store choice:** self-hosted Langfuse (EU) vs managed Postgres + minimal UI. Recommendation: Langfuse EU.
3. **Alert recipients:** Quentin only, or clinic owner too — and at what severity threshold?
4. **Alert channel:** SMS (reuse existing Twilio path) and/or Slack?
5. **Budget:** appetite for any paid tooling, or in-house + open-source only?
6. **Volume:** current live calls/day across clients — sizes storage + judge-inference cost.

---

## Appendix — sources

- [Voice agent testing platforms 2026 (Speechmatics)](https://www.speechmatics.com/company/articles-and-news/de-risk-your-voice-agent-11-best-voice-agent-testing-platforms)
- [Hamming vs Cekura (Coval)](https://www.coval.ai/blog/hamming-vs-cekura)
- [AI agent observability tools 2026 — Langfuse/ClickHouse (AIMultiple)](https://aimultiple.com/agentic-monitoring)
- [Voice agent evaluation metrics (Hamming)](https://hamming.ai/resources/voice-agent-evaluation-metrics-guide)
- [LLM-as-a-judge (MLflow)](https://mlflow.org/llm-as-a-judge)
- [Retell post-call analysis & webhooks](https://www.retellai.com/blog/vapi-ai-review)
- [GDPR recording phone calls UK (PurpleBox)](https://purpleboxuk.com/2025/06/07/gdpr-recording-phone-calls-uk/)
- [Patient data privacy for UK physio clinics (Sprintlaw)](https://sprintlaw.co.uk/articles/privacy-and-patient-data-collection-for-uk-physiotherapy-clinics/)
