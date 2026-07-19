# app/media_streams/clinical_screening.py
"""
Deterministic clinical red-flag layer for template clinics (Layer 1).

Two jobs, both driven by the clinic's `clinical_screening` config block
(app/clinics/<id>/clinic.json — see jv_v1 for the schema):

1. EMERGENCY INTERCEPT — if the caller volunteers a genuine emergency
   ("chest pain", "can't breathe", stroke signs, collapse), return the
   clinic's scripted emergency response so the caller hears it immediately
   and deterministically. The life-safety path never depends on the model.

2. PROACTIVE SCREEN TRACKING — when the caller's presentation matches a
   screen's trigger keywords (e.g. lower-back pain → cauda equina), set
   session["pending_screen"] = <screen id>. The prompt layer
   (_b7_call_state → SCREEN REQUIRED) then forces the model to ask that
   screen's question before booking, and the book_appointment tool refuses
   while the flag is unresolved. Once the screen question has been asked,
   this module classifies the caller's answer deterministically:
     - red-flag positive → escalation text is spoken deterministically and
       session["screen_red_flag"] is set (booking stays blocked);
     - clear negative    → the flag is cleared and the screen is marked
       completed (asked at most once per call);
     - unclear           → the flag stays set and the prompt re-drives.

Everything here is pure keyword/pattern matching on the transcript — no LLM
call, no latency. Clinics without a `clinical_screening` block (or with
enabled=false) are completely unaffected.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Session keys owned by this module
PENDING_SCREEN_KEY = "pending_screen"        # screen id awaiting question/answer
SCREEN_RED_FLAG_KEY = "screen_red_flag"      # screen id that answered positive
SCREENS_COMPLETED_KEY = "screens_completed"  # list of screen ids already run

# Brief empathetic acknowledgement spoken immediately before a freshly-armed
# screen question — this replaces the warm "acknowledge first" the model used to
# add in the prompt-driven flow. Kept to a short empathy line ONLY, because the
# configured screen_question already carries its own conversational lead-in
# (e.g. "Before we look at the next step, can I ask — …"); a longer lead here
# would collide with it. Overridable per screen via clinic.json
# screen["screen_lead_in"] (set "" to omit entirely).
_DEFAULT_SCREEN_LEAD_IN = "I'm sorry to hear that."


def _norm(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation apart from apostrophes."""
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def screening_config(clinic: Dict[str, Any]) -> Dict[str, Any]:
    cs = clinic.get("clinical_screening") or {}
    return cs if cs.get("enabled") else {}


def screening_enabled(clinic: Dict[str, Any]) -> bool:
    return bool(screening_config(clinic))


def _screens(clinic: Dict[str, Any]) -> List[Dict[str, Any]]:
    return screening_config(clinic).get("screens") or []


def get_screen(clinic: Dict[str, Any], screen_id: str) -> Optional[Dict[str, Any]]:
    for s in _screens(clinic):
        if s.get("id") == screen_id:
            return s
    return None


# ─────────────────────────────────────────────────────────────────────────
# 1. Emergency intercept
# ─────────────────────────────────────────────────────────────────────────
def detect_emergency(text: str, clinic: Dict[str, Any]) -> bool:
    """True if the utterance volunteers an emergency red flag (config-driven)."""
    cs = screening_config(clinic)
    kws = (cs.get("emergency_red_flags") or {}).get("keywords") or []
    if not kws:
        return False
    t = _norm(text)
    return any(_norm(k) in t for k in kws)


def emergency_response_text(clinic: Dict[str, Any]) -> str:
    pf = clinic.get("prompt_facts", {}) or {}
    return (
        pf.get("emergency_response")
        or (clinic.get("call_handling", {}) or {}).get("emergency_message")
        or "If you are experiencing a medical emergency, please hang up and "
           "call 999 immediately, or go to your nearest A&E."
    )


# ─────────────────────────────────────────────────────────────────────────
# 2. Screen trigger + answer classification
# ─────────────────────────────────────────────────────────────────────────
def _screen_triggered(text_norm: str, screen: Dict[str, Any]) -> bool:
    """True if the utterance matches this screen's trigger definition.

    Two forms, config-driven:
      trigger_keywords     — ANY keyword matches (simple presentations).
      trigger_all_groups   — list of keyword groups; EVERY group must have at
                             least one hit. Used for compound presentations
                             (e.g. VBI = neck complaint AND a dizziness/
                             neuro signal) so a plain neck-pain caller is not
                             over-screened."""
    groups = screen.get("trigger_all_groups")
    if groups:
        return all(
            any(_norm(k) in text_norm for k in group) for group in groups if group
        )
    return any(
        _norm(k) in text_norm for k in (screen.get("trigger_keywords") or [])
    )


