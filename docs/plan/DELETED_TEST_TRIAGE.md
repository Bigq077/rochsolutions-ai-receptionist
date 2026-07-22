# Deleted Test Triage — Phase 0 item 2

**Filled 2026-07-21.** Base = `origin/latency-eval@022f816` (deployment truth).
Method: read every deleted body from `origin/main`, restored all 20 into the base
working tree, ran them, diagnosed each failure (defect vs test-drift), then reverted
the tree to clean. Nothing was left restored — see *Output* for why.

> ## ⚠️ HEADLINE: yes, a deletion hid a live defect — **FM-01, Tier-1.**
> `test_book_affirmative_gate` guarded the invariant *"never fire `book_appointment`
> unless the caller gave a clear yes."* On `latency-eval` that deterministic check is
> **gone**: the book gate (`app/media_streams/llm_stream.py:1822`) verifies only that
> the confirmation *question was asked*, then trusts the model to have waited for a
> yes. A negative/ambiguous reply — or the barge-in-"yes" case the code's own comment
> describes — can still book. This outranks the rest of Phase 0. **Not fixed, per
> instruction.**

20 files were deleted `origin/main` → `origin/latency-eval` (`git diff --diff-filter=D
--name-only origin/main..origin/latency-eval -- tests/`). **None restores green** —
the engine diverged ~150 commits, so the guards either target renamed/removed
internals or expose behaviour changes. The value here is the triage, not a restore.

---

## Classification

- **(a) Clinic-specific, correctly removed** — asserts behaviour only meaningful for
  a clinic this base does not serve (Theorem `theorem_v2/v3`, Alcester/Redditch).
- **(b) Generic guard, wrongly removed** — protects engine behaviour that applies to
  every clinic. Its failure-on-restore is a defect (→ register).
- **(c) Superseded** — the behaviour still exists but was reworked; the test targets
  removed internals. Replacement named.

| Test file | Lines | Class | Reasoning (symbol status · run result) | Disposition |
|---|---|---|---|---|
| `test_book_affirmative_gate.py` | 88 | **(b)** | `_execute_tools` no longer takes `last_user_text` → 4/4 TypeError. Book gate checks question-asked, **not** caller-yes. | **LIVE GAP · FM-01 (Tier-1).** Close in Phase 1; rewrite guard vs current API. |
| `test_emergency_reask_suppression.py` | 52 | **(b)** | `WebSocketCallHandler._emergency_reask_override` gone → 4/4 AttributeError. Emergency *detection* is now deterministic & better (`clinical_screening.detect_emergency`), but post-emergency chirpy-reask suppression has no replacement. | **VERIFY · FM-04.** Confirm `SilenceHandler` doesn't chirp "how can I help?" after a 999/A&E response. |
| `test_surname_straggler_suppression.py` | 89 | **(b)** | `_v3_try_persist_name` **exists**; 3/4 pass but the core case (stray "Rock" backfills silently, turn dropped) **fails**. | **VERIFY · FM-08.** Likely intended by the name-flow rework; confirm no junk turn / mis-capture. |
| `test_transfer_disabled_gate.py` | 108 | **(b)** | 2/4 pass (not-disabled). Disabled: Twilio Client still constructed (skips only on call-status) and the "transferring a patient" **SMS still fires**. | **VERIFY · FM-03/05.** Either the test's disable mechanism is stale or the disabled-gate regressed. |
| `test_transfer_line_spoken.py` | 70 | (c) | Blocked→speaks-nothing **passes** (the safety direction holds). Authorised→G18 line **not** queued. | Superseded/drift — G18 line reworded/relocated. Safe direction intact; low risk. |
| `test_greeting_disclosure_guard.py` | 196 | (c) | `_BARGE_PROTECTED_MARKER` gone from `connection.py`; "disclosure" now lives in `app/prompts/susie_system_prompt.py`. ImportError. | **VERIFY (regulatory).** Confirm the AI-disclosure line is still guaranteed spoken and un-bargeable. |
| `test_llm_stream_turns.py` | 37 | (c) | `_append_history` exists; 2/3 pass, "records both sides in order" **fails** (turn structure changed). | Superseded/drift. Verify obs/transcript records caller+assistant in order. |
| `regression/test_booking_phone_step.py` | 56 | (c) | `_booking_phone_q` renamed; the "use this number vs keypad" behaviour lives in `connection.py`. 3/3 fail (symbol gone). | Superseded — rewrite vs the renamed method. |
| `test_faq_clinic_gate.py` | 55 | (c) | `_faq_needs_clinic` gone, no replacement — part of the removed multi-site FAQ-gating. ImportError. | Superseded by clinic-model rework (see multi-site VERIFY). |
| `test_location_deictic_clinic.py` | 74 | (c)/(a) | `_is_deictic_current_clinic` gone. Alcester/Redditch two-site ladder. ImportError. | Removed with the Theorem multi-site ladder. |
| `test_location_gate_sticky_reask.py` | 84 | (c)/(a) | `_disengage_location_gate`/`_location_gate_should_fire` gone. ImportError. | Same subsystem. |
| `test_location_indifference.py` | 94 | (c)/(a) | `_DEFAULT_CLINIC` gone (asserted `== "alcester"`). ImportError. | Same subsystem. |
| `test_location_ladder_escape.py` | 40 | (c)/(a) | `_location_ladder_exhausted` gone. ImportError. | Same subsystem. |
| `test_surname_ask_continuation.py` | 61 | (c) | `_surname_ask_continuation`/`_rewrite_surname_ask_reply` gone — name flow reworked (name-first → end-of-flow). ImportError. | Superseded by current name flow. |
| `test_surname_first_recovery.py` | 130 | (c) | `_v3_surname_only` gone (theorem_v3 name-first design). ImportError. | Superseded by current name flow. |
| `test_caller_concerns.py` | 191 | (a) | `app/clinics/theorem/caller_concerns.py` absent on base. ImportError. | Correctly removed (Theorem). |
| `test_theorem_canonical.py` | 267 | (a) | `app/clinics/theorem/canonical.py` absent on base. ImportError. | Correctly removed (Theorem). |
| `test_logistics_faqs.py` | 50 | (a) | Asserts Theorem FAQ facts (`theoremhealth.co.uk`). 3/3 **pass** (theorem config persists) but clinic-specific. | Correctly removed from engine baseline; harmless if Theorem stays. |
| `test_clinic_config_mapping.py` | 66 | (a) | `theorem_v2/v3` + a specific purchased staging number. 4/5 pass (generic resolver works), 1 fail (staging-number migration). | Correctly removed (Theorem/deployment-specific). |
| `auto/scenarios/regressions/__init__.py` | 5 | (a) | Package marker (docstring), not a test. | Structural — no coverage lost. |

