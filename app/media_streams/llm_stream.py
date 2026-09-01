# app/media_streams/llm_stream.py
"""
Claude streaming LLM integration for the Media Streams pipeline.

Key difference from realtime.py (non-streaming):
  - Uses client.messages.stream() to receive tokens as they arrive
  - Feeds tokens through ResponseChunker -> emits 15-50 word chunks
  - Each chunk is immediately sent to ElevenLabs TTS via tts_text_queue
  - First audio can start playing while Claude is still generating tokens

Fast-path integration:
  - try_fast_path() is called BEFORE the LLM on every turn
  - If FastPathResult returned (needs_llm=False): play response_text, skip LLM
  - If None returned (session updated silently): LLM handles the full response
  - If no match: LLM handles the full turn

Filler guard:
  - If no first chunk within LLM_FIRST_CHUNK_TIMEOUT_MS (5s), play filler phrase
  - Filler rate-limited to once per LLM_FILLER_COOLDOWN_SEC (20s)

Tool calling:
  - Tool calls require full response buffering (Claude API streaming constraint)
  - Text alongside tool calls is chunked and queued for TTS before tools execute
  - After tools run, streaming continues for the next LLM turn

GPT-4.1-mini fallback:
  - Activated when Claude raises APIStatusError with status 529 or 500
  - Same chunked delivery through tts_text_queue

Model selection:
  - SONNET if active booking step (slots offered, confirming name/phone/booking)
  - HAIKU otherwise (information queries, greetings, FAQ)
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
import time
from datetime import date, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .config import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    SONNET,
    HAIKU,
    GPT_MODEL,
    CLAUDE_MAX_TOKENS,
    CLAUDE_TEMPERATURE,
    FORCE_TEXT_NEXT_ITERATION,
    MAX_TOOL_ITERATIONS,
    MAX_HISTORY_TURNS,
    LLM_FIRST_CHUNK_TIMEOUT_MS,
    HOLD_HEAD_DELAY_MS,
    LLM_FILLER_COOLDOWN_SEC,
    LLM_FILLER_SECOND_DELAY_MS,
    FILLER_PHRASES,
    FILLER_PHRASE,
    ACK_FILLER_MARKER,
    SAFE_FALLBACK_PHRASE,
    F_LAST_BOT_PROMPT,
    F_LAST_QUESTION,
    F_COLLECTED,
    WS_A_FAST_FIRST_CHUNK,
    WS_A_MIN_WORDS_FIRST,
    BOOK_CLASSIFIER_ENABLED,
    BOOK_CLASSIFIER_TIMEOUT_S as _BOOK_CLASSIFIER_TIMEOUT_S,
)
from app.obs import turns as _obs_turns
from .chunker import ResponseChunker
from .fast_path import try_fast_path
from .session import save_session
from .tts_stream import _apply_tts_substitutions_elevenlabs as _apply_tts_subs
from .turn_handler import (
    sanitise_response,
    _phone_question_for,
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_CANCEL,
    WRITE_FAMILY_RESCHEDULE,
    WRITE_REFUSED_KEY,
    WRITE_SUCCEEDED_KEY,
    CANCEL_SUCCEEDED_ID_KEY,
)

logger = logging.getLogger(__name__)

# Sentinel for usage-field probing: separates "the SDK did not report this
# field" from "the field is present and zero". See the [LAT] prompt-cache
# counters in _one_streaming_call.
_MISSING_USAGE = object()


def _usage_token(usage: Any, name: str) -> Optional[int]:
    """One counter off an Anthropic ``usage`` object, or None if absent.

    The distinction is the entire point, and it is not pedantry:

      * attribute MISSING          -> None -> reported as -1, "not observed"
      * attribute present but None -> 0    -> "observed, nothing cached"
      * attribute present, integer -> that integer

    Defaulting a missing attribute to 0 would make an SDK rename look exactly
    like a permanently cold prompt cache -- which is the one conclusion these
    counters exist to test (B2). Verified against anthropic 0.84.0, whose
    Usage model carries cache_read_input_tokens, cache_creation_input_tokens
    and input_tokens.
    """
    _v = getattr(usage, name, _MISSING_USAGE)
    if _v is _MISSING_USAGE:
        return None
    try:
        return int(_v or 0)
    except (TypeError, ValueError):
        return None

# Sentinel prefix for pre-tool text chunks.  All text chunks in a streaming
# call are prefixed with this marker so that if check_availability is detected
# mid-stream (via content_block_start), the tts_loop can drop them before they
# reach ElevenLabs.  Uses the same marker+flag pattern as ACK_FILLER_MARKER.
PRE_SLOT_MARKER = "\x01PRE_SLOT\x01"


def _clinic_keeps_pre_slot_speech(session: Dict[str, Any]) -> bool:
    """True when this clinic opts in to keeping pre-check_availability speech.

    Engine-wide, text streamed before a check_availability tool_use is marked
    with PRE_SLOT_MARKER and dropped when the tool is detected — otherwise the
    caller hears a half-finished sentence, then the hold clip, then slots.
    That suppress is load-bearing and must not be deleted.

    Job 3c.3 / CAce1457d1: on Joint Venture the dropped text was the physio
    empathy line Quentin wanted callers to hear ("I'm sorry to hear that —
    ankle problems can really stop you…"). Opt-in via
    prompt_facts.keep_pre_slot_speech so only clinics that ask for it keep the
    line; everyone else keeps the silence-safe default.
    """
    try:
        from app.clinic_config import get_clinic
        _pf = (get_clinic(session.get("clinic_id")) or {}).get("prompt_facts") or {}
        return bool(_pf.get("keep_pre_slot_speech"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Guaranteed end-of-turn fallbacks (C8-5 silence eradication)
# ---------------------------------------------------------------------------
# When a turn completes without a single audio chunk reaching the caller the
# loop-level guarantee in _streaming_tool_loop emits one of these so the caller
# never hears dead air.  Two variants so the message fits the situation:
#   - NO_AVAILABILITY_FALLBACK: a check_availability ran this turn and the most
#     recent one returned zero slots (the original C8-5 scenario — caller asked
#     for a date with no openings and Susie went silent).
#   - SILENCE_RECOVERY_FALLBACK: any other no-speech turn (text fully stripped
#     by gate5, suppression-gate break with no flow.py output, etc.).
NO_AVAILABILITY_FALLBACK = (
    "I'm afraid I don't have anything free then — "
    "would another day or time work for you?"
)
SILENCE_RECOVERY_FALLBACK = (
    "Sorry, I didn't quite catch that — could you say that again?"
)
#   - SLOT_RECOVERY_FALLBACK: a check_availability ran this turn AND returned
#     slots, but the turn still produced no audible speech (e.g. gate5 dropped a
#     reasoning-prefaced slot chunk).  The caller WAS understood — never blame
#     them with "I didn't quite catch that"; own the hiccup and let them re-ask.
SLOT_RECOVERY_FALLBACK = (
    "Sorry, could you say that again for me?"
)


# ---------------------------------------------------------------------------
# Cache-invalidation helpers for check_availability dedup guard
# ---------------------------------------------------------------------------

def _extract_week_reference(hint: str) -> str:
    """
    Return a normalised week token from a date_hint string.

    Used to decide whether two consecutive check_availability calls target
    the same week (same cache is valid) or a different week (cache must be
    invalidated so a fresh API call is made).

    Returns one of: "week_after" / "next_week" / "this_week" /
    "day_<name>" / "unspecified".
    """
    if not hint:
        return "unspecified"
    h = hint.lower()
    if (
        "week after" in h
        or "following week" in h
        or "the week after" in h
        or "next next week" in h
    ):
        return "week_after"
    if "next week" in h:
        return "next_week"
    if "this week" in h:
        return "this_week"
    for _day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        if _day in h:
            return f"day_{_day}"
    return "unspecified"


def _date_hints_differ_materially(hint_a: str, hint_b: str) -> bool:
    """
    Return True when the two date_hints reference materially different
    time windows (different weeks).  A difference in time-of-day filter
    alone (e.g. "next week mornings" vs "next week afternoons") is NOT
    considered material — the week is the same.
    """
    return _extract_week_reference(hint_a) != _extract_week_reference(hint_b)


# Words that signal the caller wants a DIFFERENT / new slot (a real reason to
# re-run check_availability after a slot is already confirmed).  Used by the
# slot-locked guard so a genuine slot change still searches while a spurious
# re-search during name collection is blocked.  Matched on whole words only.
_NEW_SLOT_INTENT_WORDS: frozenset = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "morning", "afternoon", "evening", "noon", "midday", "tonight",
    "tomorrow", "today", "week", "weekend", "o'clock",
    "earlier", "later", "sooner", "soonest", "another", "other", "different",
    "instead", "change", "move", "reschedule", "else", "no", "not", "nope",
    "wrong", "actually", "next", "following", "available", "slot", "when",
    "day", "date", "time",
})


# An affirmative is a SHAPE, not a phrase table: a short reply carrying a
# yes-word and no negation. The alternative is the treadmill documented on
# _PHONE_CONFIRM_AFFIRMATIVE_PHRASES, patched four times, once for each literal
# a single live call happened to use.
#
# Word-boundary matching throughout: the screening negators were substring-
# matched once and "know" contained "no".
_DAY_OFFER_YES: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "aye", "sure", "ok", "okay", "okey",
    "please", "alright", "right", "fine", "great", "perfect", "lovely",
    "go", "do", "definitely", "absolutely", "certainly", "cheers", "ta",
})
_DAY_OFFER_NO: frozenset = frozenset({
    "no", "nope", "nah", "not", "dont", "don't", "never", "cant", "can't",
})


def _is_short_affirmative(text: str) -> bool:
    """True for "yes" / "yeah go for it" / "go on then" / "please do"."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 6:
        return False
    if set(words) & _DAY_OFFER_NO:
        return False
    return bool(set(words) & _DAY_OFFER_YES)


def _answering_susies_different_day_offer(messages, session) -> bool:
    """The caller said yes to Susie's OWN offer to look at another day.

    Finding 2 / CA890b511e (27 Aug 2026, 08:42:53):

        Susie:  "I don't have any further times on that day - would you like
                 me to look at a different day?"
        caller: "yeah go for it"
        -> _caller_requests_different_day found no day word and no change
           phrase, so the dedup guard held, the model's correct
           check_availability was blocked, and she re-read the SAME two days.

    The day was named by SUSIE, so a predicate that only reads the caller's
    words cannot see it -- the same shape as the Gate-5g name deadlock, where
    the only source of the first name was Susie's own speech.

    Matched against the sentence THIS CODEBASE generates, taken from the
    function that generates it, so a reword cannot leave a stale literal
    behind. That is why this is not the banned "match one literal of model
    speech": the text is ours, deterministic, and has exactly one producer.

    One-turn scoped for free: it reads the IMMEDIATELY PRECEDING assistant
    message, so it cannot fire on a stale flag a turn later.
    """
    try:
        if not isinstance(session, dict):
            return False          # no history to read -- stay conservative
        from app.tools.slot_followup import format_next_batch_speech
        offer = format_next_batch_speech([], False).strip().lower()
        if not offer:
            return False
        # Inlined rather than calling _last_assistant_text, which exists ONLY
        # on latency-eval -- it arrived with the B-19 filler re-arm and never
        # ported. Depending on it made this whole predicate a silent no-op on
        # all three live branches: the NameError was swallowed by the except
        # below and every caller simply read False, which is the defect this
        # function exists to fix. Four lines is cheaper than a cross-branch
        # dependency.
        said = ""
        for _m in reversed((session.get("conversation_history") or [])):
            if isinstance(_m, dict) and _m.get("role") == "assistant":
                said = str(_m.get("content") or "")
                break
        if offer not in said.strip().lower():
            return False
        return _is_short_affirmative(_last_user_text(messages))
    except Exception:
        # NEVER silently: a swallowed error here reads exactly like "the caller
        # did not ask", which is the bug. That is how the missing helper above
        # went unnoticed until the port suites caught it.
        logger.exception(
            "[ms_llm] different-day-offer check failed — treating as no request"
        )
        return False


def _last_user_text(messages) -> str:
    """Return the most recent caller TEXT utterance from the message list,
    skipping tool_result-only user turns (which carry no spoken text)."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            if c.strip():
                return c
            continue
        if isinstance(c, list):
            parts = [
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
    return ""


# ---------------------------------------------------------------------------
# C1 — the date the caller was actually told (write-guard, 2026-07-30)
# ---------------------------------------------------------------------------
# CA5c4fb14f: she said "Tuesday the 4th of August at seven in the evening", he
# said yes, she said "All booked", and the event was created for 2026-08-05 — a
# Wednesday. Nothing in the call sounds wrong and nothing downstream is
# inconsistent (the booking matches the slot), so no guard, detector or readback
# enforcement could see it. The caller arrives to nothing.
#
# Steering the model away from mislabelling will not hold — it free-texts the
# spoken phrase from a multi-day available_days still in its context. The write
# is the only place the two can be compared, so that is where it is checked.
_MONTH_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
# "Tuesday the 4th of August" and "Tuesday 4th August" both occur in her speech.
_SPOKEN_DATE_RE = re.compile(
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)?\s*"
    r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\s+(?:of\s+)?"
    r"(" + "|".join(_MONTH_NUM) + r")",
    re.IGNORECASE,
)
# Only sentences the caller is agreeing to. An availability list names several
# dates the caller never acted on; latching onto one of those would make this
# guard fire on correct bookings, and a guard that blocks real bookings is worse
# than the defect it protects against.
_SPOKEN_COMMITMENT_RE = re.compile(
    r"so that'?s|shall i go ahead|you'?re in for|all booked|book that in",
    re.IGNORECASE,
)


def _spoken_slot_date(text: str, year: int) -> Optional[str]:
    """The YYYY-MM-DD the caller was told, from a commitment sentence. Else None."""
    if not text or not _SPOKEN_COMMITMENT_RE.search(text):
        return None
    m = _SPOKEN_DATE_RE.search(text)
    if not m:
        return None
    try:
        return f"{year:04d}-{_MONTH_NUM[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    except (ValueError, KeyError):
        return None


# The slot itself, as spoken — "Wednesday the 5th of August at quarter past six
# in the evening" out of "So that's Sarah, Wednesday the 5th of August at quarter
# past six in the evening — shall I go ahead and book that in?".
#
# CA42486ff4 (31 Jul 2026): once name and phone were in, the booking readback told
# the model to fill the day "from the slot the caller already agreed to earlier in
# this conversation". That call had TWO agreements earlier in the conversation —
# Tuesday 6:30, then Wednesday 6:15 — so the instruction was ambiguous by
# construction and the model composed Tuesday's date with Wednesday's time, an
# appointment that existed on no calendar. Capturing the phrase means the readback
# can state the slot instead of asking the model to pick one.
#
# Bounded at the first dash, full stop or question mark so it cannot swallow the
# rest of the sentence ("— shall I go ahead and book that in?").
_SPOKEN_SLOT_PHRASE_RE = re.compile(
    r"((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
    r"(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(?:" + "|".join(_MONTH_NUM) + r")"
    r"(?:\s+at\s+[^—\-—.?!]{1,60})?)",
    re.IGNORECASE,
)


def _spoken_slot_phrase(text: str) -> Optional[str]:
    """The slot as it was said, from a commitment sentence. Else None."""
    if not text or not _SPOKEN_COMMITMENT_RE.search(text):
        return None
    m = _SPOKEN_SLOT_PHRASE_RE.search(text)
    if not m:
        return None
    phrase = " ".join(m.group(1).split()).strip(" ,;")
    # A phrase with no time is still useful (the day is the thing that goes
    # wrong), but an empty or absurd capture is not.
    return phrase if 8 <= len(phrase) <= 90 else None


def _phrase_date(phrase: str) -> Optional[str]:
    """The YYYY-MM-DD named inside an already-extracted slot phrase.

    Unlike _spoken_slot_date this does NOT require a commitment marker — the
    phrase has already been established as one. Used to tell a stale confirmed
    slot from a current one.
    """
    from datetime import datetime as _dt
    m = _SPOKEN_DATE_RE.search(phrase or "")
    if not m:
        return None
    try:
        return (
            f"{_dt.now().year:04d}-{_MONTH_NUM[m.group(2).lower()]:02d}-"
            f"{int(m.group(1)):02d}"
        )
    except (ValueError, KeyError):
        return None


def _note_spoken_slot_date(session: Dict[str, Any], spoken_text: str) -> None:
    """Record the slot last SPOKEN to the caller in a commitment sentence.

    Deliberately overwrites: after a change of mind the newest spoken slot is the
    one the caller agreed to, so a stale earlier one must never outlive it and
    block a legitimate booking. This is the property that lets the caller move
    Tuesday -> Wednesday and still get booked, and it is pinned by test.
    """
    from datetime import datetime as _dt
    date = _spoken_slot_date(spoken_text or "", _dt.now().year)
    if date:
        session["last_spoken_slot_date"] = date
        # Only alongside a parsed date, so the phrase and the date can never
        # describe different turns.
        phrase = _spoken_slot_phrase(spoken_text or "")
        if phrase:
            session["last_spoken_slot_phrase"] = phrase
            _refresh_confirmed_slot_phrase(session, phrase, date)


def _refresh_confirmed_slot_phrase(
    session: Dict[str, Any], phrase: str, date: str
) -> None:
    """Move v3_confirmed_slot_phrase onto the day the caller has just agreed to.

    v3_confirmed_slot_phrase is captured ONCE, at the name request
    (connection.py). A caller who changes day after that never refreshes it, so
    the Gate-5 date guard in turn_handler finds it naming an abandoned day and
    stands down — correctly, since it cannot tell which day is right, but it then
    stays stood down for the REST of the call (CA2c2f9b6a, 2 Aug 2026: the caller
    moved Thursday -> Friday and every readback from there on ran with the date
    guard blind). The guard that exists because CA5c4fb14f said "Tuesday the 4th"
    and booked the 5th must not disarm itself mid-call.

    Refreshing it re-arms the guard on the new day. The condition that makes this
    safe is the last one:

        the newly spoken day must be the day check_availability last OFFERED.

    Without it this would be circular — Gate 5 rewrites the spoken text, so a
    correction toward the stale phrase would come back here as "the caller agreed
    to the stale day" and confirm itself. v3_last_offered_day_iso comes from the
    tool result and nothing in the gate can touch it, which breaks the loop: a
    gate-rewritten date names the abandoned day, which by construction is not the
    day on offer, so it can never refresh anything.

    Never CREATES the key — an absent phrase means the slot flow has not reached
    the name request, and three other readers treat its presence as "a slot is
    locked" (connection.py surname straggler, clinic_template_prompt PHONE STEP
    OUTSTANDING, slot_followup's early return). This only ever moves an existing
    phrase forward.

    Silent no-op whenever it cannot be sure — no confirmed phrase, an unparseable
    one, no offered day recorded. Every one of those leaves today's behaviour
    exactly as it is.
    """
    conf = (session.get("v3_confirmed_slot_phrase") or "").strip()
    if not conf:
        return
    conf_date = _phrase_date(conf)
    if not conf_date or conf_date == date:
        return                      # nothing captured to move, or already current
    day_iso = str(session.get("v3_last_offered_day_iso") or "")
    if len(day_iso) < 10 or day_iso[5:10] != date[5:10]:
        # Compared on MM-DD for the same reason _confirmed_slot_is_stale does:
        # the spoken phrase carries no year, so a December call reading into
        # January would disagree on the year alone and skip a valid refresh.
        return
    session["v3_confirmed_slot_phrase"] = phrase
    # The phrase now names the day the caller has moved to, so it is no longer
    # superseded — re-arm the date guard on the new day.
    session.pop("v3_slot_phrase_superseded", None)
    logger.info(
        "[ms_llm] v3_confirmed_slot_phrase refreshed %r -> %r "
        "(caller moved to the day now on offer, %s)",
        conf, phrase, day_iso,
    )


# Explicit "I want a DIFFERENT DAY" signals (Bug B, 2026-07-30).
#
# Deliberately NARROWER than _NEW_SLOT_INTENT_WORDS below, which also matches any
# digit plus "no"/"not"/"slot"/"when". Those fire on ordinary turns — a caller
# correcting a phone number says digits — and the post-collect guard they would
# release exists to stop the model spuriously re-searching after name and phone
# are in (BUG-14). Widening that guard with a loose predicate trades one defect
# for another, so this set contains only words that refer to a specific calendar
# day and nothing else.
#
# Same-day TIME words (morning / later / earlier) are excluded on purpose: the
# V5 deterministic follow-up already serves remaining times from available_days,
# and re-fetching leads with the earliest slots again — the exact problem 368b4e0
# was written to fix. "may" is omitted for the same reason it is omitted below:
# it is a common auxiliary verb.
_DIFFERENT_DAY_WORDS: frozenset = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "tomorrow", "today", "tonight", "weekend",
})
_DIFFERENT_DAY_PHRASES = (
    "next week", "following week", "week after", "next month",
    "another day", "different day", "other day", "any other day",
    "another date", "different date",
)

# Time-of-day change on the SAME day. Only ever used to release the post-collect
# block so the V5 same-day path downstream can answer it — never to trigger a
# re-fetch.
_NEW_TIME_OF_DAY_WORDS: frozenset = frozenset({
    "morning", "afternoon", "evening", "midday", "noon",
    "earlier", "later", "sooner",
})


def _slot_date_disagrees_with_speech(args: Dict[str, Any], session: Dict[str, Any]) -> bool:
    """True when the slot about to be booked is on a different DAY from the one
    the caller was last told.

    Silent — returns False — whenever it cannot be sure:
      * no commitment sentence has been spoken yet (nothing to compare against);
      * slot_iso is missing or unparseable (other guards own that).
    A guard that blocks a real booking on a bad parse is worse than the defect it
    exists to prevent, so every uncertain case books.
    """
    spoken = session.get("last_spoken_slot_date")
    if not spoken:
        return False
    raw = str((args or {}).get("slot_iso") or "").strip()
    if len(raw) < 10:
        return False
    slot_date = raw[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", slot_date):
        return False
    return slot_date != spoken


_DIFFERENT_DAY_STEER = (
    "DIFFERENT DAY REQUESTED — the caller has just asked about a day other than "
    "the one you were discussing. The times earlier in this conversation are for "
    "the OLD day and say nothing about the new one. You do NOT know what is free "
    "on the day they asked for. Call check_availability for that day now, before "
    "you say anything about times. Do NOT offer, repeat or re-read any time "
    "already mentioned, do NOT tell them what you have 'already got', and NEVER "
    "attach the new day's name to a time that came from the old one. If they have "
    "already agreed a slot it is not cancelled — the caller is allowed to change "
    "their mind, so check the new day and let them choose."
)


def _different_day_steer(session: Dict[str, Any], messages) -> str:
    """The per-turn steer that pushes the model to the tool when the caller names
    a different day, or "" when it must stay silent.

    CAb81fe651 (30 Jul 2026): the caller asked for Wednesday four times and was
    served Tuesday every time, the fourth reply calling a Tuesday slot "Wednesday
    the 5th". He hung up unbooked.

    5b0c9c2 released the guards that used to block this, and they DO release —
    _caller_requests_different_day returns True on all four of his utterances,
    checked against the real transcripts. The model never called
    check_availability at all; it answered from the Tuesday slots still in its
    message history, which looked to it like good data. There was no block left
    to remove, so this pushes toward the tool instead.

    Silent unless BOTH hold:
      * the caller has just named a different day, and
      * there are older slots in context to answer from — with none, the model
        calls the tool unprompted and the steer would be noise.

    Self-suppressing: _check_av_ran_turn flips as soon as check_availability
    executes, so the steer is gone by the presentation pass and can never argue
    with the slots it just asked for.
    """
    if session.get("_check_av_ran_turn"):
        return ""
    if not (session.get("last_offered_slots") or session.get("available_days")):
        return ""
    if not _caller_requests_different_day(messages or [], session):
        return ""
    return _DIFFERENT_DAY_STEER


def _spoken_day_phrase(iso_date: str) -> str:
    """Render '2026-08-05' as 'Wednesday the 5th of August', or '' if unparseable.

    The write-guard's refusal is only actionable if it can name the day the slot
    is REALLY on. CAb81fe651 (30 Jul 2026): the guard fired correctly on a
    Wednesday slot, but its message only said "tell the caller the day you can
    actually offer" without saying what that day was — so the model repeated the
    Tuesday it had already spoken, the guard fired again on the same mismatch,
    and the caller hung up. Fail-closed turned a wrong-day booking into no
    booking, which is safer but still a lost patient.
    """
    from datetime import date as _date

    from app.tools.receptionist_tools import _ordinal

    try:
        d = _date.fromisoformat(str(iso_date or "")[:10])
    except (ValueError, TypeError):
        return ""
    return f"{d.strftime('%A')} the {_ordinal(d.day)} of {d.strftime('%B')}"


def _offered_day_dates(session: Optional[Dict[str, Any]]) -> tuple:
    """Every offered/agreed day as a real `date`, from the four keys that can
    carry one. Unparseable entries are dropped.

    Extracted so the name vocabulary below and the same-weekday proximity test
    read the SAME four keys. They disagreed once already: adding a source to one
    and not the other is how a guard silently stops covering a path.
    """
    if not session:
        return ()

    _isos: set = set()
    for _d in (session.get("available_days") or []):
        if isinstance(_d, dict) and _d.get("date"):
            _isos.add(str(_d["date"])[:10])
    for _s in (session.get("last_offered_slots") or []):
        if isinstance(_s, dict) and _s.get("start"):
            _isos.add(str(_s["start"])[:10])
    for _k in ("last_spoken_slot_date", "v3_last_presented_date_hint"):
        _v = str(session.get(_k) or "")[:10]
        if _v:
            _isos.add(_v)

    from datetime import date as _date

    _dates: list = []
    for _iso in _isos:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _iso):
            continue
        try:
            _dates.append(_date(int(_iso[:4]), int(_iso[5:7]), int(_iso[8:10])))
        except ValueError:
            continue
    return tuple(sorted(_dates))


def _offered_day_vocabulary(session: Optional[Dict[str, Any]]) -> frozenset:
    """The day-words that name a day ALREADY on the table — weekday and month
    names for every date the caller has been offered or has agreed to.

    Empty when nothing has been offered yet, or when session is None. An empty
    result means the caller cannot be accepting anything, so every caller in
    this module treats it as "cannot rule out a change request".

    NAMES ONLY, and that is the trap — see `_offered_weekday_is_within_reach`.
    """
    _vocab: set = set()
    for _dt in _offered_day_dates(session):
        _vocab.add(_dt.strftime("%A").lower())
        _vocab.add(_dt.strftime("%B").lower())
    return frozenset(_vocab)


# "the 22nd" -- a date written with a digit and an ordinal suffix. Unambiguous:
# a list position is spoken "number two", never "the 2nd", so any value 1-31
# counts here.
_DIGIT_DATE_ORDINAL_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_BARE_NUMBER_RE = re.compile(r"\b(\d{1,2})\b")


def _dates_of_month_the_caller_named(txt: str) -> set:
    """Every day-of-month `txt` refers to, as a set of ints.

    Two passes, because the two spellings carry different certainty.

    DIGITS ("the 22nd") are unambiguous and taken at any value. A numbered
    readout is spoken "number two", never "the 2nd", so nothing else in this
    system produces that shape.

    WORDS ("the twenty second") have to be folded to a number first, and the
    fold is lossy in the one place it matters: "the second one" -- a caller
    picking option 2 off a numbered list -- folds to exactly the same "2" that
    "the 2nd" does. So a folded WORD ordinal is only read as a date above
    `MAX_SPOKEN_OPTIONS`, which caps a readout at three options; 4 and up
    cannot be a position, and 1-3 are left to the day-word tests below rather
    than guessed at. "the twenty second" survives that cut; "the second one"
    does not, which is the point.

    Folding alone cannot find the word ordinals, because it also leaves
    ordinary numbers where they were -- "quarter past 6 works" folds to itself.
    A number that is bare in the FOLDED text but not bare in the RAW text is
    one the fold created, and that is exactly the set of word ordinals: a
    word boundary does not match inside "22nd", so a digit ordinal is never
    bare in the raw text.

    RESIDUAL, deliberately: a word ordinal of 1-3 with a month behind it --
    "the second of September" -- is still invisible here, and still reaches the
    month-name test that cannot separate it from the September already on the
    table. Narrower than the defect this closes, and not guessable without
    parsing the date properly, which is Tier 2 work.
    """
    from app.tools.slot_followup import _fold_ordinals, MAX_SPOKEN_OPTIONS

    txt = (txt or "").lower()
    found = {int(m) for m in _DIGIT_DATE_ORDINAL_RE.findall(txt)}

    _folded = _fold_ordinals(txt)
    _bare_raw = set(_BARE_NUMBER_RE.findall(txt))
    for _num in _BARE_NUMBER_RE.findall(_folded):
        if _num in _bare_raw:
            continue                     # was already a number -- not an ordinal
        if int(_num) > MAX_SPOKEN_OPTIONS:
            found.add(int(_num))
    return {d for d in found if 1 <= d <= 31}


def _caller_named_an_unoffered_date(txt: str, session) -> bool:
    """True when `txt` names a day-of-month that is on no day already offered.

    B-106, CA8c4efedbc7fbc2cc74ab47fcc834ecdf (JV go-live rehearsal, 27 Aug
    2026). Susie had offered Wednesday 2nd September. The caller asked:

        13:54:31  caller: "uh what about the 22nd"
        13:54:35  Susie:  "Wednesday 22nd September is fully booked, I'm
                           afraid -- the available slots for Wednesday 2nd
                           September are ..."

    The 22nd was never looked at. "the 22nd" carries no weekday and no month,
    so `_DIFFERENT_DAY_WORDS` -- weekday names, month names and the relative
    tokens, and nothing else -- matched nothing, the predicate returned False,
    and check_availability was refused as `already_retrieved`. That refusal
    hands the model the day it had ALREADY offered under a message that says
    "present the existing slots", and it reconciled the two by declaring the
    day it could not see to be full.

    A caller who is told a day is fully booked does not ask again. This is the
    quiet one: no error, no alert, no retry -- the call sounds completely
    normal and the patient is simply gone.

    Fires when the named date is on none of the offered days, which also
    catches the same-weekday form of CA166de2a9 ("wednesday the 22nd" while
    the 2nd is on the table) that the name-only test reads as an acceptance.
    With nothing offered yet there is nothing to be accepting, so a named date
    is a request -- the conservative direction this module takes everywhere.
    """
    _named = _dates_of_month_the_caller_named(txt)
    if not _named:
        return False
    _offered_days = {d.day for d in _offered_day_dates(session)}
    if not _offered_days:
        return True
    return not _named.issubset(_offered_days)


def _presentation_for_refusal(session, days):
    """Carry diary data on a REFUSAL in the shape a real lookup returns.

    B-118, CA14c0707a (28 Aug 2026, theorem_v3, build f2cc28dc -- B-116 and
    B-117 already live). The caller asked for the others on a day, B-98 opened
    the spent band, B-116 read the three UNHEARD times and B-117 explained why
    they were afternoons. All correct. One turn later the model called
    check_availability again, the guard refused it as `already_retrieved`, and
    that refusal handed back session["available_days"] -- the full seven -- with
    a sentence of English asking for "AT MOST 3 times, soonest first". The model
    read out all seven, mornings included, twenty seconds after Susie had said
    "I have given you all the mornings I have that day".

    A prose cap is not a cap. Worse, "soonest first" had become the WRONG
    instruction: after B-116 the soonest times on a spent-band day are exactly
    the ones the caller has already heard.

    So a refusal now carries `first_day`, chosen by the same selector a real
    lookup uses. available_days stays whole, exactly as `_cap_presented_slots`
    leaves it on a normal result, so `_resolve_slot_iso`, DTMF and the unspoken
    follow-up still see every bookable time and `_note_availability_seen` --
    which arms on result["available_days"] -- behaves identically.

    Never raises: a refusal must still refuse if the presentation helper is
    unavailable, and the fallback is the raw payload this replaced.
    """
    try:
        from app.tools.receptionist_tools import _cap_presented_slots
        if not isinstance(days, list) or not days:
            return {"available_days": days}
        return _cap_presented_slots({"available_days": days}, session)
    except Exception:
        return {"available_days": days}


def _note_availability_seen(session: Dict[str, Any], result: Any) -> bool:
    """Record that a check_availability result carried real slots. Returns that
    same fact, which the caller keeps as the per-turn `_check_av_had_slots`.

    `_slots_offered_this_call` is call-scoped and never cleared — unlike the
    per-turn flags beside it, and unlike `last_offered_slots`, which is wiped at
    the top of every turn. `_resolve_slot_iso` needs to tell two states apart
    that look identical once that cache is gone (CA166de2a9):

        this call has read the diary, so an ISO matching nothing is a fabrication
        no lookup ever ran, so the ISO is all there is and always was

    Only a result carrying `available_days` arms it. Seven checks in that call
    were BLOCKED and came back as the booking-details-already-complete error; a
    guard's refusal is not a reading of the diary, and treating it as one would
    make a clinic that never got a real lookup start refusing direct bookings.

    (Spelling that error's key out verbatim here would break
    test_blocked_tool_forces_text, which scans this file for the FIRST occurrence
    of the literal and checks the force-text flag sits beside it.)

    Extracted rather than left inline for the reason `_post_collect_readback_due`
    was: a guard only reachable through a 15k-line method is a guard whose tests
    pass when it is deleted.
    """
    _had = bool(isinstance(result, dict) and result.get("available_days"))
    if _had:
        session["_slots_offered_this_call"] = True
    return _had


_WEEKDAY_WORDS: frozenset = frozenset({
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
})


def _offered_weekday_is_within_reach(
    session: Optional[Dict[str, Any]], word: str, today
) -> bool:
    """True when saying `word` can only mean the day already offered.

    CA166de2a9 (Theorem, 2026-08-10): Susie offered **Wednesday 19 August** —
    the 12th had no afternoon left, so the tool skipped it. The caller asked
    "are you free earlier… on a wednesday". "wednesday" was in the offered
    vocabulary, so `_caller_requests_different_day` read it as him ACCEPTING the
    day on the table, the steer stayed silent, and the model never called the
    tool. It answered from the 19th's slots still in its message history and
    spoke them as "Wednesday the 12th — two, three and four in the afternoon".
    Those three times existed on no calendar. Four bookings 400'd out of Acuity,
    four manual-followup alerts went to the owner, and the caller only got
    booked because he suggested Thursday himself — a different weekday WORD,
    which is the only reason the steer finally fired.

    Weekday names repeat every 7 days. A name identifies a day only while no
    OTHER date of that weekday sits between today and the one offered, so that
    is exactly the test: the offered date must be within a week.

        offered 19 Aug (Wed), today 10 Aug   → 9 days → Wed 12th is unoffered
                                               and nearer: AMBIGUOUS, fire.
        offered 15 Aug (Sat), today 8 Aug    → 7 days → no Saturday in between:
                                               he is accepting (CAb297555c).

    Seven is the boundary and must stay inclusive: it is CAb297555c exactly, the
    call this suppression was built for.

    Past dates do not count. A day that has been and gone cannot be the one the
    caller is accepting, and treating it as reachable would suppress the steer
    on the stalest state in the session.

    ── "next week" moves the lower bound (CA403eb7e2, 31 Aug 2026) ───────────
    Seven days is measured from TODAY, and that is only the right floor while
    every intervening date of that weekday was a candidate. It is not when the
    caller has scoped themselves further out. He said "anytime next week", was
    offered Monday 7th and Tuesday 8th September, and answered "9 in the morning
    on a tuesday" — option 2, with its own time. Today was Monday the 31st, so
    the offered Tuesday was 8 days away: one over the boundary. The nearer
    Tuesday, 1 September, went unoffered precisely BECAUSE he asked for next
    week, so it was never a thing he could have meant. The steer fired, the
    model re-ran the lookup, and he was read the whole day back and had to say
    "yeah I said 9 in the morning works".

    So a captured `day_preference` of "next week" raises the floor to next
    Monday, and the offered date qualifies only if it is the FIRST date of that
    weekday on or after it. Deliberately narrow:

      * ONLY "next week". The other captured values ("this week", "tomorrow",
        "as soon as possible", "today", "tonight", "whenever") all point at or
        before the current week and so cannot explain away a nearer date.
      * FIRST occurrence only, never "anything from next Monday on" — with
        Tuesday the 8th unoffered, Tuesday the 15th is exactly as ambiguous as
        before and must stay so.
      * Earned by the CALLER's own words. With no day_preference the delta-8
        case is unchanged, so a session that never captured one is not silently
        loosened.

    The shift words ("next tuesday", "the tuesday after") are tested BEFORE this
    in `_caller_requests_different_day` and still win, so a caller moving a week
    on is never read as accepting.
    """
    for _dt in _offered_day_dates(session):
        if _dt.strftime("%A").lower() != word:
            continue
        _delta = (_dt - today).days
        if 0 <= _delta <= 7:
            return True
        if _next_week_pins_the_weekday(session, _dt, word, today):
            return True
    return False


def _next_week_pins_the_weekday(
    session: Optional[Dict[str, Any]], offered, word: str, today
) -> bool:
    """True when the caller's own "next week" makes `offered` the only date that
    `word` can mean. PURE. See `_offered_weekday_is_within_reach` for the call
    it comes from and why it is scoped this tightly.
    """
    if not session or session.get("day_preference") != "next week":
        return False
    from datetime import timedelta as _td

    try:
        # Monday of next week — from Sunday that is tomorrow, from Monday a
        # full seven days. Never today: "next week" excludes the current one.
        _floor = today + _td(days=7 - today.weekday())
        _wanted = [
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        ].index(word)
    except (ValueError, TypeError, AttributeError):
        return False
    _first = _floor + _td(days=(_wanted - _floor.weekday()) % 7)
    return offered == _first


# Words that mark a SHIFT away from the day under discussion even when the
# weekday named is the same one ("the saturday after", "next saturday"). Their
# presence blocks the acceptance suppression below, so a caller asking for a
# different week is never mistaken for one accepting this week.
_DAY_SHIFT_WORDS: frozenset = frozenset({
    "next", "after", "following", "another", "different", "instead",
    "rather", "else", "other",
})


def _followup_must_yield_to_a_real_lookup(
    session: Optional[Dict[str, Any]], messages
) -> bool:
    """True when the session cannot honestly answer "what else that day?".

    Two things have to hold. The band hid times on the day now on the table --
    so available_days is not the day, and every subtraction below it runs out
    of survivors while a bookable appointment sits behind the filter (B-99).
    And the caller is asking for MORE times rather than taking one: an
    acceptance must still be intercepted, because letting "that works for me"
    reach a real lookup is CAce1457d1, where the caller had to accept twice.

    Never raises: a session this cannot read keeps the guard's existing
    behaviour rather than opening it.
    """
    try:
        from app.tools.slot_followup import (
            offer_day_hides_times,
            utterance_accepts_offered_slot,
        )
        if not offer_day_hides_times(session or {}):
            return False
        if utterance_accepts_offered_slot(_last_user_text(messages or [])):
            return False
        return True
    except Exception:
        return False


def _caller_requests_different_day(
    messages, session: Optional[Dict[str, Any]] = None, today=None
) -> bool:
    """True if the caller's latest utterance names a DIFFERENT calendar day.

    `today` is the clinic's date, defaulted from the one shared zoned clock and
    injectable so the suppression can be tested without the calendar deciding
    the result. Never `date.today()` — that is server-local, and B-09 is what
    that costs between 23:00 and midnight under BST.

    CAc6b971ad (30 Jul 2026): the caller asked for Wednesday seven times after
    giving name and phone. Each time the post-collect guard blocked
    check_availability and instructed the model to repeat the Tuesday
    confirmation verbatim, so she twice said "let me check Wednesday" and then
    re-read Tuesday back. He hung up without booking.

    ── Naming the day you are ACCEPTING (CAb297555c, 8 Aug 2026) ─────────────
    Susie offered "Saturday 15th August — eleven in the morning or midday". The
    caller said "yeah 11 in the morning works for saturday" and this returned
    True, because "saturday" is a weekday word and nothing here compared it
    against the day already on the table. The steer fired, the model re-ran
    check_availability for the day it had just offered, and the turn took 5.3s
    against a ~2.3s baseline.

    Accepting a day and requesting one are word-identical. The only thing that
    separates them is what has already been offered, so `session` is now read.

    ── Which way this fails ─────────────────────────────────────────────────
    Deliberately asymmetric, because the two errors are not equally bad:

      False negative — silent here when the caller DID want another day. That
      is CAb81fe651: Wednesday asked four times, Tuesday served every time,
      hung up unbooked.
      False positive — fires on an acceptance. One wasted tool round trip,
      ~3s of latency, and the caller still gets the right answer.

    So suppression requires certainty on every count: a change PHRASE anywhere
    ("next week", "another day") still returns True; a shift word still returns
    True; an unresolvable or relative token ("tomorrow", "weekend" — no clock
    here) still returns True; and every concrete day-word the caller used must
    name a day already offered. Anything else fires.

    `session` defaults to None, which reproduces the old behaviour exactly, so
    a call site that has not been threaded stays conservative rather than
    silently gaining the suppression.
    """
    # An affirmative inherits the question it answers. Everything below reads
    # only the caller's words, and cannot see a day that SUSIE named -- so a
    # plain "yeah go for it" to her own "shall I look at a different day?" read
    # as no request at all and the guard held (Finding 2, CA890b511e 08:42:53).
    # Checked first because such a reply carries no day word to compare.
    if _answering_susies_different_day_offer(messages, session):
        logger.info(
            "[ms_llm] caller said yes to Susie's own offer of a different day "
            "— treating as a different-day request (Finding 2)",
        )
        return True

    txt = _last_user_text(messages).lower()
    if not txt:
        return False
    if any(p in txt for p in _DIFFERENT_DAY_PHRASES):
        return True

    # A bare day-of-month is a day-word the vocabulary below cannot see, and
    # "uh what about the 22nd" carries neither a weekday nor a month -- so the
    # day-word test matched nothing and this returned False three lines down.
    # check_availability was then refused as already_retrieved, and the model,
    # holding only the day it had already offered, announced that the day it
    # had never looked at was fully booked (B-106, CA8c4efedb).
    if _caller_named_an_unoffered_date(txt, session):
        logger.info(
            "[ms_llm] caller named a date that is on no offered day "
            "-- treating as a different-day request (B-106)",
        )
        return True

    _named = set(re.findall(r"[a-z']+", txt)) & _DIFFERENT_DAY_WORDS
    if not _named:
        return False

    _offered = _offered_day_vocabulary(session)
    if not _offered:
        return True
    if set(re.findall(r"[a-z']+", txt)) & _DAY_SHIFT_WORDS:
        return True
    if not _named.issubset(_offered):
        return True

    # Every day-word names something on the table BY NAME. For a weekday that is
    # not enough — the name repeats weekly, so it only pins the offered day when
    # no nearer date of that weekday exists. CA166de2a9 is what the name-only
    # test let through; see _offered_weekday_is_within_reach.
    #
    # Month words keep the name test: "august" cannot be the wrong August the
    # way "wednesday" can be the wrong Wednesday, and the relative tokens
    # ("tomorrow", "weekend") never enter the vocabulary at all, so both reach
    # here already decided.
    if today is None:
        from app.date_context import clinic_today as _clinic_today
        today = _clinic_today()
    for _w in (_named & _WEEKDAY_WORDS):
        if not _offered_weekday_is_within_reach(session, _w, today):
            return True

    # Accepting, not asking.
    return False


def _caller_requests_new_day_or_time(messages, session: Optional[Dict[str, Any]] = None) -> bool:
    """A different day OR a different time of day — i.e. the caller is still
    choosing when to come in, so the post-collect guard must stand down."""
    if _caller_requests_different_day(messages, session):
        return True
    txt = _last_user_text(messages).lower()
    if not txt:
        return False
    return bool(set(re.findall(r"[a-z']+", txt)) & _NEW_TIME_OF_DAY_WORDS)


def _second_filler_text(
    session, first_text: str, got_first_chunk: bool
) -> "str | None":
    """Text for the re-armed (second) filler, or None if it must not play.

    B-19: the background filler was one-shot — it fired once at
    LLM_FIRST_CHUNK_TIMEOUT_MS and the task ended, so an upstream stall past
    that point was bare silence (measured: a 14s spike gave one phrase at 1.8s
    and ~12s of nothing).

    Three reasons NOT to speak again, in order:

    1. `got_first_chunk` — the LLM answered during the wait. Belt and braces:
       the first token also cancels the whole task, so this is the race guard.
    2. `_ack_filler_active` is False — a tool-call filler took over
       (`filler_phrases.with_filler` clears it) and is already speaking.
       Deliberately NOT `_ack_filler_cancelled`: `_tts_loop` *consumes* that
       flag, so it reads False whether or not a tool filler won.
    3. Never a verbatim repeat, and never a second write-ack — the first phrase
       may have been "Just locking that in now…", and saying it twice claims
       the write twice to a caller who has already confirmed.
    """
    if got_first_chunk:
        return None
    if not session.get("_ack_filler_active"):
        return None
    pool = [p for p in FILLER_PHRASES if p != first_text] or list(FILLER_PHRASES)
    return random.choice(pool)


# ── A refusal is not a reading of the diary ─────────────────────────────────
# The guards below refuse check_availability without consulting Acuity at all.
# The model receives that refusal in the same slot a real availability result
# would occupy, and on CA166de2a9 it narrated the refusal as clinic state:
#
#   15:02:15  "it looks like Wednesday afternoon has filled up"
#   15:02:33  "that slot doesn't seem to be available any more"
#
# Neither was true, and neither came from Mark's calendar — both were the model
# explaining a block to the caller. This is B-58's rule: a guard must never let
# the model state world state it was not told. The clause is appended to the
# refusals that carry NO diary data. `already_retrieved` deliberately does not
# get it — that one returns available_days, so a claim about availability made
# from it is grounded.
_NOT_AVAILABILITY_NEWS = (
    " This is an internal instruction, NOT a reading of the diary: nothing has "
    "been checked. Do NOT tell the caller that a day, time or slot is full, "
    "taken, gone, unavailable or no longer free — you have not been told that "
    "and it may be untrue."
)


def _post_collect_readback_due(tool_name: str, session, messages) -> bool:
    """True when check_availability must be blocked in favour of the booking
    read-back: the caller's details are settled and nobody is trying to change
    the slot.

    B-46 (2026-08-03) — read `phone_confirmed`, NEVER `collected["phone"]`.

    `collected["phone"]` is pre-loaded from the Twilio caller-ID at connect
    (connection.py, "Populate collected.phone from Twilio caller-ID so Susie
    never asks for it"), so on every inbound call carrying a number it is
    truthy from turn one. Testing it collapsed this predicate to "a name has
    been collected" — and under name-first the first name is stored at turn 1,
    so the read-back was forced BEFORE any slot had been offered, skipping the
    surname and phone-confirmation steps.

    `phone_confirmed` is set only where the caller actively confirms a number
    (the keypad commit and the two verbal-confirm sites in connection.py), and
    book_appointment's A1 gate already requires it. So "the caller has confirmed
    their number" and "the slot is already agreed" are the same moment, which is
    the moment this guard was always meant to fire at.

    Extracted from the inline condition so it can be tested without standing up
    a connection — the same reason B-38 extracted `_cta_asked`. A guard that is
    only reachable through a 15k-line method is a guard whose tests pass when it
    is deleted.
    """
    if tool_name != "check_availability":
        return False
    # CA166de2a9 (2026-08-10): "the caller's details are settled and nobody is
    # trying to change the slot" stops being true the moment the provider
    # rejects the slot. This guard fired seven times after the first Acuity 400,
    # each time telling the model not to ask about the day or time again — so
    # the one action that could recover the call was the one it forbade. Four
    # minutes, four failed writes, four owner alerts, and the caller had to
    # suggest a different day himself.
    if session.get(BOOKING_WRITE_FAILED_KEY):
        return False
    if not session.get("phone_confirmed"):
        return False
    _col = session.get("collected") or {}
    if not (_col.get("name") or _col.get("full_name")):
        return False
    # Bug B (2026-07-30): "the slot is already agreed" is only true while
    # nobody is trying to change it. CAc6b971ad hung up unbooked after asking
    # for Wednesday seven times behind this guard.
    return not _caller_requests_new_day_or_time(messages or [], session)


def _caller_wants_new_slot(messages) -> bool:
    """True if the caller's latest utterance signals they want a different slot
    (a new-date word or any digit) — i.e. a legitimate reason to re-search
    availability even though a slot is already confirmed."""
    txt = _last_user_text(messages).lower()
    if not txt:
        return False
    if any(ch.isdigit() for ch in txt):
        return True
    words = set(re.findall(r"[a-z']+", txt))
    return bool(words & _NEW_SLOT_INTENT_WORDS)


# Every gated WRITE tool, mapped to the phrase family its confirmation belongs
# to. A "write" here is a tool that mutates the clinic's calendar — the three
# operations a caller can be told happened when it did not.
_WRITE_TOOL_FAMILIES = {
    "book_appointment":       WRITE_FAMILY_BOOKING,
    "reschedule_appointment": WRITE_FAMILY_RESCHEDULE,
    "cancel_appointment":     WRITE_FAMILY_CANCEL,
}

# B-36 cause 2d — the model is told, per family, that the write did NOT happen.
# Before 2026-08-03 only the booking rule existed, so on a blocked reschedule or
# cancellation the model had an instruction to ask a question and no rule at all
# against announcing success. On CA23199d089 it announced success: the caller was
# told their appointment had moved and it had not.
#
# Each rule constrains what Susie may SAY. It must not describe the state of the
# world, because the only thing this code knows is that *this attempt* was
# refused — not what the calendar holds. The cancel rule used to end "Their
# original appointment still stands", and on CA0f9a12 that sentence was read out
# of a refused duplicate cancel *after a real one had already succeeded*: the
# caller had cancelled, said "thank you bye", the model fired cancel_appointment
# one more time on the farewell turn, and the guard handed it a false statement
# to narrate. He hung up 150 ms into the apology and heard nothing — timing, not
# a safety net. A rule that only ever narrows what may be asserted cannot do
# that, and cannot suppress a genuine refusal either.
_WRITE_NO_CLAIM_RULE = {
    WRITE_FAMILY_BOOKING: (
        "This booking attempt did not go through. Do not claim this attempt "
        "succeeded — do not tell the caller they are booked, confirmed, or all "
        "set. Do not tell the caller anything about the state of their "
        "appointments that you have not been told. Ask the outstanding "
        "question, and only state a booking once book_appointment returns "
        "success."
    ),
    WRITE_FAMILY_RESCHEDULE: (
        "This reschedule attempt did not go through. Do not claim this attempt "
        "succeeded — do not tell the caller it has been rescheduled, moved, "
        "changed or sorted, and do not state the new day or time as if it were "
        "settled. Do not tell the caller anything about the state of their "
        "appointments that you have not been told, in particular do not say "
        "their original appointment still stands. Ask the outstanding "
        "question, and only state a move once reschedule_appointment returns "
        "success."
    ),
    WRITE_FAMILY_CANCEL: (
        "This cancellation attempt did not go through. Do not claim this "
        "attempt succeeded — do not tell the caller it has been cancelled and "
        "do not imply the slot has been given up. Do not tell the caller "
        "anything about the state of their appointments that you have not been "
        "told, in particular do not say their original appointment still "
        "stands. Ask the outstanding question, and only state a cancellation "
        "once cancel_appointment returns success."
    ),
}

# Layer 2 — what the model is told instead when the refused write belongs to a
# family that ALREADY succeeded on this call. Arming the no-claim rule there is
# wrong twice over: the attempt is a duplicate of work that is done, and the
# caller is usually mid-farewell. Nothing here asserts calendar state either —
# a caller with two appointments may legitimately be part-way through the
# second — it says only what this code actually knows, and tells the model that
# a goodbye deserves a goodbye.
#
# The latch itself is WRITE_SUCCEEDED_KEY, defined in turn_handler beside
# WRITE_REFUSED_KEY and imported above — Gate 5f reads it too (B-75).

# B-62 — the wall clock of the move that actually succeeded this call.
#
# Kept beside WRITE_SUCCEEDED_KEY rather than inside it because
# test_b58_duplicate_write_after_success asserts that key's values are literally
# True, and because "did this family succeed" and "which slot did it write" are
# different questions with different lifetimes.
RESCHEDULE_SUCCEEDED_SLOT_KEY = "_reschedule_succeeded_slot"


def _slot_wall_clock(slot_iso: Optional[str]) -> Optional[str]:
    """'2026-08-19T18:15:00+01:00' -> '2026-08-19T18:15'. None when unusable.

    Offset-insensitive deliberately: the model passes `new_slot_iso` both with
    and without an offset for the same slot — both shapes appear on
    CA6bd7fb424a72246a38671d4690913850 — and every clinic slot is local wall
    time. The wall clock is also what the caller actually heard, which is the
    thing this comparison is protecting.

    Returns None rather than guessing on anything malformed, and None means
    "cannot tell", which the caller of this function must treat as NOT a
    duplicate. See _refusal_is_a_genuine_duplicate.
    """
    if not isinstance(slot_iso, str):
        return None
    s = slot_iso.strip()
    if len(s) < 16 or s[10] not in ("T", " "):
        return None
    if not (s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()):
        return None
    return s[:16]


def _refusal_is_a_genuine_duplicate(
    session: dict, family: str, result: dict
) -> bool:
    """True when a refused write really is a repeat of one that already landed.

    B-62 (P1), JV CA6bd7fb424a72246a38671d4690913850, 19 Aug 2026. A caller
    moved his appointment to 17:30, was confused into restarting the
    reschedule (B-61), and the second move — to 18:15 — was refused. Layer 2b
    asked only "has the reschedule family succeeded this call?", said yes, and
    so did not arm the false-confirmation guard. Susie announced the refused
    move as done, at the new time, and promised a text that was never sent.
    The caller left believing quarter past six; the diary said half past five.

    The family-level test is right for a genuine repeat (CA0f9a12) and wrong the
    moment the second write names a different target. So compare targets.

    **Fails safe.** When either slot is unknown the answer is False — not a
    duplicate — so the guard arms. A spurious arm costs a stripped sentence and
    a re-ask; a missed one costs a caller walking away with the wrong time.

    Scoped to reschedule on purpose. Booking and cancel keep the family-level
    behaviour: cancel's success payload carries no appointment id at all (see
    the note above WRITE_SUCCEEDED_KEY), so widening this needs executor changes
    and its own evidence. Returning True for them leaves them exactly as they
    were.
    """
    if family != WRITE_FAMILY_RESCHEDULE:
        return True
    succeeded_slot = session.get(RESCHEDULE_SUCCEEDED_SLOT_KEY)
    refused_slot = _slot_wall_clock((result or {}).get("attempted_slot_iso"))
    if not succeeded_slot or not refused_slot:
        return False
    return succeeded_slot == refused_slot

# Call-scoped: book_appointment reached the provider and came back failed.
#
# Deliberately NOT WRITE_REFUSED_KEY, which is turn-scoped and covers all three
# families and both refusal kinds. This one answers a narrower question that two
# guards need — "is the slot on record still worth defending?" — and the answer
# has to outlive the turn, because on CA166de2a9 the block that would not stand
# down fired across seven separate turns after the first failure.
BOOKING_WRITE_FAILED_KEY = "_booking_write_failed"

_WRITE_ALREADY_DONE_RULE = {
    WRITE_FAMILY_BOOKING: (
        "A booking already completed successfully earlier on this call. This "
        "further attempt did not go through and does not undo it. Do not "
        "apologise, do not tell the caller anything failed, and do not tell "
        "them anything about the state of their appointments that you have not "
        "been told. If they are saying goodbye, simply say goodbye."
    ),
    WRITE_FAMILY_RESCHEDULE: (
        "A reschedule already completed successfully earlier on this call. "
        "This further attempt did not go through and does not undo it. Do not "
        "apologise, do not tell the caller anything failed, and do not tell "
        "them anything about the state of their appointments that you have not "
        "been told. If they are saying goodbye, simply say goodbye."
    ),
    WRITE_FAMILY_CANCEL: (
        "A cancellation already completed successfully earlier on this call. "
        "This further attempt did not go through and does not undo it. Do not "
        "apologise, do not tell the caller anything failed, and do not tell "
        "them anything about the state of their appointments that you have not "
        "been told. If they are saying goodbye, simply say goodbye."
    ),
}


def _booking_outcome_line(result: dict) -> str:
    """A caller-ready sentence describing a booking that HAS just been written.

    Built from the tool result and nothing else. That is the whole point: when
    the turn that should announce the booking produces no speech, the outcome is
    still known deterministically, and saying the true thing beats saying the
    deaf thing.

    On CAd8868396 (Vital Edge, 2026-08-11) `book_appointment` returned
    `{"success": true, "provisional": true, "booked_slot": "Tuesday 18 August at
    12:00", "note": "…Tell the caller it is NOT confirmed…"}`. The very next
    iteration stalled 21s on the provider, retried, and emitted nothing — so the
    caller heard "Sorry, I didn't quite catch that — could you say that again?",
    said goodbye, and hung up. The request was in Jonathan's diary and they were
    never told. The call was then recorded as `abandoned`, because nothing had
    been spoken for the summariser to see.

    PROVISIONAL IS NOT CONFIRMED, and the distinction is the reason this returns
    two different sentences rather than one. Telling a Vital Edge caller they
    are "booked in" when the practitioner has not accepted yet is a false
    confirmation — the exact failure the whole write-guard family exists to
    prevent. Neither sentence promises a text: SMS is env-gated per service and
    a promise made here cannot check it.

    Returns "" when the result carries no slot to speak, so the caller gets the
    ordinary fallback rather than a sentence with a hole in it.
    """
    if not isinstance(result, dict) or result.get("success") is not True:
        return ""
    slot = str(result.get("booked_slot") or "").strip()
    if not slot:
        return ""
    if result.get("provisional") is True:
        return (
            f"Right — I've put that request through for {slot}. "
            "It's not confirmed just yet; the practitioner will confirm it "
            "with you shortly."
        )
    return f"That's booked in — you're down for {slot}."


def _reschedule_outcome_line(result: dict) -> str:
    """A caller-ready sentence describing a move that HAS just been written.

    The reschedule twin of `_booking_outcome_line`, and it exists for the same
    reason that one does. JV CA9262659c (21 Aug): reschedule_appointment
    returned success at 19:59:30, the confirmation the model generated for it
    was discarded 2.4s later as a stale chunk after a barge-in
    ("tts_inhibit: discarding stale chunk 'That's you rescheduled - you're now
    in for Friday the 28th o'"), the turn ended having emitted nothing, and the
    caller was left asking "have you rescheduled it then".

    Booking had this backstop; reschedule and cancel never did, because the
    latch that feeds it was written only under `family == WRITE_FAMILY_BOOKING`
    - the same booking-only scoping that caused B-75 one function away.

    PROVISIONAL IS NOT CONFIRMED. A provisional clinic has REQUESTED the move,
    not made it, so this returns two different sentences exactly as the booking
    twin does. `_exec_reschedule_appointment` now reports which it was.

    Returns "" when the result carries no slot to speak, so the caller gets the
    ordinary fallback rather than a sentence with a hole in it.
    """
    if not isinstance(result, dict) or result.get("success") is not True:
        return ""
    slot = str(result.get("rescheduled_to") or "").strip()
    if not slot:
        return ""
    if result.get("provisional") is True:
        return (
            f"Right - I've put that change through for {slot}. "
            "It's not confirmed just yet; the practitioner will confirm it "
            "with you shortly."
        )
    return f"That's you rescheduled - you're now in for {slot}."


def _note_write_result(session: dict, tool_name: str, result):
    """P1 #5 / F-023 / B-36 — Layers 1 & 2 of the false-confirmation guard.

    Called once per tool result, for every write tool, from the single funnel in
    `_run_tools` — which sees BOTH the gate-branch refusals constructed above it
    (`*_required`, no `success` key at all) and the executor's own results.

    Layer 1: when the write actually SUCCEEDS, record it — per family in
    `WRITE_SUCCEEDED_KEY`, and for booking additionally as
    `booking_write_confirmed`, the call-scoped "a real booking exists" signal
    Gate 5f reads.

    Layer 2: when the write is blocked or fails, attach an explicit rule to the
    tool_result the model reads, forbidding a success claim for THAT family.
    Steering only — it fires on the already-failed path, so it can never suppress
    a real write.

    Layer 2b (CA0f9a12): if the refused family already succeeded earlier in the
    call, the refusal is a duplicate, not a failure. The gate is not armed and
    the model is given `_WRITE_ALREADY_DONE_RULE` instead of a no-claim rule, so
    a farewell turn that re-fires the write ends in a goodbye rather than an
    apology for something that did not fail.

    Polarity is deliberate: the refusal branch is `not (success is True)`, not
    `success is False`. Every gate refusal in `_run_tools` returns
    `{"status": "..._required", "message": ...}` with **no success key**, so a
    `success is False` test would catch none of them. All three write executors
    set `success: True` on their success path, so "not True" is a safe refusal
    test and it fails closed.

    Reschedule and cancel were out of scope until 2026-08-03 — the docstring here
    said so, and said why ("a different phrase family"). CA23199d089 falsified it:
    a refused reschedule was narrated as done. See docs/plan/REGISTER_B_U.md B-36
    cause 2.
    """
    family = _WRITE_TOOL_FAMILIES.get(tool_name)
    if family is None or not isinstance(result, dict):
        return result
    refused = session.get(WRITE_REFUSED_KEY)
    if not isinstance(refused, dict):
        refused = {}
    succeeded = session.get(WRITE_SUCCEEDED_KEY)
    if not isinstance(succeeded, dict):
        succeeded = {}
    if result.get("success") is True:
        # A retry SUCCEEDED later in the same turn. The tool loop can run a
        # write up to MAX_TOOL_ITERATIONS times (CA7e389a47 did three), and the
        # observed B-36 call ran lookup_patient -> reschedule_appointment ->
        # speech all within one turn. Leaving the marker set here would arm
        # Gate 5f against the turn's own LEGITIMATE confirmation — the exact
        # over-fire that abandoned a completed booking on 2026-06-12.
        #
        # CA3b303f: clear the reschedule/cancel lookup purpose so a later
        # legitimate book_appointment in the same call is not blocked.
        from app.tools.receptionist_tools import LOOKUP_PURPOSE_KEY
        session.pop(LOOKUP_PURPOSE_KEY, None)
        if refused.pop(family, None):
            logger.info(
                "[ms_llm] %s succeeded after an earlier refusal this turn — "
                "false-confirmation guard disarmed for the %s family",
                tool_name, family,
            )
        session[WRITE_REFUSED_KEY] = refused
        succeeded[family] = True
        session[WRITE_SUCCEEDED_KEY] = succeeded
        # B-62: remember WHICH slot landed, so a later refusal can be told apart
        # from a repeat of this one. Taken from the target that was requested —
        # the funnel attaches it at the call site — because the two reschedule
        # executors return differently-shaped success payloads and neither
        # carries an ISO.
        if family == WRITE_FAMILY_RESCHEDULE:
            session[RESCHEDULE_SUCCEEDED_SLOT_KEY] = _slot_wall_clock(
                result.get("attempted_slot_iso")
            )
            # B-75c: the same deferred backstop booking has had since
            # CAd8868396. On CA9262659c this move landed and the confirmation
            # the model wrote for it was thrown away as a stale chunk after a
            # barge-in, so the turn emitted nothing and the caller had to ask
            # "have you rescheduled it then". Shares the booking key: it holds
            # "the outcome sentence this turn owes the caller", whichever write
            # produced it, and is popped and cleared exactly as before.
            _r_line = _reschedule_outcome_line(result)
            if _r_line:
                session["_booking_outcome_unspoken"] = _r_line
        if family == WRITE_FAMILY_CANCEL:
            # B-65: remember WHICH appointment went, so a later cancel in the
            # same call can tell "do it again" from "delete a different one".
            # Recorded only when the executor actually reports an id - an empty
            # value leaves the guard disarmed, which is today behaviour.
            _cid = str((result or {}).get("cancelled_appointment_id") or "").strip()
            if _cid:
                session[CANCEL_SUCCEEDED_ID_KEY] = _cid
        if family == WRITE_FAMILY_BOOKING:
            session["booking_write_confirmed"] = True
            # A real booking now exists, so the availability blocks are correct
            # again — the caller is not choosing a time any more. Cleared rather
            # than left latched so a farewell turn cannot spend a round trip
            # re-reading the diary.
            session.pop(BOOKING_WRITE_FAILED_KEY, None)
            # Keep a caller-ready sentence describing what just happened, built
            # from the TOOL RESULT rather than from anything the model says.
            # See _booking_outcome_line: this is what the deferred Gate-5
            # fallback speaks when the confirmation turn produces nothing.
            _line = _booking_outcome_line(result)
            if _line:
                session["_booking_outcome_unspoken"] = _line
        return result
    if succeeded.get(family) and not _refusal_is_a_genuine_duplicate(
        session, family, result
    ):
        # B-62 — the family succeeded, but THIS refusal named a different slot
        # (or one we cannot identify). Calling that a duplicate is what let
        # "you're now in for quarter past six" reach a caller whose diary said
        # half past five. Fall through and arm the guard, exactly as a refusal
        # with no prior success would.
        logger.warning(
            "[ms_llm] %s refused for a DIFFERENT target than the move that "
            "succeeded this call (succeeded=%r refused=%r) — NOT a duplicate, "
            "arming the false-confirmation guard",
            tool_name,
            session.get(RESCHEDULE_SUCCEEDED_SLOT_KEY),
            _slot_wall_clock((result or {}).get("attempted_slot_iso")),
        )
    elif succeeded.get(family):
        # CA0f9a12 — a duplicate write in a family that already completed this
        # call. Arming here is wrong: Gate 5f would strip the turn's speech on
        # the strength of an attempt that changed nothing, and the no-claim rule
        # would hand the model a sentence about the caller's calendar that this
        # code has no basis for. Note what is NOT done here — the marker is not
        # set, so a refusal that follows a success cannot re-arm the gate for
        # the rest of the turn.
        logger.info(
            "[ms_llm] %s did not succeed (status=%r) but the %s family already "
            "completed this call — guard NOT armed, duplicate-write rule "
            "attached instead",
            tool_name, result.get("status") or result.get("error"), family,
        )
        result = dict(result)
        # B-65, JV CA44046f96321b, 20 Aug 2026. This rule used to be attached
        # ALONGSIDE whatever the refusal already said, and that was the whole
        # defect. A duplicate cancel is refused for lack of consent, and that
        # refusal message reads "cancel_appointment cannot fire yet. Ask for
        # consent..." - stating, in the same payload as the rule below, that the
        # write has NOT happened.
        #
        # The model obeyed the message. Having just cancelled the appointment
        # successfully and said so, Susie apologised - "I actually need to
        # complete the cancellation properly" - for a cancellation that had
        # already gone through, texted the caller and alerted the owner. The
        # caller answered "i am lost have you cancelled it then".
        #
        # A rule cannot out-argue an instruction sitting next to it; that shape
        # has now cost three live calls. So the misleading operational text is
        # REMOVED rather than argued with. `status` is deliberately left alone,
        # so the model still knows this particular attempt wrote nothing.
        for _misleading in ("message", "error"):
            result.pop(_misleading, None)
        # setdefault, not assignment: an executor that has already explained its
        # own refusal in caller-facing terms keeps the more specific wording.
        # The B-65 different-target guard in _exec_cancel_appointment does
        # exactly that, and it says more than this generic rule can.
        result.setdefault("caller_message_rule", _WRITE_ALREADY_DONE_RULE[family])
        return result
    # Layer 3's arming signal (B-36 cause 2a): Gate 5f is scoped to the write
    # that was actually refused, not to a conversation flow flag that a
    # reschedule never sets.
    refused[family] = True
    session[WRITE_REFUSED_KEY] = refused
    # CA166de2a9 — call-scoped, and armed ONLY by an executor failure.
    #
    # The availability blocks below exist to stop a spurious re-search while the
    # name and number are being collected. Once book_appointment has come back
    # from the provider having failed, that premise is gone: the slot on record
    # is one the calendar has just rejected, and re-reading it back is the one
    # thing that cannot help. On that call the post-collect block fired seven
    # times AFTER the first 400, each time instructing the model not to ask about
    # the day or time again. It looped for four minutes and the caller had to
    # propose a different day himself.
    #
    # `success is False` and not the branch condition: this branch also runs for
    # the gate refusals, which carry `{"status": "..._required"}` and no success
    # key at all. Those mean the write was never attempted because details are
    # still missing — precisely when the block is doing its job — so they must
    # not release it. Only a real attempt that reached the provider and failed.
    if family == WRITE_FAMILY_BOOKING and result.get("success") is False:
        session[BOOKING_WRITE_FAILED_KEY] = True
    logger.warning(
        "[ms_llm] %s did not succeed (status=%r) — false-confirmation guard "
        "ARMED for the %s family this turn",
        tool_name, result.get("status") or result.get("error"), family,
    )
    result = dict(result)
    result.setdefault("caller_message_rule", _WRITE_NO_CLAIM_RULE[family])
    return result


def _note_lookup_name_spoken(session: dict, spoken: str) -> None:
    """B-42 — record that the looked-up patient's NAME actually reached the caller.

    Called with the text released to TTS for the turn, so this is what was
    *heard*, not what was generated — the distinction that mattered on
    CA7e389a47 and again here. Latches: once true it stays true until the next
    lookup match resets it.

    Matching is per name token, word-bounded, minimum three characters. A first
    name alone counts: among people sharing a phone number ("Sarah" vs "Quentin")
    the first name is the discriminator, and requiring the surname would loop the
    caller on a readback Susie composed naturally.

    Bias note, because it runs the wrong way here: a FALSE POSITIVE opens the
    write gate and restores exactly the B-42 failure, whereas a false negative
    only re-asks. Hence word boundaries rather than substring — "rock" must not
    be satisfied by "Brockley".
    """
    from app.tools.receptionist_tools import LOOKUP_NAME_SPOKEN_KEY
    if session.get(LOOKUP_NAME_SPOKEN_KEY):
        return
    nm = (session.get("_lookup_patient_name") or "").strip()
    if not nm or not spoken:
        return
    low = spoken.lower()
    tokens = [tok for tok in re.split(r"[^a-z]+", nm.lower()) if len(tok) >= 3]
    if any(re.search(r"\b" + re.escape(tok) + r"\b", low) for tok in tokens):
        session[LOOKUP_NAME_SPOKEN_KEY] = True
        logger.info(
            "[ms_llm] B-42: looked-up name %r was spoken to the caller — "
            "identity gate satisfied", nm,
        )


_ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh",
    12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
    16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth",
    20: "twentieth", 21: "twenty-first", 22: "twenty-second",
    23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
    26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth",
    29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first",
}


def under_age_blocks_booking(session: dict) -> bool:
    """True when this caller stated an age below the clinic's minimum.

    Thin by design — the judgement lives in `capture_under_age`, which is
    clinic-gated and conservative. Extracted anyway, for the reason
    `_booking_confirmation_asked` and `_post_collect_readback_due` were: a
    condition written inline inside `_execute_tools` can only be tested by
    reading the source, and a source test catches the branch being DELETED but
    not the branch being disabled. Named, it can be asserted against directly.
    """
    return bool(session.get("_under_age_declared"))


def note_reason_question_asked(session: dict, spoken: str) -> bool:
    """Latch that the REASON question has been put to this caller.

    `CA86c320ef` (4 Aug 2026, Vital Edge, live) asked it twice:

        "Right — What's the appointment for?"                      (turn 2)
        "Noted — and is there a particular area or reason for the
         massage, like back tension, general stress, or something
         else?"                                                    (turn 3)

    Rule 1b already says "ask ONCE". It was not honoured, and it could not be:
    the model composes each turn with no reliable memory of having asked, so
    "once" is a property of the CALL, not of a sentence. Prompt text sets the
    wording; engine state has to set the guarantee. Same division of labour as
    `_note_lookup_name_spoken` above and `capture_duration_choice`.

    Matched on INTENT rather than the clinic's exact literal, deliberately. The
    second ask on that call was an improvisation that shared no wording with the
    first — a literal match would have missed precisely the turn that mattered.

    Read from the text released to TTS, so it records what the caller HEARD.
    Latches: once true it stays true for the call.

    Gated on the clinic having opted in, so that for a clinic which never asked
    for any of this — jv_v1, theorem — this function does nothing at all rather
    than merely having no visible effect. The prompt side was already inert for
    them (their rendered prompt is byte-identical, static and dynamic, across
    every session shape); this makes the code side inert too, which is the
    difference between "no behaviour change" and "no execution".
    """
    if session.get("_reason_question_asked"):
        return True
    if not spoken or "?" not in spoken:
        return False
    try:
        from app.clinic_config import get_clinic
        _pf = (get_clinic(session.get("clinic_id")) or {}).get("prompt_facts") or {}
        if not _pf.get("reason_question"):
            return False
    except Exception:
        return False
    low = spoken.lower()
    asked = (
        # "what's the appointment for", "what is it for", "what's it for"
        re.search(r"\bwhat(?:'?s| is)\b[^?]{0,40}\bfor\b[^?]{0,20}\?", low)
        # "is there a particular area or reason…", "what's the reason for…"
        or re.search(r"\breason for\b[^?]{0,60}\?", low)
        or re.search(r"\barea or reason\b", low)
        # "what brings you in", "what's brought you in"
        or re.search(r"\bwhat\b[^?]{0,20}\bbrings? you (?:in|to)\b", low)
    )
    if not asked:
        return False
    session["_reason_question_asked"] = True
    logger.info(
        "[ms_llm] reason question asked — latched so it cannot be re-asked: %r",
        spoken[:90],
    )
    return True


def _note_lookup_slot_spoken(session: dict, spoken: str) -> None:
    """B-54 — record that the matched appointment's DATE actually reached the caller.

    `_note_lookup_name_spoken` answers "is this the right person". It cannot
    answer "is this the right appointment", and on CA156fa25 (3 Aug 2026) that
    distinction cost a real calendar event: all 15 matches were the SAME person,
    so saying "Quentin Rock" settled nothing. The caller agreed to their own
    name — the register's framing that they never agreed is wrong — and match #1
    was cancelled. What they were never told is WHICH of their appointments it
    was. `lookup_patient` emits the EARLIEST upcoming match unconditionally
    (receptionist_tools.py, `matches.sort(...)` then `matches[0]`), and the
    caller meant the one they had booked four minutes earlier.

    So this latch is on the appointment axis, not the identity axis. Both must
    be satisfied before a destructive write is allowed.

    Matched on WEEKDAY NAME **and** DAY OF MONTH, both required. Same bias as
    B-42 and it runs the same way: a false positive opens the gate on a
    destructive write, a false negative only makes Susie say the date. Hence two
    independent components rather than one, and word boundaries throughout.

    The TIME is deliberately NOT required. Susie says "half past six in the
    evening", "six thirty", "18:30" — matching that reliably is a false-negative
    factory, and on this path a false negative loops the caller on a cancel they
    are entitled to make. **Consequence, stated so nobody assumes otherwise:
    two appointments on the SAME DATE are not disambiguated by this latch.**
    That is a real residual, it is rarer than the initial-plus-follow-up case
    this closes, and it needs the time-matching work to fix properly.
    """
    from datetime import datetime as _dt
    from app.tools.receptionist_tools import LOOKUP_SLOT_SPOKEN_KEY
    if session.get(LOOKUP_SLOT_SPOKEN_KEY):
        return
    iso = (session.get("_lookup_appointment_datetime") or "").strip()
    if not iso or not spoken:
        return
    try:
        when = _dt.fromisoformat(iso)
    except ValueError:
        # Fail CLOSED — the gate stays shut and the model is re-steered. The
        # alternative (treat an unparseable date as "no need to verify") opens a
        # destructive write on the exact input we understand least. The caller
        # is not stranded: the refusal is a message to the model, so the turn
        # continues and degrades to taking a message.
        logger.warning(
            "[ms_llm] B-54: could not parse looked-up appointment datetime %r "
            "— slot gate stays CLOSED", iso,
        )
        return
    low = spoken.lower()
    weekday = when.strftime("%A").lower()
    if not re.search(r"\b" + weekday + r"\b", low):
        return
    dom = when.day
    forms = [
        rf"\b{dom}(?:st|nd|rd|th)?\b",
        r"\b" + re.escape(_ORDINAL_WORDS.get(dom, "\x00")) + r"\b",
    ]
    if any(re.search(f, low) for f in forms):
        session[LOOKUP_SLOT_SPOKEN_KEY] = True
        logger.info(
            "[ms_llm] B-54: appointment date %s (%s the %d) was spoken to the "
            "caller — slot gate satisfied", iso, weekday, dom,
        )


def _lookup_identity_unconfirmed(session: dict) -> bool:
    """B-42 + B-54 — True when the active lookup was ambiguous and the caller has
    not been told BOTH whose appointment it is and WHICH ONE.

    Two axes, deliberately separate. B-42 is the shared-phone / different-person
    case (a couple, a parent, a carer) and its name check is verified live on
    CAdbc84848. B-54 is the same-person / multiple-appointments case — an initial
    plus a follow-up is entirely routine — which the name check does not model at
    all. Neither subsumes the other, so both are required.
    """
    from app.tools.receptionist_tools import (
        LOOKUP_AMBIGUOUS_KEY, LOOKUP_NAME_SPOKEN_KEY, LOOKUP_SLOT_SPOKEN_KEY,
    )
    return bool(
        session.get(LOOKUP_AMBIGUOUS_KEY)
        and not (
            session.get(LOOKUP_NAME_SPOKEN_KEY)
            and session.get(LOOKUP_SLOT_SPOKEN_KEY)
        )
    )


def _book_reply_is_affirmative(messages) -> bool:
    """FM-01: book_appointment may fire only on a clear caller YES to the
    "Shall I go ahead and book that in?" confirmation. The question-asked guard
    is necessary but not sufficient — a negative, ambiguous or absent reply, or
    an affirmative paired with a correction ("yes, actually no"), must not book.
    Reuses fast_path's yes/no patterns (the same affirmative detection the rest
    of the engine uses). Bias: a false block just re-asks; a false allow books
    the wrong thing.
    """
    from app.media_streams.fast_path import _YES_PATTERNS, _NO_PATTERNS
    text = _last_user_text(messages or []).lower()
    is_yes = any(p in text for p in _YES_PATTERNS)
    is_no = any(p in text for p in _NO_PATTERNS)
    return is_yes and not is_no


# ── The affirmation verdict: L1 deterministic, L2 classifier ────────────────
#
# `_book_reply_is_affirmative` above is INTENTIONALLY left alone. It still feeds
# the FM-25 write-ack filler (one call site) and is imported directly by
# tests/regression/test_write_ack_filler_gate.py, which calls it synchronously.
# Making it async to add a classifier would break both. The gates get their own
# function instead.
#
# Why this exists at all — measured against the live pattern sets on 1 Aug 2026:
#
#     "go for it"                         BOOK=False  <- CA7e389a47, lost booking
#     "crack on" / "go on then"           BOOK=False
#     "don't do it"                       BOOK=True   <- WRONG booking
#     "don't book it"                     BOOK=True   <- WRONG booking
#     "yes but can we do friday instead"  BOOK=True   <- books the WRONG SLOT
#
# Substring matching cannot be repaired by adding more substrings: adding
# "go for it" to the yes list also makes "don't go for it" book. The negation
# and correction cues below are what close the wrong-booking half, and they are
# deterministic — no classifier is involved in that decision.

# A yes token immediately behind one of these is a refusal, not a confirmation.
_NEGATION_CUES: tuple = (
    "don't", "dont", "do not", "please don't", "please dont", "rather not",
    "no need", "hold off", "not yet",
)
# An affirmative paired with one of these is a correction in progress. FM-01's
# docstring already required blocking these ("yes, actually no"); only the
# literal "actually no" was ever caught.
_CORRECTION_CUES: tuple = (
    "but ", "but,", "instead", "actually", "hang on", "hold on", "wait",
    "can we", "could we", "can i", "change", "different", "rather",
    "sorry", "although",
)


def _book_verdict_deterministic(text: str) -> str:
    """L1 — 'yes' | 'no' | 'unsure', with no network call.

    Ordering is the safety property: negation and correction are checked BEFORE
    the affirmative, so "don't book it" and "yes but can we do Friday instead"
    can never reach the yes branch. Anything this cannot settle returns 'unsure'
    and is handed to L2 — it never guesses.
    """
    from app.media_streams.fast_path import _YES_PATTERNS, _NO_PATTERNS

    t = " " + (text or "").strip().lower() + " "
    if not t.strip():
        return "no"          # absent reply is not consent
    if any(c in t for c in _NEGATION_CUES):
        return "no"
    if any(p in t for p in _NO_PATTERNS):
        return "no"
    _is_yes = any(p in t for p in _YES_PATTERNS)
    if _is_yes and any(c in t for c in _CORRECTION_CUES):
        # "yeah actually hang on", "yes but can we do Friday instead" — an
        # affirmative the caller is retracting. 'no', not 'unsure': FM-01
        # requires that these never book, and a hard requirement should not be
        # delegated to a classifier that could return yes. The cost of the
        # stricter reading is a re-ask on "yes, sorry, go ahead" — which is the
        # trade this gate is explicitly biased toward.
        return "no"
    if _is_yes:
        return "yes"
    return "unsure"


# Affirmatives for "is that the best number for the booking?" — deliberately
# NOT fast_path._YES_PATTERNS, which is tuned for "shall I book that in?".
# Replaying both over the 950 stored caller turns, reusing that set accepted
# 209 extra utterances including '11 in the morning please' and '28 please at
# 5', because "please" is an affirmative for the BOOKING question and simply a
# politeness marker everywhere else. Its members are also bare substrings, so
# 'ok' matches inside 'looking'.
#
# Word-boundary matched for that reason. Safe to enumerate ONLY because the
# negation and correction checks run first — "don't use that one" is settled as
# a refusal before any of these is consulted, which is the property a plain
# phrase list can never have.
_PHONE_YES_RE = re.compile(
    r"\b("
    r"yes|yeah|yep|yup|aye|correct|perfect|grand"
    r"|ok|okay|sure|fine|great|lovely|spot on|go ahead"
    r"|that(?:'s| is|s) (?:right|correct|fine|it|the one|the number"
    r"|the best number|the best one)"
    r"|that(?:'ll| will|ll) do"
    r"|it is"
    r"|the best number"
    r"|use (?:this|that) (?:number|one)"
    r"|keep (?:this|that) (?:number|one)"
    r"|(?:this|that) (?:number|one) is fine"
    r"|(?:this|that) number"
    r")\b",
    re.IGNORECASE,
)


#: A bare affirmative TOKEN. Deliberately much stricter than
#: _PHONE_YES_RE, which matches "use that one" -- and therefore matches
#: "don't use that one" too. That looseness is safe only because the
#: negation check runs first, so anything reading an affirmative in the
#: PRESENCE of a negation needs its own evidence.
_STRICT_YES = re.compile(
    r"\b(?:yes|yeah|yep|yup|aye|correct|that\'?s right|that\'?s the one|that\'?s it|it is)\b",
    re.IGNORECASE,
)


def _NO_PATTERNS_SET():
    from app.media_streams.fast_path import _NO_PATTERNS

    return _NO_PATTERNS


_no_pattern_re_cached = None


def _matches_a_no_pattern(padded: str) -> bool:
    """`_NO_PATTERNS`, matched on WORD boundaries rather than as substrings.

    The set contains the bare token "no", and a plain `in` test finds it
    inside "a-no-ther", "no-thing", "k-no-w". Live on the adaptive-caller
    suite: "I haven't got another one, but yes that's right" scored as a
    refusal because of the word "another".

    Third instance of this exact shape in this codebase -- the screening
    triggers had it when "know" matched "no". The lesson each time is the
    same and it is not "add more phrases": a bare negator has to be matched
    as a WORD.

    Scoped to this function deliberately. `_NO_PATTERNS` is shared with
    fast_path, whose own callers have their own guards and their own
    regression tests; widening the fix to the shared set is a separate
    change with a separate blast radius.
    """
    global _no_pattern_re_cached
    if _no_pattern_re_cached is None:
        _no_pattern_re_cached = re.compile(
            r"\b(?:"
            + "|".join(re.escape(p.strip()) for p in sorted(_NO_PATTERNS_SET()))
            + r")\b",
            re.IGNORECASE,
        )
    return bool(_no_pattern_re_cached.search(padded))


def _retracted_after_yes(padded: str) -> bool:
    """Did a correction come AFTER the affirmative? PURE.

    Position is the difference between taking an answer back and arriving at
    one. "Yes, but can I give you a different number" retracts; "I haven't got
    another one, but yes that's right" does not.
    """
    match = _STRICT_YES.search(padded)
    if not match:
        # No plain affirmative to place a correction against, so keep the
        # original behaviour: any correction cue counts.
        return any(cue in padded for cue in _CORRECTION_CUES)
    return any(cue in padded[match.end():] for cue in _CORRECTION_CUES)


def _qualified_yes(padded: str) -> bool:
    """A plain YES that survives everything around it. PURE.

    "I don't think I gave you that, but yes, that's my number" -- the negation
    is about how the number was obtained, and the answer to the question is the
    yes that follows it.

    POSITION is the whole rule, applied twice:

      * the yes must come AFTER the last negation. "Don't use that one" has no
        affirmative at all; "yes, but don't use that one" puts the negation last
        and is a retraction. Both stay refusals.
      * no correction may come AFTER the yes. `_CORRECTION_CUES` contains
        "but ", and "but yes" is the pivot INTO agreement rather than away from
        it -- checking for the cue anywhere in the utterance made every
        qualified yes a refusal, which is the bug this exists to fix. "Yes but
        can I give you a different number" puts the correction after the yes and
        stays a refusal.

    Never returns a verdict on its own: the caller turns this into 'unsure',
    never 'yes', so a mistake here costs a keypad handoff rather than a wrong
    number on a real booking.
    """
    last_negation = max(
        (padded.rfind(cue) for cue in _NEGATION_CUES), default=-1
    )
    if last_negation < 0:
        return False
    tail = padded[last_negation:]
    match = _STRICT_YES.search(tail)
    if not match:
        return False
    after_yes = tail[match.end():]
    return not any(cue in after_yes for cue in _CORRECTION_CUES)


def _phone_confirm_verdict(text: str) -> str:
    """L1 — 'yes' | 'no' | 'unsure' for the caller-ID confirmation step.

    CAcb4a11b90 (2 Aug 2026). The caller answered "yeah that's the one" to
    "I've got you on … is that the best number for the booking?".
    `_is_use_this_number` returned False, phone_confirmed was never set,
    book_appointment's A1 gate refused the write, the model re-asked, the
    caller said the same words again, and the call was abandoned after two
    full cycles with name, slot and number all collected.

    Worse, and found while fixing it: `_is_use_this_number("don't use that
    one")` returns TRUE. A caller explicitly refusing the caller ID would have
    it stored as confirmed and BOOKED ON. That is a wrong booking rather than
    a missed one, and it is the identical defect `_book_verdict_deterministic`
    was written to close on the booking path ("don't book it" -> BOOK=True):

        "Substring matching cannot be repaired by adding more substrings:
         adding 'go for it' to the yes list also makes 'don't go for it' book."

    Same fix, same ordering, applied to the path that never got it. Negation
    and correction are checked BEFORE the affirmative, so a refusal can never
    reach the yes branch no matter what is added to the affirmative sets.

    Returns 'unsure' rather than guessing; the caller of this decides what an
    unsettled reply does. It must NOT be "re-ask the same question" — that is
    the loop above. The keypad is deterministic and its ladder terminates.
    """
    t = " " + (text or "").strip().lower() + " "
    if not t.strip():
        return "no"                     # an absent reply is not a confirmation
    _is_yes = bool(_PHONE_YES_RE.search(t))
    _negated = (
        any(c in t for c in _NEGATION_CUES) or _matches_a_no_pattern(t)
    )
    if _negated:
        # A negation still beats an affirmative -- that ordering is what stops
        # "don't use that one" being stored as a confirmation and booked on, and
        # it does not change here.
        #
        # But a negation that is not ABOUT the number, sitting in front of a
        # plain yes, is neither a refusal nor a confirmation. Live on the
        # adaptive-caller suite (2026-08-29): "I don't think I gave you that,
        # but yes, that's my number" scored 'no', so phone_confirmed stayed
        # False, the PHONE STEP OUTSTANDING steer kept rendering, and the model
        # obediently re-asked the phone question AFTER the caller had already
        # agreed to the booking -- until the caller said "you've already asked
        # me that twice". That is the A4 confirmation loop, and detect_defects
        # counts 144 of them across the obs corpus.
        #
        # 'unsure' is the correct verdict and it is NOT a soft 'yes': it can
        # never satisfy the A1 book gate, and it routes into the unsettled
        # ladder that terminates in the keypad after two answers. So the
        # wrong-number risk is untouched while the loop is bounded.
        #
        # A retraction stays 'no': "yes but can I give you a different number"
        # is the caller taking the affirmative back, and the keypad is where
        # that belongs.
        if _qualified_yes(t):
            return "unsure"
        return "no"
    if _is_yes and _retracted_after_yes(t):
        # "yes but can I give you a different number" — an affirmative the
        # caller is retracting. Deliberately 'no' (which routes to the keypad),
        # not 'unsure': storing the caller ID here is the wrong-number outcome.
        #
        # AFTER the yes, not anywhere in the sentence. "but" is a correction
        # cue and "but yes" is the pivot INTO agreement, so an unpositioned
        # check made "I haven't got another one, but yes that's right" a
        # refusal. _book_verdict_deterministic keeps the unpositioned form on
        # purpose -- a wrong booking is worse than a re-ask -- and is not
        # touched here.
        return "no"
    if _is_yes:
        return "yes"
    return "unsure"


_classifier_client_cached = None


def _classifier_client():
    """One reused client for L2.

    Two fixes over the first cut, both found by reading CA7d46c2bc back:

    1. `api_key` is passed explicitly, matching how this module builds its main
       client (line ~805). `AsyncAnthropic()` with no argument only works when
       the key happens to be in os.environ; every other client here is explicit,
       and a classifier that silently cannot authenticate fails closed — which
       looks exactly like the caller having said no.
    2. Built once. A per-call client pays connection setup inside a 1.5s budget;
       the first such call measured 1.8s locally before it even reached auth.
       That is a fail-closed timeout on the caller's first "go for it" and
       nothing in the transcript would explain it.
    """
    global _classifier_client_cached
    if _classifier_client_cached is None:
        import anthropic as _anthropic
        _classifier_client_cached = _anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=_BOOK_CLASSIFIER_TIMEOUT_S,
            max_retries=0,          # the wait_for below is the only budget
        )
    return _classifier_client_cached


async def prewarm_classifier() -> float:
    """Make the L2 classifier's TLS pool live at boot. Returns elapsed seconds,
    or 0.0 if nothing was warmed. NEVER raises — a failed prewarm must not
    block startup.

    Why this exists (CA3a6cfb84, 2026-08-03, build 8e12aafe8b39). A caller said
    "uh go for it", L2 timed out, `_book_reply_verdict` failed closed, and
    `book_appointment` was blocked. The caller had to say "i said go for it"
    before the booking went through — an extra turn on the single most
    important question in the call.

    `_classifier_client()` builds the client ONCE, which was meant to fix
    exactly this (see its docstring: "a fail-closed timeout on the caller's
    first 'go for it'"). But it builds it LAZILY, on first use. So the cost
    moved off calls 2..n and stayed on call 1 after every deploy or cold start,
    where it is paid inside the `BOOK_CLASSIFIER_TIMEOUT_S` budget.

    Constructing the client is NOT enough — the object is cheap; the expense is
    the first request's DNS + TCP + TLS + auth. So this issues one real,
    minimal request, exactly as the Acuity and ElevenLabs prewarms do for the
    same reason.

    It must use `_classifier_client()` itself, not any other AsyncAnthropic
    instance: each client owns its own httpx pool, so warming a different one
    warms a different connection. `app/main.py` already prewarms
    `app.flows.conversation._get_client()`, and that did nothing for this path.
    """
    if not BOOK_CLASSIFIER_ENABLED:
        logger.info("[ms_llm] classifier prewarm skipped — BOOK_CLASSIFIER_ENABLED is off")
        return 0.0
    if not ANTHROPIC_API_KEY:
        logger.info("[ms_llm] classifier prewarm skipped — no ANTHROPIC_API_KEY")
        return 0.0

    _t0 = time.monotonic()
    try:
        # Deliberately NOT _classify_book_reply(): that carries the per-turn
        # BOOK_CLASSIFIER_TIMEOUT_S budget, which is the very thing too tight to
        # absorb a cold connection. At boot there is no caller waiting, so allow
        # a real one.
        await asyncio.wait_for(
            _classifier_client().messages.create(
                model=HAIKU,
                max_tokens=1,
                temperature=0,
                messages=[{"role": "user", "content": "ok"}],
            ),
            timeout=10.0,
        )
        _elapsed = time.monotonic() - _t0
        logger.info(
            "[ms_llm] L2 classifier TLS pool pre-warmed (%.0fms) — the first "
            "booking confirmation no longer pays connection setup",
            _elapsed * 1000,
        )
        return _elapsed
    except Exception as exc:
        # Non-fatal by design. A failed prewarm leaves behaviour exactly as it
        # is today: the first classifier call pays setup and may fail closed to
        # a re-ask. That is worse than warm and better than a boot failure.
        logger.warning(
            "[ms_llm] classifier prewarm failed after %.0fms (non-fatal — first "
            "booking confirmation may still fail closed): %r",
            (time.monotonic() - _t0) * 1000, exc,
        )
        return 0.0


async def _classify_book_reply(text: str) -> str:
    """L2 — Haiku on the utterances L1 could not settle. 'yes' | 'no'.

    Fails CLOSED (returns 'no'), which routes to a re-ask rather than a booking.
    That is only a safe default because the re-ask path works — it did not
    before the D2 fix in this same commit, where Gate 5f's re-steer left
    last_bot_prompt empty and the re-ask could never satisfy the gate.

    Timeout is explicit and short. Every other Haiku call in this codebase has
    none, and on the booking turn an unbounded hang is dead air at the moment
    the caller is waiting to be booked. 1.0s fits inside the write-ack filler
    that is already playing (measured: filler queued 1.25s before the gate).
    """
    resp = await asyncio.wait_for(
        _classifier_client().messages.create(
            model=HAIKU,
            max_tokens=5,
            temperature=0,
            system=(
                "You judge whether a caller consented to an action a receptionist "
                "just offered. Answer with exactly one word: YES or NO.\n"
                "YES only if they are agreeing to go ahead now.\n"
                "NO if they are declining, hesitating, asking a question, "
                "correcting a detail, or asking to wait."
            ),
            messages=[{
                "role": "user",
                "content": (
                    "The receptionist asked whether to go ahead. "
                    f"The caller replied: \"{text}\"\nYES or NO?"
                ),
            }],
        ),
        timeout=_BOOK_CLASSIFIER_TIMEOUT_S,
    )
    _answer = (resp.content[0].text or "").strip().lower() if resp.content else ""
    return "yes" if _answer.startswith("y") else "no"


async def _book_reply_verdict(messages, session) -> bool:
    """True when the caller clearly consented. Used by FM-01 and FM-23.

    L1 settles the clear cases with no network call and no latency; only the
    genuinely ambiguous middle reaches L2. Memoised per turn because the tool
    loop retries book_appointment on a block and the utterance cannot change
    between retries.
    """
    text = _last_user_text(messages or []).lower()
    _cache = session.get("_book_verdict_cache") or {}
    if text in _cache:
        return _cache[text]

    verdict = _book_verdict_deterministic(text)
    if verdict == "unsure":
        if not BOOK_CLASSIFIER_ENABLED:
            # Flag off: behave as today — an unsettled reply blocks and re-asks.
            verdict = "no"
            logger.info("[ms_llm] L2 disabled — unsure reply blocks: %r", text[:60])
        else:
            try:
                verdict = await _classify_book_reply(text)
                logger.info(
                    "[ms_llm] L2 classifier: %r -> %s", text[:60], verdict,
                )
            except Exception as exc:
                verdict = "no"
                logger.error(
                    "[ms_llm] L2 classifier failed (%r) — failing closed to a "
                    "re-ask, NOT to a booking", exc,
                )
    else:
        logger.info("[ms_llm] L1 verdict: %r -> %s", text[:60], verdict)

    _result = verdict == "yes"
    _cache[text] = _result
    session["_book_verdict_cache"] = _cache
    return _result


def _clear_slot_window_after_write_cta(session: dict) -> bool:
    """B1.2 — drop the slot-selection window once a write CTA has been spoken.

    Reschedule never asks for a name after the slot pick, so Spec J never clears
    `v3_awaiting_slot_selection`. The flag then survives the move CTA and silence
    re-asks *"which of those days suits you?"* (CAba5b1629… A9b) instead of
    staying on the confirmation. Booking usually clears via Spec J; this makes
    the write-CTA turn itself authoritative for every write family.

    NOT when the window was armed by the very reply that carried the CTA. One
    turn can do both — verified by running `_flush_slot_buf` and this function
    over a single string:

        "I can move that for you. Number 1 - Monday the 24th at 9am,
         Number 2 - Tuesday the 25th at 2pm. Shall I go ahead and move it?"

    `_flush_slot_buf` arms the map and the flag off that text mid-stream; a few
    lines later in the SAME `run_turn` this function matched the move CTA in the
    same sentence and popped both. The caller heard two options and could then
    select neither — not verbally (the flag is gone) nor by keypad (the map that
    the "keypad" fallback arms from, connection.py, is gone with it). Arming and
    destroying a window from one sentence is never what B1.2 was for: it exists
    for the CTA turn that FOLLOWS the pick, where the flag is a leftover.

    So a window stamped with the current turn is left alone. In the A9b case the
    stamp is from an earlier turn — or `_flush_slot_buf` has already dropped the
    map itself, having found no numbered options this turn — and the clear runs
    exactly as before.

    U-07-a — the trigger is the MAP, not the flag, and that distinction is the
    whole reason this function was dead on the path it was written for.

    `v3_awaiting_slot_selection` is not owned here. connection.py derives it from
    the map after every turn: a map present re-sets it True, a map absent pops it.
    A second writer — connection.py's "caller is responding, the slot window has
    closed" pop — clears the FLAG on the caller's reply and deliberately leaves
    the map alone, so that a keypad press still resolves. Reading the flag as the
    trigger therefore made this function early-return on exactly the turn it
    exists for:

        caller picks a day   -> flag popped (map kept)
        Susie speaks the move CTA
        this function        -> flag already gone, early return, MAP KEPT
        connection.py        -> map present, so flag re-armed True
        silence              -> "which of those days suits you?"

    Verified against `CA3eccc7c153bb92cc8142f625dfcc5414`: the watchdog logged
    `slot_selection_grace (v3_awaiting_slot_selection)` eight seconds after this
    clear was supposed to have run. That call passed on `36a7e5b`'s family-aware
    re-ask wording instead — which is still the load-bearing mechanism and must
    not be trimmed on the strength of this fix.

    So the window is open if EITHER key is live, and closing it means dropping
    the map. Leave the flag to its one owner; take away what that owner reads.
    """
    if not session or not (
        session.get("v3_awaiting_slot_selection")
        or session.get("v3_dtmf_slot_map")
    ):
        return False
    if not (
        _cta_asked(session, _booking_confirmation_asked)
        or _cta_asked(session, _move_confirmation_asked)
        or _cta_asked(session, _cancel_retention_asked)
    ):
        return False
    if session.get("v3_slot_map_armed_turn") == session.get("turn_count", 0):
        logger.info(
            "[ms_llm] write CTA spoken, but the slot window was armed by this "
            "same reply — leaving it open so the caller can still pick"
        )
        return False
    session.pop("v3_awaiting_slot_selection", None)
    session.pop("v3_dtmf_slot_map", None)
    session.pop("v3_slot_dtmf_active", None)
    session.pop("v3_slot_map_armed_turn", None)
    logger.info(
        "[ms_llm] write CTA spoken — dropped the slot window "
        "(map + v3_awaiting_slot_selection)"
    )
    return True


def _cta_asked(session: dict, predicate) -> bool:
    """B-38 — apply a CTA predicate to what was ASKED, not only to the capped prompt.

    `last_bot_prompt` is truncated at 200 characters. Reproduced 3 Aug 2026: an
    ordinary read-back naming the service, the practitioner and the site runs to
    **207 characters on a cancel and 251 on a reschedule**, and the confirmation
    question falls off the end. The observed calls ran 148, so the headroom is
    tens of characters, not hundreds.

    When it happens, three things break at once — the write is blocked (B-36
    cause 1, arriving by truncation instead of by rewording), the caller's
    "go ahead" is dropped by the slot guard (B-37), and Gate 5f arms. One
    truncation re-opens two fixed defects.

    `last_question` holds exactly the question sentence and is stored **uncapped**
    (see the F_LAST_QUESTION assignment in `run_turn`), so it survives the cut.
    B-31 established this same fallback for the clinical layer in
    `clinical_screening.py`; the write gates never got it.

    Each source is judged WHOLE and independently — deliberately not concatenated.
    Joining them could span a false match: a prompt ending "...book that" beside a
    question starting "in the morning?" would read as the booking CTA "book that
    in" and open the gate on a sentence nobody said.

    No staleness risk: F_LAST_QUESTION is assigned unconditionally every turn, so
    a turn that asked nothing sets it to "" rather than leaving an older CTA
    standing.
    """
    for text in (session.get(F_LAST_BOT_PROMPT), session.get(F_LAST_QUESTION)):
        if text and predicate(text):
            return True
    return False


def _booking_confirmation_asked(last_bot_prompt: str) -> bool:
    """True when the bot's last turn asked the BOOKING confirmation question.

    Extracted verbatim from the book_appointment gate so that the predicate has
    a name and can be asserted against directly — B-36 R5 turns on which
    last_bot_prompt satisfies which gate, and a test that re-types the literals
    would not have caught the leak it exists to prevent.

    B-36 R6 / VE acceptance run 2026-08-04, calls 1 and 7 (`CA094dcb41`,
    `CAb408ed32`): the two original literals are the CONFIRMED-booking wording.
    A **provisional** clinic's prompt mandates a different sentence —
    `clinic_template_prompt.py`'s `readback_cta` is "shall I put that request
    through to {prac} to confirm?" — and deliberately bans "book"/"booked", so
    neither literal could ever appear. Vital Edge's prompt was therefore
    required to say the one thing its own write gate could not recognise, and
    `book_appointment` was refused every time the model obeyed it.

    Intermittent rather than total, which is why it survived: when the model
    drifted to "shall I go ahead and put that through" the first literal matched
    and the booking landed. Obedience to the mandated wording is what broke it.

    This is the same defect and the same repair as `_move_confirmation_asked`
    below (`CA23199d08`, 3 Aug) — a single-phrasing gate against a sentence the
    model composes. Widening the CTA arm cannot by itself book anything: FM-01
    requires `_book_reply_is_affirmative` on top, and this predicate is only its
    necessary half.
    """
    lbp = (last_bot_prompt or "").lower()
    return (
        "shall i go ahead" in lbp
        or "book that in" in lbp
        # Provisional clinics. Practitioner-name agnostic on purpose — the
        # mandated sentence interpolates {prac}, so matching the name would
        # re-break on the next clinic.
        or "put that request through" in lbp
    )


def _cancel_retention_asked(last_bot_prompt: str) -> bool:
    """True when the bot's last turn asked FOR CONSENT TO CANCEL.

    Two shapes, because the three prompts in this repo mandate two different
    ones:

      * the RETENTION question — "would you like to reschedule this
        appointment, or cancel it altogether?" — which `clinic_template_prompt`
        requires and which the `"altogether"` arm has always covered;
      * a DIRECT cancel CTA — "shall I go ahead and cancel that?" — which
        `susie_system_prompt`'s theorem_v3 branch mandates *by name* ("the CTA
        is always 'shall I go ahead and cancel that?'"), and which contains no
        `"altogether"` at all.

    `B-57`: on Theorem the gate could therefore never open, so
    `cancel_appointment` was refused every time the model obeyed its own prompt.
    Same defect and same repair as `_booking_confirmation_asked` (R6) and
    `_move_confirmation_asked` — a single-phrasing gate against a sentence the
    model composes.

    The ask-shape arm requires BOTH an ask shape and a cancel verb, so a
    statement ("I'm cancelling that for you") is not treated as having asked —
    that sentence is the claim Gate 5f exists to strip, not the question. The
    booking and reschedule re-steers carry an ask shape but no cancel verb, so
    neither can arm this gate; `test_b36_gate5f_write_families` asserts that
    directly and it is the leak that must not reopen.

    Widening the CTA arm cannot by itself cancel anything:
    `_cancel_reply_consents` still has to pass independently, and that is the
    condition doing the safety work.
    """
    lbp = (last_bot_prompt or "").lower()
    if not lbp:
        return False
    # The retention question, in any wording. Kept as the first arm because it
    # is what the template prompt mandates and what the cancel re-steer says.
    if "altogether" in lbp:
        return True
    return _direct_cancel_cta(lbp)


def _direct_cancel_cta(last_bot_prompt: str) -> bool:
    """True for a cancel CTA that offers ONE action, not a choice.

    The distinction matters to consent, not to arming. Against the retention
    question a bare "yes" identifies nothing — it answers an OR — which is why
    `_cancel_reply_consents` demands an explicit cancel token. Against "shall I
    go ahead and cancel that?" a "yes" is unambiguous, and `B-57` recorded what
    demanding the token costs there: the re-steer asks a question whose natural
    answer blocks the write, and `B-44` has a caller stating the intention to
    cancel four times across 89 seconds.

    Anything offering the caller an alternative is excluded, so the retention
    question and the cancel re-steer both fall out here rather than becoming
    yes-cancellable.
    """
    lbp = (last_bot_prompt or "").lower()
    if not lbp:
        return False
    if any(alt in lbp for alt in ("altogether", "reschedul", "keep this", "move it")):
        return False
    _ask_shapes = (
        "shall i go ahead", "shall i cancel", "would you like me to cancel",
        "want me to cancel", "happy for me to cancel", "ok to cancel",
        "okay to cancel", "shall i remove",
    )
    _cancel_verbs = ("cancel", "cancelling")
    return any(a in lbp for a in _ask_shapes) and any(v in lbp for v in _cancel_verbs)


def _move_confirmation_asked(last_bot_prompt: str) -> bool:
    """True when the bot's last turn asked the reschedule confirmation question.

    FM-23's gate used a single literal, `"move it for you" in last_bot_prompt`.
    That is one phrasing of a question the model composes, and on
    `CA23199d08907234dddb7d2167fb23753c` (3 Aug 2026, 01:04) it composed a
    different one:

        "Shall I go ahead and move your appointment to Thursday the 6th of
         August at quarter to seven in the evening?"

    Unmistakably the confirmation question, and it did not contain the literal.
    Worse, the gate reads
    `"move it for you" in ... AND await _book_reply_verdict(...)` — so the
    substring miss short-circuited, the caller's "yeah go for it" was **never
    evaluated**, the write was blocked, and the model announced success anyway.
    The caller was told their appointment had moved and it had not.

    Why it varied: the caller had just said "I think you got cut off", so the
    model re-asked with the full detail instead of the canned short form.
    Reasonable behaviour that a literal cannot survive. Same class as `B-25`
    and the step-8 reword — a hard-coded literal drifting from the prompt.

    The booking gate two branches up never had this problem because it accepts
    `"shall i go ahead" OR "book that in"`; the ask-shape arm carries any
    rewording. This brings reschedule into line.

    **This does not weaken the gate.** It only makes it *reachable*: the caller's
    affirmative still has to pass `_book_reply_verdict` independently, and that
    is the condition doing the safety work. A booking CTA cannot satisfy it
    either — "Shall I go ahead and book that in?" has the ask shape but no move
    verb, so both arms are required.
    """
    lbp = (last_bot_prompt or "").lower()
    if not lbp:
        return False
    # The canned template CTA, kept as its own arm so the exact wording the
    # prompt mandates can never stop matching.
    if "move it for you" in lbp:
        return True
    # Otherwise: an explicit ask shape AND a move/reschedule verb. Both, so a
    # statement about moving ("I'm moving your appointment to Thursday") does
    # not count as having ASKED — that sentence is the read-back, not the
    # question, and it is spoken on the turn before.
    _ask_shapes = (
        "shall i go ahead", "shall i move", "shall i reschedule",
        "would you like me to move", "want me to move", "happy for me to move",
        "ok to move", "okay to move", "would you like me to reschedule",
    )
    _move_verbs = ("move", "moving", "reschedul")
    return any(a in lbp for a in _ask_shapes) and any(v in lbp for v in _move_verbs)


def _cancel_reply_consents(messages, session: Optional[dict] = None) -> bool:
    """FM-23: cancel_appointment is DESTRUCTIVE — it may fire only on an EXPLICIT
    cancel instruction, in the template cancel-retention context. The confirm is
    the retention question ("...reschedule this appointment, or cancel it
    altogether?"); the caller consents by SAYING "cancel", so this can NOT reuse
    _book_reply_is_affirmative ("cancel" is a NO pattern). Bias hard toward NOT
    cancelling: a bare "yes"/"ok"/"go ahead" is ambiguous against the OR-question
    and must not cancel; a reschedule word, "keep/leave it", "don't cancel", or a
    bare "no" all block. Only an explicit "cancel" token allows.

    `B-57`, second half: that reasoning is sound for an OR-question and wrong for
    a single-action CTA. Theorem's prompt mandates "shall I go ahead and cancel
    that?", to which "yes please" is the natural and unambiguous answer — and
    requiring the token there means a caller can be unable to complete a cancel
    at all. So when `session` shows the CTA was a DIRECT cancel ask, a clear
    affirmative counts.

    Deliberately narrow, in three ways. The affirmative is judged by
    `_book_verdict_deterministic`, which settles negation and correction BEFORE
    the yes ("don't cancel it", "yes but hang on") and returns 'unsure' rather
    than guessing — no classifier, no network, and 'unsure' blocks. The
    alternative-offering shapes are excluded by `_direct_cancel_cta`, so the
    retention question and the cancel re-steer still demand the token. And
    everything below still applies first: any negation, any reschedule word, or
    "keep/leave it" blocks regardless of what was asked.
    """
    text = _last_user_text(messages or []).lower()
    if not text:
        return False
    # Any negation defeats consent — a destructive cancel must never fire while the
    # caller is negating ("I don't want to cancel", "no cancellation", "not cancel",
    # "leave/keep it"). Bias hard: block on ANY negation token, even "no, cancel it"
    # — a re-ask is safe; a wrong delete is not.
    if any(n in text for n in (
        "don't", "do not", "dont", "not ", "n't", "never", "no ", "leave", "keep",
    )):
        return False
    # Caller chose the retention alternative (reschedule) rather than cancel.
    if any(w in text for w in ("reschedul", "move", "change", "different", "another time")):
        return False
    # Consent requires an explicit cancel token — a bare yes/ok is ambiguous
    # against the "reschedule, or cancel?" OR-question and must not delete.
    if "cancel" in text:
        return True
    # B-57 — unless the question named one action, in which case the yes is not
    # ambiguous. Read through _cta_asked so the uncapped `last_question` is
    # consulted too: a cancel read-back naming service, practitioner and site
    # runs past last_bot_prompt's 200-char cap (B-38), and the truncated form
    # would silently fall back to demanding the token.
    if session is not None and _cta_asked(session, _direct_cancel_cta):
        return _book_verdict_deterministic(text) == "yes"
    return False


# Phrases the assistant SPEAKS during the phone step (Step 8) — offering the
# calling number for confirmation or asking the caller to type a new one.  Used
# by the phone backstop to tell whether the phone question was ever actually put
# to the caller.  Matched as substrings against assistant turns in the recent
# history window; kept broad so any reasonable phrasing of the phone question
# registers (a false "asked" only relaxes the backstop, never over-blocks).
_PHONE_STEP_MARKERS: tuple = (
    "use this number",
    "best one for your",
    "best number",
    # Step 8's read-back opener (2026-07-26, A1). Kept in sync with
    # clinic_template_prompt._PHONE_STEP_MARKERS.
    "i've got you on",
    "ive got you on",
    "number you're calling on",
    "number you're calling from",
    "number you're ringing",
    # "on your keypad" is deliberately NOT a marker. It also appears verbatim
    # in the LOCATION rung-3 prompt — "No problem at all — on your keypad, just
    # press 1 for Awlstuh, or 2 for Redditch" (connection.py _LOC_RUNG3_DTMF) —
    # so keying on it treats a CLINIC question as the phone question having
    # been asked. Two consequences, both live on theorem_v3 2026-08-06:
    #
    #   * the phone backstop below (book_appointment, ~line 3475) blocks only
    #     when phone_confirmed is unset AND _phone_step_asked is False. A
    #     caller who saw the location keypad flipped the second to True, so
    #     the backstop was disarmed and a booking could be written with an
    #     unconfirmed caller-ID number that nobody had read back;
    #   * connection.py's phone-confirm-unsettled ladder fired against the
    #     clinic question — 13:38:57 and 20:53:03 both logged "phone confirm
    #     unsettled" and queued "go ahead and type the number" at a caller who
    #     had been asked which clinic they wanted.
    #
    # Nothing is lost by omitting it: every genuine phone prompt says "type the
    # number on your keypad" and so still matches "type the number" below.
    # latency_timing._PHONE_QUESTION_MARKERS reached this same conclusion for
    # B-15 and excluded it there; this is that decision applied to the list it
    # was copied from.
    "type the number",
    # "type YOUR number" — the model uses both. Susie said "could you type
    # your number on your keypad?" on CA9758ceab and this list matched nothing,
    # so the phone step was never recorded as asked. Safe to add: it does not
    # appear in the LOCATION rung ("on your keypad, just press 1 for Awlstuh"),
    # which is the collision that removed "on your keypad" from this list.
    "type your number",
)


def _phone_step_asked(messages) -> bool:
    """True if the assistant has already put the phone question to the caller
    (Step 8) anywhere in the recent history — the calling number was offered for
    confirmation, or a keypad entry was requested.  The phone backstop uses this
    so it only blocks book_appointment when the phone step was genuinely skipped,
    and can never loop a legitimate booking: the moment the model asks the phone
    question this returns True and booking proceeds on the next turn."""
    for m in messages or []:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = " ".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        low = text.lower()
        if any(mk in low for mk in _PHONE_STEP_MARKERS):
            return True
    return False


# Phrases the assistant SPEAKS when asking for the caller's surname (Step 7).
# Used by the surname backstop as an anti-deadlock fallback: if the model DID
# ask for the surname but capture missed it, booking still proceeds rather than
# looping. A false "asked" only relaxes the backstop, never over-blocks.
_SURNAME_STEP_MARKERS: tuple = (
    "your surname",
    "and your surname",
    "surname",
    "last name",
    "family name",
)


def _surname_step_asked(messages) -> bool:
    """True if the assistant has already asked the caller for their surname
    anywhere in the recent history. The surname backstop uses this so it can
    never loop a legitimate booking: once the surname question has been put to
    the caller, booking proceeds even if the capture pipeline missed the word."""
    for m in messages or []:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = " ".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        low = text.lower()
        if any(mk in low for mk in _SURNAME_STEP_MARKERS):
            return True
    return False


# ---------------------------------------------------------------------------
# Anthropic client singleton
# ---------------------------------------------------------------------------

_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        import httpx
        _anthropic_client = AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(30.0),   # streaming: allow full response time
        )
    return _anthropic_client


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

def _build_claude_tools(session: Dict[str, Any] = None) -> list:
    """Return tool definitions in Anthropic native format, customised per clinic."""
    from app.tools.receptionist_tools import build_tool_schemas
    cid = (session or {}).get("clinic_id")
    return list(build_tool_schemas(cid))


_GPT_CONSTRAINT_PREFIX = (
    "You are a voice receptionist. Keep responses under 2 sentences. "
    "Never start with: Of course, Absolutely, Certainly, Sure, Great, "
    "No problem, No worries. "
    "Never say: take your time, no rush, bear with me, just a moment, "
    "I'd be happy to, I'd be glad to, go ahead whenever you are ready. "
    "Respond directly and naturally.\n\n"
    # B-45 — steering half. The tools are withheld and the dispatch
    # refuses, so this is not what makes the guarantee; it is what stops
    # the caller hearing a dead end. Without it the model discovers it
    # cannot book only by trying.
    "IMPORTANT: you cannot make, move or cancel any appointment on this "
    "call — the booking system is temporarily unreachable. Never say "
    "anything has been booked, moved or cancelled. If the caller wants "
    "any of those, take their name and number and tell them the clinic "
    "will call straight back to confirm, or offer to put them through.\n\n"
)


def _build_openai_tools(
    session: Dict[str, Any] = None, allow_writes: bool = True
) -> list:
    """Return tool definitions in OpenAI function-calling format, per clinic.

    `allow_writes=False` withholds the three calendar-mutating tools. Used by the
    GPT fallback — see `_gpt_fallback` for why a degraded path must not write.
    """
    from app.tools.receptionist_tools import build_tool_schemas
    cid = (session or {}).get("clinic_id")
    tools = []
    for tool in build_tool_schemas(cid):
        if not allow_writes and tool["name"] in _WRITE_TOOL_FAMILIES:
            continue
        tools.append({
            "type": "function",
            "function": {
                "name":        tool["name"],
                "description": tool.get("description", ""),
                "parameters":  tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return tools


# ---------------------------------------------------------------------------
# Model selector
# ---------------------------------------------------------------------------

def _pick_model(session: Dict[str, Any]) -> str:
    """
    Select the starting model for a turn.  Sonnet handles iteration=1 (tool
    decisions, date parsing, free-form reasoning).  Post-tool iterations switch
    to Haiku inside _streaming_tool_loop when _last_check_avail is True.
    """
    return SONNET


# ---------------------------------------------------------------------------
# Date injection helper
# ---------------------------------------------------------------------------

def _build_date_prefix() -> str:
    """
    Return a date-context string for the system prompt.
    "Today is Thursday 12 June 2025. This week ends on Sunday 15 June.
    Next week starts Monday 16 June."
    """
    # B-09, two defects in one line. `date.today()` was SERVER-local, not
    # Europe/London — a day behind between 23:00 and midnight on a UTC container
    # under BST — and the inline Sunday arithmetic was seven days late. Both now
    # come from the one shared, explicitly-zoned implementation.
    from app.date_context import clinic_today as _clinic_today, week_anchors as _week_anchors
    today      = _clinic_today()
    weekday    = today.strftime("%A")
    date_str   = today.strftime("%d %B %Y")
    _a          = _week_anchors(today)
    this_sunday = _a.this_sunday
    next_monday = _a.next_monday
    return (
        f"Today is {weekday} {date_str}. "
        f"This week ends on Sunday {this_sunday.strftime('%d %B')}. "
        f"Next week starts Monday {next_monday.strftime('%d %B')}."
    )


# ---------------------------------------------------------------------------
# LLMStream class
# ---------------------------------------------------------------------------

class LLMStream:
    """
    Streaming Claude LLM integration for the Media Streams pipeline.

    run_turn() is called once per caller utterance from connection.py's llm_loop.
    It drives the complete turn: fast-path check, streaming LLM call,
    tool execution, and TTS chunk delivery.
    """

    def __init__(self) -> None:
        self._last_filler_at: float = 0.0
        # Latency-eval per-turn timing record, stashed by connection._llm_loop
        # before each run_turn. None when LATENCY_TIMING is OFF (default) so all
        # stamp sites short-circuit on a falsy check.
        self._timing = None

    async def run_turn(
        self,
        user_text: str,
        session: Dict[str, Any],
        call_sid: Optional[str],
        stream_sid: Optional[str],
        tts_text_queue: asyncio.Queue,
        audio_out_queue: asyncio.Queue,
        websocket: Any,
        on_transfer: Optional[Callable[[], Coroutine]] = None,
    ) -> None:
        """
        Run one caller turn end-to-end.

        Steps:
          1. Try fast-path resolution
          2. If matched (needs_llm=False): enqueue response, return
          3. If matched (needs_llm=True): enqueue interim, fall through to LLM
          4. Select model (SONNET or HAIKU)
          5. Build system prompt with date prefix
          6. Stream Claude response through ResponseChunker -> tts_text_queue
          7. Handle tool calls (buffered, then re-stream after result)
          8. GPT-4.1-mini fallback on Claude 529/500
          9. Update conversation history and session
        """
        # Obs transcript: mark where this caller turn begins. Everything spoken
        # from here until _append_history runs belongs after the caller's line,
        # and the assistant side is appended live from the TTS loop — so the
        # caller's line has to be inserted back at this mark, not appended.
        _obs_turns.mark_turn_start(session)

        # ── Step 1-3: Fast-path ──────────────────────────────────────────
        fp_result = try_fast_path(session, user_text)
        if fp_result is not None:
            await tts_text_queue.put(fp_result.response_text)
            _advance_fp_state(session, fp_result.turn_type)
            if not fp_result.needs_llm_followup:
                # Pure fast-path (no LLM tokens): tag it so it never pollutes
                # the LLM TTFT / chunk-gate stats. t1==t2 at the queue put.
                if self._timing is not None:
                    self._timing.path = "fast_path"
                    self._timing.stamp("t1")
                    self._timing.stamp("t2")
                # Update history with fast-path exchange
                _append_history(session, user_text, fp_result.response_text)
                await save_session(call_sid, session)
                return
            # needs_llm_followup=True: interim phrase already queued.
            # Mark it so _one_streaming_call strips the duplicate from the
            # start of the LLM response (BUG 2 fix).
            session["interim_played"] = True

        # ── V5 unspoken-slot follow-up (deterministic) ───────────────────
        # After slots were offered, "anything later?" / a specific unspoken
        # time must be answered from session["available_days"] − last_offered,
        # not by the model (which anchors on what it already said) and not by
        # re-fetching check_availability (which leads with the earliest again).
        try:
            from app.tools.slot_followup import try_unspoken_followup_speech
            _follow_speech = try_unspoken_followup_speech(session, user_text)
        except Exception:
            logger.exception("[ms_llm] unspoken slot follow-up failed — falling through")
            _follow_speech = None
        if _follow_speech:
            if self._timing is not None:
                self._timing.path = "slot_followup"
                self._timing.stamp("t1")
                self._timing.stamp("t2")
            await tts_text_queue.put(_follow_speech)
            _append_history(session, user_text, _follow_speech)
            await save_session(call_sid, session)
            logger.info(
                "[ms_llm] unspoken slot follow-up spoken call_sid=%s text=%r",
                call_sid, _follow_speech[:120],
            )
            return

        # ── Step 4: Model selection ──────────────────────────────────────
        model = _pick_model(session)
        # latency-eval: record the model on the timing record (None when OFF).
        if self._timing is not None:
            self._timing.model = model

        # ── The caller's opening utterance ───────────────────────
        # Recorded here, immediately before the prompt is built, because
        # `conversation_history` is appended only AFTER the turn completes
        # (`_append_history`). On turn 1 — the turn whose opening decides
        # whether the reason question should be asked at all — history is
        # still empty, so reading the opening back out of it is impossible.
        #
        # Set once and never overwritten: "opening" means the first thing
        # the caller said, not the most recent thing.
        # A bare "hi" is a greeting, not an opening, and latching it would
        # spend the one shot this gets on a turn that says nothing. Defer past
        # at most two such turns, then take whatever arrives - an unbounded
        # search would let a quiet caller move the "opening" to the middle of
        # the call, which is not what any reader of it expects.
        _ou = (user_text or "").strip()
        if _ou and not session.get("opening_utterance"):
            from app.media_streams.first_turn_extractor import (
                opening_is_substantive as _ou_ok,
            )
            _probes = int(session.get("_opening_probe_count") or 0)
            if _ou_ok(_ou) or _probes >= 2:
                session["opening_utterance"] = _ou
            else:
                session["_opening_probe_count"] = _probes + 1

        # Commit the opening reason HERE, not only on the booking-ack path.
        # CAa23b1ed5 (demo line, first live call on this fix) is why: the
        # condition-led rung told the model NOT to say "Right -", so the
        # injector never ran, so `_reason_already_known` - the only caller of
        # `commit_opening_reason` - never ran either, and the call finished
        # with "pre-summary reason: collected=None session=None". The two
        # halves of the fix were fighting: the better the question is
        # suppressed, the less often the reason gets written.
        #
        # That matters because A2 refuses a booking carrying no reason and its
        # refusal text orders the model to ask - AFTER slots, which BOOKING
        # STEPS 1b forbids outright. Recording it at the point the utterance
        # is read makes the write independent of which path the turn takes.
        #
        # Gated to clinics that opt into a reason question, so a clinic that
        # never asks (theorem, theorem_v3) keeps an empty reason as its own
        # correct outcome rather than silently gaining one.
        try:
            from app.media_streams.turn_handler import (
                _clinic_asks_its_own_reason_question as _asks_reason,
            )
            if _asks_reason(session):
                from app.media_streams.first_turn_extractor import (
                    commit_opening_reason as _commit_reason,
                )
                _commit_reason(session)
        except Exception:
            logger.debug("[ms_llm] opening-reason commit failed", exc_info=True)

        # ── Step 5: System prompt (two-block caching) ───────────────────
        # static_prompt: large, never changes within a call → cached.
        # dynamic_prompt: small per-turn state → sent uncached each turn.
        from app.prompts.susie_system_prompt import build_system_prompt_parts
        _static_prompt, _dynamic_prompt = build_system_prompt_parts(session)
        system_prompt = _static_prompt  # kept for any legacy references below

        # ── Step 6-8: LLM streaming with tool loop ───────────────────────
        history: List[dict] = session.setdefault("conversation_history", [])

        # Deep-copy so cache_control mutations never bleed back into session
        # history.  The previous shallow copy (list(history[...])) left dict
        # references intact, so cache_control blocks accumulated turn-over-turn
        # and hit Anthropic's hard limit of 4 by turn 7.
        messages: List[dict] = copy.deepcopy(list(history[-MAX_HISTORY_TURNS:]))
        messages.append({"role": "user", "content": user_text})

        # ── Prompt cache: strip then re-apply ────────────────────────────
        # CHANGE 1: remove every cache_control block from the working copy.
        # History may carry stale blocks from earlier turns (pre-fix) or from
        # tool-result messages whose content is already a list.  Wiping first
        # makes the count deterministic before we re-apply below.
        for _msg in messages:
            _mc = _msg.get("content")
            if isinstance(_mc, list):
                for _blk in _mc:
                    if isinstance(_blk, dict):
                        _blk.pop("cache_control", None)
            _msg.pop("cache_control", None)

        # CHANGE 2: re-apply cache_control to exactly one point in messages —
        # the last assistant message (Point B).  Point A is the system prompt,
        # applied inline at the API call site.  Two breakpoints total, always
        # within Anthropic's hard limit of 4 regardless of history length.
        for _msg in reversed(messages):
            if _msg.get("role") == "assistant":
                _mc = _msg.get("content")
                if isinstance(_mc, str):
                    # Anthropic rejects cache_control on empty text blocks.
                    if _mc.strip():
                        _msg["content"] = [{
                            "type":          "text",
                            "text":          _mc,
                            "cache_control": {"type": "ephemeral"},
                        }]
                elif isinstance(_mc, list):
                    # Find the last non-empty text block and tag it.
                    for _blk in reversed(_mc):
                        if (
                            isinstance(_blk, dict)
                            and _blk.get("type") == "text"
                            and _blk.get("text", "").strip()
                        ):
                            _blk["cache_control"] = {"type": "ephemeral"}
                            break
                break  # only the most recent assistant turn

        tools       = _build_claude_tools(session)
        full_reply  = ""     # assembled from all chunks for history
        transfer_initiated = False

        interim_played: bool = bool(session.pop("interim_played", False))

        try:
            full_reply, transfer_initiated = await self._streaming_tool_loop(
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                session=session,
                call_sid=call_sid,
                tts_text_queue=tts_text_queue,
                on_transfer=on_transfer,
                interim_played=interim_played,
                dynamic_prompt=_dynamic_prompt,
            )
        except Exception as exc:
            logger.error("[ms_llm] streaming_tool_loop error: %r", exc)
            full_reply = SAFE_FALLBACK_PHRASE
            await tts_text_queue.put(SAFE_FALLBACK_PHRASE)

        # ── Step 9: Update history ───────────────────────────────────────
        # Fix 2: deduplication — discard if LLM generated the same first 50
        # characters as the previous response (e.g. slot question asked twice).
        if full_reply.strip():
            _prev_resp = session.get("_last_llm_response", "")
            if (
                _prev_resp.strip()
                and full_reply.strip()[:50] == _prev_resp.strip()[:50]
            ):
                logger.info("[ms_llm] duplicate response discarded (matches previous)")
                full_reply = ""
            else:
                session["_last_llm_response"] = full_reply

        if not transfer_initiated:
            # Sanitize full_reply before extracting last_bot_prompt / last_question.
            # sanitise_response strips internal reasoning sentences that the LLM
            # sometimes narrates aloud (e.g. "The caller said...", "I'll pick the
            # three with the most slots...").  These sentences are already stripped
            # per-chunk before reaching TTS, but full_reply is assembled from raw
            # tokens — without this step, reasoning text fills last_bot_prompt[:200]
            # before the actual spoken response, causing _parse_v3_slot_options to
            # miss numbered options and v3_awaiting_slot_selection to stay unset,
            # which in turn fires the watchdog 4.5 s after TTS ends instead of 10 s.
            #
            # Computed BEFORE _append_history (2026-07-29) so the obs record can be
            # given the spoken form. Same single sanitise_response call as before,
            # just ordered earlier — no extra pass over the reply.
            # What the caller HEARD, accumulated per-chunk as Gate 5 released it
            # (_record_spoken). This replaces a second sanitise_response() pass
            # over the raw full_reply, which was wrong twice over:
            #
            #   1. sanitise_response IS the gate chain, and several gates are
            #      stateful. Running it again at turn end fired them a second
            #      time on text they had already processed. Gate 5f, having
            #      re-steered per-chunk, took its already-fired branch and
            #      returned "" for the WHOLE reply — so last_bot_prompt went
            #      empty at the exact moment the caller had just been asked the
            #      confirmation question, and every later book_appointment was
            #      blocked as "question not asked". CA7e389a47.
            #   2. Even when it did not blank, it described the model's
            #      GENERATION, not the delivery. Gate 5f rewrites what is
            #      spoken; the raw text still claimed "All booked". Feeding that
            #      back as the model's own memory is why it kept re-claiming a
            #      booking it had never actually announced.
            #
            # Fallback to the old derivation when nothing was recorded: several
            # paths speak without passing through the chunk seam (SAFE_FALLBACK,
            # the guaranteed fallback, the Gate-5 fallback, the ack filler). A
            # turn carried entirely by one of those must not blank the state.
            # B-76: did ANY chunk go through the chunk seam this turn? The key
            # is created by _record_spoken and left in place (empty) by
            # _unrecord_spoken, so its PRESENCE separates "the seam was used and
            # every chunk was then dropped" from "nothing ever reached the seam".
            # Without this the fallback below reaches for full_reply and puts the
            # unheard sentence straight back into last_bot_prompt - which is the
            # entire defect.
            _seam_used = isinstance(session.get(SPOKEN_CHUNKS_KEY), list)
            _spoken_turn = (session.pop("_spoken_this_turn", "") or "").strip()
            # B-76: pop the backing store WITH it. Left behind, a drop arriving
            # from the async TTS loop after turn end would rebuild
            # `_spoken_this_turn` from a finished turn and resurrect it into the
            # next one. _unrecord_spoken is a no-op once this is gone, which is
            # the intended behaviour.
            session.pop(SPOKEN_CHUNKS_KEY, None)
            _nothing_spoken = _seam_used and not _spoken_turn
            if _spoken_turn:
                _display_reply = _spoken_turn
            elif _nothing_spoken:
                # Every chunk was dropped before TTS. The caller heard nothing,
                # so the "was X said out loud this turn" latches below must all
                # see nothing - that is the honest answer and the safe one.
                _display_reply = ""
            else:
                _display_reply = sanitise_response(full_reply, session)
            # assistant_text -> conversation_history + obs (what was heard).
            # raw_text -> session["turns"] only, which feeds live clinics' owner
            # summaries and SMS windows and stays on the raw shape.
            _append_history(
                session, user_text, _display_reply, raw_text=full_reply
            )
            # SPEC 4: store the phonetic (TTS-substituted) form so that
            # last_bot_prompt reflects what was actually spoken — used by the
            # silence watchdog re-ask and logging.
            # B-76: a turn that spoke NOTHING must not blank these. They mean
            # "the last thing Susie said", and if this turn said nothing then
            # that is still the previous turn's question - which the caller is
            # still answering. Blanking them fails the write gates just as
            # surely as the unheard sentence did: on JV CAe84b871b the caller
            # had approved the move twice and reschedule_appointment was still
            # refused with "the move confirmation question was never asked".
            if not _nothing_spoken:
                session[F_LAST_BOT_PROMPT] = _apply_tts_subs(
                    _display_reply
                )[:200]
            else:
                logger.info(
                    "[ms_llm] B-76: every chunk was dropped before TTS — "
                    "last_bot_prompt left as %r",
                    (session.get(F_LAST_BOT_PROMPT) or "")[:60],
                )
            # B-42: was the looked-up patient's NAME actually said out loud this
            # turn? Read from _display_reply (what was HEARD) and not from
            # full_reply, and deliberately NOT from last_bot_prompt, which is
            # capped at 200 chars — a readback long enough to lose the name to
            # that cap is exactly the turn where the caller most needs it.
            _note_lookup_name_spoken(session, _display_reply)
            # B-54: and was the matched appointment's DATE said out loud? Same
            # source and the same reasoning — the name alone cannot distinguish
            # one caller's initial from their follow-up.
            _note_lookup_slot_spoken(session, _display_reply)
            # And whether the REASON question was put to the caller this turn —
            # same source, same reason: rule 1b's "ask ONCE" needs a latch, not
            # an instruction. CA86c320ef asked it twice in two different forms.
            note_reason_question_asked(session, _display_reply)
            # Store only the question portion in F_LAST_QUESTION.
            # F_LAST_BOT_PROMPT keeps the full response for fast-path trigger
            # matching; F_LAST_QUESTION is narrowed to the actual question
            # sentence so the re-ask watchdog only replays real questions.
            # B-76: same reasoning - the outstanding question is unchanged by a
            # turn that produced no audio.
            if not _nothing_spoken:
                session[F_LAST_QUESTION] = _question_from_response(_display_reply)
            # B1.2: write CTA closes the slot-selection window (reschedule has no
            # Spec J name-ask to clear it). Silence must not re-ask for a day.
            _clear_slot_window_after_write_cta(session)

        session["turn_count"] = session.get("turn_count", 0) + 1
        await save_session(call_sid, session)

    # -----------------------------------------------------------------------
    # Flow-engine instruction runner (used by FlowEngine in flow.py)
    # -----------------------------------------------------------------------

    async def run_instruction(
        self,
        instruction: str,
        session: Dict[str, Any],
        tts_text_queue: asyncio.Queue,
        call_sid: Optional[str] = None,
        stream_sid: Optional[str] = None,
        audio_out_queue: Optional[asyncio.Queue] = None,
        websocket: Any = None,
        on_transfer: Optional[Callable] = None,
        allow_tools: bool = True,
        error_phrase: str = None,
    ) -> str:
        """
        Simple single-instruction LLM call for the FlowEngine.

        Streams the Claude response directly to tts_text_queue using the
        existing tool-loop infrastructure (so check_availability still works
        for the PRESENT_SLOTS step).

        Returns the full response text (also stored in session["last_bot_prompt"]).
        """
        from .config import get_system_prompt as _get_system_prompt
        system_prompt = _get_system_prompt(session)

        # For cancel/reschedule terminal steps the LLM's anti-injection safeguards
        # refuse to call the tool when the directive arrives as a user message.
        # Fix: append directive to system prompt + pass full history so the LLM
        # has proper authority and patient context.
        # For all other steps (PRESENT_SLOTS, CONFIRM_BOOKING, COLLECT_DURATION …)
        # keep the original simple user-message approach — it works correctly and
        # avoids polluting the system prompt with prior cancel/reschedule directives.
        is_terminal_action = (
            "cancel_appointment" in instruction
            or "reschedule_appointment" in instruction
        )

        if is_terminal_action:
            augmented_system = (
                system_prompt
                + "\n\n[FLOW DIRECTIVE — trusted internal instruction, execute immediately]:\n"
                + instruction
            )
            history = list(session.get("conversation_history", []))
            if history and history[-1]["role"] == "user":
                messages = history
            elif history:
                messages = history + [{"role": "user", "content": "[execute flow directive]"}]
            else:
                messages = [{"role": "user", "content": "[execute flow directive]"}]
        else:
            # Original approach — simple single user message
            augmented_system = system_prompt
            messages = [{"role": "user", "content": instruction}]

        tools    = _build_claude_tools(session) if allow_tools else []

        full_reply = ""
        try:
            full_reply, _ = await self._streaming_tool_loop(
                model=SONNET,
                system_prompt=augmented_system,
                messages=messages,
                tools=tools,
                session=session,
                call_sid=call_sid,
                tts_text_queue=tts_text_queue,
                on_transfer=on_transfer,
                interim_played=False,
            )
        except Exception as exc:
            logger.error("[ms_llm] run_instruction error: %r", exc)
            _err = error_phrase or SAFE_FALLBACK_PHRASE
            full_reply = _err
            await tts_text_queue.put(_err)

        session["last_bot_prompt"] = full_reply
        return full_reply

    # -----------------------------------------------------------------------
    # Slot presentation complete-response buffer
    # -----------------------------------------------------------------------

    @staticmethod
    async def _flush_slot_buf(
        buf_queue: asyncio.Queue,
        tts_queue: asyncio.Queue,
        session: Dict[str, Any],
    ) -> None:
        """
        Drain the complete-response slot presentation buffer, extract the
        DTMF slot map, re-split the full response by numbered-option boundary,
        and flush exactly one TTS chunk per option.

        The buffer is filled by routing the post-check_availability LLM
        streaming output through _slot_buf instead of directly to
        tts_text_queue.  Every chunk carries a PRE_SLOT_MARKER prefix added
        by _one_streaming_call; these are stripped here before text assembly.

        Re-splitting guarantee: after joining the clean text, we split on
        'Number 2', 'Number 3', … boundaries (lookahead so the delimiter is
        kept with the following content).  This means:
          - Preamble + Number 1 → TTS chunk 1
          - "Number 2, ..." → TTS chunk 2
          - "Number 3, ..." → TTS chunk 3
        regardless of how the streaming ResponseChunker originally cut the
        text.  Without this, a short response could collapse all options into
        one large chunk that split_tts_text() then re-cuts at an em-dash,
        silently dropping the last option's spoken text.

        Empty chunks (after stripping) produce a WARNING and are never
        forwarded to TTS so no silent data loss can occur.
        """
        import re as _re  # noqa: PLC0415 — local import to avoid module-level cycle

        # ── 1. Drain the slot buffer ─────────────────────────────────────────
        raw_chunks: List[str] = []
        while True:
            try:
                raw_chunks.append(buf_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        logger.info(
            "[ms_gate5] slot buf complete-response flush: %d raw chunk(s) from buffer",
            len(raw_chunks),
        )

        # ── 1b. Deterministic offer, if one was built for this payload ───────
        # Everything from section 3a to section 6 below repairs text a model
        # wrote: capping the options, parsing the speech back into slots,
        # regexing the keypad map out of the sentence, re-splitting on
        # "Number N", reconciling the "a few others that day" claim, and
        # warning when those disagree. None of it applies when the sentence was
        # built from the payload — the record and the map came out of the same
        # function as the words.
        #
        # The model's buffered text is DISCARDED, deliberately and noisily:
        # this path must never blend the two, because a half-model half-code
        # sentence is the one shape no record could describe.
        _prebuilt = session.pop("_slot_offer_prebuilt", None)
        if isinstance(_prebuilt, dict) and _prebuilt.get("chunks"):
            _det_chunks = [str(c) for c in _prebuilt["chunks"] if str(c).strip()]
            _det_slots = [s for s in (_prebuilt.get("slots") or []) if s.get("start")]
            _det_map = dict(_prebuilt.get("dtmf_map") or {})
            logger.info(
                "[ms_gate5] deterministic offer in force — %d chunk(s); the "
                "model's %d buffered chunk(s) are discarded (%r)",
                len(_det_chunks), len(raw_chunks),
                (" ".join(raw_chunks))[:80],
            )

            # The record. Cumulative FIRST, as everywhere else in this family:
            # last_offered_slots is about to be overwritten and is the only
            # other trace that these were spoken (B-78b).
            from app.tools.slot_followup import record_spoken_slots
            record_spoken_slots(session, _det_slots)
            # TWO RECORDS, AND THEY ARE NOT THE SAME LIST.
            #
            # The cumulative record above holds every time the sentence named.
            # `last_offered_slots` is "the offer on the table" and is read BY
            # POSITION -- `_resolve_slot_iso` maps an ordinal ("the second
            # one") onto it, and on multi_day a position means a DAY, which is
            # also what the day-keyed DTMF map means. receptionist_tools calls
            # that "not negotiable" and it is right.
            #
            # Writing every slot here was correct only while a multi_day offer
            # carried ONE time per day: the two lists were then identical. At
            # two times per day they diverge, and "the second one" would pick
            # the first day's SECOND time while pressing 2 picks the second
            # DAY -- one utterance meaning two different slots depending on
            # whether the caller spoke or pressed.
            _det_positional: list = []
            _det_pos_labels: list = []
            _det_seen_dates: set = set()
            for _s in _det_slots:
                if _prebuilt.get("mode") == "multi_day":
                    _sd = _s.get("date")
                    if _sd in _det_seen_dates:
                        continue
                    _det_seen_dates.add(_sd)
                _det_positional.append(
                    {"start": _s["start"], "end": _s.get("end") or ""}
                )
                _det_pos_labels.append(_s.get("spoken"))
            session["last_offered_slots"] = _det_positional
            session["slot_labels"] = _det_pos_labels
            # B-126: this record is a transcript, not a projection, so no day
            # of it needs marking as unsafe to reason from.
            from app.tools.slot_followup import LOSSY_SPOKEN_DAYS_KEY
            session.pop(LOSSY_SPOKEN_DAYS_KEY, None)

            if len(_det_map) >= 2:
                session["v3_dtmf_slot_map"] = _det_map
                session["v3_awaiting_slot_selection"] = True
                session["v3_slot_map_armed_turn"] = session.get("turn_count", 0)
                session.pop("v3_slot_map_superseded", None)
                session.pop("slots_stale_modality_switch", None)
                session["_slot_chunks_sent"] = len(_det_chunks)
                session["_slot_chunks_inhibited"] = 0
            else:
                session.pop("_slot_chunks_sent", None)
                session.pop("_slot_chunks_inhibited", None)
            # `day_iso` is set only by the multi_day builder, to the PAYLOAD's
            # first day -- the same value section 4 writes, and the meaning
            # turn_handler documents. single_day sends None and keeps the
            # first-spoken-slot behaviour it shipped with, where the two
            # coincide anyway. See the anchor note at the multi_day build site.
            _det_day = _prebuilt.get("day_iso")
            if not _det_day and _det_slots and _det_slots[0].get("date"):
                _det_day = _det_slots[0]["date"]
            if _det_day:
                session["v3_last_offered_day_iso"] = _det_day

            for _i, _c in enumerate(_det_chunks):
                logger.info(
                    "[ms_gate5] deterministic TTS chunk %d/%d: %r — len=%d",
                    _i + 1, len(_det_chunks), _c[:60], len(_c),
                )
                await tts_queue.put(_c)
                session["_slotbuf_emitted"] = True
            return

        if not raw_chunks:
            return

        # ── 2. Strip PRE_SLOT_MARKER and log every boundary decision ─────────
        clean_chunks: List[str] = []
        for i, rc in enumerate(raw_chunks):
            c = rc[len(PRE_SLOT_MARKER):] if rc.startswith(PRE_SLOT_MARKER) else rc
            c = c.strip()
            logger.info(
                "[ms_gate5] slot buf chunk %d: %r — len=%d — sending=%s",
                i, c[:60], len(c), len(c) > 0,
            )
            if not c:
                logger.warning(
                    "[ms_gate5] slot buf chunk %d: EMPTY after stripping — not forwarded",
                    i,
                )
            else:
                clean_chunks.append(c)

        if not clean_chunks:
            logger.warning("[ms_gate5] slot buf: no clean chunks after stripping — nothing sent to TTS")
            return

        # ── 3. Assemble complete text for slot map + re-split ────────────────
        _joined = " ".join(clean_chunks)

        # The weekday must agree with the date. sanitise_response already ran
        # this per STREAMED chunk, but a date split across two chunks matches
        # nothing there — and this is the path the defect fired on, where the
        # model wrote "Tuesday 26th August" over a payload that said Wednesday.
        # Idempotent, so the second pass costs nothing when the first caught it.
        from .turn_handler import _correct_weekday_against_known_dates
        _joined = _correct_weekday_against_known_dates(_joined, session)

        # ── 3a. Cap the spoken option count, and record what was read out ───
        # CA6b90c3a2 (24 Aug, jv_v1): the first offer was correctly capped at
        # two, then the caller said "go ahead", the model re-called
        # check_availability, hit the already_retrieved guard — which hands it
        # the FULL available_days and says "present the existing slots" — and
        # read out all five of Tuesday's times in one breath.
        #
        # Two things were wrong and the noisy one is the lesser. The count is
        # enforced here because no prompt rule has ever won that argument. The
        # worse defect is that nothing recorded what the model had just said:
        # the cumulative spoken record (B-78b) only ever learned from the
        # deterministic follow-up path, so a model-composed readout left it
        # stale. The caller then asked for "the others" and was offered 18:30
        # and 19:15 — which they had heard forty seconds earlier — and then
        # 20:00, which they had heard too.
        from app.tools.slot_followup import (
            MAX_SPOKEN_OPTIONS,
            cap_spoken_options,
            day_key_of,
            day_named_in_readout,
            extract_slot_options,
            option_label_candidates,
            record_spoken_slots,
            resolve_all_spoken_times,
            resolve_spoken_options,
            unspoken_remain_on_day,
        )

        _cap = int(session.get("_slot_spoken_cap") or MAX_SPOKEN_OPTIONS)
        _joined, _n_read, _n_kept = cap_spoken_options(_joined, _cap)
        _was_capped = _n_read != _n_kept
        if _was_capped:
            logger.warning(
                "[ms_gate5] slot buf: TRIMMED readout %d option(s) -> %d "
                "(cap=%d) — after=%r",
                _n_read, _n_kept, _cap, _joined[:160],
            )

        # Resolve the options actually about to be spoken back to real slots.
        # All-or-nothing: a partial resolution would write a last_offered_slots
        # that disagrees with the speech, and that is worse than not writing.
        _spoken_opts = None
        # Candidates, not the DTMF label: the multi-day readout puts the day
        # INSIDE the option ("Thursday 27th August - half past seven in the
        # evening") and extract_slot_options truncates at the dash, throwing
        # away the only part that can match a slot. The keypad map below keeps
        # its own extraction — its label is injected as a synthetic transcript
        # and must not change.
        _spoken_labels = list(option_label_candidates(_joined).values())
        # WHICH day these bare times belong to. The sentence about to be spoken
        # outranks `_slot_presented_day`, which is inherited from the previous
        # payload's first day and is stale the moment the caller moves to a
        # different one — B-93, CA903bd6ef (26 Aug 2026, vital_edge):
        #
        #   offer:  "Number 1, Monday 31st August - one in the afternoon.
        #            Number 2, Tuesday 1st September - one in the afternoon."
        #   caller: "the second one please"
        #   tool:   BLOCKED (already retrieved) -> no first_day -> the fallback
        #           takes last_offered_slots[0] = MONDAY
        #   spoken: "Tuesday 1st September - Number 1, one in the afternoon ..."
        #   record: ['2026-08-31T13:00', '2026-08-31T14:00', '2026-08-31T15:00']
        #
        # She said Tuesday and wrote Monday three times over, and the read-back
        # the caller confirmed ("Tuesday the 1st at three") then disagreed with
        # the only machine-readable record of what was offered. Both days held
        # the same afternoon times, so nothing downstream could notice.
        #
        # Only overrides when the readout names exactly ONE known day; a
        # multi-day readout or a paraphrase returns None and nothing changes.
        _named_day = day_named_in_readout(session.get("available_days"), _joined)
        _prefer_day = _named_day or session.get("_slot_presented_day")
        if _named_day and _named_day != session.get("_slot_presented_day"):
            logger.info(
                "[ms_gate5] slot buf: readout names %s — preferring it over "
                "inherited presented_day=%r (B-93)",
                _named_day, session.get("_slot_presented_day"),
            )
        _all_heard: list = []
        if _spoken_labels:
            # Every time actually named, for the cumulative record only — see
            # resolve_all_spoken_times. Never feeds last_offered_slots.
            _all_heard = resolve_all_spoken_times(
                session.get("available_days"),
                _spoken_labels,
                prefer_day=_prefer_day,
            )
            _r = resolve_spoken_options(
                session.get("available_days"),
                _spoken_labels,
                prefer_day=_prefer_day,
            )
            if _r and len({day_key_of(_s) for _s in _r}) == 1:
                _spoken_opts = _r
            elif _r:
                # A multi-day readout IS a real offer, and the caller really did
                # hear those times — so the cumulative spoken record must learn
                # from it. That record is a flat set of ISO starts and
                # `unspoken_remain_on_day` filters it by day itself, so spanning
                # days is not a problem for it. Not recording here is what made
                # "what else have you got?" re-offer times heard 40s earlier.
                #
                # `last_offered_slots` and `slot_labels` are deliberately NOT
                # written: `_resolve_slot_iso` indexes that record BY POSITION
                # for an ordinal choice, and `slot_labels` is times-only, which
                # across two days is ambiguous to a caller. Widening those is a
                # separate change with its own consumers to audit.
                record_spoken_slots(session, _all_heard or _r)
                logger.info(
                    "[ms_gate5] slot buf: spoken options span %d days — "
                    "recorded as heard, offer record left unchanged",
                    len({day_key_of(_s) for _s in _r}),
                )
            else:
                logger.info(
                    "[ms_gate5] slot buf: could not resolve spoken option(s) "
                    "%r against available_days — offer record left unchanged",
                    _spoken_labels[:4],
                )
        if _spoken_opts:
            # Cumulative FIRST: last_offered_slots is about to be overwritten
            # and is the only other record that these were ever spoken.
            #
            # And it records EVERY time named, not one per option. An option can
            # carry two ("Number 1, Monday 7th — ten in the morning. Or two in
            # the afternoon"); the caller heard both, but _spoken_opts holds one
            # per option because last_offered_slots is indexed BY POSITION below.
            # On CAcb5988e0 the second time was re-offered 19s after being read
            # out because only the first was ever recorded.
            record_spoken_slots(session, _all_heard or _spoken_opts)
            session["last_offered_slots"] = [
                {"start": _s["start"], "end": _s.get("end") or ""}
                for _s in _spoken_opts
            ]
            session["slot_labels"] = [_s.get("spoken") for _s in _spoken_opts]
            logger.info(
                "[ms_gate5] slot buf: %d spoken option(s) recorded as offered — %s",
                len(_spoken_opts), [_s.get("start") for _s in _spoken_opts],
            )
        elif not _spoken_labels:
            # B-100, CA315e501a (27 Aug 2026). Everything above reads the
            # cumulative spoken record through option_label_candidates, which
            # is driven by "Number N" anchors. A readout with no numbering --
            # "Friday 28th August — the available time is two in the
            # afternoon." -- yields no anchors, so NOTHING is recorded, even
            # though the caller plainly heard a time.
            #
            # That record is what B-98's band-spent rule reads. On that call
            # the very next lookup could not tell the 2pm had been spoken, did
            # not open the day, and re-offered the same 2pm; the caller had to
            # name "midday" themselves to reach the slot the band was hiding.
            #
            # ONE slot only, and from the PAYLOAD rather than the sentence.
            # last_offered_slots is what check_availability presented, so with
            # exactly one entry there is nothing else the readout could have
            # been about. Recording more than that on a guess is the dangerous
            # direction: a slot wrongly marked heard is a slot never offered
            # again, which is the B-97 family coming back through this door.
            _payload_one = session.get("last_offered_slots") or []
            if (
                isinstance(_payload_one, list)
                and len(_payload_one) == 1
                and (_payload_one[0] or {}).get("start")
            ):
                record_spoken_slots(session, _payload_one)
                logger.info(
                    "[ms_gate5] slot buf: un-numbered readout — recorded the "
                    "one slot the payload presented as heard: %s (B-100)",
                    _payload_one[0].get("start"),
                )

        # ── 3b. Reconcile the "a few others that day" claim with the data ────
        # The formatter is a language model being shown a template; on
        # 24 Aug 2026 it copied the more_times=true example verbatim onto a day
        # that had exactly the two slots it had just listed, and told the caller
        # there were more. That sentence is a claim about the provider's
        # calendar, so it is decided here from the tool result, not by the
        # model. Runs BEFORE slot-map extraction and the Number re-split so both
        # operate on the text the caller will actually hear.
        from app.tools.slot_followup import reconcile_extra_slots_claim

        _more_times = bool(session.get("_slot_more_times"))
        _n_offered = int(session.get("_slot_n_offered") or 2)
        _allow_append = session.get("_slot_presentation_mode") == "single_day"
        if _spoken_opts:
            # Exact, and strictly better than the tool flag: the cumulative
            # record knows what EARLIER turns already read out, which no single
            # tool result does. A follow-up batch that finishes a day reads
            # False here even though its own payload cannot tell.
            #
            # It REPLACES the flag rather than lowering it, so it has to know
            # everything the flag knows. B-112: it did not -- a slot the
            # caller's time-of-day band removed is never in available_days for
            # it to walk, so a fully-spoken pair of survivors read as a
            # finished day and the tail was dropped from a day holding five
            # more. The hidden count is read there now; do not reintroduce a
            # second owner for this here.
            _more_times = unspoken_remain_on_day(session, day_key_of(_spoken_opts[0]))
            _n_offered = len(_spoken_opts)
            # Every option resolved to ONE day, so "that day" has a referent.
            # A stronger guarantee than presentation_mode, and it is what lets
            # the tail reach the already_retrieved path, which carries no
            # presentation_mode at all. The multi_day case a74f60c8 guards
            # against cannot get here: two days named means the day-key set has
            # two members, _spoken_opts stays None, and the presentation_mode
            # gate still decides.
            _allow_append = True
        elif _was_capped:
            # We removed options ourselves, so more on that day exist by
            # construction — even though we could not name them.
            _more_times = True
            _n_offered = _n_kept
        _reconciled, _action = reconcile_extra_slots_claim(
            _joined, _more_times, _n_offered,
            allow_append=_allow_append,
        )
        if _action == "stripped":
            # Loud on purpose: this is the model asserting availability that
            # does not exist, and it belongs in the call record.
            logger.warning(
                "[ms_gate5] slot buf: REMOVED unfounded extra-availability "
                "claim (more_times=False) — before=%r after=%r",
                _joined[:160], _reconciled[:160],
            )
        elif _action == "appended":
            logger.info(
                "[ms_gate5] slot buf: appended more-times tail "
                "(more_times=True, n_offered=%d)", _n_offered,
            )
        _joined = _reconciled

        # ── 3b-ii. Say why these times are outside the band they asked for ──
        # B-117. B-98 opened a band this caller has used up and B-116 leads
        # with the times they have not heard, which for a "morning" request
        # means afternoons. Unexplained that reads as not listening.
        # Sentence only: it must not change which times were chosen.
        _band_spent = str(session.get("_slot_band_spent_label") or "")
        if _band_spent and _allow_append:
            from app.tools.slot_followup import acknowledge_spent_band
            _joined, _ack = acknowledge_spent_band(_joined, _band_spent)
            if _ack == "prepended":
                logger.info(
                    "[ms_gate5] slot buf: acknowledged the spent band (%s) "
                    "before reading times outside it (B-117)", _band_spent,
                )

        # ── 3c. Name the further dates matching the caller's weekday ─────────
        # Same contract as 3b and for the same reason: which dates exist is a
        # fact about the provider's calendar, decided here from the tool
        # result. B-109/B-110 wrote guidance asking the formatter to do this
        # and it never did -- its prompt does not mention the field and, since
        # 8de7e7d0, forbids it from mentioning further availability at all.
        # Runs AFTER the more_times reconcile so it appends to the final text,
        # and after the write-guards, which have already run upstream.
        from app.tools.slot_followup import append_other_dates_offer

        _joined, _od_action = append_other_dates_offer(
            _joined, session.get("_slot_other_dates"),
        )
        if _od_action == "appended":
            logger.info(
                "[ms_gate5] slot buf: named %d further date(s) the payload held "
                "back (B-111)", len(session.get("_slot_other_dates") or []),
            )

        # ── 4. Slot map extraction (Bug 7 fix) ───────────────────────────────
        # Runs on the complete assembled response so every option's date string
        # is present and untruncated (last_bot_prompt is capped at 200 chars).
        # Extracted by slot_followup so the cap in 3a, this map and the TTS
        # re-split below all count options with the SAME pattern — a cap that
        # counted differently would trim to a boundary the keypad does not
        # share. Re-run on the RECONCILED text rather than reusing 3a's result:
        # reconcile can strip or append a sentence, and the map must describe
        # what is actually spoken.
        _slot_map: dict = extract_slot_options(_joined)
        _slot_map_count = 0
        if len(_slot_map) >= 2:
            session["v3_dtmf_slot_map"] = _slot_map
            session["v3_awaiting_slot_selection"] = True
            # Stamp the turn that armed this window. run_turn's write-CTA
            # cleanup runs LATER IN THIS SAME TURN and would otherwise wipe
            # a window whose options the caller has only just heard — see
            # _clear_slot_window_after_write_cta. turn_count is incremented
            # at the very end of run_turn, so a stamp equal to the current
            # turn_count means "armed by the reply being spoken right now".
            session["v3_slot_map_armed_turn"] = session.get("turn_count", 0)
            # B-80: this map describes the readout being spoken right now,
            # so any staleness mark left by an earlier deterministic
            # follow-up no longer applies.
            session.pop("v3_slot_map_superseded", None)
            _slot_map_count = len(_slot_map)
            # Fresh slots have now been presented for the CURRENT modality,
            # so the "stale after modality switch" mark no longer applies —
            # clear it so normal open-availability suppression resumes.
            session.pop("slots_stale_modality_switch", None)
            # Save the first offered day's ISO date for FAQ-detour recovery.
            # If the caller asks a FAQ mid-selection (clearing the slot map),
            # this lets CALL STATE redirect check_availability to only that day.
            _av_days = session.get("available_days") or []
            if _av_days:
                session["v3_last_offered_day_iso"] = _av_days[0]["date"]
                logger.info(
                    "[ms_gate5] v3_last_offered_day_iso=%r saved",
                    _av_days[0]["date"],
                )
            logger.info(
                "[ms_gate5] slot map extracted on complete response "
                "(%d option(s)) — DTMF standby: %r",
                _slot_map_count,
                _slot_map,
            )

        # If no new numbered options were found this turn (single-slot response,
        # date-specific re-check, etc.) clear any stale slot map so connection.py
        # does not re-arm DTMF pointing at options the caller never heard.
        # NOTE: v3_last_offered_day_iso is intentionally NOT cleared here —
        # it must survive the FAQ detour so CALL STATE can direct the LLM back
        # to the correct day on the caller's next booking confirmation.
        if _slot_map_count == 0:
            if session.pop("v3_dtmf_slot_map", None) is not None:
                session.pop("v3_awaiting_slot_selection", None)
                session.pop("v3_slot_map_armed_turn", None)
                logger.info(
                    "[ms_gate5] slot buf: no numbered options this turn"
                    " — cleared stale slot map (v3_last_offered_day_iso preserved)"
                )

        # ── 5. Re-split by numbered-option boundary ──────────────────────────
        # Split before "Number 2", "Number 3", … (lookahead keeps the
        # delimiter with the following content).  Preamble + Number 1 stay
        # together in the first chunk.  Responses with no numbered options
        # (single-day, time selection) are returned as a single chunk.
        tts_chunks = _re.split(r"(?=\bNumber\s+[2-9]\b)", _joined, flags=_re.IGNORECASE)
        tts_chunks = [c.strip() for c in tts_chunks if c.strip()]

        if not tts_chunks:
            tts_chunks = [_joined.strip()] if _joined.strip() else []

        logger.info(
            "[ms_gate5] slot buf sending %d TTS chunk(s) after Number-boundary re-split",
            len(tts_chunks),
        )

        # ── 6. Warn if chunk count mismatches DTMF map ───────────────────────
        if _slot_map_count > 0 and len(tts_chunks) != _slot_map_count:
            logger.warning(
                "[ms_gate5] slot buf MISMATCH: %d TTS chunk(s) vs %d DTMF map "
                "entries — some options may not be spoken — map=%r",
                len(tts_chunks), _slot_map_count,
                session.get("v3_dtmf_slot_map"),
            )

        # ── 7. Arm slot-chunk inhibit tracking (CODE SPEC AC) ───────────────
        # Record the number of TTS chunks being sent so _tts_loop can detect
        # when ALL of them are discarded by tts_inhibit (barge-in before the
        # patient heard a single option).  In that case the slot map is stale
        # and must be cleared so the next availability check starts fresh.
        # Only armed when a real DTMF slot map was extracted (_slot_map_count≥2);
        # single-slot responses have no map to clear.
        if _slot_map_count >= 2:
            session["_slot_chunks_sent"]      = len(tts_chunks)
            session["_slot_chunks_inhibited"] = 0
            logger.info(
                "[ms_gate5] slot inhibit guard armed: %d chunk(s) tracked",
                len(tts_chunks),
            )
        else:
            # No slot map — discard any stale counters from a previous turn.
            session.pop("_slot_chunks_sent",      None)
            session.pop("_slot_chunks_inhibited", None)

        # ── 8. Send to TTS ───────────────────────────────────────────────────
        for i, c in enumerate(tts_chunks):
            logger.info(
                "[ms_gate5] slot buf TTS chunk %d/%d: %r — len=%d",
                i + 1, len(tts_chunks), c[:60], len(c),
            )
            await tts_queue.put(c)
            # Signal to the tool loop that real audio reached the caller this
            # turn (C8-5 silence guarantee).
            session["_slotbuf_emitted"] = True

    # -----------------------------------------------------------------------
    # Streaming tool loop
    # -----------------------------------------------------------------------

    async def _streaming_tool_loop(
        self,
        model: str,
        system_prompt: str,
        messages: List[dict],
        tools: list,
        session: Dict[str, Any],
        call_sid: Optional[str],
        tts_text_queue: asyncio.Queue,
        on_transfer: Optional[Callable[[], Coroutine]],
        interim_played: bool = False,
        dynamic_prompt: str = "",
    ) -> tuple:
        """
        Run the Claude streaming + tool-calling loop.

        Returns (full_reply_text, transfer_initiated).
        """
        client = _get_anthropic_client()
        full_reply = ""
        transfer_initiated = False
        filler_sent = False
        # True when the previous iteration executed check_availability — arms the
        # complete-response slot buffer for the following iteration so the full
        # LLM response is held and flushed to TTS only after the stream ends.
        _last_check_avail: bool = False

        # ── C8-5 silence guarantee — per-turn speech tracking ───────────────
        # _turn_real_tts: True once ANY chunk reaches the real tts_text_queue
        #   this turn (direct stream, slot-buffer flush, or per-call fallback).
        # _check_av_ran_turn: True if any iteration executed check_availability
        #   (used to choose the no-availability vs generic fallback message).
        # _flow_suppressed: True if the loop broke on a deterministic-suppression
        #   gate (flow.py owns the spoken output for that state) — suppresses the
        #   inline guarantee for non-v3 clinics so it never double-speaks over
        #   flow.py.  (v3 is protected separately by connection.py's post-turn
        #   _v3_post_turn_speech guard, so the deferral is always safe there.)
        session["_turn_real_tts"]    = False
        session["_check_av_ran_turn"] = False
        # P1 #5: the false-confirmation re-steer fires at most once per turn (the
        # first phantom chunk becomes the confirmation question; later ones are
        # dropped). booking_write_confirmed is NOT reset here — a real booking is
        # call-scoped, so once one succeeds the guard stays off for the rest of
        # the call.
        session["_false_confirm_resteered"] = False
        # B-36 / R2: the refusal marker is TURN-scoped and must be cleared here.
        # Left set, it would keep Gate 5f armed for the rest of the call and
        # strip every later confirmation — including a genuine one. This is the
        # opposite lifetime to booking_write_confirmed directly above, and
        # deliberately so: a refusal is a fact about one turn, a completed
        # booking is a fact about the call.
        session.pop(WRITE_REFUSED_KEY, None)
        # What the caller hears this turn, accumulated by _record_spoken as each
        # post-Gate-5 chunk is released. Cleared here so a turn can never inherit
        # the previous turn's speech.
        session.pop("_spoken_this_turn", None)
        # B-76: its per-chunk backing store shares the same lifetime exactly.
        session.pop(SPOKEN_CHUNKS_KEY, None)
        # Gate 5b-r substitutes the outstanding booking step when stripping the
        # reason question leaves the turn with nothing to ask. TURN-scoped, and
        # it must be: sanitise_response runs once per streamed chunk, so without
        # the reset a later turn would inherit the latch and go back to shipping
        # the silence this exists to prevent.
        session.pop("_gate5br_substituted", None)
        # The booking-outcome fallback is TURN-scoped. It is set when a booking
        # write succeeds and consumed by the deferred Gate-5 fallback in the
        # same turn. Clearing it here means a later empty turn — a farewell the
        # model fumbles, say — can never re-announce a booking the caller was
        # told about minutes ago.
        session.pop("_booking_outcome_unspoken", None)
        # L1/L2: the affirmation verdict is memoised per turn. The tool loop can
        # retry book_appointment up to MAX_TOOL_ITERATIONS times (CA7e389a47 did
        # three in one turn), and without this each retry would re-run the
        # classifier on an utterance that has not changed.
        session.pop("_book_verdict_cache", None)
        _flow_suppressed: bool = False

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            logger.info("[ms_llm] iteration=%d model=%s", iteration, model)

            # Popped, not read: the suppression lasts exactly one iteration.
            # Left set it would disarm tools for the rest of the turn, and a
            # later iteration that legitimately needs book_appointment — the
            # caller says "yes, go ahead" — would be unable to call it and the
            # booking would silently never happen. That is the worst failure
            # this system has, so the flag is consumed at the first read.
            _force_text = bool(session.pop(FORCE_TEXT_NEXT_ITERATION, False))
            _tool_choice = {"type": "none"} if _force_text else None
            if _force_text:
                logger.info(
                    "[ms_llm] tools suppressed for iter=%d — the previous "
                    "iteration blocked a tool call and asked for speech",
                    iteration,
                )

            # ── Slot complete-response buffer ─────────────────────────────
            # When the previous iteration executed check_availability, route
            # this iteration's output through a temporary buffer instead of
            # directly to tts_text_queue.  _one_streaming_call fills the
            # buffer while streaming; after it returns (stream complete, turn
            # done) _flush_slot_buf drains the entire buffer to TTS in one
            # pass.  This eliminates sentinel guessing — the full response is
            # always flushed intact.
            _slot_buf: Optional[asyncio.Queue] = None
            _active_q = tts_text_queue
            _call_system  = system_prompt
            _call_dynamic = dynamic_prompt

            # ── DIFFERENT DAY REQUESTED steer (Bug B, 2026-07-30) ────────────
            # CAb81fe651: the caller asked for Wednesday four times. Every reply
            # served Tuesday, and the fourth said "Wednesday the 5th is what
            # we've got" about a slot on Tuesday the 4th. He hung up unbooked.
            #
            # 5b0c9c2 released the two guards that used to block this, and they
            # do release — the predicate returns True on all four utterances,
            # verified against the real transcripts. The model simply never
            # called check_availability. It answered from the Tuesday slots still
            # sitting in its message history, which read to it as perfectly good
            # data. Releasing a block cannot fix that: there was no block left to
            # release, so the fix has to push toward the tool rather than stop
            # stopping it.
            #
            # Fires ONLY in the exact defect state — the caller has just named a
            # different day AND there are older slots in context to answer from.
            # With nothing to answer from, the model calls the tool anyway and
            # this line never renders.
            #
            # Self-suppressing: _check_av_ran_turn flips the moment
            # check_availability executes, so the steer is gone on the
            # presentation pass and cannot argue with the slots it just asked
            # for. (It would be dropped there regardless — the Haiku slot
            # formatter clears _call_dynamic below.)
            _dd_steer = _different_day_steer(session, messages or [])
            if _dd_steer:
                _call_dynamic = (
                    (_call_dynamic + "\n\n") if _call_dynamic else ""
                ) + _dd_steer
                # Counted into obs so "did the steer fire" is answerable from the
                # call record. On 30 Jul that question cost three round-trips
                # through Render logs.
                session["_different_day_steer_fired"] = (
                    int(session.get("_different_day_steer_fired") or 0) + 1
                )
                # The caller has asked for a different day, so any confirmed
                # slot phrase now names a day they are leaving. Mark it, and the
                # Gate-5 date guard stands down until a new phrase is captured
                # or refreshed. This is the caller's own words — the one input
                # to that decision Gate 5 cannot rewrite, which is what stops
                # the guard confirming its own output. See
                # _confirmed_slot_is_stale (turn_handler).
                session["v3_slot_phrase_superseded"] = True
                logger.info(
                    "[ms_llm] DIFFERENT DAY REQUESTED steer applied iter=%d "
                    "call_sid=%s", iteration, call_sid,
                )
            # Flag the post-check_availability slot-presentation pass so Gate 5a
            # exempts its (legitimately time-dense) output from the
            # high_time_density reasoning drop. Tied to _last_check_avail — the
            # exact condition that arms the slot buffer — so it is True ONLY on
            # the slot pass and reset on every other iteration (no cross-turn leak).
            session["_slot_buf_active"] = bool(_last_check_avail)
            if _last_check_avail:
                _slot_buf = asyncio.Queue()
                _active_q = _slot_buf
                # Post-check_availability slot presentation is deterministic
                # template-filling — switch to Haiku with a focused formatting
                # prompt (~1.5K tokens) instead of the full ~19K persona prompt.
                # The big prompt forced a cold-cache prefill on Haiku's first
                # slot call (~9s of dead air, since the prompt cache is keyed
                # per-model and Sonnet's cache doesn't carry to Haiku); the
                # focused prompt prefills in ~1s even cold.  messages + tools
                # are left unchanged so conversational context is preserved.
                from app.prompts.susie_system_prompt import (
                    SLOT_FORMATTER_SYSTEM_PROMPT,
                )
                model = HAIKU
                _call_system  = SLOT_FORMATTER_SYSTEM_PROMPT
                _call_dynamic = ""
                logger.info(
                    "[ms_llm] slot buffer active (post-check_availability) iter=%d"
                    " — switched to HAIKU + focused slot prompt",
                    iteration,
                )
            _last_check_avail = False  # reset; re-armed below after tool execution

            # ── Try Claude streaming ──────────────────────────────────────
            try:
                chunk_text, tool_uses, did_transfer = await self._one_streaming_call(
                    client=client,
                    model=model,
                    system_prompt=_call_system,
                    messages=messages,
                    tools=tools,
                    session=session,
                    tts_text_queue=_active_q,
                    filler_sent=filler_sent,
                    # Only suppress on first iteration — subsequent iterations
                    # (after tool calls) generate genuinely new text.
                    interim_played=(interim_played and iteration == 1),
                    dynamic_prompt=_call_dynamic,
                    tool_choice=_tool_choice,
                )
                filler_sent = True  # suppress filler on subsequent iterations

                # ── Track real-queue speech for the C8-5 silence guarantee ──
                # When the slot buffer is active this iteration's output went to
                # _slot_buf (not the real queue), so the authoritative signal is
                # whether _flush_slot_buf actually sent chunks — checked below.
                # When the buffer is NOT active, _one_streaming_call's own
                # emission flag is authoritative.
                _oc_emitted = bool(session.pop("_oc_emitted_tts", False))

                # ── Flush complete-response slot buffer ───────────────────
                if _slot_buf is not None:
                    session["_slotbuf_emitted"] = False
                    await self._flush_slot_buf(_slot_buf, tts_text_queue, session)
                    if session.pop("_slotbuf_emitted", False):
                        session["_turn_real_tts"] = True
                elif _oc_emitted:
                    session["_turn_real_tts"] = True

            except Exception as exc:
                status = getattr(exc, "status_code", None)
                exc_str = str(exc).lower()
                _is_overloaded = (
                    status in (429, 500, 529)
                    or "overloaded" in exc_str
                    or "overloaded_error" in exc_str
                )
                if _is_overloaded:
                    # Retry up to 2 times with backoff before falling through to GPT
                    _retry_ok = False
                    for _attempt in range(1, 3):
                        _wait = _attempt * 1.5
                        logger.warning(
                            "[ms_llm] Claude overloaded (attempt %d) — retrying in %.1fs",
                            _attempt, _wait,
                        )
                        await asyncio.sleep(_wait)
                        try:
                            chunk_text, tool_uses, did_transfer = await self._one_streaming_call(
                                client=client,
                                model=model,
                                system_prompt=system_prompt,
                                messages=messages,
                                tools=tools,
                                session=session,
                                tts_text_queue=tts_text_queue,
                                filler_sent=True,
                                interim_played=True,
                                dynamic_prompt=dynamic_prompt,
                                # Same iteration, retried after a 529 — it must
                                # carry the same suppression. The flag was
                                # already popped above, so reading the session
                                # again here would find nothing and the retry
                                # would silently regain tools.
                                tool_choice=_tool_choice,
                            )
                            filler_sent = True
                            _retry_ok = True
                            break
                        except Exception as _retry_exc:
                            logger.warning("[ms_llm] retry %d failed: %r", _attempt, _retry_exc)
                    if not _retry_ok:
                        if OPENAI_API_KEY:
                            logger.warning("[ms_llm] Claude still overloaded — GPT fallback")
                            reply = await self._gpt_fallback(
                                system_prompt=system_prompt,
                                messages=messages,
                                session=session,
                                tts_text_queue=tts_text_queue,
                            )
                            full_reply += reply
                            return full_reply, False
                        else:
                            logger.error("[ms_llm] Claude overloaded, no GPT key — fallback phrase")
                            await tts_text_queue.put(SAFE_FALLBACK_PHRASE)
                            return SAFE_FALLBACK_PHRASE, False
                else:
                    logger.error("[ms_llm] Claude API error: %r", exc)
                    await tts_text_queue.put(SAFE_FALLBACK_PHRASE)
                    return SAFE_FALLBACK_PHRASE, False

            full_reply += chunk_text

            if did_transfer:
                transfer_initiated = True
                break

            # ── No tool calls: we're done ─────────────────────────────────
            if not tool_uses:
                if not chunk_text.strip():
                    # Empty response -- nudge Claude
                    logger.warning("[ms_llm] empty response iter %d -- nudging", iteration)
                    messages.append({
                        "role": "user",
                        "content": (
                            "Please give the caller a natural spoken response "
                            "based on the most recent tool result and continue."
                        ),
                    })
                    continue
                break

            # ── Build assistant message with tool_use blocks ──────────────
            assistant_content: List[dict] = []
            if chunk_text:
                assistant_content.append({"type": "text", "text": chunk_text})
            for tu in tool_uses:
                assistant_content.append({
                    "type":  "tool_use",
                    "id":    tu["id"],
                    "name":  tu["name"],
                    "input": tu["input"],
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # ── Speak text alongside tool calls ──────────────────────────
            # (already queued during streaming -- nothing extra needed here)

            # ── Execute tools ─────────────────────────────────────────────
            # Pass tts_text_queue so filler phrases play during API latency.
            tool_result_blocks = await self._execute_tools(
                tool_uses, session, call_sid, tts_text_queue=tts_text_queue,
                messages=messages,
            )
            messages.append({"role": "user", "content": tool_result_blocks})

            # Re-arm slot buffer for the next iteration ONLY when
            # check_availability ran AND returned usable slots.  A zero-slot
            # result must fall through to Sonnet (full prompt) so it can
            # explain the lack of availability and offer an alternative — the
            # focused Haiku slot prompt has no handling for an empty result and
            # would emit silence (C8-5: caller abandoned the call after a
            # 0-slot "tomorrow" check returned nothing and Susie went quiet).
            _ran_check_av = any(
                tu.get("name") == "check_availability" for tu in tool_uses
            )
            _last_check_avail = _ran_check_av and bool(
                session.get("_check_av_had_slots")
            )
            if _ran_check_av and not _last_check_avail:
                logger.info(
                    "[ms_llm] check_availability returned 0 slots — slot buffer "
                    "NOT armed; Sonnet handles the no-availability reply"
                )

            await save_session(call_sid, session)

            # ── Deterministic post-tool speech gates ─────────────────────
            # flow.py owns the single spoken output for each of these states.
            # Breaking here prevents the LLM's iteration-2 text from reaching
            # tts_text_queue before flow.py's drain/deterministic-prompt runs.
            # Without this break, chunks streamed during iteration 2 are
            # consumed by the TTS coroutine before the drain executes, making
            # the drain a no-op and causing duplicate speech.

            if session.get("rc_lookup_failed"):
                # flow.py rc_lookup_failed handler emits the recovery prompt.
                logger.info(
                    "[ms_llm] rc_lookup_failed after tool — "
                    "suppressing post-tool LLM response"
                )
                _flow_suppressed = True
                break

            if session.get("rc_lookup_just_succeeded"):
                # flow.py rc_lookup_just_succeeded handler (ask_current_question)
                # will drain TTS and emit a single deterministic confirmation.
                logger.info(
                    "[ms_llm] rc_lookup_just_succeeded after tool — "
                    "suppressing post-tool LLM response"
                )
                _flow_suppressed = True
                break

            if session.get("rc_appointment_confirmed"):
                # flow.py rc_appointment_confirmed handler advances the flow and
                # asks CONFIRM_RESCHEDULE_OR_CANCEL — no LLM speech needed.
                logger.info(
                    "[ms_llm] rc_appointment_confirmed after tool — "
                    "suppressing post-tool LLM response"
                )
                _flow_suppressed = True
                break

            # PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE: the deterministic path in
            # ask_current_question() always emits the day phrase — LLM must be
            # silent after check_availability returns.  The instruction says
            # "say NOTHING further" but is not always honoured; enforce it here.
            _pd_suppress_states = {"PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"}
            if (
                session.get("state") in _pd_suppress_states
                or session.get("flow_state") in _pd_suppress_states
            ):
                logger.info(
                    "[ms_llm] PRESENT_DAYS state after tool — "
                    "suppressing post-tool LLM response"
                )
                _flow_suppressed = True
                break

            # ── Transfer requested by a tool ─────────────────────────────
            # Use .get() NOT .pop() — _on_transfer_request() calls
            # _should_allow_transfer() which reads session["request_transfer"].
            # If we pop it first, the guard sees False and blocks the transfer.
            # Clear it manually after on_transfer() fires instead.
            if session.get("request_transfer"):
                logger.info("[ms_llm] transfer requested call_sid=%s", call_sid)
                if on_transfer:
                    await on_transfer()
                session["request_transfer"] = False  # clear after guard consumed it
                transfer_initiated = True
                break

        else:
            logger.warning("[ms_llm] hit MAX_TOOL_ITERATIONS")
            await tts_text_queue.put(SAFE_FALLBACK_PHRASE)
            full_reply = SAFE_FALLBACK_PHRASE
            session["_turn_real_tts"] = True  # the line above reached the queue

        # ── C8-5 silence guarantee — end-of-turn catch-all ──────────────────
        # If this entire turn (every iteration, slot-buffer flush, and per-call
        # fallback) emitted nothing audible, the caller would hear dead air —
        # the exact failure that made a tester abandon the call (Call 8: a
        # zero-slot "tomorrow" check returned nothing and Susie went silent).
        # Guarantee a spoken response on every no-speech exit path here.
        #
        # Skipped when:
        #   - transfer_initiated: the transfer flow owns the audio.
        #   - _flow_suppressed (non-v3 only): a deterministic-suppression gate
        #     handed the spoken output to flow.py, which speaks via its own
        #     drain — emitting here would double-speak.  For v3 there is no
        #     flow.py drain, and connection.py's post-turn _v3_post_turn_speech
        #     guard suppresses the deferred fallback if any recovery path spoke,
        #     so deferring is always safe (and IS the fix for a suppression-gate
        #     break that leaves flow.py with nothing to present).
        from app.clinic_config import is_freeform_clinic as _is_freeform
        # Free-form clinics (theorem_v3 + template_v1) have no flow.py drain, so
        # the deferral logic below applies to all of them, not just v3.
        _is_v3 = _is_freeform(session.get("clinic_id"))
        if (
            not transfer_initiated
            and not session.get("_turn_real_tts")
            and not (_flow_suppressed and not _is_v3)
        ):
            _ran_av   = bool(session.get("_check_av_ran_turn"))
            _had_slots = bool(session.get("_check_av_had_slots"))
            if _ran_av and not _had_slots:
                _fallback = NO_AVAILABILITY_FALLBACK
            elif _ran_av and _had_slots:
                # Slots WERE retrieved this turn but none reached the queue
                # (formatter output dropped by gate5).  The caller was
                # understood — use the non-blaming recovery, not "didn't catch
                # that".
                _fallback = SLOT_RECOVERY_FALLBACK
            else:
                _fallback = SILENCE_RECOVERY_FALLBACK
            if _is_v3:
                # Defer to connection.py's post-turn path (avoids racing the
                # booking-ack location question / FAQ synthetic re-queue).  When
                # a check ran this turn the av-aware phrase (no-availability or
                # slot-recovery) always wins over any generic "didn't catch
                # that" a per-call fallback may have queued; otherwise only fill
                # in a pending fallback if one is not already queued.
                if _ran_av:
                    session["_gate5_fallback_pending"] = _fallback
                else:
                    session.setdefault("_gate5_fallback_pending", _fallback)
                logger.warning(
                    "[ms_llm] turn produced no audible speech — guaranteed "
                    "fallback DEFERRED to v3 post-turn path (ran_av=%s "
                    "had_slots=%s flow_suppressed=%s): %r",
                    _ran_av, _had_slots, _flow_suppressed, _fallback,
                )
            else:
                await tts_text_queue.put(_fallback)
                full_reply = full_reply or _fallback
                logger.warning(
                    "[ms_llm] turn produced no audible speech — emitted "
                    "guaranteed fallback (ran_av=%s had_slots=%s): %r",
                    _ran_av, _had_slots, _fallback,
                )

        return full_reply, transfer_initiated

    # -----------------------------------------------------------------------
    # Single streaming Claude call
    # -----------------------------------------------------------------------

    async def _one_streaming_call(
        self,
        client: Any,
        model: str,
        system_prompt: str,
        messages: List[dict],
        tools: list,
        session: Dict[str, Any],
        tts_text_queue: asyncio.Queue,
        filler_sent: bool,
        interim_played: bool = False,
        dynamic_prompt: str = "",
        tool_choice: Optional[dict] = None,
    ) -> tuple:
        """
        Open one Claude streaming session, feed tokens through the chunker,
        and put text chunks onto tts_text_queue.

        Returns (full_text, tool_uses, transfer_initiated).
        tool_uses is non-empty if stop_reason == "tool_use".

        `tool_choice` is passed straight through to the Messages API when set.
        `{"type": "none"}` makes a tool call structurally impossible for that
        iteration — see the FORCE_TEXT_NEXT_ITERATION note in run_turn(). It is
        omitted entirely when None rather than sent as null, so the request
        shape is unchanged on every normal iteration.
        """
        chunker    = ResponseChunker(
            min_words_first=WS_A_MIN_WORDS_FIRST,
            fast_first=WS_A_FAST_FIRST_CHUNK,
        )
        full_text  = ""
        tool_uses: List[dict] = []
        timeout_sec          = LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0
        got_first_chunk      = False
        _first_tts_emitted   = False  # tracks whether first TTS chunk has been sent
        _any_tts_emitted     = False  # True if ANY sanitised chunk actually reached the queue
        # True when the pre-tool hold latch below was set from full_text,
        # which Gate 5 has not run on yet. Per-iteration, like full_text.
        _latched_on_ungated_text = False

        # Reset ack-filler state for this turn.  _ack_filler_active is set True
        # by _delayed_filler() below when FILLER_PHRASE is queued; with_filler()
        # reads it and sets _ack_filler_cancelled True when a tool-call filler
        # supersedes it.  _tts_loop uses _ack_filler_cancelled to silently drop
        # the marked ack-filler chunk before it reaches ElevenLabs.
        session["_ack_filler_active"]    = False
        session["_ack_filler_cancelled"] = False
        # O-18: set by Gate 5g when it deletes a booking CTA because the NAME is
        # missing — which also deletes the model's acknowledgement of the name
        # the caller just gave. Reset per turn, or a stale True would let a later
        # turn read a name out of an unrelated raw reply. See turn_handler and
        # connection._v3_try_persist_name's call site.
        session["_gate5g_dropped_name_ack"] = False
        # Pre-slot cancellation: all text chunks in this turn are prefixed with
        # PRE_SLOT_MARKER.  When check_availability tool_use is detected via
        # content_block_start, _pre_slot_cancelled is set True so the tts_loop
        # drops any PRE_SLOT_MARKER chunks still in the queue.
        session["_pre_slot_cancelled"] = False
        _slot_tool_active: bool = False

        # Background task: fire filler phrase after timeout if no text yet.
        # Cannot rely on stream events alone — if Claude takes >5s to send
        # the first event, the in-loop deadline check never fires.
        _filler_task: "asyncio.Task | None" = None

        # ── The situational head ─────────────────────────────────────────
        # What the CALLER asked for, read from the transcript. Pure, cheap, and
        # available NOW -- the tool name is not known for another ~2s, and on
        # an FAQ turn no tool runs at all.
        #
        # This decides two things: the wording, and how long to wait before
        # saying it. A contentless head can only be justified once the caller
        # has waited long enough that acknowledging the wait is the honest
        # thing (3000ms, ~8% of turns). A head derived from the caller's own
        # words is not a guess about work, so it does not have to earn its
        # place that way: turn time-to-first-audio is p50 1,938ms across the
        # corpus, so at 600ms it lands in front of half of all replies rather
        # than the slow tail.
        _hs_situational = ""
        _hs_intent = None
        try:
            from app.hold_speech import (
                classify_intent as _classify_intent,
                hold_speech_enabled as _hs_enabled,
                render_intent_head as _render_intent_head,
                subject_for as _subject_for,
            )
            if _hs_enabled(session):
                _hs_utterance = _last_user_text(messages or [])
                # B-90's verdict, asked directly instead of inferred from
                # Susie's previous sentence. A slot readout says "Number 1,
                # ... Number 2, ...", and `_CONFIRM_Q` used to match that
                # READOUT -- so from the first offer onwards every diary
                # head in the call was suppressed, including the two turns
                # on the 2026-08-30 demo call that most needed one.
                # Deferred import: connection imports this module lazily in
                # the other direction, so neither edge exists at module
                # scope. Never allowed to break a call -- a head is a
                # nicety and the pre-arbiter behaviour is silence.
                try:
                    from .connection import (
                        utterance_is_slot_selection as _is_pick,
                    )
                    _hs_picking = _is_pick(_hs_utterance, session)
                except Exception:  # pragma: no cover - defensive
                    _hs_picking = False
                _hs_hits = _classify_intent(
                    _hs_utterance,
                    _last_assistant_text(session),
                    screen_pending=bool(session.get("pending_screen")),
                    slot_selection=_hs_picking,
                )
                if _hs_hits:
                    _hs_intent = _hs_hits[0]
                    _hs_situational = _render_intent_head(
                        _hs_intent,
                        subject=_subject_for(_hs_utterance),
                        index=len(session.get("used_fillers") or []),
                    )
        except Exception:  # pragma: no cover - a head must never break a call
            logger.warning("[ms_llm] situational head unavailable", exc_info=True)
            _hs_situational, _hs_intent = "", None

        _hold_delay_s = (
            HOLD_HEAD_DELAY_MS / 1000.0 if _hs_situational else timeout_sec
        )
        # The 8s cross-turn cooldown guards against a cadence of contentless
        # phrases -- the corpus has one call with 17 of them. A situational head
        # is not that: it carries the caller's own subject and is joined into
        # the sentence behind it, so two consecutive turns opening "Sorry to
        # hear that -" then "On price -" is ordinary receptionist speech, not
        # stacking. Stacking WITHIN a turn is still unrepresentable, because
        # _hold_head_spoken is checked by decide_hold either way.
        _cooled = (time.monotonic() - self._last_filler_at) >= LLM_FILLER_COOLDOWN_SEC
        if not filler_sent and (_cooled or _hs_situational):
            async def _delayed_filler() -> None:
                await asyncio.sleep(_hold_delay_s)
                if not got_first_chunk:
                    # Prefix with ACK_FILLER_MARKER so _tts_loop can identify
                    # this chunk and suppress it if a tool-call filler fires
                    # in the same turn and sets _ack_filler_cancelled.
                    # On the turn right after a booking/reschedule "yes", use a
                    # write-acknowledging filler ("Just locking that in now…")
                    # instead of the generic "Give me a moment…", which confuses
                    # a caller who just confirmed and can re-open the readback.
                    from app.filler_phrases import (
                        is_write_filler as _is_write_filler,
                        note_filler_played as _note_filler,
                        should_play_filler as _should_filler,
                    )
                    from app.filler_phrases import confirm_write_filler
                    from app.hold_speech import (
                        WorkKind as _WorkKind,
                        clinic_facts as _clinic_facts,
                        confirm_write_kind as _confirm_write_kind,
                        decide_hold as _decide_hold,
                        hold_speech_enabled as _hs_on,
                        render_head as _render_head,
                    )
                    # This fires before the LLM has emitted anything, so with one
                    # exception NOTHING here knows whether a tool is coming. The
                    # old code guessed "Let me just check that…" anyway and was
                    # wrong on 135 of the 322 stored hold phrases — the caller who
                    # asked "are you a robot?" heard "Just getting that for you…"
                    # before "No — I'm Susie".
                    #
                    # The exception is a caller who has just said yes to a locked
                    # confirm CTA: a write IS about to run, and that is knowable
                    # from conversation state. FM-25 — consent is required, not
                    # merely that the CTA was asked.
                    _provisional, _prac = _clinic_facts(session)
                    _kind = _confirm_write_kind(
                        _last_assistant_text(session),
                        _book_reply_is_affirmative(messages),
                        provisional=_provisional,
                    )
                    if _kind is _WorkKind.NONE:
                        # The caller is saying goodbye. Nothing is in flight and
                        # nobody is waiting, so there is no wait to cover: stay
                        # quiet rather than falling through to a phrase that
                        # acknowledges one. Checked BEFORE the UNKNOWN_SLOW
                        # fallback because that fallback is precisely what fired
                        # here -- "Sorry, still with you -- Take care of
                        # yourself" after "Alright. I'll ring 111 then. Thanks."
                        try:
                            from app.hold_speech import is_closing as _is_closing

                            if _is_closing(_last_user_text(messages or [])):
                                logger.info(
                                    "[ms_llm] no hold phrase: the caller is "
                                    "closing the call, not waiting"
                                )
                                return
                        except Exception:  # pragma: no cover - never break a call
                            pass
                        # Work unknown. Say something that names none of it --
                        # unless the CALLER told us what this turn is about, in
                        # which case there is something true and specific to
                        # say and no need to fall back to a contentless one.
                        _kind = _WorkKind.UNKNOWN_SLOW
                    _decision = _decide_hold(
                        legacy=not _hs_on(session),
                        session=session,
                        # FM-25: the pre-arbiter site tried confirm_write_filler
                        # first and only fell back to a neutral phrase, so a
                        # "no"/ambiguous reply never became a booking claim.
                        legacy_override=confirm_write_filler(
                            session, _book_reply_is_affirmative(messages)
                        ) or "",
                        kind=_kind,
                        head_already_spoken=bool(
                            session.get("_hold_head_spoken")
                        ),
                        practitioner=_prac,
                        heads_used=len(session.get("used_fillers") or []),
                    )
                    _ack_filler_text = _decision.head
                    # The situational head replaces the arbiter's contentless
                    # one, and ONLY that one: a head chosen from the work in
                    # flight is more specific than one chosen from the request,
                    # and a write already in progress outranks both.
                    if (
                        _hs_situational
                        and _decision.speak
                        and _decision.kind is _WorkKind.UNKNOWN_SLOW
                    ):
                        logger.info(
                            "[ms_llm] situational head (%s): %r",
                            getattr(_hs_intent, "value", "?"), _hs_situational,
                        )
                        _ack_filler_text = _hs_situational
                    # Producer B of three (CA8cf0aaea). On the phone-confirm
                    # path connection.py has already spoken ~1.8s earlier, and
                    # the tool filler follows ~1.6s later — the caller heard
                    # three hold phrases in 3.4 seconds. A write filler still
                    # plays through the first suppression: the calendar
                    # round-trip after "yes, go ahead" must never be silent.
                    _ack_is_write = _is_write_filler(_ack_filler_text)
                    if not _decision.speak:
                        # The arbiter declined. Silence is a real answer here:
                        # a head already played this turn, or there is nothing
                        # truthful to say. Returning also cancels the B-19
                        # re-arm below, which must never be the FIRST thing the
                        # caller hears.
                        logger.info(
                            "[ms_llm] no hold phrase: %s", _decision.reason,
                        )
                        return
                    if not _should_filler(session, is_write=_ack_is_write):
                        logger.info(
                            "[ms_llm] ack filler suppressed by cooldown — a "
                            "filler is still in the caller's ear: %r",
                            _ack_filler_text[:40],
                        )
                    else:
                        logger.info(
                            "[ms_llm] filler phrase triggered (background task): %r",
                            _ack_filler_text[:40],
                        )
                        await tts_text_queue.put(ACK_FILLER_MARKER + _ack_filler_text)
                        session["_ack_filler_active"] = True
                        # Pass the wording: join_after_head needs it to make the
                        # model's reply continue this clause rather than restart
                        # after it.
                        _note_filler(
                            session,
                            is_write=_ack_is_write,
                            text=_ack_filler_text,
                        )
                        session.setdefault("used_fillers", []).append(
                            _ack_filler_text
                        )
                    self._last_filler_at = time.monotonic()

                    # ── B-19: re-arm ONCE ────────────────────────────────
                    # Without this the task ends here, so an upstream stall
                    # past this point is bare silence for as long as it lasts
                    # (measured: 14s spike → ~12s of nothing).
                    #
                    # Cancellation is already handled: the first token sets
                    # got_first_chunk and cancels this task, so this sleep is
                    # torn down on any normal recovery.
                    await asyncio.sleep(LLM_FILLER_SECOND_DELAY_MS / 1000.0)
                    # Not stacking — stacking is two heads back to back. This is
                    # a second reassurance after five further seconds of nothing,
                    # where silence is the worse fault (measured: a 14s stall
                    # left ~12s of dead air). Same arbiter, rotated so it is
                    # never a verbatim repeat, and still naming no work.
                    if got_first_chunk:
                        return
                    _second_text = _render_head(
                        _WorkKind.UNKNOWN_SLOW,
                        index=len(session.get("used_fillers") or []),
                    )
                    if not _second_text or _second_text == _ack_filler_text:
                        return
                    logger.info(
                        "[ms_llm] second filler phrase (no chunk %.1fs after "
                        "the first): %r",
                        LLM_FILLER_SECOND_DELAY_MS / 1000.0, _second_text,
                    )
                    await tts_text_queue.put(ACK_FILLER_MARKER + _second_text)
                    self._last_filler_at = time.monotonic()
            _filler_task = asyncio.create_task(_delayed_filler(), name="ms_llm_filler")

        # Regression guard: total cache_control blocks must never exceed 4
        # (Anthropic hard limit).  System prompt = 1; messages should have
        # exactly 1 (last assistant turn) = 2 total.  Warn loudly if higher.
        _cc_count = sum(
            1
            for _m in messages
            for _b in (_m.get("content") if isinstance(_m.get("content"), list) else [])
            if isinstance(_b, dict) and "cache_control" in _b
        )
        if _cc_count > 1:
            logger.warning(
                "[ms_llm] CACHE_CONTROL_OVERFLOW: %d block(s) in messages "
                "(expected 1) — %d total with system prompt. "
                "History mutation regression — check run_turn() cache logic.",
                _cc_count, _cc_count + 1,
            )

        # Build system blocks: static (cached) + optional dynamic (uncached).
        # Anthropic caches the static prefix for 5 min — only turn 1 pays
        # full input cost for the ~19K-token static block.
        _system_blocks: list = [{
            "type":          "text",
            "text":          system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
        if dynamic_prompt:
            _system_blocks.append({
                "type": "text",
                "text": dynamic_prompt,
            })

        # tool_choice is omitted unless set. It is safe to vary per iteration:
        # changing tool_choice does NOT invalidate the tools or system prefix in
        # the prompt cache (only tool DEFINITION and model changes do), and the
        # static system block here is ~19K tokens.
        _tc_kwargs = {"tool_choice": tool_choice} if tool_choice else {}
        if tool_choice:
            logger.info("[ms_llm] tool_choice=%r for this iteration", tool_choice)

        async with client.messages.stream(
            model=model,
            system=_system_blocks,
            messages=messages,
            tools=tools,
            max_tokens=CLAUDE_MAX_TOKENS,
            temperature=CLAUDE_TEMPERATURE,
            **_tc_kwargs,
        ) as stream:

            async for event in stream:
                # ── Tool-use block opening ────────────────────────────────
                # Detect check_availability as early as possible (before the
                # tool result arrives) so the tts_loop can drop pre-tool
                # PRE_SLOT_MARKER chunks that haven't been consumed yet.
                # ── Prompt-cache accounting ───────────────────────────────
                # B2. The first-turn latency penalty (~0.7-1.0s measured) is
                # the 5-minute prompt cache expiring between sparse calls,
                # not the httpx pool. That was INFERRED from the shape of
                # llm_ttft_ms; these counters make it a fact. `message_start`
                # is the only event carrying input usage.
                #
                # first-write-wins, matching llm_ttft_ms: a tool loop makes
                # several calls per turn and only the first is in front of
                # the caller. Wrapped because usage shape is an API detail —
                # a measurement must never be able to kill a live call.
                if (
                    self._timing is not None
                    and getattr(event, "type", None) == "message_start"
                    and self._timing.cache_read_tokens is None
                ):
                    try:
                        _u = event.message.usage

                        self._timing.cache_read_tokens = _usage_token(
                            _u, "cache_read_input_tokens"
                        )
                        self._timing.cache_write_tokens = _usage_token(
                            _u, "cache_creation_input_tokens"
                        )
                        self._timing.prompt_input_tokens = _usage_token(
                            _u, "input_tokens"
                        )
                    except Exception:
                        pass

                if hasattr(event, "type") and event.type == "content_block_start":
                    _cb = getattr(event, "content_block", None)
                    if (
                        _cb is not None
                        and getattr(_cb, "type", None) == "tool_use"
                        and getattr(_cb, "name", None) == "check_availability"
                        and not _slot_tool_active
                    ):
                        _slot_tool_active = True
                        # Don't cancel pre-slot text when the previous check in
                        # this turn returned 0 slots — Sonnet's text is the
                        # no-availability explanation, not filler throat-clearing.
                        _prev_ran = bool(session.get("_check_av_ran_turn"))
                        _prev_had = bool(session.get("_check_av_had_slots"))
                        if not (_prev_ran and not _prev_had):
                            if _clinic_keeps_pre_slot_speech(session):
                                # Job 3c.3: JV opt-in — let empathy / physio
                                # knowledge reach the caller before slots.
                                # Suppress remains the default elsewhere.
                                logger.info(
                                    "[ms_gate5] pre-tool TTS preserved — "
                                    "clinic keep_pre_slot_speech "
                                    "(check_availability detected)"
                                )
                                # ...and when what was preserved IS a hold
                                # phrase, say so, because the tool-time
                                # producer below asks decide_hold with
                                # head_already_spoken=_hold_head_spoken and
                                # nothing was setting that latch here.
                                #
                                # Live 2026-08-29, CA7454c983a10dd3db7caee7dba3b06238:
                                #   23:02:25.558  "Got it. Let me check what's
                                #                  available for you as soon as
                                #                  possible"   (the model's own)
                                #   23:02:26.370  "Okay, one sec —"  (ours)
                                # Two hold phrases 0.8s apart — B-121, the
                                # detector's own defect, live on the demo line.
                                #
                                # Gated on _NAMES_THE_WORK rather than on the
                                # branch, because this branch also preserves
                                # EMPATHY ("I'm sorry to hear that — shoulder
                                # pain can be limiting"), and latching on that
                                # would suppress a hold phrase the caller
                                # genuinely needs. The module's own definition
                                # of "claims a lookup or a write" is the right
                                # discriminator and it already exists.
                                _pre_hold = (full_text or "").strip()
                                if _pre_hold and _NAMES_THE_WORK.search(_pre_hold):
                                    # Whose latch is this? The revocation below
                                    # says of itself "Only OUR latch is
                                    # revocable; another producer's records audio
                                    # that has already gone out" -- but nothing
                                    # enforced that, because the flag was set
                                    # without ever looking at the prior value.
                                    #
                                    # When a situational head has ALREADY spoken
                                    # this turn (600ms, well before these tokens)
                                    # the latch is the head producer's and the
                                    # audio is already on the wire. Claiming it
                                    # here makes it revocable, and once the
                                    # duplicate model line is suppressed the
                                    # revocation sees no hold phrase in
                                    # `_spoken_this_turn`, clears a latch it does
                                    # not own, and the tool-time producer speaks
                                    # a second phrase after all -- the same
                                    # defect from a different producer.
                                    _already_latched = bool(
                                        session.get("_hold_head_spoken")
                                    )
                                    session["_hold_head_spoken"] = True
                                    # full_text is PRE-Gate-5. The sentence
                                    # this latched on may still be deleted
                                    # as a banned phrase, in which case
                                    # nothing was spoken and standing the
                                    # tool-time producer down buys silence.
                                    # Revoked at the end of this call if
                                    # nothing survived -- see the check
                                    # after the flush. Only OUR latch is
                                    # revocable; another producer's records
                                    # audio that has already gone out.
                                    _latched_on_ungated_text = not _already_latched
                                    if _already_latched:
                                        logger.info(
                                            "[ms_gate5] the preserved pre-tool line "
                                            "IS a hold phrase (%r) but a head had "
                                            "already spoken — the latch is not ours "
                                            "and stays NON-revocable",
                                            _pre_hold[:60],
                                        )
                                    else:
                                        logger.info(
                                            "[ms_gate5] the preserved pre-tool line IS "
                                            "a hold phrase (%r) — latching "
                                            "_hold_head_spoken so the tool-time "
                                            "producer stands down instead of saying a "
                                            "second one",
                                            _pre_hold[:60],
                                        )
                            else:
                                session["_pre_slot_cancelled"] = True
                                logger.info(
                                    "[ms_gate5] pre-tool TTS output cancelled — "
                                    "slot buffer taking over (check_availability detected)"
                                )
                        else:
                            logger.info(
                                "[ms_gate5] pre-tool TTS preserved — "
                                "previous check returned 0 slots, Sonnet explanation kept"
                            )
                    continue

                # ── Text token ────────────────────────────────────────────
                if hasattr(event, "type"):
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "type") and delta.type == "text_delta":
                            token = delta.text or ""
                            if not token:
                                continue

                            full_text += token

                            if not got_first_chunk:
                                got_first_chunk = True
                                # t1 — first LLM token (latency-eval; None when OFF)
                                if self._timing is not None:
                                    self._timing.stamp("t1")
                                # Cancel background filler task — response arrived in time
                                if _filler_task and not _filler_task.done():
                                    _filler_task.cancel()

                            chunk = chunker.add_token(token)
                            if chunk:
                                if not _first_tts_emitted:
                                    _first_tts_emitted = True
                                    # A hold phrase the caller already heard is
                                    # the same opener the model is about to say
                                    # again ~1-2s later. interim_played covers
                                    # the fast-path; _hold_head_spoken covers
                                    # every deterministic producer, which is
                                    # where all 95 stored duplicates came from.
                                    _head = session.get("_hold_head_text", "")
                                    if interim_played or session.get(
                                        "_hold_head_spoken"
                                    ):
                                        chunk = join_after_head(
                                            chunk, _head or "…",
                                            suppress_pure_duplicate=(
                                                _may_suppress_pure_dupe(
                                                    session,
                                                    _head,
                                                    _latched_on_ungated_text,
                                                )
                                            ),
                                        )
                                        if chunk:
                                            logger.debug(
                                                "[ms_llm] interim stripped; first chunk: %r",
                                                chunk[:60],
                                            )
                                # GATE 5: sanitise before TTS
                                chunk = sanitise_response(chunk, session)
                                if chunk:
                                    _record_spoken(session, chunk)
                                    # t2 — first content chunk to TTS (WS-A gate cost)
                                    if self._timing is not None:
                                        self._timing.stamp("t2")
                                    # Prefix with PRE_SLOT_MARKER so the
                                    # tts_loop can drop this chunk if
                                    # check_availability is detected this turn.
                                    await tts_text_queue.put(PRE_SLOT_MARKER + chunk)
                                    _any_tts_emitted = True

                        continue

                    if event.type == "message_delta":
                        # Check stop_reason on final message_delta
                        stop_reason = getattr(event.delta, "stop_reason", None)
                        if stop_reason and stop_reason != "end_turn":
                            pass  # tool_use handled below after stream ends

            # ── Flush remaining buffer ─────────────────────────────────────
            final_chunk = chunker.flush()
            if final_chunk:
                if not _first_tts_emitted and (
                    interim_played or session.get("_hold_head_spoken")
                ):
                    # Entire response was a single short flush — same join.
                    final_chunk = join_after_head(
                        final_chunk,
                        session.get("_hold_head_text", "") or "…",
                        suppress_pure_duplicate=_may_suppress_pure_dupe(
                            session,
                            session.get("_hold_head_text", ""),
                            _latched_on_ungated_text,
                        ),
                    )
                    _first_tts_emitted = True
                # GATE 5: sanitise flush chunk before TTS
                final_chunk = sanitise_response(final_chunk, session)
                if final_chunk:
                    _record_spoken(session, final_chunk)
                    # t2 fallback — whole reply arrived as a single flush chunk
                    # (first-write-wins, so a no-op if t2 already stamped above).
                    if self._timing is not None:
                        self._timing.stamp("t2")
                    await tts_text_queue.put(PRE_SLOT_MARKER + final_chunk)
                    _any_tts_emitted = True

            # ── The pre-tool hold latch, re-checked against what survived ──
            # It was set off `full_text`, which is what the model GENERATED.
            # Gate 5 runs per chunk between there and the queue and deletes
            # banned sentences outright, so the phrase the latch trusted may
            # never have been spoken. If nothing at all reached the queue
            # this iteration, the caller heard no hold phrase, and leaving
            # the latch set would make the tool-time producer stand down for
            # speech that does not exist -- silence across the whole tool
            # round trip.
            #
            # Revoked here rather than predicted at latch time: predicting
            # means knowing what Gate 5 will do to text still in the
            # chunker's buffer. This needs no prediction, and it is in time
            # -- the tool-time producer runs in _streaming_tool_loop after
            # this call returns.
            #
            # `_any_tts_emitted` is NOT the right test: Gate 5 can delete the
            # hold sentence while some other sentence of the same reply
            # survives, and then something was spoken but no hold phrase was.
            # `_spoken_this_turn` is the post-Gate-5 record of what the caller
            # actually heard, so ask it the same question the latch asked
            # full_text -- does this still claim a lookup or a write?
            if _latched_on_ungated_text:
                _spoken_after_gate = (
                    session.get("_spoken_this_turn") or ""
                ).strip()
                if not (
                    _spoken_after_gate
                    and _NAMES_THE_WORK.search(_spoken_after_gate)
                ):
                    session["_hold_head_spoken"] = False
                    logger.info(
                        "[ms_gate5] pre-tool hold latch REVOKED — the phrase it "
                        "latched on did not survive Gate 5 (spoken=%r), so no "
                        "hold phrase reached the caller and the tool-time "
                        "producer must not stand down",
                        _spoken_after_gate[:60],
                    )

            # ── GATE 5: per-turn reasoning drop count ─────────────────────
            _g5_drops = int(session.pop("_gate5_reasoning_drops", 0) or 0)
            logger.info(
                "[ms_gate5] turn complete: %d chunk(s) dropped as reasoning",
                _g5_drops,
            )

            # ── Collect tool uses from final message ──────────────────────
            final_message = await stream.get_final_message()
            stop_reason   = final_message.stop_reason

            if stop_reason == "tool_use":
                for block in final_message.content:
                    if block.type == "tool_use":
                        tool_uses.append({
                            "id":    block.id,
                            "name":  block.name,
                            "input": block.input,
                        })
                # full_text may include pre-tool speech; extract it cleanly
                text_parts = [
                    block.text
                    for block in final_message.content
                    if block.type == "text"
                ]
                full_text = "".join(text_parts)

                # Queue pre-tool text if any (it was already streamed token by
                # token, so this avoids double-queueing -- text is already in
                # tts_text_queue from the streaming loop above)

            # ── GATE 5: empty-response fallback (Failure Mode A) ────────────────
            # Fires whenever the caller would hear silence at end-of-turn:
            #   - _any_tts_emitted=False  → nothing reached the queue this turn
            #   - stop_reason != "tool_use" → no tool response is incoming
            # Covers two failure modes:
            #   A) LLM produced text but gate5 stripped every chunk to empty
            #      (e.g. one_for_practitioner removed the entire response)
            #   B) LLM produced a truly empty response (full_text="") —
            #      previously gated by `full_text.strip()` which silently
            #      skipped this case, causing dead air (confirmed Call 8:
            #      zero-slot result → empty LLM turn → caller abandoned call).
            # full_text.strip() check intentionally removed — both modes need
            # a fallback; the tool_use guard already excludes tool-call turns.
            if (
                not _any_tts_emitted
                and stop_reason != "tool_use"
            ):
                _gate5_fallback = (
                    "Sorry, I didn't quite catch that"
                    " — could you say that again?"
                )
                # A booking was WRITTEN this turn and the turn that should have
                # announced it produced nothing. Asking the caller to repeat
                # themselves is the worst available answer: the booking exists,
                # the caller does not know, and on CAd8868396 they said goodbye
                # and hung up believing nothing had happened.
                #
                # Consumed with pop(), so it can be spoken at most once, and
                # cleared at the start of every turn (see the per-turn reset) so
                # a later empty turn can never re-announce a stale booking.
                _outcome = session.pop("_booking_outcome_unspoken", "")
                if _outcome:
                    _gate5_fallback = _outcome
                    logger.info(
                        "[ms_gate5] empty turn after a SUCCESSFUL booking — "
                        "speaking the outcome from the tool result instead of "
                        "the re-ask: %r", _outcome[:80],
                    )
                # theorem_v3 (Bug B2): defer the fallback to connection.py's
                # post-turn path instead of emitting it inline.  The v3 loop
                # runs recovery logic AFTER run_turn returns — the booking-ack
                # location question and the FAQ synthetic re-queue both
                # legitimately produce the caller's next prompt.  Emitting the
                # fallback here races ahead of that recovery, so the caller
                # hears "Sorry, I didn't quite catch that" immediately followed
                # by the correct question.  connection.py emits the deferred
                # fallback only if the turn produced NO speech AND queued NO
                # synthetic continuation.  The FlowEngine path is unchanged
                # (it has its own gated global fallback in connection.py).
                from app.clinic_config import is_freeform_clinic as _is_freeform
                if _is_freeform(session.get("clinic_id")):
                    session["_gate5_fallback_pending"] = _gate5_fallback
                    logger.info(
                        "[ms_gate5] no TTS emitted this turn (full_text=%r,"
                        " stop_reason=%r) — fallback DEFERRED to v3 post-turn"
                        " path",
                        bool(full_text.strip()), stop_reason,
                    )
                else:
                    logger.info(
                        "[ms_gate5] no TTS emitted this turn (full_text=%r,"
                        " stop_reason=%r) — substituting fallback",
                        bool(full_text.strip()), stop_reason,
                    )
                    await tts_text_queue.put(_gate5_fallback)
                    # Count this inline fallback as real emission so the
                    # loop-level C8-5 guarantee does not double-speak.
                    _any_tts_emitted = True

        # Expose this call's emission state to the tool loop so it can track
        # whether ANY audio reached the real queue across all iterations
        # (C8-5 silence guarantee).  When the slot buffer is active this reflects
        # puts to the buffer, not the real queue — the loop overrides it with the
        # _flush_slot_buf result in that case.
        session["_oc_emitted_tts"] = _any_tts_emitted

        # Ensure background filler task is cleaned up
        if _filler_task and not _filler_task.done():
            _filler_task.cancel()

        return full_text, tool_uses, False

    # -----------------------------------------------------------------------
    # Tool execution
    # -----------------------------------------------------------------------

    async def _execute_tools(
        self,
        tool_uses: List[dict],
        session: Dict[str, Any],
        call_sid: Optional[str],
        tts_text_queue: Optional[asyncio.Queue] = None,
        messages: Optional[List[dict]] = None,
    ) -> List[dict]:
        """
        Execute all tool calls and return the tool_result blocks for Anthropic.

        tts_text_queue is optional; when provided, filler phrases are played
        concurrently with check_availability and book_appointment calls so the
        caller doesn't hear dead air during API latency.
        """
        from app.tools.receptionist_tools import TOOL_EXECUTORS
        from app.filler_phrases import (
            with_filler,
            THINKING_FILLERS_PRIMARY,
            BOOKING_WRITE_FILLERS,
            LOOKUP_FILLERS,
            CANCEL_WRITE_FILLERS,
            RESCHEDULE_WRITE_FILLERS,
            CALLBACK_FILLERS,
            WAITLIST_FILLERS,
        )

        # Tools that get filler phrases → list to draw from
        _FILLER_TOOLS = {
            "check_availability": THINKING_FILLERS_PRIMARY,
            "book_appointment":   BOOKING_WRITE_FILLERS,
            # lookup_patient uses generic "finding that for you" fillers — it
            # runs both when finding an appointment AND on the cancel/reschedule
            # confirmation wait, where "checking the diary" wording is wrong (P17).
            "lookup_patient":     LOOKUP_FILLERS,
            # The two destructive/anxious writes had no filler at all, so the
            # calendar round-trip after the caller's go-ahead was silence. B-40
            # measured 11.1 s of it on a cancel. A gate refusal returns above
            # this branch, so neither can be spoken over a blocked write.
            "cancel_appointment":     CANCEL_WRITE_FILLERS,
            "reschedule_appointment": RESCHEDULE_WRITE_FILLERS,
            # NOT LOOKUP_FILLERS: every phrase there is about an appointment
            # the caller already has, and a caller asking for a ring-back or a
            # waitlist place has none. CAa0f76e2c (VE, 2026-08-20) heard "just
            # pulling your appointment up" having never booked anything.
            "request_callback":       CALLBACK_FILLERS,
            "add_to_waitlist":        WAITLIST_FILLERS,
        }

        result_blocks: List[dict] = []

        for tu in tool_uses:
            tool_name = tu["name"]
            args      = tu["input"]

            logger.info(
                "[ms_llm] tool: name=%s id=%s args=%s",
                tool_name, tu["id"], json.dumps(args, default=str)[:200],
            )

            try:
                # ── Cache invalidation: new date_hint targets a different week ─
                # Before the dedup guard fires, check whether the incoming
                # date_hint refers to a materially different week than the one
                # used to populate last_offered_slots.  If so, clear the cache
                # so the guard below falls through to a real API call.
                #
                # "Materially different" = different week reference (next week
                # vs the week after, etc.).  A change in time-of-day filter
                # alone (mornings → afternoons within the same week) does NOT
                # invalidate the cache.
                #
                # last_date_hint is written here so it survives across turns and
                # is always the hint that produced the current last_offered_slots.
                if tool_name == "check_availability":
                    _new_hint = str(
                        args.get("date_hint") or args.get("preference") or ""
                    )
                    _last_hint = str(session.get("last_date_hint") or "")
                    if (
                        session.get("last_offered_slots")
                        and _date_hints_differ_materially(_new_hint, _last_hint)
                    ):
                        logger.info(
                            "[ms_llm] check_availability cache INVALIDATED — "
                            "date_hint changed from %r to %r; running fresh check "
                            "call_sid=%s",
                            _last_hint, _new_hint, call_sid,
                        )
                        session["last_offered_slots"] = None
                    # Always track the latest hint so the next call can compare.
                    session["last_date_hint"] = _new_hint

                # Dedup guard: block check_availability if slots were already
                # retrieved this turn (last_offered_slots populated).  The LLM
                # must use the data already returned rather than re-fetching.
                # Allows a second call only if the session key was cleared
                # upstream (e.g. caller explicitly asked for a new date range
                # and connection.py cleared last_offered_slots, or the cache
                # invalidation above detected a new week reference).
                #
                # Post-collect guard: block check_availability once name AND
                # phone are both confirmed.  At that point the slot is already
                # agreed — re-running availability causes Haiku's slot buffer to
                # misfire and ask for the name a second time.
                #
                # ...UNLESS the caller is asking for a different day or time
                # (Bug B, 2026-07-30). "The slot is already agreed" is only true
                # while nobody is trying to change it. CAc6b971ad: the caller asked
                # for Wednesday seven times after giving name and phone; this guard
                # blocked every availability check and told the model to repeat the
                # Tuesday confirmation verbatim, so she twice announced "let me
                # check Wednesday" and then re-read Tuesday. He hung up unbooked.
                #
                # The neighbouring guard below already stands down on
                # _caller_wants_new_slot; the escape simply was never wired into
                # this one. A purpose-built predicate is used instead of that
                # broader helper, which also matches any digit and "no"/"not" and
                # would release this guard on ordinary turns — see the note on
                # _DIFFERENT_DAY_WORDS.
                # B-46 (2026-08-03): the whole condition lives in
                # _post_collect_readback_due, which gates on phone_confirmed
                # rather than collected["phone"] — the latter is pre-loaded from
                # the Twilio caller-ID at connect and is therefore always
                # present, which made this guard fire before any slot had been
                # offered. See that predicate for the full reasoning.
                #
                # main fixed the same defect first; this keeps the two
                # latency-eval-only protections main does not have — the
                # _caller_requests_new_day_or_time escape (Bug B, 2026-07-30)
                # and the BUG-14 name/location injection below.
                _col = session.get("collected") or {}
                if _post_collect_readback_due(tool_name, session, messages):
                    logger.warning(
                        "[ms_llm] check_availability BLOCKED — name collected + "
                        "phone CONFIRMED; forcing booking readback call_sid=%s",
                        call_sid,
                    )
                    # BUG-14: inject the KNOWN full name + location from session so
                    # the forced readback can't drop them. The old template left the
                    # model to reconstruct everything from history and it dropped the
                    # surname AND the whole slot → "So that's Quentin, —". The slot
                    # day/date/time is not stored in session on the template path, so
                    # it still comes from history — but instruct hard it MUST be
                    # included and never left blank.
                    _rb_name = (_col.get("full_name") or _col.get("name") or "").strip()
                    _rb_loc = (session.get("selected_location") or "").strip().title()
                    _rb_name_txt = _rb_name or "[full name INCLUDING surname]"
                    _rb_loc_clause = f" at {_rb_loc}" if _rb_loc else ""
                    # DEFECT-3 fix: the connection layer captures the exact slot the
                    # caller agreed to (from the name-request readback) into
                    # v3_confirmed_slot_phrase.  When present, inject it verbatim so
                    # the model can never dead-end into "I don't have a slot
                    # confirmed for you yet" (Call 1, 2026-07-07).  Falls back to the
                    # old reconstruct-from-history template when it is absent, so the
                    # existing behaviour is unchanged whenever the slot was not
                    # captured.
                    _rb_slot = (session.get("v3_confirmed_slot_phrase") or "").strip()
                    # ...but it is captured on ONE transition — the name request
                    # at the end of the slot flow — so a caller who changes day
                    # after giving their name never refreshes it. It then names a
                    # day the caller has since moved off, and being injected
                    # "verbatim" makes that authoritative.
                    #
                    # last_spoken_slot_date is rewritten on EVERY commitment
                    # sentence, so it is the day the caller most recently agreed
                    # to. If the captured phrase names a different day, it is
                    # stale and the newest agreement wins — the same
                    # newest-wins rule the write-guard is built on.
                    if _rb_slot and session.get("last_spoken_slot_date"):
                        _rb_slot_date = _phrase_date(_rb_slot)
                        if (
                            _rb_slot_date
                            and _rb_slot_date != session["last_spoken_slot_date"]
                        ):
                            logger.warning(
                                "[ms_llm] v3_confirmed_slot_phrase is stale (%s, "
                                "caller has since agreed %s) — using the latest "
                                "spoken slot. call_sid=%s",
                                _rb_slot_date, session["last_spoken_slot_date"],
                                call_sid,
                            )
                            _rb_slot = ""
                    if _rb_slot:
                        _rb_msg = (
                            "Name, phone number and the appointment slot are all "
                            "already confirmed. Do NOT call check_availability and "
                            "do NOT ask for the day or time again. Say EXACTLY this, "
                            "then stop: "
                            f"\"So that's {_rb_name_txt}, {_rb_slot}"
                            f"{_rb_loc_clause} — shall I go ahead and book that in?\""
                        )
                    elif session.get("last_spoken_slot_phrase"):
                        # The slot most recently AGREED, taken from the last
                        # commitment sentence actually spoken to the caller.
                        #
                        # CA42486ff4 (31 Jul 2026): the branch below used to tell
                        # the model to fill the day "from the slot the caller
                        # already agreed to earlier in this conversation". That
                        # call had two agreements earlier in the conversation —
                        # Tuesday 6:30, then Wednesday 6:15 after he changed his
                        # mind — so the instruction was ambiguous by construction.
                        # The model took Tuesday's date and Wednesday's time and
                        # tried to book "Tuesday the 4th at quarter past six", a
                        # slot that existed on no calendar. The write-guard caught
                        # it, but by then the wrong day had already been said.
                        #
                        # _note_spoken_slot_date overwrites on every commitment, so
                        # this is the LATEST agreement, not the first — that is
                        # what lets the caller change day and still get booked.
                        _rb_spoken = session["last_spoken_slot_phrase"]
                        _rb_msg = (
                            "Name and phone number are already confirmed. Do NOT "
                            "call check_availability and do NOT ask for the day or "
                            "time again. The slot the caller agreed to is "
                            f"\"{_rb_spoken}\" — that exact day and time, not any "
                            "other day or time mentioned earlier in this "
                            "conversation. Say EXACTLY this, then stop: "
                            f"\"So that's {_rb_name_txt}, {_rb_spoken}"
                            f"{_rb_loc_clause} — shall I go ahead and book that in?\""
                        )
                    else:
                        # No commitment sentence has been spoken yet, so there is
                        # genuinely nothing to quote. Left as it was — but this is
                        # now the rare path, not the one a day change lands on.
                        _rb_msg = (
                            "Name and phone number are already confirmed. "
                            "Do NOT call check_availability. Produce the booking "
                            "summary now, using this EXACT shape and filling the "
                            "day/date/time from the slot the caller already agreed "
                            "to earlier in this conversation. You MUST include the "
                            "specific day, date and time — never leave them blank — "
                            "and use the full name including surname exactly as "
                            "confirmed (do NOT shorten to the first name only): "
                            f"\"So that's {_rb_name_txt}, [day] the [ordinal] of "
                            f"[month] at [time]{_rb_loc_clause} — shall I go ahead "
                            "and book that in?\""
                        )
                    result = {
                        "error": "booking_details_already_complete",
                        "message": _rb_msg + _NOT_AVAILABILITY_NEWS,
                    }
                    # Every branch above ends with "Say EXACTLY this, then stop"
                    # or "Produce the booking summary now" — the required next
                    # action is SPEECH, and there is no tool that could serve it.
                    # Take the choice away rather than repeating the instruction:
                    # on CAd34a122247 the model re-called check_availability
                    # after reading this exact message, on two separate turns,
                    # burning a ~2.3s round trip each time. See
                    # FORCE_TEXT_NEXT_ITERATION in config.py.
                    session[FORCE_TEXT_NEXT_ITERATION] = True
                elif (
                    tool_name == "check_availability"
                    and session.get("v3_confirmed_slot_phrase")
                    and not session.get("last_offered_slots")
                    and not _caller_wants_new_slot(messages or [])
                    # Releasing the post-collect guard above and not this one
                    # would have changed nothing on CA166de2a9: every blocked
                    # turn also satisfied this condition — a confirmed phrase, an
                    # empty cache, and "yes please" as the utterance, which
                    # carries no digit and no new-slot word. The next `elif`
                    # would simply have taken over the blocking. Both stand down
                    # on a failed write, or neither does.
                    and not session.get(BOOKING_WRITE_FAILED_KEY)
                ):
                    # Slot-locked guard: the caller has already agreed a specific
                    # slot (v3_confirmed_slot_phrase set by the connection layer)
                    # and we are now collecting the name/number.  A re-run of
                    # check_availability here is spurious (Call 2, 2026-07-07:
                    # after "yes that's right" the model re-searched, cancelling
                    # the surname question and glitching into "Sorry, I didn't
                    # quite catch that").  Blocked UNLESS the caller signalled a
                    # new date/time (handled above by _caller_wants_new_slot) or
                    # fresh slots were offered this turn (last_offered_slots) — a
                    # real slot change still searches.
                    logger.warning(
                        "[ms_llm] check_availability BLOCKED — slot already "
                        "confirmed (%r), name collection in progress, no new-date "
                        "intent call_sid=%s",
                        session.get("v3_confirmed_slot_phrase"), call_sid,
                    )
                    result = {
                        "status": "slot_already_confirmed",
                        "message": (
                            "A specific appointment slot is already confirmed with "
                            "the caller (\""
                            + str(session.get("v3_confirmed_slot_phrase"))
                            + "\"). Do NOT call check_availability. Continue "
                            "collecting the caller's first name, surname and phone "
                            "number, then read back the booking summary."
                            + _NOT_AVAILABILITY_NEWS
                        ),
                    }
                elif (
                    tool_name == "check_availability"
                    and session.get("last_offered_slots")
                    # A DIFFERENT-DAY request must not be answered from this day's
                    # remaining times (Bug B). Everything below serves slots from
                    # session["available_days"], and its else-branch returns
                    # already_retrieved — "present the existing slots" — which is
                    # how the caller who asked for Wednesday was offered Tuesday
                    # twice more even after the guard above stood down. Fall through
                    # to the real check_availability for a genuinely new day.
                    #
                    # Same-day time requests ("anything later?") deliberately still
                    # come here: re-fetching leads with the earliest times again,
                    # which is what 368b4e0 (V5) exists to prevent.
                    and not _caller_requests_different_day(messages or [], session)
                    # B-99. Everything below subtracts from available_days,
                    # which is what SURVIVED the caller's time-of-day band. If
                    # the band hid times on the day now on the table, that copy
                    # is not the day: the follow-up can only re-offer survivors
                    # and then report the day full while a bookable appointment
                    # sits behind the filter. On CA890b511e Susie said "I don't
                    # have any further times on that day" at 08:42:49 and that
                    # same day produced a midday at 08:43:39.
                    #
                    # A real lookup is the one path that can reach them --
                    # B-98's band-spent rule opens the day once its in-band
                    # times have been spoken -- so stand down and let it run.
                    and not _followup_must_yield_to_a_real_lookup(
                        session, messages
                    )
                ):
                    # V5: if the caller asked for later / an unspoken time, do NOT
                    # tell the model to re-present the already-spoken slots.
                    # Serve the next unspoken batch (or confirm a resolved time)
                    # from session["available_days"].
                    from app.tools.slot_followup import (
                        remaining_slots_after_offer,
                        all_remaining_on_next_day,
                        utterance_requests_more_slots,
                        utterance_accepts_offered_slot,
                        resolve_requested_time,
                        apply_next_batch_to_session,
                        apply_resolved_time_to_session,
                        build_followup_tool_result,
                        remaining_unspoken,
                        remaining_unspoken_on_current_day,
                        exhaustion_claim_is_supported,
                    )
                    _days = session.get("available_days") or []
                    _offered = session.get("last_offered_slots") or []
                    # Cumulative across the day (B-78b): subtracting only the
                    # CURRENT offer makes the previous batch unoffered again and
                    # the caller ping-pongs between two pairs for ever.
                    _remaining = remaining_unspoken(session)
                    _user = _last_user_text(messages or [])
                    # _days as well (B-114). This is the SECOND branch that
                    # resolves a caller time phrase, and it hands the model
                    # an instruction to confirm and offer to book -- so a
                    # wrong hit here is louder than one on the speech path.
                    _hit = (
                        resolve_requested_time(_user, _remaining, _days)
                        if _remaining else None
                    )
                    if _hit is not None:
                        apply_resolved_time_to_session(session, _hit)
                        result = build_followup_tool_result(_days, [_hit], more=False)
                        result["message"] = (
                            "Caller named an unspoken time that IS available. "
                            "Confirm it and ask whether to book — do NOT say it "
                            "is unavailable."
                        )
                        logger.info(
                            "[ms_llm] check_availability → unspoken time hit %s "
                            "call_sid=%s", _hit.get("start"), call_sid,
                        )
                    elif _remaining and utterance_requests_more_slots(_user):
                        # Day-scoped: _remaining spans the whole sweep, so
                        # an unscoped batch answers "anything else that day?"
                        # with whichever day sorts first. _remaining itself
                        # stays whole for the named-time hit above.
                        _batch, _more = all_remaining_on_next_day(
                            remaining_unspoken_on_current_day(session)
                        )
                        if not _batch:
                            # B-99. An empty batch is NOT an answer here.
                            #
                            # apply_next_batch_to_session would set
                            # last_offered_slots to [] -- destroying the only
                            # record of what is on the table, which is what
                            # this whole module reads -- and
                            # build_followup_tool_result would return "No
                            # further times on that day", a completeness claim
                            # about a day taken from last_offered_slots[0].
                            # On the three-day offer of CA890b511e that day is
                            # not the day the caller asked about.
                            #
                            # exhaustion_claim_is_supported owns the question.
                            # When it says no, say nothing about the day: ask
                            # which one they mean. That terminates -- their
                            # answer names a day, _caller_requests_different_day
                            # fires on the next turn, and a real lookup runs.
                            if exhaustion_claim_is_supported(session):
                                result = build_followup_tool_result(
                                    _days, [], False,
                                )
                            else:
                                logger.warning(
                                    "[ms_llm] follow-up came up empty on a day "
                                    "it cannot name — offer spans %d day(s); "
                                    "asking which day instead of claiming the "
                                    "day is full (B-99) call_sid=%s",
                                    len({
                                        str((o or {}).get("start") or "")[:10]
                                        for o in _offered
                                    }),
                                    call_sid,
                                )
                                result = {
                                    "status": "which_day",
                                    "message": (
                                        "You offered times on more than one day, "
                                        "so which day the caller means is not "
                                        "established. Do NOT say you have no "
                                        "further times on that day — you cannot "
                                        "know that here. Ask which day they "
                                        "mean, then check that day."
                                    ),
                                    **_presentation_for_refusal(session, _days),
                                }
                        else:
                            apply_next_batch_to_session(session, _batch, _more)
                            result = build_followup_tool_result(
                                _days, _batch, _more,
                            )
                        logger.info(
                            "[ms_llm] check_availability → next unspoken batch "
                            "%s call_sid=%s",
                            [s.get("start") for s in _batch], call_sid,
                        )
                    else:
                        # Job 3c.1 / CAce1457d1: on "that works for me" the model
                        # re-called check_availability. The old already_retrieved
                        # message told it to "present the existing slots" — so
                        # the caller heard the same offer again and had to accept
                        # twice (~24s when Spec I had also wiped the cache).
                        if utterance_accepts_offered_slot(_user):
                            logger.warning(
                                "[ms_llm] check_availability BLOCKED — caller is "
                                "accepting an already-offered slot; do not re-list "
                                "call_sid=%s user=%r",
                                call_sid, (_user or "")[:60],
                            )
                            result = {
                                "status": "slot_offer_still_live",
                                "message": (
                                    "The caller is responding to the slots you "
                                    "already offered. Do NOT call "
                                    "check_availability again and Do NOT re-list "
                                    "the times. If they accepted a specific time, "
                                    "confirm that slot and move to collecting "
                                    "their name. If they said a non-specific "
                                    "'that works' / 'any of those', ask which of "
                                    "the offered times they want — then move on. "
                                    "Do NOT present the existing slots again."
                                ),
                                **_presentation_for_refusal(
                                    session, session.get("available_days") or []
                                ),
                            }
                            session[FORCE_TEXT_NEXT_ITERATION] = True
                        else:
                            logger.warning(
                                "[ms_llm] check_availability BLOCKED — slots already retrieved "
                                "this turn (last_offered_slots present); returning cached result "
                                "call_sid=%s", call_sid,
                            )
                            result = {
                                "status": "already_retrieved",
                                "message": (
                                    "check_availability has already returned slot data. "
                                    "Do NOT call it again. Read out ONLY the times in "
                                    "first_day.slot_times_spoken, verbatim and in the order "
                                    "given — they have already been chosen and limited for "
                                    "you. Do NOT read anything from available_days and do "
                                    "NOT re-order them. If more_times is true, say you have "
                                    "a few others that day rather than naming them."
                                ),
                                **_presentation_for_refusal(
                                    session, session.get("available_days") or []
                                ),
                            }
                elif (
                    tool_name == "book_appointment"
                    and under_age_blocks_booking(session)
                ):
                    # ── Under-age backstop ────────────────────────────────────
                    # CA7d7c109b (VE acceptance run, 4 Aug): the caller raised an
                    # under-18, Susie declined — correctly — and then asked for a
                    # day and time anyway. The decline was prompt text; nothing
                    # deterministic stopped the booking machinery.
                    #
                    # The clinic's own policy machinery could not help. Its
                    # `never_autobook` entry ("Anyone under 18") is read by no
                    # Python, and `evaluate_policy_gate`, which has a real minor
                    # check, is reachable only from flow.py — bypassed on every
                    # live clinic. So this write was ungated entirely.
                    #
                    # First in the book_appointment chain deliberately: there is
                    # no point validating a slot or a confirmation for an
                    # appointment the clinic will not accept. Clinic-gated via
                    # minimum_age_years, which jv_v1 does not set — its policy is
                    # "No minimum age".
                    _ua = session.get("_under_age_declared")
                    # The minimum is READ, never written into the sentence as a
                    # literal. It was "18" here and in _b7_call_state, which was
                    # true of the only clinic that had the gate (Vital Edge) and
                    # would have been a lie the moment a second clinic switched
                    # it on with a different number — Susie would have refused
                    # correctly and then quoted a policy the clinic does not
                    # have. Same reason `minimum_age` (prose) and
                    # `minimum_age_years` (engine) are separate keys: a
                    # safeguarding sentence must not depend on copywriting.
                    try:
                        from app.clinic_config import get_clinic as _ua_clinic
                        from app.tools.receptionist_tools import (
                            minimum_age_years as _ua_min,
                        )
                        _min = _ua_min(_ua_clinic(session.get("clinic_id")) or {})
                    except Exception:
                        _min = None
                    logger.error(
                        "[ms_llm] book_appointment BLOCKED — caller stated age "
                        "%s, below the clinic minimum (%s). Booking refused for "
                        "the rest of this call.", _ua, _min,
                    )
                    # A gate that armed can only have armed because a minimum
                    # was configured, so _min is None only if config changed
                    # mid-call. Fall back to naming no number rather than
                    # naming the wrong one.
                    _floor = (
                        f"appointments are for those aged {_min} and over"
                        if _min is not None
                        else "they are below the age this clinic can see"
                    )
                    result = {
                        "status": "under_age_declined",
                        "message": (
                            f"book_appointment is REFUSED. The caller has said "
                            f"they are {_ua}, which is below this clinic's "
                            f"minimum age. Do NOT book, do NOT offer times, and "
                            f"do NOT ask for a day, a name or a number — the "
                            f"appointment cannot go ahead whatever they answer. "
                            f"Say kindly that {_floor}, so this is not something "
                            f"you can book, and do not offer an alternative "
                            f"appointment. If they ask why, it is the clinic's "
                            f"policy. You may still answer general questions."
                        ),
                    }
                elif (
                    tool_name == "book_appointment"
                    and _slot_date_disagrees_with_speech(args, session)
                ):
                    # C1 write-guard (2026-07-30). The slot about to be written is
                    # on a different DAY from the one we last told the caller.
                    #
                    # CA5c4fb14f: "Tuesday the 4th of August at seven in the
                    # evening" -> "All booked" -> event on 2026-08-05, a Wednesday.
                    # He would have arrived to nothing. Nothing else can catch this:
                    # the booking matches the slot, so every downstream check is
                    # consistent — only the speech disagrees, and by then it has
                    # already been said.
                    #
                    # Fail closed. A booking the caller cannot attend is worse than
                    # one extra question, and this re-steer is a question, so the
                    # call continues rather than dead-ending: the model states the
                    # real day, the caller confirms or corrects, the next spoken
                    # commitment updates last_spoken_slot_date, and the booking then
                    # goes through normally.
                    _spoken_date = session.get("last_spoken_slot_date")
                    logger.error(
                        "[ms_book] BLOCKED — slot_iso %r is not the day the caller "
                        "was told (%s). call_sid=%s",
                        args.get("slot_iso"), _spoken_date, call_sid,
                    )
                    session["_c1_write_guard_fired"] = (
                        int(session.get("_c1_write_guard_fired") or 0) + 1
                    )
                    # Name the real day. Without it the model can only repeat the
                    # day it already said — the wrong one — so the guard fires
                    # again on the identical mismatch and the caller is asked the
                    # same question until they hang up (CAb81fe651, 30 Jul 2026:
                    # the slot was Wednesday, she re-read "Tuesday the 4th" twice).
                    # The slot date is authoritative here: it is the appointment
                    # that would actually exist.
                    _slot_phrase = _spoken_day_phrase(
                        str((args or {}).get("slot_iso") or "")[:10]
                    )
                    _spoken_phrase = _spoken_day_phrase(_spoken_date or "")
                    _correction = (
                        (
                            f" The slot you are holding is on {_slot_phrase}, not "
                            f"{_spoken_phrase or 'the day you last said'}. Say "
                            f"{_slot_phrase} — with the time — and ask if that is "
                            "the one they want."
                        )
                        if _slot_phrase
                        else ""
                    )
                    result = {
                        "status": "slot_date_mismatch",
                        "message": (
                            "NOT booked. The appointment you were about to create is "
                            "on a different DAY from the one you last told the "
                            "caller, so one of them is wrong and you must not guess "
                            "which. Do NOT say they are booked. Tell the caller the "
                            "day and time you can actually offer, in full — weekday, "
                            "date and time — and ask them to confirm it is the one "
                            "they want before you book."
                            + _correction
                        ),
                    }
                elif (
                    tool_name == "book_appointment"
                    and not session.get("surname_captured")
                    and " " not in (session.get("patient_name") or "").strip()
                    and not _surname_step_asked(messages or [])
                ):
                    # Surname backstop (JV name redesign, 2026-07-07).
                    #
                    # JV requires a surname on the booking, but the capture
                    # pipeline reads back only the first name and often locks a
                    # first-name-only record (STT splits "Quentin Rock" across two
                    # turns). Block book_appointment until a surname word is on
                    # record so a first-name-only booking never reaches the
                    # calendar. Placed BEFORE the phone guard so the steer order
                    # is surname (Step 7) → phone (Step 8) → confirmation.
                    #
                    # Signals (all must say "no surname" to block): surname_captured
                    # flag unset AND no space in patient_name. Anti-deadlock:
                    # _surname_step_asked yields the moment the model has asked for
                    # the surname, so a capture miss cannot loop — booking proceeds
                    # and the next caller word is back-filled as the surname.
                    # Reschedule/cancel do not call book_appointment, so untouched.
                    logger.warning(
                        "[ms_llm] book_appointment BLOCKED — surname not captured "
                        "(patient_name=%r) call_sid=%s",
                        session.get("patient_name"), call_sid,
                    )
                    result = {
                        "status": "surname_required",
                        "message": (
                            "book_appointment cannot fire yet — only the caller's "
                            "first name is on record and this clinic requires a "
                            "surname. Do NOT book. Ask for the surname as its own "
                            "turn: \"And your surname?\" Accept WHATEVER they say "
                            "silently — do NOT read it back, spell it, confirm it, "
                            "or ask again. Then read back the booking summary and "
                            "ask \"Shall I go ahead and book that in?\" before "
                            "calling book_appointment."
                        ),
                    }
                elif (
                    tool_name == "book_appointment"
                    and not session.get("phone_confirmed")
                    and not _phone_step_asked(messages or [])
                ):
                    # Phone backstop (JV regression, 2026-07-07 11:39).
                    #
                    # collected["phone"] is ALWAYS pre-filled from the Twilio
                    # caller-ID at call start, and nothing at code level requires
                    # the caller to have actually confirmed a number before
                    # book_appointment fires — phone collection (prompt Step 8)
                    # is enforced by the prompt alone.  When the caller front-
                    # loads the booking (e.g. opens with "book me on this
                    # number"), the model can collapse slot-accept → readback →
                    # book, skipping Step 8, and book with an UNCONFIRMED caller-
                    # ID number (and a first-name-only name).
                    #
                    # Block ONLY when BOTH signals agree the phone step was
                    # skipped: phone_confirmed is unset (no verbal "use this
                    # number" and no DTMF entry ever landed — the authoritative
                    # flag, also what the SMS router trusts) AND the phone
                    # question was never asked anywhere in recent history.  This
                    # cannot loop a legitimate booking — the instant the model
                    # asks the phone question (as steered below) _phone_step_asked
                    # flips True and the next book_appointment proceeds even if
                    # phone_confirmed has not flipped.  Reschedule/cancel do not
                    # call book_appointment, so those flows are untouched.
                    logger.warning(
                        "[ms_llm] book_appointment BLOCKED — phone step skipped "
                        "(phone_confirmed unset, phone question never asked) "
                        "call_sid=%s", call_sid,
                    )
                    # The steer quotes the phone question that fits THIS caller.
                    # It used to hardcode the calling-number offer — "Is the
                    # number you're calling on the best one…" — which is a
                    # question with no answer when the caller withheld their
                    # number or the caller-ID was suppressed. _phone_question_for
                    # is the same helper Gate 5g substitutes, so the steer and
                    # the deterministic replacement cannot drift into two
                    # different scripts.
                    result = {
                        "status": "phone_confirmation_required",
                        "message": (
                            "book_appointment cannot fire yet — the caller's "
                            "phone number has not been confirmed. Do NOT book "
                            "and do NOT assume the calling number. Ask the phone "
                            "question as its own separate turn first: "
                            f"\"{_phone_question_for(session)}\" Wait "
                            "for the caller's answer, then read back the booking "
                            "summary and ask \"Shall I go ahead and book that "
                            "in?\" before calling book_appointment."
                        ),
                    }
                elif (
                    tool_name == "book_appointment"
                    and session.get("_lookup_purpose") == "reschedule"
                    and session.get("_lookup_appointment_id")
                ):
                    # CA3b303f (Emma Clifton, theorem_v3, 2026-08-14): caller
                    # asked to MOVE 1 Sep → 8 Sep; Susie called book_appointment
                    # instead of reschedule_appointment, left the original in
                    # place, then looped forever trying to cancel it. While a
                    # reschedule lookup is still active, a new book is the wrong
                    # tool — steer to the move.
                    logger.warning(
                        "[ms_llm] book_appointment BLOCKED — active reschedule "
                        "lookup (appointment_id=%r); use reschedule_appointment "
                        "call_sid=%s",
                        session.get("_lookup_appointment_id"), call_sid,
                    )
                    result = {
                        "status": "reschedule_required",
                        "message": (
                            "book_appointment cannot fire — this caller already "
                            "has an appointment looked up for reschedule "
                            f"(id {session.get('_lookup_appointment_id')}). "
                            "Do NOT create a second booking. Call "
                            "reschedule_appointment with that appointment_id "
                            "and the new slot once they have confirmed the move. "
                            "If they instead want to cancel the old one and book "
                            "fresh, cancel_appointment first, then book."
                        ),
                    }
                elif tool_name == "book_appointment" and not (
                    _cta_asked(session, _booking_confirmation_asked)
                ):
                    # Booking confirmation guard.
                    #
                    # book_appointment must only fire after the system has
                    # explicitly asked the booking confirmation question
                    # ("Shall I go ahead and book that in?") AND received an
                    # affirmative response to THAT specific question.
                    #
                    # The failure case this prevents: the summary readback is
                    # barged in on mid-sentence and the barge-in contains "yes"
                    # — the LLM can misconstrue that affirmative as booking
                    # confirmation and immediately fire book_appointment.
                    #
                    # Guard: if last_bot_prompt does not contain the booking
                    # confirmation phrases, block the call and instruct the LLM
                    # to ask the confirmation question first.
                    _lbp_preview = (session.get("last_bot_prompt") or "")[:80]
                    logger.warning(
                        "[ms_llm] book_appointment BLOCKED — booking confirmation "
                        "question not yet asked (last_bot_prompt=%r)",
                        _lbp_preview,
                    )
                    result = {
                        "status": "confirmation_required",
                        "message": (
                            "book_appointment cannot fire yet. The booking "
                            "confirmation question has not been asked in the "
                            "current turn. You MUST ask: "
                            "\"Shall I go ahead and book that in?\" "
                            "and wait for the caller to say yes before calling "
                            "book_appointment. Do not book without this explicit "
                            "confirmation."
                        ),
                    }
                elif tool_name == "book_appointment" and not await _book_reply_verdict(
                    messages, session
                ):
                    # FM-01: the confirmation question was asked (the guard above
                    # passed) but the caller has not given a clear yes. Block on a
                    # negative, ambiguous or absent reply, or an affirmative paired
                    # with a correction. A missed booking is recoverable; a wrong
                    # one is not.
                    _lut_preview = _last_user_text(messages or [])[:80]
                    logger.warning(
                        "[ms_llm] book_appointment BLOCKED — no clear caller yes to "
                        "the booking confirmation (last_user_text=%r)", _lut_preview,
                    )
                    result = {
                        "status": "affirmation_required",
                        "message": (
                            "book_appointment cannot fire yet. The booking "
                            "confirmation question was asked but the caller has not "
                            "given a clear yes. Wait for an explicit affirmative "
                            "(\"yes\", \"go ahead\") before calling book_appointment. "
                            "Do not book on an ambiguous, negative, or absent reply."
                        ),
                    }
                elif (
                    tool_name in ("reschedule_appointment", "cancel_appointment")
                    and _lookup_identity_unconfirmed(session)
                ):
                    # ── B-42: identity backstop ──────────────────────────────
                    # CAe74ceae7 (3 Aug 2026): lookup returned "match 1/13
                    # name='Sarah Jenkins'", Susie read back a day and a time and
                    # asked "is that the right one?" WITHOUT the name, the caller
                    # said yes, and Sarah Jenkins's appointment was cancelled.
                    # The caller confirmed a DATE, never a PERSON.
                    #
                    # A shared phone number is ordinary in physiotherapy — a
                    # couple, a parent booking for a child, a carer — so the
                    # same path cancels the wrong family member's appointment
                    # with nobody aware. Placed BEFORE the reschedule and cancel
                    # confirmation gates on purpose: there is no point asking
                    # "shall I move it?" while we do not know whose "it" is.
                    #
                    # A gate rather than prompt wording because the write is
                    # destructive and invisible to the caller. B-36 cause 1 is
                    # the standing evidence that prompt wording alone does not
                    # hold: the model rewords, and the guarantee evaporates.
                    _lp_name = session.get("_lookup_patient_name") or "the patient"
                    # Say WHICH arm failed. The single-cause line was accurate
                    # while there was one, and would now misdirect exactly the
                    # way the reschedule log did before it was split below.
                    from app.tools.receptionist_tools import (
                        LOOKUP_NAME_SPOKEN_KEY as _K_NAME,
                        LOOKUP_SLOT_SPOKEN_KEY as _K_SLOT,
                    )
                    _why = " and ".join(
                        w for w, ok in (
                            ("name not read back (B-42)", session.get(_K_NAME)),
                            ("appointment date not read back (B-54)",
                             session.get(_K_SLOT)),
                        ) if not ok
                    )
                    logger.warning(
                        "[ms_llm] %s BLOCKED — ambiguous lookup, %s: name=%r "
                        "when=%r matches>1",
                        tool_name, _why, _lp_name,
                        session.get("_lookup_appointment_datetime"),
                    )
                    _lp_count = session.get("_lookup_match_count") or 0
                    _lp_howmany = (
                        f"{_lp_count} upcoming appointments"
                        if _lp_count > 1 else "more than one upcoming appointment"
                    )
                    result = {
                        "status": "identity_confirmation_required",
                        "message": (
                            f"There are {_lp_howmany} on this phone number, so "
                            f"you know neither which person you are talking to "
                            f"nor which of the appointments they mean. The one "
                            f"you have selected is under the name {_lp_name}. "
                            f"Do NOT cancel or move anything yet. Tell the "
                            f"caller HOW MANY there are — they cannot ask for a "
                            f"different one if they do not know others exist — "
                            f"then say that name AND the day and date and time, "
                            f"and ask BOTH questions — for example \"I've got "
                            f"{_lp_howmany} on this number; this one's under "
                            f"{_lp_name}, on Tuesday the 5th at half past "
                            f"eight in the evening — is that you? And is that "
                            f"the one you mean?\". The day and date are not "
                            f"optional: "
                            f"naming the person does not tell a caller which "
                            f"of their own appointments you are about to "
                            f"touch, and that is what went wrong on the call "
                            f"this rule exists for. If they say it is not "
                            f"them, OR that it is not the appointment they "
                            f"meant, call lookup_patient again with next=true "
                            f"to step to the following match."
                        ),
                    }
                elif tool_name == "reschedule_appointment" and not (
                    _cta_asked(session, _move_confirmation_asked)
                    and await _book_reply_verdict(messages, session)
                ):
                    # FM-23: reschedule gate — mirrors FM-01. Require the move
                    # confirmation question in last_bot_prompt AND a clear caller
                    # yes. The CTA test was a single literal ("move it for you")
                    # until 3 Aug 2026; see _move_confirmation_asked for the live
                    # call where the model asked the question in other words, the
                    # gate short-circuited before ever reading the caller's yes,
                    # and the reschedule silently did not happen.
                    _lut_preview = _last_user_text(messages or [])[:80]
                    _lbp_preview = (session.get("last_bot_prompt") or "")[:80]
                    # Log WHICH arm failed. The old line always blamed the
                    # caller's reply — on the call above the reply was fine and
                    # the CTA test was at fault, and the message actively
                    # misdirected the investigation.
                    _cta_ok = _cta_asked(session, _move_confirmation_asked)
                    logger.warning(
                        "[ms_llm] reschedule_appointment BLOCKED — %s "
                        "(last_bot_prompt=%r last_user_text=%r)",
                        (
                            "no clear caller yes after the move confirmation"
                            if _cta_ok else
                            "the move confirmation question was never asked"
                        ),
                        _lbp_preview, _lut_preview,
                    )
                    result = {
                        "status": "reschedule_confirmation_required",
                        "message": (
                            "reschedule_appointment cannot fire yet. Ask 'Shall I go "
                            "ahead and move it for you?' and wait for a clear yes "
                            "before calling reschedule_appointment. Do not reschedule "
                            "on an ambiguous, negative, or absent reply."
                        ),
                    }
                elif tool_name == "cancel_appointment" and not (
                    _cta_asked(session, _cancel_retention_asked)
                    and _cancel_reply_consents(messages, session)
                ):
                    # FM-23: cancel is DESTRUCTIVE. The template cancel flow's confirm
                    # is the retention question ("...or cancel it altogether?") and the
                    # caller consents by SAYING "cancel" — so _book_reply_is_affirmative
                    # can't be reused ("cancel" is a NO pattern). Require BOTH the
                    # retention question in last_bot_prompt AND an explicit cancel
                    # token; a bare "yes", a reschedule word, "keep it", "don't cancel"
                    # or "no" all block. A missed cancel re-asks; a wrong one deletes a
                    # real patient's appointment.
                    _lut_preview = _last_user_text(messages or [])[:80]
                    logger.warning(
                        "[ms_llm] cancel_appointment BLOCKED — no explicit caller "
                        "cancel-consent (last_user_text=%r)", _lut_preview,
                    )
                    result = {
                        "status": "cancellation_confirmation_required",
                        "message": (
                            "cancel_appointment cannot fire yet. Ask for consent in "
                            "the wording your instructions give you — either "
                            "'Shall I go ahead and cancel that?' or the retention "
                            "question 'Would you like to reschedule this "
                            "appointment, or cancel it altogether?' — and wait for "
                            "a clear answer. After the retention question only an "
                            "explicit 'cancel' counts, because a bare 'yes' does "
                            "not say which option was chosen. Do not cancel on a "
                            "reschedule request, or on an ambiguous, negative or "
                            "absent reply."
                        ),
                    }
                elif tool_name == "escalate_to_claude":
                    result = await self._exec_escalate(args, session)
                else:
                    executor = TOOL_EXECUTORS.get(tool_name)
                    if executor:
                        # How long the provider actually took. Nothing else
                        # measures this: a tool round-trip is absorbed silently
                        # into llm_ttft_ms, so "is 1800ms the right moment to
                        # speak?" has never been answerable from data. Gated on
                        # LATENCY_TIMING like every other timing capture, so the
                        # default path is one falsy check.
                        from .latency_timing import LATENCY_TIMING as _LAT_ON
                        _t_tool0 = time.monotonic() if _LAT_ON else None
                        # Filler phrases: play concurrently for slow API tools
                        # The wording comes from the WORK, not the tool name:
                        # book_appointment on a provisional clinic is a request,
                        # not a booking, and three stored Vital Edge calls said
                        # "Just locking that in now…" one sentence before "sent
                        # it to Jonathan… subject to his confirmation".
                        #
                        # This is also the earliest TRUTHFUL moment to speak —
                        # the tool is about to run, so unlike the 1800ms ack
                        # filler nothing here is guessing.
                        from app.hold_speech import (
                            clinic_facts as _hs_facts,
                            decide_hold as _hs_decide,
                            hold_speech_enabled as _hs_on,
                            work_for_tool as _hs_work,
                        )
                        _hs_legacy = not _hs_on(session)
                        _hs_prov, _hs_prac = _hs_facts(session)
                        _hs_decision = _hs_decide(
                            legacy=_hs_legacy,
                            session=session,
                            kind=_hs_work(tool_name, provisional=_hs_prov),
                            head_already_spoken=bool(
                                session.get("_hold_head_spoken")
                            ),
                            practitioner=_hs_prac,
                            heads_used=len(session.get("used_fillers") or []),
                        )
                        # A single-item list: with_filler still owns the
                        # concurrency, the 4s escalation and the shielding —
                        # all of it tested — but no longer owns the choice.
                        # Legacy took the per-TOOL list and handed the whole
                        # thing to with_filler, which owns the escalation. The
                        # arbiter narrows it to one chosen head instead.
                        _filler_list = (
                            _FILLER_TOOLS.get(tool_name) if _hs_legacy
                            else ([_hs_decision.head] if _hs_decision.speak else None)
                        )
                        if _filler_list and tts_text_queue is not None:
                            async def _tts_fn(text: str, _q=tts_text_queue) -> None:
                                await _q.put(text)
                            result = await with_filler(
                                api_coro=executor(args, session),
                                filler_list=_filler_list,
                                session=session,
                                tts_fn=_tts_fn,
                                # FillerGuard arms at turn start and fires at
                                # 350ms, before anyone knows a tool is coming.
                                # If it spoke, the caller has already been told
                                # to hold on and this list would say it again.
                                skip_primary=bool(
                                    session.get("_filler_clip_spoke_this_turn")
                                ),
                            )
                        else:
                            result = await executor(args, session)

                        if _t_tool0 is not None:
                            session.setdefault("lat_tools", []).append({
                                "tool": tool_name,
                                "ms":   int((time.monotonic() - _t_tool0) * 1000),
                            })

                        # Mark slots as presented the moment check_availability
                        # returns slots so the LLM knows not to re-present them.
                        if tool_name == "check_availability" and session.get("last_offered_slots"):
                            session["slots_presented"] = True
                            n = len(session["last_offered_slots"])
                            session["slots_count"] = n
                            logger.info(
                                "[ms_llm] slots_presented=True slots_count=%d", n,
                            )
                    else:
                        logger.warning("[ms_llm] unknown tool: %s", tool_name)
                        result = {"error": f"Unknown tool: {tool_name}"}
            except Exception as exc:
                logger.error("[ms_llm] tool %s error: %r", tool_name, exc)
                result = {"error": str(exc)}

            # Track, fresh per call, whether THIS check_availability returned
            # usable slots.  Derived from the tool result itself so it is never
            # stale across turns (slots_count above is only written on success
            # and would otherwise carry over from a previous turn).  The slot-
            # buffer re-arm reads this to choose Haiku (slots → format) vs
            # Sonnet (no slots → explain + offer alternative).  Without it a
            # zero-slot result armed the focused Haiku prompt, which has no
            # handling for an empty result and emitted silence (C8-5).
            if tool_name == "check_availability":
                session["_check_av_had_slots"] = _note_availability_seen(
                    session, result
                )
                # Ground truth for the "a few others that day" tail, captured
                # from the tool result the formatter is about to be shown.
                # THREE producers set more_times — _check_availability_acuity
                # (single_day >3 times), _cap_presented_slots (spoken list
                # trimmed) and slot_followup.build_followup_tool_result — and
                # all three land here, so this is the one place that sees every
                # presentation. _flush_slot_buf reconciles the spoken text
                # against it. See reconcile_extra_slots_claim.
                # presentation_mode gates the APPEND only: the tail says "that
                # day", which a multi_day reply (two different days named) has
                # no referent for. Stripping is unconditional.
                _fd = result.get("first_day") if isinstance(result, dict) else None
                if isinstance(_fd, dict):
                    session["_slot_more_times"] = bool(_fd.get("more_times"))
                    session["_slot_n_offered"] = len(_fd.get("slot_times") or [])
                elif isinstance(result, dict):
                    session["_slot_more_times"] = bool(result.get("more_times"))
                    session["_slot_n_offered"] = 2
                else:
                    session["_slot_more_times"] = False
                    session["_slot_n_offered"] = 2
                # B-111. The dates the payload is holding back, captured in
                # the same place and for the same reason as more_times: the
                # sentence naming them is a claim about the clinic's calendar,
                # so it is built from the tool result rather than by the model.
                # B-117. Same contract and the same reason as more_times:
                # whether this caller has used up the band they asked for is
                # decided by the retrieval path, which is the only thing that
                # knows, and carried here rather than guessed by the model.
                session["_slot_band_spent_label"] = (
                    result.get("band_spent_label")
                    if isinstance(result, dict) else ""
                ) or ""
                session["_slot_other_dates"] = (
                    result.get("other_dates_for_requested_day")
                    if isinstance(result, dict) else None
                )
                session["_slot_presentation_mode"] = (
                    result.get("presentation_mode")
                    if isinstance(result, dict) else None
                )
                # ── Deterministic single_day presentation ────────────────────
                # Step 3 of docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md. The
                # sentence and the record of what it named are built HERE, from
                # the payload, by one function — so they cannot disagree. The
                # model still runs and its output is discarded in
                # _flush_slot_buf; removing that call is a separate,
                # latency-only change.
                #
                # `first_day` is ALREADY trimmed to the positions
                # choose_presented_indices picked, which prefers times this
                # caller has not heard (B-116). That selection is NOT taken over
                # here — only the words are — so more_times is passed in rather
                # than recomputed off a list that has had slots removed on
                # purpose.
                #
                # multi_day is wired below, as step 4. It is where the parse
                # failure actually lives: measured over the stored corpus on
                # 1 Sept 2026, 51 of 52 multi_day readouts (98%) hand the
                # positional resolver a DAY-only label -- "Monday 10th August"
                # -- because extract_slot_options commits to the segment before
                # the em dash. A day label matches every slot on that day and
                # the resolver refuses an ambiguous match by design, so
                # multi_day does not fail occasionally; it cannot succeed.
                session.pop("_slot_offer_prebuilt", None)
                _offer = None
                _det_mode = None
                _det_day_iso = None
                if (
                    session.get("_slot_presentation_mode") == "single_day"
                    and isinstance(_fd, dict)
                    and (_fd.get("slots") or _fd.get("slot_times"))
                ):
                    _det_mode = "single_day"
                    try:
                        from app.tools.slot_offer import (
                            build_slot_offer, earliest_lead_in_is_true,
                        )
                        # B-125. "The earliest I have is ..." is a RANKING claim
                        # about the day, and first_day has already had heard
                        # times removed from it (B-116), so its first slot is
                        # not necessarily the day's. Gate 5a-f catches that on
                        # the model path; a payload-built sentence never reaches
                        # Gate 5, so it is decided here against the UNTRIMMED
                        # day and dropped to the neutral opener when it cannot
                        # be established.
                        _lead_in = (
                            str(result.get("lead_in") or "")
                            if isinstance(result, dict) else ""
                        )
                        if _lead_in == "earliest":
                            _full = next(
                                (
                                    d for d in (
                                        (result or {}).get("available_days") or []
                                    )
                                    if isinstance(d, dict)
                                    and d.get("date") == _fd.get("date")
                                ),
                                None,
                            )
                            if not earliest_lead_in_is_true(_full, _fd):
                                logger.info(
                                    "[ms_gate5] earliest lead-in dropped — the "
                                    "presented list does not start at the day's "
                                    "first slot (B-125)"
                                )
                                _lead_in = ""
                        _offer = build_slot_offer(
                            [_fd],
                            lead_in=_lead_in,
                            more_times=bool(session.get("_slot_more_times")),
                        )
                    except Exception:
                        # A caller mid-booking must not lose the offer to a
                        # formatter fault. Falling through leaves the model's
                        # presentation and the existing repair layer exactly as
                        # they were.
                        logger.exception(
                            "[ms_gate5] deterministic slot offer failed — "
                            "falling back to the model's presentation"
                        )
                        _offer = None
                # ── Deterministic multi_day presentation (step 4) ────────────
                # Fed `presented_days`, NOT `available_days`, and that choice is
                # the whole point rather than a detail:
                #
                #   * `presented_days` is what `_cap_presented_slots` already
                #     decided should be SPOKEN -- <= _MAX_PRESENTED_DAYS (3) days
                #     at _MAX_PRESENTED_TIMES_MULTI_DAY (2) times each. Taking
                #     it keeps ONE owner for "how many", which is the invariant
                #     this plan exists to establish. Feeding `available_days`
                #     and re-capping here would create a second owner and
                #     rebuild the defect.
                #   * Those times went through `choose_presented_indices`, which
                #     prefers times this caller has not heard (B-116).
                #     `available_days` is the untrimmed bookable set and has
                #     had no such selection, so building from it would re-offer
                #     times the caller was just read.
                #
                # CALLER-AUDIBLE CONSEQUENCE, measured and deliberate. Live
                # multi_day readouts were bimodal: 24 of 52 at 2 days x 1 time
                # (the model obeying presented_days) and 25 at 3 days x 2 times
                # (the model obeying its own prompt instead). Owner decision of
                # 1 Sept 2026: three days at two times, every time. Both
                # numbers live in `_cap_presented_slots` and nowhere else, so
                # changing them moves this path and the model path together.
                #
                # No more-times tail is emitted: `build_slot_offer` makes that
                # claim only where it has a referent -- one day -- which is the
                # B-99 rule. 50 of those 52 readouts carry no tail today, so
                # silence is also what almost all callers already hear.
                elif (
                    session.get("_slot_presentation_mode") == "multi_day"
                    and isinstance(result, dict)
                    and result.get("presented_days")
                ):
                    _det_mode = "multi_day"
                    try:
                        from app.tools.slot_offer import build_slot_offer
                        # No lead_in. "The earliest I have is ..." is a claim
                        # about ONE day; B-125 decided it against the untrimmed
                        # day, and there is no such day here.
                        _offer = build_slot_offer(
                            list(result["presented_days"]),
                            more_times=bool(session.get("_slot_more_times")),
                            other_dates=session.get("_slot_other_dates"),
                        )
                        # THE ANCHOR KEEPS ITS EXISTING MEANING. Section 4 of
                        # _flush_slot_buf writes v3_last_offered_day_iso as
                        # `available_days[0]["date"]` -- the PAYLOAD's first day
                        # -- and turn_handler documents it as exactly that. The
                        # one time a reader treated it as "the day the caller is
                        # being offered", CA6e1024db went four turns with the
                        # staleness gate blind for the whole call, and the fix
                        # was to read `available_days` as the primary signal,
                        # NOT to change what this scalar holds. Four readers sit
                        # on that contract, so step 4 does not touch it.
                        #
                        # `SlotOffer.first_spoken_date` is the better value and
                        # is tested, but adopting it is a separate change to a
                        # session key with its own blast radius, its own tests
                        # and its own real call.
                        _av = (result.get("available_days") or [])
                        if _av and isinstance(_av[0], dict):
                            _det_day_iso = _av[0].get("date") or None
                    except Exception:
                        logger.exception(
                            "[ms_gate5] deterministic multi_day offer failed — "
                            "falling back to the model's presentation"
                        )
                        _offer = None
                if _offer is not None:
                    # A plain dict: the session is serialised to Redis.
                    session["_slot_offer_prebuilt"] = {
                        "chunks": list(_offer.chunks),
                        "slots": [
                            {
                                "start": s.get("start"),
                                "end": s.get("end") or "",
                                "spoken": s.get("spoken"),
                                "date": s.get("date"),
                            }
                            for s in _offer.slots
                        ],
                        "dtmf_map": dict(_offer.dtmf_map),
                        "more_times": bool(_offer.more_times),
                        # Absent on single_day, so section 1b keeps its existing
                        # behaviour there verbatim. Only multi_day sets it.
                        "day_iso": _det_day_iso,
                        # Read by section 1b to decide whether
                        # `last_offered_slots` is one-per-day (multi_day, where
                        # a position means a DAY) or every slot named. Taken
                        # from the offer rather than re-derived, so it cannot
                        # disagree with the sentence that was built.
                        "mode": _offer.mode,
                    }
                    logger.info(
                        "[ms_gate5] deterministic %s offer built: "
                        "%d chunk(s), %d slot(s) recorded — %r",
                        _det_mode, len(_offer.chunks), len(_offer.slots),
                        [s.get("start") for s in _offer.slots],
                    )
                # WHICH day this readout is about. Needed because a clinic on a
                # fixed evening rota has the same spoken labels on every day of
                # the week, so resolving a label against the whole sweep is
                # ambiguous on almost every real readout. first_day names it
                # exactly; the blocked/cached results carry no first_day, but
                # they only fire when an offer is already on the table, and
                # that offer's day IS the day under discussion.
                _presented_day = (_fd or {}).get("date") if isinstance(_fd, dict) else None
                if not _presented_day:
                    _prev = (session.get("last_offered_slots") or [{}])[0]
                    _presented_day = str(_prev.get("start") or "")[:10] or None
                session["_slot_presented_day"] = _presented_day
                # How many options this readout may SPEAK. Normally the owner
                # cap. On a next_unspoken_batch the caller has explicitly asked
                # for the rest of the day, so the batch itself is the ceiling:
                # trimming there would withhold the very times they asked for,
                # which is the defect that batch exists to fix.
                from app.tools.slot_followup import (
                    MAX_SPOKEN_OPTIONS as _MAX_SPOKEN_OPTIONS,
                )
                _n_fd = (
                    len((_fd or {}).get("slot_times") or [])
                    if isinstance(_fd, dict) else 0
                )
                session["_slot_spoken_cap"] = (
                    max(_n_fd, _MAX_SPOKEN_OPTIONS)
                    if (isinstance(result, dict)
                        and result.get("status") == "next_unspoken_batch")
                    else _MAX_SPOKEN_OPTIONS
                )
                # Mark that a check ran this turn so the loop-level C8-5 silence
                # guarantee can choose the no-availability fallback over the
                # generic re-ask when the turn ends with no audible speech.
                session["_check_av_ran_turn"] = True

            # P1 #5 / F-023 / B-36: record a successful write (Layer 1) and
            # attach a do-not-claim-success rule to a blocked/failed one
            # (Layer 2) before the model sees the result. Covers all three write
            # families — booking, reschedule and cancel.
            # B-62: carry the slot this call ASKED for into the funnel. Both the
            # success and the refusal reach _note_write_result as bare results
            # that never say which slot they concern — the executors' payloads
            # differ and neither carries an ISO — so without this the duplicate
            # test can only be family-level, which is the defect.
            if tool_name == "reschedule_appointment" and isinstance(result, dict):
                result.setdefault("attempted_slot_iso", args.get("new_slot_iso"))
            result = _note_write_result(session, tool_name, result)

            logger.info(
                "[ms_llm] tool result: name=%s result=%s",
                tool_name, json.dumps(result, default=str)[:200],
            )
            result_blocks.append({
                "type":        "tool_result",
                "tool_use_id": tu["id"],
                "content":     json.dumps(result, default=str),
            })

        return result_blocks

    async def _exec_escalate(
        self,
        args: Dict[str, Any],
        session: Dict[str, Any],
    ) -> dict:
        question = args.get("question", "")
        logger.info("[ms_llm] escalate_to_claude: question=%r", question)
        try:
            from app.flows.conversation import handle_turn
            reply_text, updated_session = await handle_turn(question, session)
            session.update(updated_session)
            return {"reply": reply_text}
        except Exception as exc:
            logger.error("[ms_llm] escalate error: %r", exc)
            return {"reply": "Bear with me — just a moment and I'll get that sorted."}

    # -----------------------------------------------------------------------
    # GPT-4.1-mini fallback
    # -----------------------------------------------------------------------

    async def _gpt_fallback(
        self,
        system_prompt: str,
        messages: List[dict],
        session: Dict[str, Any],
        tts_text_queue: asyncio.Queue,
    ) -> str:
        """
        Non-streaming GPT-4.1-mini call as fallback when Claude is unavailable.

        Puts the full response as a single chunk onto tts_text_queue.
        Returns the reply text.
        """
        if not OPENAI_API_KEY:
            await tts_text_queue.put(SAFE_FALLBACK_PHRASE)
            return SAFE_FALLBACK_PHRASE

        # ── Change 1: Phone-collection keypad hardcode ────────────────────────
        # When the caller indicates they want to use a different number while
        # the system is in the phone-collection phase (name confirmed, phone not
        # yet stored), skip GPT entirely.  The verbatim keypad prompt contains
        # the word "keypad" which arms v3_phone_dtmf_active in connection.py
        # via the last_bot_prompt detection path — identical to the Claude path.
        _KEYPAD_PROMPT = (
            "Of course — could you type the number on your keypad? "
            "You can press the star key to reset at any time."
        )
        _collected = session.get("collected") or {}
        _last_user_text = ""
        for _m in reversed(messages):
            if not isinstance(_m, dict):
                continue
            if _m.get("role") != "user":
                continue
            _mc = _m.get("content", "")
            if isinstance(_mc, str):
                _last_user_text = _mc.lower()
            elif isinstance(_mc, list):
                for _blk in _mc:
                    if isinstance(_blk, dict) and _blk.get("type") == "text":
                        _last_user_text = _blk.get("text", "").lower()
                        break
            break
        _wants_different_number = any(
            phrase in _last_user_text
            for phrase in (
                "different number", "another number", "wrong number",
                "use a different", "different one", "not that number",
                "change the number", "update the number", "type it",
                "use my keypad", "use the keypad",
            )
        )
        _in_phone_phase = (
            bool(_collected.get("full_name") or _collected.get("name"))
            and not _collected.get("phone")
        )
        if _wants_different_number and _in_phone_phase:
            logger.info(
                "[ms_llm] GPT fallback: phone-collection keypad hardcode "
                "(name=%r, last_user=%r)",
                _collected.get("full_name") or _collected.get("name"),
                _last_user_text[:60],
            )
            await tts_text_queue.put(_KEYPAD_PROMPT)
            session["last_bot_prompt"] = _KEYPAD_PROMPT
            return _KEYPAD_PROMPT

        # ── Change 2: GPT constraint prefix ──────────────────────────────────
        # Prepend a brief voice-receptionist discipline block so that GPT
        # output at minimum respects the banned phrase rules even when Claude
        # is unavailable.  The prefix is invisible to the conversation history
        # — it only modifies the system message sent to OpenAI.

        try:
            from openai import AsyncOpenAI
            gpt_client   = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=15.0)
            # B-45: the degraded path is not allowed to mutate the calendar.
            tools        = _build_openai_tools(session, allow_writes=False)
            oai_messages = [
                {"role": "system", "content": _GPT_CONSTRAINT_PREFIX + system_prompt},
            ] + list(messages)
            reply_text   = SAFE_FALLBACK_PHRASE

            from app.tools.receptionist_tools import TOOL_EXECUTORS

            for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
                logger.info("[ms_llm] GPT fallback iter=%d", iteration)
                response = await gpt_client.chat.completions.create(
                    model=GPT_MODEL,
                    messages=oai_messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=CLAUDE_MAX_TOKENS,
                    temperature=CLAUDE_TEMPERATURE,
                )
                choice = response.choices[0]
                msg    = choice.message

                if not msg.tool_calls:
                    reply_text = (msg.content or "").strip() or SAFE_FALLBACK_PHRASE
                    break

                # Append assistant message with tool calls
                oai_messages.append({
                    "role":       "assistant",
                    "content":    msg.content or "",
                    "tool_calls": [
                        {
                            "id":   tc.id,
                            "type": "function",
                            "function": {
                                "name":      tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = {}
                    try:
                        if tool_name in _WRITE_TOOL_FAMILIES:
                            # ── B-45: no calendar writes on the degraded path ──
                            # This loop calls TOOL_EXECUTORS directly. It never
                            # reaches _execute_tools, so NONE of the write gates
                            # apply here: not FM-01's booking confirmation, not
                            # the surname or phone backstops, not FM-23's move
                            # and cancel consent checks, and not B-42's identity
                            # check. A cancellation on this path could not tell
                            # whose appointment it was destroying.
                            #
                            # The right degraded behaviour is not "write without
                            # gates" — it is the one CLAUDE.md §6 bar 3 already
                            # specifies: when the LLM is down, produce a
                            # controlled outcome (take a message, promise a
                            # callback, transfer), never a hallucinated
                            # confirmation. A missed booking is recoverable by a
                            # callback; a wrong cancellation is not.
                            #
                            # Belt and braces: these tools are also withheld from
                            # the schema (`allow_writes=False`), so a well-behaved
                            # model never asks. This branch is what holds if the
                            # schema is ever widened again, and it is the reason
                            # the guarantee does not rest on a tool list.
                            #
                            # Routed through _note_write_result on purpose: that
                            # arms Gate 5f for this family and attaches the
                            # do-not-claim rule, and this path DOES sanitise its
                            # reply through Gate 5 — so the model cannot narrate
                            # a booking it was just refused.
                            logger.error(
                                "[ms_llm] %s REFUSED on the GPT fallback path — "
                                "no write gates exist here (B-45) call_sid=%s",
                                tool_name, session.get("call_sid"),
                            )
                            result = _note_write_result(session, tool_name, {
                                "status": "unavailable_degraded_mode",
                                "message": (
                                    "The booking system cannot be changed on "
                                    "this call. Do NOT say anything has been "
                                    "booked, moved or cancelled. Tell the caller "
                                    "you are having trouble reaching the diary, "
                                    "take their name and number, and promise the "
                                    "clinic will call them straight back to "
                                    "confirm — or offer to put them through to "
                                    "someone using transfer_to_human."
                                ),
                            })
                        elif tool_name == "escalate_to_claude":
                            result = await self._exec_escalate(args, session)
                        else:
                            executor = TOOL_EXECUTORS.get(tool_name)
                            result = await executor(args, session) if executor else {"error": f"Unknown tool: {tool_name}"}
                    except Exception as exc:
                        result = {"error": str(exc)}

                    oai_messages.append({
                        "role":         "tool",
                        "tool_call_id": tc.id,
                        "content":      json.dumps(result, default=str),
                    })

                if session.get("request_transfer"):
                    session["request_transfer"] = False
                    return ""

        except Exception as exc:
            logger.error("[ms_llm] GPT fallback error: %r", exc)
            reply_text = SAFE_FALLBACK_PHRASE

        # ── Gate 5 (A1 / phantom-booking bypass, 2026-07-29) ─────────────────
        # The streaming path sanitises every chunk before TTS (see GATE 5 at
        # _streaming_tool_loop). This path did not — so whenever Claude was
        # overloaded and we fell back to GPT, the caller received raw model
        # output with no filtering whatsoever. That included Gate 5f, the guard
        # that stops a phantom "all booked" reaching a caller when no booking
        # exists. The bypass activates under load, which is exactly when a busy
        # clinic can least afford it.
        #
        # _GPT_CONSTRAINT_PREFIX above is not a substitute: it is an instruction
        # the model is free to ignore, and Gate 5 exists precisely because it
        # sometimes does.
        #
        # TRADE-OFF, deliberate: this sanitises the whole reply in one call,
        # whereas the streaming path sanitises per chunk. Gate 5a drops the
        # ENTIRE text it is given, so a reply that carries a reasoning opener
        # anywhere is dropped whole here where streaming would have lost only
        # one chunk. That is accepted — the caller then hears SAFE_FALLBACK_PHRASE
        # and can retry, which is strictly better than an unfiltered phantom
        # confirmation. Never emit nothing: silence is the documented worse
        # outcome (cf. the deferred _gate5_fallback on the streaming path).
        _spoken = sanitise_response(reply_text, session) or SAFE_FALLBACK_PHRASE
        if _spoken != reply_text:
            logger.info(
                "[ms_gate5] GPT fallback reply sanitised before TTS (%d -> %d chars)",
                len(reply_text), len(_spoken),
            )
        # Return the SPOKEN text, not the raw: the caller sees this go into
        # conversation history and the obs transcript, and a record that differs
        # from what was said is how A1 stayed invisible for three weeks.
        await tts_text_queue.put(_spoken)
        return _spoken


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _advance_fp_state(session: Dict[str, Any], turn_type: Any) -> None:
    """No-op: free-form loop no longer uses the state machine."""
    pass


# B-76 — the per-chunk backing store for `_spoken_this_turn`.
#
# A list, not a string, so a chunk the TTS loop later DISCARDS can be removed
# exactly. Substring surgery on the joined form would be ambiguous whenever a
# turn repeats a phrase, and this key exists precisely to correct a record that
# turned out to be wrong.
#
# TURN-scoped: cleared alongside `_spoken_this_turn` at the top of every turn.
SPOKEN_CHUNKS_KEY = "_spoken_chunks"


def _record_spoken(session: Dict[str, Any], chunk: str) -> None:
    """Accumulate one post-Gate-5 chunk — the text the caller actually hears.

    Recorded HERE, synchronously inside the turn, and deliberately not in
    connection.py's TTS loop even though that loop is strictly closer to the
    audio. The loop runs asynchronously: on CA7e389a47 the turn-end derivation
    ran at 18:34:24.023 and the ElevenLabs request for the same text went out at
    .133, so anything read from the loop at turn end is empty or partial.

    The known cost of the earlier seam: `_pre_slot_cancelled`
    (connection.py ~11478) can still drop a chunk recorded here, and phonetic
    substitution happens later. Neither makes this worse than what it replaces —
    the previous source was the RAW generated reply, which contains the dropped
    text too and has had no gate applied at all. This is strictly closer to what
    was spoken, not perfectly equal to it.

    Chunks arrive already stripped (ResponseChunker._emit), so they are joined
    with an explicit space. Joining without one produces "available.Friday 7th
    August" — the C5 artifact recorded in docs/plan/AUDIT_2026-07-29.md.
    """
    if not chunk or not chunk.strip():
        return
    _chunks = session.get(SPOKEN_CHUNKS_KEY)
    if not isinstance(_chunks, list):
        _chunks = []
    _chunks.append(chunk.strip())
    session[SPOKEN_CHUNKS_KEY] = _chunks
    session["_spoken_this_turn"] = " ".join(_chunks)


def _unrecord_spoken(session: Dict[str, Any], chunk: str) -> None:
    """Undo one `_record_spoken` — the chunk was recorded but never spoken.

    B-76. `_record_spoken` runs synchronously inside the turn, immediately
    before the chunk is put on `tts_text_queue`. connection.py's TTS loop then
    dequeues it and can still DROP it — for a confirmed barge-in
    (`tts_inhibit`), a cancelled ack filler, or a cancelled pre-slot chunk. The
    record was optimistic and nothing ever corrected it, so
    `session["last_bot_prompt"]` could name a sentence the caller never heard.

    That is not cosmetic. Every write gate in this module reads
    `last_bot_prompt` to decide whether its confirmation question was asked. On
    JV CAe84b871b (21 Aug) the duration question was discarded at 21:23:58.600
    and at 21:24:07.230 `reschedule_appointment` was BLOCKED because
    `last_bot_prompt` was that unheard question — the caller had already
    approved the move, twice.

    Recording early is deliberate and must stay (see `_record_spoken`: the TTS
    loop is async, and anything read from it at turn end is empty or partial).
    So the record is optimistic by design and this is its correction.

    Removes the LAST matching entry: chunks are appended in order and dropped in
    order, so on the rare turn that repeats a phrase the most recent one is the
    one that did not make it. Silent no-op when the chunk is not found — the
    turn may already have ended and popped the buffer, and a drop arriving after
    that must never resurrect it.
    """
    if not chunk or not chunk.strip():
        return
    _chunks = session.get(SPOKEN_CHUNKS_KEY)
    if not isinstance(_chunks, list) or not _chunks:
        return
    _target = chunk.strip()
    for _idx in range(len(_chunks) - 1, -1, -1):
        if _chunks[_idx] == _target:
            del _chunks[_idx]
            break
    else:
        return
    session[SPOKEN_CHUNKS_KEY] = _chunks
    session["_spoken_this_turn"] = " ".join(_chunks)


def _append_history(
    session: Dict[str, Any],
    user_text: str,
    assistant_text: str,
    spoken_text: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> None:
    """Append a user/assistant exchange to conversation_history, trim to MAX_HISTORY_TURNS.

    `assistant_text` is what the caller HEARD — the post-Gate-5 text. As of
    2026-08-02 conversation_history stores this rather than the raw generation.

    `raw_text` is what the model produced. It is kept in session["turns"] under
    a separate `raw` key, present only when it differs from what was spoken.
    Defaults to assistant_text on the deterministic paths, where the text we
    queue IS the text we speak.

    session["turns"]["text"] WAS the raw generation until 2026-08-30. It fed the
    owner-facing actionable summary through `_format_turns`, so the owner's
    record of a call was built from what the model GENERATED rather than what
    the caller HEARD. On CA8e688605 (30 Aug, demo line) two guards corrected one
    sentence on the way out -- a wrong time and a false completeness claim --
    and the summary was still handed the uncorrected version, naming a time the
    caller was never offered. The Gate 5f case is worse than the time: a
    "you're booked in for Thursday" that Gate 5f stopped the caller ever hearing
    was still reported to the owner as though it had been said.

    The note below scoped session["turns"] out on 2026-08-02 as out of scope for
    the history fix. This is that revisit; the SMS path genuinely is unaffected,
    because the confirmation SMS reads `selected_slot` / `selected_slot_speech`
    off the booking record and never the transcript.

    ONE CONSUMER STILL WANTS THE RAW, and it is a P1 if it loses it:
    connection.py's Gate 5g name recovery. Gate 5g deletes a booking CTA when
    the name is missing, and the sentence it deletes is usually the model's
    acknowledgement of the name the caller just gave -- the only place a first
    name is ever read from. The recovery re-runs the name parser against the raw
    reply. On CA041352eb the caller was asked their name four times and hung up.
    It reads `raw` first and falls back to `text`; the fallback is a no-op on the
    deterministic paths, where the two are equal and the recovery's own
    `!= _last_bot` guard declines.

    `spoken_text` overrides what the obs record stores; it now normally equals
    assistant_text and is kept for callers that pass it explicitly.

    WHY history changed (CA7d46c2bc / CA7e389a47, 1 Aug 2026). Gate 5f rewrites
    the SPOKEN text when the model claims a booking that never happened, but
    history used to record the claim. So the model read back its own "All
    booked — you're in for Thursday", believed it had already confirmed, and
    said it again; Gate 5f rewrote it again. Three affirmatives from the caller,
    no booking, and nothing in the model's context ever revealed that the
    sentence it thought it had spoken was never said out loud.

    The note below scoped this out on 2026-07-29 as "worth revisiting,
    separately, with their own tests". This is that revisit. session["turns"]
    and the SMS path remain out of scope, as it said.
    """
    history: List[dict] = session.setdefault("conversation_history", [])
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    if len(history) > MAX_HISTORY_TURNS:
        session["conversation_history"] = history[-MAX_HISTORY_TURNS:]
    # `text` is what the caller HEARD; `raw` is what the model produced, and is
    # present only when the two differ. See the docstring — this was the raw
    # generation until 2026-08-30, which meant the owner's record of a call
    # could contradict the call.
    _raw = raw_text if raw_text is not None else assistant_text
    _entry = {"role": "assistant", "text": assistant_text}
    if _raw != assistant_text:
        _entry["raw"] = _raw
    session.setdefault("turns", []).append(_entry)

    # The CALLER side of the observability transcript (app/obs/**). The assistant
    # side is no longer written here: it is recorded in connection.py's TTS loop,
    # at the one seam every utterance passes through. Writing it here recorded the
    # LLM turns and nothing else — no greeting, no watchdog re-ask, no dead-air
    # sign-off — so the stored call ended wherever the last LLM turn did, and the
    # judge filled in the ending itself. See app/obs/turns.py.
    #
    # obs_turns stays a SEPARATE key from session["turns"]: that list feeds the
    # owner-facing actionable summary (_format_turns, max_turns=10) and the SMS
    # router (last-8 window), both tuned against an assistant-only shape. Adding
    # caller turns there would halve those windows' real coverage and change a
    # live clinic's summaries as a side effect of an observability fix.
    #
    # record_user inserts at the mark run_turn set on entry, not at the end —
    # this runs at TURN END, by which point this turn's replies are already in
    # the list, and a plain append would print every exchange backwards.
    _obs_turns.record_user(session, user_text)
    _spoken = assistant_text if spoken_text is None else spoken_text

    # C1 write-guard input: remember the date the caller was actually TOLD, taken
    # from the spoken form for the same reason obs uses it — the raw reply is not
    # what reached their ear.
    _note_spoken_slot_date(session, _spoken)


# ---------------------------------------------------------------------------
# Interim-phrase duplicate suppression (BUG 2)
# ---------------------------------------------------------------------------

# Matches phrases that fast-path plays as interim ("Let me check…") so they
# can be stripped from the start of the subsequent LLM response if both would
# otherwise be spoken back-to-back.
_INTERIM_DUPE_RE = re.compile(
    r"^(?:"
    r"Let me check(?:\s+that)?(?:\s+for\s+you)?[\.,]?\s*"
    r"|One\s+moment(?:\.{1,3})?\s*"
    r"|Just\s+a\s+moment(?:\.{1,3})?\s*"
    r"|Just\s+bear\s+with\s+me(?:\.{1,3})?\s*"
    r"|Bear\s+with\s+me(?:\.{1,3})?\s*"
    # Openers the stored calls actually produced. Every one of these was spoken
    # on top of a hold phrase the system had already played, ~1-2s earlier.
    r"|(?:Right,?\s+)?Let(?:'|’)?s\s+see(?:\.{1,3})?[,—-]?\s*"
    r"|Let\s+me\s+see(?:\.{1,3})?[,—-]?\s*"
    r"|Let\s+me\s+(?:just\s+)?(?:have\s+a\s+)?look(?:\s+that\s+up)?"
    r"(?:\s+for\s+you)?[\.,]?\s*"
    r"|Let\s+me\s+pull\s+that\s+up(?:\s+for\s+you)?[\.,]?\s*"
    r"|Let\s+me\s+find\s+(?:that|you)(?:\s+for\s+you)?[\.,]?\s*"
    r"|Right\s+with\s+you(?:\.{1,3})?\s*"
    r"|Just\s+getting\s+that(?:\s+for\s+you)?(?:\.{1,3})?\s*"
    r"|Give\s+me\s+(?:a|one)\s+(?:moment|second)(?:\.{1,3})?\s*"
    r"|Let\s+me\s+get\s+that\s+(?:booked\s+in|sorted|moved|changed|cancelled)"
    r"(?:\s+for\s+you)?(?:\s+now)?[\.,]?\s*"
    r")",
    re.IGNORECASE,
)


# Subordinating conjunctions. A phrase stripped from in front of one of these
# leaves a clause that cannot stand as a sentence.
# The complement words too, not just the adverbial ones. "Let me check
# what's available for Saturday" loses its verb when the opener goes,
# leaving "What's available for Saturday." -- which reads as a statement,
# is a fragment, and was spoken. The same defect as the "While I look that
# up." wreckage these were added for; the list simply stopped one word short.
_ORPHAN_LEAD = re.compile(
    r"^(?:while|whilst|as\s+I|so\s+I|until|what|whether|which|how|when|where|if)\b",
    re.I,
)


def _strip_interim_opener(text: str) -> str:
    """
    Remove a known interim phrase from the start of an LLM first chunk to
    prevent it being spoken twice (once from fast-path, once from the LLM).

    Also removes the first sentence if it contains "check" within the first
    15 words (catches paraphrases like "Let me just check what we have…").
    """
    stripped = _INTERIM_DUPE_RE.sub("", text).lstrip()
    if stripped != text and _ORPHAN_LEAD.match(stripped):
        # The opener was the HEAD of a subordinate clause, not a sentence of its
        # own: "Bear with me while I look that up." Removing the phrase leaves
        # "While I look that up." — a dangling clause Susie then says out loud.
        # Six of those reached callers on 21-22 Aug. Gate 5b deletes the whole
        # sentence for exactly this reason; this ran first and left the wreckage.
        _end = re.search(r"[.!?]\s*", stripped)
        stripped = stripped[_end.end():].lstrip() if _end else ""
    if stripped != text:
        # Capitalise after stripping if needed
        if stripped:
            stripped = stripped[0].upper() + stripped[1:]
        return stripped

    # Fallback: strip the first sentence if it contains "check" in its first 15
    # words (catches paraphrases). The boundary is not only "." — the model
    # frequently ends the clause with a dash ("Let me just check what we have for
    # you - which clinic...?"), and splitting on "." alone let those through.
    m = re.search(r"[\.!?]|\s—\s", text)
    if m and m.start() > 0:
        first_sentence = text[: m.end()]
        words = first_sentence.split()[:15]
        if any("check" in w.lower() for w in words):
            remainder = text[m.end():].lstrip()
            if remainder:
                return remainder[0].upper() + remainder[1:]

    return text


# Words that keep their capital even mid-sentence, so a joined payload does not
# become "let me see - friday the fourteenth". Proper nouns the payload can open
# with; anything else is ordinary sentence-initial capitalisation and is lowered.
_KEEPS_CAPITAL = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "i", "i'm", "i've", "i'll", "i'd",
}


#: "Claims a lookup or a write." Shared with the head pools so one definition
#: decides both what a head may promise and whether the model's own pre-tool
#: sentence already made that promise.
from app.hold_speech import _NAMES_THE_WORK  # noqa: E402


def _may_suppress_pure_dupe(
    session: dict, head: str, latched_on_ungated_text: bool
) -> bool:
    """May an all-opener chunk be dropped rather than spoken again? PURE.

    Two conditions, and BOTH are about not suppressing a sentence on the
    strength of speech that does not exist.

    1. ``head`` must be real. The call sites pass ``_head or "…"`` into
       ``join_after_head`` so the seam logic always has something to work with,
       but a placeholder is not evidence that a hold phrase was spoken -- it is
       evidence that we do not know what the caller heard. Suppressing against
       it would delete a sentence to make room for nothing.

    2. The latch must not have been set by THIS turn's own model text. The flush
       path runs after the ``content_block_start`` for the tool call, so by then
       the preserved pre-tool line may itself have latched ``_hold_head_spoken``
       -- and joining that sentence against a latch it set moments earlier
       suppresses it against ITSELF. That is not hypothetical: it is what
       `test_a_hold_phrase_that_survives_gate_5_still_latches` caught when this
       suppression was first written unconditionally, and it revoked the latch
       and re-opened B-121 in the model-first direction while closing it in the
       head-first one.

    ``interim_played`` is deliberately NOT enough on its own here. The fast-path
    interim does not record its wording in ``_hold_head_text``, so it fails
    condition 1 and keeps the original fail-safe -- the conservative direction.
    """
    if not (head or "").strip():
        return False
    if latched_on_ungated_text:
        return False
    return bool(session.get("_hold_head_spoken"))


def join_after_head(
    chunk: str, head: str, *, suppress_pure_duplicate: bool = False
) -> str:
    """Make ``chunk`` read as the continuation of the hold phrase ``head``.

    Pure. The perceptual half of the hold-speech work: a head that ends in a
    comma or a dash is an unfinished clause, and the reply is its completion —
    "Let me see - Friday the fourteenth at ten's free" rather than "Right with
    you..." <pause> "Friday 14th August at ten in the morning is available."

    Three things happen, in order:

    1. A duplicate opener is stripped. The head already said it.
    2. The payload is decapitalised, but ONLY when the head left the sentence
       open (``,`` or a dash). A head ending in ``.`` or the ellipsis is a closed
       sentence and the payload must keep its capital. Proper nouns are exempt —
       day and month names are how these replies usually start.
    3. Exactly one space at the seam. Concatenating without a separator is what
       produced "...what's available.The available slots for Tuesday" in 106
       stored fragments; doing the join in one place makes that unrepresentable.

    ``head`` is what the caller HEARD, not what is about to be synthesised, so an
    empty head means no hold phrase played and the chunk is returned untouched.

    ``suppress_pure_duplicate`` decides what happens when the chunk turns out to
    be NOTHING BUT the opener — see the branch below. It is opt-in because only a
    caller that can guarantee the turn still produces audio can afford it, and
    the default keeps the original fail-safe for everyone else.
    """
    if not chunk:
        return chunk
    if not head:
        return chunk

    body = _strip_interim_opener(chunk).lstrip()
    if not body:
        # The chunk was NOTHING BUT the opener — the head already said this.
        #
        # The original fail-safe returned the chunk unchanged, priced as "saying
        # the phrase twice is a much smaller fault than saying nothing". That
        # trade is real but it is not the trade on offer at every call site, and
        # taking it unconditionally is B-121 in the direction nobody checked:
        # the owner heard it on the demo line on 2026-08-30,
        # CAd1bc6681b69e48fc8527449d65a03a23 —
        #
        #   10:26:01.403  head:  'Let me see what Tuesday looks like —'
        #   10:26:03.084  model: "Let me check what's available on Tuesday for
        #                         you."   <- 1.68s later, said again
        #
        # `_strip_interim_opener` had already reduced that sentence to "". The
        # machinery saw the duplicate correctly; this branch put it back.
        #
        # Note the premise the fail-safe rests on is FALSE here. This function is
        # only ever called when `interim_played or _hold_head_spoken` — that is,
        # when the caller has already heard a hold phrase — so suppressing this
        # chunk cannot leave the turn with "no audio at all". The head IS the
        # audio. The only genuinely bad case left is the model promising a lookup
        # and then making no tool call, and `_one_streaming_call` already rescues
        # that at end of stream ("no TTS emitted this turn"), with a real
        # sentence rather than a repetition of the phrase just spoken.
        #
        # Still opt-in rather than the new default: the rescue is what makes it
        # safe, and only a caller sitting above the rescue can promise it.
        if suppress_pure_duplicate:
            return ""
        return chunk

    # One space at a sentence boundary. The model welds its opener to the payload
    # ("...what's available.The available slots for Tuesday"): 106 stored
    # fragments, and ElevenLabs reads the run-on without a breath. Repaired here
    # so the seam is guaranteed in one place rather than trusted to the model.
    # NOTE the backreferences. This passed a bare " " until 2026-08-29, which
    # deleted the punctuation AND the capital after it: "available.The available
    # slots" became "available he available slots". The repair was meant to
    # INSERT the missing space, not to eat the seam it found.
    body = re.sub(r"([\.!?])([A-Z])", r"\1 \2", body)

    if head.rstrip()[-1:] in (",", "—", "-"):
        first = body.split(" ", 1)[0].strip(".,!?").lower()
        if first not in _KEEPS_CAPITAL:
            body = body[0].lower() + body[1:]

    return body


def _last_assistant_text(session: dict) -> str:
    """The most recent thing Susie said, or "".

    A module-level helper rather than an inline loop inside ``_delayed_filler``
    on purpose: test_b19_filler_rearm pins that function to be loop-free, so a
    reader (and the guard) can see at a glance that the re-arm is a single
    second phrase and not a cadence. Keeping an unrelated ``for`` out of that
    body keeps the guard meaningful.
    """
    for _m in reversed(session.get("conversation_history") or []):
        if _m.get("role") == "assistant":
            return _m.get("content") or ""
    return ""


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LLM_OPENER_PREFIXES = (
    "Absolutely, ",
    "Certainly, ",
    "Of course, ",
    "Sure, ",
    "Great, ",
    "Sorry, ",
)


def _question_from_response(text: str) -> str:
    """
    Extract the last question sentence from an LLM response for F_LAST_QUESTION.

    Returns the last sentence ending with '?', with any banned opener affirmation
    stripped from the start.  Returns '' if the response contains no question
    (so the re-ask watchdog is not incorrectly armed on statement-only responses).

    Mirrors the logic in connection._extract_question — kept as a local copy
    to avoid a circular import between llm_stream.py and connection.py.
    """
    if not text or "?" not in text:
        return ""

    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    question  = ""
    for sentence in reversed(sentences):
        s = sentence.strip()
        if s.endswith("?"):
            question = s
            break

    if not question:
        return ""

    for prefix in _LLM_OPENER_PREFIXES:
        if question.lower().startswith(prefix.lower()):
            question = question[len(prefix):].lstrip()
            if question:
                question = question[0].upper() + question[1:]
            break

    return question.strip()
