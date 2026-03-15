# app/fast_path.py
"""
Fast-path resolver — intercepts common slot-fill turns before they reach the LLM.

For predictable, unambiguous caller responses (clinic selection, new/returning,
yes/no confirmations, full name, phone parts, slot selection) this module returns
a (reply_text, updated_session) tuple in ~100 ms without touching Anthropic.

Returns None for anything ambiguous, complex, or not matched — the caller falls
through to handle_turn() as normal.

Design rules:
  - All pattern matching is lowercase + punctuation-stripped
  - Substring containment (not exact match) so natural speech works
  - Conflict detection: if two exclusive patterns both match → return None
  - Homophone normalisation applied before matching
  - Every resolution is logged at INFO level
  - Session keys last_bot_prompt and last_question are updated on complete resolves
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text pre-processing
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s'-]")  # keep word chars, spaces, hyphens, apostrophes


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, apply homophones."""
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Homophone normalisation (order matters — longer first)
    t = re.sub(r"\bwon\b", "one", t)
    t = re.sub(r"\btoo\b", "two", t)
    t = re.sub(r"\bto\b", "two", t)
    t = re.sub(r"\bfor\b", "four", t)
    t = re.sub(r"\bate\b", "eight", t)
    return t


def _digits_only(text: str) -> str:
    """Strip everything except digit characters."""
    return re.sub(r"\D", "", text)


