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
import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .config import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    SONNET,
    GPT_MODEL,
    CLAUDE_MAX_TOKENS,
    CLAUDE_TEMPERATURE,
    MAX_TOOL_ITERATIONS,
    MAX_HISTORY_TURNS,
    LLM_FIRST_CHUNK_TIMEOUT_MS,
    LLM_FILLER_COOLDOWN_SEC,
    FILLER_PHRASE,
    SAFE_FALLBACK_PHRASE,
    F_LAST_BOT_PROMPT,
    F_LAST_QUESTION,
    F_COLLECTED,
)
from .chunker import ResponseChunker
from .fast_path import try_fast_path
from .session import save_session
from .tts_stream import _apply_tts_substitutions_elevenlabs as _apply_tts_subs
from .turn_handler import sanitise_response

logger = logging.getLogger(__name__)


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

def _build_claude_tools() -> list:
    """Return tool definitions in Anthropic native format."""
    from app.tools.receptionist_tools import TOOL_SCHEMAS
    tools = list(TOOL_SCHEMAS)
    return tools


def _build_openai_tools() -> list:
    """Return tool definitions in OpenAI function-calling format."""
    from app.tools.receptionist_tools import TOOL_SCHEMAS
    tools = []
    for tool in TOOL_SCHEMAS:
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
    """Always use Sonnet — free-form loop requires consistent reasoning."""
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
                # Update history with fast-path exchange
                _append_history(session, user_text, fp_result.response_text)
                await save_session(call_sid, session)
                return
            # needs_llm_followup=True: interim phrase already queued.
            # Mark it so _one_streaming_call strips the duplicate from the
            # start of the LLM response (BUG 2 fix).
            session["interim_played"] = True

        # ── Step 4: Model selection ──────────────────────────────────────
        model = _pick_model(session)

        # ── Step 5: System prompt ────────────────────────────────────────
        from app.prompts.susie_system_prompt import build_system_prompt
        system_prompt = build_system_prompt(session)

        # ── Step 6-8: LLM streaming with tool loop ───────────────────────
        history: List[dict] = session.setdefault("conversation_history", [])
        messages: List[dict] = list(history[-MAX_HISTORY_TURNS:])
        messages.append({"role": "user", "content": user_text})

        tools       = _build_claude_tools()
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
            _append_history(session, user_text, full_reply)
            # Sanitize full_reply before extracting last_bot_prompt / last_question.
            # sanitise_response strips internal reasoning sentences that the LLM
            # sometimes narrates aloud (e.g. "The caller said...", "I'll pick the
            # three with the most slots...").  These sentences are already stripped
            # per-chunk before reaching TTS, but full_reply is assembled from raw
            # tokens — without this step, reasoning text fills last_bot_prompt[:200]
            # before the actual spoken response, causing _parse_v3_slot_options to
            # miss numbered options and v3_awaiting_slot_selection to stay unset,
            # which in turn fires the watchdog 4.5 s after TTS ends instead of 10 s.
            _display_reply = sanitise_response(full_reply, session)
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

        tools    = _build_claude_tools() if allow_tools else []

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
    # Slot presentation self-correction filter
    # -----------------------------------------------------------------------

    @staticmethod
    async def _flush_slot_buf(
        buf_queue: asyncio.Queue,
        tts_queue: asyncio.Queue,
        session: Dict[str, Any],
    ) -> None:
        """
        Drain the per-iteration slot presentation buffer and flush to the
        real TTS queue, removing mid-stream self-corrections.

        Self-correction pattern: the LLM starts listing numbered options
        ("Number 1, ...", "Number 2, ...") then backtracks with "Actually"
        or similar.  Everything up to and including the correction marker
        is discarded; only the corrected content that follows is flushed.
        If no correction is found, all buffered chunks are flushed unchanged.

        Chunks in the buffer are already gate5-filtered — no further
        sanitisation is needed here.
        """
        chunks: List[str] = []
        while True:
            try:
                chunks.append(buf_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not chunks:
            return

        # Scan for self-correction: "Actually" after a numbered slot option.
        _had_numbered = False
        _correction_idx: Optional[int] = None
        for i, c in enumerate(chunks):
            cl = c.lower()
            if any(f"number {n}" in cl for n in ("1", "2", "3", "4", "one", "two", "three")):
                _had_numbered = True
            if _had_numbered and (
                cl.strip().startswith("actually")
                or ", actually" in cl
                or "— actually" in cl
                or "- actually" in cl
            ):
                _correction_idx = i
                # Don't break — keep scanning for the last correction

        if _correction_idx is not None and _correction_idx + 1 < len(chunks):
            _pre  = chunks[:_correction_idx + 1]
            _post = chunks[_correction_idx + 1:]
            logger.info(
                "[ms_gate5] slot self-correction removed: "
                "%d chunk(s) discarded, %d kept; first_discarded=%r",
                len(_pre), len(_post), (_pre[0][:50] if _pre else ""),
            )
            session["_gate5_reasoning_drops"] = (
                int(session.get("_gate5_reasoning_drops") or 0) + len(_pre)
            )
            to_flush = _post
        else:
            to_flush = chunks

        for c in to_flush:
            await tts_queue.put(c)

        logger.info("[ms_gate5] slot buf flushed: %d chunk(s) to TTS", len(to_flush))

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
        # slot presentation buffer for the following iteration so mid-stream
        # self-corrections ("Number 3... Actually... I've got two options") are
        # removed before audio reaches TTS.
        _last_check_avail: bool = False

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            logger.info("[ms_llm] iteration=%d model=%s", iteration, model)

            # ── Slot self-correction buffer ───────────────────────────────
            # When the previous iteration executed check_availability the LLM
            # may stream a partial/wrong slot list before self-correcting with
            # "Actually".  Route this iteration's output through a temporary
            # buffer; _flush_slot_buf strips pre-correction chunks before
            # anything reaches TTS.
            _slot_buf: Optional[asyncio.Queue] = None
            _active_q = tts_text_queue
            if _last_check_avail:
                _slot_buf = asyncio.Queue()
                _active_q = _slot_buf
                logger.info(
                    "[ms_llm] slot buffer active (post-check_availability) iter=%d",
                    iteration,
                )
            _last_check_avail = False  # reset; re-armed below after tool execution

            # ── Try Claude streaming ──────────────────────────────────────
            try:
                chunk_text, tool_uses, did_transfer = await self._one_streaming_call(
                    client=client,
                    model=model,
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    session=session,
                    tts_text_queue=_active_q,
                    filler_sent=filler_sent,
                    # Only suppress on first iteration — subsequent iterations
                    # (after tool calls) generate genuinely new text.
                    interim_played=(interim_played and iteration == 1),
                )
                filler_sent = True  # suppress filler on subsequent iterations

                # ── Flush slot buffer with self-correction filtering ──────
                if _slot_buf is not None:
                    await self._flush_slot_buf(_slot_buf, tts_text_queue, session)

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
            )
            messages.append({"role": "user", "content": tool_result_blocks})

            # Re-arm slot buffer for the next iteration if check_availability ran.
            _last_check_avail = any(
                tu.get("name") == "check_availability" for tu in tool_uses
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
                break

            if session.get("rc_lookup_just_succeeded"):
                # flow.py rc_lookup_just_succeeded handler (ask_current_question)
                # will drain TTS and emit a single deterministic confirmation.
                logger.info(
                    "[ms_llm] rc_lookup_just_succeeded after tool — "
                    "suppressing post-tool LLM response"
                )
                break

            if session.get("rc_appointment_confirmed"):
                # flow.py rc_appointment_confirmed handler advances the flow and
                # asks CONFIRM_RESCHEDULE_OR_CANCEL — no LLM speech needed.
                logger.info(
                    "[ms_llm] rc_appointment_confirmed after tool — "
                    "suppressing post-tool LLM response"
                )
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
    ) -> tuple:
        """
        Open one Claude streaming session, feed tokens through the chunker,
        and put text chunks onto tts_text_queue.

        Returns (full_text, tool_uses, transfer_initiated).
        tool_uses is non-empty if stop_reason == "tool_use".
        """
        chunker    = ResponseChunker()
        full_text  = ""
        tool_uses: List[dict] = []
        timeout_sec          = LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0
        got_first_chunk      = False
        _first_tts_emitted   = False  # tracks whether first TTS chunk has been sent

        # Background task: fire filler phrase after timeout if no text yet.
        # Cannot rely on stream events alone — if Claude takes >5s to send
        # the first event, the in-loop deadline check never fires.
        _filler_task: "asyncio.Task | None" = None
        if not filler_sent and (time.monotonic() - self._last_filler_at) >= LLM_FILLER_COOLDOWN_SEC:
            async def _delayed_filler() -> None:
                await asyncio.sleep(timeout_sec)
                if not got_first_chunk:
                    logger.info("[ms_llm] filler phrase triggered (background task)")
                    await tts_text_queue.put(FILLER_PHRASE)
                    self._last_filler_at = time.monotonic()
            _filler_task = asyncio.create_task(_delayed_filler(), name="ms_llm_filler")

        async with client.messages.stream(
            model=model,
            system=[{
                "type":          "text",
                "text":          system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
            tools=tools,
            max_tokens=CLAUDE_MAX_TOKENS,
            temperature=CLAUDE_TEMPERATURE,
        ) as stream:

            async for event in stream:
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
                                    await tts_text_queue.put(chunk)

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
                    await tts_text_queue.put(final_chunk)

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

            elif stop_reason == "end_turn" and not full_text.strip():
                # Empty response -- caller receives nothing; handled by nudge in loop
                pass

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
        )

        # Tools that get filler phrases → list to draw from
        _FILLER_TOOLS = {
            "check_availability": THINKING_FILLERS_PRIMARY,
            "book_appointment":   BOOKING_WRITE_FILLERS,
            "lookup_patient":     THINKING_FILLERS_PRIMARY,
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
                # Dedup guard: block check_availability if slots were already
                # retrieved this turn (last_offered_slots populated).  The LLM
                # must use the data already returned rather than re-fetching.
                # Allows a second call only if the session key was cleared
                # upstream (e.g. caller explicitly asked for a new date range
                # and connection.py cleared last_offered_slots).
                if tool_name == "check_availability" and session.get("last_offered_slots"):
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

        try:
            from openai import AsyncOpenAI
            gpt_client   = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=15.0)
            tools        = _build_openai_tools()
            oai_messages = [{"role": "system", "content": system_prompt}] + list(messages)
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

        await tts_text_queue.put(reply_text)
        return reply_text


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
) -> None:
    """Append a user/assistant exchange to conversation_history, trim to MAX_HISTORY_TURNS."""
    history: List[dict] = session.setdefault("conversation_history", [])
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    if len(history) > MAX_HISTORY_TURNS:
        session["conversation_history"] = history[-MAX_HISTORY_TURNS:]
    session.setdefault("turns", []).append({"role": "assistant", "text": assistant_text})


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
