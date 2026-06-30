# Media Streams Migration Checklist

Migration from old webhook system (`app/routes/realtime.py`) to Media Streams
sentence-streaming pipeline (`app/media_streams/`).

All items verified complete as of 2026-03-24.

---

## System Prompt

- [x] Full system prompt transferred from old system (`app/prompts/susie_system_prompt.py`)
- [x] All banned phrases present and prominent (ABSOLUTE RULE block at top of `get_system_prompt` in `config.py`)
- [x] All filler phrases present (SILENCE_RULE, AVAILABILITY_FLOW_RULE, NAME_COLLECTION_RULE, NEW_OR_RETURNING_RULE, PHONE_READBACK_RULE, INFORMAL_SPEECH_RULE)
- [x] "Lovely" removed from all response paths — grep confirms "Lovely" only appears in LLM instruction strings (telling Claude NOT to say it), never in spoken responses
- [x] British English rules present (Section 6 of system prompt — physiotherapist, mobile, GP, half four, etc.)
- [x] Medical deflection rules present (Section 7 — condition questions deflected to physiotherapist)
- [x] Safety rules present (Section 7 — emergency redirect to 999/A&E)

---

## Clinic Configuration (`CLINIC_CONFIG` in `config.py`)

- [x] Theorem Health name correct everywhere — `"Theorem Health and Wellness"`
- [x] Alcester opening hours correct — Mon–Fri 08:30–21:00, closed weekends
- [x] Redditch opening hours correct — Mon/Tue/Fri 09:00–17:00, Wed/Thu 09:00–19:00, Sat 09:00–17:00, closed Sundays
- [x] All appointment types with correct prices:
  - Physiotherapy Assessment: 50 min, £85
  - Physiotherapy Follow-up: 40 min, £85
  - Remedial Rehabilitation: 50 min, £65
  - Prescribing Consultation: 20 min, £12.50
  - Acupuncture: 50 min, £85
  - Psychotherapy: 50 min, £85 (Alcester only)
  - Shockwave Therapy surcharge: £45
  - Class IV Laser surcharge: £45
  - Standalone shockwave or Class IV Laser: 30 min, £130
  - Package of 4 combined shockwave + Class IV Laser: £468
  - Wellness and Stress Relief Massage with In-light Therapy: 60 min, £85 (Alcester only)
- [x] Transfer number from `CLINIC_CONFIG["transfer_number"]` only (`+447870166861`) — never hardcoded elsewhere

---

## Greeting

- [x] Greeting is exactly the `BOOKING_OPEN` constant: `"Of course you can book an appointment — what brings you in today?"`
- [x] `_THEOREM_GREETING = BOOKING_OPEN` in `connection.py` — single constant, single source of truth
- [x] Greeting delivered once per call via `_inject_greeting()`, guarded by `greeting_delivered` session flag

---

## Booking Flow (`BOOKING_FLOW` in `flow.py`)

- [x] Booking flow has exactly 10 steps (0–9) — verified programmatically
- [x] LLM only called for steps 1 (COLLECT_DURATION), 5 (PRESENT_SLOTS), 9 (CONFIRM_BOOKING)
- [x] Step 0 (COLLECT_REASON): greeting already asked this — DETECT_INTENT always stores first utterance as reason and sets `flow_step=1` when intent=booking
- [x] Step 1 (COLLECT_DURATION): LLM generates one empathy sentence + "How long have you had that?"
- [x] Step 2 (CONFIRM_ASSESSMENT): "OK, that's noted. To get the best possible diagnosis initially I would recommend a physiotherapy assessment — does that sound OK?"
- [x] Step 3 (NEW_OR_RETURNING): "Have you been with us before?"
- [x] Step 4 (COLLECT_AVAILABILITY): "What days or times work best for you?"
- [x] Step 5 (PRESENT_SLOTS): LLM calls `check_availability`, presents up to 3 slots in exact format with ordinal dates and British time
- [x] Step 6 (COLLECT_NAME): "Could I take your full name please?"
- [x] Step 7 (CONFIRM_PHONE): "Just to confirm — shall I use the number you're calling from for the booking?" — skipped if no Twilio number detected
- [x] Step 8 (COLLECT_PHONE): "And the best number to reach you on?" — skipped if caller confirmed Twilio number
- [x] Step 9 (CONFIRM_BOOKING): LLM generates warm summary with name, appointment type, date/time, confirms text will follow

---

## Fast Path Patterns (`fast_path.py`)

- [x] Fast path patterns include all old patterns from `app/fast_path.py`
- [x] Northern English variants included (aye, nah, go on then, sound, sorted, reight, etc.)
- [x] "i have not" matches NEW before "i have" matches RETURNING — order enforced (new_patterns checked first in `_extract(new_or_returning)`)
- [x] All patterns case-insensitive and substring match
- [x] No pattern produces "Lovely" or any banned phrase as a response

