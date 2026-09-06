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
    """Lowercase, strip trailing punctuation, then the possessive/plural.

    `rstrip` takes a CHARACTER SET, not a suffix, so the original
    `rstrip(".,!?;:'s")` also ate a terminal 's' that is part of the word.
    Two entries of `_BODY_PARTS` were therefore unreachable:

        'achilles' -> 'achille'   'pelvis' -> 'pelvi'

    Neither stem is in the set, so Pass 1 never anchored on them. B-141,
    CA60cb41a0 (5 Sep 2026, northgate): "my achilles is stiff the first few
    minutes every morning" fell through to the injury-verb pass, which
    anchors on 'stiff', and the call record read

        reason: 'stiff the first few minutes every'

    -- a complaint with no body part in it at all.

    The vocabulary is consulted BEFORE the possessive strip, so this can only
    ADD matches: a word that already names a part is returned as it stands,
    and everything else strips exactly as before ("back's" -> "back",
    "knees" -> "knee").
    """
    w = word.rstrip(".,!?;:").lower()
    if w in _BODY_PARTS:
        return w
    return w.rstrip("'s")


# Spinal loci, and the signs that a limb is being named as REFERRAL from one
# rather than as a second complaint. Both halves are required -- a spinal part
# alone, or a limb with numbness alone, changes nothing.
_SPINAL_PARTS: frozenset = frozenset({"back", "spine", "neck"})
_REFERRAL_SIGNS: tuple = (
    "numb", "numbness", "tingling", "tingle", "pins and needles",
    "shooting", "radiating", "radiates", "sciatica", "sciatic",
)


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
    # A limb named alongside the spine, with a referral sign, is ONE locus.
    #
    # `_extract_reason` fails open on two distinct complaints because picking
    # the first-mentioned is a coin toss -- that guard is deliberate and stays.
    # But back-pain-with-leg-numbness is not two complaints, it is lumbar
    # radiculopathy: the spine is the locus and the leg is the referral. The
    # same distinction this function already makes for "the back of my legs",
    # which is one locus spelled with two part words.
    #
    # B-141, CA6b241e20 (5 Sep 2026, northgate) and CAcb51bc27 before it. The
    # caller opened with "my lower back's been really bad and my leg's gone
    # numb"; parts resolved to {back, leg}; the two-complaint guard fired; and
    # the call ended `pre-summary reason: collected=None session=None`. No
    # reason at all, on a call whose first sentence was the reason -- which
    # also empties the Sheets row, the follow-up SMS, and starves
    # `book_appointment`'s A2 gate.
    #
    # Both halves are required, so a genuine pair ("my knee and my ankle are
    # both sore") is untouched and still captures nothing.
    _spinal = found & _SPINAL_PARTS
    if len(_spinal) == 1 and len(found) > 1:
        if any(sign in text_low for sign in _REFERRAL_SIGNS):
            found = set(_spinal)
    return found


# Words the reason phrase should not START or END on. The window below is a
# fixed span around the body part, so it routinely opens on the run-up ("for
# my left shoulder") and closes mid-clause ("...it's been"). Neither carries
# meaning, and the fragment is what an operator reads on the call record.
#
# Measured over the 556 stored in-scope openings: trimming changes the TEXT on
# 103 and the fire/no-fire DECISION on none. That invariant is the point - the
# decision is what suppresses the reason question, and it is verified live.
_REASON_LEAD_WORDS = frozenset({
    "for", "my", "the", "a", "an", "i", "i'd", "id", "like", "to", "book",
    "booking", "appointment", "um", "uh", "erm", "yeah", "yes", "hi", "hello",
    "please", "it's", "its", "it", "and", "with", "about", "of", "in", "on",
    "got", "get", "just", "really", "been", "is", "was", "have", "having",
    "need", "want", "see", "someone", "there", "so", "well", "that", "this",
    "me", "looking",
})
_REASON_TRAIL_WORDS = frozenset({
    "it's", "its", "it", "is", "was", "been", "and", "the", "my", "for", "a",
    "an", "to", "that", "this", "of", "with", "but", "really", "i", "i'd",
    "id", "i've", "ive", "i'm", "im", "kind", "sort", "bit", "very", "quite",
    "so", "just", "want", "get", "please", "looking", "think", "like",
})

