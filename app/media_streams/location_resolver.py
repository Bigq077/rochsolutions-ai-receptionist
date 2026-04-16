"""
app/media_streams/location_resolver.py

Deterministic weighted clinic-location resolver for Theorem Health.

Resolves noisy STT transcripts to "alcester" or "redditch" using four
layered signals combined into a single weighted score:

    1. Hard aliases     — immediate resolution, no scoring needed
    2. Soft aliases     — +40 score bonus, still requires threshold + margin
    3. Prefix boost     — +8 to +25 based on how well the candidate's first
                          word starts with a clinic-identifying prefix
    4. Similarity       — 0–100 from difflib SequenceMatcher against a curated
                          set of reference forms for each clinic

Plus two safety layers:
    • Never-bind list   — known English-word collisions (radish, lester …)
                          that would score deceptively high; always clarify
    • Strict sim-only   — if neither prefix nor soft alias fired, require a
                          higher similarity threshold before auto-resolving

Designed exclusively for explicit ASK_LOCATION contexts.  The resolver is
more permissive than a global classifier but still rejects weak/conflicting
evidence rather than guessing.

Public API
----------
    resolve_clinic_location(raw_text, context="ask_location") -> dict

Return shape
------------
    {
        "status":     "resolved" | "ambiguous" | "unknown",
        "location":   "alcester" | "redditch" | None,
        "confidence": float,          # 0.0–1.0
        "reason":     str,            # e.g. "hard_alias", "soft_alias+prefix+similarity"
        "debug": {
            "raw":                str,
            "normalized":         str,
            "candidate":          str,
            "alcester_score":     int,
            "redditch_score":     int,
            "best_alcester_target": str,
            "best_redditch_target": str,
            "prefix_hit":         str | None,
            "hard_alias_hit":     str | None,
            "soft_alias_hit":     str | None,
            # extra diagnostic fields:
            "alcester_sim":       int,
            "redditch_sim":       int,
            "alcester_prefix":    int,
            "redditch_prefix":    int,
        },
    }
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard aliases
# Trusted enough to resolve immediately in ASK_LOCATION context.
# Checked as substring of the normalised candidate (longest-first to avoid
# short entries masking longer, more specific ones).
# ---------------------------------------------------------------------------
_HARD_ALC: frozenset = frozenset({
    # Canonical and near-canonical forms
    "alcester", "alcesta", "alcest", "alcestra",
    "alchester", "alkester", "alsester", "asester",
    "alcestr", "allcester", "alster", "alca", "alces",
    # STT confirmed variants
    "ancester", "ancestor", # strong phonetic — safe in explicit context
    "alceister", "alcesster", "arlcester", "alcaster",
    # Spaced/hyphenated forms
    "al sester", "al-sester",
    "our sester", "all sester",
    "all sister", "a sister", "al sister",
    "our sister clinic",    # specific enough for hard; bare "our sister" → soft
    "sester clinic", "sister clinic",
    "i ll sister", "house sister", "old sister",
    "l sester", "lsester",
    # Geographic landmark unambiguously associated with Alcester site
    "kinwarton",
})

_HARD_RED: frozenset = frozenset({
    # Canonical and near-canonical forms
    "redditch", "reddit", "red itch", "read itch",
    "redich", "reddich", "redidge", "reditch",
    "red each", "read each",
    "ready itch", "ready each",
    "red dish", "read edge", "red edge", "ready edge",
    "red idge", "red itch clinic",
    "reddis",
    # Geographic landmark unambiguously associated with Redditch site
    "bromsgrove",
})

# ---------------------------------------------------------------------------
# Soft aliases
# Contribute +40 points but must still pass threshold + margin checks.
# Must NOT overlap with hard aliases (any overlap would be unreachable).
# ---------------------------------------------------------------------------
_SOFT_ALC: frozenset = frozenset({
    "our sister",           # bare phrase without "clinic" — weaker evidence
    "an sester",            # STT noise variant
    "el sester", "elsester",
    "your access", "your access clinic",
    "our cester",
    "al sister clinic",
})

_SOFT_RED: frozenset = frozenset({
    "raditch",              # r+aditch → phonetic Redditch variant
    "reddage",
    "redish",
    "read dish",
    "red age", "read age",
})

# ---------------------------------------------------------------------------
# Never-bind list
# Known English-word collisions that score deceptively high against one clinic
# but are genuinely ambiguous.  Always trigger clarification regardless of score.
# ---------------------------------------------------------------------------
_NEVER_BIND: frozenset = frozenset({
    # Alcester collisions
    "leicester", "lester", "ulster",
    # Redditch collisions
    "radish", "reddish", "registry", "wreckage",
})

# ---------------------------------------------------------------------------
# Prefix-fallback blocklist
# Common English discourse words whose opening letter/syllable matches a
# clinic-identifying prefix but which are never location references.
# Only include words that are UNAMBIGUOUSLY non-clinic; do NOT include any
# word that could plausibly be an STT variant of a clinic name (e.g. "ready"
# could be part of "ready itch" → Redditch, so it must not be blocked).
# ---------------------------------------------------------------------------
_PREFIX_FALLBACK_BLOCKLIST: frozenset = frozenset({
    # "a…" — discourse starters, phonetically nothing like Alcester
    "actually", "anyway", "already", "again", "also", "always",
    # "re…" / "r…" — discourse starters, phonetically nothing like Redditch
    "right", "really", "rather", "recently",
    # Pure discourse openers (no clinic-prefix ambiguity)
    "i", "so", "well", "no", "yes", "wait", "never", "sorry",
})

# ---------------------------------------------------------------------------
# Similarity reference targets
# A small, discriminating set of reference forms for each clinic.
# Spaces are stripped before comparison so STT-inserted gaps don't penalise.
# ---------------------------------------------------------------------------
_SIM_TARGETS_ALC: tuple = ("alcester", "alcesta", "alchester", "alkester", "alsester")
_SIM_TARGETS_RED: tuple = ("redditch", "reddit",  "reditch",   "reddich")

# ---------------------------------------------------------------------------
# Prefix score tables  (prefix_string, score) — most specific first
# Score range: 8 (single char, weak) → 25 (4+ chars, strong)
# ---------------------------------------------------------------------------
_PREFIX_ALC: tuple = (
    ("alc", 25), ("alk", 25), ("als", 25),
    ("al",  20), ("an",  15),
    ("a",    8),
)
_PREFIX_RED: tuple = (
    ("redd", 25), ("red", 22), ("read", 20),
    ("re",   15), ("r",    8),
)

# ---------------------------------------------------------------------------
# Decision thresholds
# ---------------------------------------------------------------------------
_SOFT_ALIAS_BONUS    = 40    # points added when a soft alias matches
_RESOLVE_THRESHOLD   = 65    # winner score needed when prefix or soft alias supports
_SIM_ONLY_THRESHOLD  = 82    # winner score needed with similarity evidence only
_RESOLVE_MARGIN      = 25    # minimum gap between winner and loser scores

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------
_RE_PUNCT = re.compile(r"[^\w\s]")
_RE_WS    = re.compile(r"\s+")
_RE_STRIP_ARTICLES = re.compile(r"^(?:the|a|an)\s+")

# Candidate extraction patterns — tried in decreasing specificity order.
# Group 1 is the extracted candidate phrase.
_EXTRACT_PATTERNS: tuple = (
    # "original [appointment] [was] at|in <phrase> [clinic|one]"
    re.compile(
        r"\boriginal(?:\s+appointment)?(?:\s+was)?\s+(?:at|in)\s+"
        r"(.+?)(?:\s+clinic\b|\s+one\b|$)"
    ),
    # "booked at|in <phrase> [clinic|one]"
    re.compile(
        r"\bbooked\s+(?:at|in)\s+(.+?)(?:\s+clinic\b|\s+one\b|$)"
    ),
    # "appointment at|in <phrase> [clinic|one]"
    re.compile(
        r"\bappointment\s+(?:at|in)\s+(.+?)(?:\s+clinic\b|\s+one\b|$)"
    ),
    # "at|in <phrase> [clinic|one]"
    re.compile(
        r"\b(?:at|in)\s+(.+?)(?:\s+clinic\b|\s+one\b|$)"
    ),
    # "the <phrase> clinic"
    re.compile(r"\bthe\s+(.+?)\s+clinic\b"),
    # "<phrase> clinic"
    re.compile(r"\b(\w+(?:\s+\w+){0,2})\s+clinic\b"),
    # "<phrase> one"
    re.compile(r"\b(\w+(?:\s+\w+){0,1})\s+one\b"),
)

_FILLER_WORDS = frozenset({"a", "an", "the", "it", "is", "was", "be"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, replace punctuation with spaces, collapse whitespace."""
    t = _RE_PUNCT.sub(" ", text.strip().lower())
    return _RE_WS.sub(" ", t).strip()


