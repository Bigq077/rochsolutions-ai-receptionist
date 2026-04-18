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
  confirm.
• Surname recovery ladder: normal → reask (one clean retry) → spelling fallback.
  Spelling-confirmed surnames are trusted regardless of prior retry count.
• Deterministic only: no LLM anywhere in this module.
• No dead ends: the flow always advances, never traps the caller.

SUBSTATES  (stored in session["_nc"]["substate"])
-----------
  fn_normal    — collecting first name (initial state)
  fn_confirm   — first-name candidate awaiting yes/no confirmation
  fn_reask     — one normal re-ask after first-name denial; stores best effort
  sn_normal    — collecting surname (entered after first name is confirmed/stored)
  sn_confirm   — surname candidate awaiting confirmation
  sn_reask     — clean surname retry: routes to sn_confirm on success, sn_spelling
                  on failure (replaces old blind best-effort accept)
  sn_spelling  — letter-by-letter spelling fallback; on success → sn_confirm
  done         — full name accepted (transient — consumed immediately by flow.py)

  fn_spelling — kept as constant for code stability; NOT entered in live flow.

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

  SURNAME (recovery ladder)
    sn_normal → sn_confirm ("I've got Roch — is that right?")
      YES              → accept (sn_confirmed=True if sn_retries==0)
      inline fix       → sn_confirm with corrected candidate (sn_denials not incremented)
      NO (1st denial)  → sn_reask  ("Sorry about that — what's your surname?")
      NO (2nd denial)  → sn_spelling
    sn_reask → caller says name → sn_confirm (not blind accept)
             → no usable token  → sn_spelling
    sn_spelling → parsed/spoken → sn_confirm (sn_from_spelling=True → always trusted)
               → can't parse    → graceful best-effort accept

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
NC_SN_REASK    = "sn_reask"      # one clean retry after surname denial → sn_confirm or sn_spelling
NC_SN_SPELLING = "sn_spelling"   # letter-by-letter spelling fallback → sn_confirm
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

# Minimum token length (chars) considered "strong" for direct-accept without
# a confirmation turn.  Applies in fn_normal and fn_reask.
_FN_STRONG_LEN: int = 5

