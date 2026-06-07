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


def detect_caller_pause_request(utterance: str) -> bool:
    """
    Return True if the utterance contains a caller-requested pause phrase.

    Keyword match only — no LLM. Completes in well under 50ms.
    Never raises; returns False on any error.
    """
    try:
        lower = utterance.lower()
        return any(phrase in lower for phrase in PAUSE_PHRASES)
    except Exception:
        return False
