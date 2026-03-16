# app/media_streams/turn_handler.py
"""
Hard-gate turn handler for the Media Streams voice pipeline.

Implements the 5-gate architecture that makes structural bugs impossible:

  GATE 2 — update_session_from_transcript()
    Extracts answers from the caller's transcript and writes them to session
    BEFORE the LLM ever sees them.  The LLM inherits a session that already
    knows patient_type / availability_preference / selected_slot, so it
    never needs to re-ask a question the fast-path or gate2 already answered.

  GATE 3 — get_next_action()
    Pure state-machine function.  Given the current session, returns exactly
    ONE of three Action types:
      "hardcoded" — play this exact text, advance to next_state, no LLM
      "llm"       — call the LLM (complex turn, tool call needed, etc.)
      "skip"      — do nothing (state already complete, wait for caller)

  GATE 5 — sanitise_response()
    Applied to every LLM text chunk BEFORE it reaches tts_text_queue.
    Removes banned phrases and questions about fields already in session.

Gates 1 (noise filter) and 4 (LLM execution) live in connection.py and
llm_stream.py respectively.

All functions are pure (no asyncio, no network) so they can be unit-tested
without starting the pipeline.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .session import CallState, get_call_state, advance_state
from .config import F_COLLECTED, F_LAST_BOT_PROMPT, F_LAST_QUESTION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action dataclass (returned by GATE 3)
# ---------------------------------------------------------------------------

@dataclass
class Action:
    """Instruction returned by get_next_action()."""
    type: str                                # "hardcoded" | "llm" | "skip"
    text: str = ""                           # exact text to speak (hardcoded only)
    next_state: Optional[CallState] = None  # state to advance to after speaking


# ---------------------------------------------------------------------------
# Text normalisation (shared by gate2 matchers)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s'-]")


def _norm(text: str) -> str:
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Homophone normalisation for ordinals / numbers
    t = re.sub(r"\bwon\b", "one", t)
    t = re.sub(r"\btoo\b", "two", t)
    t = re.sub(r"\bto\b",  "two", t)
    t = re.sub(r"\bfor\b", "four", t)
    t = re.sub(r"\bate\b", "eight", t)
    return t


# ---------------------------------------------------------------------------
# GATE 2 — update_session_from_transcript
# ---------------------------------------------------------------------------

# New-patient signal words / phrases (substring match on normalised text)
_NEW_SIGNALS: frozenset = frozenset({
    "new patient", "first time", "never been", "haven't been",
    "not been before", "first visit", "new to you", "never visited",
    "i'm new", "brand new", "not been", "no i haven't",
    "no not been", "new", "nope", "nah", "never",
})
# Single-word-only new signals (only match on the exact stripped transcript)
_NEW_SINGLE: frozenset = frozenset({"no", "new"})

# Returning-patient signal words / phrases
_RETURNING_SIGNALS: frozenset = frozenset({
    "returning", "existing", "been before", "i have been",
    "already a patient", "i've been", "been there", "been to you",
    "come before", "visited before", "regular", "already registered",
    "yes", "yeah", "ya", "yah", "yea", "ye", "yer", "yep", "yup",
    "i have",
})

# Slot ordinals (substring-in-norm matching)
_SLOT_ONE_PATTERNS   = ("one", "1", "first", "the first", "first one",
                         "option one", "number one", "first option")
_SLOT_TWO_PATTERNS   = ("two", "2", "second", "the second", "second one",
                         "option two", "number two", "second option")
_SLOT_THREE_PATTERNS = ("three", "3", "third", "the third", "third one",
                         "option three", "number three", "last", "last one")


def update_session_from_transcript(session: dict, transcript: str) -> None:
    """
    GATE 2: Extract caller answers from transcript and store in session.

    Called for EVERY utterance before the LLM or fast-path runs.
    Only updates fields that are not yet set — never overwrites.
    Completely safe to call when the state / transcript don't match
    (all branches have an early return).

    Side-effects on session:
      collected["patient_type"]    — if state==NEW_OR_RETURNING and not yet set
      availability_preference      — if state==COLLECT_AVAILABILITY and not yet set
      selected_slot                — if state==PRESENT_SLOTS and not yet set
    """
    state     = get_call_state(session)
    norm      = _norm(transcript)
    collected = session.setdefault("collected", {})

    # ── patient_type ─────────────────────────────────────────────────────────
    if state == CallState.NEW_OR_RETURNING and not collected.get("patient_type"):
        is_new = (
            any(sig in norm for sig in _NEW_SIGNALS if len(sig) > 3)
            or norm.strip() in _NEW_SINGLE
        )
        is_ret = (
            any(sig in norm for sig in _RETURNING_SIGNALS if len(sig) > 3)
            or norm.strip() in _RETURNING_SIGNALS
        )
        # Only update on unambiguous match
        if is_new and not is_ret:
            collected["patient_type"] = "new"
            logger.info(
                "[ms_gate2] patient_type=new from %r", transcript[:60],
            )
        elif is_ret and not is_new:
            collected["patient_type"] = "returning"
            logger.info(
                "[ms_gate2] patient_type=returning from %r", transcript[:60],
            )
        # Conflict or no match → leave as None; LLM handles

    # ── availability_preference ───────────────────────────────────────────────
    if state == CallState.COLLECT_AVAILABILITY:
        if not session.get("availability_preference") and transcript.strip():
            session["availability_preference"] = transcript.strip()
            logger.info(
                "[ms_gate2] availability_preference=%r", transcript[:60],
            )

    # ── slot selection ────────────────────────────────────────────────────────
    if state == CallState.PRESENT_SLOTS:
        offered = session.get("last_offered_slots") or []
        if offered and not session.get("selected_slot"):
            one   = any(p in norm for p in _SLOT_ONE_PATTERNS)
            two   = any(p in norm for p in _SLOT_TWO_PATTERNS)
            three = any(p in norm for p in _SLOT_THREE_PATTERNS) and len(offered) >= 3
            matches = sum([one, two, three])
            if matches == 1:
                idx = 0 if one else (1 if two else 2)
                if idx < len(offered):
                    session["selected_slot"] = offered[idx]
                    logger.info(
                        "[ms_gate2] selected_slot idx=%d from %r", idx, transcript[:60],
                    )


# ---------------------------------------------------------------------------
# GATE 3 — get_next_action
# ---------------------------------------------------------------------------

def get_next_action(session: dict) -> Action:
    """
    GATE 3: Pure state-machine — decide what Susie should do next.

    Called after update_session_from_transcript() so the session already
    reflects the caller's latest answer before we decide the action.

    Returns:
      Action("hardcoded", text, next_state)  — play text, advance state
      Action("llm")                          — hand off to LLM
      Action("skip")                         — nothing to do this turn

    Design rule: LLM is called ONLY when the output cannot be determined
    from session state alone (e.g. tool result needed, complex phrasing).
    Every deterministic transition is handled here.
    """
    state     = get_call_state(session)
    collected = session.get("collected", {})

    # ── NEW_OR_RETURNING ──────────────────────────────────────────────────────
    if state == CallState.NEW_OR_RETURNING:
        pt = collected.get("patient_type")
        if pt:
            # Gate2 (or fast-path) already extracted the answer —
            # emit hardcoded transition and advance state.
            text = "Great — and could I take your full name please?"
            logger.info("[ms_gate3] hardcoded: new_or_returning→collect_name")
            return Action("hardcoded", text, CallState.COLLECT_NAME)
        # No answer yet — LLM handles ambiguous responses ("erm...", "I think
        # I was here years ago but not recently")
        return Action("llm")

    # ── COLLECT_NAME ──────────────────────────────────────────────────────────
    if state == CallState.COLLECT_NAME:
        if collected.get("full_name"):
            # Fast-path already captured name; LLM loop should not be called
            # but guard here as belt-and-suspenders.
            logger.info("[ms_gate3] skip: full_name already set")
            return Action("skip")
        return Action("llm")

    # ── COLLECT_AVAILABILITY ──────────────────────────────────────────────────
    if state == CallState.COLLECT_AVAILABILITY:
        if (
            session.get("availability_preference")
            or session.get("_availability_response_received")
        ):
            # Have a preference → LLM will call check_availability
            return Action("llm")
        # No time/day info in transcript — re-ask without hitting LLM
        last_q = (
            session.get("last_question")
            or "What days or times work best for you?"
        )
        reask = f"Sorry, I didn't quite catch that — {last_q}"
        logger.info("[ms_gate3] hardcoded: availability re-ask")
        return Action("hardcoded", reask)

    # ── PRESENT_SLOTS ─────────────────────────────────────────────────────────
    if state == CallState.PRESENT_SLOTS:
        if session.get("slots_presented") and not session.get("selected_slot"):
            # Slots already presented — nothing to say; wait for selection
            logger.info("[ms_gate3] skip: slots_presented, waiting for selection")
            return Action("skip")
        # Either slots not yet presented (LLM will read them) or slot selected
        # (LLM will confirm it)
        return Action("llm")

    # ── All other states: defer to LLM ───────────────────────────────────────
    return Action("llm")


# ---------------------------------------------------------------------------
# GATE 5 — sanitise_response
# ---------------------------------------------------------------------------

# Patterns whose ENTIRE containing sentence must be removed.
# Each tuple is (description, compiled_regex).
_BANNED_SENTENCE_RE: list = [
    ("bear_with_me",   re.compile(r"[^.!?]*\bbear with me\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    ("bare_with_me",   re.compile(r"[^.!?]*\bbare with me\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    ("just_a_moment",  re.compile(r"[^.!?]*\bjust a moment\b[^.!?]*[.!?]?",        re.IGNORECASE)),
    ("one_moment",     re.compile(r"[^.!?]*\bone moment please\b[^.!?]*[.!?]?",    re.IGNORECASE)),
    ("i_am_waiting",   re.compile(r"[^.!?]*\bI am waiting\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    ("im_waiting",     re.compile(r"[^.!?]*\bI'?m waiting\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    ("are_you_there",  re.compile(r"[^.!?]*\bare you still there\b[^.!?]*[.!?]?",  re.IGNORECASE)),
    ("hello_query",    re.compile(r"[^.!?]*\bHello\?\b[^.!?]*[.!?]?",              re.IGNORECASE)),
    ("still_there",    re.compile(r"[^.!?]*\bstill there\b[^.!?]*[.!?]?",          re.IGNORECASE)),
]

# Questions to remove when the field is already collected.
# Format: (collected_field_key, compiled_regex_matching_the_question)
_ALREADY_ANSWERED_QUESTION_RE: list = [
    (
        "patient_type",
        re.compile(
            r"[^.!?]*(?:been (?:to|with) us before"
            r"|new or returning"
            r"|visited us before"
            r"|seen us before"
            r"|have you been here)[^.!?]*\?",
            re.IGNORECASE,
        ),
    ),
    (
        "full_name",
        re.compile(
            r"[^.!?]*(?:could I (?:take|get) your (?:full )?name"
            r"|what(?:'s| is) your (?:full )?name"
            r"|may I (?:take|have) your (?:full )?name)[^.!?]*\?",
            re.IGNORECASE,
        ),
    ),
    (
        "phone",
        re.compile(
            r"[^.!?]*(?:first five digits"
            r"|first 5 digits"
            r"|phone number"
            r"|contact number"
            r"|best number to reach)[^.!?]*\?",
            re.IGNORECASE,
        ),
    ),
]

# "Welcome back" must never be said to a new patient
_WELCOME_BACK_RE = re.compile(
    r"[^.!?]*\bwelcome back\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)

# Artefact cleanup after removals
_MULTI_SPACE_RE   = re.compile(r" {2,}")
_LEADING_JUNK_RE  = re.compile(r"^[\s,—–\-]+")


def sanitise_response(text: str, session: dict) -> str:
    """
    GATE 5: Clean LLM output before it reaches tts_text_queue.

    Applied per-chunk so the pipeline stays streaming (no full-response
    buffering needed).  Banned phrases are almost always contained within
    a single 15-50 word ResponseChunker chunk.

    Operations (in order):
      1. Remove sentences containing banned phrases
      2. Remove questions about fields already in session["collected"]
      3. Remove "welcome back" for new patients
      4. Strip whitespace artefacts

    Returns the cleaned text, or "" if the entire chunk was removed
    (caller must skip putting "" on tts_text_queue).
    """
    if not text or not text.strip():
        return text

    collected = session.get("collected", {})
    result    = text

    # 1. Banned phrases
    for desc, pattern in _BANNED_SENTENCE_RE:
        cleaned = pattern.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed banned phrase (%s)", desc)
            result = cleaned

    # 2. Already-answered questions
    for field_key, pattern in _ALREADY_ANSWERED_QUESTION_RE:
        if collected.get(field_key):
            cleaned = pattern.sub("", result)
            if cleaned != result:
                logger.info(
                    "[ms_gate5] removed question about already-set field=%s", field_key,
                )
                result = cleaned

    # 3. "Welcome back" for new patients
    if collected.get("patient_type") == "new":
        cleaned = _WELCOME_BACK_RE.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed 'welcome back' for new patient")
            result = cleaned

    # 4. Cleanup artefacts
    result = _MULTI_SPACE_RE.sub(" ", result)
    result = _LEADING_JUNK_RE.sub("", result)
    result = result.strip()

    if result != text and result:
        logger.debug("[ms_gate5] %r → %r", text[:60], result[:60])

    return result