# Where the caller stops DESCRIBING and starts TRANSACTING. Without this the
# forward window swallows the booking clause: "tight hamstring from running"
# became "tight hamstring from running and i'd like".
_REASON_STOP_WORDS = frozenset({
    "book", "booking", "booked", "appointment", "appointments", "schedule",
    "slot", "can", "could", "would", "i'd", "id", "please", "available",
    "availability",
})


def _bare(w: str) -> str:
    return w.strip(".,!?;:").lower()


def _reason_window(words: list, start: int, end: int) -> str:
    """Join words[start:end], stopping early at a transactional word."""
    out = []
    for j in range(start, end):
        if j > start and _bare(words[j]) in _REASON_STOP_WORDS:
            break
        out.append(words[j])
    return " ".join(out)


def _trim_reason(phrase: str) -> str:
    """Drop leading run-up and trailing dangle from a reason phrase."""
    w = phrase.split()
    while w and _bare(w[0]) in _REASON_LEAD_WORDS:
        w.pop(0)
    while w and _bare(w[-1]) in _REASON_TRAIL_WORDS:
        w.pop()
    return " ".join(w).strip()


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
                end   = min(len(words), i + 10)
                snippet = _trim_reason(
                    _TRAILING_JUNK.sub("", _reason_window(words, start, end))
                )
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
            end   = min(len(words), i + 6)
            snippet = _trim_reason(
                _TRAILING_JUNK.sub("", _reason_window(words, start, end))
            )
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


def note_opening_utterance(session: Dict[str, Any], text: Any) -> None:
    """Latch the caller's OPENING utterance, once. PURE apart from the write.

    Extracted from llm_stream's turn loop on 2026-09-04 so it has ONE
    definition. It had one caller and needed two: a turn that arms a clinical
    screen is answered by the `ask_screen` short-circuit in connection.py,
    which returns before the LLM turn ever runs -- so on a call where the
    caller opens with their complaint, the opening was never latched and every
    later `commit_opening_reason` had nothing to read.

    CAdd64c466 (northgate, 4 Sep 2026) is the call. He opened with "i'd like to
    book an appointment essentially i was playing football um and i rolled my
    ankle", which armed trauma_fracture and was consumed there. The call
    finished with `pre-summary reason: collected=None session=None` -- no
    reason at all, on a call whose very first sentence was the reason.

    Set once and never overwritten: "opening" means the first thing the caller
    said, not the most recent. A bare "hi" is a greeting, not an opening, and
    latching it would spend the one shot this gets on a turn that says nothing
    -- so defer past at most two such turns, then take whatever arrives. An
    unbounded search would let a quiet caller move the "opening" to the middle
    of the call, which is not what any reader of it expects.
    """
    ou = str(text or "").strip()
    if not ou or session.get("opening_utterance"):
        return
    probes = int(session.get("_opening_probe_count") or 0)
    if opening_is_substantive(ou) or probes >= 2:
        session["opening_utterance"] = ou
    else:
        session["_opening_probe_count"] = probes + 1


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


# ── The caller's ANSWER to the reason question, read on the LIVE path ─────────
# `commit_opening_reason` above covers the caller who states the reason in their
# OPENING sentence. It cannot cover the caller who states it in reply to the
# question itself, because on a free-form clinic nothing writes the canonical
# slot mid-call: the only live writers are that helper (first turn) and the A2
# gate inside `book_appointment` (many turns later).
#
# CAea8abdb (2 Sep 2026, Vital Edge, live) is what that costs. Susie asked
# "Is there a particular area or concern you're looking to address?", the caller
# answered "I'm a full-time athlete and I just need some recovery work", and one
# turn later she asked the clinic's mandated reason question anyway. Nothing had
# recorded the answer, so `_reason_already_known` was still False and the model's
# prompt still said it did not know what the appointment was for.
#
# Recording it is also what makes SUPPRESSING the second ask safe. Gate 5b-r
# will only strip a re-ask once while no reason is on record, precisely because
# stripping without recording deadlocks `book_appointment`'s A2 gate. With the
# answer captured, `_reason_on_record` is True and the strip is unconditional.

