# app/media_streams/first_turn_extractor.py
"""
Deterministic first-turn signal extraction for the AI receptionist.

Reads the caller's first real utterance and extracts structured signals
into session state.  Pure extraction only — no routing, no TTS, no LLM,
no flow decisions.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.media_streams.utterance_router import has_service_fit_question

logger = logging.getLogger(__name__)

# ── Booking ────────────────────────────────────────────────────────────────────
# Single booking words checked with word-boundary regex to avoid substring hits
# (e.g. "Facebook").  Multi-word phrases checked via substring.

_BOOKING_WORD_RE = re.compile(
    r"\b(book|booking|booked|appointment|appointments|schedule|scheduled)\b"
)
_BOOKING_MULTI = (
    "come in", "get seen", "be seen", "pop in", "get that sorted",
    "seen as soon as", "make an appointment", "book me in",
    "get an appointment", "arrange an", "get booked",
    "come and see", "come to see",
)


def _has_booking(t: str) -> bool:
    return bool(_BOOKING_WORD_RE.search(t)) or any(p in t for p in _BOOKING_MULTI)


# ── FAQ / info-seeking ─────────────────────────────────────────────────────────

_FAQ_PHRASES = (
    "do you treat", "do you see", "what do you do", "what do you offer",
    "what do you treat", "what exactly", "just wanted to know",
    "just want to know", "wanted to know", "want to know",
    "just wondering", "just to ask", "just to check",
    "wondering if", "i was wondering", "need to know",
    "can i ask", "is that something", "is this something",
)


def _has_faq(t: str) -> bool:
    return any(p in t for p in _FAQ_PHRASES)


# ── Patient relationship ───────────────────────────────────────────────────────

_RELATIONSHIP_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bmy son\b",        "son"),
    (r"\bmy daughter\b",   "daughter"),
    (r"\bmy little boy\b", "son"),
    (r"\bmy little girl\b","daughter"),
    (r"\bmy child\b",      "child"),
    (r"\bmy children\b",   "child"),
    (r"\bmy kid\b",        "child"),
    (r"\bmy kids\b",       "child"),
    (r"\bmy teenager\b",   "teenager"),
    (r"\bmy teen\b",       "teenager"),
    (r"\bmy wife\b",       "wife"),
    (r"\bmy husband\b",    "husband"),
    (r"\bmy partner\b",    "partner"),
    (r"\bmy mum\b",        "mother"),
    (r"\bmy mom\b",        "mother"),
    (r"\bmy mother\b",     "mother"),
    (r"\bmy dad\b",        "father"),
    (r"\bmy father\b",     "father"),
    (r"\bmy friend\b",     "friend"),
    (r"\bmy brother\b",    "brother"),
    (r"\bmy sister\b",     "sister"),
    (r"\bmy colleague\b",  "colleague"),
)

_CHILD_RELATIONSHIPS = frozenset({"son", "daughter", "child", "teenager"})

# Phrases that indicate a child patient without the "my X" pattern
_CHILD_DIRECT = (
    "little boy", "little girl", "young boy", "young girl",
    "children", "paediatric", "pediatric",
)


# ── Explicit age ───────────────────────────────────────────────────────────────

_AGE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:he|she|they)\s*[''`]?(?:s|re)\s+(\d{1,2})\b", "pronoun_age"),
    (r"\b(\d{1,2})\s*[-\u2013]?\s*years?\s*old\b",         "years_old"),
    (r"\baged?\s+(\d{1,2})\b",                              "aged"),
    (r"\b(\d{1,2})\s*[-\u2013]?\s*yrs?\b",                 "yr"),
)


# ── Injury / reason extraction ─────────────────────────────────────────────────

_BODY_PARTS = frozenset({
    "ankle", "knee", "shoulder", "back", "neck", "hip", "wrist", "elbow",
    "foot", "feet", "leg", "arm", "calf", "hamstring", "groin",
    "achilles", "heel", "thumb", "finger", "toe", "spine", "chest",
    "quad", "quadricep", "rib", "ribs", "pelvis",
})

_INJURY_VERBS = frozenset({
    "hurt", "hurting", "pain", "painful", "ache", "aching",
    "injured", "injury", "sprain", "sprained", "strain", "strained",
    "fracture", "fractured", "twisted", "swollen", "swelling",
    "sore", "stiff", "stiffness", "torn", "pulled",
})

_TRAILING_JUNK = re.compile(r"[\s.,!?;:]+$")


def _extract_reason(t: str) -> Optional[str]:
    """
    Extract a short, usable reason/injury phrase.
    Returns None when nothing reliable is found — never hallucinates.
    """
    words = t.split()

    # Pass 1: find a body-part word → take ±3/+2 word window
    for i, w in enumerate(words):
        stem = w.rstrip(".,!?;:'s").lower()
        if stem in _BODY_PARTS:
            start = max(0, i - 3)
            end   = min(len(words), i + 3)
            snippet = _TRAILING_JUNK.sub("", " ".join(words[start:end]))
            if len(snippet) > 2:
                return snippet

    # Pass 2: injury verb → short forward context
    for i, w in enumerate(words):
        stem = w.rstrip(".,!?;:").lower()
        if stem in _INJURY_VERBS:
            start = max(0, i - 1)
            end   = min(len(words), i + 4)
            snippet = _TRAILING_JUNK.sub("", " ".join(words[start:end]))
            if len(snippet) > 2:
                return snippet

    return None


# ── Location clue ──────────────────────────────────────────────────────────────
# Conservative: only explicit clinic name / landmark mentions.
# Does NOT update selected_location — that remains the job of _switch_flow.

_LOCATION_KEYS: dict[str, str] = {
    "alcester":      "alcester",
    "greig":         "alcester",
    "kinwarton":     "alcester",
    "redditch":      "redditch",
    "bromsgrove":    "redditch",
}


# ── Urgency clues ──────────────────────────────────────────────────────────────

_URGENCY_PHRASES = (
    "as soon as possible", "asap", "as soon as", "urgently", "urgent",
    "today", "this week", "been in pain", "can't walk", "cannot walk",
    "can't move", "cannot move", "immediately",
)


# ── Clinic discovery ───────────────────────────────────────────────────────────

_DISCOVERY_PHRASES = (
    "saw you online", "found you online", "heard about you",
    "recommended", "good reviews", "your website",
    "saw online", "found online", "searched", "referred",
)


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_first_turn_signals(text: str) -> Dict[str, Any]:
    """
    Deterministically extract structured signals from a first caller utterance.
    Returns a plain dict.  Pure read — no side effects.

    Safe uncertainty rule: if a fact is not clearly present, leave it absent.
    """
    t = text.strip().lower()
    out: Dict[str, Any] = {}

    # 1. Booking intent signal
    out["first_turn_booking_signal"] = _has_booking(t)

    # 2. FAQ / info-seeking signal
    out["first_turn_faq_signal"] = _has_faq(t)

    # 3. Service-fit signal (reuses existing tested phrase list)
    out["first_turn_service_fit"] = has_service_fit_question(text)

    # 4 & 5. Patient relationship + patient_is_caller
    relationship: Optional[str] = None
    for pattern, rel in _RELATIONSHIP_PATTERNS:
        if re.search(pattern, t):
            relationship = rel
            break
    out["first_turn_patient_is_caller"] = relationship is None
    if relationship:
        out["first_turn_patient_relationship"] = relationship

    # 6. Child-related flag
    out["first_turn_child_related"] = (
        relationship in _CHILD_RELATIONSHIPS
        or any(p in t for p in _CHILD_DIRECT)
    )

    # 7. Explicit age (only when stated numerically)
    for pat, src in _AGE_PATTERNS:
        m = re.search(pat, t)
        if m:
            try:
                age_val = int(m.group(1))
                if 0 < age_val < 100:
                    out["first_turn_age"]        = age_val
                    out["first_turn_age_source"] = src
                    break
            except (ValueError, IndexError):
                pass

    # 8. Reason / injury phrase
    reason_phrase = _extract_reason(t)
    out["first_turn_reason_captured"] = bool(reason_phrase)
    if reason_phrase:
        out["first_turn_reason"] = reason_phrase

    # 9. Location clue (does NOT update selected_location)
    for key, loc_id in _LOCATION_KEYS.items():
        if key in t:
            out["first_turn_location_clue"] = loc_id
            break

    # 10. Urgency clue
    for phrase in _URGENCY_PHRASES:
        if phrase in t:
            out["first_turn_urgency"] = phrase
            break

    # 11. Clinic discovery clue
    out["first_turn_clinic_discovery"] = any(p in t for p in _DISCOVERY_PHRASES)

    return out


def apply_first_turn_signals(signals: Dict[str, Any], session: Dict[str, Any]) -> None:
    """
    Store extracted signals into session state, then emit one compact log line.

    Conservative write rules:
    - Never overwrites a field already set by earlier logic.
    - Maps first_turn_reason → canonical 'reason' field only when not already set.
    - Sets _first_turn_extracted so this only runs once per call.
    """
    for key, value in signals.items():
        if key == "first_turn_reason":
            # Populate the canonical 'reason' field used to skip COLLECT_REASON,
            # but only if it hasn't been set by something with higher confidence.
            if not session.get("reason"):
                session["reason"] = value
                # Mirror into collected["reason"] — the nested slot read by
                # build_call_summary and smart_sms_router. Without this mirror,
                # skipping COLLECT_REASON leaves collected["reason"]=None and
                # the downstream summary / SMS log shows reason=None.
                _collected = session.setdefault("collected", {})
                if not _collected.get("reason"):
                    _collected["reason"] = value
                    logger.info(
                        "[first_turn] canonical-commit reason=%r "
                        "(mirrored session['reason'] → collected['reason'])",
                        value[:60] if isinstance(value, str) else value,
                    )
            session["first_turn_reason"] = value  # always store the extracted phrase
        elif key not in session:
            session[key] = value

    session["_first_turn_extracted"] = True

    logger.info(
        "[first_turn] extract: "
        "booking=%s faq=%s service_fit=%s child=%s age=%s "
        "relation=%s patient_is_caller=%s location=%s reason=%r "
        "urgency=%s discovery=%s",
        signals.get("first_turn_booking_signal",  False),
        signals.get("first_turn_faq_signal",      False),
        signals.get("first_turn_service_fit",     False),
        signals.get("first_turn_child_related",   False),
        signals.get("first_turn_age"),
        signals.get("first_turn_patient_relationship"),
        signals.get("first_turn_patient_is_caller", True),
        signals.get("first_turn_location_clue"),
        signals.get("first_turn_reason", "")[:40] if signals.get("first_turn_reason") else None,
        signals.get("first_turn_urgency"),
        signals.get("first_turn_clinic_discovery", False),
    )
