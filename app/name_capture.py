# app/name_capture.py
"""Canonical caller-name capture — the single source of truth for every clinic.

This module is intended to be BYTE-IDENTICAL on every clinic branch
(origin/main, jv-v1-onboarding, vitaledge-onboarding).  Verify with:

    git rev-parse origin/main:app/name_capture.py \
                  jv-v1-onboarding:app/name_capture.py \
                  vitaledge-onboarding:app/name_capture.py

All three hashes must match.  If they diverge, a fix has landed on one clinic
and not the others — which is exactly the failure this module exists to stop.

Design rules, deliberately kept narrow so the file stays portable:
  • Pure functions.  Input is a string, output is a string.
  • No session state, no logging, no imports beyond `re`.
  • Session coupling and the phase gate stay in connection.py.

Capture contract (owner policy, 2026-07-07):
  • Susie reads back the FIRST NAME only.  The surname is NEVER read back,
    confirmed, spelled, or re-asked for accuracy.
  • Because the surname is never confirmed, a WRONG surname is never caught by
    the caller — it flows silently into the booking, the SMS and the Sheets
    row.  So a wrong capture is strictly worse than no capture, and every
    pattern below prefers to return "" over guessing.
"""

import re

# ---------------------------------------------------------------------------
# Vocabularies (verbatim from theorem_v3 / connection.py)
# ---------------------------------------------------------------------------

NAME_FALSE_POSITIVES = frozenset({
    "sorry", "right", "great", "perfect", "ok", "okay", "sure",
    "yes", "no", "of", "course", "me", "you", "we", "it", "is",
    "thanks", "thank", "hi", "hello", "hey", "now", "just", "that",
    "this", "then", "so", "and", "but", "the", "a", "an",
    # Spec T amendment additions
    "brilliant", "lovely", "noted", "awlstuh", "redditch",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    # CODE SPEC AB: time-of-day words — prevent slot-presentation phrases like
    # "Afternoons — I've got..." from being stored as a patient name.
    "morning", "mornings", "afternoon", "afternoons", "evening", "evenings",
    # Scheduling vocabulary that appears at the start of availability responses
    "number", "slot", "appointment", "available", "availability",
    # Month names — prevent date strings like "May —" being captured as a name
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})

NAME_LEAD_FILLERS = frozenset({
    "is", "it", "yeah", "yes", "um", "uh", "erm", "so", "well",
    "my", "hi", "hello", "and", "ok", "okay",
})

SURNAME_STOPWORDS = frozenset({
    "is", "are", "was", "be", "my", "your", "the", "a", "an", "and", "or",
    "but", "name", "names", "first", "last", "given", "middle", "second",
    "surname", "family", "please", "thanks", "thank", "yes", "yeah", "yep",
    "no", "nope", "use", "this", "that", "number", "one", "calling", "from",
    "on", "with", "for", "it", "its", "im", "i", "me", "we", "you", "he",
    "she", "they", "clinic", "appointment", "booking", "book", "sorry",
    "mr", "mrs", "ms", "miss", "mister", "doctor", "dr", "there", "hi",
    "hello", "hey", "ok", "okay", "just", "spelt", "spelled", "spell",
    "like", "would", "to", "of", "as", "so", "well", "um", "uh", "er",
})

_SURNAME_TOKEN = r"[a-z][a-z'\-]{1,24}"

# Connective words that can sit between the surname marker and the surname
# itself ("my surname WILL BE green") but are NOT in SURNAME_STOPWORDS, so
# _ok() would otherwise accept them as the surname.  Only skipped when a real
# candidate follows — so a caller genuinely surnamed "Will" ("my surname is
# Will") is still captured.
SURNAME_MARKER_FILLER = frozenset({
    "will", "shall", "gonna", "going", "been", "being", "do", "does", "did",
    "actually", "probably", "then", "now", "also", "definitely",
})

# A lone contraction is never a surname.  Without this guard a bare "it's"
# arriving while we await the surname registers the caller as "Quentin It's".
SURNAME_CONTRACTIONS = frozenset({
    "it's", "that's", "what's", "he's", "she's", "here's", "there's", "let's",
})

# Nobiliary / patronymic particles that bind FORWARD onto the token after them,
# so "de silva" is one surname, not the surname "De" (or "Silva").  A particle
# is never a surname on its own.
SURNAME_PARTICLES = frozenset({
    "de", "del", "della", "di", "da", "dos", "du",
    "van", "von", "der", "den", "ten", "ter",
    "la", "le", "les", "bin", "ibn", "al", "st",
})

