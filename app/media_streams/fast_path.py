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
    F_SELECTED_LOCATION,
    F_LOCATION_SELECTED,
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
    return " ".join(digits)


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

    # State-aware handler dispatch — only run handlers valid for current step.
    # Falls back to full list if state is unrecognised (safe default).
    state = get_call_state(session)
    logger.debug("[ms_fast] checking transcript=%r state=%s", transcript[:60], state.value)
    if state == CallState.COLLECT_PHONE_PART_TWO:
        handlers = [_try_phone_last_six, _try_yes_no_confirmation]
    elif state == CallState.COLLECT_PHONE_PART_ONE:
        handlers = [_try_phone_first_five, _try_yes_no_confirmation]
    elif state == CallState.COLLECT_NAME:
        handlers = [_try_full_name]
    elif state in (CallState.GREETING, CallState.CLINIC_SELECTION):
        handlers = [_try_clinic_selection]
    elif state == CallState.NEW_OR_RETURNING:
        handlers = [_try_new_returning]
    elif state == CallState.PRESENT_SLOTS:
        handlers = [_try_slot_selection, _try_yes_no_confirmation]
    elif state == CallState.CONFIRM_BOOKING:
        handlers = [_try_yes_no_confirmation]
    else:
        handlers = [
            _try_phone_last_six, _try_phone_first_five, _try_full_name,
            _try_clinic_selection, _try_new_returning,
            _try_yes_no_confirmation, _try_slot_selection,
        ]

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
# Slot 1 -- Clinic selection (preserved exactly)
# ---------------------------------------------------------------------------

_ALCESTER_PATTERNS = (
    "alcester", "alchester", "alster", "all ster", "all-ster", "allster",
    "alce", "awlster", "olster", "ulster", "alcest",
    " one", "^one", "number one", "option one", "the first", "first one",
    "first option", "that one", "option 1",
)
_REDDITCH_PATTERNS = (
    "redditch", "reditch", "reddich", "red ditch", "red witch", "reddit",
    " two", "^two", "number two", "option two", "the second", "second one",
    "second option", "option 2",
)
_LOCATION_TRIGGER_PHRASES = (
    "say one for",
    "alcester or redditch",
    "which clinic",
    "redditch or alcester",
    "which location",
    "which branch",
    "press one",
    "option one for",
)


def _try_clinic_selection(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    if not any(p in last_prompt for p in _LOCATION_TRIGGER_PHRASES):
        return None

    alcester = _matches_location(norm, _ALCESTER_PATTERNS, ("1",))
    redditch = _matches_location(norm, _REDDITCH_PATTERNS, ("2",))

    if alcester and redditch:
        logger.info("[ms_fast_path] clinic_selection conflict norm=%r", norm)
        return None
    if not alcester and not redditch:
        return None

    location_id    = "alcester" if alcester else "redditch"
    location_label = "Alcester" if alcester else "Redditch"

    session.setdefault(F_COLLECTED, {})
    session[F_SELECTED_LOCATION]            = location_id
    session[F_LOCATION_SELECTED]            = True
    session[F_COLLECTED]["location"]        = location_id
    session[F_COLLECTED].setdefault("service", "physiotherapy assessment")

    # If patient_type already known, fall through to LLM
    if session.get(F_COLLECTED, {}).get("patient_type"):
        return None

    reply = f"Right, {location_label} -- no problem. And have you been to us before?"
    session[F_LAST_BOT_PROMPT] = reply
    session[F_LAST_QUESTION]   = reply

    return FastPathResult(
        turn_type=FastPathTurnType.CLINIC_SELECTION,
        value=location_id,
        response_text=reply,
        needs_llm_followup=False,
    )


def _matches_location(norm: str, patterns: tuple, digit_tokens: tuple) -> bool:
    for p in patterns:
        if p.startswith("^"):
            if norm == p[1:] or norm.startswith(p[1:] + " "):
                return True
        elif p.startswith(" "):
            word = p.strip()
            if re.search(r"\b" + re.escape(word) + r"\b", norm):
                return True
        else:
            if p in norm:
                return True
    stripped = norm.strip()
    for d in digit_tokens:
        if stripped == d:
            return True
    return False


# ---------------------------------------------------------------------------
# Slot 2 -- New / returning patient (preserved exactly)
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
_RETURNING_SINGLE = {"yes", "yeah", "yep", "yup", "i have"}
_NR_TRIGGER_PHRASES = (
    "been to us before",
    "been here before",
    "new or returning",
    "visited us before",
    "seen us before",
)


def _try_new_returning(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
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

_YES_PATTERNS = (
    "yes", "yeah", "yep", "yup", "correct", "that's right", "thats right",
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

    # Build reply
    caller_number = session.get("twilio_from_local", "")
    if caller_number and not session.get(F_COLLECTED, {}).get("phone"):
        digits    = _digits_only(caller_number)
        formatted = _fmt_phone(digits)
        reply = (
            f"Lovely, {name_stored}. And the best number to reach you on -- "
            f"is that the same number you're calling from, {formatted}?"
        )
    else:
        reply = f"Lovely, {name_stored}. And the best number to reach you on?"

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
_SLOT_ONE_PATTERNS   = ("one", "1", "first", "the first", "first one", "first slot",
                        "option one", "that one", "number one", "the first one", "first option")
_SLOT_TWO_PATTERNS   = ("two", "2", "second", "the second", "second one", "second slot",
                        "option two", "number two", "second option")
_SLOT_THREE_PATTERNS = ("three", "3", "third", "the third", "third one", "last",
                        "last one", "the last one", "third slot", "final one",
                        "number three", "third option")


def _try_slot_selection(
    last_prompt: str, norm: str, raw: str, session: Dict[str, Any],
) -> Optional[FastPathResult]:
    offered = session.get("last_offered_slots") or []
    if not offered:
        return None
    if not any(p in last_prompt for p in _SLOT_TRIGGER_PHRASES):
        return None

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

    session[F_SELECTED_SLOT] = offered[idx]

    # Return None — LLM generates the full confirmation sentence with date/time wording.
    return None
