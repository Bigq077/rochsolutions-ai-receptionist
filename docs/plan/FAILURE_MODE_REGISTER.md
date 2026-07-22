# Failure Mode Register

Ranked by **blast radius × likelihood**, not by how interesting they are to fix.
Blast radius is measured in the currency that matters: *does the clinic lose a
patient, and do they find out from the patient rather than from us?*

Scoring: Likelihood L1 (rare) → L5 (expect it weekly at 10 clinics).
Impact I1 (caller mildly annoyed) → I5 (clinic loses a patient and trust).

> **⚠️ ID COLLISION — read before citing an FM number.** Two different defects
> are both called **FM-01** in this repo:
>
> * **FM-01 in THIS register** — *silent booking failure*: the assistant claims
>   success when the provider call actually failed. The invariant is "only claim
>   success on a returned booking ID". This is the same defect Jules's sweep
>   calls **P1 #5 (false 'all booked')**. **Still OPEN.**
> * **FM-01 in the commit log and `DELETED_TEST_TRIAGE.md`** — the *book
>   affirmative gate*: `book_appointment` fired without the caller saying yes.
>   Guard is `_book_reply_is_affirmative`. **CLOSED** (commit `c01fddb`).
>
> They are different control points: the gate checks the tool's **input** (did
> the caller consent?); the booking-ID invariant checks its **output** (did it
> actually succeed?). Closing one does not close the other. The engineering
> handoff's instruction to "merge P1 #5 with FM-01 into ONE confirmation guard"
> appears to stem from this collision — do **not** merge them, or the phantom
> appointment survives behind a guard that looks like it covers it.
>
> Numbers are left as-is rather than renumbered: every existing reference would
> otherwise break. Cite as "register FM-01" or "gate FM-01" when it matters.

---

## Tier 1 — Must be closed before any clinic goes live

### FM-01 · Silent booking failure
**L4 · I5 · Phase 1**
The assistant confirms an appointment; the provider call failed, timed out, or
returned an unexpected body; the broad `except` swallowed it. The call sounds
perfect. The patient arrives to no appointment.

*Evidence:* on `latency-eval`, 87 of 104 `except` clauses in
`app/tools/receptionist_tools.py` (6,135 lines) are `except Exception` or bare.
Many outbound HTTP call sites lack explicit timeouts.

*Mitigation:* confirmation utterances gated on a returned booking ID; specific
exceptions with defined recovery on the booking path; operator alert on failure;
honest caller outcome ("the clinic will call you back within the hour").

*Verification:* break the Acuity credential, place a live call, confirm no false
confirmation and an alert fires.

---

### FM-02 · Failure is invisible
**L5 · I5 · Phase 2**
All four `OBS_*_ENABLED` flags default to `false` on `latency-eval` and no
Postgres is provisioned. Any of the failures in this register can occur
repeatedly without anyone knowing. This failure mode *multiplies every other
one* — it is why it scores L5.

It is also the cheapest to close: the subsystem is fully built
(`app/obs/`, 18 modules). Provision a database, run `python -m app.obs.migrate`,
set `OBS_CAPTURE_ENABLED` and `OBS_ALERTS_ENABLED`.

*Mitigation:* enable capture + alerting; verify the alert reaches a real handset.

*Verification:* test call retrievable with transcript within 60 s.

---

### FM-03 · Dead air
**L4 · I4 · Phase 1 + 3**
A provider hangs. Nothing is spoken. The caller assumes the line dropped and
hangs up. From the clinic's view this is indistinguishable from the phone not
being answered — the exact failure they hired you to fix.

*Evidence:* missing timeouts; `flow.py` complexity makes the failure paths hard
to enumerate.

*Mitigation:* aggressive in-call timeouts; filler on any wait over ~800 ms;
hard guarantee of no gap over 3 s. `filler_phrases.py`,
`tests/test_dead_air_safety_net.py` exist — verify under *induced* provider hangs.

---

