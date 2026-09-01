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


# ── Hardening (2026-08-02, B-23) ───────────────────────────────────────────────
# Pass 1 matched BARE body-part words, which is unsafe for the two entries that
# are also ordinary English. Measured against 967 stored caller turns, "back"
# collided once — "hi can you call me back later" captured
# reason='you call me back later'.
#
# The clinical screening config solved this same problem and did not use bare
# words: cauda_equina keys on "my back" / "back pain" / "sore back", never
# "back". These rules inherit that discipline for the ambiguous words only.
# "knee", "shoulder", "ankle" and the rest are unambiguous nouns and are left
# exactly as they were — narrowing them would cost true positives for nothing.
#
# The other three rules are fail-OPEN: on any signal that the captured part is
# not the caller's own single presenting complaint, capture nothing and let the
# question be asked. An extra question costs a turn; a wrong reason picks the
# wrong service (jv_v1 has ten, 30-60 min) and can satisfy book_appointment's
# reason guard by accident.
_AMBIGUOUS_PARTS = {"back", "arm"}

_BACK_ANATOMICAL = tuple(re.compile(p) for p in (
    r"\b(?:my|his|her|their|the)\s+(?:lower\s+|low\s+|upper\s+)?back\b",
    r"\b(?:lower|low|upper)\s+back\b",
    r"\bback\s+(?:pain|ache|is|was|'s|s)\b",
    r"\b(?:sore|bad|stiff|aching|dodgy)\s+back\b",
))
# "the back of my legs" is POSITIONAL — the anatomy is the legs. Treating it as
# the anatomical back cost a real capture in the corpus: "the back of my legs
# kind of warm" windowed onto 'the back of my' and lost the leg entirely. That
# phrasing is also the DVT screen's presentation, so it is the last one to lose.
_BACK_POSITIONAL = re.compile(r"\bback\s+of\s+(?:my|his|her|the)\b")

_ARM_ANATOMICAL = tuple(re.compile(p) for p in (
    r"\b(?:my|his|her|their|the)\s+(?:upper\s+|left\s+|right\s+)?arm\b",
    r"\barm\s+(?:pain|ache|is|was|'s|s)\b",
    r"\b(?:sore|bad|stiff|aching)\s+arm\b",
))

# NOT guarded here: a third-party complaint ("my son hurt his ankle"). The
# planning note for B-23 proposed failing open on it, and that was wrong —
# extract_first_turn_signals already answers "whose complaint is this?" with a
# dedicated signal, first_turn_patient_is_caller, which reads False on exactly
# those utterances. Capturing "ankle" there is correct and intended: the child
# policy gate needs the reason for a paediatric booking, and two existing tests
# (test_ankle_body_part, test_booking_plus_child) assert it.
#
# Attribution is a CONSUMER question, not an extraction one. Whatever wires this
# into the v3 path must read first_turn_patient_is_caller alongside the reason
# rather than expecting the reason to be absent.


def _part_stem(word: str) -> str:
    """Lowercase and strip trailing punctuation/possessive. Unchanged semantics."""
    return word.rstrip(".,!?;:'s").lower()


def _usable_body_parts(text_low: str, words: list) -> set:
    """Body-part words in `words` that are actually being used anatomically."""
    found = {w for w in (_part_stem(x) for x in words) if w in _BODY_PARTS}
    if "back" in found:
        anatomical = (
            any(p.search(text_low) for p in _BACK_ANATOMICAL)
            and not _BACK_POSITIONAL.search(text_low)
        )
        if not anatomical:
            found.discard("back")
    if "arm" in found and not any(p.search(text_low) for p in _ARM_ANATOMICAL):
        found.discard("arm")
    return found


def _extract_reason(t: str) -> Optional[str]:
    """
    Extract a short, usable reason/injury phrase.
    Returns None when nothing reliable is found — never hallucinates.
    """
    words = t.split()
    text_low = t.lower()

    parts = _usable_body_parts(text_low, words)

    # ── Fail-open guards ──────────────────────────────────────────────────
    # Two distinct complaints: we cannot tell which is THE reason, and picking
    # the first-mentioned is a coin toss. ("back of my legs" is one locus, not
    # two — _usable_body_parts has already dropped the positional "back".)
    if len(parts) > 1:
        return None
    # An explicit correction: "not my knee, it's my hip".
    for p in parts:
        if re.search(r"\b(?:not|isn'?t)\s+(?:my\s+)?" + re.escape(p) + r"\b", text_low):
            return None

    # Pass 1: body-part word → take ±3/+2 word window. Restricted to the usable
    # set, so a discarded "back" can no longer anchor the window.
    if parts:
        for i, w in enumerate(words):
            if _part_stem(w) in parts:
                start = max(0, i - 3)
                end   = min(len(words), i + 3)
                snippet = _TRAILING_JUNK.sub("", " ".join(words[start:end]))
                if len(snippet) > 2:
                    return snippet

    # Pass 2: injury verb → short forward context. Deliberately unchanged: it
    # is what still captures a complaint whose body part the STT mangled
    # ("my call's been very sore" — calf), and it anchors on the symptom, not
    # on a word that might not be anatomy.
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


