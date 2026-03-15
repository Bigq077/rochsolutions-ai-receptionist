# Media Streams Migration Checklist

All items verified present in the new system (`app/media_streams/`).

## System Prompt

- [x] Full system prompt transferred — `app/prompts/susie_system_prompt.py` (single source of truth, imported by `llm_stream.py`)
- [x] All banned phrases present — "Certainly!", "Absolutely!", "Great!", "I understand", "Go ahead", "Take your time", etc.
- [x] All approved filler phrases present — "Of course", "Not a problem", "Right, just bear with me", "Let me just check that"
- [x] British English rules present — physiotherapist, mobile, GP, half four, straight away
- [x] Both clinic configs with correct hours — `app/clinic_config.py` (Alcester Mon–Fri 08:30–21:00; Redditch Mon/Tue/Fri 09:00–17:00, Wed/Thu 09:00–19:00, Sat 09:00–17:00)
- [x] All appointment types present — Physio Assessment £75, Follow-up £75, Remedial Rehab £65, Prescribing £12.50, Acupuncture £75, Psychotherapy £75, Shockwave +£45, Laser +£45

## Conversation Flow Guards

- [x] Greeting fires exactly once — `_inject_greeting()` called from `_handle_start()`, which fires only on the Twilio `start` event
- [x] New/returning fires exactly once — session guard `session["collected"]["patient_type"]` checked in `_try_clinic_selection` and fast_path; LLM prompt has `{_nr_guard}` block
- [x] Full name as single field — `_try_full_name` asks "Could I take your full name please?" and stores `full_name` in one shot; never splits first/last
- [x] Phone collection two-part without dropping call — `_try_phone_first_five` → `_try_phone_last_six` with state guard (`COLLECT_PHONE_PART_ONE` → `COLLECT_PHONE_PART_TWO`)
- [x] Booking flow opening line correct — `_try_clinic_selection` asks "Right, just bear with me a moment... Which clinic would you like to visit — say one for our Alcester clinic or two for our Redditch one" (LLM uses "Of course I can help you with that. Which clinic…")
- [x] Injury question is optional — never blocks booking; Fast Track flow (Theorem) skips reason entirely

## Date and Slot Handling

- [x] Date reasoning implemented — `_build_date_prefix()` in `llm_stream.py` injects today, this Sunday, next Monday on every LLM call
- [x] Slot presentation wording correct — "I have found X available slots during that time frame. The first being [DATE TIME]..." enforced in system prompt
- [x] Current state injected into every LLM call — `state_ctx` block prepended to system prompt in `run_turn()` (line ~255 `llm_stream.py`)

## Error Prevention

- [x] Fast path all patterns carried over (broad) — all 7 handlers preserved: `_try_clinic_selection`, `_try_new_returning`, `_try_yes_no_confirmation`, `_try_full_name`, `_try_phone_first_five`, `_try_phone_last_six`, `_try_slot_selection`
- [x] State-aware fast path dispatch — `try_fast_path()` selects handlers based on `CallState`; wrong-state matches are impossible
- [x] Transfer guard implemented — `_should_allow_transfer()` is the single choke-point; transfer only fires on `transfer_requested_by_caller`, `medical_emergency_detected`, `failed_understanding_count >= 3`, or `request_transfer` (tool)
- [x] Dead air watchdog implemented — `_watchdog_loop()` fires rotating bridge phrases after `WATCHDOG_SILENCE_SEC` of silence while LLM is active
- [x] Last question re-ask implemented — `_silence_reask_loop()` re-asks after `QUESTION_SILENCE_SEC` of silence, max `MAX_REASK_ATTEMPTS` times
- [x] No double acknowledgements — fast-path interim phrases replace LLM openers; system prompt bans "Okay, no problem" + separate filler
- [x] State machine covers all flow steps — `CallState` enum: GREETING → CLINIC_SELECTION → NEW_OR_RETURNING → COLLECT_NAME → COLLECT_PHONE_PART_ONE → COLLECT_PHONE_PART_TWO → COLLECT_AVAILABILITY → PRESENT_SLOTS → CONFIRM_BOOKING → COMPLETE (+ TRANSFER)
- [x] State only ever moves forward — `advance_state()` enforces forward-only via `_STATE_ORDER` index comparison

## Single Source of Truth Verification

| Element | Location | Duplicated? |
|---------|----------|-------------|
| Susie system prompt | `app/prompts/susie_system_prompt.py` | No |
| Clinic config | `app/clinic_config.py` | No |
| Tool definitions | `app/tools/receptionist_tools.py` | No |
| Fast-path patterns | `app/media_streams/fast_path.py` | No |
| Call state machine | `app/media_streams/session.py` | No |
| Transfer guard | `app/media_streams/connection.py` (`_should_allow_transfer`) | No |
| Date injection | `app/media_streams/llm_stream.py` (`_build_date_prefix`) | No |
