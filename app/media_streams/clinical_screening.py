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
SCREEN_TRUNCATED_KEY = "screen_truncation_downgrades"  # screen ids re-asked once
SCREEN_ARM_PATHS_KEY = "screen_arm_paths"    # screen id -> how it armed (below)
SCREEN_REASKS_KEY = "screen_reasks"          # screen ids re-asked once, deterministically
SCREEN_HEDGE_PROBES_KEY = "screen_hedge_probes"  # screen ids probed once after a hedge

# How a screen came to be pending. Recorded per screen so the durable call
# record can answer "did Layer 1 arm this, or did Layer 2 cover for it?" —
# previously that lived only in the log text, which meant grepping a full call
# log by hand. Jules's 2026-07-25 sweep produced `dvt ORPHAN×1 ARMED×0`, the
# exact dormant-Layer-1 signature, and it took a human reading the log to see.
#   "trigger"          — Layer 1 matched the caller's presentation. Healthy.
#   "orphan"           — the model asked it; Layer 1 never armed. Layer 1 gap.
#   "model_asked"      — trigger hit AND the model had already asked it.
#   "arming_utterance" — the arming turn already contained the red-flag answer.
ARM_TRIGGER = "trigger"
ARM_ORPHAN = "orphan"
ARM_MODEL_ASKED = "model_asked"
ARM_IN_UTTERANCE = "arming_utterance"


def _arm(session: Dict[str, Any], screen_id: str, path: str) -> None:
    """Mark `screen_id` pending and record HOW it armed.

    Single choke point for `pending_screen` writes inside this module, so the
    arm path can never silently go unrecorded when a new arming site is added.
    """
    session[PENDING_SCREEN_KEY] = screen_id
    paths = dict(session.get(SCREEN_ARM_PATHS_KEY) or {})
    paths.setdefault(screen_id, path)  # first arm wins; re-arms are not new info
    session[SCREEN_ARM_PATHS_KEY] = paths


# Brief empathetic acknowledgement spoken immediately before a freshly-armed
# screen question — this replaces the warm "acknowledge first" the model used to
# add in the prompt-driven flow. Kept to a short empathy line ONLY, because the
# configured screen_question already carries its own conversational lead-in
# (e.g. "Before we look at the next step, can I ask — …"); a longer lead here
# would collide with it. Overridable per screen via clinic.json
# screen["screen_lead_in"] (set "" to omit entirely).
_DEFAULT_SCREEN_LEAD_IN = "I'm sorry to hear that."

# Spoken when a pending screen has to be asked a SECOND time because the first
# answer could not be graded. Deliberately different from the first lead-in: a
# caller who hears the identical sentence twice concludes they were not heard.
_DEFAULT_SCREEN_REASK_LEAD = "Sorry — I do need to check one thing before we go on."


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


# Filler words tolerated between the words of a multi-word TRIGGER keyword.
# Callers rarely use the config's exact phrasing — "losing weight" is said as
# "losing a bit of weight", "both wrists" as "both my wrists". 3 covers natural
# insertions ("a bit of") while staying tight enough that words scattered across
# an unrelated sentence do not connect into a spurious trigger (measured: 0 new
# false-fires on the benign corpus; a larger window starts joining unrelated
# clauses). TRIGGERS ONLY — see _phrase_in.
_TRIGGER_MAX_GAP = 3


def _phrase_in(keyword: str, text_norm: str, max_gap: int) -> bool:
    """The words of `keyword` appear IN ORDER in text_norm, each within
    `max_gap` filler words of the previous. Single-word keywords defer to
    `_kw_in` (boundary + inflection) unchanged.

    Used for TRIGGER matching only. Trigger failure just fails to ask a screen
    question — recoverable, and exactly the P1 #3 recall gap. Answer
    classification and the emergency intercept keep strict `_kw_in`: loosening
    those would add escalation / 999 surface, the wrong direction.

    Order matters and is deliberate: "night pain" does NOT match "pain … night".
    Reverse-order phrasings are a config-vocabulary question (add the variant as
    its own keyword), not something to solve by making the matcher order-blind —
    that multiplies spurious combinations across every screen.
    """
    words = _norm(keyword).split()
    if len(words) <= 1:
        return _kw_in(keyword, text_norm)
    tokens = text_norm.split()
    pos = 0
    last = -1
    for w in words:
        found = -1
        for i in range(pos, len(tokens)):
            if _kw_in(w, tokens[i]):
                found = i
                break
        if found < 0:
            return False
        if last >= 0 and found - last - 1 > max_gap:
            return False
        last = found
        pos = found + 1
    return True


def screening_config(clinic: Dict[str, Any]) -> Dict[str, Any]:
    cs = clinic.get("clinical_screening") or {}
    return cs if cs.get("enabled") else {}


def screening_enabled(clinic: Dict[str, Any]) -> bool:
    """PROACTIVE screening — the safety questions asked before booking."""
    return bool(screening_config(clinic))


def emergency_keywords(clinic: Dict[str, Any]) -> List[str]:
    """Emergency words, read INDEPENDENTLY of proactive screening.

    These are two different things and used to be one switch, which forced a
    clinic into a choice it should never have faced. Theorem declined clinical
    triage -- Mark wants the fastest possible booking, and jv_v1's six screens
    each cost the caller a question. But his prompt ALREADY tells Susie to say
    "call 999" when someone describes an emergency, and his config carries the
    wording. What he lacked was a deterministic trigger, because
    `detect_emergency` read its keywords through `screening_config`, which
    returns nothing unless `enabled` is set -- and setting `enabled` is what his
    own test forbids.

    So the intercept now keys on the keywords being CONFIGURED, not on
    screening being on. A clinic can have one without the other, in either
    direction, and Theorem's "no screening" pin keeps passing untouched because
    it never needs `enabled`.

    `emergency_intercept: false` is an explicit opt-out for a clinic that wants
    the wording available to the model but no deterministic interception.
    """
    cs = clinic.get("clinical_screening") or {}
    if cs.get("emergency_intercept") is False:
        return []
    return (cs.get("emergency_red_flags") or {}).get("keywords") or []


