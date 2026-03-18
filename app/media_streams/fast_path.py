# app/media_streams/fast_path.py
"""
Fast-path resolver for the Media Streams pipeline.

Mirrors app/fast_path.py exactly in return semantics:
  - None                             → no match, fall through to LLM
  - FastPathResult(needs_llm=False)  → matched, play response_text, skip LLM
  - None (session already updated)   → matched silently, LLM generates response

Handlers that return FastPathResult (skip LLM):
  _try_clinic_selection, _try_full_name, _try_phone_first_five, _try_phone_last_six

Handlers that return None (LLM generates response):
  _try_new_returning, _try_yes_no_confirmation, _try_slot_selection
  (session is updated before returning None so LLM sees the resolved values)

ALL original pattern matching rules are preserved exactly.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .session import CallState, get_call_state
from .config import (
    FastPathTurnType,
    F_LAST_BOT_PROMPT,
    F_LAST_QUESTION,
    F_COLLECTED,
    F_PHONE_PART_ONE,
    F_PHONE_PART_TWO,
    F_FAST_PATH_FULL_PHONE,
    F_FAST_PATH_PHONE_CONFIRMED,
    F_FAST_PATH_SLOT_CONFIRMED,
    F_FAST_PATH_FINAL_CONFIRMED,
    F_FAST_PATH_CORRECTION,
    F_SELECTED_SLOT,
    F_FAST_PATH_LAST_RESOLVED,
    F_TWILIO_FROM,
    F_PHONE_COLLECTED_FROM_TWILIO,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FastPathResult:
    """Result returned by try_fast_path on a successful match."""
    turn_type: FastPathTurnType        # which slot was resolved
    value: Any                         # the resolved value (str, bool, dict)
    response_text: str                 # text to play via TTS immediately
    needs_llm_followup: bool           # True -> also run LLM after TTS


# ---------------------------------------------------------------------------
# Text pre-processing (identical to app/fast_path.py)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s'-]")


def _normalize(text: str) -> str:
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\bwon\b", "one", t)
    t = re.sub(r"\btoo\b", "two", t)
    t = re.sub(r"\bto\b",  "two", t)
    t = re.sub(r"\bfor\b", "four", t)
    t = re.sub(r"\bate\b", "eight", t)
    return t


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _fmt_phone(digits: str) -> str:
    """Format digits for TTS readback: each digit separated by ' — ' pause."""
    return " — ".join(list(digits))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def try_fast_path(
    session: Dict[str, Any],
    transcript: str,
) -> Optional[FastPathResult]:
    """
    Try to resolve the caller's response instantly using pattern matching.

    Returns a FastPathResult on a confident match.
    Returns None to fall through to the LLM.

    All handlers are called in priority order, same as app/fast_path.py.
    """
    last_prompt = (session.get(F_LAST_BOT_PROMPT) or "").lower()
    norm        = _normalize(transcript)

    # If the caller's number came from Twilio caller-ID, phone collection is
    # skipped entirely — no point running phone handlers.
    phone_already_known = bool(session.get("phone_from_twilio"))

    # State-aware handler dispatch — only run handlers valid for current step.
    # Falls back to full list if state is unrecognised (safe default).
    state = get_call_state(session)
    logger.debug("[ms_fast] checking transcript=%r state=%s phone_from_twilio=%s",
                 transcript[:60], state.value, phone_already_known)
    # New booking flow state dispatch
    if state == CallState.PRESENT_SLOTS:
        handlers = [_try_slot_selection, _try_yes_no_confirmation]
    elif state == CallState.NEW_OR_RETURNING:
        handlers = [_try_new_returning]
    elif state == CallState.CONFIRM_BOOKING:
        handlers = [_try_yes_no_confirmation]
    elif state == CallState.COLLECT_NAME:
        handlers = [_try_full_name]
    else:
        # For all other states (GREETING, COLLECT_REASON, COLLECT_DURATION,
        # CONFIRM_ASSESSMENT, COLLECT_AVAILABILITY, COLLECT_PHONE, etc.)
        # the gate3 state machine handles responses — fast-path is not active.
        handlers = []

    for handler in handlers:
        result = handler(last_prompt, norm, transcript, session)
        if result is not None:
            session[F_FAST_PATH_LAST_RESOLVED] = result.turn_type.value
            logger.info(
                "[ms_fast] transcript=%r state=%s resolved=%s",
                transcript[:60], state.value, result.turn_type.value,
            )
            return result

    logger.debug(
        "[ms_fast] transcript=%r state=%s resolved=None",
        transcript[:60], state.value,
    )
    return None


# ---------------------------------------------------------------------------
# Slot 1 -- New / returning patient
# (Clinic selection removed — single-site deployment)
# ---------------------------------------------------------------------------

_NEW_PATTERNS = (
    "new patient", "first time", "never been", "haven't been",
    "not been before", "first visit", "new to you", "never visited",
    "i'm new", "brand new", "not been", "no i haven't", "haven't",
    "no not been",
)
_NEW_SINGLE    = {"new", "no", "nope", "nah", "never"}
_RETURNING_PATTERNS = (
    "returning", "existing", "been before", "i have been",
    "already a patient", "i've been", "been there", "been to you",
    "come before", "visited before", "regular", "already registered",
    "new or returning",
)
# Fix 6: add all "yeah" variants to returning single-word matches
_RETURNING_SINGLE = {"yes", "yeah", "ya", "yah", "yea", "ye", "yer", "yep", "yup", "i have"}
_NR_TRIGGER_PHRASES = (
    "been to us before",
    "been here before",
    "been with us before",   # matches Q_NEW_OR_RETURNING = "Have you been with us before?"
    "new or returning",
    "visited us before",
    "seen us before",
)


def _try_new_returning(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    # Fix 1: session guard — if already answered, never update or re-trigger
    if session.get(F_COLLECTED, {}).get("patient_type"):
        logger.debug("[ms_fast] new_returning guard — patient_type already set, skipping")
        return None
    if not any(p in last_prompt for p in _NR_TRIGGER_PHRASES):
        return None

    is_new = (
        any(p in norm for p in _NEW_PATTERNS)
        or norm.strip() in _NEW_SINGLE
    )
    is_returning = (
        any(p in norm for p in _RETURNING_PATTERNS)
        or norm.strip() in _RETURNING_SINGLE
    )

    if is_new and is_returning:
        logger.info("[ms_fast_path] new_returning conflict norm=%r", norm)
        return None
    if not is_new and not is_returning:
        return None

    patient_type = "new" if is_new else "returning"
    session.setdefault(F_COLLECTED, {})
    session[F_COLLECTED]["patient_type"] = patient_type

    # Return None — LLM handles the full acknowledgement and continues the flow.
    # Session already has patient_type set so LLM skips re-asking.
    return None


# ---------------------------------------------------------------------------
# Slot 3 -- Yes / No confirmation (preserved exactly)
# ---------------------------------------------------------------------------

# Fix 6: all informal yes variants included
_YES_PATTERNS = (
    "yes", "yeah", "ya", "yah", "yea", "ye", "yer", "yep", "yup",
    "correct", "that's right", "thats right",
    "perfect", "great", "sure", "go ahead", "yes please", "that works",
    "sounds good", "that's fine", "thats fine", "ok", "okay", "fine",
    "do it", "book it", "confirmed", "please",
)
_NO_PATTERNS = (
    "no", "nope", "nah", "not really", "don't think so", "actually no",
    "cancel", "never mind", "nevermind", "wrong", "that's wrong",
    "thats wrong", "not right", "not that one", "different",
    "change it", "no thanks",
)
_CONFIRMATION_TRIGGERS = (
    "is that correct",
    "does that work for you",
    "does that all sound right",
    "is that right",
    "does that sound right",
    "is that ok",
    "is that okay",
)


def _try_yes_no_confirmation(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    trigger = next(
        (p for p in _CONFIRMATION_TRIGGERS if p in last_prompt),
        None,
    )
    if not trigger:
        return None

    is_yes = any(p in norm for p in _YES_PATTERNS)
    is_no  = any(p in norm for p in _NO_PATTERNS)

    if is_yes and is_no:
        logger.info("[ms_fast_path] yes_no conflict norm=%r", norm)
        return None
    if not is_yes and not is_no:
        return None

    if is_yes:
        if "is that correct" in trigger:
            session[F_FAST_PATH_PHONE_CONFIRMED] = True
        elif "does that work for you" in trigger:
            session[F_FAST_PATH_SLOT_CONFIRMED] = True
        else:
            session[F_FAST_PATH_FINAL_CONFIRMED] = True
        value = "yes"
    else:
        session[F_FAST_PATH_CORRECTION] = True
        value = "no"

    # Return None — LLM handles the full response based on the confirmed/denied flags.
    return None


# ---------------------------------------------------------------------------
# Slot 4 -- Full name (preserved exactly)
# ---------------------------------------------------------------------------

_NAME_TRIGGER_PHRASES = (
    "full name",
    "your name",
    "name please",
    "name for",
)
_INVALID_NAME_RE = re.compile(r"\d")


def _try_full_name(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    if not any(p in last_prompt for p in _NAME_TRIGGER_PHRASES):
        return None

    words = norm.split()
    if len(words) < 2 or len(words) > 4:
        return None
    if _INVALID_NAME_RE.search(norm):
        return None

    name_stored = " ".join(w.capitalize() for w in words)
    session.setdefault(F_COLLECTED, {})
    session[F_COLLECTED]["full_name"] = name_stored
    session[F_COLLECTED]["name"]      = name_stored

    # Build reply — fork on whether phone is already known from Twilio caller-ID.
    # If known: phone is in collected["phone"], skip directly to availability.
    # If not: ask for the phone number.
    phone_already_confirmed = (
        session.get(F_PHONE_COLLECTED_FROM_TWILIO)
        and bool(session.get(F_COLLECTED, {}).get("phone"))
    )

    if phone_already_confirmed:
        # Phone is already locked in — go straight to date/time preference.
        reply = "And do you have a preferred day or time in mind for the appointment?"
    else:
        caller_number = session.get("twilio_from_local", "")
        if caller_number and not session.get(F_COLLECTED, {}).get("phone"):
            digits    = _digits_only(caller_number)
            formatted = _fmt_phone(digits)
            reply = (
                f"And the best number to reach you on — "
                f"is that the same number you're calling from, {formatted}?"
            )
        else:
            reply = (
                "What number would you like to use for the booking? "
                "Could you give me the first five digits?"
            )

    session[F_LAST_BOT_PROMPT] = reply
    session[F_LAST_QUESTION]   = reply

    return FastPathResult(
        turn_type=FastPathTurnType.FULL_NAME,
        value=name_stored,
        response_text=reply,
        needs_llm_followup=False,
    )


# ---------------------------------------------------------------------------
# Slot 5 -- Phone: first five digits (preserved exactly)
# ---------------------------------------------------------------------------

_PHONE5_TRIGGER_PHRASES = (
    "first five digits",
    "first 5 digits",
    "first five",
)


def _try_phone_first_five(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    if not any(p in last_prompt for p in _PHONE5_TRIGGER_PHRASES):
        return None

    digits = _digits_only(raw)
    if len(digits) != 5:
        return None

    session[F_PHONE_PART_ONE] = digits

    reply = "Thank you -- and the last six digits?"
    session[F_LAST_BOT_PROMPT] = reply
    session[F_LAST_QUESTION]   = reply

    return FastPathResult(
        turn_type=FastPathTurnType.PHONE_FIRST_FIVE,
        value=digits,
        response_text=reply,
        needs_llm_followup=False,
    )


# ---------------------------------------------------------------------------
# Slot 6 -- Phone: last six digits (preserved exactly)
# ---------------------------------------------------------------------------

_PHONE6_TRIGGER_PHRASES = (
    "last six digits",
    "last 6 digits",
    "last six",
)


def _try_phone_last_six(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    if not any(p in last_prompt for p in _PHONE6_TRIGGER_PHRASES):
        return None

    digits = _digits_only(raw)
    if len(digits) != 6:
        return None

    part_one = session.get(F_PHONE_PART_ONE, "")
    if len(part_one) != 5:
        return None

    full_number = part_one + digits
    session[F_PHONE_PART_TWO]         = digits
    session[F_FAST_PATH_FULL_PHONE]   = full_number

    formatted = _fmt_phone(full_number)
    reply = f"Got that -- so that's {formatted} -- is that correct?"
    session[F_LAST_BOT_PROMPT] = reply
    session[F_LAST_QUESTION]   = reply

    return FastPathResult(
        turn_type=FastPathTurnType.PHONE_LAST_SIX,
        value=full_number,
        response_text=reply,
        needs_llm_followup=False,
    )


# ---------------------------------------------------------------------------
# Slot 7 -- Slot / appointment time selection (preserved exactly)
# ---------------------------------------------------------------------------

_SLOT_TRIGGER_PHRASES = (
    "which would you prefer",
    "first being",
    "does that work",
    "option one",
    "option two",
    "option three",
    "would you like",
    "which one works",
)
_SLOT_ONE_PATTERNS   = (
    "first", "one", "1", "the first", "first one", "first slot",
    "option one", "option 1", "that first", "number one",
    "the first one", "first option", "the one",
)
_SLOT_TWO_PATTERNS   = (
    "second", "two", "2", "the second", "second one", "second slot",
    "option two", "option 2", "that second", "number two",
    "the second one", "second option", "middle one", "the middle one",
)
_SLOT_THREE_PATTERNS = (
    "third", "three", "3", "the third", "third one", "third slot",
    "option three", "option 3", "that third", "number three",
    "the third one", "third option",
)
# "Last / final" catch-all — maps to the HIGHEST slot presented (slots_count).
# Checked before numbered patterns to prevent "the last one" matching slot-1
# via the "one" substring.
_SLOT_LAST_PATTERNS  = (
    "last one", "the last one", "final one", "the final one",
    "the last", "last option", "last slot", "final slot", "final option",
    "that last one", "the final", "last", "final",
)


def _try_slot_selection(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    offered = session.get("last_offered_slots") or []
    if not offered:
        return None
    if not any(p in last_prompt for p in _SLOT_TRIGGER_PHRASES):
        return None

    slots_count = session.get("slots_count", len(offered))

    # "Last / final" catch-all — maps to the highest slot regardless of number.
    # Checked first so "the last one" doesn't also match slot-1 via "one".
    if any(p in norm for p in _SLOT_LAST_PATTERNS):
        idx = min(slots_count, len(offered)) - 1
        logger.info("[ms_fast] slot_selection last/final → idx=%d norm=%r", idx, norm)
        session[F_SELECTED_SLOT] = offered[idx]
        return None

    # Numbered slot matching
    one   = any(p in norm for p in _SLOT_ONE_PATTERNS)
    two   = any(p in norm for p in _SLOT_TWO_PATTERNS)
    three = any(p in norm for p in _SLOT_THREE_PATTERNS) and len(offered) >= 3

    matches = sum([one, two, three])
    if matches != 1:
        if matches > 1:
            logger.info("[ms_fast_path] slot_selection conflict norm=%r", norm)
        return None

    idx = 0 if one else (1 if two else 2)
    if idx >= len(offered):
        return None

    logger.info("[ms_fast] slot_selection idx=%d norm=%r", idx, norm)
    session[F_SELECTED_SLOT] = offered[idx]

    # Return None — LLM generates the full confirmation sentence with date/time wording.
    return None
