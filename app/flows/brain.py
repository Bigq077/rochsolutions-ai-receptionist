# app/flows/brain.py
from __future__ import annotations

import datetime
import logging
from typing import Tuple, Dict, Any

from app.flows.triage_legacy import triage_turn
from app.config import PHASE3_ENABLED

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sidebar return phrases — one per state.
# If a state is missing the safe default is used instead.
# ---------------------------------------------------------------------------
_SIDEBAR_DEFAULT_RETURN = "So, coming back to your booking —"

RETURN_PHRASES: dict[str, str] = {
    "TRIAGE":            "So, coming back to what I can help you with —",
    "ASK_LOCATION":      "So, coming back to your booking — which clinic are you calling about, Alcester or Redditch?",
    "BOOK_PATIENT_TYPE": "So, coming back to your booking — are you a new patient or have you been here before?",
    "BOOK_REASON":       "So, coming back to your booking — what's the appointment for?",
    "BOOK_INTAKE":       "So, coming back to your booking —",
    "BOOK_RECOMMEND":    "So, coming back to your booking — shall I go ahead and book you in for that?",
    "BOOK_TIME_PREF":    "So, coming back to your booking — what day or time would suit you?",
    "BOOK_PICK_SLOT":    "So, coming back to your booking — please say 1, 2, or 3 for your preferred slot.",
    "BOOK_NAME":         "So, coming back to your booking — what's your full name?",
    "BOOK_PHONE":        "So, coming back to your booking — what's the best mobile number for us to reach you on?",
    "BOOK_CONFIRM":      "So, coming back to your booking — shall I go ahead and confirm that?",
    "RESCH_NAME":        "So, coming back to your reschedule — what's your full name so I can find your booking?",
    "RESCH_PHONE":       "So, coming back to your reschedule — what's your phone number?",
    "RESCH_BOOK_BACK":   "So, coming back to your reschedule — would you like to book back in?",
    "RESCH_SAME_PROBLEM": "So, coming back to your reschedule — is this for the same problem or a new one?",
    "CANCEL_OR_REBOOK":  "So, coming back to your appointment — would you like to cancel or rebook?",
    "RESCH_ORIGINAL":    "So, coming back to your reschedule —",
    "RESCH_NEW_PREF":    "So, coming back to your reschedule — what day or time works for you?",
    "RESCH_PICK_SLOT":   "So, coming back to your reschedule — please say 1, 2, or 3 for your preferred slot.",
    "RESCH_CONFIRM":     "So, coming back to your reschedule — shall I go ahead and confirm that?",
    "RESCH_PHONE_FALLBACK": "So, coming back — what's your phone number?",
    "INS_EXPLAIN":       "So, coming back to your insurance query —",
    "INS_COLLECT_INSURER": "So, coming back to your insurance — what's the name of your insurer?",
    "INS_BUPA_RESPONSE": "So, coming back to your insurance —",
    "INS_COLLECT_POLICY": "So, coming back to your insurance — what's your policy number?",
    "INSURANCE_PROVIDER": "So, coming back to your insurance details —",
    "FAQ_DETOUR":        "So, coming back to your booking —",
    "MANUAL_CAPTURE":    "So, coming back — shall I pass your details to the team?",
}

# Slot names used in loop-prevention message, keyed by state
_STATE_SLOT_NAMES: dict[str, str] = {
    "TRIAGE":            "your request",
    "ASK_LOCATION":      "your clinic location",
    "BOOK_PATIENT_TYPE": "your patient type",
    "BOOK_REASON":       "the reason for your visit",
    "BOOK_INTAKE":       "those details",
    "BOOK_RECOMMEND":    "your service selection",
    "BOOK_TIME_PREF":    "your preferred time",
    "BOOK_PICK_SLOT":    "your slot choice",
    "BOOK_NAME":         "your name",
    "BOOK_PHONE":        "your phone number",
    "BOOK_CONFIRM":      "your confirmation",
    "RESCH_NAME":        "your name",
    "RESCH_PHONE":       "your phone number",
    "RESCH_BOOK_BACK":   "your booking preference",
    "RESCH_SAME_PROBLEM": "your reason",
    "CANCEL_OR_REBOOK":  "your choice",
    "RESCH_ORIGINAL":    "your original appointment",
    "RESCH_NEW_PREF":    "your preferred time",
    "RESCH_PICK_SLOT":   "your slot choice",
    "RESCH_CONFIRM":     "your confirmation",
    "RESCH_PHONE_FALLBACK": "your phone number",
    "INS_EXPLAIN":       "your insurance details",
    "INS_COLLECT_INSURER": "your insurer",
    "INS_BUPA_RESPONSE": "your insurance response",
    "INS_COLLECT_POLICY": "your policy number",
    "INSURANCE_PROVIDER": "your insurance details",
    "FAQ_DETOUR":        "your question",
    "MANUAL_CAPTURE":    "your details",
}

# Extractor slot names → session collected keys
_EXTRACTOR_TO_COLLECTED: dict = {
    "reason_for_visit": "reason",
    "preferred_day":    "time_pref",
    "preferred_time":   "time_pref",
    "patient_name":     "name",
    "phone_number":     "phone",
}


def _append_turn(session: dict, role: str, text: str) -> dict:
    if not text:
        return session
    turns = session.get("turns", [])
    turns.append({"role": role, "text": text})
    session["turns"] = turns
    return session