# A surname may span at most this many tokens ("van der berg").  Bounds the
# particle walk so a run-on utterance can never be swallowed whole.
_MAX_SURNAME_TOKENS = 3


# ---------------------------------------------------------------------------
# Casing
# ---------------------------------------------------------------------------

def _cap(piece: str) -> str:
    """Upper-case the first character, leave the rest as given."""
    return piece[:1].upper() + piece[1:] if piece else piece


def titlecase_surname(raw: str) -> str:
    """Title-case a surname the way a clinic form would write it.

    str.capitalize() lower-cases everything after the first character, which
    mangles exactly the names people notice:  o'brien -> "O'brien",
    smith-jones -> "Smith-jones".  Those strings reach Acuity, the confirmation
    SMS and the Sheets row.

    Deliberately does NOT infer Mc/Mac casing ("mackenzie" -> "MacKenzie" is
    wrong as often as it is right; "mace", "machin" would be corrupted).
    """
    words = []
    for word in raw.split():
        parts = []
        for seg in word.split("-"):                    # smith-jones
            if "'" in seg:                             # o'brien, d'angelo
                head, _, tail = seg.partition("'")
                # Only a short particle head takes a capital after the
                # apostrophe: "o'brien" -> O'Brien, but "shaun'a" is left alone.
                seg = _cap(head) + "'" + (_cap(tail) if len(head) <= 2 and tail else tail)
            else:
                seg = _cap(seg)
            parts.append(seg)
        words.append("-".join(parts))
    return " ".join(words)


# ---------------------------------------------------------------------------
# Surname extraction
# ---------------------------------------------------------------------------

# Verb-contraction suffixes.  "i'm", "that's", "we've" must never be surnames,
# but "o'brien" / "d'angelo" must survive — so reject on the SUFFIX, not on the
# mere presence of an apostrophe.
_CONTRACTION_SUFFIXES = ("'m", "'s", "'re", "'ve", "'ll", "'d", "'t")


def _is_contraction(tok: str) -> bool:
    return tok in SURNAME_CONTRACTIONS or tok.endswith(_CONTRACTION_SUFFIXES)


def _make_ok(first_name: str):
    """Build the token-acceptance predicate for this caller."""
    first_l = (first_name or "").lower()

    def _ok(tok: str) -> bool:
        return (
            bool(tok)
            and 2 <= len(tok) <= 25
            and tok != first_l
            and tok not in SURNAME_STOPWORDS
            and tok not in NAME_FALSE_POSITIVES
            and not _is_contraction(tok)
        )

    return _ok


def _walk_particles_forward(tokens, start, ok) -> str:
    """Join a particle chain onto the surname that follows it.

    ["de", "silva"]        -> "de silva"
    ["van", "der", "berg"] -> "van der berg"
    ["rock", "please"]     -> "rock"          (no particle, stop immediately)
    ["de"]                 -> ""              (a bare particle is not a surname)
    """
    out = [tokens[start]]
    j = start
    while (
        out[-1] in SURNAME_PARTICLES
        and len(out) < _MAX_SURNAME_TOKENS
        and j + 1 < len(tokens)
        and ok(tokens[j + 1])
    ):
        j += 1
        out.append(tokens[j])
    while out and out[-1] in SURNAME_PARTICLES:        # never end on a particle
        out.pop()
    return " ".join(out)


def _walk_particles_back(tail, ok) -> str:
    """Given the tokens AFTER the first name, take the trailing surname group.

    ["james", "rock"]         -> "rock"            (middle name dropped)
    ["de", "silva"]           -> "de silva"
    ["van", "der", "berg"]    -> "van der berg"
    ["smith", "please"]       -> "smith"           (trailing filler stripped)
    ["jenkins", "thanks"]     -> "jenkins"
    ["please"]                -> ""
    """
    # Strip trailing filler/stopwords and dangling particles ("smith please",
    # "jenkins thanks", "de") so the surname is read from the last REAL name
    # token, rather than rejected because a politeness word trails it.  Only
    # tokens ok() already refuses (stopwords, contractions, false-positives) or
    # bare particles are dropped, so a genuine trailing surname is never lost.
    end = len(tail)
    while end > 0 and (not ok(tail[end - 1]) or tail[end - 1] in SURNAME_PARTICLES):
        end -= 1
    tail = tail[:end]
    if not tail:
        return ""
    j = len(tail) - 1
    while (
        j > 0
        and tail[j - 1] in SURNAME_PARTICLES
        and (len(tail) - j) < _MAX_SURNAME_TOKENS
    ):
        j -= 1
    return " ".join(tail[j:])