def _fmt_phone(digits: str) -> str:
    """Format 11 digit string as space-separated individual digits."""
    return " ".join(digits)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_fast_path(
    session: Dict[str, Any],
    transcript: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Try to resolve the caller's response instantly using pattern matching.

    Returns (reply_text, updated_session) on a confident match.
    Returns None to fall through to the LLM.
    """
    last_prompt = (session.get("last_bot_prompt") or "").lower()
    norm = _normalize(transcript)

    # Try each slot handler in priority order.
    # Each handler returns (reply, session) or None.
    handlers = [
        _try_phone_last_six,
        _try_phone_first_five,
        _try_full_name,
        _try_clinic_selection,
        _try_new_returning,
        _try_yes_no_confirmation,
        _try_slot_selection,
    ]

    for handler in handlers:
        result = handler(last_prompt, norm, transcript, session)
        if result is not None:
            return result

    return None


# ---------------------------------------------------------------------------
# Slot 1 — Clinic selection
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
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    # Only fire when the bot was asking a location question
    if not any(p in last_prompt for p in _LOCATION_TRIGGER_PHRASES):
        return None

    # Check exact single-token matches to avoid false positives from longer text
    alcester = _matches_location(norm, _ALCESTER_PATTERNS, ("1",))
    redditch = _matches_location(norm, _REDDITCH_PATTERNS, ("2",))

    if alcester and redditch:
        logger.info("fast_path: clinic_selection conflict — falling through norm=%r", norm)
        return None
    if not alcester and not redditch:
        return None

    location_id = "alcester" if alcester else "redditch"
    location_label = "Alcester" if alcester else "Redditch"

    # Update session
    session.setdefault("collected", {})
    session["selected_location"] = location_id
    session["location_selected"] = True
    session["collected"]["location"] = location_id
    session["collected"].setdefault("service", "physiotherapy assessment")

    logger.info(
        "fast_path: slot=clinic_selection value=%r input=%r",
        location_id, _raw,
    )

    # If patient_type already known → LLM must handle (needs check_availability immediately)
    if session.get("collected", {}).get("patient_type"):
        return None

    reply = f"Right, {location_label} — no problem. And have you been to us before?"
    session["last_bot_prompt"] = reply
    session["last_question"] = reply
    return reply, session


def _matches_location(norm: str, patterns: tuple, digit_tokens: tuple) -> bool:
    """Return True if norm matches any location pattern or bare digit."""
    for p in patterns:
        # Handle ^ anchored patterns
        if p.startswith("^"):
            if norm == p[1:] or norm.startswith(p[1:] + " "):
                return True
        elif p.startswith(" "):
            # Trailing-space patterns: match as whole word
            word = p.strip()
            if re.search(r"\b" + re.escape(word) + r"\b", norm):
                return True
        else:
            if p in norm:
                return True
    # Bare digit (exact or with filler)
    stripped = norm.strip()
    for d in digit_tokens:
        if stripped == d:
            return True
    return False


# ---------------------------------------------------------------------------
# Slot 2 — New / returning patient
# ---------------------------------------------------------------------------

_NEW_PATTERNS = (
    "new patient", "first time", "never been", "haven't been",
    "not been before", "first visit", "new to you", "never visited",
    "i'm new", "brand new", "not been", "no i haven't", "haven't",
    "no not been", "first time",
)
_NEW_SINGLE = {"new", "no", "nope", "nah", "never", "nope"}

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
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
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
        logger.info("fast_path: new_returning conflict — falling through norm=%r", norm)
        return None
    if not is_new and not is_returning:
        return None

    patient_type = "new" if is_new else "returning"
    session.setdefault("collected", {})
    session["collected"]["patient_type"] = patient_type

    logger.info(
        "fast_path: slot=new_returning value=%r input=%r",
        patient_type, _raw,
    )

    # Return None — the LLM handles the acknowledgement and triggers check_availability.
    # Session is already updated so LLM skips re-asking.
    return None


# ---------------------------------------------------------------------------
# Slot 3 — Yes / No confirmation
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
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    trigger = next(
        (p for p in _CONFIRMATION_TRIGGERS if p in last_prompt),
        None,
    )
    if not trigger:
        return None

    is_yes = any(p in norm for p in _YES_PATTERNS)
    is_no = any(p in norm for p in _NO_PATTERNS)

    if is_yes and is_no:
        logger.info("fast_path: yes_no conflict — falling through norm=%r", norm)
        return None
    if not is_yes and not is_no:
        return None

    if is_yes:
        if "is that correct" in trigger:
            session["_fast_path_phone_confirmed"] = True
        elif "does that work for you" in trigger:
            session["_fast_path_slot_confirmed"] = True
        else:
            session["_fast_path_final_confirmed"] = True
        logger.info("fast_path: slot=yes_no value=yes trigger=%r input=%r", trigger, _raw)
    else:
        session["_fast_path_correction_needed"] = True
        logger.info("fast_path: slot=yes_no value=no trigger=%r input=%r", trigger, _raw)

    # Return None — LLM handles the actual confirmation / correction flow
    return None


# ---------------------------------------------------------------------------
# Slot 4 — Full name
# ---------------------------------------------------------------------------

_NAME_TRIGGER_PHRASES = (
    "full name",
    "your name",
    "name please",
    "name for",
)

_INVALID_NAME_RE = re.compile(r"\d")  # digits in name input → fall through


def _try_full_name(
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not any(p in last_prompt for p in _NAME_TRIGGER_PHRASES):
        return None

    # Must be 2–4 words, no digits
    words = norm.split()
    if len(words) < 2 or len(words) > 4:
        return None
    if _INVALID_NAME_RE.search(norm):
        return None

    # Capitalise each word for the stored name
    name_stored = " ".join(w.capitalize() for w in words)

    session.setdefault("collected", {})
    session["collected"]["full_name"] = name_stored
    session["collected"]["name"] = name_stored

    logger.info("fast_path: slot=full_name value=%r input=%r", name_stored, _raw)

    # Build reply
    caller_number = session.get("twilio_from_local", "")
    if caller_number and not session.get("collected", {}).get("phone"):
        digits = _digits_only(caller_number)
        formatted = _fmt_phone(digits)
        reply = (
            f"Lovely, {name_stored}. And the best number to reach you on — "
            f"is that the same number you're calling from, {formatted}?"
        )
    else:
        reply = f"Lovely, {name_stored}. And the best number to reach you on?"

    session["last_bot_prompt"] = reply
    session["last_question"] = reply
    return reply, session


# ---------------------------------------------------------------------------
# Slot 5 — Phone: first five digits
# ---------------------------------------------------------------------------

_PHONE5_TRIGGER_PHRASES = (
    "first five digits",
    "first 5 digits",
    "first five",
)


def _try_phone_first_five(
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not any(p in last_prompt for p in _PHONE5_TRIGGER_PHRASES):
        return None

    digits = _digits_only(_raw)
    if len(digits) != 5:
        return None

    session["phone_part_one"] = digits
    logger.info("fast_path: slot=phone_first_five value=%r input=%r", digits, _raw)

    reply = "Thank you — and the last six digits?"
    session["last_bot_prompt"] = reply
    session["last_question"] = reply
    return reply, session


# ---------------------------------------------------------------------------
# Slot 6 — Phone: last six digits
# ---------------------------------------------------------------------------

_PHONE6_TRIGGER_PHRASES = (
    "last six digits",
    "last 6 digits",
    "last six",
)


def _try_phone_last_six(
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not any(p in last_prompt for p in _PHONE6_TRIGGER_PHRASES):
        return None

    digits = _digits_only(_raw)
    if len(digits) != 6:
        return None

    part_one = session.get("phone_part_one", "")
    if len(part_one) != 5:
        # Can't assemble full number — fall through to LLM
        return None

    full_number = part_one + digits
    session["phone_part_two"] = digits
    session["_fast_path_full_phone"] = full_number

    logger.info(
        "fast_path: slot=phone_last_six full=%r input=%r",
        full_number, _raw,
    )

    formatted = _fmt_phone(full_number)
    reply = f"Got that — so that's {formatted} — is that correct?"
    session["last_bot_prompt"] = reply
    session["last_question"] = reply
    return reply, session


# ---------------------------------------------------------------------------
# Slot 7 — Slot / appointment time selection
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

_SLOT_ONE_PATTERNS = (
    "one", "1", "first", "the first", "first one", "first slot",
    "option one", "that one", "number one", "the first one", "first option",
)
_SLOT_TWO_PATTERNS = (
    "two", "2", "second", "the second", "second one", "second slot",
    "option two", "number two", "second option",
)
_SLOT_THREE_PATTERNS = (
    "three", "3", "third", "the third", "third one", "last",
    "last one", "the last one", "third slot", "final one",
    "number three", "third option",
)


def _try_slot_selection(
    last_prompt: str,
    norm: str,
    _raw: str,
    session: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    offered = session.get("last_offered_slots") or []
    if not offered:
        return None
    if not any(p in last_prompt for p in _SLOT_TRIGGER_PHRASES):
        return None

    one = any(p in norm for p in _SLOT_ONE_PATTERNS)
    two = any(p in norm for p in _SLOT_TWO_PATTERNS)
    three = any(p in norm for p in _SLOT_THREE_PATTERNS) and len(offered) >= 3

    # Conflict check
    matches = sum([one, two, three])
    if matches != 1:
        if matches > 1:
            logger.info("fast_path: slot_selection conflict — falling through norm=%r", norm)
        return None

    if one:
        idx = 0
    elif two:
        idx = 1
    else:
        idx = 2

    if idx >= len(offered):
        return None  # Slot index doesn't exist

    session["selected_slot"] = offered[idx]

    logger.info(
        "fast_path: slot=slot_selection idx=%d value=%r input=%r",
        idx, offered[idx], _raw,
    )

    # Return None — LLM generates the full confirmation sentence with date/time wording
    return None
