# app/phrases.py
"""
Central store for all user-facing spoken strings.

Import and use via:
    from app.phrases import P, CLINIC_NAME
    reply = P["ask_name"]
    reply = P["transfer_fallback"]
"""
import os

CLINIC_NAME: str = os.getenv("CLINIC_NAME", "the clinic")

# ---------------------------------------------------------------------------
# Master phrase dictionary — every key maps to a TTS-ready string.
# No "Could I take your...", no "Please hold".
# Short warm openers ("Of course", "Certainly", "Of course!") are encouraged
# at the start of FAQ and informational answers to avoid sounding robotic.
# ---------------------------------------------------------------------------

P: dict = {

    # ── Greetings & general ────────────────────────────────────────────────
    "how_can_i_help":           "How can I help?",
    "how_can_i_help_today":     "How can I help you today?",
    "okay_ack":                 "Okay.",
    "no_problem_help":          "No problem. How can I help today?",
    "no_problem_what_next":     "No problem — what would you like to do: book, reschedule, or ask a question?",
    "no_problem_instead":       "No problem. What would you like to do instead?",
    "ready_when_you_are":       "Of course. When you're ready, just let me know what you'd like to do.",
    "start_over":               "Brilliant — starting fresh. What would you like to do?",
    "lost_place_recovery":      (
        "Apologies for that — let me pick back up. "
        "I can help you book an appointment, reschedule, or answer any questions. "
        "What would you like to do?"
    ),

    # ── Mishear / retry ────────────────────────────────────────────────────
    "mishear_generic":          "Sorry about that — could you say that again?",
    "mishear_number":           "Sorry about that — could you say that number again?",
    "mishear_slot_choice":      "Sorry — please say 1, 2, or 3.",

    # ── Technical errors ───────────────────────────────────────────────────
    "error_generic":            "Something went wrong on my end — bear with me a moment.",
    "error_bear_with_me":       "Bear with me one moment — I'll get that sorted for you.",
    "error_try_again":          "Sorry about that — could you say that again and I'll get it sorted?",
    "error_pipeline_failure":   (
        "My apologies — I'm having a bit of a technical moment. "
        "Please call back and I'll be ready to help."
    ),
    "error_safe_fallback":      "Sorry, just a brief blip — could you give me a moment and try again?",
    "error_calendar":           (
        "The calendar's not cooperating right now — bear with me. "
        "Could you give me your name and number and I'll make sure the team calls you back?"
    ),
    "error_reschedule":         (
        "My apologies — I wasn't able to complete that reschedule. "
        "Please give us a call directly and the team will be happy to sort it."
    ),

    # ── Transfer / human ───────────────────────────────────────────────────
    "transfer_now":             "Of course — let me put you straight through to the team.",
    "transfer_hold":            "Of course — just bear with me a moment and I'll put you through.",
    "transfer_silence":         (
        "I'm having a little trouble hearing you — "
        "let me transfer you to someone who can help."
    ),
    "transfer_unavailable":     (
        "Looks like the team isn't available at the moment, but I'm here — "
        "did you want to book an appointment, or is there something else I can help with?"
    ),
    "transfer_twiml_say":       (
        "Bear with me — putting you through to the team now."
    ),

    # ── Location ───────────────────────────────────────────────────────────
    "ask_location":             "Are you looking to book at our Alcester or Redditch clinic?",
    "ask_location_full":        "Just to make sure I get you to the right team — Alcester or Redditch?",
    "location_complaint_bridge":(
        "Got it — we can certainly look into that for you. "
        "Just so I can direct you to the right team, are you calling about our Alcester or Redditch clinic?"
    ),

    # ── Patient type ───────────────────────────────────────────────────────
    "ask_patient_type":         "Are you a new patient, or have you been here before?",
    "patient_type_reprompt":    "Are you a new patient or have you been here before?",
    "patient_type_defaulted":   "No problem — I'll pop you down as a new patient. What's the appointment for?",

    # ── Reason / condition ─────────────────────────────────────────────────
    "ask_reason":               "What's the appointment for?",
    "ask_reason_full":          "And what's been bringing you in?",
    "ask_reason_reschedule":    "Of course. What's the problem you'd like to be seen for?",
    "book_reason_no_problem":   "No problem. What would you like to book instead? For example, you can say physiotherapy, sports massage, or rehabilitation.",

    # ── Time preference ────────────────────────────────────────────────────
    "ask_day":                  "What day or time would suit you?",
    "ask_day_reschedule":       "No problem. When would you like to come in?",

    # ── Name ───────────────────────────────────────────────────────────────
    "ask_name":                 "And who am I booking in today?",
    "ask_name_reschedule":      "What's your name so I can find your booking?",
    "ask_name_cancel":          "What's your name so I can find the appointment?",
    "ask_name_manual":          "What's your name and I'll log a request for the team?",
    "ask_name_manual_with_phone": (
        "Could you give me your name and phone number "
        "and I'll make sure the team calls you back?"
    ),
    "ask_name_resch_intent":    "What's your name so I can {action}?",  # format before use

    # ── Phone ──────────────────────────────────────────────────────────────
    "ask_phone":                "What's the best number to reach you on?",
    "ask_phone_resch":          "And what number was the booking made with?",
    "ask_phone_mobile":         "What's the best mobile number for us to reach you on?",

    # ── Slot picking ───────────────────────────────────────────────────────
    "ask_slot_choice":          "Please say 1 for the first option, 2 for the second, or 3 for the third.",
    "slot_choice_already_have": "You've chosen {label}. Shall I go ahead and book that for you? Say yes to confirm or no to cancel.",
    "slot_no_availability":     "I'm not seeing clear availability right now — not to worry. Could I take your name and I'll make sure the team gets back to you?",

    # ── Confirmation ───────────────────────────────────────────────────────
    "booking_confirmed":        "Brilliant — you're all booked in for {label}. We look forward to seeing you.",
    "booking_confirm_prompt":   "Great. Say yes to confirm, or no to cancel.",
    "booking_confirm_retry":    (
        "Something went wrong on my end — let me try that again. "
        "Say yes to confirm your booking for {label}, or no to cancel."
    ),
    "booking_cancelled":        "No problem — I've cancelled that. What would you like to do instead?",
    "booking_cancelled_outro":  "Your appointment has been cancelled.",

    # ── Manual capture ─────────────────────────────────────────────────────
    "manual_passed_on":         "Perfect — I've passed all of that on to the clinic team.",
    "manual_no_problem":        "No problem — is there anything else I can help with?",
    "no_calendar_access":       (
        "I don't have calendar access right now. "
        "What's your name and I'll log a booking request for the team?"
    ),

    # ── Insurance ──────────────────────────────────────────────────────────
    "ask_insurer":              "Which insurance company are you with? For example, AXA, Vitality, Aviva, or WPA.",
    "ask_insurer_retry":        "Sorry — which insurance company are you with? For example, AXA, Vitality, Aviva, or WPA.",
    "ins_bupa_yes":             "Brilliant. Not a problem at all. Shall we go ahead and get you booked in?",
    "ins_bupa_no":              (
        "Absolutely no pressure — take your time. "
        "If you'd like, I can take your name and number and have someone from the team call you back. "
        "Would that help?"
    ),

    # ── Reschedule / cancel ────────────────────────────────────────────────
    "resch_or_cancel":          "No problem — would you like to reschedule or cancel an appointment?",
    "cancel_or_rebook_prompt":  "Would you like to cancel the appointment, or would you prefer a new slot instead? Say cancel or new slot.",
    "resch_book_back":          "Great — is it for the same problem as before, or something new?",
    "resch_same_problem":       "Sure — is it for the same problem as before, or something different?",
    "resch_no_book_back":       "No problem — we'll leave it there for now.",
    "resch_error_legacy":       "Sorry — let me just take your phone number to find your booking.",
    "resch_error_state":        "Sorry — something went wrong with that reschedule. Let's start again — what can I help you with?",
    "error_state_reset":        "Sorry — something went wrong. Let's start again — what can I help you with?",

    # ── FAQ detour ─────────────────────────────────────────────────────────
    "faq_detour_dtmf":          "You're in the help section. Say continue to go back to booking.",
    "faq_detour_ask_another":   "You can ask another question, or say continue to go back to booking.",
    "faq_detour_physical":      (
        "That's not something I can pinpoint exactly from here, "
        "but a physiotherapy assessment is the right starting point — "
        "the physio will take a proper look and work out what's going on. "
        "Would you like to book in for that, or is there anything else I can help with?"
    ),

    # ── Missed slot request ────────────────────────────────────────────────
    "callback_request":         (
        "No problem — please say your name, number, "
        "and what you need help with, and the team will call you back."
    ),

    # ── No problem — switch ────────────────────────────────────────────────
    "switch_to_triage":         (
        "No problem — what would you like to do instead? "
        "I can help you book, reschedule, or answer a question."
    ),

    # ── Tier 2 spoken menu ─────────────────────────────────────────────────
    "tier2_menu":               (
        "Let me give you a quick menu. "
        "Say book to book an appointment. "
        "Say treatments for information about our services and prices. "
        "Or say transfer and I'll put you straight through to the team. "
        "What would you like?"
    ),

    # ── Transfer escalation ────────────────────────────────────────────────
    "transfer_escalation":      "Bear with me — putting you through to the team now.",

    # ── Fallback escalation ────────────────────────────────────────────────
    "fallback_transfer":        (
        "My apologies — I'm having a little trouble following. "
        "Let me put you straight through to the team."
    ),

    # ── resume_prompt_for_state strings ────────────────────────────────────
    "resume_book_reason":       (
        "Now — what would you like to book? "
        "For example, you could say lower back pain, sports injury, or physiotherapy."
    ),
    "resume_book_intake":       (
        "Just to follow up — how long has that been going on, "
        "and is it constant or does it come and go?"
    ),
    "resume_book_recommend":    "Shall I go ahead and book you in for that?",
    "resume_book_time_pref":    "So — what day or time would suit you?",
    "resume_book_pick_slot":    "Please say 1, 2, or 3 for your preferred slot.",
    "resume_book_name":         "Who am I booking in today?",
    "resume_book_phone":        "What's the best mobile number for us to reach you on?",
    "resume_book_confirm":      "Say yes to confirm the booking, or no to cancel.",
    "resume_book_patient_type": "Are you a new patient or have you been here before?",
    "resume_triage":            (
        "What would you like to do — book an appointment, "
        "reschedule, cancel, or ask a question?"
    ),

    # ── media_streams / voicecopy equivalents ─────────────────────────────
    "ms_ask_name":              "Who am I booking in today?",
    "ms_ask_name_returning":    "And your name — just so I can find your records?",
    "ms_ask_phone":             "What's the best number to reach you on?",
    "ms_technical_pause":       "Bear with me — just a moment.",
    "ms_pipeline_failure":      (
        "My apologies — I'm having a brief technical moment. "
        "Please call back and I'll be ready to help."
    ),
    "ms_safe_fallback":         "Sorry, just a brief blip — could you give me a moment and try again?",
    "ms_error_reschedule":      (
        "My apologies — I wasn't able to complete that reschedule. "
        "Please give us a call directly and the team will be happy to sort it."
    ),
}

