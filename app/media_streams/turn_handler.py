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
from .config import (
    F_COLLECTED, F_LAST_BOT_PROMPT, F_LAST_QUESTION,
    BOOKING_OPEN, Q_RECOMMEND, Q_NEW_OR_RETURNING,
    Q_AVAILABILITY, Q_CHECKING, Q_NAME, Q_PHONE,
)

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
_SLOT_ONE_PATTERNS   = (
    "first", "one", "1", "the first", "first one", "first slot",
    "option one", "option 1", "that first", "number one",
    "the first one", "first option",
)
_SLOT_TWO_PATTERNS   = (
    "second", "two", "2", "the second", "second one", "second slot",
    "option two", "option 2", "that second", "number two",
    "the second one", "second option", "middle one", "the middle one",
)
_SLOT_THREE_PATTERNS = (
    "third", "three", "3", "the third", "third one", "third slot",
    "option three", "option 3", "that third", "number three",
    "the third one", "third option",
)
# "Last/final" catch-all — always maps to the highest slot presented.
# Checked BEFORE numbered patterns so "the last one" can't ambiguously
# match both "last" (→ slot 3) and "one" (→ slot 1) at the same time.
_SLOT_LAST_PATTERNS  = (
    "last one", "the last one", "final one", "the final one",
    "the last", "last option", "last slot", "final slot", "final option",
    "that last one", "the final",
)


def update_session_from_transcript(session: dict, transcript: str) -> None:
    """
    GATE 2: Extract caller answers from transcript and store in session.

    Called for EVERY utterance before get_next_action() runs.
    Only updates fields that are not yet set — never overwrites.

    Side-effects on session (by state):
      reason              — COLLECT_REASON: any transcript >= 2 words
      duration            — COLLECT_DURATION: transcript with duration signals
      assessment_confirmed — CONFIRM_ASSESSMENT: yes/ok/sure etc.
      collected.patient_type — NEW_OR_RETURNING
      availability_preference — COLLECT_AVAILABILITY
      selected_slot       — PRESENT_SLOTS
      collected.full_name — COLLECT_NAME
      phone_number        — COLLECT_PHONE
    """
    state     = get_call_state(session)
    norm      = _norm(transcript)
    collected = session.setdefault("collected", {})

    # ── reason ───────────────────────────────────────────────────────────────
    if state == CallState.COLLECT_REASON and not session.get("reason"):
        # Any transcript of 2+ words is the caller's medical reason.
        # Single-word noise ("erm", "mm") is excluded by the word-count guard.
        if len(transcript.strip().split()) >= 2:
            session["reason"] = transcript.strip()
            logger.info("[ms_gate2] reason=%r", transcript[:60])

    # ── duration ─────────────────────────────────────────────────────────────
    elif state == CallState.COLLECT_DURATION and not session.get("duration"):
        _DURATION_SIGNALS = (
            "day", "days", "week", "weeks", "month", "months", "year", "years",
            "hour", "hours", "long", "while", "yesterday", "today", "ago",
            "since", "morning", "recently", "just", "about", "around",
            "couple", "few", "always", "forever", "a while",
            "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten",
        )
        if any(sig in norm for sig in _DURATION_SIGNALS):
            session["duration"] = transcript.strip()
            logger.info("[ms_gate2] duration=%r", transcript[:60])
        elif len(transcript.strip().split()) >= 2:
            # Accept any multi-word answer (e.g. "since the summer")
            session["duration"] = transcript.strip()
            logger.info("[ms_gate2] duration (fallback)=%r", transcript[:60])

    # ── assessment_confirmed ──────────────────────────────────────────────────
    elif state == CallState.CONFIRM_ASSESSMENT and session.get("assessment_confirmed") is None:
        _YES = (
            "yes", "yeah", "ya", "yah", "yea", "ye", "yer", "yep", "yup",
            "ok", "okay", "sure", "fine", "sounds good", "that's fine",
            "go ahead", "please", "that works", "correct", "absolutely",
            "definitely", "of course", "alright", "great",
        )
        if any(p in norm for p in _YES):
            session["assessment_confirmed"] = True
            logger.info("[ms_gate2] assessment_confirmed=True")

    # ── patient_type (new or returning) ──────────────────────────────────────
    elif state == CallState.NEW_OR_RETURNING and not collected.get("patient_type"):
        is_new = (
            any(sig in norm for sig in _NEW_SIGNALS if len(sig) > 3)
            or norm.strip() in _NEW_SINGLE
        )
        is_ret = (
            any(sig in norm for sig in _RETURNING_SIGNALS if len(sig) > 3)
            or norm.strip() in _RETURNING_SIGNALS
        )
        if is_new and not is_ret:
            collected["patient_type"] = "new"
            logger.info("[ms_gate2] patient_type=new from %r", transcript[:60])
        elif is_ret and not is_new:
            collected["patient_type"] = "returning"
            logger.info("[ms_gate2] patient_type=returning from %r", transcript[:60])

    # ── availability_preference ───────────────────────────────────────────────
    elif state == CallState.COLLECT_AVAILABILITY:
        if not session.get("availability_preference") and transcript.strip():
            session["availability_preference"] = transcript.strip()
            logger.info("[ms_gate2] availability_preference=%r", transcript[:60])

    # ── slot selection ────────────────────────────────────────────────────────
    elif state == CallState.PRESENT_SLOTS:
        offered = session.get("last_offered_slots") or []
        if offered and not session.get("selected_slot"):
            slots_count = session.get("slots_count", len(offered))

            # "Last / final" catch-all — maps to the highest slot presented.
            # Checked first so "the last one" is never split across matchers.
            if any(p in norm for p in _SLOT_LAST_PATTERNS):
                idx = min(slots_count, len(offered)) - 1
                session["selected_slot"] = offered[idx]
                logger.info(
                    "[ms_gate2] selected_slot idx=%d (last/final) from %r",
                    idx, transcript[:60],
                )
            else:
                one   = any(p in norm for p in _SLOT_ONE_PATTERNS)
                two   = any(p in norm for p in _SLOT_TWO_PATTERNS)
                three = any(p in norm for p in _SLOT_THREE_PATTERNS) and len(offered) >= 3
                matches = sum([one, two, three])
                if matches == 1:
                    idx = 0 if one else (1 if two else 2)
                    if idx < len(offered):
                        session["selected_slot"] = offered[idx]
                        logger.info(
                            "[ms_gate2] selected_slot idx=%d from %r",
                            idx, transcript[:60],
                        )

    # ── full_name ─────────────────────────────────────────────────────────────
    elif state == CallState.COLLECT_NAME and not collected.get("full_name"):
        words = transcript.strip().split()
        if 1 <= len(words) <= 6:
            collected["full_name"] = transcript.strip()
            logger.info("[ms_gate2] full_name=%r", transcript[:60])

    # ── phone_number ──────────────────────────────────────────────────────────
    elif state == CallState.COLLECT_PHONE and not session.get("phone_number"):
        digits = "".join(c for c in transcript if c.isdigit())
        if len(digits) >= 10:
            session["phone_number"] = digits
            # Also store in collected for LLM context
            collected["phone"] = digits
            logger.info("[ms_gate2] phone_number=%r", digits)


