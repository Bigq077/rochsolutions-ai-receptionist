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
    MAX_TOOL_ITERATIONS,
    MAX_HISTORY_TURNS,
    LLM_FIRST_CHUNK_TIMEOUT_MS,
    LLM_FILLER_COOLDOWN_SEC,
    FILLER_PHRASES,
    FILLER_PHRASE,
    ACK_FILLER_MARKER,
    SAFE_FALLBACK_PHRASE,
    F_LAST_BOT_PROMPT,
    F_LAST_QUESTION,
    F_COLLECTED,
    WS_A_FAST_FIRST_CHUNK,
    WS_A_MIN_WORDS_FIRST,
)
from .chunker import ResponseChunker
from .fast_path import try_fast_path
from .session import save_session
from .tts_stream import _apply_tts_substitutions_elevenlabs as _apply_tts_subs
from .turn_handler import sanitise_response

logger = logging.getLogger(__name__)

# Sentinel prefix for pre-tool text chunks.  All text chunks in a streaming
# call are prefixed with this marker so that if check_availability is detected
# mid-stream (via content_block_start), the tts_loop can drop them before they
# reach ElevenLabs.  Uses the same marker+flag pattern as ACK_FILLER_MARKER.
PRE_SLOT_MARKER = "\x01PRE_SLOT\x01"

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


def _caller_requests_different_day(messages) -> bool:
    """True if the caller's latest utterance names a DIFFERENT calendar day.

    CAc6b971ad (30 Jul 2026): the caller asked for Wednesday seven times after
    giving name and phone. Each time the post-collect guard blocked
    check_availability and instructed the model to repeat the Tuesday
    confirmation verbatim, so she twice said "let me check Wednesday" and then
    re-read Tuesday back. He hung up without booking.
    """
    txt = _last_user_text(messages).lower()
    if not txt:
        return False
    if any(p in txt for p in _DIFFERENT_DAY_PHRASES):
        return True
    return bool(set(re.findall(r"[a-z']+", txt)) & _DIFFERENT_DAY_WORDS)


def _caller_requests_new_day_or_time(messages) -> bool:
    """A different day OR a different time of day — i.e. the caller is still
    choosing when to come in, so the post-collect guard must stand down."""
    if _caller_requests_different_day(messages):
        return True
    txt = _last_user_text(messages).lower()
    if not txt:
        return False
    return bool(set(re.findall(r"[a-z']+", txt)) & _NEW_TIME_OF_DAY_WORDS)


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


def _note_book_write_result(session: dict, tool_name: str, result):
    """P1 #5 / F-023 — Layers 1 & 2 of the false-confirmation guard.

    Layer 1: when book_appointment actually SUCCEEDS, record it deterministically
    on the session. This is the "a real booking exists" signal the output guard
    (turn_handler Gate 5f) checks — it did not exist before, so success language
    could not previously be gated on a real result.

    Layer 2: when book_appointment is blocked or fails, attach an explicit rule
    to the tool_result the model reads, forbidding a success claim. Steering only
    — it fires on the already-failed path, so it can never suppress a real
    booking.

    Reschedule is intentionally out of scope: its confirmation is a different
    phrase family ("moved"), and Gate 5f targets booking phantoms.
    """
    if tool_name != "book_appointment" or not isinstance(result, dict):
        return result
    if result.get("success") is True:
        session["booking_write_confirmed"] = True
        return result
    result = dict(result)
    result.setdefault(
        "caller_message_rule",
        "The booking was NOT made. Do not tell the caller they are booked, "
        "confirmed, or all set. Ask the outstanding question, and only state a "
        "booking once book_appointment returns success.",
    )
    return result


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


