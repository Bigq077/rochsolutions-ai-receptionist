"""
app/media_streams/name_collector.py
====================================
Deterministic unified name-collection engine for the Theorem Health receptionist.

Replaces the former ~550-line COLLECT_NAME handler block in flow.py with a
single canonical implementation shared across ALL name-collection states:
  COLLECT_NAME, COLLECT_NAME_RETURNING, COLLECT_NAME_RESCHEDULE, COLLECT_NAME_CANCEL

PHILOSOPHY
----------
• Normal-first: collect first name then surname conversationally.  No spelling
  by default — only when friction is detected.
• Field-level confirmation: every field (first name, surname) is confirmed
  individually before the flow advances.  Denial of a normal confirmation
  immediately triggers strict spelling mode — no second normal attempt.
• Explicit substates: every possible name-collection phase is an explicit named
  substate, never an implicit flag scattered across condition checks.
• Deterministic only: no LLM anywhere in this module.
• Minimal friction for clean names, robust fallback for hard names.

SUBSTATES  (stored in session["_nc"]["substate"])
-----------
  fn_normal    — collecting first name (initial state)
  fn_confirm   — first-name candidate awaiting yes/no confirmation
  fn_spelling  — first-name spelling mode: expect letter-by-letter input
  sn_normal    — collecting surname (entered after first name is accepted)
  sn_confirm   — surname candidate awaiting confirmation (normal or spelled)
  sn_spelling  — surname spelling mode: expect letter-by-letter input
  done         — full name accepted (transient — consumed immediately by flow.py)

CONFIRMATION CONTRACT
---------------------
  FIRST NAME
    ask → normal_capture → fn_confirm ("I've got Quentin — is that right?")
      YES → sn_normal
      NO  → fn_spelling ("No problem — could you spell out your first name…")
      Spelling offer → fn_spelling

  SURNAME
    ask → normal_capture → sn_confirm ("I've got Roch — is that right?")
      YES → accept
      NO  → sn_spelling ("No problem — could you spell it out letter by letter?")
    Spelled mode → sn_confirm ("I've got R O C H — is that right?")
      YES → accept
      NO  → sn_spelling again

RETURN PROTOCOL  (from NameCollector.handle())
--------------
  ("ask",    "question text")  — speak and wait for the caller's next turn
  ("repair", "question text")  — speak (clarification/repeat) and wait
  ("accept", "First Surname")  — name is complete; flow.py stores + advances step

RESET PROTOCOL
--------------
  nc.reset()            — full reset; use when stepping back to COLLECT_NAME
  nc.reset_to_surname() — keep first name, restart surname only

SESSION STORAGE  (canonical, kept in sync with legacy vars for compat)
---------------
  session["_nc"]                    — master state dict for this engine
  session["name_fragment"]          — legacy: first name (kept in sync)
  session["full_name"]              — legacy: full name (set on accept)
  session["spelling_confirm_surname"] — legacy: surname candidate (set in sn_confirm)

All three legacy vars are updated by this module so downstream code
(phone readback, CONFIRM_BOOKING, lookup validation) continues to work unchanged.
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Substate constants ──────────────────────────────────────────────────────────
NC_FN_NORMAL   = "fn_normal"
NC_FN_CONFIRM  = "fn_confirm"   # first-name candidate awaiting confirmation
NC_FN_SPELLING = "fn_spelling"
NC_SN_NORMAL   = "sn_normal"
NC_SN_SPELLING = "sn_spelling"
NC_SN_CONFIRM  = "sn_confirm"
NC_DONE        = "done"

# ── Name-label prefixes to strip (longest first to avoid prefix overlap) ────────
_PREFIXES: tuple = (
    "my first name is ", "first name is ",
    "my surname is ", "surname is ",
    "my family name is ", "family name is ",
    "my last name is ", "last name is ",
    "my full name is ", "full name is ",
    "my name is ", "name is ",
    "my first name's ", "my name's ",
    # Correction wrappers commonly spoken in spelling mode
    "no i said ", "no it's ", "no it is ",
    "sorry it's ", "sorry it is ",
    "i said ", "i meant ", "i mean ", "i spelled ",
    "it's ", "its ", "it is ",
    "booking in ", "booking for ",
    "it's for ", "for booking ",
    "call me ", "i'm ", "im ",
)

# ── Non-name tokens: function words, domain words, fragments ─────────────────
_FUNCTION_WORDS = frozenset({
    "in", "on", "at", "to", "for", "of", "by", "up", "as",
    "is", "am", "are", "was", "be", "been", "do", "did",
    "if", "got", "get", "has", "have", "had", "out", "off",
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "fine",
    "works", "work", "sorry", "what", "well", "now", "just",
    "like", "said", "please", "right", "wrong", "the", "a", "an",
    "i", "me", "my", "you", "we", "us", "he", "she", "they",
    "this", "that", "these", "those", "it", "its",
    "and", "or", "but", "so", "when", "then", "also",
    # Contractions that slip through the alpha-only regex filter
    "want", "can", "spell", "say", "tell", "each", "letter",
    # Meta-control verbs / words that are never plausible names
    "catch", "hear", "noted", "get", "not",
    # Common contractions that tokenise as single tokens
    "it's", "that's", "there's", "here's", "what's",
})

# ── Meta/acknowledgement words that are never plausible surnames ──────────────
_META_WORDS = frozenset({
    "noted", "understood", "received", "confirmed", "acknowledged",
    "recorded", "registered", "entered", "captured",
    "cheers", "brilliant", "great", "lovely", "wonderful",
})

_DOMAIN_WORDS = frozenset({
    "clinic", "clinics", "therapy", "therapist", "physio",
    "physiotherapy", "reception", "receptionist", "appointment",
    "appointments", "booking", "bookings", "health", "redditch",
    "alcester", "doctor", "service", "services", "number",
    "here", "there", "today", "tomorrow", "next", "last",
    "hello", "hi", "hey",
})

_DATE_TOKENS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "morning", "afternoon", "evening", "am", "pm",
    "slot", "date", "time", "week", "month",
})

_NOISE_FRAGMENTS = frozenset({
    "ic", "ck", "ng", "nk", "uh", "um", "er", "ah", "hm", "mm", "eh",
})

# ── Phrases that are meta-language / control utterances ──────────────────────
# These must never be stored as a name and should trigger salvage or spelling.
_META_LANGUAGE: tuple = (
    "do you need", "need help", "help spelling", "help me spell",
    "shall i spell", "do you want me to spell", "want me to spell",
    "is that right", "is that correct", "did i say", "did you catch",
    "can you spell", "spell that", "how do you spell", "how did you spell",
    "how do you have", "did you get", "do you understand",
    "do you have that", "did you catch that", "is that clear",
    "you repeat", "you're repeating", "you are repeating",
    "can you say", "need you to", "you need to",
    "do you need help",
    # Acknowledgement / confirmation meta-questions
    "is that noted", "have you noted", "did you note",
    "is that ok", "is that okay", "is that alright",
    "have you got that", "did you get that",
    # Can-you-hear-me type queries
    "can you hear", "do you hear", "if you catch", "if you get that",
    # Spelling-offer variants not already in _SPELLING_OFFER
    "i can spell", "can spell", "i'll spell that",
    "want to spell", "going to spell", "i could spell",
)

# ── Spelling offer phrases ────────────────────────────────────────────────────
_SPELLING_OFFER: tuple = (
    "shall i spell", "should i spell", "do you want me to spell",
    "want me to spell", "can i spell", "let me spell",
    "do you need me to spell", "need me to spell",
    "do you need help spelling", "need help spelling",
    "should i say each letter", "spell it out", "spell my name",
    "spell that out", "spell it", "i'll spell",
    "would it help if i spell", "want me to say each letter",
    "shall i say each letter",
    # Additional natural-language variants
    "i can spell", "i will spell", "i'll tell you each letter",
    "i could spell", "i'll spell that",
)

# ── Repair / repeat request phrases ──────────────────────────────────────────
_REPAIR: tuple = (
    "could you repeat", "repeat that", "say that again",
    "sorry what was that", "sorry, what was that",
    "i didn't catch that", "i didn't quite catch that",
    "what did you say", "didn't catch", "didn't hear",
    "pardon", "come again", "say it again",
)

# ── Name-negation phrases ─────────────────────────────────────────────────────
_NEGATION: tuple = (
    "i'm not ", "im not ", "not called ", "not named ",
    "my name is not ", "my name isn't ", "not my name",
    "that's not my name", "thats not my name",
)

# ── NATO phonetic alphabet ────────────────────────────────────────────────────
_NATO: dict = {
    "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h",
    "india": "i", "juliet": "j", "kilo": "k", "lima": "l",
    "mike": "m", "november": "n", "oscar": "o", "papa": "p",
    "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t",
    "uniform": "u", "victor": "v", "whiskey": "w", "x-ray": "x",
    "yankee": "y", "zulu": "z",
}

# ── Confirmation phrases ──────────────────────────────────────────────────────
_CONFIRM_YES: tuple = (
    "yes", "yeah", "yep", "yup", "ya", "correct", "right",
    "that's right", "thats right", "that's correct", "that is correct",
    "confirmed", "confirm", "sounds right", "that's it", "thats it",
    "perfect", "spot on", "exactly", "aye", "yeh",
    "no change", "no that's right", "sounds good", "brilliant",
)

_CONFIRM_NO: tuple = (
    "no", "nope", "nah", "wrong", "incorrect", "not right",
    "that's wrong", "thats wrong", "not correct", "that's not right",
    "start again", "try again", "different", "not that",
    "no that's wrong", "no thats wrong",
)

# Phrases that are unambiguously denials even when a weak YES word also matches.
# Used in confirm handlers: when any of these appear alongside a YES signal,
# the YES is discarded and the NO path fires.
_STRONG_NO: frozenset = frozenset({
    "not right", "that's wrong", "not correct",
    "that's not right", "wrong", "incorrect", "not that",
})


# ── Leading filler stripper (for multi-word prefixes like "yeah it's") ───────
_LEADING_FILLERS_RE = re.compile(
    r"^(?:yeah|yep|nah|ok|okay|right|sorry|well|uh|um|er|ah|so|and|no)\s+",
    re.IGNORECASE,
)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _strip_prefixes(text: str) -> str:
    """Remove the first matching name-label prefix from normalised text."""
    for p in _PREFIXES:
        if text.startswith(p):
            return text[len(p):].strip()
    return text


def _strip_filler_prefix(text: str) -> str:
    """
    Strip one leading filler word, then apply normal prefix stripping.

    Handles compound openings like "yeah it's Roch":
      "yeah it's roch"  →  strip "yeah "  →  "it's roch"  →  strip "it's "  →  "roch"
    """
    text = _LEADING_FILLERS_RE.sub("", text)
    return _strip_prefixes(text)


def _strip_spelling_wrapper(text: str) -> str:
    """
    Aggressively strip wrapper language from spelling-mode input before
    attempting letter parsing.

    Handles all observed patterns:
      "yeah it's r o c h"    →  "r o c h"
      "no it's r o c h"      →  "r o c h"
      "no i said r o c h"    →  "r o c h"
      "sorry r o c h"        →  "r o c h"
      "it's r o c h"         →  "r o c h"
      "i said r o c h"       →  "r o c h"

    Applies up to three rounds of filler + prefix stripping so that
    compound openers ("yeah no it's", "no i said") are fully cleared.
    """
    text = text.strip()
    for _ in range(3):
        stripped = _LEADING_FILLERS_RE.sub("", text)
        stripped = _strip_prefixes(stripped)
        if stripped == text:
            break
        text = stripped
    return text


def _is_valid_name_token(token: str) -> bool:
    """
    Return True if token looks like a plausible name element.

    A valid name token is 2–20 chars, entirely alpha/hyphen/apostrophe,
    and is not a function word, domain word, date token, or noise fragment.
    """
    t = token.lower().strip()
    if not t:
        return False
    if not (2 <= len(t) <= 20):
        return False
    if not all(c.isalpha() or c in "-'" for c in t):
        return False
    if t in _FUNCTION_WORDS:
        return False
    if t in _DOMAIN_WORDS:
        return False
    if t in _DATE_TOKENS:
        return False
    if t in _NOISE_FRAGMENTS:
        return False
    if t in _META_WORDS:
        return False
    return True


def _tokenise(text: str) -> list:
    """Split text into valid name tokens."""
    raw = re.findall(r"[a-zA-Z'\-]{2,}", text)
    return [t for t in raw if _is_valid_name_token(t)]


def _extract_leading_token(text: str, stop_phrases: tuple) -> Optional[str]:
    """
    Extract exactly one valid name token that appears BEFORE the first
    stop phrase in text.

    Used to salvage a name from utterances like:
      "Slater do you need help spelling that" → "Slater"

    Returns title-cased token or None if:
      - No stop phrase found in text
      - Stop phrase is at position 0
      - 0 or 2+ tokens before the stop phrase (ambiguous)
    """
    stop_pos = len(text)
    for p in stop_phrases:
        idx = text.lower().find(p)
        if 0 <= idx < stop_pos:
            stop_pos = idx

    if stop_pos == len(text) or stop_pos == 0:
        return None

    pre = text[:stop_pos].strip()
    if not pre:
        return None

    tokens = _tokenise(pre)
    if len(tokens) == 1:
        return tokens[0].title()
    return None


def _parse_spelled_letters(text: str) -> Optional[str]:
    """
    Parse a sequence of spelled-out letters into a capitalised name string.

    Handles:
      "S L A T E R"               → "Slater"
      "S-L-A-T-E-R"               → "Slater"
      "Sierra Lima Alpha Tango…"  → "Slater"
      "R for Romeo, O, C, H"      → "Roch"   (connector "for" is skipped)

    Returns capitalised result or None if:
      - Fewer than 2 letters resolved
      - Any token is neither a single letter, NATO word, nor the connector "for"
    """
    normalised = re.sub(r"[-,.\s]+", " ", text.lower()).strip()
    words = normalised.split()
    if not words:
        return None
    letters: list = []
    i = 0
    while i < len(words):
        w = words[i]
        if len(w) == 1 and w.isalpha():
            letters.append(w.upper())
            # "R for Romeo" — skip optional "for <word>" clarification
            if i + 2 < len(words) and words[i + 1] == "for":
                i += 3
                continue
        elif w in _NATO:
            letters.append(_NATO[w].upper())
        elif w == "for":
            # Stray connector between letters (e.g. "r for o c h") — skip
            i += 1
            continue
        else:
            return None  # unrecognised token → not a clean spelling sequence
        i += 1
    if len(letters) < 2:
        return None
    return "".join(letters).title()


def _parse_spelled_letters_tolerant(text: str) -> Optional[str]:
    """
    Like _parse_spelled_letters but tolerates a single trailing non-letter word.

    Handles "r o c h rock" → tries "r o c h" when full parse fails.
    The trailing word is accepted as noise if the abbreviated result
    case-insensitively matches its start (the word is plausibly a
    pronunciation of the spelled result).

    Only drops the LAST token — any interior noise still returns None.
    """
    result = _parse_spelled_letters(text)
    if result is not None:
        return result
    # Try dropping the last whitespace-separated token
    parts = text.rsplit(" ", 1)
    if len(parts) < 2:
        return None
    trimmed, trailing = parts[0].strip(), parts[1].strip()
    if not trimmed:
        return None
    result_trimmed = _parse_spelled_letters(trimmed)
    if result_trimmed is None:
        return None
    # Accept if trailing word looks like a pronunciation of the spelled result
    # (e.g. "rock" ≈ "roch", "slater" == "slater")
    if trailing.lower().startswith(result_trimmed[:2].lower()):
        return result_trimmed
    # Also accept if trailing is in function/domain words (pure noise)
    if trailing.lower() in _FUNCTION_WORDS or trailing.lower() in _DOMAIN_WORDS:
        return result_trimmed
    return None


def _is_spelling_offer(text: str) -> bool:
    return any(p in text for p in _SPELLING_OFFER)


def _is_repair_request(text: str) -> bool:
    return any(p in text for p in _REPAIR)


def _has_meta_language(text: str) -> bool:
    return any(p in text for p in _META_LANGUAGE)


# ── NameCollector class ───────────────────────────────────────────────────────

class NameCollector:
    """
    Deterministic per-call name-collection engine.

    Manages the complete first-name + surname lifecycle through named substates.
    A new instance wraps the session dict on every call — the actual state lives
    in ``session["_nc"]`` so it survives FlowManager re-creation.

    Usage in flow.py::

        nc = NameCollector(session)
        action, payload = nc.handle(text, transcript)
        if action == "accept":
            full_name = payload          # e.g. "Matt Slater"
        else:
            question_to_speak = payload  # speak and wait

    Reset usage::

        NameCollector(session).reset()            # full reset
        NameCollector(session).reset_to_surname() # keep first name
    """

    def __init__(self, session: dict) -> None:
        self._s = session
        if "_nc" not in session:
            self._init_state()

    # ── Initialisation ──────────────────────────────────────────────────────

    def _init_state(self) -> None:
        """Create a fresh _nc state dict in session."""
        self._s["_nc"] = {
            "substate":          NC_FN_NORMAL,
            # First-name tracking
            "fn_candidate":      None,   # candidate held in fn_confirm
            "fn_spelled":        False,  # True when fn_candidate came from spelling
            "fn_letter_buffer":  [],     # accumulates single-letter turns in fn_spelling
            "first_name":        None,
            # Surname tracking
            "surname_candidate": None,
            "sn_spelled":        False,  # True when candidate came from sn_spelling
            "sn_letter_buffer":  [],     # accumulates single-letter turns in sn_spelling
            "pending_surname":   None,   # pre-queued sn token from 2-token fn_normal
            # Retry counters
            "fn_retries":        0,
            "sn_retries":        0,
        }

    def reset(self) -> None:
        """
        Full reset — call when stepping back into ANY COLLECT_NAME state
        from COLLECT_PHONE, CONFIRM_PHONE, or a global repair path.

        Clears both first name and surname state plus all legacy session vars.
        """
        self._s["_nc"] = {
            "substate":          NC_FN_NORMAL,
            "fn_candidate":      None,
            "fn_spelled":        False,
            "fn_letter_buffer":  [],
            "first_name":        None,
            "surname_candidate": None,
            "sn_spelled":        False,
            "sn_letter_buffer":  [],
            "pending_surname":   None,
            "fn_retries":        0,
            "sn_retries":        0,
        }
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        col = self._s.get("collected", {})
        col.pop("full_name", None)
        col.pop("name", None)

    def reset_to_surname(self) -> None:
        """
        Partial reset — keep first name, restart surname collection.

        Use when a post-lookup correction reveals only the surname is wrong
        but the first name is already confirmed.
        """
        nc = self._s.get("_nc", {})
        self._s["_nc"] = {
            "substate":          NC_SN_NORMAL,
            "fn_candidate":      None,
            "fn_spelled":        False,
            "fn_letter_buffer":  [],
            "first_name":        nc.get("first_name"),  # preserved
            "surname_candidate": None,
            "sn_spelled":        False,
            "sn_letter_buffer":  [],
            "pending_surname":   None,
            "fn_retries":        nc.get("fn_retries", 0),
            "sn_retries":        0,
        }
        # name_fragment is the first name — keep it
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        col = self._s.get("collected", {})
        col.pop("full_name", None)
        col.pop("name", None)

    # ── Convenience properties ───────────────────────────────────────────────

    @property
    def _nc(self) -> dict:
        return self._s["_nc"]

    @property
    def substate(self) -> str:
        return self._nc.get("substate", NC_FN_NORMAL)

    @property
    def first_name(self) -> Optional[str]:
        return self._nc.get("first_name")

    # ── Canonical question for current substate ──────────────────────────────

    def _question(self) -> str:
        ss = self.substate
        if ss == NC_FN_NORMAL:
            return "What's your first name please?"
        if ss == NC_FN_CONFIRM:
            cand = self._nc.get("fn_candidate") or ""
            if self._nc.get("fn_spelled") and cand:
                spaced = " ".join(list(cand.upper()))
                return f"I've got {spaced} — is that right?"
            return f"I've got {cand} — is that right?" if cand else "What's your first name please?"
        if ss == NC_FN_SPELLING:
            return (
                "Of course — please say your first name one letter at a time "
                "and I'll read it back when you're done."
            )
        if ss == NC_SN_NORMAL:
            return "And what's your surname?"
        if ss == NC_SN_SPELLING:
            return "Please spell out your surname — say each letter clearly."
        if ss == NC_SN_CONFIRM:
            cand = self._nc.get("surname_candidate") or ""
            if self._nc.get("sn_spelled") and cand:
                spaced = " ".join(list(cand.upper()))
                return f"I've got {spaced} — is that right?"
            return f"I've got {cand} — is that right?" if cand else "Could you say your surname again?"
        return "What's your first name please?"

    # ── Main entry point ─────────────────────────────────────────────────────

    def handle(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Process one caller turn and advance the substate machine.

        Parameters
        ----------
        text : str
            Normalised (lower-case, stripped) transcript.
        raw : str
            Original un-normalised transcript.

        Returns
        -------
        ("ask",    question)  — speak question and wait
        ("repair", question)  — speak clarification and wait
        ("accept", full_name) — name is complete; flow.py should advance step
        """
        ss = self.substate

        # Global repair gate — fires in every substate.
        # Spelling offers must NOT be caught here — they are a valid next step.
        if _is_repair_request(text) and not _is_spelling_offer(text):
            return ("repair", self._question())

        if ss == NC_FN_NORMAL:
            return self._fn_normal(text, raw)
        if ss == NC_FN_CONFIRM:
            return self._fn_confirm(text, raw)
        if ss == NC_FN_SPELLING:
            return self._fn_spelling(text, raw)
        if ss == NC_SN_NORMAL:
            return self._sn_normal(text, raw)
        if ss == NC_SN_SPELLING:
            return self._sn_spelling(text, raw)
        if ss == NC_SN_CONFIRM:
            return self._sn_confirm(text, raw)

        # Unknown substate — defensive reset
        logger.warning("[NameCollector] unknown substate %r — resetting to fn_normal", ss)
        self._init_state()
        return ("ask", self._question())

    # ── Substate: fn_normal ──────────────────────────────────────────────────

    def _fn_normal(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect first name in normal (non-spelling) mode.

        Fast path: valid single token → enter fn_confirm.
        Two-token path: first token → fn_confirm, second token queued as
          pending_surname so after fn is confirmed we jump straight to sn_confirm.
        Meta-language path: salvage leading token → fn_confirm.
        Escalation: ≥2 failures → auto-enter fn_spelling.
        """
        # Explicit spelling offer
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_FN_SPELLING
            self._nc["fn_letter_buffer"] = []
            return ("ask", self._question())

        cleaned = _strip_filler_prefix(text)

        # Negation: "I'm not Sarah" → try to extract corrected name
        if any(text.startswith(p) or (" " + p) in text for p in _NEGATION):
            m = re.search(
                r"(?:it'?s|its|i'?m|im|the name(?:'s| is)?|actually|no it'?s)\s+"
                r"([a-z][a-z\-']{1,})",
                cleaned,
            )
            if m:
                token = m.group(1).strip().title()
                if _is_valid_name_token(token):
                    return self._enter_fn_confirm(token, spelled=False)
            # No valid token → fresh re-ask (reset retries — negation is not a failure)
            self._nc["fn_retries"] = 0
            return ("ask", "No problem — what's your first name please?")

        # Meta-language: "Matt, do you need me to spell that?"
        # Try to salvage the token that appeared BEFORE the meta phrase.
        if _has_meta_language(cleaned):
            token = _extract_leading_token(cleaned, _META_LANGUAGE)
            if token and _is_valid_name_token(token):
                return self._enter_fn_confirm(token, spelled=False)
            return self._fn_fail(
                "Sorry, I didn't quite catch that — what's your first name?"
            )

        tokens = _tokenise(cleaned)

        # Two valid tokens → treat as "FirstName Surname".
        # Confirm first name; queue surname so after fn is confirmed we
        # jump straight to sn_confirm for the pre-captured surname.
        if len(tokens) == 2:
            fn_tok = tokens[0].title()
            sn_tok = tokens[1].title()
            self._nc["pending_surname"] = sn_tok
            return self._enter_fn_confirm(fn_tok, spelled=False)

        # One valid token → enter fn_confirm
        if len(tokens) == 1:
            return self._enter_fn_confirm(tokens[0].title(), spelled=False)

        # Nothing usable
        return self._fn_fail(
            "Sorry, I didn't quite catch that — could you say your first name again?"
        )

    # ── Substate: fn_confirm ─────────────────────────────────────────────────

    def _fn_confirm(self, text: str, raw: str) -> Tuple[str, str]:
        """
        First-name candidate awaiting yes/no confirmation.

        Question played: "I've got Quentin — is that right?"
                    or: "I've got Q U E N T I N — is that right?"  (after spelling)

        YES  → store first name, ask for surname (or jump to sn_confirm if
               a pending_surname was queued from a 2-token fn_normal turn).
        NO   → immediately enter fn_spelling (strict mode, not a normal retry).
        Spelling offer → fn_spelling.
        Spelled letters given → update candidate, re-confirm.
        Clean word → update candidate, re-confirm.
        Ambiguous → re-ask.
        """
        cand = self._nc.get("fn_candidate") or ""

        # Spelling offer → switch to fn_spelling
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_FN_SPELLING
            self._nc["fn_candidate"] = None
            self._nc["fn_letter_buffer"] = []
            return ("ask", self._question())

        # Spelled correction: caller provides the correct first name as letters
        spelled = _parse_spelled_letters(text)
        if spelled:
            return self._enter_fn_confirm(spelled.title(), spelled=True)

        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        # Strong-denial override: "no that is not right" contains both "right"
        # (a YES phrase) and "no"/"not right" (NO phrases) — when explicit
        # denial phrases are present, prefer NO regardless of weak yes signals.
        if has_yes and has_no and any(p in text for p in _STRONG_NO):
            has_yes = False

        # YES — accept first name; advance to surname
        if has_yes and not has_no:
            pending_sn = self._nc.get("pending_surname")
            self._store_first_name(cand)
            if pending_sn:
                # Caller gave full name in one go — jump straight to sn_confirm
                self._nc["pending_surname"] = None
                logger.info("[NameCollector] fn_confirm: YES — jumping to sn_confirm for pending %r", pending_sn)
                return self._enter_sn_confirm(pending_sn, spelled=False)
            return ("ask", "And what's your surname?")

        # NO — enter strict fn_spelling immediately (no second normal attempt)
        if has_no and not has_yes:
            self._nc["substate"]     = NC_FN_SPELLING
            self._nc["fn_candidate"] = None
            self._nc["fn_letter_buffer"] = []
            logger.info("[NameCollector] fn_confirm: NO — entering fn_spelling")
            return ("ask", "No problem — could you spell out your first name letter by letter?")

        # Both YES and NO signals present (e.g. "yes, no wait…") — ambiguous;
        # do NOT fall through to token extraction or a noise word becomes a name.
        if has_yes and has_no:
            if cand:
                return ("ask", f"Sorry — just yes or no: is {cand} right?")
            self._nc["substate"] = NC_FN_SPELLING
            return ("ask", "Could you spell out your first name for me?")

        # Meta-language in confirm context → re-ask yes/no
        if _has_meta_language(text):
            if cand:
                return ("ask", f"Sorry — I just need a yes or a no. I've got {cand} — is that right?")
            self._nc["substate"] = NC_FN_SPELLING
            return ("ask", "Could you spell out your first name for me?")

        # Caller gives a corrected name directly (single clean word)
        cleaned = _strip_filler_prefix(text)
        tokens = _tokenise(cleaned)
        if len(tokens) == 1:
            return self._enter_fn_confirm(tokens[0].title(), spelled=False)

        # Ambiguous — re-ask
        if cand:
            return ("ask", f"Sorry — did I get your first name right? I have {cand}. Just say yes or no.")
        self._nc["substate"] = NC_FN_SPELLING
        return ("ask", "Could you spell out your first name for me?")

    # ── Substate: fn_spelling ────────────────────────────────────────────────

    def _fn_spelling(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect first name via letter-by-letter spelling.

        Buffers single-letter turns so "Q" then "U E N T I N" combines correctly.
        On a complete sequence → fn_confirm (spelled=True) so caller can verify.
        If caller just says a clean word directly, enter fn_confirm (spelled=False).
        """
        stripped = _strip_spelling_wrapper(text)
        buf = self._nc.get("fn_letter_buffer", [])

        # Meta-language while in spelling mode → re-ask for letters
        if _has_meta_language(stripped) and not _is_spelling_offer(stripped):
            return ("ask", (
                "I'm just listening for individual letters — "
                "could you say your first name one letter at a time?"
            ))

        # Try letter parsing on wrapper-stripped text, combining with buffer
        combined = (" ".join(buf) + " " + stripped).strip() if buf else stripped
        spelled = _parse_spelled_letters_tolerant(combined)
        if spelled:
            fn = spelled.title()
            self._nc["fn_letter_buffer"] = []
            logger.info("[NameCollector] fn_spelling: parsed %r (buf=%r) → %r", text[:30], buf, fn)
            return self._enter_fn_confirm(fn, spelled=True)

        # Caller may just say the name normally after entering spelling mode
        tokens = _tokenise(stripped)
        if len(tokens) == 1 and len(tokens[0]) >= 2:
            fn = tokens[0].title()
            self._nc["fn_letter_buffer"] = []
            logger.info("[NameCollector] fn_spelling: caller said normal word %r", fn)
            return self._enter_fn_confirm(fn, spelled=False)

        # Single letter → buffer it and acknowledge
        stripped_words = stripped.split()
        if len(stripped_words) == 1 and len(stripped_words[0]) == 1 and stripped_words[0].isalpha():
            buf.append(stripped_words[0].upper())
            self._nc["fn_letter_buffer"] = buf
            so_far = " ".join(buf)
            return ("ask", (
                f"OK, I've got {so_far} so far — keep going, "
                "or say all the remaining letters together."
            ))

        # Single letter on raw input (before stripping)
        words = text.strip().split()
        if len(words) == 1 and len(words[0]) == 1 and words[0].isalpha():
            buf.append(words[0].upper())
            self._nc["fn_letter_buffer"] = buf
            so_far = " ".join(buf)
            return ("ask", (
                f"OK, I've got {so_far} so far — keep going, "
                "or say all the remaining letters together."
            ))

        return ("ask", (
            "I'm just listening for individual letters — "
            "could you say your first name one letter at a time?"
        ))

    # ── Substate: sn_normal ──────────────────────────────────────────────────

    def _sn_normal(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect surname in normal mode.

        ALL accepts now route through sn_confirm so the caller can verify
        before the name is committed — regardless of surname length.

        Fast path: 1 valid token → sn_confirm (normal format).
        Salvage path: meta/spelling trigger with leading token → sn_confirm.
        Spelling detect: stripped text looks like a spelling sequence → sn_confirm (spelled).
        Double-barrelled: 2 tokens → sn_confirm (normal format).
        Escalation: ≥2 failures → enter sn_spelling.
        """
        # Combined salvage check: scan for BOTH meta-language AND spelling-offer
        # triggers BEFORE entering spelling mode so a leading surname token
        # ("Slater do you need help spelling that") is captured first.
        _combined_triggers = _META_LANGUAGE + _SPELLING_OFFER
        if any(p in text for p in _combined_triggers):
            # Try to salvage a leading name token before the trigger phrase
            cleaned_for_salvage = _strip_filler_prefix(text)
            token = _extract_leading_token(cleaned_for_salvage, _combined_triggers)
            if token and _is_valid_name_token(token):
                # Salvaged name with uncertainty signal → confirm before accepting
                return self._enter_sn_confirm(token, spelled=False)
            # No leading token — was this a pure spelling offer?
            if _is_spelling_offer(text):
                self._nc["substate"] = NC_SN_SPELLING
                self._nc["sn_letter_buffer"] = []
                return ("ask", self._question())
            # Pure meta-language with no usable name
            return self._sn_fail(
                "Sorry, I didn't catch that — could you say your surname again?"
            )

        cleaned = _strip_filler_prefix(text)

        # Detect spelling sequence in normal mode (e.g. "it's r o c h" after
        # stripping produces "r o c h" which has no 2+ char tokens but IS letters)
        spelled = _parse_spelled_letters_tolerant(cleaned)
        if spelled and len(spelled) >= 2:
            logger.info("[NameCollector] sn_normal: detected inline spelling %r → %r", text[:40], spelled)
            return self._enter_sn_confirm(spelled, spelled=True)

        tokens = _tokenise(cleaned)
        # Strip any date/scheduling tokens that may have leaked in
        tokens = [t for t in tokens if t.lower() not in _DATE_TOKENS]

        if len(tokens) == 1:
            sn = tokens[0].title()
            # All surnames now route through confirmation (not direct accept)
            return self._enter_sn_confirm(sn, spelled=False)

        if len(tokens) == 2:
            # Double-barrelled or hyphenated surname (e.g. "Smith-Jones")
            sn_combined = f"{tokens[0].title()}-{tokens[1].title()}"
            return self._enter_sn_confirm(sn_combined, spelled=False)

        if len(tokens) > 2:
            return self._sn_fail(
                "Sorry — could you just give me your surname on its own?"
            )

        # No valid tokens — check if this is a single letter (start of spelling).
        # Caller may begin spelling their surname from sn_normal without an
        # explicit offer ("S ... L ... A ...").  Silently enter sn_spelling and
        # buffer the letter so the next turn can combine them.
        raw_words = text.strip().split()
        if len(raw_words) == 1 and len(raw_words[0]) == 1 and raw_words[0].isalpha():
            letter = raw_words[0].upper()
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["sn_letter_buffer"] = [letter]
            logger.info("[NameCollector] sn_normal: single letter %r — entering sn_spelling with buffer", letter)
            return ("ask", (
                f"Got {letter} — keep going with the remaining letters."
            ))

        # No usable input at all
        return self._sn_fail(
            "Sorry — could you say your surname again?"
        )

    # ── Substate: sn_spelling ────────────────────────────────────────────────

    def _sn_spelling(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect surname via letter-by-letter spelling.

        Applies aggressive wrapper stripping before letter parsing so that
        "yeah it's r o c h", "no it's r o c h", "no i said r o c h" etc.
        resolve cleanly to "Roch".

        Buffers single-letter turns so "R" then "O C H" combines to "ROCH".

        Meta-language in spelling mode → re-ask for letters (do NOT parse
        the meta phrase as a surname token).

        On success, always enters sn_confirm (spelled=True) so the caller
        can verify the captured letters before they are committed.
        """
        # Strip wrapper language first
        stripped = _strip_spelling_wrapper(text)
        buf = self._nc.get("sn_letter_buffer", [])

        # Meta-language while in spelling mode → re-ask for letters.
        # This is the key guard that prevents "did you catch that" →
        # _tokenise → ["catch"] → accepted as surname "Catch".
        if _has_meta_language(stripped) and not _is_spelling_offer(stripped):
            return ("ask", (
                "Could you spell your surname one letter at a time for me?"
            ))

        # Also guard the raw text (before stripping) — catches meta phrases
        # that stripping doesn't fully remove.
        if _has_meta_language(text) and not _is_spelling_offer(text):
            return ("ask", (
                "Could you spell your surname one letter at a time for me?"
            ))

        # Try to parse a complete spelling sequence, combining with any buffered letters
        combined = (" ".join(buf) + " " + stripped).strip() if buf else stripped
        spelled = _parse_spelled_letters_tolerant(combined)
        if spelled:
            self._nc["sn_letter_buffer"] = []
            logger.info("[NameCollector] sn_spelling: parsed %r (buf=%r) → %r", text[:40], buf, spelled)
            return self._enter_sn_confirm(spelled.title(), spelled=True)

        # Caller may just say the surname directly (switched out of spelling mode)
        tokens = _tokenise(stripped)
        if len(tokens) == 1 and len(tokens[0]) >= 2:
            sn = tokens[0].title()
            self._nc["sn_letter_buffer"] = []
            logger.info("[NameCollector] sn_spelling: caller said normal word %r", sn)
            return self._enter_sn_confirm(sn, spelled=False)

        # Single letter on stripped input → buffer and acknowledge
        stripped_words = stripped.split()
        if len(stripped_words) == 1 and len(stripped_words[0]) == 1 and stripped_words[0].isalpha():
            buf.append(stripped_words[0].upper())
            self._nc["sn_letter_buffer"] = buf
            so_far = " ".join(buf)
            return ("ask", (
                f"Got {so_far} so far — keep going, "
                "or say all the remaining letters in one go."
            ))

        # Single letter on original (before stripping) — caller spelling one at a time
        words = text.strip().split()
        if len(words) == 1 and len(words[0]) == 1 and words[0].isalpha():
            buf.append(words[0].upper())
            self._nc["sn_letter_buffer"] = buf
            so_far = " ".join(buf)
            return ("ask", (
                f"Got {so_far} so far — keep going, "
                "or say all the remaining letters in one go."
            ))

        return ("ask", (
            "Could you spell your surname one letter at a time for me?"
        ))

    # ── Substate: sn_confirm ─────────────────────────────────────────────────

    def _sn_confirm(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Confirm a surname candidate.

        Normal confirm:  "I've got Roch — is that right?"
        Spelled confirm: "I've got R O C H — is that right?"

        Branches:
          YES             → accept
          NO              → enter sn_spelling (strict spelling mode — not a retry)
          Spelled letters → update candidate (spelled confirm), re-confirm
          Clean word      → update candidate (normal confirm), re-confirm
          Meta-language   → re-ask yes/no (do NOT parse meta as a surname token)
          Ambiguous       → re-ask
        """
        cand = self._nc.get("surname_candidate") or ""
        fn = self.first_name or ""

        # Spelling offer during confirmation
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["surname_candidate"] = None
            self._nc["sn_letter_buffer"] = []
            self._s.pop("spelling_confirm_surname", None)
            return ("ask", self._question())

        # Spelled correction: caller provides alternative spelling
        spelled = _parse_spelled_letters(text)
        if spelled:
            return self._enter_sn_confirm(spelled.title(), spelled=True)

        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        # Strong-denial override: "no that is not right" fires both "right" (YES)
        # and "no"/"not right" (NO) — prefer NO when explicit denial phrases present.
        if has_yes and has_no and any(p in text for p in _STRONG_NO):
            has_yes = False

        # YES — accept candidate
        if has_yes and not has_no:
            full = f"{fn} {cand}".strip().title() if fn else cand.title()
            self._accept(full)
            return ("accept", full)

        # NO — enter strict sn_spelling immediately
        if has_no and not has_yes:
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["surname_candidate"] = None
            self._nc["sn_letter_buffer"] = []
            self._s.pop("spelling_confirm_surname", None)
            logger.info("[NameCollector] sn_confirm: NO — entering sn_spelling")
            return ("ask", "No problem — could you spell it out letter by letter for me?")

        # Both YES and NO signals present (e.g. "yes, no wait…") — ambiguous;
        # do NOT fall through to token extraction or a noise word becomes a surname.
        if has_yes and has_no:
            if cand:
                return ("ask", f"Sorry — just yes or no: is {cand} right?")
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["sn_letter_buffer"] = []
            return ("ask", "Could you spell out your surname for me?")

        # Meta-language in confirm context → re-ask yes/no.
        # CRITICAL: must happen before the token-extraction path so that
        # "if you catch" or "did you catch that" cannot produce "Catch" as surname.
        cleaned = _strip_prefixes(text)
        if _has_meta_language(text) or _has_meta_language(cleaned):
            if cand:
                return ("ask", f"Sorry — I just need a yes or a no. I've got {cand} — is that right?")
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["sn_letter_buffer"] = []
            return ("ask", "Could you spell out your surname for me?")

        # Caller gave a clean single word (the correct surname directly)
        tokens = _tokenise(cleaned)
        if len(tokens) == 1:
            sn = tokens[0].title()
            return self._enter_sn_confirm(sn, spelled=False)

        # Ambiguous — re-ask
        if cand:
            return ("ask", f"Sorry — did you say {cand}? Just say yes or no.")
        self._nc["substate"] = NC_SN_SPELLING
        self._nc["sn_letter_buffer"] = []
        return ("ask", "Could you spell out your surname for me?")

    # ── Retry / escalation helpers ────────────────────────────────────────────

    def _fn_fail(self, re_ask: str) -> Tuple[str, str]:
        """
        Increment first-name retry counter.
        After 2 failures, auto-escalate to fn_spelling.
        """
        self._nc["fn_retries"] = self._nc.get("fn_retries", 0) + 1
        if self._nc["fn_retries"] >= 2:
            self._nc["substate"] = NC_FN_SPELLING
            self._nc["fn_letter_buffer"] = []
            logger.info(
                "[NameCollector] fn_fail: escalating to fn_spelling "
                "after %d retries", self._nc["fn_retries"],
            )
            return ("ask", (
                "Let me try a different way — "
                "please say your first name one letter at a time."
            ))
        return ("ask", re_ask)

    def _sn_fail(self, re_ask: str) -> Tuple[str, str]:
        """
        Increment surname retry counter.
        After 2 failures, auto-escalate to sn_spelling.
        """
        self._nc["sn_retries"] = self._nc.get("sn_retries", 0) + 1
        if self._nc["sn_retries"] >= 2:
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["sn_letter_buffer"] = []
            logger.info(
                "[NameCollector] sn_fail: escalating to sn_spelling "
                "after %d retries", self._nc["sn_retries"],
            )
            return ("ask", (
                "Let me try a different approach — "
                "please spell out your surname one letter at a time."
            ))
        return ("ask", re_ask)

    # ── State-transition helpers ──────────────────────────────────────────────

    def _store_first_name(self, fn: str) -> None:
        """Store first name, advance to sn_normal, sync legacy session var.
        Clears letter buffers and pending_surname (consumed by caller)."""
        self._nc["first_name"]       = fn
        self._nc["substate"]         = NC_SN_NORMAL
        self._nc["fn_letter_buffer"] = []
        self._nc["sn_letter_buffer"] = []
        self._s["name_fragment"] = fn  # legacy compat
        logger.info("[NameCollector] stored first_name=%r → sn_normal", fn)

    def _enter_fn_confirm(self, candidate: str, spelled: bool = False) -> Tuple[str, str]:
        """Enter fn_confirm substate with the given first-name candidate."""
        self._nc["fn_candidate"] = candidate
        self._nc["fn_spelled"]   = spelled
        self._nc["substate"]     = NC_FN_CONFIRM
        logger.info("[NameCollector] entering fn_confirm for %r (spelled=%s)", candidate, spelled)
        if spelled:
            spaced = " ".join(list(candidate.upper()))
            return ("ask", f"I've got {spaced} — is that right?")
        return ("ask", f"I've got {candidate} — is that right?")

    def _enter_sn_confirm(self, candidate: str, spelled: bool = False) -> Tuple[str, str]:
        """Enter sn_confirm substate with the given surname candidate.

        spelled=True  → readback uses spaced letters: "I've got R O C H — is that right?"
        spelled=False → readback uses the word:        "I've got Roch — is that right?"
        """
        self._nc["surname_candidate"] = candidate
        self._nc["sn_spelled"]        = spelled
        self._nc["substate"]          = NC_SN_CONFIRM
        self._s["spelling_confirm_surname"] = candidate  # legacy compat
        logger.info("[NameCollector] entering sn_confirm for %r (spelled=%s)", candidate, spelled)
        if spelled:
            spaced = " ".join(list(candidate.upper()))
            return ("ask", f"I've got {spaced} — is that right?")
        return ("ask", f"I've got {candidate} — is that right?")

    def _accept(self, full: str) -> None:
        """
        Store accepted full name in session.

        Updates both the _nc substate and all legacy session vars so that
        downstream code (phone readback, CONFIRM_BOOKING, lookup) continues
        to work without modification.
        """
        self._nc["substate"]          = NC_DONE
        self._nc["surname_candidate"] = None
        self._nc["sn_letter_buffer"]  = []
        self._nc["fn_letter_buffer"]  = []
        # Legacy session vars kept in sync
        self._s["full_name"] = full
        col = self._s.setdefault("collected", {})
        col["full_name"] = full
        col["name"]      = full
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        logger.info("[NameCollector] accepted full_name=%r", full)
