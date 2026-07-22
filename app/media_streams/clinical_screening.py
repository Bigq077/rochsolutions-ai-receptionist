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
    """Lowercase, DELETE apostrophes, blank out other punctuation, collapse space.

    Apostrophes are DELETED (not preserved, not replaced with a space) so that
    "can't" -> "cant" rather than "can t". Speech-to-text drops apostrophes
    unpredictably, and 17 of the jv_v1 screening keywords are contractions
    ("can't breathe", "can't feel", "can't put weight", "grip's gone"). While
    this function preserved them, a transcript of "I cant breathe" did not match
    the "can't breathe" emergency keyword and the deterministic 999 intercept
    did not fire (P1 #4, Jules's 14-call sweep). Deleting on BOTH sides of every
    comparison — every caller here normalises the keyword through _norm too —
    makes matching independent of how the transcriber punctuated the word.

    Both the straight (U+0027) and curly (U+2019) forms are removed. The curly
    form was previously replaced with a space, splitting the word in two, so it
    was broken in a second, quieter way — worth knowing if a transcript ever
    arrives with smart quotes applied.
    """
    t = (text or "").lower()
    t = t.replace("'", "").replace("’", "")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Inflectional endings a keyword may pick up and still be the same word.
# Whitelisted, not "any letters": 'numb'+'ness' is the same concept, 'numb'+'er'
# is a different word entirely and was the source of a false escalation.
_KW_INFLECTION = r"(?:s|es|ed|ing|ness)?"


