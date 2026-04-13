"""
app/media_streams/name_collector.py
====================================
Deterministic unified name-collection engine for the Theorem Health receptionist.

Shared across ALL name-collection states:
  COLLECT_NAME, COLLECT_NAME_RETURNING, COLLECT_NAME_RESCHEDULE, COLLECT_NAME_CANCEL

PHILOSOPHY
----------
• Normal-first: collect first name then surname conversationally.
• Field-level confirmation: every field is read back once for the caller to
  confirm.  Denial of a confirmation triggers exactly ONE normal re-ask, then
  the best-effort value is stored and the flow moves on unconditionally.
• No spelling mode in the live call flow.  If a name is imperfect after one
  re-ask the caller can correct it via the post-call confirmation SMS.
• Deterministic only: no LLM anywhere in this module.
• No dead ends: the flow always advances, never traps the caller.

SUBSTATES  (stored in session["_nc"]["substate"])
-----------
  fn_normal    — collecting first name (initial state)
  fn_confirm   — first-name candidate awaiting yes/no confirmation
  fn_reask     — one normal re-ask after first-name denial; stores best effort
  sn_normal    — collecting surname (entered after first name is confirmed/stored)
  sn_confirm   — surname candidate awaiting confirmation
  sn_reask     — one normal re-ask after surname denial; stores best effort
  done         — full name accepted (transient — consumed immediately by flow.py)

  fn_spelling / sn_spelling — kept as constants for code stability; NEVER
  entered from any live booking path.

CONFIRMATION CONTRACT
---------------------
  FIRST NAME
    fn_normal → fn_confirm ("I've got Quentin — is that right?")
      YES           → sn_normal (fn_confirmed=True)
      NO / any deny → fn_reask  ("Sorry, I didn't quite catch that — please say: my first name is...")
    fn_reask  → store best effort (fn_confirmed=False) → sn_normal
      Response:  "Okay, noted — I'll send you a confirmation message after
                  the call, and if the name needs correcting you can reply there.
                  And what's your surname?"

  SURNAME
    sn_normal → sn_confirm ("I've got Roch — is that right?")
      YES           → accept (sn_confirmed=True)
      NO / any deny → sn_reask ("Sorry, I didn't quite catch that — please say: my surname is...")
    sn_reask  → store best effort (sn_confirmed=False) → accept
      Before accepting: session["_nc_accept_preamble"] is set so flow.py can
      play "Okay, noted — ..." before advancing to the next step.

RETURN PROTOCOL  (from NameCollector.handle())
--------------
  ("ask",    "question text")  — speak and wait for the caller's next turn
  ("repair", "question text")  — speak (clarification/repeat) and wait
  ("accept", "First Surname")  — name is complete; flow.py stores + advances step

  When accept is returned after a best-effort surname, flow.py checks:
    session.pop("_nc_accept_preamble", None)
  and plays that phrase before advancing to the next question.

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
  session["_nc_accept_preamble"]    — transient: "Okay noted…" phrase for flow.py
                                       to play before advancing after a best-effort accept

All legacy vars are updated by this module so downstream code (phone readback,
CONFIRM_BOOKING, lookup validation) continues to work unchanged.
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Substate constants ──────────────────────────────────────────────────────────
NC_FN_NORMAL   = "fn_normal"
NC_FN_CONFIRM  = "fn_confirm"    # first-name candidate awaiting confirmation
NC_FN_REASK    = "fn_reask"      # one normal re-ask after first-name denial
NC_FN_SPELLING = "fn_spelling"   # kept for code stability — NOT entered in live flow
NC_SN_NORMAL   = "sn_normal"
NC_SN_CONFIRM  = "sn_confirm"    # surname candidate awaiting confirmation
NC_SN_REASK    = "sn_reask"      # one normal re-ask after surname denial
NC_SN_SPELLING = "sn_spelling"   # kept for code stability — NOT entered in live flow
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
    "no i said ", "no it's ", "no it is ",
    "sorry it's ", "sorry it is ",
    "i said ", "i meant ", "i mean ",
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
    "want", "can", "spell", "say", "tell", "each", "letter",
    "catch", "hear", "noted", "get", "not",
    "it's", "that's", "there's", "here's", "what's",
    # Auxiliary verbs that are never plausible names
    "shall", "would", "could", "might",
})

# ── Meta/acknowledgement words that are never plausible surnames ──────────────
_META_WORDS = frozenset({
    "noted", "understood", "received", "confirmed", "acknowledged",
    "recorded", "registered", "entered", "captured",
    "cheers", "brilliant", "great", "lovely", "wonderful",
    # Structural label words — these appear in prefix phrases like
    # "my surname is", "my last name is", "my first name is".
    # After prefix stripping they become the sole surviving token.
    # They must NEVER be promoted into a name candidate.
    "surname", "name", "firstname", "lastname", "familyname",
    "first", "family",
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
    "is that noted", "have you noted", "did you note",
    "is that ok", "is that okay", "is that alright",
    "have you got that", "did you get that",
    "can you hear", "do you hear", "if you catch", "if you get that",
    "i can spell", "can spell", "i'll spell that",
    "want to spell", "going to spell", "i could spell",
    # Additional repair / control phrases not covered above ──────────────────
    # "if you not catch that", "if you didn't catch that" etc.
    "if you didn't", "if you not", "if you couldn't",
    # "let me try again", "let me say it again"
    "let me try", "let me say",
    # "you not catch", "you couldn't hear", "didn't get that"
    "not catch", "couldn't hear", "didn't get that",
    # catch plain repair signals not already in _REPAIR
    "you didn't hear", "didn't hear that",
)

# ── Spelling offer phrases (kept for helper function; no longer route to spelling mode) ──
_SPELLING_OFFER: tuple = (
    "shall i spell", "should i spell", "do you want me to spell",
    "want me to spell", "can i spell", "let me spell",
    "do you need me to spell", "need me to spell",
    "do you need help spelling", "need help spelling",
    "should i say each letter", "spell it out", "spell my name",
    "spell that out", "spell it", "i'll spell",
    "would it help if i spell", "want me to say each letter",
    "shall i say each letter",
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

# ── NATO phonetic alphabet (kept for code stability) ─────────────────────────
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
_STRONG_NO: frozenset = frozenset({
    "not right", "that's wrong", "not correct",
    "that's not right", "wrong", "incorrect", "not that",
})

# Phrase spoken after storing a best-effort name (first or surname) without
# explicit confirmation.  Signals to the caller that correction is possible
# via post-call SMS.
_BEST_EFFORT_ACK = (
    "Okay, noted — I'll send you a confirmation message after the call, "
    "and if the name needs correcting you can reply there."
)


# ── Leading filler stripper ───────────────────────────────────────────────────
_LEADING_FILLERS_RE = re.compile(
    r"^(?:yeah|yep|nah|ok|okay|right|sorry|well|uh|um|er|ah|so|and|no)\s+",
    re.IGNORECASE,
)

# ── Denial prefix stripper (for inline-correction extraction) ─────────────────
# Matches "no" with any trailing punctuation/whitespace so "no, it's X" →  "it's X"
_DENIAL_PREFIX_RE = re.compile(r'^no[,\.!\s]+', re.IGNORECASE)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _strip_prefixes(text: str) -> str:
    """Remove the first matching name-label prefix from normalised text."""
    for p in _PREFIXES:
        if text.startswith(p):
            return text[len(p):].strip()
    return text


def _strip_filler_prefix(text: str) -> str:
    """Strip one leading filler word then apply prefix stripping."""
    text = _LEADING_FILLERS_RE.sub("", text)
    return _strip_prefixes(text)


def _strip_spelling_wrapper(text: str) -> str:
    """
    Aggressively strip correction-wrapper language (kept for code stability;
    no longer called in the live booking flow but preserved for any callers
    that may reference it directly).
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
    """Return True if token looks like a plausible name element."""
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
    stop phrase in text.  Returns title-cased token or None if ambiguous.
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
    Kept for code stability — not called in the live booking flow.
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
            if i + 2 < len(words) and words[i + 1] == "for":
                i += 3
                continue
        elif w in _NATO:
            letters.append(_NATO[w].upper())
        elif w == "for":
            i += 1
            continue
        else:
            return None
        i += 1
    if len(letters) < 2:
        return None
    return "".join(letters).title()