def _contains_any(text: str, phrases: frozenset) -> Optional[str]:
    """
    Return the first phrase from *phrases* found as a substring of *text*,
    searching longest phrases first so specific entries win over short ones.
    Returns None if nothing matches.
    """
    for phrase in sorted(phrases, key=len, reverse=True):
        if phrase in text:
            return phrase
    return None


def _extract_candidate(text: str) -> str:
    """
    Extract the most likely location phrase from a normalised utterance.

    Tries anchor patterns in decreasing specificity order.
    Falls back to the whole normalised text if no anchor matches.
    """
    for pat in _EXTRACT_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = _RE_STRIP_ARTICLES.sub("", m.group(1).strip())
            if len(candidate) >= 2 and candidate not in _FILLER_WORDS:
                return candidate
    return text


def _prefix_score(candidate: str, clinic: str) -> int:
    """
    Return 0–25 based on how strongly the candidate's first word starts
    with a clinic-identifying prefix.  Checks against the first word only
    so multi-word candidates like "our sister" don't get a false hit on
    the initial "o".
    """
    first_word = candidate.split()[0]
    table = _PREFIX_ALC if clinic == "alcester" else _PREFIX_RED
    for prefix, pts in table:
        if first_word.startswith(prefix):
            return pts
    return 0


def _max_similarity(candidate: str, targets: tuple) -> Tuple[float, str]:
    """
    Compare *candidate* (with spaces removed) against each target string
    using difflib SequenceMatcher.  Returns (best_ratio, best_target).
    """
    cand_ns = _RE_WS.sub("", candidate)  # remove spaces for phonetic comparison
    best_ratio  = 0.0
    best_target = targets[0]
    for t in targets:
        ratio = SequenceMatcher(None, cand_ns, t).ratio()
        if ratio > best_ratio:
            best_ratio  = ratio
            best_target = t
    return best_ratio, best_target


