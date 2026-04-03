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
    # Greeting / triage
    "TRIAGE":               "greeting",
    "ASK_LOCATION":         "greeting",
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