async def _prefill_slots_from_utterance(user_text: str, session: dict) -> dict:
    """
    Call extract_slots_from_utterance and merge confident extractions into
    session["collected"] BEFORE the state machine evaluates which slot to ask
    for next.  On ANY exception the session is returned unchanged.
    """
    try:
        from app.slot_extractor import extract_slots_from_utterance
        extracted = await extract_slots_from_utterance(
            user_text, session.get("collected", {})
        )
        if not extracted:
            return session

        collected = session.setdefault("collected", {})

        # Handle preferred_day + preferred_time combination
        _day  = extracted.get("preferred_day")
        _time = extracted.get("preferred_time")
        if _day and _time and not collected.get("time_pref"):
            collected["time_pref"]        = f"{_day} {_time}"
            collected["time_pref_source"] = "pre-filled"
        elif _day and not collected.get("time_pref"):
            collected["time_pref"]        = _day
            collected["time_pref_source"] = "pre-filled"
        elif _time and not collected.get("time_pref"):
            collected["time_pref"]        = _time
            collected["time_pref_source"] = "pre-filled"

        for ext_key, value in extracted.items():
            if ext_key in ("preferred_day", "preferred_time"):
                continue  # already handled above
            sess_key = _EXTRACTOR_TO_COLLECTED.get(ext_key)
            if sess_key and not collected.get(sess_key):
                collected[sess_key]                  = value
                collected[f"{sess_key}_source"]      = "pre-filled"

    except Exception as exc:
        logger.error("brain._prefill_slots_from_utterance failed: %r", exc)

    return session


def _truncate_to_last_sentence(text: str, max_chars: int) -> str:
    """Truncate text at the last complete sentence boundary within max_chars."""
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    # Find the last sentence-ending punctuation
    for sep in (".", "!", "?"):
        idx = chunk.rfind(sep)
        if idx > 0:
            return chunk[:idx + 1]
    # No sentence boundary found — hard truncate
    return chunk.rstrip()


async def _handle_sidebar(
    user_text: str, session: dict
) -> Tuple[bool, str]:
    """
    Check for a sidebar and respond if one is detected.

    Returns (handled: bool, reply: str).
    If handled is True the caller should return reply immediately without
    advancing the state machine.
    """
    from app.sidebar_handler import detect_sidebar
    from app.knowledge_base import answer_sidebar, CLINIC_KB

    current_state = session.get("state", "TRIAGE")
    sidebar_counts: dict = session.setdefault("sidebar_count_per_state", {})
    count = sidebar_counts.get(current_state, 0)

    # ── Loop prevention: second sidebar on the same state ───────────────────
    if count >= 1:
        is_sidebar = await detect_sidebar(user_text, current_state)
        if is_sidebar:
            slot_name = _STATE_SLOT_NAMES.get(current_state, "your booking")
            loop_msg = (
                f"I want to make sure I get your booking sorted — "
                f"let me confirm {slot_name} first, and someone can answer "
                f"any other questions after the call."
            )
            logger.info(
                "SIDEBAR_LOOP_PREVENTION timestamp=%s state=%s utterance=%r",
                datetime.datetime.utcnow().isoformat(),
                current_state,
                user_text,
            )
            return True, loop_msg

    # ── First sidebar detection ──────────────────────────────────────────────
    is_sidebar = await detect_sidebar(user_text, current_state)
    if not is_sidebar:
        return False, ""

    # Answer the sidebar
    answer = await answer_sidebar(user_text, CLINIC_KB)
    return_phrase = RETURN_PHRASES.get(current_state, _SIDEBAR_DEFAULT_RETURN)

    # Enforce 400-char combined cap — truncate answer only, never return_phrase
    max_answer_chars = 400 - len(return_phrase) - 1  # -1 for the space
    answer = _truncate_to_last_sentence(answer, max(max_answer_chars, 20))

    reply = f"{answer} {return_phrase}".strip()

    # Increment per-state counter
    sidebar_counts[current_state] = count + 1

    # Structured log
    logger.info(
        "SIDEBAR_ANSWERED timestamp=%s state=%s utterance=%r answer=%r",
        datetime.datetime.utcnow().isoformat(),
        current_state,
        user_text,
        answer,
    )

    return True, reply


async def handle_user_text(user_text: str, session: dict) -> Tuple[str, dict]:
    """
    Shared brain handler for any channel (Twilio, Avatar web, etc.)
    - adds user turn
    - pre-fills slots from utterance (before state machine runs)
    - checks for sidebar questions (answered without advancing the state machine)
    - runs triage_turn (legacy) or handle_turn (Phase 3)
    - adds assistant turn
    - returns reply text + updated session
    """
    session = _append_turn(session, "user", user_text)

    # ── Slot pre-filling ────────────────────────────────────────────────────
    # Merge any confidently-extracted slots into session["collected"] BEFORE
    # the state machine decides which slot to ask for next.  On failure the
    # session is returned unchanged and the state machine continues normally.
    session = await _prefill_slots_from_utterance(user_text, session)
    # ── End slot pre-filling ────────────────────────────────────────────────

    # ── Sidebar detection ───────────────────────────────────────────────────
    # Only run mid-booking (not during TRIAGE — caller may be asking what
    # the clinic offers, which is on-flow at that stage).
    _state = session.get("state", "TRIAGE")
    _mid_booking = _state not in ("TRIAGE", "ASK_LOCATION")
    if _mid_booking:
        try:
            _sidebar_handled, _sidebar_reply = await _handle_sidebar(user_text, session)
            if _sidebar_handled:
                session = _append_turn(session, "assistant", _sidebar_reply)
                return _sidebar_reply, session
        except Exception as _sb_exc:
            logger.error("brain._handle_sidebar failed: %r", _sb_exc)
    # ── End sidebar detection ────────────────────────────────────────────────

    if PHASE3_ENABLED:
        from app.flows.conversation import handle_turn
        reply_text, session = await handle_turn(user_text, session)
    else:
        reply_text, session = await triage_turn(user_text, session)

    session = _append_turn(session, "assistant", reply_text)
    return reply_text, session
