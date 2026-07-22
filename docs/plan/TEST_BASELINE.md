# Test Baseline

**Filled 2026-07-21 (Phase 0 item 1).** Branch: `release/cohort-1` · Engine:
`origin/latency-eval@022f816` (the FM-01 doc/fix commits do not change the 96
failures — verified: pre/post-fix failure sets are byte-identical).

---

## Summary

| Metric | Value |
|---|---|
| Tests collected | 1257 (`testpaths = tests`) |
| Passed | 1161 (incl. the 6 new FM-01 regression tests) |
| Failed | **96** |
| Skipped / collection errors | 0 |
| Wall-clock runtime | ~140 s (0:02:20) |

Command: `pytest -q` (`pytest.ini`, `asyncio_mode = auto`). Pass rate 92.4%.
`app/booking/tests/` is outside `testpaths` — **not yet run separately** (open item).

---

## FM-01 (fixed this session — the one confirmed live defect)

`book_appointment` could fire on a non-yes reply once the confirmation *question*
was asked. Reproduced, fixed with a minimal diff (`llm_stream.py` — one helper +
one `elif`), regression test in `tests/regression/test_book_affirmative_gate.py`
(6/6). **Regression proof:** full suite pre-fix vs post-fix = the same 96
failures, byte-for-byte (0 introduced, 0 coincidentally fixed).

---

## The only question that matters: any failure on a safety path?

| Safety path | Failures | Verdict |
|---|---|---|
| Booking **write** (`book_appointment`) | 0 (FM-01 fixed + guarded) | **Clear** |
| **Clinical screening** (red-flag / emergency / cauda equina) | **0** | **Clear** |
| **Transfer / escalation to a human** | **0** (`test_third_silence_triggers_transfer` passes) | **Clear** |
| Booking **adjacent** (phone-confirm gate, dead-air net) | **2 candidates** | **Review in Phase 1 — see below** |

No failure books the wrong thing, misses a red flag, or breaks the human-transfer
escape hatch. The two candidates are on booking-*adjacent* paths and are
drift-vs-defect ambiguous — flagged, not fixed.

---

## Bucketed triage — all 96

| Subsystem (file) | # | Safety path? | Root cause (one line) | Disposition |
|---|---|---|---|---|
| `test_name_collector.py` | 36 | no | name-capture flow reworked (fn/sn confirm, reask, SMS-flag, escalation-to-*reask*) — tests assert the old flow | **Quarantine** — name-flow drift |
| `test_embedded_confirmation.py` | 20 | booking-flow **phrasing** | location-embedding + "use this number"/phone-question wording contracts changed | **Quarantine** — UX phrasing drift; phone-wording cluster → glance w/ FM-21 |
| `test_mistake_recovery.py` | 8 | confirmation-adjacent | (a) `caller_classifier` LLM unmocked → JSON parse fail; (b) empty-slot now *reroutes* instead of auto-confirming (**more** conservative) | **Quarantine** — env + safer-behaviour; note design Q below |
| `test_silence_handler.py` | 5 | no | silence re-ask wording / thresholds changed | **Quarantine** — wording drift |
| `test_greeting_builder.py` | 5 | no (but disclosure) | greeting construction wording changed | **Quarantine** — but verify AI-disclosure separately (moved to system prompt) |
| `test_faq_continuation.py` | 5 | no | FAQ follow-up flow / clinic content drift | **Quarantine** — FAQ drift |
| `test_filler_guard.py` | 4 | no | filler-phrase set / guard changed | **Quarantine** — latency/filler drift |
| `test_service_fit_priority.py` | 2 | Theorem policy | Theorem child-policy service-fit config | **Quarantine** — Theorem (non-cohort) policy |
| `test_policy_gate.py` | 2 | **safeguarding-adjacent** | Theorem minor-age 15 now `ALLOW` vs expected `DISALLOW` | **Quarantine for JV** — but **verify per-clinic** before any Theorem go-live |
| `test_dead_air_safety_net.py` | 2 | **FM-03 (dead air)** | net fired while `_tts_playing` (stale-flag backstop force-clear) + a keypad nudge during DTMF | **FM-22 candidate** (TTS one) + benign (DTMF nudge) |
| `test_acuity_live.py` | 2 | booking backend | **live Acuity returned real slots**; tests assert renamed field `start` (now `start_time`) + log format | **Quarantine** — test drift; *confirms Acuity is live & working* |
| `test_sms_templates.py` | 1 | no | slot-label formatting changed (SMS is flag-gated OFF anyway) | **Quarantine** — SMS drift |
| `test_service_fit_policy.py` | 1 | Theorem policy | Theorem service-fit scenario config | **Quarantine** — Theorem policy |
| `test_returning_treatment_plan_exit.py` | 1 | no | returning-patient exit flow drift | **Quarantine** — flow drift |
| `test_critical_flows.py` | 1 | **booking phone gate** | `TestReschedulePhoneGate` — `phone_confirmed` not `True` after the driven flow | **FM-21 candidate** — verify phone gate not regressed |
| `alerts/test_alerts.py` | 1 | no | obs alerts (subsystem flag-gated OFF) | **Quarantine** — obs off |
| **Total** | **96** | | | |

