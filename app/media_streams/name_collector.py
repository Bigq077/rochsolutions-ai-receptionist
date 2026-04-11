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
• Explicit substates: every possible name-collection phase is an explicit named
  substate, never an implicit flag scattered across condition checks.
• Deterministic only: no LLM anywhere in this module.
• Minimal friction for clean names, robust fallback for hard names.

SUBSTATES  (stored in session["_nc"]["substate"])
-----------
  fn_normal    — collecting first name (initial state)
  fn_spelling  — first-name spelling mode: expect letter-by-letter input
  sn_normal    — collecting surname (entered after first name is accepted)
  sn_spelling  — surname spelling mode: expect letter-by-letter input
  sn_confirm   — surname candidate awaiting confirmation (short / salvaged / spelled)
  done         — full name accepted (transient — consumed immediately by flow.py)

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


# ── Module-level helpers ──────────────────────────────────────────────────────

def _strip_prefixes(text: str) -> str:
    """Remove the first matching name-label prefix from normalised text."""
    for p in _PREFIXES:
        if text.startswith(p):
            return text[len(p):].strip()
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

    Returns capitalised result or None if:
      - Fewer than 2 letters
      - Any word is not a single letter or NATO phonetic
    """
    normalised = re.sub(r"[-,.\s]+", " ", text.lower()).strip()
    words = normalised.split()
    if not words:
        return None
    letters: list = []
    for w in words:
        if len(w) == 1 and w.isalpha():
            letters.append(w.upper())
        elif w in _NATO:
            letters.append(_NATO[w].upper())
        else:
            return None  # non-letter token → not a spelling sequence
    if len(letters) < 2:
        return None
    return "".join(letters).title()


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
            "first_name":        None,
            "surname_candidate": None,
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
            "first_name":        None,
            "surname_candidate": None,
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
            "first_name":        nc.get("first_name"),  # preserved
            "surname_candidate": None,
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
            spaced = " ".join(list(cand.upper())) if cand else "?"
            return f"I've got {spaced} — is that right?"
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

        Fast path: valid single or double token → accept immediately.
        Meta-language path: salvage leading token → store + ask surname.
        Escalation: ≥2 failures → auto-enter fn_spelling.
        """
        # Explicit spelling offer
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_FN_SPELLING
            return ("ask", self._question())

        cleaned = _strip_prefixes(text)

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
                    self._store_first_name(token)
                    return ("ask", "And what's your surname?")
            # No valid token → fresh re-ask (reset retries — negation is not a failure)
            self._nc["fn_retries"] = 0
            return ("ask", "No problem — what's your first name please?")

        # Meta-language: "Matt, do you need me to spell that?"
        # Try to salvage the token that appeared BEFORE the meta phrase.
        if _has_meta_language(cleaned):
            token = _extract_leading_token(cleaned, _META_LANGUAGE)
            if token and _is_valid_name_token(token):
                self._store_first_name(token)
                return ("ask", "And what's your surname?")
            return self._fn_fail(
                "Sorry, I didn't quite catch that — what's your first name?"
            )

        tokens = _tokenise(cleaned)

        # Two valid tokens → accept as "FirstName Surname"
        if len(tokens) == 2:
            full = f"{tokens[0].title()} {tokens[1].title()}"
            self._accept(full)
            return ("accept", full)

        # One valid token → first name only; ask for surname
        if len(tokens) == 1:
            self._store_first_name(tokens[0].title())
            return ("ask", "And what's your surname?")

        # Nothing usable
        return self._fn_fail(
            "Sorry, I didn't quite catch that — could you say your first name again?"
        )

    # ── Substate: fn_spelling ────────────────────────────────────────────────

    def _fn_spelling(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect first name via letter-by-letter spelling.

        Accepts the full name spelled in one utterance (most natural for phones).
        If caller just says a clean word directly (e.g. "Matt"), accept it —
        they changed their mind about spelling.
        """
        # Caller may just say the name normally after entering spelling mode
        cleaned = _strip_prefixes(text)
        tokens = _tokenise(cleaned)
        spelled = _parse_spelled_letters(text)

        if (
            spelled is None
            and len(tokens) == 1
            and len(tokens[0]) >= 2
        ):
            # Caller said a normal word — accept as first name
            fn = tokens[0].title()
            self._store_first_name(fn)
            logger.info("[NameCollector] fn_spelling: caller said normal word %r", fn)
            return ("ask", f"Got it — and your surname?")

        if spelled:
            fn = spelled.title()
            self._store_first_name(fn)
            logger.info("[NameCollector] fn_spelling: parsed %r → %r", text[:30], fn)
            return ("ask", f"Got it — and your surname?")

        # Single letter? Caller is spelling one letter at a time — acknowledge and wait
        words = text.strip().split()
        if len(words) == 1 and len(words[0]) == 1 and words[0].isalpha():
            return ("ask", (
                f"OK, I've got {words[0].upper()} — keep going, "
                "or say all the letters together."
            ))

        return ("ask", (
            "I'm just listening for individual letters — "
            "could you say your first name one letter at a time?"
        ))

    # ── Substate: sn_normal ──────────────────────────────────────────────────

    def _sn_normal(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect surname in normal mode.

        Fast path: 1 valid token ≥4 chars → accept.
        Confirm path: 1 valid token ≤3 chars → enter sn_confirm (possibly clipped).
        Salvage path: meta-language with leading token → enter sn_confirm.
        Escalation: ≥2 failures → enter sn_spelling.
        """
        # Explicit spelling offer
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_SN_SPELLING
            return ("ask", self._question())

        cleaned = _strip_prefixes(text)

        # Meta-language: "Slater do you need help spelling that"
        if _has_meta_language(cleaned):
            token = _extract_leading_token(cleaned, _META_LANGUAGE)
            if token and _is_valid_name_token(token):
                # Even for longer tokens, the meta-language signals uncertainty —
                # enter sn_confirm so caller can verify the spelling.
                return self._enter_sn_confirm(token)
            return self._sn_fail(
                "Sorry, I didn't catch that — could you say your surname again?"
            )

        tokens = _tokenise(cleaned)
        # Strip any date/scheduling tokens that may have leaked in
        tokens = [t for t in tokens if t.lower() not in _DATE_TOKENS]

        if len(tokens) == 1:
            sn = tokens[0].title()
            # Short surname (≤3 chars) — may be a clipped STT fragment
            if len(sn) <= 3 and sn.isalpha():
                return self._enter_sn_confirm(sn)
            # Clean surname — accept directly
            fn = self.first_name or ""
            full = f"{fn} {sn}".strip().title() if fn else sn.title()
            self._accept(full)
            return ("accept", full)

        if len(tokens) == 2:
            # Double-barrelled or hyphenated surname (e.g. "Smith-Jones")
            sn_combined = f"{tokens[0].title()}-{tokens[1].title()}"
            fn = self.first_name or ""
            full = f"{fn} {sn_combined}".strip() if fn else sn_combined
            self._accept(full)
            return ("accept", full)

        if len(tokens) > 2:
            return self._sn_fail(
                "Sorry — could you just give me your surname on its own?"
            )

        # No valid tokens at all
        return self._sn_fail(
            "Sorry — could you say your surname again?"
        )

    # ── Substate: sn_spelling ────────────────────────────────────────────────

    def _sn_spelling(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Collect surname via letter-by-letter spelling.

        On success, always enters sn_confirm so the caller can verify
        what was captured before it is committed.
        """
        cleaned = _strip_prefixes(text)
        tokens = _tokenise(cleaned)
        spelled = _parse_spelled_letters(text)

        # Caller may just say the surname directly
        if (
            spelled is None
            and len(tokens) == 1
            and len(tokens[0]) >= 2
        ):
            sn = tokens[0].title()
            logger.info("[NameCollector] sn_spelling: caller said normal word %r", sn)
            return self._enter_sn_confirm(sn)

        if spelled:
            logger.info("[NameCollector] sn_spelling: parsed %r → %r", text[:30], spelled)
            return self._enter_sn_confirm(spelled.title())

        # Single letter: acknowledge and wait
        words = text.strip().split()
        if len(words) == 1 and len(words[0]) == 1 and words[0].isalpha():
            return ("ask", (
                f"Got {words[0].upper()} — keep going, "
                "or say all the letters in one go."
            ))

        return ("ask", (
            "I'm just getting the individual letters — "
            "could you spell your surname out for me?"
        ))

    # ── Substate: sn_confirm ─────────────────────────────────────────────────

    def _sn_confirm(self, text: str, raw: str) -> Tuple[str, str]:
        """
        Confirm a surname candidate.

        Question played: "I've got S-L-A-T-E-R — is that right?"

        Branches:
          YES             → accept
          NO              → enter sn_spelling (restart surname only)
          Spelled letters → update candidate, re-confirm
          Clean word      → accept as new surname (or re-confirm if ≤3 chars)
          Ambiguous       → re-ask
        """
        cand = self._nc.get("surname_candidate") or ""
        fn = self.first_name or ""

        # Spelling offer during confirmation
        if _is_spelling_offer(text):
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["surname_candidate"] = None
            self._s.pop("spelling_confirm_surname", None)
            return ("ask", self._question())

        # Spelled correction: caller provides alternative spelling
        spelled = _parse_spelled_letters(text)
        if spelled:
            return self._enter_sn_confirm(spelled.title())

        # YES — accept candidate
        has_yes = any(p in text for p in _CONFIRM_YES)
        has_no  = any(p in text for p in _CONFIRM_NO)

        if has_yes and not has_no:
            full = f"{fn} {cand}".strip().title() if fn else cand.title()
            self._accept(full)
            return ("accept", full)

        # NO — restart surname spelling
        if has_no and not has_yes:
            self._nc["substate"] = NC_SN_SPELLING
            self._nc["surname_candidate"] = None
            self._s.pop("spelling_confirm_surname", None)
            return ("ask", "No problem — please spell out your surname one letter at a time.")

        # Caller gave a clean single word (the correct surname directly)
        cleaned = _strip_prefixes(text)
        tokens = _tokenise(cleaned)
        if len(tokens) == 1:
            sn = tokens[0].title()
            if len(sn) <= 3 and sn.isalpha():
                return self._enter_sn_confirm(sn)
            full = f"{fn} {sn}".strip().title() if fn else sn.title()
            self._accept(full)
            return ("accept", full)

        # Ambiguous — re-ask
        if cand:
            spaced = " ".join(list(cand.upper()))
            return ("ask", f"Sorry — did you say {spaced}? Just say yes or no.")
        self._nc["substate"] = NC_SN_SPELLING
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
        """Store first name, advance to sn_normal, sync legacy session var."""
        self._nc["first_name"] = fn
        self._nc["substate"]   = NC_SN_NORMAL
        self._s["name_fragment"] = fn  # legacy compat
        logger.info("[NameCollector] stored first_name=%r → sn_normal", fn)

    def _enter_sn_confirm(self, candidate: str) -> Tuple[str, str]:
        """Enter sn_confirm substate with the given surname candidate."""
        self._nc["surname_candidate"] = candidate
        self._nc["substate"] = NC_SN_CONFIRM
        self._s["spelling_confirm_surname"] = candidate  # legacy compat
        spaced = " ".join(list(candidate.upper()))
        logger.info("[NameCollector] entering sn_confirm for %r", candidate)
        return ("ask", f"I've got {spaced} — is that right?")

    def _accept(self, full: str) -> None:
        """
        Store accepted full name in session.

        Updates both the _nc substate and all legacy session vars so that
        downstream code (phone readback, CONFIRM_BOOKING, lookup) continues
        to work without modification.
        """
        self._nc["substate"]          = NC_DONE
        self._nc["surname_candidate"] = None
        # Legacy session vars kept in sync
        self._s["full_name"] = full
        col = self._s.setdefault("collected", {})
        col["full_name"] = full
        col["name"]      = full
        self._s.pop("name_fragment", None)
        self._s.pop("spelling_confirm_surname", None)
        logger.info("[NameCollector] accepted full_name=%r", full)
