# Susie — Production Shipment Synthesis

**Product:** Susie, AI phone receptionist — Theorem Health & Wellness (`theorem_v3`)
**Branch:** `investigate/susie-call-flows`  ·  **Ship target:** Monday 6 July 2026
**From:** Jules  ·  **To:** Quentin  ·  **Date:** 4 July 2026

---

## 1. Bottom line

**Susie is ship-ready for Monday.** Over this remediation effort we closed **12 fixes** from the
14-call production sign-off sweep — the entire actionable Susie-code backlog. Every zero-tolerance
safety gate passes. **11 of 12 fixes are phone-verified on staging**; the 12th is unit-verified and
its trigger path is now unreachable by design (explained below). Test baseline is clean: **0
regressions**.

One item (**F21**, long/un-bargeable TTS) is deliberately **deferred** — it is a re-design, not a bug
fix, and its impact is low (4/10). Details in §5.

**Three items are for your (infra) track, not Susie's logic** — see §6. One of them (Acuity isolation)
is the only real caveat before we take live bookings.

---

## 2. What shipped — the 12 fixes

All fixes followed the same discipline: root-cause diagnosis → failing test first (TDD) → smallest
correct change → full test run → phone-verify on staging.

### Safety spine (zero-tolerance) — all phone-verified
| # | Fix | What it does |
|---|-----|--------------|
| **F17** | Deterministic transfer line | The G18 "Putting you through now — please stay on the line" is now emitted deterministically at the transfer choke point, not left to LLM prose (which gate5 could strip). |
| **F20** | Booking requires a clear YES | `book_appointment` now checks an actual affirmative was given, not merely that the confirm question was asked — a weak/ambiguous "yes" can no longer book. |
| **F23** | Calm 999 re-anchor | After emergency (999/A&E) instructions, a dead-air re-ask no longer fires the chirpy "how can I help today?" that tonally undercut the emergency. |

### Conversation correctness — all phone-verified
| # | Fix | What it does |
|---|-----|--------------|
| **F13** | No booking-CTA on pure FAQ | "Would you like to book?" no longer appends to plain FAQ answers (prices, parking). Prompt-only (a deterministic gate was tried and reverted — it regressed the concern turn). |
| **F14** | Bank-holiday FAQ not clinic-gated | "Open on Easter Monday?" no longer triggers an inescapable "which clinic?" ladder — closures are identical at both sites. |
| **F24 / F26** | Deflection scoped + logistics answered | "That's one for the practitioner" is now scoped to clinical questions only; logistics questions (book online, phone/video) are answered properly. |
| **F25 (location)** | Single-location services | Wellness Massage & Psychotherapy are stated Awlstuh-only; a generic "a massage" request is disambiguated instead of mis-gated. |

### Clinic-resolver v2 (the call-killer family) — the core of this effort
The clinic-location question was the single biggest source of caller friction in the sweep — it
trapped the tester twice, to the point of abandoning calls. This was the make-or-break issue (we'd
rate the original problem 7–8/10). It is now resolved deterministically end-to-end:

| # | Fix | What it does | Verified |
|---|-----|--------------|----------|
| **Escape hatch** | Break the keypad loop | After the DTMF keypad rung, a further unrecognized answer breaks out to the LLM instead of looping the keypad forever. | Phone ✅ |
| **v2-1** | Indifference → default clinic | "whichever / either / you pick / I don't mind / both / doesn't matter" now resolve to Alcester (the primary Mon–Fri site) instead of re-asking. | Phone ✅ |
| **v2-2** | Full gate stand-down (no sticky re-ask) | The escape hatch now also clears the booking-intent latch, so the clinic question can't silently re-arm on the next turn (an outer loop). | Unit ✅ (see note) |
| **v2-3 (F16)** | Deictic "this clinic" | "this clinic please" / "the one I called" resolve directly to the dialled site instead of going biased-confirm → dead air → keypad (~30s friction removed). | Phone ✅ |

**F25 (naming, Group 6):** the wellness massage's price line in the prompt had drifted to "Wellness
Massage with In-light Therapy", dropping "and Stress Relief" and diverging from the canonical source
of truth. Reconciled to the canonical **"Wellness and Stress Relief Massage"** and locked with a
prompt-sync test. Phone ✅.