# Replies that answer nothing. A caller who says "yes" has not told us what the
# appointment is for, and writing that into the booking is worse than writing
# nothing: the A2 gate would pass and the calendar entry would read "yes".
_REASON_NON_ANSWERS: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "no", "nope", "nah", "ok", "okay",
    "sure", "right", "correct", "please", "thanks", "thank you",
    "um", "uh", "erm", "hmm", "sorry", "what", "pardon", "say that again",
    "i don't know", "i dont know", "not really", "not sure", "no idea",
})

# How many caller turns the pending flag may survive. The answer is normally the
# very next turn; two allows for one filler ("um, hang on") without letting the
# flag drift onto an answer to some later, unrelated question.
_REASON_ANSWER_MAX_TURNS = 2


def utterance_is_reason_answer(session: Dict[str, Any], utterance: str) -> bool:
    """True when `commit_reason_answer` would treat *utterance* as the reply.

    Pure. Reads the three fields that helper keys on and writes none of
    them, so merely ASKING the question cannot consume a caller's reason.

    Exists because the answer to "what's the appointment for?" is the one
    utterance in the call most likely to contain a time word that is not a
    scheduling request -- a symptom is very often described BY its timing.
    Callers say "stiff every morning", "worse at night", "I did it on
    Saturday". Those are the complaint, not a preference, and the capture
    block in connection.py had no way to tell the difference.

    B-138, CA04219aeb (5 Sep 2026, northgate). Asked what the appointment
    was for, the caller said:

        "yeah my achilles is stiff for the first few minutes every morning
         and it eases as i walk"

    which banked a HARD mornings preference, sent
    date_hint="mornings" into check_availability, and offered six slots of
    which every one was AM. The caller had expressed no preference at all;
    "every morning" was the diagnostic detail that makes it tendinopathy.

    Deliberately spans the WHOLE pending window rather than only the very
    next turn: `_REASON_ANSWER_MAX_TURNS` allows one filler, so a caller
    who says "um" and then describes the complaint reaches this code on
    the second turn, and a turn-zero gate would miss exactly that caller.
    Kept in step with the consumer by BEING the consumer's test --
    `commit_reason_answer` calls this, it does not restate it.
    """
    if not session.get("_reason_answer_pending"):
        return False
    armed_on = session.get("_reason_answer_armed_on")
    if armed_on is None:
        # The arming turn itself. `commit_reason_answer` has not yet
        # recorded which utterance provoked the question, so nothing is
        # pending an answer yet.
        return False
    return (utterance or "") != armed_on


def utterance_is_opening_reason(session: Dict[str, Any], utterance: str) -> bool:
    """True when *utterance* is the caller's opening AND carries a complaint.

    The other half of [[B-138]]. `utterance_is_reason_answer` covers the
    caller who is ANSWERING "what's the appointment for?". This covers the
    caller who never had to be asked, because they opened with it -- and on
    the live calls that is the commoner phrasing of the two.

    B-138 second attempt, CA556c7e20 (5 Sep 2026, northgate). The first fix
    shipped and the defect reproduced verbatim the same evening:

        09:21:43.615  time_of_day_preference captured: mornings (tier=hard,
                      from utterance 'um yeah my achilles is stiff for the
                      first few minutes every morning and eases as i walk')
        09:21:43.616  [first_turn] opening reason committed on the live path
        09:22:03.078  check_availability ... date_hint="mornings"

    -- six slots offered, every one AM. One millisecond separates the timing
    capture from the reason commit claiming the SAME utterance. The reason
    question was never asked, so `_reason_answer_pending` never armed and the
    first gate was correct but inert. Same defect, same sentence, other door.

    Reads the UTTERANCE, not a latch, and that is load-bearing:
    `note_opening_utterance` runs inside run_turn, which is AFTER this is
    asked, so on turn 1 `opening_utterance` is still unset when the capture
    happens. A predicate keyed on the latch would be inert exactly when it
    is needed -- which is the mistake this function exists to correct.

    `opening_utterance` being ALREADY set is therefore the negative test: it
    means an opening was latched on some earlier turn and this one is not it.
    A caller who opens with a bare "hi" defers the latch (see
    `note_opening_utterance`), so their complaint on the next turn is still
    correctly treated as the opening.
    """
    if session.get("opening_utterance"):
        return False
    text = (utterance or "").strip()
    if not text or not opening_is_substantive(text):
        return False
    return bool(_extract_reason(text.lower()))


