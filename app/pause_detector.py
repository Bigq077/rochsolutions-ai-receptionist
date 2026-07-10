# app/pause_detector.py
"""
Pause detector for the Susie AI receptionist.

Detects when a caller explicitly requests a pause ("hang on", "one sec", etc.)
so the silence handler can enter an extended-wait mode instead of firing a
re-ask phrase.

This module uses keyword matching only — no LLM calls.
All matches must complete under 50ms.
"""
from __future__ import annotations

import re

PAUSE_PHRASES = [
    "hang on",
    "one sec",
    "one second",
    "just a sec",
    "just a moment",
    "let me check",
    "let me ask",
    "bear with me",
    "hold on",
    "give me a moment",
    "give me a second",
    "wait a moment",
    "just checking",
    "one minute",
    "just a minute",
    "two secs",
]

# Stop / correction / override signals (P30). When one of these is present the
# caller is INTERRUPTING or correcting — not asking for patience — even if the
# utterance also contains a pause phrase ("hang on, that's wrong" / "hold on,
# stop"). Such utterances must NOT get the canned "Take your time."; they route
# to the LLM, which halts and asks what the caller wants to change.
_STOP_OVERRIDE_PHRASES = (
    "stop",
    "wrong",
    "that's not", "thats not",
    "not right", "not what i",
    "go back", "cancel that", "scrap that",
    "let me change", "i didn't", "i did not",
    "no wait", "wait no", "hang on no", "hold on no",
)
# Leading negation ("no"/"nope" at the very start) — matched only at the start
# so innocent substrings ("now", "know", "no problem") never trigger.
_LEADING_NEGATION = re.compile(r"^\s*(?:no|nope)\b", re.IGNORECASE)


def _has_stop_override(lower: str) -> bool:
    if any(p in lower for p in _STOP_OVERRIDE_PHRASES):
        return True
    return bool(_LEADING_NEGATION.match(lower))


def detect_caller_pause_request(utterance: str) -> bool:
    """
    Return True if the utterance is a genuine caller-requested pause.

    Keyword match only — no LLM. Completes in well under 50ms.
    Never raises; returns False on any error.

    A pause phrase combined with a stop/correction signal ("hang on, that's
    wrong") is treated as an INTERRUPTION, not a pause (P30): it returns False so
    the utterance reaches the LLM, which halts and acknowledges rather than
    replying "Take your time."
    """
    try:
        lower = utterance.lower()
        if not any(phrase in lower for phrase in PAUSE_PHRASES):
            return False
        if _has_stop_override(lower):
            return False
        return True
    except Exception:
        return False