# ── The caller's opening utterance, read on the LIVE path ─────────────────────
# Everything above this line is reachable only through FlowEngine
# (`apply_first_turn_signals` has exactly two callers, both in flow.py), and
# FlowEngine is bypassed on every live clinic. So on a real call the canonical
# `reason` slot was NEVER populated from the opening utterance — the only live
# writer is the A2 gate inside `book_appointment`, which runs many turns later.
#
# What that cost (measured over 683 stored calls, 2026-09-01): on 33 calls the
# caller opened with the reason — "i'd like to book please it's for knee pain"
# — and was asked "What's the appointment for?" anyway. The guard meant to
# prevent it, `_reason_already_known`, reads three keys and all three are empty
# at the moment the injector decides:
#
#   session["reason"] / collected["reason"]  -> written only by the A2 gate
#   soft_context["condition_notes"]          -> written by a fire-and-forget
#                                               Haiku task launched during the
#                                               SAME turn; it cannot have landed
#
# The guard was not wrong, it was starved. These helpers feed it from the one
# signal that is available synchronously and needs no model: the caller's own
# first sentence, through the same deterministic extractor Theorem already uses.
#
# Measured on the 556 in-scope (jv_v1 + northgate) openings before shipping:
# `_extract_reason` fired on 133 with no false positive and no false negative,
# and `_has_booking` on 268 with none of either. Both fail closed — an opening
# naming two body parts, or a correction ("not my knee, it's my hip"), returns
# None and the question is asked as before.


def opening_utterance(session: Dict[str, Any]) -> str:
    """The caller's first utterance of the call, or "" before one has arrived.

    Recorded by run_turn (llm_stream Step 5) rather than read back out of
    conversation_history, because history is appended AFTER the turn completes:
    on turn 1 — the only turn that matters here — it is still empty when the
    system prompt is built.
    """
    return (session.get("opening_utterance") or "").strip()


def opening_reason(session: Dict[str, Any]) -> Optional[str]:
    """The reason the caller gave in their opening utterance, if any.

    Pure and cached: `_extract_reason` is deterministic, so the result is
    memoised per call under a private key rather than recomputed on every
    prompt render and every gate check.
    """
    text = opening_utterance(session)
    if not text:
        # Nothing to read yet. Deliberately NOT cached: caching None here would
        # freeze the answer for the whole call, and the opening utterance is
        # recorded a moment later on the very first turn.
        return None
    if session.get("_opening_reason_cache_for") == text:
        return session.get("_opening_reason_cache")
    reason = _extract_reason(text.lower())
    session["_opening_reason_cache"] = reason
    session["_opening_reason_cache_for"] = text
    return reason


def opening_had_booking_intent(session: Dict[str, Any]) -> bool:
    """True when the caller ASKED TO BOOK in their opening utterance.

    Separate from `opening_reason` because the two answer different questions
    and only their CONJUNCTION is the condition-led opening that BOOKING STEPS
    1 mishandles: a caller who describes a complaint wants an offer, a caller
    who describes a complaint AND asks to be booked has already accepted one.
    """
    text = opening_utterance(session)
    if not text:
        return False
    if session.get("_opening_booking_cache_for") == text:
        return session.get("_opening_booking_cache")
    intent = bool(_has_booking(text.lower()))
    session["_opening_booking_cache"] = intent
    session["_opening_booking_cache_for"] = text
    return intent


def commit_opening_reason(session: Dict[str, Any]) -> bool:
    """Record the opening reason into the canonical slots. Returns True if known.

    The WRITE is not incidental — it is the safety half of the fix, and
    suppressing the question without it is the failure this must not repeat.
    `book_appointment`'s A2 gate refuses any booking that carries no reason and
    its refusal text tells the model to ask "What's the appointment for?" — a
    phrasing Gate 5b-r then strips. Suppress-without-record therefore does not
    save a turn, it deadlocks the booking and loops the caller.

    Never overwrites: a reason the caller stated later, or one the model
    collected, was said with more deliberation than an opening aside.
    """
    reason = opening_reason(session)
    if not reason:
        return False
    if not (session.get("reason") or "").strip():
        session["reason"] = reason
        logger.info(
            "[first_turn] opening reason committed on the live path: %r",
            reason[:60],
        )
    collected = session.setdefault("collected", {})
    if isinstance(collected, dict) and not (collected.get("reason") or "").strip():
        collected["reason"] = reason
    return True


def opening_is_substantive(text: str) -> bool:
    """True when *text* is worth latching as the caller's opening.

    A bare "hi" is not an opening, it is a greeting, and latching it spends the
    one shot this mechanism gets on a turn that says nothing. 77 of the 556
    in-scope stored openings look like that ("hi", "hi there", and STT
    fragments such as "hi i'd like to").

    Substantive means: it carries a signal we can act on, or it is long enough
    that the caller has plainly said something. Kept deliberately loose - the
    caller of this function bounds how many turns it may defer, so a wrong
    "not substantive" costs one turn, never the call.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    return bool(_extract_reason(t)) or bool(_has_booking(t)) or len(t.split()) > 4