**Design question surfaced (not a failure):** `test_mistake_recovery` encodes an
old rule that an *unclassifiable* readback is "treated as confirmed." Current code
reroutes instead (safer). Confirm no path still auto-confirms an unclassifiable
readback — that would be an FM-01 sibling. One-hour check in Phase 1.

---

## Candidate FMs (flagged, NOT fixed — per instruction)

- **FM-21 · Booking phone-confirmation gate may be regressed.**
  `test_critical_flows.py::TestReschedulePhoneGate::test_booking_flow_phone_gate_not_regressed`
  expects `session["phone_confirmed"] is True` after a booking-flow drive; it is
  `False`. On the `flow.py` FlowEngine (live path). Drift-vs-defect unresolved.
  *Verify:* does the phone-confirm gate still block a book on an unconfirmed number?
  (Sibling of the FM-01/phone backstop.)
- **FM-22 · Dead-air net can fire while TTS is (thought to be) playing.**
  `test_dead_air_safety_net.py::test_no_fire_while_tts_playing` — a "Bug A
  backstop" force-clears a `_tts_playing` flag it deems stale (`playout ended
  -1.0s ago`) and then fires a re-ask. If the staleness detection is wrong in
  prod, the assistant talks over its own audio (FM-03). *Verify:* is the stale-flag
  detection sound under real playout timing, or only a test-clock artifact?

---

## Verdict

- [ ] Suite green (or every failure has a written verdict) — **verdicts written
      above; quarantine not yet applied in code**
- [x] No unexplained skips (0 skips)
- [ ] Flakiness check (3× run) — **not yet done**
- [x] Runtime under 2 min target — 2:20 (close; acceptable)

**Gate 0 item 1: NOT yet passed** — the verdicts exist but the ~92 benign-drift
failures are not yet quarantined in code, so the suite cannot serve as a green
regression tripwire, and FM-21/FM-22 are unresolved.

**Recommendation (safety of building Phase 1 on this baseline):** **Yes, with two
conditions.** From a *safety* standpoint the baseline is sound — zero failures on
the clinical-screening and human-transfer paths, and the booking-write hole
(FM-01) is closed and guarded. The 96 are concentrated in name-capture, phrasing,
Theorem-only policy, greeting/FAQ wording, fillers, SMS templates and unmocked-LLM
/ live-integration env issues — none of which touches a cohort clinic's safety. So
Phase 1 ("stop the silent failures") can start. But before leaning on the suite:
(1) resolve **FM-21** and **FM-22** (they are exactly Phase-1-shaped), and
(2) apply the quarantines above so a real regression is visible against green —
otherwise a new break hides in a field of 96 reds. Do **not** bulk-fix the 96;
quarantine the benign, verify the two candidates.