def _cancel_reply_consents(messages) -> bool:
    """FM-23: cancel_appointment is DESTRUCTIVE — it may fire only on an EXPLICIT
    cancel instruction, in the template cancel-retention context. The confirm is
    the retention question ("...reschedule this appointment, or cancel it
    altogether?"); the caller consents by SAYING "cancel", so this can NOT reuse
    _book_reply_is_affirmative ("cancel" is a NO pattern). Bias hard toward NOT
    cancelling: a bare "yes"/"ok"/"go ahead" is ambiguous against the OR-question
    and must not cancel; a reschedule word, "keep/leave it", "don't cancel", or a
    bare "no" all block. Only an explicit "cancel" token allows.
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
    return "cancel" in text


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
    "on your keypad",
    "type the number",
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


def _build_openai_tools(session: Dict[str, Any] = None) -> list:
    """Return tool definitions in OpenAI function-calling format, per clinic."""
    from app.tools.receptionist_tools import build_tool_schemas
    cid = (session or {}).get("clinic_id")
    tools = []
    for tool in build_tool_schemas(cid):
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
    today      = date.today()
    weekday    = today.strftime("%A")
    date_str   = today.strftime("%d %B %Y")
    # Find this coming Sunday
    days_to_sun = (6 - today.weekday()) % 7   # weekday(): Mon=0 Sun=6
    this_sunday = today + timedelta(days=days_to_sun if days_to_sun > 0 else 7)
    next_monday = this_sunday + timedelta(days=1)
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
            _display_reply = sanitise_response(full_reply, session)
            _append_history(
                session, user_text, full_reply, spoken_text=_display_reply
            )
            # SPEC 4: store the phonetic (TTS-substituted) form so that
            # last_bot_prompt reflects what was actually spoken — used by the
            # silence watchdog re-ask and logging.
            session[F_LAST_BOT_PROMPT] = _apply_tts_subs(
                _display_reply
            )[:200]
            # Store only the question portion in F_LAST_QUESTION.
            # F_LAST_BOT_PROMPT keeps the full response for fast-path trigger
            # matching; F_LAST_QUESTION is narrowed to the actual question
            # sentence so the re-ask watchdog only replays real questions.
            session[F_LAST_QUESTION] = _question_from_response(_display_reply)

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

        # ── 4. Slot map extraction (Bug 7 fix) ───────────────────────────────
        # Runs on the complete assembled response so every option's date string
        # is present and untruncated (last_bot_prompt is capped at 200 chars).
        _SLOT_ANCHOR_FULL_RE = _re.compile(
            r"Number\s+([1-9])\b|(?<!\d)([1-9])\s*[—–\-]\s*",
            _re.IGNORECASE,
        )
        _full_anchors = [
            (m.start(), m.end(), m.group(1) or m.group(2))
            for m in _SLOT_ANCHOR_FULL_RE.finditer(_joined)
        ]
        _slot_map_count = 0
        if len(_full_anchors) >= 2:
            _slot_map: dict = {}
            for _i, (_fa_start, _fa_end, _fa_digit) in enumerate(_full_anchors):
                _next = (
                    _full_anchors[_i + 1][0]
                    if _i + 1 < len(_full_anchors)
                    else len(_joined)
                )
                _lbl = _joined[_fa_end:_next].lstrip(", ")
                _lbl = _re.split(r"[—–\.]", _lbl)[0].strip().rstrip(".,;- ")
                if _lbl:
                    _slot_map[_fa_digit] = _lbl
            if len(_slot_map) >= 2:
                session["v3_dtmf_slot_map"] = _slot_map
                session["v3_awaiting_slot_selection"] = True
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
        _flow_suppressed: bool = False

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            logger.info("[ms_llm] iteration=%d model=%s", iteration, model)

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
    ) -> tuple:
        """
        Open one Claude streaming session, feed tokens through the chunker,
        and put text chunks onto tts_text_queue.

        Returns (full_text, tool_uses, transfer_initiated).
        tool_uses is non-empty if stop_reason == "tool_use".
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

        # Reset ack-filler state for this turn.  _ack_filler_active is set True
        # by _delayed_filler() below when FILLER_PHRASE is queued; with_filler()
        # reads it and sets _ack_filler_cancelled True when a tool-call filler
        # supersedes it.  _tts_loop uses _ack_filler_cancelled to silently drop
        # the marked ack-filler chunk before it reaches ElevenLabs.
        session["_ack_filler_active"]    = False
        session["_ack_filler_cancelled"] = False
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
        if not filler_sent and (time.monotonic() - self._last_filler_at) >= LLM_FILLER_COOLDOWN_SEC:
            async def _delayed_filler() -> None:
                await asyncio.sleep(timeout_sec)
                if not got_first_chunk:
                    # Prefix with ACK_FILLER_MARKER so _tts_loop can identify
                    # this chunk and suppress it if a tool-call filler fires
                    # in the same turn and sets _ack_filler_cancelled.
                    # On the turn right after a booking/reschedule "yes", use a
                    # write-acknowledging filler ("Just locking that in now…")
                    # instead of the generic "Give me a moment…", which confuses
                    # a caller who just confirmed and can re-open the readback.
                    from app.filler_phrases import confirm_write_filler
                    # FM-25: only speak a write-ack ("Just locking that in now…")
                    # when the caller actually confirmed — a "no"/ambiguous reply
                    # must fall back to a neutral filler, never a booking claim.
                    _ack_filler_text = (
                        confirm_write_filler(session, _book_reply_is_affirmative(messages))
                        or random.choice(FILLER_PHRASES)
                    )
                    logger.info(
                        "[ms_llm] filler phrase triggered (background task): %r",
                        _ack_filler_text[:40],
                    )
                    await tts_text_queue.put(ACK_FILLER_MARKER + _ack_filler_text)
                    session["_ack_filler_active"] = True
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

        async with client.messages.stream(
            model=model,
            system=_system_blocks,
            messages=messages,
            tools=tools,
            max_tokens=CLAUDE_MAX_TOKENS,
            temperature=CLAUDE_TEMPERATURE,
        ) as stream:

            async for event in stream:
                # ── Tool-use block opening ────────────────────────────────
                # Detect check_availability as early as possible (before the
                # tool result arrives) so the tts_loop can drop pre-tool
                # PRE_SLOT_MARKER chunks that haven't been consumed yet.
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
                                    if interim_played:
                                        chunk = _strip_interim_opener(chunk)
                                        if chunk:
                                            logger.debug(
                                                "[ms_llm] interim stripped; first chunk: %r",
                                                chunk[:60],
                                            )
                                # GATE 5: sanitise before TTS
                                chunk = sanitise_response(chunk, session)
                                if chunk:
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
                if not _first_tts_emitted and interim_played:
                    # Entire response was a single short flush — strip interim opener
                    final_chunk = _strip_interim_opener(final_chunk)
                    _first_tts_emitted = True
                # GATE 5: sanitise flush chunk before TTS
                final_chunk = sanitise_response(final_chunk, session)
                if final_chunk:
                    # t2 fallback — whole reply arrived as a single flush chunk
                    # (first-write-wins, so a no-op if t2 already stamped above).
                    if self._timing is not None:
                        self._timing.stamp("t2")
                    await tts_text_queue.put(PRE_SLOT_MARKER + final_chunk)
                    _any_tts_emitted = True

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
        )

        # Tools that get filler phrases → list to draw from
        _FILLER_TOOLS = {
            "check_availability": THINKING_FILLERS_PRIMARY,
            "book_appointment":   BOOKING_WRITE_FILLERS,
            # lookup_patient uses generic "finding that for you" fillers — it
            # runs both when finding an appointment AND on the cancel/reschedule
            # confirmation wait, where "checking the diary" wording is wrong (P17).
            "lookup_patient":     LOOKUP_FILLERS,
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
                _col = session.get("collected") or {}
                if (
                    tool_name == "check_availability"
                    and _col.get("phone")
                    and (_col.get("name") or _col.get("full_name"))
                    and not _caller_requests_new_day_or_time(messages or [])
                ):
                    logger.warning(
                        "[ms_llm] check_availability BLOCKED — name+phone already "
                        "collected; forcing booking readback call_sid=%s", call_sid,
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
                    if _rb_slot:
                        _rb_msg = (
                            "Name, phone number and the appointment slot are all "
                            "already confirmed. Do NOT call check_availability and "
                            "do NOT ask for the day or time again. Say EXACTLY this, "
                            "then stop: "
                            f"\"So that's {_rb_name_txt}, {_rb_slot}"
                            f"{_rb_loc_clause} — shall I go ahead and book that in?\""
                        )
                    else:
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
                        "message": _rb_msg,
                    }
                elif (
                    tool_name == "check_availability"
                    and session.get("v3_confirmed_slot_phrase")
                    and not session.get("last_offered_slots")
                    and not _caller_wants_new_slot(messages or [])
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
                    and not _caller_requests_different_day(messages or [])
                ):
                    # V5: if the caller asked for later / an unspoken time, do NOT
                    # tell the model to re-present the already-spoken slots.
                    # Serve the next unspoken batch (or confirm a resolved time)
                    # from session["available_days"].
                    from app.tools.slot_followup import (
                        remaining_slots_after_offer,
                        next_slot_batch,
                        utterance_requests_more_slots,
                        resolve_requested_time,
                        apply_next_batch_to_session,
                        apply_resolved_time_to_session,
                        build_followup_tool_result,
                    )
                    _days = session.get("available_days") or []
                    _offered = session.get("last_offered_slots") or []
                    _remaining = remaining_slots_after_offer(_days, _offered)
                    _user = _last_user_text(messages or [])
                    _hit = resolve_requested_time(_user, _remaining) if _remaining else None
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
                        _batch, _more = next_slot_batch(_remaining, n=2)
                        apply_next_batch_to_session(session, _batch, _more)
                        result = build_followup_tool_result(_days, _batch, _more)
                        logger.info(
                            "[ms_llm] check_availability → next unspoken batch "
                            "%s call_sid=%s",
                            [s.get("start") for s in _batch], call_sid,
                        )
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
                                "Use the data in available_days that was already returned. "
                                "Do NOT call check_availability again — present the existing "
                                "slots to the caller."
                            ),
                            "available_days": session.get("available_days", {}),
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
                    result = {
                        "status": "phone_confirmation_required",
                        "message": (
                            "book_appointment cannot fire yet — the caller's "
                            "phone number has not been confirmed. Do NOT book "
                            "and do NOT assume the calling number. Ask the phone "
                            "question as its own separate turn first: \"Is the "
                            "number you're calling on the best one for your "
                            "booking? If so, just say use this number.\" Wait "
                            "for the caller's answer, then read back the booking "
                            "summary and ask \"Shall I go ahead and book that "
                            "in?\" before calling book_appointment."
                        ),
                    }
                elif tool_name == "book_appointment" and not (
                    "shall i go ahead" in (session.get("last_bot_prompt") or "").lower()
                    or "book that in" in (session.get("last_bot_prompt") or "").lower()
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
                elif tool_name == "book_appointment" and not _book_reply_is_affirmative(messages):
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
                elif tool_name == "reschedule_appointment" and not (
                    "move it for you" in (session.get("last_bot_prompt") or "").lower()
                    and _book_reply_is_affirmative(messages)
                ):
                    # FM-23: reschedule gate — mirrors FM-01. The template reschedule
                    # CTA is the enforced "Shall I go ahead and move it for you?".
                    # Require that CTA in last_bot_prompt AND a clear caller yes.
                    _lut_preview = _last_user_text(messages or [])[:80]
                    logger.warning(
                        "[ms_llm] reschedule_appointment BLOCKED — no clear caller "
                        "yes after the move confirmation (last_user_text=%r)",
                        _lut_preview,
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
                    (
                        "cancel it altogether" in (session.get("last_bot_prompt") or "").lower()
                        or "altogether" in (session.get("last_bot_prompt") or "").lower()
                    )
                    and _cancel_reply_consents(messages)
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
                            "cancel_appointment cannot fire yet. Ask the retention "
                            "question — 'Would you like to reschedule this "
                            "appointment, or cancel it altogether?' — and only "
                            "cancel when the caller explicitly says cancel. Do not "
                            "cancel on a bare 'yes', a reschedule request, or an "
                            "ambiguous, negative, or absent reply."
                        ),
                    }
                elif tool_name == "escalate_to_claude":
                    result = await self._exec_escalate(args, session)
                else:
                    executor = TOOL_EXECUTORS.get(tool_name)
                    if executor:
                        # Filler phrases: play concurrently for slow API tools
                        _filler_list = _FILLER_TOOLS.get(tool_name)
                        if _filler_list and tts_text_queue is not None:
                            async def _tts_fn(text: str, _q=tts_text_queue) -> None:
                                await _q.put(text)
                            result = await with_filler(
                                api_coro=executor(args, session),
                                filler_list=_filler_list,
                                session=session,
                                tts_fn=_tts_fn,
                            )
                        else:
                            result = await executor(args, session)

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
                session["_check_av_had_slots"] = bool(
                    isinstance(result, dict) and result.get("available_days")
                )
                # Mark that a check ran this turn so the loop-level C8-5 silence
                # guarantee can choose the no-availability fallback over the
                # generic re-ask when the turn ends with no audible speech.
                session["_check_av_ran_turn"] = True

            # P1 #5 / F-023: record a successful booking write (Layer 1) and
            # attach a do-not-claim-success rule to a blocked/failed one
            # (Layer 2) before the model sees the result.
            result = _note_book_write_result(session, tool_name, result)

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
        _GPT_CONSTRAINT_PREFIX = (
            "You are a voice receptionist. Keep responses under 2 sentences. "
            "Never start with: Of course, Absolutely, Certainly, Sure, Great, "
            "No problem, No worries. "
            "Never say: take your time, no rush, bear with me, just a moment, "
            "I'd be happy to, I'd be glad to, go ahead whenever you are ready. "
            "Respond directly and naturally.\n\n"
        )

        try:
            from openai import AsyncOpenAI
            gpt_client   = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=15.0)
            tools        = _build_openai_tools(session)
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
                        if tool_name == "escalate_to_claude":
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