# ---------------------------------------------------------------------------
# GATE 3 — get_next_action
# ---------------------------------------------------------------------------

def get_next_action(session: dict) -> Action:
    """
    GATE 3: Pure state-machine — decide what Susie should do next.

    Called after update_session_from_transcript() so session already reflects
    the caller's latest answer.

    Flow (in order):
      GREETING            → hardcoded BOOKING_OPEN → COLLECT_REASON
      COLLECT_REASON      → hardcoded BOOKING_OPEN (if no reason yet)
                          → llm empathy turn       (if reason set) → COLLECT_DURATION
      COLLECT_DURATION    → hardcoded Q_RECOMMEND  (if duration set) → CONFIRM_ASSESSMENT
      CONFIRM_ASSESSMENT  → hardcoded Q_NEW_OR_RETURNING (if confirmed) → NEW_OR_RETURNING
      NEW_OR_RETURNING    → hardcoded Q_AVAILABILITY (if patient_type set) → COLLECT_AVAILABILITY
                          → llm (ambiguous answer)
      COLLECT_AVAILABILITY→ llm (calls check_availability, presents slots) → PRESENT_SLOTS
      PRESENT_SLOTS       → llm (present slots / wait)
                          → hardcoded Q_NAME (if slot selected) → COLLECT_NAME
      COLLECT_NAME        → hardcoded Q_PHONE (if name set) → COLLECT_PHONE
      COLLECT_PHONE       → llm confirm booking → CONFIRM_BOOKING
      Other               → llm
    """
    state     = get_call_state(session)
    collected = session.get("collected", {})

    # ── GREETING ─────────────────────────────────────────────────────────────
    # Play BOOKING_OPEN on the very first caller utterance.
    if state == CallState.GREETING:
        logger.info("[ms_gate3] hardcoded: greeting→collect_reason")
        return Action("hardcoded", BOOKING_OPEN, CallState.COLLECT_REASON)

    # ── COLLECT_REASON ────────────────────────────────────────────────────────
    if state == CallState.COLLECT_REASON:
        if not session.get("reason"):
            # BOOKING_OPEN already played once (when state advanced to COLLECT_REASON).
            # If we somehow end up here without a reason, wait — do not re-play.
            logger.info("[ms_gate3] skip: waiting for reason")
            return Action("skip")
        # Reason received — LLM generates one empathy sentence + duration question.
        logger.info("[ms_gate3] llm: reason set → empathy+duration_q")
        return Action("llm", next_state=CallState.COLLECT_DURATION)

    # ── COLLECT_DURATION ──────────────────────────────────────────────────────
    if state == CallState.COLLECT_DURATION:
        if not session.get("duration"):
            logger.info("[ms_gate3] skip: waiting for duration")
            return Action("skip")
        logger.info("[ms_gate3] hardcoded: duration→confirm_assessment")
        return Action("hardcoded", Q_RECOMMEND, CallState.CONFIRM_ASSESSMENT)

    # ── CONFIRM_ASSESSMENT ────────────────────────────────────────────────────
    if state == CallState.CONFIRM_ASSESSMENT:
        if not session.get("assessment_confirmed"):
            logger.info("[ms_gate3] skip: waiting for assessment confirmation")
            return Action("skip")
        logger.info("[ms_gate3] hardcoded: confirmed→new_or_returning")
        return Action("hardcoded", Q_NEW_OR_RETURNING, CallState.NEW_OR_RETURNING)

    # ── NEW_OR_RETURNING ──────────────────────────────────────────────────────
    if state == CallState.NEW_OR_RETURNING:
        pt = collected.get("patient_type")
        if pt:
            logger.info("[ms_gate3] hardcoded: new_or_returning→collect_availability")
            return Action("hardcoded", Q_AVAILABILITY, CallState.COLLECT_AVAILABILITY)
        # Ambiguous answer — LLM handles
        return Action("llm")

    # ── COLLECT_AVAILABILITY ──────────────────────────────────────────────────
    if state == CallState.COLLECT_AVAILABILITY:
        if not session.get("availability_preference"):
            logger.info("[ms_gate3] skip: waiting for availability")
            return Action("skip")
        # Availability captured — LLM says Q_CHECKING, calls tool, presents slots.
        logger.info("[ms_gate3] llm: availability set → check_slots")
        return Action("llm", next_state=CallState.PRESENT_SLOTS)

    # ── PRESENT_SLOTS ─────────────────────────────────────────────────────────
    if state == CallState.PRESENT_SLOTS:
        if session.get("selected_slot"):
            logger.info("[ms_gate3] hardcoded: slot_selected→collect_name")
            return Action("hardcoded", Q_NAME, CallState.COLLECT_NAME)
        if session.get("slots_presented"):
            logger.info("[ms_gate3] skip: slots_presented, waiting for selection")
            return Action("skip")
        # Slots not yet presented — LLM presents them.
        logger.info("[ms_gate3] llm: presenting slots")
        return Action("llm")

    # ── COLLECT_NAME ──────────────────────────────────────────────────────────
    if state == CallState.COLLECT_NAME:
        if collected.get("full_name"):
            logger.info("[ms_gate3] hardcoded: name→collect_phone")
            return Action("hardcoded", Q_PHONE, CallState.COLLECT_PHONE)
        logger.info("[ms_gate3] skip: waiting for name")
        return Action("skip")

    # ── COLLECT_PHONE ─────────────────────────────────────────────────────────
    if state == CallState.COLLECT_PHONE:
        if not session.get("phone_number"):
            logger.info("[ms_gate3] skip: waiting for phone")
            return Action("skip")
        # Phone received — LLM generates booking confirmation summary.
        logger.info("[ms_gate3] llm: phone set → confirm_booking")
        return Action("llm", next_state=CallState.CONFIRM_BOOKING)

    # ── All other states — LLM ────────────────────────────────────────────────
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