def extract_surname(caller_utterance: str, first_name: str) -> str:
    """Best-effort surname extraction from the caller's name-answer utterance.

    The first name is taken authoritatively from Susie's readback ("Thanks
    Quentin —"); this only recovers the SURNAME so the full name can be
    registered WITHOUT ever reading the surname back.  Returns a title-cased
    surname, or "" if none can be confidently identified.

    Only invoked inside the name-collection phase (gated by the caller), so the
    utterance is a name answer — which keeps false positives low.
    """
    if not caller_utterance:
        return ""
    text = caller_utterance.lower()
    text = re.sub(r"[^a-z'\-\s]", " ", text)   # punctuation/digits → space
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    first_l = (first_name or "").lower()
    ok = _make_ok(first_name)

    # 1) Explicit surname marker — most reliable.
    #    The connective between the marker and the surname is free-form:
    #    "surname is rock", "surname's rock", "surname would be rock",
    #    "surname will be rock", "surname, uh, rock".  Requiring a fixed
    #    (?:is|was|'s) connective silently dropped the surname for every other
    #    phrasing.  Instead scan forward and take the first plausible token:
    #    every connective ("is", "was", "would", "be", "uh", …) is already in
    #    SURNAME_STOPWORDS, so ok() skips them.  Bounded to the next 5 tokens so
    #    a distant unrelated word can never be grabbed.
    m = re.search(r"(?:surname|last name|family name|second name)\b(.*)$", text)
    if m:
        toks = [t.strip("'-") for t in m.group(1).split()[:5]]
        # Prefer the first non-connective candidate ("surname will be green" →
        # green, not will).  Fall back to a connective only when it is the sole
        # candidate ("my surname is Will" → Will).
        idx = next(
            (i for i, t in enumerate(toks)
             if ok(t) and t not in SURNAME_MARKER_FILLER),
            None,
        )
        if idx is None:
            idx = next((i for i, t in enumerate(toks) if ok(t)), None)
        if idx is not None:
            cand = _walk_particles_forward(toks, idx, ok)
            if cand:
                return titlecase_surname(cand)

    # 2) "my name is X Y[ Z]", "it's X Y", "i'm X Y", "this is X Y".
    #    The token after the intro MUST be the first name we already hold.
    #    Without that anchor the pattern happily reads "i'm calling about my
    #    knee" as surname "Knee" — it never checked that the name it matched
    #    belonged to the caller.  Reachable whenever a caller answers with the
    #    name AND their reason in one breath, which the name-first prompt
    #    explicitly invites ("Quentin Roch, I'm calling about my knee").
    if first_l:
        m = re.search(
            # Intro phrases that precede the caller's name.  "would be" /
            # "that's" / "that is" cover the very common "that would be Quentin
            # Rock" / "it would be Quentin Rock" answers, which otherwise fell
            # through both this pattern and the bare-name pattern (the name sits
            # too deep for the leading-filler rule) → surname silently dropped
            # (observed 2026-07-11).  Safe: the token after the intro must be
            # the first name we already hold, so a wrong word can't be grabbed.
            r"(?:my name is|name'?s|name is|it'?s|it is|i'?m|i am|this is"
            r"|that is|that'?s|would be)\s+"
            + re.escape(first_l) + r"\s+(" + _SURNAME_TOKEN
            + r"(?:\s+" + _SURNAME_TOKEN + r")*)",
            text,
        )
        if m:
            tail = m.group(1).split()
            # Short tail → the surname is the trailing group (drops a middle
            # name).  Long tail → the caller ran on into their reason, so take
            # only the token straight after the first name.
            cand = (
                _walk_particles_back(tail, ok)
                if len(tail) <= _MAX_SURNAME_TOKENS
                else (_walk_particles_forward(tail, 0, ok)
                      if ok(tail[0]) or tail[0] in SURNAME_PARTICLES else "")
            )
            if cand:
                return titlecase_surname(cand)

    # 3) Bare name "quentin rock" / "quentin de silva" — the first name leads
    #    the answer, OR sits just after a single leading filler / STT artefact
    #    ("yeah sarah jenkins").  A non-filler lead ("no sarah wrong") is NOT
    #    trusted, so a correction/aside can never be mis-read as the name.
    tokens = text.split()
    if first_l in tokens:
        idx = tokens.index(first_l)
        leads = idx == 0 or (idx == 1 and tokens[0] in NAME_LEAD_FILLERS)
        if leads and idx < len(tokens) - 1:
            tail = tokens[idx + 1:]
            if len(tokens) <= 5:
                # Short answer — the surname is the trailing group, so a middle
                # name is dropped ("quentin james rock" → Rock).
                cand = _walk_particles_back(tail, ok)
            else:
                # 4) Long utterance — name plus reason in one breath ("quentin
                #    roch i'm calling about my knee").  Trusting the LAST token
                #    would capture "Knee", so take ONLY the token immediately
                #    after the first name, plus any particle chain it starts.
                cand = _walk_particles_forward(tail, 0, ok) if ok(tail[0]) or tail[0] in SURNAME_PARTICLES else ""
            if cand:
                return titlecase_surname(cand)

    return ""