def _build_reason(
    has_soft: bool,
    has_prefix: bool,
    sim_pts: int,
) -> str:
    """Build a human-readable reason string from which signals contributed."""
    parts = []
    if has_soft:
        parts.append("soft_alias")
    if has_prefix:
        parts.append("prefix")
    if sim_pts >= 40:
        parts.append("similarity")
    return "+".join(parts) if parts else "low_signal"


def _log_result(result: dict) -> None:
    d = result["debug"]
    logger.info(
        "[location_resolver] raw=%r normalized=%r candidate=%r "
        "alcester_score=%s(sim=%s,prefix=%s) redditch_score=%s(sim=%s,prefix=%s) "
        "status=%r location=%r reason=%r confidence=%.2f "
        "hard=%r soft=%r prefix=%r",
        d.get("raw"),        d.get("normalized"), d.get("candidate"),
        d.get("alcester_score"), d.get("alcester_sim"), d.get("alcester_prefix"),
        d.get("redditch_score"), d.get("redditch_sim"), d.get("redditch_prefix"),
        result["status"],    result["location"],   result["reason"],
        result["confidence"],
        d.get("hard_alias_hit"), d.get("soft_alias_hit"), d.get("prefix_hit"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_clinic_location(
    raw_text: str,
    context: str = "ask_location",
) -> dict:
    """
    Resolve a caller utterance to 'alcester', 'redditch', or ambiguous/unknown.

    Parameters
    ----------
    raw_text : str
        Raw STT transcript (any case, may contain punctuation).
    context : str
        Resolution mode.  Currently only ``"ask_location"`` is used, which
        activates permissive thresholds appropriate for explicit clinic-
        selection steps.  Any other value applies stricter thresholds.

    Returns
    -------
    dict — see module docstring for the full schema.
    """
    # ── Normalise input ────────────────────────────────────────────────────
    normalized = _normalize(raw_text)
    candidate  = _extract_candidate(normalized)

    # Shared debug structure — populated incrementally below.
    debug: dict = {
        "raw":                  raw_text,
        "normalized":           normalized,
        "candidate":            candidate,
        "alcester_score":       0,
        "redditch_score":       0,
        "best_alcester_target": _SIM_TARGETS_ALC[0],
        "best_redditch_target": _SIM_TARGETS_RED[0],
        "prefix_hit":           None,
        "hard_alias_hit":       None,
        "soft_alias_hit":       None,
        # Extra diagnostic fields
        "alcester_sim":         0,
        "redditch_sim":         0,
        "alcester_prefix":      0,
        "redditch_prefix":      0,
    }

    def _resolve(status, location, confidence, reason):
        result = {
            "status":     status,
            "location":   location,
            "confidence": confidence,
            "reason":     reason,
            "debug":      debug,
        }
        _log_result(result)
        return result

    # ── 1. Never-bind guard ────────────────────────────────────────────────
    # Whole-word check against known collision words.  These always clarify.
    cand_words = set(candidate.split())
    if cand_words & _NEVER_BIND:
        matched = next(w for w in cand_words if w in _NEVER_BIND)
        debug["hard_alias_hit"] = f"never_bind:{matched}"
        return _resolve("ambiguous", None, 0.0, "never_bind")

    # ── 2. Hard alias check ────────────────────────────────────────────────
    hard_alc = _contains_any(candidate, _HARD_ALC)
    hard_red = _contains_any(candidate, _HARD_RED)

    debug["hard_alias_hit"] = (
        f"alcester:{hard_alc}" if hard_alc and not hard_red else
        f"redditch:{hard_red}" if hard_red and not hard_alc else
        f"conflict:{hard_alc}|{hard_red}" if hard_alc and hard_red else None
    )

    if hard_alc and not hard_red:
        debug.update(alcester_score=100, redditch_score=0)
        return _resolve("resolved", "alcester", 1.0, "hard_alias")

    if hard_red and not hard_alc:
        debug.update(alcester_score=0, redditch_score=100)
        return _resolve("resolved", "redditch", 1.0, "hard_alias")

    if hard_alc and hard_red:
        debug.update(alcester_score=100, redditch_score=100)
        return _resolve("ambiguous", None, 0.0, "conflict")

    # ── 3. Compute weighted scores for both clinics ────────────────────────

    alc_sim_ratio, alc_best = _max_similarity(candidate, _SIM_TARGETS_ALC)
    red_sim_ratio, red_best = _max_similarity(candidate, _SIM_TARGETS_RED)

    alc_sim_pts    = int(alc_sim_ratio * 100)
    red_sim_pts    = int(red_sim_ratio * 100)
    alc_prefix_pts = _prefix_score(candidate, "alcester")
    red_prefix_pts = _prefix_score(candidate, "redditch")
    alc_soft       = _contains_any(candidate, _SOFT_ALC)
    red_soft       = _contains_any(candidate, _SOFT_RED)

    alc_score = min(100, alc_sim_pts
                       + alc_prefix_pts
                       + (_SOFT_ALIAS_BONUS if alc_soft else 0))
    red_score = min(100, red_sim_pts
                       + red_prefix_pts
                       + (_SOFT_ALIAS_BONUS if red_soft else 0))

    debug.update(
        alcester_score       = alc_score,
        redditch_score       = red_score,
        best_alcester_target = alc_best,
        best_redditch_target = red_best,
        alcester_sim         = alc_sim_pts,
        redditch_sim         = red_sim_pts,
        alcester_prefix      = alc_prefix_pts,
        redditch_prefix      = red_prefix_pts,
        prefix_hit           = (
            f"alcester:{alc_prefix_pts}" if alc_prefix_pts else
            f"redditch:{red_prefix_pts}" if red_prefix_pts else None
        ),
        soft_alias_hit       = (
            f"alcester:{alc_soft}" if alc_soft else
            f"redditch:{red_soft}" if red_soft else None
        ),
    )

    # ── 4. Decision policy ─────────────────────────────────────────────────

    # Exact tie — genuinely ambiguous
    if alc_score == red_score:
        return _resolve("ambiguous", None, 0.0, "conflict")

    # Identify winner
    if alc_score > red_score:
        winner       = "alcester"
        winner_score = alc_score
        loser_score  = red_score
        has_prefix   = alc_prefix_pts > 0
        has_soft     = bool(alc_soft)
        winner_sim   = alc_sim_pts
    else:
        winner       = "redditch"
        winner_score = red_score
        loser_score  = alc_score
        has_prefix   = red_prefix_pts > 0
        has_soft     = bool(red_soft)
        winner_sim   = red_sim_pts

    margin = winner_score - loser_score
    reason = _build_reason(has_soft, has_prefix, winner_sim)

    # Effective threshold: pure similarity wins require stricter evidence
    # to guard against false positives (e.g. "lester" → alcester via sim alone).
    # If prefix or soft alias contributed, the lower threshold applies.
    if context == "ask_location":
        threshold = _RESOLVE_THRESHOLD if (has_soft or has_prefix) else _SIM_ONLY_THRESHOLD
        margin_needed = _RESOLVE_MARGIN
    else:
        # Stricter mode for any non-explicit context (defensive default)
        threshold     = _SIM_ONLY_THRESHOLD
        margin_needed = 35

    # Confidence: scale winner score, never 1.0 on scoring path (reserved for hard alias)
    confidence = round(min(winner_score, 99) / 100, 2)

    if winner_score >= threshold and margin >= margin_needed:
        return _resolve("resolved", winner, confidence, reason)

    # ── 5. ASK_LOCATION-only prefix fallback ──────────────────────────────────
    # After scoring fails to meet the resolution threshold, inspect the first
    # meaningful token's prefix as a last-resort lean in explicit clinic-
    # selection context.  Only fires when the opposing clinic has weak evidence
    # (< 40 pts) so it cannot override a genuinely competitive score.
    # Confidence is capped at 0.55 to signal low certainty; the flow opens a
    # one-turn correction window so the caller can immediately override.
    if context == "ask_location":
        _first = candidate.split()[0]
        # Skip prefix_fallback entirely for discourse/filler words — these
        # share an opener letter with a clinic prefix but are never location
        # references.  Without this guard "actually" → fallback:alcester.
        if _first not in _PREFIX_FALLBACK_BLOCKLIST:
            _alc_pfx = any(_first.startswith(p) for p, _ in _PREFIX_ALC)
            _red_pfx = any(_first.startswith(p) for p, _ in _PREFIX_RED)
            if _alc_pfx and not _red_pfx and red_score < 40:
                debug["prefix_hit"] = f"fallback:alcester:{_first}"
                return _resolve("resolved", "alcester", 0.55, "prefix_fallback")
            if _red_pfx and not _alc_pfx and alc_score < 40:
                debug["prefix_hit"] = f"fallback:redditch:{_first}"
                return _resolve("resolved", "redditch", 0.55, "prefix_fallback")

    if winner_score >= 40 and margin >= 15:
        # Some signal but not enough to auto-resolve — ask for clarification
        return _resolve("ambiguous", None, confidence, reason)

    return _resolve("unknown", None, 0.0, "low_signal")