def _reason_on_record(session: Dict[str, Any]) -> bool:
    """True when this call already has a booking reason recorded.

    The same pair of slots `commit_reason_answer` consults before it declines
    to overwrite. Named here so the volunteered-complaint door below and that
    consumer cannot drift to two different answers to "do we know yet?".
    """
    if (session.get("reason") or "").strip():
        return True
    collected = session.get("collected")
    return bool(
        isinstance(collected, dict) and (collected.get("reason") or "").strip()
    )


def utterance_is_volunteered_reason(
    session: Dict[str, Any], utterance: str
) -> bool:
    """True when the caller has just VOLUNTEERED their complaint mid-call.

    The third door. The first two -- `utterance_is_opening_reason` and
    `utterance_is_reason_answer` -- between them cover the caller who leads
    with the complaint and the caller who is replying to "what's the
    appointment for?". Neither covers the caller who does neither, and until
    5 Sep 2026 nothing had to: the clinical-screening short-circuit in
    connection.py captured that caller as a side effect, because an utterance
    describing a complaint is exactly what arms a screen (B-136/B-137).

    Turning the screens off on jv_v1 (`ed7f5c0c`) removed that capture path,
    and the gap became reachable on a patient line the same night.

    5 Sep 2026, JV, build ed7f5c0ce1c0. The caller opened with "um yeah i'd
    like to know about pricing at your clinic" -- so `opening_utterance`
    latched the FAQ, one-shot and never overwritten, and carried no
    complaint. On turn 3 he said

        "okay um yeah essentially my lower back's been really bad and my leg's
         gone numb"

    Susie went straight from empathy to the booking offer, so the reason
    question was never asked and `_reason_answer_pending` never armed. Both
    existing doors were correct and both were inert. The call ended

        pre-summary reason: collected=None session=None -> None

    and, JV's Sheets credentials working where the demo line's do not, Marcus
    got a 101-second row with a name, a number and no reason on it.

    Two conditions, and the second is what bounds this. There must be no
    reason on record -- so this fires at most ONCE per call and cannot
    overwrite a reason stated with more deliberation -- and `_extract_reason`
    must actually find a complaint. Requiring an extraction is also what
    excludes the bare booking request: "i'd like to book an appointment" says
    what the caller wants done, not what it is for, and `_has_booking`
    without `_extract_reason` is precisely that shape.

    Deliberately NOT gated on the turn number. The whole failure is that the
    complaint arrived on a turn nobody was watching.
    """
    if _reason_on_record(session):
        # ...unless this very utterance is the one that put it there. The
        # scheduling capture in connection.py asks this predicate BEFORE
        # run_turn commits (12295 vs 12829), so on the live ordering the
        # slot is still empty here. The latch makes the answer independent
        # of that ordering rather than quietly dependent on it -- the same
        # shape `_reason_answer_armed_on` uses for the same reason.
        return (utterance or "") == (session.get("_volunteered_reason_from") or "")
    text = (utterance or "").strip()
    if not text:
        return False
    return bool(_extract_reason(text.lower()))


def commit_volunteered_reason(session: Dict[str, Any], utterance: str) -> bool:
    """Record a complaint the caller volunteered mid-call. True if one landed.

    Calls the predicate rather than restating it, so the capture that
    SUPPRESSES the timing read and the write that RECORDS the reason can
    never disagree about which utterance was the reason -- the drift that
    made B-138 reproduce verbatim the evening its first fix shipped.

    The write is the point, not a nicety: `book_appointment`'s A2 gate
    refuses any booking carrying no reason, and jv_v1 opts into the reason
    question (`clinic.json` `prompt_facts.reason_question`), so on that line
    a reasonless call is a booking that can only be rescued by the model
    passing `args["reason"]` itself. Recording it here removes the coin toss.
    """
    if not utterance_is_volunteered_reason(session, utterance):
        return False
    if _reason_on_record(session):
        # The latch arm above matched -- already recorded, on this same
        # utterance, earlier in this same turn. Nothing left to do.
        return True

    reason = (utterance or "").strip()[:200]
    session["reason"] = reason
    session["_volunteered_reason_from"] = utterance or ""
    collected = session.setdefault("collected", {})
    if isinstance(collected, dict) and not (collected.get("reason") or "").strip():
        collected["reason"] = reason
    logger.info(
        "[first_turn] reason captured from a volunteered complaint: %r",
        reason[:60],
    )
    return True