### FM-04 · Missed clinical red flag
**L2 · I5 · Phase 3**
Caller describes cauda equina symptoms, chest pain, or post-trauma neurological
signs; the assistant books a routine appointment instead of escalating. Low
likelihood, unbounded impact — clinical, reputational, regulatory.

*Mitigation:* explicit red-flag screening with a conservative bias toward
escalation; adversarial test scenarios. `app/media_streams/clinical_screening.py`
(299 lines) and `tests/test_clinical_screening.py` (339 lines) exist on
`latency-eval` and not on `main` — read both and verify they cover the
*escalation* path, not just detection. Note the JV `clinic.json` mentions
emergency/999/A&E/urgent handling but not cauda equina by name; confirm the
red-flag vocabulary is adequate for physiotherapy specifically.

*Note:* this is the one failure mode where over-triggering is the correct error.

---

### FM-05 · No emergency stop
**L3 · I5 · Phase 5**
Something goes wrong at 09:00 on a Monday and there is no fast way to return a
clinic's calls to their own line. Every minute is missed patients, and the
clinic's confidence does not recover.

*Mitigation:* per-clinic bypass switch, operable from a phone by a
non-engineer, tested live.

---

### FM-23 · Ungated cancel / reschedule — CLOSED
**L3 · I5 · closed 2026-07-22**
`cancel_appointment` and `reschedule_appointment` were both exposed to the model
with **no consent gate** on either clinic. A model misfire could move or delete a
real patient's appointment with no caller instruction — destructive, silent, and
discovered by the patient rather than by us.