# ---------------------------------------------------------------------------
# Silence responses — keyed by a short state category, not the raw state name.
# The silence_handler module maps raw state names → these keys.
# ---------------------------------------------------------------------------

SILENCE_RESPONSES: dict = {
    # Phone capture: structured prompts mirror the name-state fast-recovery style.
    # connection.py _run() W1 overrides these with DTMF-vs-speech variants.
    "ask_phone":     "Sorry, I didn't quite catch that — please say the phone number slowly.",
    "confirm_phone": "Sorry, I didn't quite catch that — please say: use this number — or: do not use this number.",
    # NC-managed name collection (COLLECT_NAME*): W1 overrides this in
    # connection.py with a substate-aware scaffold prompt (first-name vs surname).
    # This entry is only the W2 fallback (fires ~20 s after W1).
    "collect_name_nc":    "Sorry, I didn't quite catch that — could you say your name again?",
    "ask_name":           "Take your time.",
    # BUG 1 fix: state-aware name sub-categories
    "ask_first_name":     "Sorry, I missed that. Could you tell me your first name again?",
    "ask_surname":        "Sorry, I missed that. And your family name?",
    "ask_day":            "Have a think — no hurry at all.",
    "ask_time":           "No rush on that.",
    "ask_reason":         "Take your time telling me.",
    # BUG 1 fix: GREETING gets a booking-intent re-ask; ASK_LOCATION gets a location re-ask
    "greeting":           "Sorry, I didn't quite catch that. Are you calling to book, reschedule, or cancel an appointment?",
    "ask_location":       "Sorry, I didn't quite catch that — could you say the Alcester clinic or the Redditch clinic?",
    "confirm_read_back":  "Just let me know if any of those details need changing.",
    # Long list / long explanation states — encourage patient thinking
    "present_days":       "No rush — take your time picking a day.",
    "present_times":      "No rush — take your time picking a time.",

    "confirm_assessment": "Of course — was there anything else you'd like to ask?",
    "faq_offer":          "Is there anything else I can help with?",
    "default":            "Sorry about that — could you say that again for me?",
    "default_retry":      "Sorry about that — could you say that again?",
}