# ── First-name context noise (applied ONLY in _fn_normal, not globally) ───────
# STT mishearings that appear in "my first NAME is …" patterns when the
# label word is garbled.  Not added to the global blacklists so that
# unrelated surname / other contexts are unaffected.
_FN_CONTEXT_NOISE = frozenset({
    "theme",   # mishear of "name"  (e.g. "my first theme is quentin")
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


_NAME_AFTER_IS_RE = re.compile(r'\bis\s+([a-zA-Z\'-]{2,})\s*$', re.IGNORECASE)


def _extract_name_after_is(text: str) -> Optional[str]:
    """
    Extract the token immediately after ' is ' at the end of text.

    Handles STT label-word mishearings where prefix stripping does not
    fire because the label was garbled, e.g.:
        "my first theme is quentin"  →  "quentin"   (theme≈name)
        "my surname was roch"        →  (no match — 'was' not 'is')

    Returns title-cased token if it passes _is_valid_name_token(), else None.
    """
    m = _NAME_AFTER_IS_RE.search(text)
    if not m:
        return None
    token = m.group(1).strip()
    return token.title() if _is_valid_name_token(token) else None


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

    Also catches filler-prefixed scaffolds such as "no my surname is" or
    "yeah it's" — these are denial-or-acknowledgement words prepended to a
    bare scaffold by the caller (or STT) before the name token arrives.
    Without this check they fall into the denial handler and inflate sn_denials
    prematurely.
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
    # One level of leading-filler stripping — catches "no my surname is",
    # "yeah it's", "sorry my name is", etc.  _LEADING_FILLERS_RE includes
    # "no", "yeah", "sorry", "okay", "right", "well", and common hesitations.
    after_filler = _LEADING_FILLERS_RE.sub("", lowered).strip()
    if after_filler and after_filler != lowered:
        if after_filler in {"my", "it's", "its", "i'm", "im"}:
            return True
        for p in _PREFIXES:
            if after_filler == p.strip():
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
            # Retry / denial counters
            "fn_retries":        0,
            "sn_retries":        0,
            "sn_denials":        0,          # times sn_confirm returned a plain denial
            "sn_from_spelling":  False,      # True when candidate came from sn_spelling
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
            "sn_denials":        0,
            "sn_from_spelling":  False,
        }
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        self._s.pop("_nc_accept_preamble", None)
        self._s.pop("needs_name_correction_sms", None)
        self._s.pop("_nc_sn_trusted", None)
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
            "sn_denials":        0,
            "sn_from_spelling":  False,
        }
        self._s.pop("spelling_confirm_surname", None)
        self._s.pop("full_name", None)
        self._s.pop("_nc_accept_preamble", None)
        self._s.pop("needs_name_correction_sms", None)
        self._s.pop("_nc_sn_trusted", None)
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
            return "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?"
        if ss in (NC_FN_SPELLING,):
            return "What's your first name please?"
        if ss == NC_SN_NORMAL:
            return "And what's your surname?"
        if ss == NC_SN_CONFIRM:
            cand = self._nc.get("surname_candidate") or ""
            return f"I've got {cand} — is that right?" if cand else "And what's your surname?"
        if ss == NC_SN_REASK:
            return "Sorry, I didn't quite catch that — please say: my surname is..."
        if ss == NC_SN_SPELLING:
            return "Could you spell your surname for me, one letter at a time?"
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
        if ss == NC_SN_SPELLING:
            return self._sn_spelling(text, raw)

        # Unknown substate (incl. legacy fn_spelling) — defensive reset
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

        # Quality gate for first-name capture:
        #   ≥5 chars → strong → skip fn_confirm (direct store via _store_fn_direct)
        #   2-4 chars → weak/partial → repair prompt via _fn_fail
        # This prevents STT fragments like "Ting" (from "Quentin") being confirmed.

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
                    if len(token) >= _FN_STRONG_LEN:
                        logger.info(
                            "[NameCollector] fn_normal: negation strong token %r → fn_confirm",
                            token,
                        )
                        return self._enter_fn_confirm(token)
                    # Weak token (2-4 chars) — treat as no capture; don't echo
                    logger.info(
                        "[NameCollector] fn_normal: negation weak token %r (len=%d) → repair",
                        token, len(token),
                    )
                    return self._fn_fail(
                        "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?"
                    )
            self._nc["fn_retries"] = 0
            return ("ask", "No problem — what's your first name please?")

        # Meta-language: try to salvage a leading name token
        if _has_meta_language(cleaned):
            token = _extract_leading_token(cleaned, _META_LANGUAGE)
            if token and _is_valid_name_token(token):
                if len(token) >= _FN_STRONG_LEN:
                    logger.info(
                        "[NameCollector] fn_normal: meta-language strong token %r → fn_confirm",
                        token,
                    )
                    return self._enter_fn_confirm(token)
                # Weak token — treat as no capture
                logger.info(
                    "[NameCollector] fn_normal: meta-language weak token %r (len=%d) → repair",
                    token, len(token),
                )
            return self._fn_fail(
                "Sorry, I didn't quite catch that — what's your first name?"
            )

        tokens = _tokenise(cleaned)
        # Remove context-specific noise tokens (STT label-word mishearings).
        tokens = [t for t in tokens if t.lower() not in _FN_CONTEXT_NOISE]

        # "name-after-is" rule (FIX 1/2/3): when prefix stripping left label
        # noise in the cleaned text (e.g. "my first theme is quentin"), the
        # token right after "is" is the actual name.  This overrides the
        # two-token "FirstName Surname" heuristic and drops the noise token.
        if len(tokens) >= 2 and " is " in cleaned.lower():
            _ais = _extract_name_after_is(cleaned)
            if _ais:
                logger.info(
                    "[NameCollector] fn_normal: name-after-is → %r (dropped prefix noise)",
                    _ais,
                )
                if len(_ais) >= _FN_STRONG_LEN:
                    logger.info(
                        "[NameCollector] fn_normal: name-after-is strong %r → fn_confirm",
                        _ais,
                    )
                    return self._enter_fn_confirm(_ais)
                # Weak — treat as no capture
                logger.info(
                    "[NameCollector] fn_normal: name-after-is weak %r (len=%d) → repair",
                    _ais, len(_ais),
                )
                return self._fn_fail(
                    "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?"
                )

        # One token → strong (≥5 chars) → store directly, skip fn_confirm;
        # weak (2-4 chars) → repair.
        if len(tokens) == 1:
            _tok = tokens[0].title()
            if len(_tok) >= _FN_STRONG_LEN:
                logger.info(
                    "[NameCollector] fn_normal: strong single token %r (len=%d) → sn_normal (skip confirm)",
                    _tok, len(_tok),
                )
                return self._store_fn_direct(_tok)
            logger.info(
                "[NameCollector] fn_normal: weak single token %r (len=%d) → repair",
                _tok, len(_tok),
            )
            return self._fn_fail(
                "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?"
            )

        # Two tokens → treat as "FirstName Surname"; strong first → store directly,
        # skip fn_confirm; weak first → repair (don't echo either token)
        if len(tokens) == 2:
            fn_tok = tokens[0].title()
            sn_tok = tokens[1].title()
            if len(fn_tok) >= _FN_STRONG_LEN:
                self._nc["pending_surname"] = sn_tok
                logger.info(
                    "[NameCollector] fn_normal: strong 2-token first=%r → sn_confirm (skip fn_confirm) "
                    "(pending_surname=%r)", fn_tok, sn_tok,
                )
                return self._store_fn_direct(fn_tok)
            logger.info(
                "[NameCollector] fn_normal: weak 2-token first=%r (len=%d) → repair",
                fn_tok, len(fn_tok),
            )
            return self._fn_fail(
                "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?"
            )

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

        # YES — store first name as confirmed.
        # An explicit YES is always a clean confirmation regardless of fn_retries.
        # The degraded/SMS-fallback path only applies in _fn_reask stage 2
        # (where we accept without a confirmation turn).  Here the caller just
        # said "yes", so we clear any prior repair-uncertainty flags.
        if has_yes and not has_no:
            _prior_retries = self._nc.get("fn_retries", 0)
            # Clear any SMS-correction flag set during the failed-capture phase.
            self._s.pop("needs_name_correction_sms", None)
            # Surname is collected via SMS — accept first name only.
            self._nc["pending_surname"] = None
            self._store_first_name(cand, confirmed=True)
            self._accept(cand)
            logger.info(
                "[NameCollector] fn_confirm: YES — fn_confirmed=True fn_retries=%d "
                "(repair flags cleared on explicit confirm) cand=%r",
                _prior_retries, cand,
            )
            return ("accept", cand)

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
            # Plain denial — enter fn_reask for one final retry with repair prompt
            self._nc["substate"] = NC_FN_REASK
            self._nc["fn_candidate"] = cand   # keep candidate as fallback
            # Clear pending_surname — full name was apparently wrong
            self._nc["pending_surname"] = None
            logger.info("[NameCollector] fn_confirm: denial — entering fn_reask")
            return ("ask", "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?")

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
        Two-stage retry after the repair prompt ("could you say 'my name is'…").

        fn_reask_count tracks how many times this handler has been called:

          Stage 1 (fn_reask_count == 0, first response to repair prompt):
            • Strong token (≥4 chars) → fn_confirm ("I've got X — is that right?")
            • Weak / none           → one final retry question (count becomes 1)

          Stage 2 (fn_reask_count >= 1, response to final retry question):
            • Strong token (≥4 chars) → fn_confirm
            • Weak / none            → SMS fallback

        The ≥4-char threshold here is intentionally lower than fn_normal's ≥5,
        because the caller was explicitly asked to say "my name is …", giving
        extra context confidence for shorter names.
        """
        # Track how many times we've been called
        fn_reask_count = self._nc.get("fn_reask_count", 0)
        self._nc["fn_reask_count"] = fn_reask_count + 1

        cleaned = _strip_filler_prefix(text)

        # Extract best candidate — ignore spelling offers and pure meta-language
        best: Optional[str] = None
        if not _is_spelling_offer(text) and not _has_meta_language(cleaned):
            tokens = _tokenise(cleaned)
            if tokens:
                candidate = tokens[0].title()
                if len(candidate) >= 4:  # Lower threshold: repair-prompt scaffold gives confidence
                    best = candidate

        if best:
            if len(best) >= _FN_STRONG_LEN:
                # Caller in repair mode gave a strong, unambiguous name (≥5 chars).
                # Skip fn_confirm entirely — direct store triggers the
                # "Thanks {name} —" prefix on the next question.
                logger.info(
                    "[NameCollector] fn_reask: stage=%d strong token %r "
                    "(len≥%d) → direct store (skip fn_confirm)",
                    fn_reask_count, best, _FN_STRONG_LEN,
                )
                return self._store_fn_direct(best)
            # Shorter name (4 chars, e.g. "Ryan") — still confirm once
            logger.info(
                "[NameCollector] fn_reask: stage=%d shorter token %r (len=%d) → fn_confirm",
                fn_reask_count, best, len(best),
            )
            return self._enter_fn_confirm(best)

        # No strong token
        if fn_reask_count == 0:
            # Stage 1: give the caller one more chance with a plain retry
            logger.info(
                "[NameCollector] fn_reask: stage=0 weak/none → final retry question",
            )
            return ("ask", "Sorry, I didn't quite catch that — could you repeat your first name once more?")

        # Stage 2 (fn_reask_count >= 1): still nothing usable → SMS fallback
        placeholder = self._nc.get("fn_candidate") or "Unknown"
        logger.info(
            "[NameCollector] fn_reask: stage=%d no usable token → sms_fallback (placeholder=%r)",
            fn_reask_count, placeholder,
        )
        self._store_first_name(placeholder, confirmed=False)
        self._s["needs_name_correction_sms"] = True
        self._accept(placeholder)
        return ("sms_fallback", "Okay, that's noted — I'll send you an SMS.")

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
            # Pure meta or spelling offer with no leading name — re-ask WITHOUT
            # inflating sn_retries (may be TTS self-echo; treat as no-op).
            return ("ask", "Sorry, I didn't catch that — could you say your surname again?")

        cleaned = _strip_filler_prefix(text)
        tokens = _tokenise(cleaned)
        tokens = [t for t in tokens if t.lower() not in _DATE_TOKENS]

        # "name-after-is" rule: handles STT label-word mishearings for surnames
        # e.g. "my surname word is roch" → extract "roch", drop noise.
        if len(tokens) >= 2 and " is " in cleaned.lower():
            _ais = _extract_name_after_is(cleaned)
            if _ais:
                logger.info(
                    "[NameCollector] sn_normal: name-after-is → %r (dropped prefix noise)",
                    _ais,
                )
                return self._enter_sn_confirm(_ais)

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

        YES              → accept (sn_confirmed=True if clean path).
        inline fix       → re-confirm with corrected candidate.
        NO (1st denial)  → sn_reask (one clean retry).
        NO (2nd denial)  → sn_spelling (escalation).
        Meta / ambiguous → re-ask yes/no (stay in sn_confirm).
        """
        cand = self._nc.get("surname_candidate") or ""
        fn   = self.first_name or ""

        # Scaffold in confirm state — caller is restarting their answer.
        # Hold silently; the completing token will arrive in the next turn.
        if _is_incomplete_scaffold(text):
            logger.info("[NameCollector] sn_confirm: scaffold %r — scaffold_continue", text)
            return ("scaffold_continue", self._question())

        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        # Strong-denial: discard weak YES signal
        if has_yes and has_no and any(p in text for p in _STRONG_NO):
            has_yes = False

        # YES — accept (or correction-while-confirming)
        # Spelling-path candidates are always trusted (caller spelled & confirmed).
        # Acoustic candidates are degraded when sn_retries > 0 (extraction failures).
        if has_yes and not has_no:
            # Edge case: "yes it's Roch" when candidate is "Rock" — the caller is
            # confirming intent but also STATING the correct name via an explicit
            # bridge phrase.  Without this check the YES path short-circuits and
            # the embedded correction is silently ignored, locking in the wrong name.
            #
            # Bridge phrases we recognise: "it's X", "it is X", "my name is X",
            # "my surname is X".  "yes it's right" does NOT match because "right"
            # is a function word and fails _is_valid_name_token.
            _SN_BRIDGE_RE = re.compile(
                r"\b(?:it'?s|it is|my (?:name|surname) is)\s+([a-zA-Z'\-]{2,})\b",
                re.IGNORECASE,
            )
            _bm = _SN_BRIDGE_RE.search(text)
            if _bm:
                _embedded = _bm.group(1).strip().title()
                if (
                    _is_valid_name_token(_embedded)
                    and cand
                    and _embedded.lower() != cand.lower()
                ):
                    logger.info(
                        "[NameCollector] sn_confirm: YES with embedded correction "
                        "%r → re-confirm (prev cand=%r)",
                        _embedded, cand,
                    )
                    return self._enter_sn_confirm(_embedded)
            # Plain YES (or embedded name matches current candidate) — accept.
            full = f"{fn} {cand}".strip().title() if fn else cand.title()
            _sn_degraded = (
                self._nc.get("sn_retries", 0) > 0
                and not self._nc.get("sn_from_spelling")
            )
            self._nc["sn_confirmed"] = not _sn_degraded
            if _sn_degraded:
                self._s["_nc_accept_preamble"] = _BEST_EFFORT_ACK
                self._s["needs_name_correction_sms"] = True
                logger.info(
                    "[NameCollector] sn_confirm: YES after %d sn_retries (degraded) — "
                    "sn_confirmed=False",
                    self._nc["sn_retries"],
                )
            self._accept(full)
            return ("accept", full)

        # NO / spelling offer — recovery ladder:
        #   first denial  → sn_reask (one clean retry)
        #   second denial → sn_spelling
        is_denial = (has_no and not has_yes) or _is_spelling_offer(text)
        if is_denial:
            # Inline correction: "no, it's Roch" / "no Roch" / "no my surname is Roch"
            # Does NOT increment sn_denials — it's a correction, not a plain denial.
            _tail = _DENIAL_PREFIX_RE.sub("", text).strip()
            if _tail:
                _corr_tokens = _tokenise(_strip_filler_prefix(_tail))
                if _corr_tokens:
                    logger.info(
                        "[NameCollector] sn_confirm: inline correction %r → confirming",
                        _corr_tokens[0],
                    )
                    return self._enter_sn_confirm(_corr_tokens[0].title())
            # Plain denial — count it
            self._nc["sn_denials"] = self._nc.get("sn_denials", 0) + 1
            if self._nc["sn_denials"] >= 2:
                logger.info(
                    "[NameCollector] sn_confirm: %d denials — escalating to sn_spelling",
                    self._nc["sn_denials"],
                )
                # Candidate was explicitly rejected twice — clear it now so the
                # sn_spelling best-effort path cannot fall back to a denied name.
                self._nc["surname_candidate"] = None
                self._s.pop("spelling_confirm_surname", None)
                return self._enter_sn_spelling()
            # First denial — one clean re-ask.
            # Clear the rejected candidate: it was explicitly denied and must not
            # resurface as a best-effort fallback in sn_spelling or sn_reask.
            self._nc["substate"] = NC_SN_REASK
            self._nc["surname_candidate"] = None
            self._s.pop("spelling_confirm_surname", None)
            logger.info("[NameCollector] sn_confirm: denial 1 — entering sn_reask (candidate cleared)")
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
        Clean surname retry after the first confirmation denial.
        Question played: "Sorry about that — what's your surname?"

        If the caller supplies a usable token: route to sn_confirm (let them
        verify it — no blind accept).
        If nothing usable arrives: escalate to sn_spelling.
        """
        # Scaffold — hold for the name to arrive
        if _is_incomplete_scaffold(text):
            logger.info("[NameCollector] sn_reask: scaffold %r — scaffold_continue", text)
            return ("scaffold_continue", "Sorry about that — what's your surname?")

        cleaned = _strip_filler_prefix(text)

        best: Optional[str] = None
        if not _is_spelling_offer(text) and not _has_meta_language(cleaned):
            tokens = _tokenise(cleaned)
            if tokens:
                best = tokens[0].title()

        if best:
            logger.info("[NameCollector] sn_reask: got token %r → sn_confirm", best)
            return self._enter_sn_confirm(best)

        # Nothing usable — escalate to spelling
        logger.info("[NameCollector] sn_reask: no usable token — escalating to sn_spelling")
        return self._enter_sn_spelling()

    # ── Spelling fallback ────────────────────────────────────────────────────

    def _enter_sn_spelling(self) -> Tuple[str, str]:
        """Enter sn_spelling substate and ask the caller to spell their surname."""
        self._nc["substate"]        = NC_SN_SPELLING
        self._nc["sn_from_spelling"] = True
        logger.info("[NameCollector] entering sn_spelling")
        return ("ask", "Could you spell your surname for me, one letter at a time?")

    def _sn_spelling(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Surname spelling fallback.

        Parses letter-by-letter input (individual letters or NATO phonetics)
        into a surname candidate, then routes to sn_confirm so the caller can
        verify it.  If parsing fails, falls back to normal token extraction, then
        to graceful best-effort accept.  Flagged sn_from_spelling=True means
        sn_confirm will mark the result as trusted regardless of sn_retries.
        """
        # Scaffold — hold for the spelling to arrive
        if _is_incomplete_scaffold(text):
            logger.info("[NameCollector] sn_spelling: scaffold %r — scaffold_continue", text)
            return ("scaffold_continue", "Could you spell your surname for me, one letter at a time?")

        cleaned = text.strip()

        # Primary: parse as spelled letters / NATO phonetics
        result = (
            _parse_spelled_letters_tolerant(cleaned)
            or _parse_spelled_letters(cleaned)
        )
        if result and _is_valid_name_token(result):
            logger.info("[NameCollector] sn_spelling: parsed spelling → %r", result)
            return self._enter_sn_confirm(result)

        # Fallback: caller may have just said the name again rather than spelling it
        tokens = _tokenise(_strip_filler_prefix(cleaned))
        if tokens:
            logger.info("[NameCollector] sn_spelling: name token %r → sn_confirm", tokens[0])
            return self._enter_sn_confirm(tokens[0].title())

        # Nothing usable — graceful best-effort accept with last known candidate
        fn   = self.first_name or ""
        best = self._nc.get("surname_candidate") or "Unknown"
        full = f"{fn} {best}".strip().title() if fn else best.title()
        self._nc["sn_confirmed"] = False
        self._s["needs_name_correction_sms"] = True
        self._accept(full)
        logger.info(
            "[NameCollector] sn_spelling: could not parse — best-effort accept %r",
            full,
        )
        return ("accept", full)

    # ── Retry helpers ─────────────────────────────────────────────────────────

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
            return ("ask", "Sorry, I didn't quite catch that — could you say 'my name is' and then your first name?")
        return ("ask", re_ask)

    def _sn_fail(self, re_ask: str) -> Tuple[str, str]:
        """
        Increment surname retry counter.

        Two genuine failures required before escalating (sn_retries >= 2) so
        that a single TTS self-echo or STT glitch does not immediately corrupt
        the substate.  On escalation, transitions to NC_SN_REASK so the next
        turn runs _sn_reask(), which routes to sn_confirm if a token is found,
        or sn_spelling if not.

        Pure meta-language / no-token paths in _sn_normal return a plain re-ask
        directly and MUST NOT call _sn_fail — they do not represent a real
        extraction failure and must not inflate sn_retries.

        Scaffold fragments ("my surname is", "it's", "my") are handled upstream
        in _sn_normal and never reach here.
        """
        self._nc["sn_retries"] = self._nc.get("sn_retries", 0) + 1
        retries = self._nc["sn_retries"]
        logger.info("[NameCollector] sn_fail: retry #%d", retries)
        if retries >= 2:
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

    def _store_fn_direct(self, fn: str) -> Tuple[str, str]:
        """
        Store first name directly without asking for confirmation (strong-token path).

        Sets ``_fn_direct_stored=True`` so that ``_accept()`` can signal flow.py to
        prepend "Thanks {name} —" to the next question.  If a pending_surname was
        pre-queued (2-token input like "quentin roch"), consume it immediately and
        enter sn_confirm; otherwise advance to sn_normal and ask for the surname.
        """
        self._nc["_fn_direct_stored"] = True
        self._store_first_name(fn, confirmed=True)
        pending = self._nc.get("pending_surname")
        if pending:
            self._nc["pending_surname"] = None
            logger.info(
                "[NameCollector] _store_fn_direct: %r → sn_confirm (pending_surname=%r)",
                fn, pending,
            )
            return self._enter_sn_confirm(pending)
        logger.info("[NameCollector] _store_fn_direct: %r → sn_normal", fn)
        return ("ask", "And what's your surname?")

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
        # Surname trust signal: True only when caller said YES after a clean
        # first-attempt capture (sn_confirmed=True ↔ sn_retries==0 at accept time).
        # False on every degraded path (reask, retries, best-effort).
        # Checked by flow.py before LOOKUP_RESCHEDULE / LOOKUP_CANCEL to block
        # unreliable surnames from poisoning the appointment lookup.
        self._s["_nc_sn_trusted"] = bool(self._nc.get("sn_confirmed", False))
        # If first name was stored directly (no fn_confirm asked), set a prefix key
        # so flow.py can prepend "Thanks {first_name} —" to the next question.
        if self._nc.get("_fn_direct_stored"):
            _fn_val = self._nc.get("first_name", "")
            if _fn_val:
                self._s["_nc_fn_name_prefix"] = _fn_val
        # Legacy session vars
        self._s["full_name"] = full
        col = self._s.setdefault("collected", {})
        col["full_name"] = full
        col["name"]      = full
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        logger.info(
            "[NameCollector] accepted full_name=%r sn_trusted=%s",
            full, self._s["_nc_sn_trusted"],
        )
