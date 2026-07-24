# app/media_streams/reask_variants.py
"""Re-ask phrase variety for the no-input watchdog.

Why this exists
---------------
The watchdog's phrase-selection block in `connection.py` already behaves as an
archetype dispatcher: it branches on the FlowEngine `state` and most branches
emit a purpose-written script rather than replaying the question (name ->
"please say: my first name is...", confirm-phone -> "please say: use this
number...", the two location retry ladders, etc).

Three branches do NOT, and instead replay `last_question` verbatim:

    connection.py:3300  GREETING non-location fallback   (_prefix + ". " + last_question)
    connection.py:3363  PRESENT_DAYS / PRESENT_TIMES     (phrase = last_question, no prefix)
    connection.py:3381  the terminal `else`              (_prefix + ". " + last_question)

On theorem_v3 the FlowEngine state stays "GREETING" for essentially the whole
call (see the comment at connection.py:3231), so the first of those is the main
re-ask path on the demo branch — which is why a caller who is mis-heard twice
hears the same sentence back twice.

This module supplies (a) a cheap archetype classifier for the question text in
those three branches, and (b) rung-indexed alternative phrasings, so a second
re-ask narrows the question instead of repeating it.

Design constraints
------------------
* Pure and synchronous. No session mutation, no I/O, no engine imports — so it
  is unit-testable without the WebSocket machinery.
* Classification is a FALLBACK inside three branches, not a new layer in front
  of the state dispatch. State remains the primary signal wherever it is
  informative; keyword matching on prompt text is the pattern that produced the
  dropped-"today" and ASAP-re-ask defects, so it is deliberately confined here.
* Returning `None` means "no better variant — caller keeps its existing
  behaviour". Every function is safe to ignore.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

__all__ = [
    "normalize_phrase",
    "classify_question",
    "variant_for",
    "ARCHETYPES",
]

ARCHETYPES = ("timing", "slot", "name", "phone", "confirm", "reason", "other")

# Keyword sets are matched against the lowercased question text.  Ordering in
# `classify_question` matters: the more specific archetypes are tested first so
# that e.g. "which day works — morning or afternoon?" classifies as `slot`
# rather than `timing`.
_SLOT_HINTS = (
    "which of those", "which option", "which one", "works best",
    "shall i put you down", "that time", "which time", "first or the second",
)
_TIMING_HINTS = (
    "morning", "afternoon", "evening", "what day", "which day",
    "particular day", "day or time", "when would", "when works",
    "what time", "soonest", "sooner",
)
_NAME_HINTS = ("your name", "first name", "surname", "last name", "spell")
_PHONE_HINTS = ("number", "phone", "mobile", "keypad", "digits")
_CONFIRM_HINTS = (
    "is that right", "does that sound", "shall i", "would you like me to",
    "happy with", "confirm", "correct?",
)
_REASON_HINTS = (
    "what's been going on", "whats been going on", "what brings",
    "how did it", "tell me a bit", "what's the problem", "whats the problem",
    "been troubling",
)

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_phrase(text: str) -> str:
    """Collapse a phrase to a comparable key.

    Lowercases, strips punctuation and collapses whitespace, so that
    "Sorry, I didn't catch that. Which day works?" and
    "sorry i didnt catch that  which day works" compare equal.  Used by the
    watchdog's already-said set so a variant that differs only in punctuation
    still counts as a repeat.
    """
    if not text:
        return ""
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", text.lower())).strip()


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    return any(n in haystack for n in needles)


def classify_question(question: str) -> str:
    """Best-effort archetype for a question Susie just asked.

    Returns one of `ARCHETYPES`.  Always returns something — "other" is the
    honest answer for anything unrecognised, and its variants are generic by
    design.
    """
    q = (question or "").lower().strip()
    if not q:
        return "other"
    # Specific before general: a slot-choice question frequently also contains
    # a time word ("which of those — the ten or the eleven?").
    if _contains_any(q, _SLOT_HINTS):
        return "slot"
    if _contains_any(q, _NAME_HINTS):
        return "name"
    if _contains_any(q, _PHONE_HINTS):
        return "phone"
    if _contains_any(q, _REASON_HINTS):
        return "reason"
    if _contains_any(q, _TIMING_HINTS):
        return "timing"
    if _contains_any(q, _CONFIRM_HINTS):
        return "confirm"
    return "other"


# Rung 2 phrasings.  Each NARROWS the question rather than restating it: the
# caller has already failed to answer the open form once, so the second ask
# offers a smaller, more answerable target.  None of these replay the original
# question text.
#
# Rung 1 is deliberately absent: the first re-ask replaying the question is
# correct behaviour (the caller may simply not have heard it), and is left to
# the existing call sites.
_RUNG2: dict[str, str] = {
    "timing": "Would a morning or an afternoon suit you better?",
    "slot": "Just say the one you'd like — the first, or the second?",
    "name": "Just your first name is fine — what should I call you?",
    "phone": "You can tap the number in on your keypad if that's easier.",
    "confirm": "Just say yes if that's right, or no if you'd like to change it.",
    "reason": "In a few words — what's been bothering you?",
    "other": "Take your time — I'm still here whenever you're ready.",
}


def variant_for(
    archetype: str,
    rung: int,
    already_said: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return an alternative phrasing for `archetype` at `rung`, or None.

    `already_said` is the set of normalized phrases spoken so far in this
    question generation.  A variant that has already been said is suppressed
    (returns None) so the caller falls back to its existing behaviour rather
    than repeating.

    Only rung 2 has variants today; rung 3 (default-forward) is intentionally
    not implemented here and remains the caller's decision.
    """
    if rung != 2:
        return None
    phrase = _RUNG2.get(archetype if archetype in _RUNG2 else "other")
    if not phrase:
        return None
    if already_said and normalize_phrase(phrase) in {
        normalize_phrase(p) for p in already_said
    }:
        return None
    return phrase