---

## Ones I looked at hardest (the four the template flagged)

- **`test_book_affirmative_gate` (FM-01)** — the real find. `main` carried the F20 fix
  (thread `last_user_text`, allow booking only on `is_yes and not is_no`). On
  `latency-eval` `_execute_tools` has surname- and phone-step gates and a
  *confirmation-question-asked* gate, but **no deterministic caller-yes check**. Code:
  `llm_stream.py:1822-1858` blocks only when `last_bot_prompt` lacks "shall i go
  ahead"/"book that in"; the comment claims "AND received an affirmative response"
  but the code delegates that to the model. **Unguarded Tier-1 invariant.**
- **`test_emergency_reask_suppression` (FM-04)** — checked first, as instructed. Good
  news: emergency *interception* is now deterministic and model-independent via
  `clinical_screening.py` (an improvement over main's model-generated path). Gap: the
  narrow "don't chirp a generic re-ask right after an emergency" guard is gone —
  verify the `SilenceHandler` path.
- **`test_transfer_disabled_gate` / `test_transfer_line_spoken`** — the *safety*
  directions hold (blocked transfer stays silent; not-disabled dial/SMS work). But a
  disabled transfer still emits the operator SMS in the test, and the authorised
  spoken line changed. Escape-hatch behaviour is partly untested — VERIFY in Phase 3.
- **`regression/test_booking_phone_step`** — deleted regression file, but benign: the
  behaviour (offer "use this number" vs keypad) survived under a renamed symbol.
  Superseded, not lost.

---

## Output

- **Classified:** (a) 5 · (b) 4 · (c) 11  =  20.
- **Files restored (kept):** **0.** None runs green on the base — (a) are clinic-specific,
  (b) fail via API drift while exposing the gaps below, (c) target removed internals.
  Leaving any would redden the Phase-0 baseline. Each is one `git checkout
  origin/main -- <path>` away if wanted for reference.
- **New defects / verify items found by restoration:**
  - **FM-01 (Tier-1, CONFIRMED):** `book_appointment` fires without a deterministic
    caller-yes check. `llm_stream.py:1822`.
  - **FM-04 (verify):** post-emergency generic re-ask no longer suppressed.
  - **FM-08 (verify):** silent surname-straggler backfill regressed (core case).
  - **Transfer (verify, FM-03/05):** disabled-transfer SMS/dial not fully gated.
  - **Disclosure (verify, regulatory):** AI-disclosure barge-protection mechanism moved
    to the prompt — confirm it still holds.
  - **Multi-site (verify):** the Alcester/Redditch location-ladder + FAQ-gating were
    removed wholesale. Correct **iff** no first-cohort clinic has multiple sites. If any
    does, this is an absent generic feature, not just an absent test.
- **Verdict — did any deletion hide a live defect? → YES (FM-01).**

This is not a "restore the tests" job; the engine moved too far. The real Phase-1 work
it surfaces: **close the FM-01 book-affirmative gap and re-express these guards against
the current API.** Bring the FM-01 finding to Ismael before continuing.