def utterance_is_read_as_the_reason(
    session: Dict[str, Any], utterance: str
) -> bool:
    """True when this utterance is the caller telling us why they rang.

    The single question the scheduling captures in connection.py ask, so a
    complaint cannot be read as a booking preference through ANY door.
    All three doors are one call: fixing one and shipping is what put the
    AM-only filter back on a live call after B-138's first attempt.
    """
    return (
        utterance_is_reason_answer(session, utterance)
        or utterance_is_opening_reason(session, utterance)
        # The third door. Widens this to "any complaint-bearing utterance
        # while no reason is on record", which suppresses at most ONE turn's
        # timing latch per call. Safe in the direction B-90 measured and
        # `_time_preference_tier` documents: the cost of declining a
        # preference is one re-ask, the cost of banking one the caller never
        # stated is a filter that silently deletes real slots.
        or utterance_is_volunteered_reason(session, utterance)
    )

def commit_reason_answer(session: Dict[str, Any], utterance: str) -> bool:
    """Record the caller's reply to the reason question. True if a reason landed.

    Armed by `note_reason_question_asked` when Susie actually ASKS, so it can
    never fire on a turn that was answering something else. Consumes its flag.

    Never overwrites a reason already on record — one the caller volunteered
    earlier, or the model collected, was said with more deliberation than a
    reply extracted here.
    """
    if not session.get("_reason_answer_pending"):
        return False

    # The arming turn is NOT the answering turn. `note_reason_question_asked`
    # fires while the reply is being composed, and this helper runs later in
    # that SAME turn — so without this the flag is consumed against the very
    # utterance that PROVOKED the question. Observed live on CA20ed370
    # (2 Sep 2026): the question latched at 22:01:48.546 and 3ms later the
    # "answer" recorded was "um yeah hi there i'd like to book an appointment
    # please". The real answer arrived eight seconds later and was dropped,
    # because a reason already on record is never overwritten.
    #
    # Keyed on the utterance rather than a turn counter so it does not depend
    # on where in the turn this is called from. If the call order ever moves
    # ahead of the reply, the cost is one turn of delay, never a junk capture.
    if session.get("_reason_answer_armed_on") is None:
        session["_reason_answer_armed_on"] = utterance or ""
        return False
    # Equivalent to the inline compare this replaces: pending is True and
    # armed_on is non-None by the two checks above, so the predicate
    # reduces to exactly that compare. Routed through the helper so the
    # capture block in connection.py and this consumer can never drift to
    # two different answers to "is this the reason answer?".
    if not utterance_is_reason_answer(session, utterance):
        return False

    collected = session.get("collected")
    already = bool((session.get("reason") or "").strip()) or bool(
        isinstance(collected, dict) and (collected.get("reason") or "").strip()
    )
    if already:
        session.pop("_reason_answer_pending", None)
        return False

    turns = int(session.get("_reason_answer_turns") or 0) + 1
    session["_reason_answer_turns"] = turns

    text = (utterance or "").strip()
    stripped = text.lower().rstrip("?.!,").strip()
    # A booking REQUEST is not a reason. "I'd like to book an appointment"
    # says what the caller wants done, not what it is for, and writing it into
    # the booking satisfies the A2 gate with nothing — the calendar entry then
    # reads back as the caller's own request. Both helpers are the measured
    # ones used on the opening utterance, so this costs no new vocabulary.
    _bare_booking = bool(_has_booking(stripped)) and not _extract_reason(stripped)
    if not text or stripped in _REASON_NON_ANSWERS or _bare_booking:
        # Not an answer. Keep waiting, but only within the bound — a flag left
        # armed for the rest of the call would eventually capture a reply to a
        # different question entirely.
        if turns >= _REASON_ANSWER_MAX_TURNS:
            session.pop("_reason_answer_pending", None)
            logger.info(
                "[first_turn] reason answer not given within %d turns — "
                "pending flag dropped", _REASON_ANSWER_MAX_TURNS,
            )
        return False

    session.pop("_reason_answer_pending", None)
    reason = text[:200]
    session["reason"] = reason
    _collected = session.setdefault("collected", {})
    if isinstance(_collected, dict) and not (_collected.get("reason") or "").strip():
        _collected["reason"] = reason
    logger.info(
        "[first_turn] reason captured from the caller's answer: %r", reason[:60],
    )
    return True