def _kw_in(keyword: str, text_norm: str) -> bool:
    """Word-boundary containment of `keyword` in ALREADY-normalised text.

    Plain `keyword in text` matched inside unrelated words, and several jv_v1
    keywords are short common fragments. The dangerous direction was red-flag
    ANSWER classification, where a match escalates and blocks booking for the
    rest of the call:

        'red'  matched tired / recovered / referred / worried
        'numb' matched number
        'hot'  matched photo / shot
        'fell' matched fellow          (trigger side: armed the trauma screen)

    The keyword must START at a word boundary, which is what kills all of the
    above. It may be followed by a common INFLECTIONAL suffix, because a strict
    boundary on both ends silently loses the clinical vocabulary itself:

        'numb'  must still match 'numbness'   <- the cauda equina positive
        'fever' must still match 'fevers'
        'crack' must still match 'cracked'

    but must NOT match 'number'. Hence a whitelist of suffixes rather than "any
    trailing letters": 'ness' is inflection, 'er' is a different word. Missing a
    cauda positive is the unbounded-harm case, so this half matters as much as
    the over-escalation half.

    Irregular plurals are NOT handled ('calf' will not match 'calves'); those
    belong in the clinic config as their own keywords.
    """
    k = _norm(keyword)
    if not k:
        return False
    return re.search(rf"(?<!\w){re.escape(k)}{_KW_INFLECTION}(?!\w)", text_norm) is not None


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
    return any(_kw_in(k, t) for k in kws)


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
            any(_kw_in(k, text_norm) for k in group) for group in groups if group
        )
    return any(
        _kw_in(k, text_norm) for k in (screen.get("trigger_keywords") or [])
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


# NB: normalised through _norm at import. This is the one literal set compared
# RAW against already-normalised text (see classify_screen_answer), so the
# contractions below — "everything's fine", "i haven't", "i don't" — would stop
# matching the moment _norm started deleting apostrophes. Left readable in
# source; normalised once here. A missed negative is not harmless: it downgrades
# a clear "no" to `unclear`, which leaves the screen pending and blocks a
# legitimate booking.
_NEGATIVE_PATTERNS = tuple(
    _norm(p)
    for p in (
        "no", "nope", "nah", "none", "neither", "nothing like that",
        "nothing of the sort", "no nothing", "not that i", "no changes",
        "no change", "all fine", "everything's fine", "everything is fine",
        "i haven't", "i have not", "i don't", "i do not", "definitely not",
        "not at all", "thankfully not", "luckily not",
    )
)


def _red_flag_hits(text: str, screen: Dict[str, Any]) -> int:
    """Number of DISTINCT red-flag keywords present in the text.

    Used only by the already-answered guard, which needs stronger evidence than
    answer-classification does. When the caller is REPLYING to the screen
    question, one keyword is decisive ("swollen" = yes to "is it swollen?").
    When they are merely DESCRIBING the problem, one keyword is weak — "my calf
    is painful and swollen" is an ordinary strain, whereas "swollen and warm,
    just the one leg" is the DVT picture. Requiring two independent signals
    keeps the unprompted escalation specific.
    """
    t = _norm(text)
    return sum(1 for k in (screen.get("red_flag_answer_keywords") or [])
               if _kw_in(k, t))


def classify_screen_answer(text: str, screen: Dict[str, Any]) -> str:
    """Classify the caller's reply to a screen question:
    'red_flag' | 'clear' | 'unclear'.

    Red-flag keywords are checked FIRST — an answer like "no feeling in my
    legs" contains 'no' but is a positive."""
    t = _norm(text)
    if not t:
        return "unclear"
    for k in screen.get("red_flag_answer_keywords") or []:
        if _kw_in(k, t):
            return "red_flag"
    first_word = t.split()[0] if t.split() else ""
    if first_word in ("no", "nope", "nah", "none", "neither"):
        return "clear"
    if any(p in t for p in _NEGATIVE_PATTERNS):
        return "clear"
    return "unclear"


def _resolve_screen_answer(
    session: Dict[str, Any],
    clinic: Dict[str, Any],
    screen_id: str,
    screen: Dict[str, Any],
    text: str,
) -> Dict[str, Any]:
    """Classify the caller's reply to an ASKED screen and update state.

    Shared by both entry points: a screen this module armed and asked, and a
    screen the PROMPT layer asked (where pending_screen was never set).
    Returns the same {"action", "speak"} contract as update_screening_state.
    """
    verdict = classify_screen_answer(text, screen)

    if verdict == "red_flag":
        session[PENDING_SCREEN_KEY] = None
        # block_booking (default True): a positive answer freezes booking until
        # urgent care. Advisory screens (e.g. the inflammatory-pattern flag) set
        # block_booking=false — the escalation is spoken but booking may
        # continue, because physio alongside a GP review is appropriate.
        if screen.get("block_booking", True):
            session[SCREEN_RED_FLAG_KEY] = screen_id
        done = list(session.get(SCREENS_COMPLETED_KEY) or [])
        if screen_id not in done:
            done.append(screen_id)
        session[SCREENS_COMPLETED_KEY] = done
        logger.info(
            "[clinical_screening] screen %s POSITIVE (block=%s): %r",
            screen_id, screen.get("block_booking", True), text[:80],
        )
        return {
            "action": "escalate",
            "speak": screen.get("escalation") or emergency_response_text(clinic),
        }

    if verdict == "clear":
        session[PENDING_SCREEN_KEY] = None
        done = list(session.get(SCREENS_COMPLETED_KEY) or [])
        if screen_id not in done:
            done.append(screen_id)
        session[SCREENS_COMPLETED_KEY] = done
        logger.info(
            "[clinical_screening] screen %s clear: %r", screen_id, text[:80]
        )
        # The LLM turn acknowledges ("that's reassuring") and moves on.
        return {"action": "none", "speak": None}

    # unclear — leave pending; the SCREEN REQUIRED steer re-drives the question.
    logger.info(
        "[clinical_screening] screen %s answer unclear: %r", screen_id, text[:80]
    )
    return {"action": "none", "speak": None}


# Meaningful short words that are legitimate screen answers and must never be
# treated as STT debris (mirrors the intent of connection.py's _V3_PRESERVE).
_MEANINGFUL_SHORT = frozenset({
    "yes", "no", "yeah", "nope", "yep", "yup", "nah",
    "ok", "okay", "sure", "fine", "none",
})

# Known mouth-noise / stutter artefacts (subset of connection.py's
# _V3_NOISE_FRAGMENTS — kept local so this module stays dependency-free).
_NOISE_WORDS = frozenset({
    "ing", "ic", "er", "um", "uh", "hmm", "hm", "mm", "ah", "eh",
    "mhm", "mmm", "uhh", "umm", "huh", "s",
})

_VOWELS = frozenset("aeiou")


def _is_junk_fragment(text: str, session: Dict[str, Any], clinic: Dict[str, Any]) -> bool:
    """True for single-word STT debris that must not advance screening state.

    Call-2 (2026-07-20): the stray final 'and' reached the classifier before
    connection.py's noise-fragment filter discarded it. Harmless there
    (verdict=unclear), but this layer runs BEFORE that filter, so a garbled
    fragment could in principle resolve a screen the caller never answered.

    Deliberately narrower than the connection.py filter: a single word that is
    a DECISIVE token for the pending screen — a red-flag keyword ('hot',
    'swollen') or a plain yes/no — is a legitimate answer and passes through.
    Only genuine debris (too-short connectives, vowel-less stutters, known
    mouth-noise) is skipped.
    """
    t = _norm(text)
    words = t.split()
    if len(words) != 1:
        return False
    w = words[0]
    if w in _MEANINGFUL_SHORT:
        return False
    # A single word matching a red-flag keyword of the PENDING screen is a
    # decisive answer ("hot" to "is it swollen, warm or red?"), never junk.
    pending_id = session.get(PENDING_SCREEN_KEY)
    if pending_id:
        screen = get_screen(clinic, pending_id) or {}
        for k in screen.get("red_flag_answer_keywords") or []:
            if w == _norm(k) or w in _norm(k).split():
                return False
    if w in _NOISE_WORDS:
        return True
    if len(w) <= 3:
        return True
    if not any(c in _VOWELS for c in w):
        return True
    return False


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

        # STT debris must not advance screening state (see _is_junk_fragment).
        if _is_junk_fragment(text, session, clinic):
            logger.info(
                "[clinical_screening] junk fragment skipped: %r", text[:40]
            )
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
                return _resolve_screen_answer(
                    session, clinic, pending_id, screen, text
                )
            # Question not asked yet — keep the flag; the SCREEN REQUIRED
            # steer forces it on the next model turn. Still allow a new,
            # different trigger to upgrade below? No — one screen at a time.
            return {"action": "none", "speak": None}

        # No pending screen — does this utterance trigger one?
        sid = match_screen_trigger(text, clinic, session)
        if sid:
            screen = get_screen(clinic, sid) or {}

            # ── DOUBLE-ASK GUARD ─────────────────────────────────────────────
            # The prompt layer (CLINICAL SAFETY SCREENING) can ask a screen
            # question itself — in that case pending_screen was never armed
            # here. The caller's ANSWER frequently still contains the trigger
            # keyword ("yeah I just said… the calf is red"), which would arm the
            # screen now and re-ask a question they have just answered
            # (Call-2, 2026-07-20: the model asked the DVT screen, then this
            # layer asked it again one turn later — caller audibly annoyed).
            # If the question is already in the last bot turn, treat THIS
            # utterance as the answer instead of re-asking it.
            if _question_was_asked(session, screen):
                session[PENDING_SCREEN_KEY] = sid
                logger.info(
                    "[clinical_screening] screen %s already asked by the model "
                    "— classifying this turn as the answer: %r", sid, text[:80],
                )
                return _resolve_screen_answer(session, clinic, sid, screen, text)

            # ── ALREADY-ANSWERED GUARD ───────────────────────────────────────
            # The arming utterance itself can already contain the red-flag
            # answer — "heard a crack and it swelled straight away" both arms
            # the trauma screen AND answers it. Asking the question back is
            # slow and tone-deaf, so escalate straight away.
            #
            # Requires TWO independent red-flag signals: unprompted description
            # is far weaker evidence than a direct answer, and a single keyword
            # over-escalates ("my calf is painful and swollen" is an ordinary
            # strain — that one gets the screen asked properly).
            if _red_flag_hits(text, screen) >= 2:
                session[PENDING_SCREEN_KEY] = sid
                logger.info(
                    "[clinical_screening] screen %s red flag present in the "
                    "arming utterance — escalating without asking: %r",
                    sid, text[:80],
                )
                return _resolve_screen_answer(session, clinic, sid, screen, text)

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
