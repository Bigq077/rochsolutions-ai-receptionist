# app/silence_handler.py
"""
Context-aware silence response selection for Susie.

Provides:
  - get_silence_response(state, consecutive_count, clinic_name) -> str
  - get_silence_threshold(state) -> float
  - log_silence_event(...)

Maps raw session state names to SILENCE_RESPONSES keys, formats
{clinic_name}, appends callback suffix on second consecutive silence.
Never raises — callers are always returned a usable string.
"""
from __future__ import annotations

import datetime
import logging
import os

from app.phrases import SILENCE_RESPONSES, SILENCE_THRESHOLDS

logger = logging.getLogger(__name__)

# Suffix appended on second consecutive silence on the same state.
_CALLBACK_SUFFIX = " — if you'd like to call back when you're ready, we're here."

# Maps raw session state names → SILENCE_RESPONSES lookup key.
# States not listed here fall through to "default".
_STATE_TO_KEY: dict[str, str] = {
    # Media-streams flow states — phone capture: 3 s fast-recovery threshold.
    "COLLECT_PHONE":            "ask_phone",
    "COLLECT_PHONE_RETURNING":  "ask_phone",
    "COLLECT_PHONE_RESCHEDULE": "ask_phone",
    "CONFIRM_PHONE":            "confirm_phone",
    "CONFIRM_PHONE_RETURNING":  "confirm_phone",
    # NC-managed name collection: 3 s fast-recovery threshold so a missed
    # short name auto-prompts with the scaffold phrase, not 26 s of silence.
    "COLLECT_NAME":            "collect_name_nc",
    "COLLECT_NAME_RETURNING":  "collect_name_nc",
    "COLLECT_NAME_RESCHEDULE": "collect_name_nc",
    "COLLECT_NAME_CANCEL":     "collect_name_nc",
    # Day / time slot presentation — extra patient (long option lists)
    "PRESENT_DAYS":              "present_days",
    "PRESENT_DAYS_RESCHEDULE":   "present_days",
    "PRESENT_TIMES":             "present_times",
    "PRESENT_TIMES_RESCHEDULE":  "present_times",
    # Reason collection — caller may need time to articulate symptoms
    "COLLECT_REASON":            "ask_reason",
    # Post-explanation confirmations — follow long TTS utterances
    "CONFIRM_ASSESSMENT":        "confirm_assessment",
    "FAQ_BOOKING_OFFER":         "confirm_assessment",
    "GENERAL_BOOKING_OFFER":     "faq_offer",
    # Booking confirmations
    "CONFIRM_BOOKING":           "confirm_read_back",
    "LOOKUP_RESCHEDULE":         "confirm_read_back",
    "LOOKUP_CANCEL":             "confirm_read_back",
    # Legacy flow states
    # Booking — phone
    "BOOK_PHONE":           "ask_phone",
    "RESCH_PHONE":          "ask_phone",
    "RESCH_PHONE_FALLBACK": "ask_phone",
    # Booking — name
    "BOOK_NAME":            "ask_name",
    "RESCH_NAME":           "ask_name",
    # Booking — time / day
    "BOOK_TIME_PREF":       "ask_day",
    "RESCH_NEW_PREF":       "ask_day",
    "RESCH_ORIGINAL":       "ask_day",
    # Booking — reason
    "BOOK_REASON":          "ask_reason",
    "BOOK_INTAKE":          "ask_reason",
    # BUG 1 fix: GREETING → booking-intent re-ask; ASK_LOCATION → location re-ask
    "TRIAGE":               "greeting",
    "GREETING":             "greeting",
    "DETECT_INTENT":        "greeting",
    "ASK_LOCATION":         "ask_location",
    # Confirmation
    "BOOK_CONFIRM":         "confirm_read_back",
    "RESCH_CONFIRM":        "confirm_read_back",
}


def _state_to_key(state: str) -> str:
    """Map a raw session state name to a SILENCE_RESPONSES key."""
    return _STATE_TO_KEY.get(state, "default")


def get_silence_response(
    state: str,
    consecutive_count: int = 0,
    clinic_name: str | None = None,
) -> str:
    """
    Return the TTS-ready silence response string for the given state.

    On second consecutive silence on the same state (consecutive_count >= 1)
    the callback suffix is appended and the message is slightly more urgent.
    Never raises — always returns a usable string.
    """
    if clinic_name is None:
        clinic_name = os.getenv("CLINIC_NAME", "the clinic")
    try:
        key = _state_to_key(state)
        template = SILENCE_RESPONSES.get(key) or SILENCE_RESPONSES["default"]
        response = template.format(clinic_name=clinic_name)
        if consecutive_count >= 1:
            if key == "default":
                return SILENCE_RESPONSES.get("default_retry", "Sorry about that — could you say that again?")
            response += _CALLBACK_SUFFIX
        return response
    except Exception:
        return SILENCE_RESPONSES.get("default", "I didn't quite catch that — could you say that again?")


def get_silence_threshold(state: str) -> float:
    """
    Return the silence window duration (seconds) for the given state.
    Falls back to SILENCE_THRESHOLDS["default"] for unmapped states.
    Never raises.
    """
    try:
        key = _state_to_key(state)
        return float(SILENCE_THRESHOLDS.get(key, SILENCE_THRESHOLDS.get("default", 4.0)))
    except Exception:
        return 4.0


def log_silence_event(
    state: str,
    silence_duration: float,
    response_spoken: str,
    consecutive_silence_count: int,
) -> None:
    """Structured log for every silence event."""
    logger.info(
        "SILENCE_EVENT timestamp=%s state=%s silence_duration=%.1fs "
        "response_spoken=%r consecutive_silence_count=%d",
        datetime.datetime.utcnow().isoformat(),
        state,
        silence_duration,
        response_spoken,
        consecutive_silence_count,
    )
