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
# sanitise_response — still used by llm_stream.py to clean LLM output
# ---------------------------------------------------------------------------

_BANNED_SENTENCE_RE = [
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
]

_MULTI_SPACE_RE  = re.compile(r" {2,}")
_LEADING_JUNK_RE = re.compile(r"^[\s,—–\-]+")


def sanitise_response(text: str, session: Dict[str, Any]) -> str:
    """
    Clean LLM output before it reaches tts_text_queue.

    Strips banned filler phrases and whitespace artefacts.
    Called per-chunk so the pipeline stays streaming.
    """
    if not text or not text.strip():
        return text

    result = text

    for desc, pattern in _BANNED_SENTENCE_RE:
        cleaned = pattern.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed banned phrase (%s)", desc)
            result = cleaned

    result = _MULTI_SPACE_RE.sub(" ", result)
    result = _LEADING_JUNK_RE.sub("", result)
    return result.strip()