def emergency_intercept_enabled(clinic: Dict[str, Any]) -> bool:
    return bool(emergency_keywords(clinic))


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
    kws = emergency_keywords(clinic)
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
                             over-screened.

    The two are OR-ed, and a screen may carry both. `trigger_all_groups` gives
    the two-signal bar that stops a bare body-part mention arming a screen;
    `trigger_keywords` is then the short list of DECISIVE phrases that must arm
    on their own regardless — "bladder", "heard a crack", "deformed". Nobody
    mentions losing bladder control while describing a stiff back, so requiring
    a second signal for those would be the wrong trade.

    This used to `return` on the groups branch, which meant a screen defining
    both silently ignored its keywords. Nothing depended on that: vbi_neck was
    the only screen with groups and it has no keywords. OR-ing fails in the safe
    direction — it can only ever arm a screen that would not have armed."""
    groups = screen.get("trigger_all_groups")
    if groups and all(
        any(_phrase_in(k, text_norm, _TRIGGER_MAX_GAP) for k in group)
        for group in groups if group
    ):
        return True
    return any(
        _phrase_in(k, text_norm, _TRIGGER_MAX_GAP)
        for k in (screen.get("trigger_keywords") or [])
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


# ---------------------------------------------------------------------------
# ORPHAN SCREEN DETECTION — a screen Layer 2 asked that Layer 1 never armed.
#
# 2026-07-25. Every call in the 7-call sweep so far has produced zero
# [clinical_screening] lines while the MODEL screened anyway, off its own
# inference from the prompt's static CLINICAL SAFETY SCREENING protocol. That
# is the belt-and-braces design collapsing to braces — with pending_screen
# never set, the SCREEN REQUIRED steer never drives, book_appointment's
# booking_blocked_reason() backstop is inert, and the caller's answer is
# graded by nothing but the model's reading of it. The failure is silent:
# a call where Layer 1 is dormant looks identical in the logs to a call where
# no screen was warranted.
#
# _question_was_asked() already knows how to recognise a screen's question in
# the last bot turn, but it is only ever called with a screen id already
# pinned by pending_screen or by a trigger hit. Running it UNPINNED across
# every screen is what closes the hole — and it is not safe as written.
#
# Measured against the real bot turn from the 16:20 call:
#
#     "Before we look at getting that booked, can I quickly check -"
#         -> matches dvt on ['before', 'quickly']
#
# Both are conversational lead-in words that happen to sit in the DVT
# question. Unpinned, that arms the DVT screen on a turn where the model was
# asking about trauma, and then grades the caller's reply against DVT's
# red-flag keywords (swollen / warm / hot / red / surgery). "It's warm" would
# have produced a deterministic DVT escalation for a screen nobody asked.
#
# Two independent brakes, because either alone is corpus-dependent:
#
#   1. CORPUS-UNIQUE. A word is evidence for a screen only if it appears in
#      exactly one screen's question. This alone kills the case above
#      ('before' is in 3 of the jv_v1 questions, 'quickly' in 2) — but only
#      because jv_v1 has six screens. A clinic configured with ONE screen has
#      no corpus to be unique against and gets the bug back verbatim.
#   2. STOPLIST. Conversational scaffolding and scheduling vocabulary are
#      never evidence, however unique they are. This is what protects the
#      single-screen clinic, and it is what stops an ordinary booking turn
#      ("we have several morning slots") from matching the inflammatory
#      screen on ['several', 'morning'].
#
# Matching uses _kw_in (word boundary + inflection), not the bare substring
# containment _question_was_asked uses, so 'double' cannot match 'doubled'.
# ---------------------------------------------------------------------------

# Never evidence that a screen was asked, however corpus-unique.
#
# Two families, both drawn from words that actually occur in the jv_v1 screen
# questions' lead-ins or in ordinary booking turns:
#   - conversational scaffolding the model reuses in every warm lead-in;
#   - scheduling vocabulary, which collides hard ('morning' is in the
#     inflammatory screen AND in half of all booking turns).
# Normalised at import, same as _NEGATIVE_PATTERNS.
_ORPHAN_STOPWORDS: frozenset = frozenset(
    _norm(w) for w in (
        # conversational scaffolding / lead-in
        "before", "quickly", "quick", "further", "alongside", "around",
        "between", "anything", "something", "really", "sorry", "helps",
        "point", "right", "looking", "little", "sounds", "actually",
        "course", "please", "myself", "yourself", "better",
        # "proper" — 3 Aug 2026, two live sightings in one call suite. The
        # clinic's canned booking_offer is "…so Marcus can take a proper look?"
        # and trauma_fracture's question opens "That sounds like a proper
        # knock". So every booking CTA scored 1 of the 2 evidence words needed
        # against a screen that was never asked. trauma_fracture has only four
        # evidence words; one more generic collision and this logs a false
        # ORPHAN — corrupting the exact metric B-20 is scored against.
        "proper",
        # 2026-08-21, Phase 4 tone pass. The screen questions were re-framed
        # to normalise the ASKING ("one routine question I ask everyone before
        # booking back pain") rather than apologise for it ("Sorry to ask",
        # "Just to be safe"), which is what made a benign caller hear a
        # red-flag question as an accusation. The new lead-ins introduce
        # exactly the collision "proper" above documents: "everyone" and
        # "theres" appeared in ONE question each, so they became UNIQUE
        # evidence words for cauda_equina, and any bot turn saying "there's"
        # plus one more would have scored a false ORPHAN against the screen
        # B-20 is measured on. Scaffolding, not clinical vocabulary — every
        # question keeps its own substance as evidence. Pinned by
        # test_a_screen_question_is_framed_as_routine.py.
        "everyone", "routine", "theres",
        # booking / scheduling vocabulary
        "morning", "mornings", "afternoon", "afternoons", "evening",
        "evenings", "tomorrow", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday", "appointment",
        "appointments", "available", "availability", "minutes", "several",
        "booking", "booked", "session", "sessions", "practitioner",
        "treatment", "treatments",
    )
)

# How many evidence words must land before we believe the model asked a
# screen. Same bar as _question_was_asked, but over a much stricter
# vocabulary — see the brakes above.
_ORPHAN_MIN_EVIDENCE = 2

# B-31. llm_stream.py truncates session["last_bot_prompt"] to this many
# characters before storing it. Held here so match_asked_screen can tell a
# SHORT bot turn that genuinely asked nothing from a LONG one whose question
# mark was cut off — those are the same string to a naive "?" test, and
# treating them the same switched this whole layer off on a live call.
#
# test_orphan_survives_the_prompt_cap.py pins this against the literal in
# llm_stream.py. If that test fails, the cap moved and this must follow it.
_LAST_BOT_PROMPT_CAP = 200


def _screen_evidence_words(clinic: Dict[str, Any]) -> Dict[str, set]:
    """Per-screen set of words that are evidence THAT screen's question was
    asked: >5 chars, unique to one screen across the clinic's whole screen
    corpus, and not conversational/scheduling scaffolding.

    A screen left with fewer than _ORPHAN_MIN_EVIDENCE words can never be
    orphan-matched. That is the correct fail-safe direction: no detection,
    which is exactly today's behaviour, rather than a loose match that arms
    the wrong screen.

    Recomputed per call rather than cached — six screens of ~30 words is
    microseconds, and a cache keyed on a mutable clinic dict is a worse bug
    than the work it saves.
    """
    words: Dict[str, set] = {}
    seen: Dict[str, int] = {}
    for s in _screens(clinic):
        sid = s.get("id")
        if not sid:
            continue
        w = {x for x in _norm(s.get("screen_question") or "").split() if len(x) > 5}
        words[sid] = w
        for x in w:
            seen[x] = seen.get(x, 0) + 1
    return {
        sid: {x for x in w if seen.get(x) == 1 and x not in _ORPHAN_STOPWORDS}
        for sid, w in words.items()
    }


def match_asked_screen(
    clinic: Dict[str, Any], session: Dict[str, Any]
) -> Optional[str]:
    """Return the id of a screen the LAST BOT TURN appears to have asked but
    which Layer 1 never armed, or None.

    Only meaningful on the path where nothing else fired: no pending screen,
    no trigger hit. Screens already completed this call are skipped, so a
    cleared screen is not re-graded while its question is still sitting in
    last_bot_prompt.
    """
    _bot = session.get("last_bot_prompt") or ""
    _q = session.get("last_question") or ""
    raw = _bot or _q
    if not raw:
        return None
    # A screen is a QUESTION. This drops statement turns cheaply and costs
    # nothing: every configured screen_question ends in '?', and so does the
    # model's paraphrase of one.
    if "?" not in raw:
        # ...but last_bot_prompt is stored TRUNCATED (_LAST_BOT_PROMPT_CAP),
        # and a paraphrase only has to run five characters long for the '?'
        # to be the character that falls off the end. B-31, sweep call 2
        # (CA2ada6263, 2 Aug 2026): the model asked a full DVT screen in 205
        # characters, this test saw no '?' in the stored 200, returned None
        # WITHOUT LOGGING, and the caller's "i had a long journey sitting
        # still" — a red-flag keyword verbatim — was never graded. Replayed
        # offline the layer escalates to NHS 111 and blocks the booking.
        #
        # last_question holds the extracted question sentence and is NOT
        # truncated, so the text we need is already in the session; the old
        # fallback just required last_bot_prompt to be EMPTY rather than
        # UNUSABLE. Gated on the length so this cannot reach for a stale
        # last_question after one of connection.py's ~20 short deterministic
        # writers (fillers, keypad prompts) overwrites last_bot_prompt —
        # those are nowhere near the cap. Only a truncated turn qualifies.
        if len(_bot) >= _LAST_BOT_PROMPT_CAP and "?" in _q:
            logger.warning(
                "[clinical_screening] last_bot_prompt truncated at %d chars and "
                "lost its '?' — falling back to last_question for orphan "
                "matching (B-31). bot=%r question=%r",
                _LAST_BOT_PROMPT_CAP, _bot[-40:], _q[:120],
            )
            raw = _q
        else:
            return None
    last = _norm(raw)
    if not last:
        return None

    done = set(session.get(SCREENS_COMPLETED_KEY) or [])
    evidence = _screen_evidence_words(clinic)
    best_id: Optional[str] = None
    best_hits = 0
    # Best sub-threshold match, logged below. B-31's real cost was not that
    # the detector was wrong — it was that "found nothing" and "never ran"
    # are the same empty log. A near miss is the only cheap evidence that
    # this layer is looking at all.
    near_id: Optional[str] = None
    near_hits = 0
    # Config order is the tie-break: _screens() order is stable, and > (not
    # >=) keeps the first-declared screen when two tie.
    for s in _screens(clinic):
        sid = s.get("id")
        if not sid or sid in done:
            continue
        hits = sum(1 for w in evidence.get(sid) or () if _kw_in(w, last))
        if hits >= _ORPHAN_MIN_EVIDENCE:
            if hits > best_hits:
                best_id, best_hits = sid, hits
        elif hits > near_hits:
            near_id, near_hits = sid, hits
    if best_id is None and near_id is not None:
        logger.info(
            "[clinical_screening] orphan NEAR MISS — %s matched %d of the %d "
            "evidence words needed; NOT armed, nothing graded this turn: %r",
            near_id, near_hits, _ORPHAN_MIN_EVIDENCE, raw[:120],
        )
    return best_id


# ---------------------------------------------------------------------------
# Negation scoping for red-flag ANSWER keywords.
#
# 2026-07-24. classify_screen_answer() checked red-flag keywords before it
# looked at anything else, so a keyword only had to APPEAR. The most natural
# ways a caller says no all contain the keyword they are denying, and all five
# of these escalated to NHS 111 and set screen_red_flag, which blocks
# book_appointment at the tool boundary for the rest of the call:
#
#     "no its not swollen or warm"                  -> dvt red_flag
#     "no, nothing like that, no surgery or trips"  -> dvt red_flag
#     "no weight loss no fevers"                    -> serious_spinal red_flag
#     "no dizziness or double vision"               -> vbi_neck red_flag
#     "no numbness and my bladder is fine"          -> cauda_equina red_flag
#
# This was always live, but it was mostly unreachable while the STT keyterm
# boost was broken and "calf" transcribed as "car" — the screens rarely armed.
# Fixing the boost makes this path fire on every clinical call, so the two ship
# together.
#
# Deliberately generous: an occurrence is treated as negated on fairly light
# evidence, because the alternative is what the table above shows. Two brakes
# keep that from swallowing a real positive:
#
#   1. A keyword that carries its own negator is never negatable. "no feeling",
#      "cant walk", "wont take my weight" and "grips gone" are red flags
#      PHRASED negatively; the docstring's "no feeling in my legs" case must
#      keep firing, and this is what preserves it.
#   2. An affirmative marker between the negator and the keyword cancels the
#      negation. "no, but Ive had surgery" answers one half of a compound
#      screen question and volunteers the other — the "no" does not govern
#      "surgery", and reading it as if it did is the false negative that costs
#      a DVT.
# ---------------------------------------------------------------------------

# Post-_norm, so apostrophes are already deleted: "isn't" arrives as "isnt".
_NEGATORS: frozenset = frozenset({
    "no", "not", "nope", "nah", "none", "neither", "nor", "never",
    "nothing", "without", "cant", "cannot", "isnt", "arent", "wasnt",
    "werent", "hasnt", "havent", "dont", "doesnt", "didnt", "wont",
})

# Re-assert the symptom, ending the negator's reach (brake 2 above).
_AFFIRMATIVE_MARKERS: frozenset = frozenset({
    "had", "have", "has", "ive", "do", "did", "does", "got", "get",
    "am", "is", "was", "yes", "yeah", "yep", "yup", "definitely",
})

# Close the negator's clause outright: "no numbness but my bladder has been..."
_NEGATION_SCOPE_BREAKERS: frozenset = frozenset({
    "but", "however", "though", "although", "except", "apart", "aside",
})

# How far back to look for the governing negator. 5 covers "no dizziness or
# double vision" (negator three tokens before the keyword, across a list) while
# staying inside one clause; the scope breakers and affirmative markers stop
# the scan early far more often than the window does.
_NEGATION_WINDOW = 5


def _occurrence_negated(text_norm: str, keyword: str) -> bool:
    """True if EVERY occurrence of `keyword` in `text_norm` is negated.

    False when the keyword is absent, when any single occurrence stands
    un-negated, or when the keyword negates itself (brake 1). One un-negated
    occurrence is enough to keep the red flag: "no swelling, but it is warm".
    """
    k = _norm(keyword)
    if not k:
        return False
    # Brake 1 — a negatively-phrased red flag can never be "negated away".
    if any(w in _NEGATORS for w in k.split()):
        return False

    pattern = rf"(?<!\w){re.escape(k)}{_KW_INFLECTION}(?!\w)"
    found = False
    for m in re.finditer(pattern, text_norm):
        found = True
        before = text_norm[: m.start()].split()
        negated = False
        for token in reversed(before[-_NEGATION_WINDOW:]):
            if token in _NEGATION_SCOPE_BREAKERS or token in _AFFIRMATIVE_MARKERS:
                break
            if token in _NEGATORS:
                negated = True
                break
        if not negated:
            return False
    return found


# Single-token negatives, matched as WHOLE WORDS. They used to sit in the
# substring tuple below, which meant "no" matched inside "know": the answer
# "yes i know i do" — an unambiguous positive to a red-flag screen — was
# classified `clear`. Found 2026-08-21 while fixing the missing affirmative
# path. Whole-word matching also stops "im not sure" clearing a screen on the
# "no" inside "not", which is the right outcome: unsure is not a no.
# "I don't know" is not "no". It reached `clear` by two separate routes, both
# live on committed HEAD before 2026-08-24:
#   "i dont know maybe" -> _NEGATIVE_PATTERNS contains "i dont", which substring-
#                          matches "i dont know"
#   "no idea mate"      -> _NEGATIVE_WORDS contains "no", and "no idea" leads
#                          with it
# Both cleared a clinical red-flag screen for a caller who had just said they
# could not answer it. That is the same defect the comment below records — "no"
# matching inside "know" — reappearing one word later, and it contradicts this
# module's own doctrine that unsure is not a no.
#
# Checked BEFORE the negative branches, because ordering is the whole bug: the
# hedge vocabulary already carries "not sure" and it never got a turn. An unsure
# answer becomes `hedged`, which the HEDGE PROBE below handles properly — one
# narrowing question naming the symptom, escalate if the answer to THAT is not a
# clean no. Asking once more is the correct treatment for "I don't know"; a
# false CLEAR is not.
_UNSURE_PHRASES: tuple = (
    "dont know", "do not know", "dunno", "no idea", "not a clue", "havent a clue",
    "cant remember", "cannot remember", "cant say", "couldnt say", "cant tell",
    "not certain", "hard to tell", "im unsure", "who knows",
)


_NEGATIVE_WORDS: frozenset = frozenset({
    "no", "nope", "nah", "none", "neither",
})

# Multi-word negatives, matched as substrings (a phrase cannot collide with the
# inside of a single word the way a bare token can).
#
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
        "nothing like that",
        "nothing of the sort", "no nothing", "not that i", "no changes",
        "no change", "all fine", "everything's fine", "everything is fine",
        "i haven't", "i have not", "i don't", "i do not", "definitely not",
        "not at all", "thankfully not", "luckily not",
    )
)

# A leading affirmative to a red-flag screen. `classify_screen_answer` could
# only ever return `red_flag` by KEYWORD, so a plain "yes" to a direct yes/no
# question fell through to `unclear` — asymmetric in the dangerous direction,
# because "no" cleared reliably. Two-word leads are listed separately because
# "i" alone is far too generic to treat as agreement.
_AFFIRMATIVE_LEAD: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "aye", "correct",
    "definitely", "absolutely", "certainly",
})

# A HEDGE is neither a yes nor a no, and it is the commonest honest answer to a
# frightening clinical question. All of these used to return `unclear`, which
# leaves the screen pending and hands the decision to the model — the same
# asymmetry the affirmative branch was added to fix, one step further along.
#
# They are not treated as positives. Escalating "a bit" straight to A&E sends
# people to hospital over a sore back and teaches a clinic to ignore the alert.
# They earn ONE narrowing question naming the specific symptom
# (screen_probe_question); if the answer to THAT is not a clean no, it escalates.
#
# "sometimes" moved here from _AFFIRMATIVE_LEAD, where it returned red_flag
# outright. That is a deliberate DE-escalation: "sometimes" answering "do you
# get dizziness when you move your neck" is a hedge, not a confirmation.
_HEDGE_LEAD: frozenset = frozenset({
    "maybe", "perhaps", "possibly", "sometimes", "occasionally",
    "slightly", "dunno", "unsure",
})
_HEDGE_PHRASES = tuple(
    _norm(p)
    for p in (
        "i think so", "think so", "i guess", "i suppose", "sort of",
        "kind of", "kinda", "a bit", "a little", "little bit",
        "on and off", "now and then", "every now and then", "here and there",
        "not sure", "im not sure", "not really sure", "hard to say",
        "could be", "might be", "now and again", "once or twice",
    )
)
_AFFIRMATIVE_LEAD_PAIRS: frozenset = frozenset({
    "i do", "i have", "i am", "i did", "i does", "i has",
})


# Known mouth-noise / stutter artefacts (subset of connection.py's
# _V3_NOISE_FRAGMENTS — kept local so this module stays dependency-free).
_NOISE_WORDS = frozenset({
    "ing", "ic", "er", "um", "uh", "hmm", "hm", "mm", "ah", "eh",
    "mhm", "mmm", "uhh", "umm", "huh", "s",
    # "erm"/"ehm" are the ordinary British spellings and were missing. They
    # reach here as the lead token of a hesitant answer ("erm yes"), which the
    # affirmative branch reads — so an absent filler is a missed escalation,
    # not a cosmetic gap.
    "erm", "ehm",
})


def _red_flag_hits(text: str, screen: Dict[str, Any]) -> int:
    """Number of DISTINCT red-flag keywords present in the text.

    Used only by the already-answered guard, which needs stronger evidence than
    answer-classification does. When the caller is REPLYING to the screen
    question, one keyword is decisive ("swollen" = yes to "is it swollen?").
    When they are merely DESCRIBING the problem, one keyword is weak — "my calf
    is painful and swollen" is an ordinary strain, whereas "swollen and warm,
    just the one leg" is the DVT picture. Requiring two independent signals
    keeps the unprompted escalation specific.

    Denied keywords do not count here either — "its not swollen or warm" is two
    keywords and would clear the two-signal bar on the strength of the caller
    ruling both out.
    """
    t = _norm(text)
    return sum(1 for k in (screen.get("red_flag_answer_keywords") or [])
               if _kw_in(k, t) and not _occurrence_negated(t, k))


def _decisive_red_flag(text: str, screen: Dict[str, Any]) -> Optional[str]:
    """The first `decisive_red_flags` keyword the caller volunteered, or None.

    The already-answered guard needs TWO ordinary red-flag keywords before it
    will escalate off an unprompted description, because one is usually weak:
    "my calf is painful and swollen" is an ordinary strain.

    Some symptoms are not weak at any count. Nobody mentions losing bladder
    control while describing a stiff back. For those, asking the screen question
    back is not just slow — it reads as not having listened, and it asks a
    frightened caller to repeat the worst thing they just said.

    2026-08-21, JV call CA4feeeec6f9077d4912eb7d2a7f1d6846. The caller opened
    with "really bad back pain ... losing feeling in my legs ... a bit of
    trouble controlling my bladder". That scored ONE keyword (`bladder`;
    "losing feeling" was not a configured phrase and "my legs" is not "both
    legs"), so the guard did not fire and Susie asked him whether he had any
    changes in bladder or bowel control. He abandoned the call.

    Deliberately a short, separate list rather than a lower global threshold:
    it bypasses the question entirely, so it must name only symptoms that are
    decisive on their own. Denied occurrences do not count, exactly as in
    _red_flag_hits — "no trouble with my bladder" is not a positive.
    """
    t = _norm(text)
    for k in screen.get("decisive_red_flags") or []:
        if _kw_in(k, t) and not _occurrence_negated(t, k):
            return k
    return None


def classify_screen_answer(text: str, screen: Dict[str, Any]) -> str:
    """Classify the caller's reply to a screen question:
    'red_flag' | 'clear' | 'hedged' | 'unclear'.

    Red-flag keywords are checked FIRST — an answer like "no feeling in my
    legs" contains 'no' but is a positive. A keyword the caller is explicitly
    DENYING ("its not swollen or warm") does not count; see
    _occurrence_negated, which also protects that "no feeling" case."""
    t = _norm(text)
    if not t:
        return "unclear"
    # A keyword the caller DENIES is not a red flag — but it is not nothing
    # either. Remember it: an explicit denial of the very finding this screen
    # asks about is the strongest `clear` signal available, and it is expressed
    # in the caller's own words rather than one of five tokens. Consumed at the
    # bottom, so every other branch still outranks it.
    _denied_keyword = False
    for k in screen.get("red_flag_answer_keywords") or []:
        if _kw_in(k, t):
            if not _occurrence_negated(t, k):
                return "red_flag"
            _denied_keyword = True
    words = t.split()

    # Drop leading mouth-noise before reading the lead. The affirmative branch
    # below tests the FIRST word, so a single disfluency defeated it entirely:
    # "yeah i do" classified red_flag while "er yeah i do" fell through to
    # `unclear`. Live on JV 2026-08-21 (CA4feeeec6f9077d4912eb7d2a7f1d6846,
    # 11:19:46) — ten minutes after the affirmative branch was deployed, on a
    # cauda equina screen. Spoken answers to a safety question start with a
    # disfluency constantly; requiring a clean first token makes the branch fire
    # only for callers who happen not to hesitate.
    #
    # Stripped for the LEAD tests only, and only after every negative branch has
    # run against the untouched text. Widening the negative branches the same way
    # would add false-CLEAR surface, which is the dangerous direction.
    lead_words = list(words)
    while lead_words and lead_words[0] in _NOISE_WORDS:
        lead_words.pop(0)

    # Unsure outranks every clear-branch. See _UNSURE_PHRASES: without this the
    # loose "i dont" pattern and the bare "no" in "no idea" both cleared a
    # red-flag screen for a caller who had just said they could not answer it.
    if any(u in t for u in _UNSURE_PHRASES):
        return "hedged"

    first_word = words[0] if words else ""
    if first_word in _NEGATIVE_WORDS:
        return "clear"
    if any(w in _NEGATIVE_WORDS for w in words):
        return "clear"
    if any(p in t for p in _NEGATIVE_PATTERNS):
        return "clear"

    # A leading affirmative, with no negator anywhere in the reply. This runs
    # AFTER every negative branch on purpose, so it can only ever turn an
    # `unclear` into a `red_flag` — it can never flip an answer that already
    # classified as `clear`. "yeah no i dont have that" is caught above by the
    # word "no" and never reaches here.
    #
    # 2026-08-21, JV call CA6246ecb88d7a7fb0d33f3f8f66ef4905: the caller
    # reported leg weakness and loss of bladder control, the cauda equina screen
    # armed and asked correctly, and the reply "yeah i do" logged as
    # `answer unclear`. No escalation ever happened. `unclear` leaves the screen
    # pending and hands the decision to the LLM — which defeats the entire point
    # of a deterministic safety layer, and on that call the LLM was down.
    lead = lead_words[0] if lead_words else ""
    if (lead in _AFFIRMATIVE_LEAD
            or " ".join(lead_words[:2]) in _AFFIRMATIVE_LEAD_PAIRS):
        return "red_flag"

    # A hedge: not a yes, not a no, and the commonest honest answer there is.
    # Runs after both the affirmative and every negative branch, so it can only
    # ever capture what would otherwise have been `unclear`. See _HEDGE_LEAD.
    if lead in _HEDGE_LEAD or any(h in t for h in _HEDGE_PHRASES):
        return "hedged"

    # The caller named one of THIS screen's red-flag findings and negated it.
    #
    # CA2087528410, 2026-08-24 00:04:53, trauma_fracture: "not a very deformed
    # case sis mate nothing serious". `deformed` is a red-flag keyword, the
    # negation engine correctly ruled it denied — and then every clear-branch
    # missed, because _NEGATIVE_WORDS is five tokens (nah/neither/no/none/nope)
    # and _NEGATIVE_PATTERNS seventeen fixed phrases, none of which is a bare
    # "not X" or "nothing serious". Verdict `unclear` leaves the screen PENDING,
    # so 16s later the LLM said "That's reassuring — glad it's nothing too
    # serious" and moved to booking, and 34s after the answer the STRANDED path
    # re-asked the same clinical question. The caller answered once, clearly,
    # and was asked again as though they had not.
    #
    # Deliberately NOT fixed by adding "not"/"nothing" to _NEGATIVE_WORDS: those
    # are excluded on purpose so that "im not sure" cannot clear a screen —
    # unsure is not a no, and widening the negative vocabulary is the direction
    # that manufactures false CLEARs.
    #
    # This signal is narrower and self-limiting. It requires the caller to have
    # named a finding the SCREEN ITSELF defines as concerning and to have
    # negated it, judged by the same _NEGATORS/_NEGATION_WINDOW engine the
    # red-flag branch above already trusts — with its scope breakers, so
    # "not deformed but it wont take my weight" does not reach here at all
    # (the second keyword is un-negated and returns red_flag at the top).
    #
    # Placed last on purpose. red_flag, every negative branch, the affirmative
    # lead and the hedge all outrank it, so it can only ever turn what would
    # have been `unclear` into `clear` — and the truncation guard downstream
    # still demotes it back to `unclear` on a half-sentence.
    if _denied_keyword:
        return "clear"

    return "unclear"


# ---------------------------------------------------------------------------
# TRUNCATED SAFETY ANSWER — do not let half a sentence clear a screen.
#
# 2026-07-25, the 16:20 call. The caller was still speaking when the endpointer
# cut the turn:
#
#     16:20:49.782  partial 'it fine and there's no marks'
#     16:20:49.896  FINAL   'it fine and there's no marks where'
#
# 114 ms later, mid-clause. classify_screen_answer read the fragment as a plain
# negative (_NEGATIVE_PATTERNS matches the 'no') and cleared the screen, and the
# model moved to booking. Had the full sentence been "there's no marks where I
# can see, but it does look out of shape", the truncation would have INVERTED
# the clinical meaning — 'out of shape' is a configured trauma red flag.
#
# Safety answers must not be endpointed on a 600 ms pause
# (media_streams/config.py: min_turn_silence=600).
#
# What this canNOT use: the obvious signal is "the final carries no terminal
# punctuation". It is unavailable. config.py sets format_turns=false on the
# AssemblyAI v3 socket, so NO final is ever punctuated or capitalised — the
# apostrophe in "there's" is v3 contraction handling, not sentence formatting.
# The signal is constant and discriminates nothing. (The other candidate,
# stt_stream's _last_endpoint_wait_ms, sits at ~min_turn_silence whenever the
# endpointer times out, which is nearly always, and is gated on _LAT_ON.)
#
# What it uses instead: the fragment ends on a closed-class word that cannot
# close an English clause. 'where' in the logged case; 'but', 'it', 'does' for
# the same sentence cut anywhere earlier. Purely lexical, needs no new state in
# connection.py, and is the same idiom as _V3_NOISE_FRAGMENTS there.
#
# The list is deliberately narrow. A false positive re-asks a safety question
# the caller already answered — audible, irritating, and demo-visible — so
# every word that can legitimately END a short screen answer is excluded, even
# where it is closed-class:
#
#   no / none / nothing / that / this   'no', 'nothing like that'
#   cant / dont / havent / isnt / …     'no I havent', 'it doesnt'
#   fine / all / both / any             'all fine', 'no not any'
#
# Negated auxiliaries are the sharpest of these: "no I haven't" is a complete,
# clear answer to every compound screen question in jv_v1, and flagging it
# would re-ask on the single most common way a caller says no.
# ---------------------------------------------------------------------------
#
# PREPOSITIONS ARE NOT IN THIS LIST, and that is the main thing to know about
# it. English strands prepositions clause-finally all the time, and the
# stranded forms are exactly how callers decline a symptom:
#
#     "not that I know of"        "nothing I'm aware of"
#     "nothing to speak of"       "no one I've been in contact with"
#
# A first cut included them and flagged "not that I know of" — which is a
# configured _NEGATIVE_PATTERNS answer. Losing the preposition class costs a
# little recall ("no nothing apart from" is now missed); admitting it would
# re-ask a clear answer, which is worse.
#
# 'so', 'though' and 'really' are excluded for the same reason: "I don't think
# so", "it's fine though" and "not really" are all complete and all common.
_OPEN_CLAUSE_TAIL: frozenset = frozenset(
    _norm(w) for w in (
        # coordinators and subordinators — the sharpest signal, because they
        # promise a clause that never arrived. Omits 'so'/'though' (see above)
        # and the temporal ones that double as clause-final adverbs
        # ('since', 'before', 'after', 'until').
        "and", "but", "or", "nor", "because", "cause", "although",
        "while", "whilst", "unless", "whether", "than", "if",
        "when", "whenever", "where", "wherever", "which", "who",
        "whom", "whose",
        # determiners / possessives — never clause-final. Omits
        # this/that/these/those, which end 'nothing like that'.
        "the", "a", "an", "my", "your", "his", "her", "its", "our", "their",
        # positive auxiliaries and copula. Their NEGATED forms are omitted:
        # "no I haven't" is the commonest complete answer in the corpus.
        # 'been' is also omitted — "no I haven't been" is complete.
        "is", "was", "are", "were", "am", "be", "being",
        "has", "have", "had", "does", "did", "will", "would",
        "can", "could", "should", "shall", "may", "might", "must",
        # subject pronouns. Omits 'it' ("that's it") and 'you'/'me'.
        "i", "he", "she", "we", "they",
    )
)


def _looks_truncated(text: str) -> bool:
    """True if the utterance ends mid-clause on a word that cannot close one.

    Single-word utterances are never truncated for this purpose: a bare 'no'
    is the commonest complete answer there is, and _is_junk_fragment already
    owns the single-word debris case.
    """
    words = _norm(text).split()
    if len(words) < 2:
        return False
    return words[-1] in _OPEN_CLAUSE_TAIL


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

    # ── TRUNCATED ANSWER GUARD ───────────────────────────────────────────────
    # Asymmetric on purpose. A truncated POSITIVE is still a positive, so
    # 'red_flag' is never softened — the guard only ever moves a verdict in the
    # safe direction. 'unclear' is already the outcome we want, so only 'clear'
    # is downgraded. See the _OPEN_CLAUSE_TAIL block for the detector.
    #
    # 'unclear' costs nothing to act on: it leaves pending_screen set, which
    # drives the SCREEN REQUIRED steer (prompts/clinic_template_prompt.py) and
    # keeps booking_blocked_reason() blocking book_appointment. The caller
    # simply hears the question again — no timer, no debounce, no added
    # latency, and no change in connection.py.
    #
    # Capped at once per screen. Without the cap, a caller whose phrasing
    # habitually trails off is re-asked the same safety question forever; the
    # second answer is graded as it comes, truncated or not. One re-ask is
    # enough to catch the endpointer clipping a sentence, which is what this is
    # for — it is not an attempt to fix a caller who genuinely never finishes.
    if verdict == "clear" and _looks_truncated(text):
        _downgraded = list(session.get(SCREEN_TRUNCATED_KEY) or [])
        if screen_id not in _downgraded:
            _downgraded.append(screen_id)
            session[SCREEN_TRUNCATED_KEY] = _downgraded
            logger.warning(
                "[clinical_screening] screen %s answer TRUNCATED — endpointed "
                "mid-clause, not treating as clear; re-asking: %r",
                screen_id, text[:80],
            )
            verdict = "unclear"

    # ── HEDGE PROBE ───────────────────────────────────────────────────────────
    # "a bit", "sort of", "I think so", "on and off". Not a yes, not a no, and
    # the commonest honest answer to a frightening question. Owner decision
    # 2026-08-21: one narrowing question naming the specific symptom, and if the
    # answer to THAT is not a clean no, escalate.
    #
    # Escalating a hedge outright would send people to A&E over a sore back and
    # teach the clinic to ignore the alert. Leaving it as `unclear` was the old
    # behaviour and hands a safety decision to the model.
    #
    # This block MUST stay above `if verdict == "red_flag"`. Promoting a verdict
    # to "red_flag" only means anything while the escalation handler is still
    # ahead of it; sitting below that branch, the promotion assigned a local that
    # nothing subsequently read and the call fell through to the `unclear`
    # return — screen not completed, booking never frozen, no escalation spoken.
    # That is the silent-safety-failure this module exists to prevent.
    #
    # Capped at once per screen, same shape as the truncation guard above. After
    # the probe, ANY verdict that is not `clear` escalates — including `unclear`,
    # because a caller who has now been asked twice and still cannot be graded is
    # not one to book in unscreened.
    _probed = list(session.get(SCREEN_HEDGE_PROBES_KEY) or [])
    if verdict == "hedged":
        probe = (screen.get("screen_probe_question") or "").strip()
        if screen_id in _probed:
            logger.warning(
                "[clinical_screening] screen %s hedged again after the probe — "
                "treating as positive: %r", screen_id, text[:80],
            )
            verdict = "red_flag"
        elif probe:
            _probed.append(screen_id)
            session[SCREEN_HEDGE_PROBES_KEY] = _probed
            logger.info(
                "[clinical_screening] screen %s HEDGED — asking one narrowing "
                "question rather than guessing: %r", screen_id, text[:80],
            )
            return {"action": "ask_screen", "speak": probe}
        else:
            # No narrowing question configured for this screen. Fall back to the
            # pre-hedge behaviour rather than escalating on a single "a bit":
            # `unclear` leaves the screen pending, which keeps booking blocked
            # and re-drives the question, so this is not a silent pass.
            logger.info(
                "[clinical_screening] screen %s hedged but has no "
                "screen_probe_question configured — leaving pending: %r",
                screen_id, text[:80],
            )
            verdict = "unclear"
    elif verdict == "unclear" and screen_id in _probed:
        # Ungradable answer to the narrowing question. Same reasoning: the
        # caller has been asked twice.
        logger.warning(
            "[clinical_screening] screen %s ungradable after the probe — "
            "treating as positive: %r", screen_id, text[:80],
        )
        verdict = "red_flag"

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
        if not (screening_enabled(clinic) or emergency_intercept_enabled(clinic)):
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

            # ── STRANDED SCREEN ──────────────────────────────────────────────
            # The answer window has shut. A pending screen is graded only while
            # the last thing Susie said still looks like the screen question, so
            # ONE ungradable answer strands it: the model replies, last_bot_prompt
            # becomes that reply, and from then on no caller turn can ever resolve
            # the screen. It stays pending for the rest of the call.
            #
            # This used to fall straight through on the assumption that "the
            # SCREEN REQUIRED steer forces it on the next model turn". It does
            # not force WHEN. Measured over the stored JV corpus with
            # scripts/replay_screening.py, a stranded screen is what produces the
            # question surfacing at an arbitrary later moment — call
            # CAb0ff51e012, where the caller had just chosen an appointment slot:
            #
            #   caller: "that that first one works great"
            #   Susie : "Right, before we go any further — can I ask, do you have
            #            any numbness around the saddle area between your legs..."
            #
            # That is alarming, it reads as random, and it is the model covering
            # for a flag this layer left set. Ask it again HERE instead: once,
            # deterministically, in the screen's own words, at the first turn
            # after the window shut.
            #
            # Capped at once per screen. Beyond that the flag stays set and
            # booking stays blocked (booking_blocked_reason), which is the safe
            # direction — a caller who cannot be graded twice is not a caller to
            # keep interrogating, but nor is it one to book in unscreened.
            if screen:
                _reasked = list(session.get(SCREEN_REASKS_KEY) or [])
                # Prefer the bare question. Every screen_question carries its own
                # preamble, which collides with the re-ask lead and produces
                # "...before we go on. Before we look at the next step, can I
                # ask —". Falls back to the full question when unconfigured.
                q = (screen.get("screen_reask_question")
                     or screen.get("screen_question") or "").strip()
                if q and pending_id not in _reasked:
                    _reasked.append(pending_id)
                    session[SCREEN_REASKS_KEY] = _reasked
                    lead = screen.get("screen_reask_lead")
                    if lead is None:
                        lead = _DEFAULT_SCREEN_REASK_LEAD
                    lead = (lead or "").strip()
                    logger.info(
                        "[clinical_screening] screen %s STRANDED — answer window "
                        "shut with the screen unresolved; re-asking once "
                        "deterministically", pending_id,
                    )
                    return {
                        "action": "ask_screen",
                        "speak": (f"{lead} {q}".strip()) if lead else q,
                    }
            # One screen at a time: a new, different trigger does not upgrade
            # while one is still outstanding.
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
                _arm(session, sid, ARM_MODEL_ASKED)
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
            #
            # ...unless the one signal is DECISIVE. Nobody mentions losing
            # bladder control while describing a stiff back, and asking that
            # caller to repeat it reads as not having listened. See
            # _decisive_red_flag for the JV call that added this.
            _decisive = _decisive_red_flag(text, screen)
            if _decisive or _red_flag_hits(text, screen) >= 2:
                _arm(session, sid, ARM_IN_UTTERANCE)
                logger.info(
                    "[clinical_screening] screen %s red flag present in the "
                    "arming utterance (%s) — escalating without asking: %r",
                    sid,
                    f"decisive: {_decisive!r}" if _decisive else "two signals",
                    text[:80],
                )
                return _resolve_screen_answer(session, clinic, sid, screen, text)

            _arm(session, sid, ARM_TRIGGER)
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

        # ── ORPHAN GUARD ─────────────────────────────────────────────────────
        # Nothing armed and nothing triggered — but the model may have screened
        # anyway off the prompt's static protocol, in which case this utterance
        # is the answer to a safety question no flag is tracking. See the
        # ORPHAN SCREEN DETECTION block above for why the matcher is
        # deliberately strict.
        #
        # The log line is the point. Arming pending_screen is what makes the
        # SCREEN REQUIRED steer and book_appointment's booking_blocked_reason()
        # backstop apply on a path where both were previously inert — and it is
        # the precondition for the truncated-answer guard, which can only run
        # once a screen is known to be outstanding.
        #
        # Guarded on pending being clear: a triggered screen with no configured
        # screen_question falls out of the branch above with pending_screen
        # ALREADY set, and must not then be overwritten by a different screen.
        orphan_id = (
            None if session.get(PENDING_SCREEN_KEY)
            else match_asked_screen(clinic, session)
        )
        if orphan_id:
            orphan_screen = get_screen(clinic, orphan_id) or {}
            _arm(session, orphan_id, ARM_ORPHAN)
            logger.warning(
                "[clinical_screening] screen %s ORPHAN — asked by the model, "
                "never armed by Layer 1; grading this turn as the answer: %r",
                orphan_id, text[:80],
            )
            return _resolve_screen_answer(
                session, clinic, orphan_id, orphan_screen, text
            )
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