def _append_history(
    session: Dict[str, Any],
    user_text: str,
    assistant_text: str,
    spoken_text: Optional[str] = None,
) -> None:
    """Append a user/assistant exchange to conversation_history, trim to MAX_HISTORY_TURNS.

    `assistant_text` is the model's raw reply — what it generated.
    `spoken_text` is what actually reached TTS after Gate 5, i.e. what the caller
    heard. Pass it whenever the two can differ (the streaming LLM path); omit it
    on deterministic paths (fast path, slot follow-up) where the text we queue IS
    the text we speak.

    Only the obs record uses `spoken_text` — see the note at the obs_turns append
    for why conversation_history deliberately keeps the raw form.
    """
    history: List[dict] = session.setdefault("conversation_history", [])
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    if len(history) > MAX_HISTORY_TURNS:
        session["conversation_history"] = history[-MAX_HISTORY_TURNS:]
    session.setdefault("turns", []).append({"role": "assistant", "text": assistant_text})

    # Both sides of the exchange, for the observability capture/judge (app/obs/**).
    # The judge cannot score a call it can only half-hear, and session["turns"] on
    # this branch holds the assistant side only.
    #
    # This is deliberately a SEPARATE key rather than a fix to session["turns"]:
    # that list feeds the owner-facing actionable summary (_format_turns, max_turns=10)
    # and the SMS router (last-8 window), which have always been tuned against the
    # assistant-only shape. Adding caller turns there would halve those windows' real
    # coverage and change a live clinic's summaries as a side effect of an
    # observability port. Upstream `main` does fix session["turns"] in place; porting
    # that here is its own change with its own testing — see the note in the PR.
    #
    # Caller turn first, then the reply, preserving order. Skip empty/whitespace
    # caller text (e.g. silence turns).
    # The obs record stores what the CALLER HEARD (2026-07-29).
    #
    # It used to store `full_reply`, assembled from raw tokens — so a transcript
    # could not distinguish "the model generated this" from "the caller heard
    # this". Gate 5 runs per-chunk on the way to TTS, after that record is
    # written, and strips a great deal. Two conclusions were drawn from these
    # transcripts today and both were wrong in consequence: the A1 defect counts
    # were an inference in BOTH directions (over-reporting text the gate caught,
    # under-reporting severity where it caught nothing). Settling it needed the
    # raw text replayed through the real chunker and the real gate.
    #
    # Recording the spoken form removes that whole class of error: every detector
    # in scripts/detect_defects.py, the judge in app/obs/judge.py, and anyone
    # reading a transcript now sees the call as the caller experienced it.
    #
    # SCOPE, deliberate: only this record changes. conversation_history above
    # keeps the RAW reply because it is fed back to the model as its own prior
    # turns — rewriting the model's memory of what it said is a behavioural
    # change with its own risk, and is not part of an instrumentation fix.
    # session["turns"] also keeps the raw form: it feeds the owner-facing summary
    # and the SMS router for live clinics (see the note above). Both are worth
    # revisiting, separately, with their own tests.
    obs_turns = session.setdefault("obs_turns", [])
    if user_text and user_text.strip():
        obs_turns.append({"role": "user", "text": user_text})
    obs_turns.append({
        "role": "assistant",
        "text": assistant_text if spoken_text is None else spoken_text,
    })


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
    r")",
    re.IGNORECASE,
)


def _strip_interim_opener(text: str) -> str:
    """
    Remove a known interim phrase from the start of an LLM first chunk to
    prevent it being spoken twice (once from fast-path, once from the LLM).

    Also removes the first sentence if it contains "check" within the first
    15 words (catches paraphrases like "Let me just check what we have…").
    """
    stripped = _INTERIM_DUPE_RE.sub("", text).lstrip()
    if stripped != text:
        # Capitalise after stripping if needed
        if stripped:
            stripped = stripped[0].upper() + stripped[1:]
        return stripped

    # Fallback: strip first sentence if it contains "check" in first 15 words
    dot = text.find(".")
    if dot > 0:
        first_sentence = text[: dot + 1]
        words = first_sentence.split()[:15]
        if any("check" in w.lower() for w in words):
            remainder = text[dot + 1 :].lstrip()
            if remainder:
                return remainder[0].upper() + remainder[1:]

    return text


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