def backfill_surname(
    caller_utterance: str,
    first_name: str,
    awaiting_surname: bool = False,
) -> str:
    """Recover a surname that arrives on a LATER turn than the first name.

    The capture pipeline reads back only the first name and often locks it
    before the surname is spoken (Susie asks "…first name and surname?", the
    caller gives the first name, it confirms, THEN the surname arrives on a
    separate turn).  Fires only on an EXPLICIT cue so it can never grab a stray
    mid-call word:

      1. an explicit marker — "surname is rock", "last name rook", or
      2. a spelled-out surname — "r o c h" → "Roch" (callers spell to correct a
         mis-heard surname), with stand-alone "i"/"a" excluded, or
      3. a bare single-token straggler, ONLY when we are provably awaiting the
         surname (STT routinely splits "Quentin Rock" across two turns).
    """
    if not caller_utterance:
        return ""
    low = caller_utterance.lower()

    # 1) Explicit marker → reuse the conservative extractor.
    if any(mk in low for mk in ("surname", "last name", "family name", "second name")):
        _s = extract_surname(caller_utterance, first_name)
        if _s:
            return _s

    # 2) Spelled-out surname ("r o c h" → "Roch").
    #    Scanning for \b([a-z])\b harvests the "s" out of every contraction, so
    #    "it's my lower back that's the problem" yielded the surname "Ss".
    #    Strip verb-contraction suffixes first, then require a RUN of adjacent
    #    single letters — that is what spelling actually looks like.  A lone
    #    "i"/"a" cannot start a run (it is the word), but may continue one, so
    #    "r a i" still spells Rai.
    _clean = re.sub(r"'(m|s|re|ve|ll|d|t)\b", " ", low)
    _clean = re.sub(r"[^a-z\s]", " ", _clean)
    _best, _cur = [], []
    for _w in _clean.split():
        if len(_w) == 1 and (_cur or _w not in ("i", "a")):
            _cur.append(_w)
        else:
            if len(_cur) > len(_best):
                _best = _cur
            _cur = []
    if len(_cur) > len(_best):
        _best = _cur
    if len(_best) >= 2:
        cand = "".join(_best)
        if (
            2 <= len(cand) <= 25
            and cand != (first_name or "").lower()
            and cand not in SURNAME_STOPWORDS
            and cand not in NAME_FALSE_POSITIVES
        ):
            return titlecase_surname(cand)

    # 3) Bare distinct-word straggler ("rock") — accepted ONLY when the caller
    #    context proves we are awaiting the surname.  Requires EXACTLY one token
    #    so a multi-word aside ("yes that works for me") can never be grabbed,
    #    and the token must clear every name stoplist.  The surname is never
    #    re-asked or read back, so any distinct valid word is accepted silently.
    if awaiting_surname:
        _toks = re.sub(r"[^a-z'\-\s]", " ", low).split()
        if len(_toks) == 1:
            cand = _toks[0]
            if (
                2 <= len(cand) <= 25
                and cand != (first_name or "").lower()
                and cand not in SURNAME_STOPWORDS
                and cand not in NAME_FALSE_POSITIVES
                and cand not in SURNAME_CONTRACTIONS
                and cand not in SURNAME_PARTICLES
            ):
                return titlecase_surname(cand)
    return ""