> **Note on v2-2:** It was not phone-triggered on the verification call — and that is the fixes
> *working*. v2-1 and v2-3 now resolve vague answers upstream, so the caller can no longer reach the
> point where the sticky re-ask occurred. v2-2 is a safety net for a path that is now hard to hit by
> design. It is covered by 6 unit tests and is a strict superset of the already-phone-verified escape
> hatch (it only adds one extra state-clear). We chose to report this honestly rather than force an
> artificial repro at 2 a.m.

---

## 3. Verification & quality baseline

- **Automated tests:** full suite runs at **90 failed / ~1095 passed / 0 regressions**.
  The 90 failures are **pre-existing** on this branch — they pre-date all of this work (verified by
  stash-compare) and are their own cleanup track (name-collector, silence-handler, sms-templates,
  soft-context). Every fix added its own passing tests on top; none moved the 90.
- **Phone verification:** each fix (except v2-2, per the note above) was verified on staging against
  the real Twilio → AssemblyAI → ElevenLabs → Claude pipeline, then re-confirmed together in a
  final 14-call sweep. The v2 resolver batch was verified on call `CA564aa12a…` (4 July, 02:01).
- **Safety:** all zero-tolerance gates held throughout — AI disclosure, no-diagnosis, emergency
  999/A&E + "not an emergency service", red-flag safety net (no booking / no false reassurance),
  age 7+ no-exceptions, transfer line, clinical boundaries.

---

## 4. Per-fix detail

Full per-fix records (symptom → root cause → change → test → phone result → commit) live in
`docs/fix_booklet.md`. The source-of-truth backlog is `docs/sweep_findings.md` (FINAL COMPILE). The
durable session-to-session continuity doc is `docs/claude_handoff.md`.

---

## 5. Deferred: F21 (long / un-bargeable TTS)

**Status: deferred to a focused post-ship session. This is the right call.**

- **What it is:** some responses run long (8–18s), and clinical turns are un-interruptible, which can
  feel robotic and cause an impatient caller to talk over Susie and be suppressed.
- **Impact: 4/10.** It is a real, frequent UX annoyance — but **no failed calls, no wrong bookings,
  no safety impact.** Nobody loses a booking over it. (Contrast the clinic loop, 7–8/10, where callers
  abandoned — that one we fixed.)
- **Why not fix it now:** both candidate fixes touch behaviour we *just signed off*, two days before
  ship. "Allow barge after sentence 1" edits the barge-in guard — the most timing-sensitive path in
  the pipeline. "Shorten responses" fights the clinical gates (empathy + physio-reassurance + booking
  offer are spec-mandated). **The risk of the fix currently exceeds the cost of the bug**, and it is
  genuinely re-design territory rather than bug-fixing.
- **Recommendation:** ship without it; take it first in a focused session afterward, with real
  phone-iteration on barge timing where a regression cannot threaten the ship.

---

## 6. Your track (infra / environment — not Susie logic)

These are outside the Susie codebase and are for you to pick up. One matters before live bookings:

| ID | Item | Priority |
|----|------|----------|
| **Acuity isolation** | Staging shares Mark's real Acuity — a `book_appointment` on staging creates a **real** appointment. We've been testing by stopping short of the final "yes". **This must be isolated (or bookings gated) before real traffic.** | **HIGH — the one real caveat** |
| **I5** | `GOOGLE_SERVICE_ACCOUNT_JSON` malformed on staging (Sheets/Calendar broken). | Medium |
| **I2** | `/twilio/status` callback points at the prod host and 403s on every call (cross-wiring). Harmless to calls but noisy. | Low |

(Prior infra already handled: Redis saturation resolved/isolated; TRANSFER_DISABLED kill-switch
shipped and verified so staging never dials Mark or SMSes him.)

---

## 7. Recommendation

**Ship Monday.** The safety spine is solid, the facts are canonical, and the clinic-resolver — the
issue that actually hurt call completion — is fixed and verified. F21 is polish for the week after.
The only pre-live gate that isn't a Susie concern is **Acuity isolation** (§6), which is yours.

*Questions or anything you want re-verified before Monday — say the word and we'll run another
staging call.*
