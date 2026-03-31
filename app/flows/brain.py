# app/flows/brain.py
from __future__ import annotations

import logging
from typing import Tuple, Dict, Any

from app.flows.triage_legacy import triage_turn
from app.config import PHASE3_ENABLED

logger = logging.getLogger(__name__)

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


async def handle_user_text(user_text: str, session: dict) -> Tuple[str, dict]:
    """
    Shared brain handler for any channel (Twilio, Avatar web, etc.)
    - adds user turn
    - pre-fills slots from utterance (before state machine runs)
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

    if PHASE3_ENABLED:
        from app.flows.conversation import handle_turn
        reply_text, session = await handle_turn(user_text, session)
    else:
        reply_text, session = await triage_turn(user_text, session)

    session = _append_turn(session, "assistant", reply_text)
    return reply_text, session