# Per-state silence window in seconds (Window 1 — first re-ask threshold).
# Keyed by the same short category used in SILENCE_RESPONSES.
# 26 s is intentionally higher than the automated test runner's TURN_WAIT_SECONDS
# (25 s) so the silence timer never fires between test injections.
# The SILENCE_WINDOW_1_SEC env var overrides this if set.
#
# State families:
#   fast  (<25 s) — binary or very short answers: location choice, confirm yes/no,
#                   greeting intent
#   medium (26 s) — identity / phone / name collection
#   slow  (28-30 s) — date/time picking; caller may consult calendar
#   extra_slow (32-34 s) — long option lists (PRESENT_DAYS/TIMES), symptom
#                           descriptions, post-explanation confirmations
SILENCE_THRESHOLDS: dict = {
    # Phone capture: fast 3 s auto-recovery mirrors name-state behaviour.
    # A missed phone answer auto-prompts with a structured hint instead of
    # waiting 22–26 s of dead silence.
    "ask_phone":          3.0,
    "confirm_phone":      3.0,
    "ask_name":           26.0,
    # NC-managed name collection (COLLECT_NAME*): 5 s gives callers enough
    # time to collect their thoughts without long dead air.  Below 4 s the
    # watchdog fires mid-sentence if the caller pauses briefly before their
    # name.  W1 still uses the substate-aware scaffold prompt via connection.py.
    "collect_name_nc":    5.0,
    "ask_day":            30.0,   # caller may check calendar / diary
    "ask_time":           28.0,
    "ask_reason":         32.0,   # describing symptoms takes time
    "greeting":           22.0,   # simple booking-intent question
    "ask_location":       19.0,   # binary choice (Alcester / Redditch)
    "confirm_read_back":  24.0,   # yes/no after hearing readback
    "present_days":       34.0,   # caller just heard a list of 4+ days
    "present_times":      34.0,   # caller just heard a list of time slots
    "confirm_assessment": 32.0,   # follows a long explanation
    "faq_offer":          30.0,   # GENERAL_BOOKING_OFFER waiting for another question
    "default":            26.0,
}

# ---------------------------------------------------------------------------
# Retry phrases — used when extraction fails on a slot question.
# Keyed by retry ordinal (first_retry / second_retry) then by state category.
# ---------------------------------------------------------------------------

RETRY_PHRASES: dict = {
    "first_retry": {
        "ask_name":   "Sorry, I didn't quite catch that — could you say your name again?",
        "ask_phone":  "Sorry — could you say that number again for me?",
        "ask_day":    "No problem — which of those days suits you?",
        "ask_reason": "Sorry, I missed that — what's been bringing you in?",
        "ask_time":   "Sorry about that — morning or afternoon, roughly?",
        "default":    "Sorry, I didn't quite catch that — could you say it again?",
    },
    "second_retry": {
        "default": "Not to worry — could you say that once more for me?",
    },
}
