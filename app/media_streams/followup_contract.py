# app/media_streams/followup_contract.py
"""
Deterministic pending follow-up contract resolver.

After the system gives a service-fit or policy-gate answer, the next caller
turn must be interpreted inside that bounded context — not routed to
ANSWER_GENERAL.  This module provides a pure classification function for
that turn and shared constants consumed by flow.py.

No I/O, no session mutations, no side effects.
"""
from __future__ import annotations

import re as _re
from typing import Optional

# ── Pending-contract kinds ─────────────────────────────────────────────────────
KIND_SERVICE_FIT_PENDING = "service_fit_pending"

# ── Resolution outcomes ────────────────────────────────────────────────────────
RESOLVE_PROCEED  = "proceed"   # caller wants to proceed / book
RESOLVE_INFO     = "info"      # caller has a related follow-up info question
RESOLVE_DECLINE  = "decline"   # caller is not proceeding right now
RESOLVE_UNKNOWN  = "unknown"   # cannot classify — fall through to router

# ── Proceed: tokens and phrases ────────────────────────────────────────────────
_PROCEED_TOKENS = frozenset({
    "yes", "yeah", "yep", "yup", "please", "ok", "okay",
    "sure", "great", "lovely", "perfect", "absolutely",
    "definitely", "brilliant", "wonderful", "alright",
    "fantastic", "excellent",
})

_PROCEED_PHRASES = (
    "go ahead", "go on then", "go on",
    "that would", "i'd like", "id like",
    "i would like", "let's do", "lets do", "sounds good",
    "book me", "book an", "arrange that", "get that",
    "take a few", "few details", "take some details",
    "please do", "can we do that", "let's arrange",
    "lets arrange", "get booked", "book that",
    "do that", "get that done", "get that arranged",
    "set that up", "that sounds", "love to",
    "want to book", "want to arrange", "like to book",
    "like to arrange", "happy to",
)

# ── Info follow-up: related service/capability questions ──────────────────────
_INFO_PHRASES = (
    "what would happen", "what happens",
    "what would you do", "what exactly would",
    "what do you do", "how does it work",
    "what does it involve", "what would it be",
    "what would it involve", "what would the",
    "would it be", "first step", "next step",
    "first thing", "what do they do", "what is it",
    "what is that", "how long does", "how long would",
    "what sort", "what kind", "what's involved",
    "what is involved", "do you treat", "can you treat",
    "could you help with", "can you help with",
    "tell me more", "more about", "more detail",
    "explain", "elaborate",
    "what would that", "how would that",
    "what happens next", "what's the next",
    "what is the process", "what is the procedure",
    "how do you", "what will you", "what will they",
)

# ── Decline: caller is not proceeding now ─────────────────────────────────────
_DECLINE_PHRASES = (
    "no thanks", "no thank you", "not right now",
    "not at the moment", "maybe later", "i'll think",
    "ill think", "not yet", "don't worry", "dont worry",
    "never mind", "nevermind", "just wanted to know",
    "just checking", "just enquiring", "just asking",
    "that's fine", "thats fine", "that's okay", "thats okay",
    "not today",
)

# ── Age extraction ─────────────────────────────────────────────────────────────
_AGE_PATTERNS = [
    _re.compile(r"\b(?:he|she|they)(?:\s+(?:is|are|'s|s))?\s+(\d{1,2})\b"),
    _re.compile(r"\bmy\s+(?:son|daughter|child|boy|girl|kid)\s+is\s+(\d{1,2})\b"),
    _re.compile(r"\b(\d{1,2})\s+years?\s+old\b"),
    _re.compile(r"\bjust\s+turned\s+(\d{1,2})\b"),
    _re.compile(r"\bturned\s+(\d{1,2})\b"),
    _re.compile(r"\babout\s+(\d{1,2})\b"),
    _re.compile(r"\b(\d{1,2})\b"),  # bare digit — last resort for short utterances
]


def extract_age(text: str) -> Optional[int]:
    """
    Extract a patient age integer from caller speech.

    Supports forms like "he's 13", "she is 8", "13 years old", "just turned 15",
    and a bare number in short utterances (≤ 5 words).

    Returns None if no valid age is found.
    """
    t = text.lower()
    for pat in _AGE_PATTERNS[:-1]:
        m = pat.search(t)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 110:
                return v
    # Bare digit: only fire for short utterances to avoid false positives
    if len(t.split()) <= 5:
        m = _AGE_PATTERNS[-1].search(t)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 110:
                return v
    return None


def resolve_service_fit_followup(text: str) -> str:
    """
    Classify the caller's response while a ``service_fit_pending`` contract
    is active.

    Returns one of: RESOLVE_PROCEED, RESOLVE_INFO, RESOLVE_DECLINE,
    RESOLVE_UNKNOWN.

    Priority: long info questions beat proceed tokens so that
    "yes but what would actually happen?" stays as INFO rather than
    jumping straight into booking.
    """
    t = text.lower().strip()
    tokens = set(t.split())

    has_info = any(p in t for p in _INFO_PHRASES)

    # Long utterances containing info-question phrases → INFO even if they
    # also contain a yes-token (e.g. "yes, but what would happen first?")
    if has_info and len(tokens) > 5:
        return RESOLVE_INFO

    # Explicit decline checked before proceed tokens to catch "not right now" etc.
    if any(p in t for p in _DECLINE_PHRASES):
        return RESOLVE_DECLINE

    # Proceed intent (short or unambiguous)
    has_proceed_token  = bool(tokens & _PROCEED_TOKENS)
    has_proceed_phrase = any(p in t for p in _PROCEED_PHRASES)
    is_short_booking   = (
        len(tokens) <= 4
        and bool(tokens & {"book", "booked", "arrange", "arranged"})
    )
    if has_proceed_token or has_proceed_phrase or is_short_booking:
        return RESOLVE_PROCEED

    # Short info question
    if has_info:
        return RESOLVE_INFO

    return RESOLVE_UNKNOWN