---

## Transfer Conditions

- [x] Transfer only fires under exact conditions in `_should_allow_transfer()`:
  1. `transfer_requested_by_caller` — caller explicitly asks to speak to someone
  2. `medical_emergency_detected` — emergency mentioned
  3. `failed_understanding_count >= 3` — three consecutive failures
  4. `request_transfer` — `transfer_to_human` tool called
  5. `silence_transfer` — SilenceHandler exhausted all re-asks
- [x] Transfer number comes from `CLINIC_CONFIG["transfer_number"]` only

---

## Silence Handling (`SilenceHandler` in `connection.py`)

- [x] Timer starts after TTS finishes playing (via `_delayed_tts_finished` → `on_tts_finished`)
- [x] Window 1 (4s): "Sorry, I didn't quite catch that — [original question]"
- [x] Window 2 (4s): "Sorry about that — [original question]"
- [x] Window 3 (4s): Transfer phrase + trigger transfer
- [x] Re-ask uses original `last_question` only — never stores re-ask phrase as new `last_question`
- [x] `_NEVER_STORE_PHRASES` prevents error/re-ask phrases from overwriting `last_question`

---

## Bug Fixes Applied

- [x] **BUG 1** — No "Lovely [name]" acknowledgement: fast path `_try_full_name` returns immediately to flow; no LLM acknowledgement between name collection and next question
- [x] **BUG 2** — LLM gate: all LLM calls gated by `step["use_llm"] == True` in `FlowEngine.ask_current_question`; no rogue LLM calls outside the gate
- [x] **BUG 3** — Duplicate sentences: `deduplicate_sentences()` applied to every chunk in `_tts_loop` before synthesis; also chunk-level dedup guard
- [x] **BUG 4** — Correct greeting: `_THEOREM_GREETING = BOOKING_OPEN` — "Of course you can book an appointment — what brings you in today?"
- [x] **BUG 5** — Booking confirmation: CONFIRM_BOOKING (step 9) always fires after phone collection
- [x] **BUG 6** — Question guard: `question_asked_this_turn` reset at start of each `handle_transcript`, checked before every TTS call in `ask_current_question`
- [x] **BUG 7** — Turn lock: `_llm_busy` instance variable drops concurrent transcripts in `_llm_loop`

---

## Watchdog Phrases

- [x] Banned phrases removed from `WATCHDOG_PHRASES` — no "bear with me", "one moment please", or "just a moment"
- [x] Safe replacements: "Let me just check that for you...", "Checking availability now...", "I'll have that sorted in a second..."

---

## Twilio Routing

- [x] `/ms/incoming` route registered — returns TwiML `<Connect><Stream url="wss://..."/>`
- [x] `/ms/stream` WebSocket route registered
- [x] Old `/twilio/voice` route preserved as fallback (not removed)
- [x] `media_streams_router` always registered in `main.py` (kill switch operates at route level, not registration level)
- [x] `THEOREM_HEALTH_USES_MEDIA_STREAMS` env var defined in `config.py`
- [x] Kill switch: `MEDIA_STREAMS_ENABLED=false` redirects to `/twilio/voice` with zero dead air

---

## Session Fields

All of the following initialised in `DEFAULT_MS_SESSION` in `session.py`:

- [x] `flow_step`
- [x] `reason`
- [x] `duration`
- [x] `assessment_confirmed`
- [x] `new_or_returning`
- [x] `availability`
- [x] `slots_count`
- [x] `slots_offered`
- [x] `selected_slot`
- [x] `full_name`
- [x] `phone_number`
- [x] `phone_confirmed`
- [x] `booking_confirmed`
- [x] `last_question`
- [x] `turn_in_progress`
- [x] `question_asked_this_turn`

---

## Production Readiness Checks

1. [x] **Banned phrases audit** — no "Lovely" in any response string
2. [x] **LLM gate audit** — all LLM calls inside `use_llm == True` gate
3. [x] **Greeting audit** — no "Hi there" or "How can I help" in response strings
4. [x] **Flow step count** — `BOOKING_FLOW` has exactly 10 steps (0–9)
5. [x] **Duplicate protection** — `deduplicate_sentences` applied in `_tts_loop`
6. [x] **Turn lock** — `_llm_busy` set True at start, cleared in `finally` block
7. [x] **Silence handler** — `on_tts_finished` fired after audio plays via `_delayed_tts_finished`
8. [x] **Session fields** — all required fields initialised in `DEFAULT_MS_SESSION`
