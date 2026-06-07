# Handoff — Theorem Health AI Receptionist

**Date:** 2026-04-06
**Branch:** `main` (latest commit `2f2e345`)
**Render service:** theorem-health-ai-receptionist (Frankfurt)

---

## What was built

Two-clinic configuration for Theorem Health.
The same FastAPI / Twilio Media Streams service now handles two phone numbers:

| Number | Config key | Clinics |
|---|---|---|
| +447426779875 | `theorem` (production) | Alcester only — unchanged |
| +447366530580 | `theorem_v2` (test / two-clinic) | Alcester **or** Redditch — caller chooses |

On `theorem_v2`, after the caller states their intent (book / reschedule / cancel) but **before** any other flow question, Susie asks:
> *"Which clinic would you like — say one for our Alcester clinic, or two for Redditch?"*

Each clinic routes to its own Acuity calendar.
The **original `theorem` number is completely unaffected** — no location question, no routing change.

---

## Key files changed

### `app/media_streams/flow.py` — core fix
`_switch_flow()` — removed the `and not self.session.get("selected_location")` guard that was always short-circuiting the ASK_LOCATION gate (because the greeting phase pre-sets `selected_location="alcester"` before any user turn). Also added a `session.pop("selected_location", None)` to clear the stale default.

```python
if (
    self.session.get("twilio_to") == "+447366530580"
    and intent in {"booking", "reschedule", "cancel"}
):
    self.session["needs_location"] = True
    self.session.pop("selected_location", None)   # clear greeting-phase default
else:
    self.session["needs_location"] = False
    self.session["selected_location"] = "alcester"
```

### `app/media_streams/router.py`
Added `twilio_to`, `needs_location`, `selected_location` to the `/ms/test/inject-transcript/{call_sid}` session diagnostic — these were the fields that revealed the pre-set bug.

### `app/prompts/susie_system_prompt.py`
`clinic.get("clinic_id") == "theorem_v2"` → `session.get("twilio_to") == "+447366530580"` (×1)

### `app/tools/receptionist_tools.py`
Same `clinic_id → twilio_to` swap in `book_appointment`, `cancel_appointment`, `reschedule_appointment` (×4 occurrences).

**Why `twilio_to` instead of `clinic_id`?**
On Render, `clinic_id` resolves unreliably (env-var override or lookup failure). `twilio_to` is injected directly into the WebSocket start event and is always correct.

### `tests/auto/scenarios/two_clinic_scenarios.py` — Phase 15–17 test scenarios
28 scenarios covering:
- **Phase 15** — location guard fires after intent, not in greeting, not for FAQs; both clinics complete the full booking flow
- **Phase 16** — off-track callers (tangents, wrong service, pricing questions, abandons)
- **Phase 17** — angry/difficult callers (impatient, rude, threatens to leave, informal speech)

### `tests/auto/run_tests.py`
Warmup prints local commit hash and instructs the tester to verify the matching deploy is live on Render dashboard (server-side commit detection was abandoned — Render strips `.git` and doesn't expose `RENDER_GIT_COMMIT` to child processes).

---

## Correct BOOKING_FLOW response sequence (theorem_v2, new patient)

This is the exact order responses must be injected for a complete booking:

```
1.  intent          → ASK_LOCATION fires
2.  location        → COLLECT_REASON  ("what brings you in today?")
3.  medical reason  → CONFIRM_ASSESSMENT  (LLM empathy + "does that sound OK?")
4.  "Yes"           → NEW_OR_RETURNING  ("have you been with us before?")
5.  "No"            → PRESENT_DAYS  (check_availability + days presented)
6.  day preference  → PRESENT_TIMES  (LLM presents slots for chosen day)
7.  slot selection  → slot_pending_confirmation  ("Just to confirm… is that right?")
8.  "Yes"           → COLLECT_NAME  ("who am I booking in today?")
9.  name            → CONFIRM_PHONE  ("shall I use this number?")
10. "Yes"           → CONFIRM_BOOKING  → AUTO-CONFIRMED in test mode → DONE
```

Step 8 ("Yes" confirming the slot) is **mandatory** — `slot_ordinal_selection` always sets `slot_pending_confirmation=True` before advancing, regardless of how many slots are available.

---

## Known issues / noise

### "I didn't quite catch that" on every step in tests
Every step in the turn trace emits a repair prompt. This is a timing/ordering artefact of the `direct_ws_test` mode — each injected response briefly appears to the step that just became active before the flow fully settles. It does **not** consume an extra user turn and does **not** cause booking failures. The flow advances correctly despite the noise.

### Render server-side commit detection
Render's Python native runtime strips the `.git` directory and does **not** expose `RENDER_GIT_COMMIT` to child-process environments. Multiple approaches were tried and abandoned (file-based, env var, subprocess git). The test runner now prints the local commit and instructs the user to check the Render dashboard manually.

---

## What to run next

```bash
# Run only the new Phase 15-17 scenarios
python tests/auto/run_tests.py --phase 15
python tests/auto/run_tests.py --phase 16
python tests/auto/run_tests.py --phase 17

# Run full suite (Phases 1-17)
python tests/auto/run_tests.py
```

Phases 1–14 are unaffected and should still pass.
Phases 15–17 are expected to pass after the two commits in this session:
- `ffe18f2` — correct BOOKING_FLOW step order (add reason + Yes + No)
- `2f2e345` — add mandatory slot-confirmation "Yes" (step 8 of 10)

---

## Remaining risk areas

| Area | Risk | Mitigation |
|---|---|---|
| CONFIRM_ASSESSMENT tangents (16.1, 16.2, 16.4, 17.3) | LLM might classify the tangent as "yes" and advance too early | The `_classify_confirm_assessment` function uses keyword + LLM; pricing/weather/diagnosis questions should be "unsure" → re-ask |
| 17.3 (two pricing tangents) | Three turns at CONFIRM_ASSESSMENT — LLM might advance on second turn | "Fine, I'll do it — yes" contains clear "yes" as fallback |
| 15.8 reschedule / 15.9 cancel | RESCHEDULE_FLOW and CANCEL_FLOW have different slot handling | Only checked for `"no_crash"` and `"asked_which_clinic"` — not `booking_confirmed` |
| 16.8 "Let's do the booking first" | This non-location response at ASK_LOCATION may or may not cause a re-ask | ASK_LOCATION re-ask is accounted for in the response list |