def match_screen_trigger(
    text: str, clinic: Dict[str, Any], session: Dict[str, Any]
) -> Optional[str]:
    """Return the id of the first not-yet-run screen whose trigger definition
    matches the utterance, or None. Screens already completed (or currently
    pending) this call are never re-triggered."""
    t = _norm(text)
    if not t:
        return None
    done = set(session.get(SCREENS_COMPLETED_KEY) or [])
    pending = session.get(PENDING_SCREEN_KEY)
    for s in _screens(clinic):
        sid = s.get("id")
        if not sid or sid in done or sid == pending:
            continue
        if _screen_triggered(t, s):
            return sid
    return None


def _question_was_asked(session: Dict[str, Any], screen: Dict[str, Any]) -> bool:
    """True if the last bot prompt was (or contained) this screen's question.

    The model is instructed to ask the configured question, but wording can
    drift slightly, so this matches on distinctive content words from the
    question (>=2 of the words longer than 5 chars) rather than an exact
    substring."""
    last = _norm(
        (session.get("last_bot_prompt") or "") + " " + (session.get("last_question") or "")
    )
    if not last:
        return False
    q = _norm(screen.get("screen_question") or "")
    distinctive = [w for w in set(q.split()) if len(w) > 5]
    if not distinctive:
        return q in last
    hits = sum(1 for w in distinctive if w in last)
    return hits >= 2


_NEGATIVE_PATTERNS = (
    "no", "nope", "nah", "none", "neither", "nothing like that",
    "nothing of the sort", "no nothing", "not that i", "no changes",
    "no change", "all fine", "everything's fine", "everything is fine",
    "i haven't", "i have not", "i don't", "i do not", "definitely not",
    "not at all", "thankfully not", "luckily not",
)


def classify_screen_answer(text: str, screen: Dict[str, Any]) -> str:
    """Classify the caller's reply to a screen question:
    'red_flag' | 'clear' | 'unclear'.

    Red-flag keywords are checked FIRST — an answer like "no feeling in my
    legs" contains 'no' but is a positive."""
    t = _norm(text)
    if not t:
        return "unclear"
    for k in screen.get("red_flag_answer_keywords") or []:
        if _norm(k) in t:
            return "red_flag"
    first_word = t.split()[0] if t.split() else ""
    if first_word in ("no", "nope", "nah", "none", "neither"):
        return "clear"
    if any(p in t for p in _NEGATIVE_PATTERNS):
        return "clear"
    return "unclear"


