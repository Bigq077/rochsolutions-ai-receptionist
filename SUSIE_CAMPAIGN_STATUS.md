# Susie Battle-Hardening — Status Update

**Prepared for:** leadership · **Date:** 2026-07-18 · **Owner:** Jules (engineering)
**Scope of this update:** Day 1 — Core Booking block (scenarios BK-1 … BK-10 of 11)

---

## Headline

**The core booking engine is solid, and the one safety-critical behaviour we most needed to
verify passed.** Across 10 test calls covering every service and booking modality, Susie routed
the caller to the **correct service 10/10 times**, and the **#1 rollout risk — Theorem/other-clinic
template leakage — did not occur on a single call.** The sweep also surfaced **two clear functional
defects** and a systemic turn-taking issue, all well-characterised with reproductions — which is
exactly what this campaign is for.

We are **not yet production-ready** (defects open, and 5 of 6 scenario days still to run), but Day 1
gives high confidence in the booking core and a clear, short fix list.

---

## Scorecard (10 calls)

| Dimension | Result |
|---|---|
| Correct service routing | **10 / 10** ✓ |
| Template leakage (Theorem/Alcester/Redditch/"which clinic?") — the #1 risk | **0 occurrences** ✓ |
| Safety: corticosteroid → never book, waitlist for practitioner | **PASS** ✓ |
| Same-day / new-vs-returning / video / home-visit / DTMF routing | all correct ✓ |
| Functional defects found | **2** (surname capture, home-visit address) |
| Systemic turn-taking issue | **1** (slot selection after long lists) |
| Items needing verification | **2** (service length, service catalogue) |

---

## What's working well (verified live)

- **Service routing** — new vs returning, in-clinic vs virtual vs home-visit, acupuncture, neuro,
  sports massage, outdoor rehab, same-day evening — all routed correctly.
- **Safety behaviour (corticosteroid)** — correctly said "launching soon", **never attempted a
  booking**, took the caller's name + number to the waitlist, and pinged the practitioner. This is
  the highest-stakes behaviour and it was clean.
- **No template leakage** — the multi-tenant hazard we most worried about for the 200-clinic
  template stayed dormant on every call.
- **Recovery behaviours** — split-name back-fill, DTMF keypad phone capture, barge-in re-prompting,
  and the dead-air safety net (never hangs up silently on a struggling caller) all worked.

## Defects found (the value of the sweep)

| ID | Sev | What | Status |
|---|---|---|---|
| **F-009** | P2 | Caller's **surname is silently dropped** when they append a filler word (e.g. "John Smith **please**" is captured as just "John"). Because Susie only reads the first name back, the caller can't catch it. **Reproduced reliably.** | Prime fix candidate |
| **F-010** | P2→P1 | A **home-visit booking never asks for the patient's address** — the visit can't be fulfilled. | Fix needed |
| **F-012** | P2 | **Slot-selection turn-taking is fragile**: after a long spoken slot list, the caller's reply is sometimes dropped and the system has to re-ask, occasionally twice. Drives up abandoned turns. | Fix / tune |
| F-008 | verify | For "the hour-long sports massage", we can't confirm the 60-min/£55 variant (vs 30-min/£40) was selected — needs a booking-completion check. | Verify |
| F-011 | verify | A neuro assessment was accepted as a *home visit*, but that combination may not be a real priced service. | Verify against config |

**Cross-cutting observation (needs a product decision):** on **every** booking call, Susie never
states the **price or duration** of the service — not when quoting, not at the final "shall I book?"
confirmation. This isn't a leak or an error, but it means a first-time caller confirms a booking
without being told the cost. **We'd like a ruling on whether Susie should quote price/duration on
booking, or only when asked.**

## Caveats / not yet covered

- **Latency numbers are not yet valid.** A dormant experimental flag (`WS_A`) was left enabled on the
  test environment, so this run produced **no clean latency baseline**. A one-line config change +
  redeploy fixes it; latency measurement resumes after that. (Voice-to-voice responsiveness *felt*
  in line with the known baseline, but we won't quote numbers until the flag is off.)
- **5 of 6 scenario days remain:** name/phone capture stress, slots + FAQ interrogation,
  reschedule/cancel, emergency & clinical-safety boundaries, and adversarial audio/turn-taking.

## Next steps

1. Fix **F-009** (surname drop) and **F-010** (home-visit address) — both well-characterised.
2. Disable the `WS_A` flag on the test environment and **re-bank a clean latency baseline.**
3. Resolve the two verify-items (F-008 service length, F-011 service catalogue).
4. Decide the **price-disclosure** question (product).
5. Continue the scenario matrix (Days 2–6), re-testing each fix as we go.

---

*Full per-call detail and the running defect tracker live in `SUSIE_CAMPAIGN_LOG.md`; redacted call
logs (PII-removed) are in `call_archive/`.*