*Fix:* `reschedule_appointment` blocks unless `last_bot_prompt` contains the
enforced CTA ("move it for you") **and** `_book_reply_is_affirmative(messages)`.
`cancel_appointment` blocks unless the retention question ("cancel it
altogether") was asked **and** `_cancel_reply_consents(messages)`. The cancel
helper deliberately does **not** reuse `_book_reply_is_affirmative`: "cancel" is
in `_NO_PATTERNS`, which would block every genuine cancel. It allows only an
explicit "cancel" token and blocks a bare "yes" — the retention question is an
OR, so a bare yes is ambiguous and must never destroy an appointment.

*Verification:* `tests/regression/test_cancel_reschedule_gate.py` (163 lines).
Live on `latency-eval`, `jv-v1-onboarding`, `vitaledge-onboarding` — guard
functions verified byte-identical across all three.

*Bias:* hard against cancelling. A missed cancel re-asks; a wrong one deletes a
real appointment.

---

### FM-25 · Write-acknowledgement filler on a refusal — CLOSED
**L3 · I4 · closed 2026-07-22**
`confirm_write_filler` keyed only off the prior assistant CTA, never off whether
the caller agreed. A caller who answered "no" to "shall I book that in?" still
heard "Just locking that in now…" and hung up believing they had been booked
against their wishes. Observed on a live JV call.

*Fix:* `confirm_write_filler(session, caller_confirmed)` returns `None` unless
consent is real. Mirrors the FM-01 book gate — verify consent, not merely that
the CTA was asked.

*Verification:* `tests/regression/test_write_ack_filler_gate.py` (73 lines).
Live on all three branches; `app/filler_phrases.py` is byte-identical across them.

---

## Tier 2 — Close before the meeting if possible, before cohort one certainly

### FM-06 · Wrong-tenant data leakage
**L2 · I5 · Phase 6 (mitigated by isolation today)**
Clinic A's prices, hours or practitioners spoken to clinic B's caller. Currently
*structurally impossible* because each clinic is a separate deployment — the
per-deployment model is accidentally protecting you here.

**This risk arrives the moment runtime tenancy lands.** It must be designed with
tenant isolation tests from the first commit, not retrofitted.

---

### FM-07 · Unhandled caller behaviour in `handle_transcript`
**L4 · I3 · Phase 3**
A 15,734-line method has a state space nobody has enumerated. Unusual callers —
interrupting, correcting, changing their mind, two people talking — will find
paths that were never considered.

*Mitigation in this window:* adversarial suite to discover the common ones;
freeze policy to avoid creating new ones. *Real fix:* decomposition, post-meeting.

---

### FM-08 · Name / phone capture errors
**L4 · I4 · Phase 3**
STT mangles a name or a run-together mobile number; the booking is made against
the wrong contact details; the clinic cannot reach the patient.

*Mitigation:* readback confirmation (handlers exist:
`_handle_phone_readback_confirmation`, `_handle_readback_confirmation`);
`stt_variants` in `clinic.json`; `name_collector.py`,
`config/pronunciation_dict.json`. Test with genuinely hard names, not "John Smith".

---

### FM-09 · Half-configured boot
**L3 · I4 · Phase 5**
37 env vars. A deployment starts missing one and fails on the first call that
needs it — potentially days later, in a way that looks random.

*Mitigation:* fail-fast config validation at boot with a clear error naming the
missing variable.

---

### FM-10 · Cold start on first call
**L3 · I3 · Phase 6**
Render spins the service down; the first call of the morning — or of the demo —
eats the cold-start penalty. This is a classic demo-killer and a classic
"the receptionist was weird first thing Monday" complaint.

*Mitigation:* verify the service does not idle down; warm it before the demo;
consider a keep-alive ping.

---

### FM-11 · Latency regression from re-integration
**L4 · I3 · Phase 2**
Observability capture on the critical path adds tens of milliseconds per turn.
The entire reason this branch exists is latency.

*Mitigation:* capture strictly asynchronous; re-measure against the Phase 0
baseline; 50 ms regression budget, enforced.

---

## Tier 3 — Known, accepted, tracked

| ID | Failure | L·I | Disposition |
|---|---|---|---|
| FM-12 | LLM rate limiting under concurrent calls | L3·I4 | Runbook + retry/fallback model; load-test post-meeting |
| FM-13 | Provider (ElevenLabs/AssemblyAI) outage | L2·I5 | Runbook; degrade to message-taking; secondary provider is post-meeting work |
| FM-14 | Engine drift across the four deployed branches | L5·I2 | Accepted while per-branch deploys exist; resolved by runtime tenancy |
| FM-15 | SMS sent for a booking that did not complete | L3·I4 | Phase 4 — same booking-ID invariant as FM-01 |
| FM-16 | Deleted regression tests hid a live defect | L3·I3 | Phase 0 item 2 triage |
| FM-17 | Concurrent calls to one clinic exhaust a shared resource | L2·I4 | Untested. Load-test before cohort two |
| FM-18 | Timezone / BST boundary errors in slot offering | L2·I4 | Verify explicitly — UK clocks change 25 Oct |
| FM-19 | Caller data handling / GDPR posture | L3·I4 | Transcripts are health-adjacent personal data. `app/obs/redact.py` exists — read it before enabling `OBS_DIGEST_INCLUDE_TRANSCRIPTS`. Needs a written position before 100 clinics; a partner-scale client will ask |
| FM-20 | Wrong branch deployed | L3·I5 | Four Render services, `autoDeploy: true`, no branch pin in `render.yaml`. A push to the wrong branch changes what answers a real clinic's phone. Phase 5 deploy checklist |
| FM-21 | Screening double-ask (model + deterministic layer) | — | **Not a defect.** Diagnosed as test drift — see `TEST_BASELINE.md` |
| FM-22 | Screening state not cleared across turns | — | **Not a defect.** Diagnosed as test drift — see `TEST_BASELINE.md` |

**Numbering:** FM-24 is unused. FM-23 and FM-25 are Tier 1 (both CLOSED) — see
above. Before reusing a number, grep the whole repo: FM ids appear in commit
subjects and in `DELETED_TEST_TRIAGE.md` as well as here, and they have already
collided once (see the warning at the top of this file).

---

## How to use this register

1. It is a living document. Every defect found in Phase 3 gets an FM number.
2. No Tier 1 entry may remain open when a clinic goes live.
3. Each entry needs a *verification*, not just a *mitigation*. "We added a
   try/except" is not evidence. "We broke the credential and observed the
   correct behaviour on a live call" is.