def _parse_spelled_letters_tolerant(text: str) -> Optional[str]:
    """
    Like _parse_spelled_letters but tolerates a single trailing non-letter word.
    Kept for code stability — not called in the live booking flow.
    """
    result = _parse_spelled_letters(text)
    if result is not None:
        return result
    parts = text.rsplit(" ", 1)
    if len(parts) < 2:
        return None
    trimmed, trailing = parts[0].strip(), parts[1].strip()
    if not trimmed:
        return None
    result_trimmed = _parse_spelled_letters(trimmed)
    if result_trimmed is None:
        return None
    if trailing.lower().startswith(result_trimmed[:2].lower()):
        return result_trimmed
    if trailing.lower() in _FUNCTION_WORDS or trailing.lower() in _DOMAIN_WORDS:
        return result_trimmed
    return None


def _is_spelling_offer(text: str) -> bool:
    return any(p in text for p in _SPELLING_OFFER)


def _is_repair_request(text: str) -> bool:
    return any(p in text for p in _REPAIR)


def _has_meta_language(text: str) -> bool:
    return any(p in text for p in _META_LANGUAGE)


def _is_incomplete_scaffold(text: str) -> bool:
    """
    Return True if the utterance is a pure setup/scaffold fragment with no
    name content — e.g. "my surname is", "my name is", "it's", "my".

    These arise when STT finalizes early on a multi-fragment utterance (the
    caller said "my surname is" and the name chunk arrived in a separate STT
    event).  They must NOT count as failed name-capture attempts or the retry
    counter gets inflated, causing the system to treat a subsequently clean
    name as degraded and wrongly fire the correction-SMS flag.
    """
    lowered = text.lower().strip()
    if not lowered:
        return False
    # Single isolated setup tokens
    if lowered in {"my", "it's", "its", "i'm", "im"}:
        return True
    # Exact match of any known name-label prefix (without trailing name content).
    # _PREFIXES entries carry a trailing space (e.g. "my surname is "); we strip
    # that to get the bare phrase and compare against the full utterance.
    for p in _PREFIXES:
        p_core = p.strip()  # e.g. "my surname is"
        if lowered == p_core:
            return True
    return False


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
            # Check for best-effort preamble before advancing:
            preamble = session.pop("_nc_accept_preamble", None)
            if preamble:
                await tts.put(preamble)
            full_name = payload
        else:
            question_to_speak = payload

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
            "fn_confirmed":      False,  # True when caller said YES
            "first_name":        None,
            # Surname tracking
            "surname_candidate": None,
            "sn_confirmed":      False,  # True when caller said YES
            "pending_surname":   None,   # pre-queued sn token from 2-token fn_normal
            # Retry counters
            "fn_retries":        0,
            "sn_retries":        0,
        }

    def reset(self) -> None:
        """Full reset — call when stepping back into ANY COLLECT_NAME state."""
        self._s["_nc"] = {
            "substate":          NC_FN_NORMAL,
            "fn_candidate":      None,
            "fn_confirmed":      False,
            "first_name":        None,
            "surname_candidate": None,
            "sn_confirmed":      False,
            "pending_surname":   None,
            "fn_retries":        0,
            "sn_retries":        0,
        }
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        self._s.pop("_nc_accept_preamble", None)
        self._s.pop("needs_name_correction_sms", None)
        col = self._s.get("collected", {})
        col.pop("full_name", None)
        col.pop("name", None)

    def reset_to_surname(self) -> None:
        """Partial reset — keep first name, restart surname collection."""
        nc = self._s.get("_nc", {})
        self._s["_nc"] = {
            "substate":          NC_SN_NORMAL,
            "fn_candidate":      None,
            "fn_confirmed":      nc.get("fn_confirmed", False),
            "first_name":        nc.get("first_name"),
            "surname_candidate": None,
            "sn_confirmed":      False,
            "pending_surname":   None,
            "fn_retries":        nc.get("fn_retries", 0),
            "sn_retries":        0,
        }
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        self._s.pop("_nc_accept_preamble", None)
        self._s.pop("needs_name_correction_sms", None)
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
            return f"I've got {cand} — is that right?" if cand else "What's your first name please?"
        if ss == NC_FN_REASK:
            return "Sorry, I didn't quite catch that — please say: my first name is..."
        if ss in (NC_FN_SPELLING,):
            return "What's your first name please?"
        if ss == NC_SN_NORMAL:
            return "And what's your surname?"
        if ss == NC_SN_CONFIRM:
            cand = self._nc.get("surname_candidate") or ""
            return f"I've got {cand} — is that right?" if cand else "And what's your surname?"
        if ss == NC_SN_REASK:
            return "Sorry, I didn't quite catch that — please say: my surname is..."
        if ss in (NC_SN_SPELLING,):
            return "And what's your surname?"
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
        if _is_repair_request(text):
            return ("repair", self._question())

        if ss == NC_FN_NORMAL:
            return self._fn_normal(text, raw)
        if ss == NC_FN_CONFIRM:
            return self._fn_confirm(text, raw)
        if ss == NC_FN_REASK:
            return self._fn_reask(text, raw)
        if ss == NC_SN_NORMAL:
            return self._sn_normal(text, raw)
        if ss == NC_SN_CONFIRM:
            return self._sn_confirm(text, raw)
        if ss == NC_SN_REASK:
            return self._sn_reask(text, raw)

        # Unknown substate (incl. legacy spelling states) — defensive reset
        logger.warning("[NameCollector] unknown/legacy substate %r — resetting", ss)
        self._init_state()
        return ("ask", self._question())

    # ── Substate: fn_normal ──────────────────────────────────────────────────

    def _fn_normal(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect first name in normal mode.

        Fast path:  1 valid token → fn_confirm.
        Two-token:  first token → fn_confirm, second queued as pending_surname.
        Scaffold:   pure setup phrase with no name — re-ask WITHOUT retry count.
        Meta/noise: salvage leading token if possible; otherwise re-ask.
        Spelling offer: treated as garbled input (not routed to spelling mode).
        """
        # Incomplete scaffold ("my name is", "it's", "my") — STT finalized early
        # before the name arrived.  Re-ask without counting this as a failed attempt.
        if _is_incomplete_scaffold(text):
            logger.info("[NameCollector] fn_normal: incomplete scaffold %r — scaffold_continue", text)
            return ("scaffold_continue", "What's your first name please?")

        cleaned = _strip_filler_prefix(text)

        # Negation: "I'm not Sarah, it's Emma" → extract corrected name
        if any(text.startswith(p) or (" " + p) in text for p in _NEGATION):
            m = re.search(
                r"(?:it'?s|its|i'?m|im|the name(?:'s| is)?|actually|no it'?s)\s+"
                r"([a-z][a-z\-']{1,})",
                cleaned,
            )
            if m:
                token = m.group(1).strip().title()
                if _is_valid_name_token(token):
                    return self._enter_fn_confirm(token)
            self._nc["fn_retries"] = 0
            return ("ask", "No problem — what's your first name please?")

        # Meta-language: try to salvage a leading name token
        if _has_meta_language(cleaned):
            token = _extract_leading_token(cleaned, _META_LANGUAGE)
            if token and _is_valid_name_token(token):
                return self._enter_fn_confirm(token)
            return self._fn_fail(
                "Sorry, I didn't quite catch that — what's your first name?"
            )

        tokens = _tokenise(cleaned)

        # Two valid tokens → treat as "FirstName Surname"
        if len(tokens) == 2:
            fn_tok = tokens[0].title()
            sn_tok = tokens[1].title()
            self._nc["pending_surname"] = sn_tok
            return self._enter_fn_confirm(fn_tok)

        # One valid token → confirm it
        if len(tokens) == 1:
            return self._enter_fn_confirm(tokens[0].title())

        # Nothing usable (includes spelling offers — treated as garbled)
        return self._fn_fail(
            "Sorry, I didn't quite catch that — could you say your first name again?"
        )

    # ── Substate: fn_confirm ─────────────────────────────────────────────────

    def _fn_confirm(self, text: str, raw: str) -> Tuple[str, str]:
        """
        First-name candidate awaiting yes/no confirmation.
        Question: "I've got Quentin — is that right?"

        YES              → store first name (confirmed), advance to surname.
        NO / deny signal → fn_reask (one normal re-ask, no spelling mode).
        Clean correction → update candidate, re-confirm.
        Meta / ambiguous → re-ask yes/no (stay in fn_confirm).
        """
        cand = self._nc.get("fn_candidate") or ""

        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        # Strong-denial: "no that is not right" — discard weak YES signal
        if has_yes and has_no and any(p in text for p in _STRONG_NO):
            has_yes = False

        # YES — store first name as confirmed (or degraded if retries occurred)
        if has_yes and not has_no:
            _fn_degraded = self._nc.get("fn_retries", 0) > 0
            if _fn_degraded:
                self._s["needs_name_correction_sms"] = True
                logger.info(
                    "[NameCollector] fn_confirm: YES after %d fn_retries — "
                    "fn_confirmed=False, needs_name_correction_sms=True",
                    self._nc["fn_retries"],
                )
            pending_sn = self._nc.get("pending_surname")
            self._store_first_name(cand, confirmed=not _fn_degraded)
            if pending_sn:
                self._nc["pending_surname"] = None
                logger.info("[NameCollector] fn_confirm: YES — sn_confirm for pending %r", pending_sn)
                return self._enter_sn_confirm(pending_sn)
            return ("ask", "And what's your surname?")

        # NO / spelling offer / strong denial — one normal re-ask, no spelling
        # Spelling offers ("shall I spell it?") are treated as a denial: the
        # caller is unhappy with the readback, so give them one more chance.
        is_denial = (has_no and not has_yes) or _is_spelling_offer(text)
        if is_denial:
            # Inline correction: "no, it's Quentin" / "no Quentin" — extract the
            # corrected name directly rather than bouncing through fn_reask.
            _tail = _DENIAL_PREFIX_RE.sub("", text).strip()
            if _tail:
                _corr_tokens = _tokenise(_strip_filler_prefix(_tail))
                if _corr_tokens:
                    logger.info(
                        "[NameCollector] fn_confirm: inline correction %r → confirming",
                        _corr_tokens[0],
                    )
                    return self._enter_fn_confirm(_corr_tokens[0].title())
            # Plain denial — one normal re-ask
            self._nc["substate"] = NC_FN_REASK
            self._nc["fn_candidate"] = cand   # keep candidate as fallback
            # Clear pending_surname — full name was apparently wrong
            self._nc["pending_surname"] = None
            logger.info("[NameCollector] fn_confirm: denial — entering fn_reask")
            return ("ask", "Sorry about that — what's your first name?")

        # Both yes+no without strong denial — ambiguous; re-ask cleanly
        if has_yes and has_no:
            if cand:
                return ("ask", f"Sorry — just yes or no: is {cand} right?")
            self._nc["substate"] = NC_FN_REASK
            return ("ask", "Sorry about that — what's your first name?")

        # Meta-language — re-ask yes/no
        if _has_meta_language(text):
            if cand:
                return ("ask", f"Sorry — I just need a yes or a no. I've got {cand} — is that right?")
            self._nc["substate"] = NC_FN_REASK
            return ("ask", "Sorry about that — what's your first name?")

        # Caller gives a corrected name directly (single clean word)
        cleaned = _strip_filler_prefix(text)
        tokens = _tokenise(cleaned)
        if len(tokens) == 1:
            return self._enter_fn_confirm(tokens[0].title())

        # Ambiguous — re-ask yes/no if we have a candidate; otherwise re-ask name
        if cand:
            return ("ask", f"Sorry — did I get your first name right? I have {cand}. Just say yes or no.")
        self._nc["substate"] = NC_FN_REASK
        return ("ask", "Sorry about that — what's your first name?")

    # ── Substate: fn_reask ───────────────────────────────────────────────────

    def _fn_reask(self, text: str, raw: str) -> Tuple[str, str]:
        """
        One normal re-ask after first-name confirmation denial.
        Question played: "Sorry, I didn't quite catch that — please say: my first name is..."

        Whatever the caller says next is stored as best effort and the flow
        moves on unconditionally.  No second confirmation loop, no spelling.

        Returns a combined phrase that acknowledges the best-effort store AND
        asks for the surname in a single turn, so there is no dead air.
        """
        cleaned = _strip_filler_prefix(text)

        # Try to extract a valid name token from the response
        best: Optional[str] = None

        # Ignore spelling offers — treat as "no name given"
        if not _is_spelling_offer(text) and not _has_meta_language(cleaned):
            tokens = _tokenise(cleaned)
            if tokens:
                best = tokens[0].title()

        # Fallback: use the previous fn_candidate if we got nothing useful
        if not best:
            best = self._nc.get("fn_candidate") or ""
            if not best:
                best = "Unknown"

        self._store_first_name(best, confirmed=False)
        self._s["needs_name_correction_sms"] = True
        logger.info(
            "[NameCollector] fn_reask: best-effort first_name=%r → "
            "needs_name_correction_sms=True",
            best,
        )

        return (
            "ask",
            f"{_BEST_EFFORT_ACK} And what's your surname?",
        )

    # ── Substate: sn_normal ──────────────────────────────────────────────────

    def _sn_normal(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect surname in normal mode.

        ALL accepts route through sn_confirm so the caller can verify once.
        Scaffold:   pure setup phrase with no name — re-ask WITHOUT retry count.
        Meta-language with a leading token → salvage into sn_confirm.
        Spelling offers → treated as garbled (not routed to spelling mode).
        """
        # Incomplete scaffold ("my surname is", "it's", "my") — STT finalized
        # early before the name arrived.  Re-ask without counting as failure.
        if _is_incomplete_scaffold(text):
            logger.info("[NameCollector] sn_normal: incomplete scaffold %r — scaffold_continue", text)
            return ("scaffold_continue", "And what's your surname?")

        # Salvage: name token before a meta/spelling trigger phrase
        _triggers = _META_LANGUAGE + _SPELLING_OFFER
        if any(p in text for p in _triggers):
            cleaned_for_salvage = _strip_filler_prefix(text)
            token = _extract_leading_token(cleaned_for_salvage, _triggers)
            if token and _is_valid_name_token(token):
                return self._enter_sn_confirm(token)
            # Pure meta or spelling offer with no leading name — re-ask
            return self._sn_fail(
                "Sorry, I didn't catch that — could you say your surname again?"
            )

        cleaned = _strip_filler_prefix(text)
        tokens = _tokenise(cleaned)
        tokens = [t for t in tokens if t.lower() not in _DATE_TOKENS]

        if len(tokens) == 1:
            return self._enter_sn_confirm(tokens[0].title())

        if len(tokens) == 2:
            # Double-barrelled surname
            sn_combined = f"{tokens[0].title()}-{tokens[1].title()}"
            return self._enter_sn_confirm(sn_combined)

        if len(tokens) > 2:
            return self._sn_fail(
                "Sorry — could you just give me your surname on its own?"
            )

        # No valid tokens
        return self._sn_fail(
            "Sorry — could you say your surname again?"
        )

    # ── Substate: sn_confirm ─────────────────────────────────────────────────

    def _sn_confirm(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Confirm a surname candidate.
        Question: "I've got Roch — is that right?"

        YES              → accept (sn_confirmed=True).
        NO / deny signal → sn_reask (one normal re-ask, no spelling mode).
        Clean correction → update candidate, re-confirm.
        Meta / ambiguous → re-ask yes/no (stay in sn_confirm).
        """
        cand = self._nc.get("surname_candidate") or ""
        fn   = self.first_name or ""

        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        # Strong-denial: discard weak YES signal
        if has_yes and has_no and any(p in text for p in _STRONG_NO):
            has_yes = False

        # YES — accept
        # Trust check: if sn_retries > 0 the candidate arrived after at least one
        # failed extraction, making it less reliable than a clean first-attempt
        # capture.  Accept for flow continuity but mark as unreliable so that
        # _accept() sets needs_name_correction_sms and the outgoing SMS includes
        # the correction instruction.
        if has_yes and not has_no:
            full = f"{fn} {cand}".strip().title() if fn else cand.title()
            _sn_degraded = self._nc.get("sn_retries", 0) > 0
            self._nc["sn_confirmed"] = not _sn_degraded
            if _sn_degraded:
                # Signal flow.py to play the "noted" acknowledgement
                self._s["_nc_accept_preamble"] = _BEST_EFFORT_ACK
                self._s["needs_name_correction_sms"] = True
                logger.info(
                    "[NameCollector] sn_confirm: YES after %d sn_retries — "
                    "sn_confirmed=False, preamble set, needs_name_correction_sms=True",
                    self._nc["sn_retries"],
                )
            self._accept(full)
            return ("accept", full)

        # NO / spelling offer — one normal re-ask, no spelling mode
        is_denial = (has_no and not has_yes) or _is_spelling_offer(text)
        if is_denial:
            # Inline correction: "no, it's Roch" / "no Roch" / "no my surname is Roch"
            _tail = _DENIAL_PREFIX_RE.sub("", text).strip()
            if _tail:
                _corr_tokens = _tokenise(_strip_filler_prefix(_tail))
                if _corr_tokens:
                    logger.info(
                        "[NameCollector] sn_confirm: inline correction %r → confirming",
                        _corr_tokens[0],
                    )
                    return self._enter_sn_confirm(_corr_tokens[0].title())
            # Plain denial — one normal re-ask
            self._nc["substate"] = NC_SN_REASK
            self._nc["surname_candidate"] = cand   # keep as fallback
            logger.info("[NameCollector] sn_confirm: denial — entering sn_reask")
            return ("ask", "Sorry about that — what's your surname?")

        # Both yes+no without strong denial — ambiguous
        if has_yes and has_no:
            if cand:
                return ("ask", f"Sorry — just yes or no: is {cand} right?")
            self._nc["substate"] = NC_SN_REASK
            return ("ask", "Sorry about that — what's your surname?")

        # Meta-language — re-ask yes/no
        cleaned = _strip_prefixes(text)
        if _has_meta_language(text) or _has_meta_language(cleaned):
            if cand:
                return ("ask", f"Sorry — I just need a yes or a no. I've got {cand} — is that right?")
            self._nc["substate"] = NC_SN_REASK
            return ("ask", "Sorry about that — what's your surname?")

        # Caller gave a clean single word (the correct surname directly)
        tokens = _tokenise(cleaned)
        if len(tokens) == 1:
            sn = tokens[0].title()
            return self._enter_sn_confirm(sn)

        # Ambiguous
        if cand:
            return ("ask", f"Sorry — did you say {cand}? Just say yes or no.")
        self._nc["substate"] = NC_SN_REASK
        return ("ask", "Sorry about that — what's your surname?")

    # ── Substate: sn_reask ───────────────────────────────────────────────────

    def _sn_reask(self, text: str, raw: str) -> Tuple[str, str]:
        """
        One normal re-ask after surname confirmation denial.
        Question played: "Sorry, I didn't quite catch that — please say: my surname is..."

        Whatever the caller says next is stored as best effort.
        Sets session["_nc_accept_preamble"] so flow.py can play the
        "Okay, noted…" acknowledgement before advancing to the next step.
        Returns ("accept", full_name) immediately.
        """
        cleaned = _strip_filler_prefix(text)
        fn = self.first_name or ""

        best: Optional[str] = None
        if not _is_spelling_offer(text) and not _has_meta_language(cleaned):
            tokens = _tokenise(cleaned)
            if tokens:
                best = tokens[0].title()

        if not best:
            best = self._nc.get("surname_candidate") or ""
            if not best:
                best = "Unknown"

        full = f"{fn} {best}".strip().title() if fn else best.title()
        self._nc["sn_confirmed"] = False
        self._accept(full)

        # Signal flow.py to play the acknowledgement before advancing
        self._s["_nc_accept_preamble"] = _BEST_EFFORT_ACK
        # SMS correction must fire — sn_reask is always a degraded capture path
        self._s["needs_name_correction_sms"] = True

        logger.info(
            "[NameCollector] sn_reask: best-effort surname=%r → full=%r "
            "needs_name_correction_sms=True",
            best, full,
        )
        return ("accept", full)

    # ── Retry helpers (no spelling escalation) ────────────────────────────────

    def _fn_fail(self, re_ask: str) -> Tuple[str, str]:
        """
        Increment first-name retry counter.

        One retry only: on the very first genuine failure (fn_retries == 1)
        escalate immediately to NC_FN_REASK so the next turn runs _fn_reask()
        which accepts best-effort input and sets needs_name_correction_sms=True.

        Scaffold fragments ("my name is", "it's", "my") are handled upstream in
        _fn_normal and never reach here, so every call to _fn_fail is a real
        extraction failure.
        """
        self._nc["fn_retries"] = self._nc.get("fn_retries", 0) + 1
        retries = self._nc["fn_retries"]
        logger.info("[NameCollector] fn_fail: retry #%d", retries)
        if retries >= 1:
            self._nc["substate"] = NC_FN_REASK
            logger.info("[NameCollector] fn_fail: escalating to NC_FN_REASK after %d retries", retries)
            return ("ask", "Sorry, I didn't quite catch that — please say: my first name is...")
        return ("ask", re_ask)

    def _sn_fail(self, re_ask: str) -> Tuple[str, str]:
        """
        Increment surname retry counter.

        One retry only: on the very first genuine failure (sn_retries == 1)
        escalate immediately to NC_SN_REASK so the next turn runs _sn_reask()
        which accepts best-effort input and sets needs_name_correction_sms=True.

        Scaffold fragments ("my surname is", "it's", "my") are handled upstream
        in _sn_normal and never reach here.
        """
        self._nc["sn_retries"] = self._nc.get("sn_retries", 0) + 1
        retries = self._nc["sn_retries"]
        logger.info("[NameCollector] sn_fail: retry #%d", retries)
        if retries >= 1:
            self._nc["substate"] = NC_SN_REASK
            logger.info("[NameCollector] sn_fail: escalating to NC_SN_REASK after %d retries", retries)
            return ("ask", "Sorry, I didn't quite catch that — please say: my surname is...")
        return ("ask", re_ask)

    # ── State-transition helpers ──────────────────────────────────────────────

    def _store_first_name(self, fn: str, confirmed: bool = True) -> None:
        """Store first name, advance to sn_normal, sync legacy session var."""
        self._nc["first_name"]    = fn
        self._nc["fn_confirmed"]  = confirmed
        self._nc["substate"]      = NC_SN_NORMAL
        self._nc["fn_candidate"]  = None
        self._s["name_fragment"]  = fn   # legacy compat
        logger.info(
            "[NameCollector] stored first_name=%r confirmed=%s → sn_normal",
            fn, confirmed,
        )

    def _enter_fn_confirm(self, candidate: str, spelled: bool = False) -> Tuple[str, str]:
        """Enter fn_confirm substate with the given first-name candidate.
        ``spelled`` parameter accepted for API compatibility but ignored —
        readback is always the plain word in the live flow."""
        self._nc["fn_candidate"] = candidate
        self._nc["substate"]     = NC_FN_CONFIRM
        logger.info("[NameCollector] entering fn_confirm for %r", candidate)
        return ("ask", f"I've got {candidate} — is that right?")

    def _enter_sn_confirm(self, candidate: str, spelled: bool = False) -> Tuple[str, str]:
        """Enter sn_confirm substate with the given surname candidate.
        ``spelled`` parameter accepted for API compatibility but ignored."""
        self._nc["surname_candidate"] = candidate
        self._nc["substate"]          = NC_SN_CONFIRM
        self._s["spelling_confirm_surname"] = candidate   # legacy compat
        logger.info("[NameCollector] entering sn_confirm for %r", candidate)
        return ("ask", f"I've got {candidate} — is that right?")

    def _accept(self, full: str) -> None:
        """
        Store accepted full name in session.
        Updates both the _nc substate and all legacy session vars so that
        downstream code (phone readback, CONFIRM_BOOKING, lookup) continues
        to work without modification.

        session["needs_name_correction_sms"] is set BEFORE this method is called
        by the degraded-path handlers (_fn_reask, _sn_reask, and the retry-aware
        branches of _fn_confirm / _sn_confirm).  This method only handles storage.
        """
        self._nc["substate"]          = NC_DONE
        self._nc["surname_candidate"] = None
        # Legacy session vars
        self._s["full_name"] = full
        col = self._s.setdefault("collected", {})
        col["full_name"] = full
        col["name"]      = full
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        logger.info("[NameCollector] accepted full_name=%r", full)
