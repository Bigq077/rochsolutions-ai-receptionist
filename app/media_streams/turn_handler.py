# app/media_streams/turn_handler.py
"""
Compatibility shim — all conversation logic has moved to flow.py.

The FlowEngine in flow.py is the single source of truth for what
Susie says next.  This file exists only to avoid breaking imports
in llm_stream.py which still uses sanitise_response().
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

# Re-export FlowEngine and FLOW for any code that imports them from here
from .flow import FlowEngine, FLOW  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate 5b — sentence-level banned-phrase patterns
# Each pattern strips the matching sentence from the chunk; surrounding text
# is preserved.  Applied AFTER the chunk-drop check (Gate 5a).
# ---------------------------------------------------------------------------

_BANNED_SENTENCE_RE = [
    # ── Banned opener words ──────────────────────────────────────────────────
    # Strip sycophantic/robotic openers from the very first token(s) of any
    # LLM chunk.  Anchored to ^ so it ONLY fires at the start of a chunk —
    # never mid-sentence.  Strips the word plus its connector (comma, dash,
    # exclamation) and any trailing space, leaving the substantive reply intact.
    # e.g. "Of course — which clinic?" → "Which clinic?"
    #      "Perfect, could I get your name?" → "Could I get your name?"
    #      "Absolutely, we offer acupuncture" → "We offer acupuncture"
    # Runs first so downstream patterns see clean text.
    ("banned_opener",
     re.compile(
         r"^(?:Absolutely|Certainly|Of course|Sure thing|Sure"
         r"|Wonderful|Fantastic|Exactly|Indeed|Definitely|Totally"
         r"|Obviously|Clearly|Lovely|Right so|Perfect|Great)"
         r"[,!\-—–]\s*",
         re.IGNORECASE,
     )),
    ("bear_with_me",  re.compile(r"[^.!?]*\bbear with me\b[^.!?]*[.!?]?",        re.IGNORECASE)),
    ("bare_with_me",  re.compile(r"[^.!?]*\bbare with me\b[^.!?]*[.!?]?",        re.IGNORECASE)),
    ("just_a_moment", re.compile(r"[^.!?]*\bjust a moment\b[^.!?]*[.!?]?",       re.IGNORECASE)),
    ("one_moment",    re.compile(r"[^.!?]*\bone moment please\b[^.!?]*[.!?]?",   re.IGNORECASE)),
    ("are_you_there", re.compile(r"[^.!?]*\bare you still there\b[^.!?]*[.!?]?", re.IGNORECASE)),
    ("still_there",   re.compile(r"[^.!?]*\bstill there\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    # "Lovely, [name]" acknowledgement — patronising opener, banned everywhere
    ("lovely_opener", re.compile(r"^[Ll]ovely[,\s!]+",                            re.IGNORECASE)),
    # Internal/meta orchestration text — must never reach caller TTS
    ("lookup_already_done",   re.compile(r"[^.!?]*\blookup (?:has )?already (?:been )?done\b[^.!?]*[.!?]?", re.IGNORECASE)),
    ("let_me_confirm_caller", re.compile(r"[^.!?]*\blet me confirm this with the caller\b[^.!?]*[.!?]?",    re.IGNORECASE)),
    ("lookup_already_ran",    re.compile(r"[^.!?]*\blookup(?:_appointment)? already ran\b[^.!?]*[.!?]?",    re.IGNORECASE)),
    ("rc_stage_leak",         re.compile(r"[^.!?]*\brc_stage\b[^.!?]*[.!?]?",                               re.IGNORECASE)),

    # ── LLM internal reasoning narration ────────────────────────────────────
    # These patterns match full sentences containing internal chain-of-thought
    # that the LLM occasionally speaks aloud instead of acting silently.
    # Each strips the entire offending sentence, leaving surrounding text intact.
    #
    # "The caller said/is/was/has/mentioned..." — state narration
    ("reasoning_the_caller",
     re.compile(
         r"[^.!?]*\bThe caller (?:said|is|was|has|mentioned|seems|appears|told me|wants|would like|appears to)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "The results within/show/are..." — tool-output narration
    ("reasoning_the_results",
     re.compile(
         r"[^.!?]*\bThe results? (?:within|show|shows|are|that|from|of the)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I'll pick/choose/select/present three/give them..." — selection narration
    ("reasoning_ill_select",
     re.compile(
         r"[^.!?]*\bI'?ll (?:pick|choose|select|present three|give them three|note that|flag)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I should..." — intention narration
    ("reasoning_i_should",
     re.compile(
         r"[^.!?]*\bI should\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I'd be happy to..." — call-centre filler, banned in all output
    ("id_be_happy_to",
     re.compile(
         r"[^.!?]*\bI'?d be happy to\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "let me pull/grab those up" variants — filler implying a fetch when the
    # data is already present; strip silently so the next substantive chunk
    # plays immediately.
    ("pull_those_up",
     re.compile(
         r"[^.!?]*\b(?:let me (?:just )?(?:pull those up|grab those)|pulling those up)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "(N slots)" annotation — raw slot-count parentheticals from tool result
    # narration (e.g. "Tuesday 12th (4 slots)"). Strip inline without removing
    # surrounding text so the day/time label survives.
    ("slot_count_annotation",
     re.compile(r"\s*\(\d+\s*slots?\)", re.IGNORECASE)),

    # ── Deflection openers ───────────────────────────────────────────────────
    # "That's one for the [role]", "that's one for the calendar",
    # "that's something to discuss with", "that's a question for".
    # These push the patient's question to someone else without engaging;
    # they are never appropriate as an opener.
    # Pattern: strip from the start of the response up to and including the
    # first em-dash / en-dash or sentence-ending punctuation (and any trailing
    # whitespace), leaving the substantive remainder to be re-capitalised by
    # the existing post-strip capitalisation step.
    ("one_for_practitioner",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?(?:practitioner|therapist|clinician)"
         r"[^—–.!?]*(?:[—–.!?]\s*)?",
         re.IGNORECASE,
     )),
    ("one_for_calendar",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?calendar[^—–.!?]*(?:[—–.!?]\s*)?",
         re.IGNORECASE,
     )),
    # "that's one for mark" — named-person / general deflection fallback.
    # (?:the )? handles "that's one for the admin" as well as "for mark".
    # Runs after the specific-role entries so it only catches residual cases.
    ("one_for_name",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?[a-zA-Z]+[^—–.!?]*(?:[—–.!?]\s*)?",
         re.IGNORECASE,
     )),
    ("something_to_discuss",
     re.compile(
         r"^[Tt]hat'?s something to discuss with[^—–.!?]*(?:[—–.!?]\s*)?",
         re.IGNORECASE,
     )),
    ("a_question_for",
     re.compile(
         r"^[Tt]hat'?s a question for[^—–.!?]*(?:[—–.!?]\s*)?",
         re.IGNORECASE,
     )),

    # ── Prompt C — scarcity-signalling openers ───────────────────────────────
    # "The closest/nearest X is/would be/— " — strip the preamble, keep the
    # date/time.  Anchored to start-of-chunk; strips up to and including the
    # first recognisable connector so the slot info plays directly.
    # Connectors handled: "is ", "would be ", em-dash/en-dash + space.
    ("closest_nearest_opener",
     re.compile(
         r"^[Tt]he (?:closest|nearest)\b[^.!?—–]*?"
         r"(?:\bis\s+|\bwould be\s+|[—–]\s*)",
         re.IGNORECASE,
     )),
    # "The closest I've got / I have / we have / available" — full-sentence
    # strip for forms where the date cannot be rescued by the opener-strip
    # above (no clean connector exposed, or the form is entirely a dead-end
    # qualifier with no following slot info).  Runs after the opener-strip so
    # cases already rescued by that pattern are never double-processed.
    ("closest_ive_got",
     re.compile(
         r"[^.!?]*\b[Tt]he (?:closest|nearest)\s+"
         r"(?:I'?ve\s+got|I\s+have|we\s+have|available)\b"
         r"[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),

    # "That is the only" / "That's the only" — full-sentence strip.
    # Scarcity framing with no recoverable date info; strip the whole sentence.
    ("that_is_the_only",
     re.compile(
         r"[^.!?]*\b[Tt]hat(?:'s| is) the only\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),

    # "The only [slot/day/time/...] [modifier] is ..." — strip just the
    # scarcity opener, keeping the date/time info that follows.
    # Mirrors closest_nearest_opener: recovers actionable slot info instead
    # of wiping the entire sentence.
    #
    # Before (full-sentence strip — BUG): "The only day available is Wednesday
    # the 17th — does that work?" → stripped to "" → fallback fires.
    # After  (prefix strip — FIXED):     "The only day available is " →
    # stripped, leaving "Wednesday the 17th — does that work?" → plays fine.
    #
    # Handles: "The only slot available is", "The only day we have is",
    # "The only available appointment is", "The only time I've got is", etc.
    ("the_only_slot_scarcity",
     re.compile(
         r"\b[Tt]he only\s+"
         r"(?:available\s+)?"
         r"(?:slot|day|time|option|appointment|[a-zA-Z]+day)"
         r"(?:\s+(?:available|that\s+(?:is\s+)?available"
         r"|we\s+have(?:\s+available)?|I(?:'ve)?\s+(?:got|have)(?:\s+available)?))?"
         r"\s+(?:is|are|would be)\s*",
         re.IGNORECASE,
     )),
]

_MULTI_SPACE_RE  = re.compile(r" {2,}")
# Also strips leading ': ' colon artefacts left when a reasoning sentence that
# ended with a colon is removed and the continuation chunk starts with ": ".
_LEADING_JUNK_RE = re.compile(r"^[\s:,—–\-]+")


# ---------------------------------------------------------------------------
# Gate 5a — chunk-level reasoning drop patterns
# If ANY of these match, the ENTIRE chunk is discarded and never reaches TTS.
# Applied BEFORE sentence-level stripping.  Matches internal reasoning output
# that should never reach the spoken layer.
# ---------------------------------------------------------------------------

# ✓ ✗ check-mark / cross symbols — hallmark of reasoning tables
_REASONING_TICK_CROSS_RE = re.compile(r"[✓✗]")

# HH:MM timestamp — >1 in a single chunk indicates a reasoning timestamp table
_REASONING_HH_MM_RE = re.compile(r"\d{2}:\d{2}")

# Sentences that open with explicit reasoning narration openers.
# Anchored to start-of-string OR after sentence-ending punctuation.
_REASONING_OPENER_RE = re.compile(
    r"(?:^|(?<=[.!?])\s*)"
    r"(?:Filtering|Checking|Skipping|The rule says|I'?ll need to|With only|"
    r"Let me work out|Looking at the|Calculating|So I need to|I should)\b",
    re.IGNORECASE,
)

# Internal label / flag words that only appear in reasoning, never in speech
_REASONING_LABEL_RE = re.compile(
    r"\b(?:single-slot|lead-time|late afternoon only|working out|decision)\b",
    re.IGNORECASE,
)

# High-density time references: >3 per chunk = a reasoning table, not speech.
# Matches patterns like "5pm", "17:00", "five in the afternoon" etc.
_TIME_DENSITY_RE = re.compile(
    r"\d+(?::\d+)?\s*(?:am|pm|in the morning|in the afternoon|in the evening)",
    re.IGNORECASE,
)


def _get_reasoning_drop_reason(text: str) -> str:
    """
    Returns a non-empty description string if `text` should be dropped
    entirely as internal reasoning output.  Returns "" if the chunk is safe.

    Called per-chunk in Gate 5a, before any sentence-level stripping.
    """
    if _REASONING_TICK_CROSS_RE.search(text):
        return "tick_cross_symbol"
    if len(_REASONING_HH_MM_RE.findall(text)) > 1:
        return "multiple_hhmm_timestamps"
    if _REASONING_OPENER_RE.search(text):
        return "reasoning_sentence_opener"
    if _REASONING_LABEL_RE.search(text):
        return "internal_label_word"
    if len(_TIME_DENSITY_RE.findall(text)) > 3:
        return "high_time_density"
    return ""


# ---------------------------------------------------------------------------
# sanitise_response — public API, called per-chunk from llm_stream.py
# ---------------------------------------------------------------------------

def sanitise_response(text: str, session: Dict[str, Any]) -> str:
    """
    Clean LLM output before it reaches tts_text_queue.

    Gate 5a — chunk-level reasoning drop:
      Discards the ENTIRE chunk when internal-reasoning patterns are detected.
      Logs the drop and increments session["_gate5_reasoning_drops"].
      The pipeline continues normally — only the individual chunk is gone.
      Slot map, DTMF standby, and watchdog state are unaffected.

    Gate 5b — sentence-level stripping:
      Strips individual banned phrases / sentences while leaving surrounding
      text intact (existing behaviour, unchanged).

    Called per-chunk so the pipeline stays streaming.
    """
    if not text or not text.strip():
        return text

    # ── Gate 5a: whole-chunk reasoning drop ──────────────────────────────────
    _drop_reason = _get_reasoning_drop_reason(text)
    if _drop_reason:
        _preview = (text[:50] + "...") if len(text) > 50 else text
        logger.info(
            "[ms_gate5] dropped reasoning chunk (%s): %r",
            _drop_reason,
            _preview,
        )
        session["_gate5_reasoning_drops"] = (
            int(session.get("_gate5_reasoning_drops") or 0) + 1
        )
        return ""

    # ── Gate 5b: sentence-level stripping ────────────────────────────────────
    result = text

    for desc, pattern in _BANNED_SENTENCE_RE:
        cleaned = pattern.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed banned phrase (%s)", desc)
            result = cleaned

    result = result.replace("\n", " ")
    result = _MULTI_SPACE_RE.sub(" ", result)
    result = _LEADING_JUNK_RE.sub("", result)
    result = result.strip()
    # ── Fix A: re-capitalise after opener strip ──────────────────────────────
    # Banned opener patterns (lovely_opener) strip the leading phrase and
    # leave the rest of the sentence starting with a lower-case continuation
    # word.  Restore sentence-initial capitalisation.
    if result:
        result = result[0].upper() + result[1:]
    return result