# ─────────────────────────────────────────────────────────────────────────
# Per-utterance state machine
# ─────────────────────────────────────────────────────────────────────────
def update_screening_state(
    session: Dict[str, Any], clinic: Dict[str, Any], text: str
) -> Dict[str, Any]:
    """Advance the screening state for one caller utterance.

    Mutates session (pending_screen / screen_red_flag / screens_completed)
    and returns {"action": ..., "speak": ...}:
      action="emergency" — speak the emergency response now, skip the LLM.
      action="escalate"  — screen answered positive; speak the escalation
                           now, skip the LLM. Booking stays blocked.
      action="ask_screen"— a screen just armed; speak its question now
                           (lead-in + screen_question), skip the LLM, so the
                           safety screen is asked on its own before booking.
      action="none"      — nothing deterministic to say; dispatch to the LLM
                           as normal (the prompt layer handles the screen
                           question itself via SCREEN REQUIRED).
    Never raises; on any error returns action="none" so a bug here can never
    take down the call loop.
    """
    try:
        if not screening_enabled(clinic):
            return {"action": "none", "speak": None}

        # Emergencies pre-empt everything, including an in-progress screen.
        if detect_emergency(text, clinic):
            session[PENDING_SCREEN_KEY] = None
            logger.info("[clinical_screening] EMERGENCY detected: %r", text[:80])
            return {"action": "emergency", "speak": emergency_response_text(clinic)}

        pending_id = session.get(PENDING_SCREEN_KEY)
        if pending_id:
            screen = get_screen(clinic, pending_id)
            if screen and _question_was_asked(session, screen):
                verdict = classify_screen_answer(text, screen)
                if verdict == "red_flag":
                    session[PENDING_SCREEN_KEY] = None
                    # block_booking (default True): a positive answer freezes
                    # booking until urgent care. Advisory screens (e.g. the
                    # inflammatory-pattern flag) set block_booking=false — the
                    # escalation is spoken but booking may continue, because
                    # physio alongside a GP review is clinically appropriate.
                    if screen.get("block_booking", True):
                        session[SCREEN_RED_FLAG_KEY] = pending_id
                    done = list(session.get(SCREENS_COMPLETED_KEY) or [])
                    if pending_id not in done:
                        done.append(pending_id)
                    session[SCREENS_COMPLETED_KEY] = done
                    logger.info(
                        "[clinical_screening] screen %s POSITIVE (block=%s): %r",
                        pending_id, screen.get("block_booking", True), text[:80],
                    )
                    return {
                        "action": "escalate",
                        "speak": screen.get("escalation")
                        or emergency_response_text(clinic),
                    }
                if verdict == "clear":
                    session[PENDING_SCREEN_KEY] = None
                    done = list(session.get(SCREENS_COMPLETED_KEY) or [])
                    if pending_id not in done:
                        done.append(pending_id)
                    session[SCREENS_COMPLETED_KEY] = done
                    logger.info(
                        "[clinical_screening] screen %s clear: %r",
                        pending_id, text[:80],
                    )
                    # The LLM turn acknowledges ("that's reassuring") and moves on.
                    return {"action": "none", "speak": None}
                # unclear — leave pending; prompt re-drives the question.
                logger.info(
                    "[clinical_screening] screen %s answer unclear: %r",
                    pending_id, text[:80],
                )
                return {"action": "none", "speak": None}
            # Question not asked yet — keep the flag; the SCREEN REQUIRED
            # steer forces it on the next model turn. Still allow a new,
            # different trigger to upgrade below? No — one screen at a time.
            return {"action": "none", "speak": None}

        # No pending screen — does this utterance trigger one?
        sid = match_screen_trigger(text, clinic, session)
        if sid:
            session[PENDING_SCREEN_KEY] = sid
            logger.info(
                "[clinical_screening] screen %s ARMED by: %r", sid, text[:80]
            )
            # Speak the screen question deterministically THIS turn — a warm
            # lead-in + the configured question, on its own, BEFORE any
            # booking/modality step. Previously this turn dispatched to the LLM
            # and relied on the SCREEN REQUIRED prompt steer, which the model
            # could skip in favour of a booking offer (Call-1, 2026-07-19). This
            # mirrors the emergency/escalate deterministic paths and removes the
            # adherence risk. The caller's answer is classified on the next turn
            # (_question_was_asked matches the spoken question's distinctive
            # words); a red-flag then escalates deterministically as before.
            screen = get_screen(clinic, sid) or {}
            q = (screen.get("screen_question") or "").strip()
            if q:
                lead = screen.get("screen_lead_in")
                if lead is None:
                    lead = _DEFAULT_SCREEN_LEAD_IN
                lead = (lead or "").strip()
                spoken = (f"{lead} {q}".strip()) if lead else q
                logger.info(
                    "[clinical_screening] screen %s asked deterministically", sid
                )
                return {"action": "ask_screen", "speak": spoken}
        return {"action": "none", "speak": None}
    except Exception:
        logger.exception("[clinical_screening] update failed — failing open")
        return {"action": "none", "speak": None}


def booking_blocked_reason(session: Dict[str, Any], clinic: Dict[str, Any]) -> Optional[str]:
    """Deterministic backstop for the book_appointment tool: return a
    caller-safe explanation if booking must not proceed, else None."""
    if not screening_enabled(clinic):
        return None
    rf = session.get(SCREEN_RED_FLAG_KEY)
    if rf:
        screen = get_screen(clinic, rf) or {}
        return screen.get("escalation") or (
            "Booking is paused — the caller reported urgent red-flag symptoms "
            "and must seek urgent care first."
        )
    pending = session.get(PENDING_SCREEN_KEY)
    if pending:
        screen = get_screen(clinic, pending) or {}
        q = screen.get("screen_question") or "the safety screening question"
        return (
            "SAFETY SCREEN NOT YET COMPLETED — before booking you must ask: "
            f"\"{q}\" Ask it now, on its own, and only book once the caller "
            "answers no."
        )
    return None
