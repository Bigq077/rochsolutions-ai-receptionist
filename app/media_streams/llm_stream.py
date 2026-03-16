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
    HAIKU,
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
    F_FAST_PATH_LAST_RESOLVED,
    F_PHONE_COLLECTED_FROM_TWILIO,
    SILENCE_RULE,
    BOOKING_OPEN,
    BOOKING_INTENT_KEYWORDS,
    AVAILABILITY_FLOW_RULE,
    NAME_COLLECTION_RULE,
    NEW_OR_RETURNING_RULE,
    PHONE_READBACK_RULE,
    INFORMAL_SPEECH_RULE,
    LLM_STATE_INSTRUCTIONS,
    Q_CHECKING,
)
from .chunker import ResponseChunker
from .fast_path import try_fast_path
from .session import save_session, advance_state, CallState
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
    tools.append({
        "name": "escalate_to_claude",
        "description": (
            "Use ONLY for genuine clinical or legal complexity requiring deep reasoning. "
            "Never for standard greetings, FAQs, availability, booking, pricing, hours, "
            "or common conditions -- handle all of those directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The caller's question or situation requiring deep reasoning.",
                }
            },
            "required": ["question"],
        },
    })
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
    tools.append({
        "type": "function",
        "function": {
            "name": "escalate_to_claude",
            "description": (
                "Use ONLY for genuine clinical or legal complexity. "
                "Never for standard greetings, FAQs, or common booking queries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The caller's question."}
                },
                "required": ["question"],
            },
        },
    })
    return tools


# ---------------------------------------------------------------------------
# Model selector
# ---------------------------------------------------------------------------

def _pick_model(session: Dict[str, Any]) -> str:
    """
    Return SONNET for active booking steps, HAIKU for everything else.

    SONNET triggers:
      - Slots have been offered (last_offered_slots is non-empty)
      - Booking is being confirmed (acuity_booking_id pending)
      - Name or phone collection is in progress
    """
    collected = session.get(F_COLLECTED) or {}
    if session.get("last_offered_slots"):
        return SONNET
    if session.get("acuity_booking_id"):
        return SONNET
    if collected.get("full_name") and not collected.get("phone"):
        return SONNET   # mid-phone-collection
    return HAIKU


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
        # ── Step 0: Booking-intent fast-path ────────────────────────────
        # When the caller expresses booking intent, play BOOKING_OPEN directly
        # (no LLM round-trip) so the wording is always deterministic.
        from .session import get_call_state, CallState, advance_state
        _state_now = get_call_state(session)
        if _state_now in (CallState.GREETING, CallState.NEW_OR_RETURNING):
            _norm = user_text.lower()
            _has_intent = any(kw in _norm for kw in BOOKING_INTENT_KEYWORDS)
            if _has_intent:
                logger.info(
                    "[ms_llm] booking intent detected — injecting BOOKING_OPEN "
                    "transcript=%r", user_text[:80],
                )
                await tts_text_queue.put(BOOKING_OPEN)
                session[F_LAST_BOT_PROMPT] = BOOKING_OPEN
                session[F_LAST_QUESTION]   = BOOKING_OPEN
                # Advance to NEW_OR_RETURNING — BOOKING_OPEN asks new/returning
                advance_state(session, CallState.NEW_OR_RETURNING)
                _append_history(session, user_text, BOOKING_OPEN)
                await save_session(call_sid, session)
                return

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
        from app.prompts.susie_system_prompt import get_system_prompt
        date_prefix = _build_date_prefix()

        # Inject current call state so Claude knows exactly where we are
        # and never re-asks something already answered.
        # ~100 tokens prepended; cached via cache_control=ephemeral after first call.
        call_state = session.get("state", "GREETING")
        collected  = session.get("collected") or {}
        known_lines: List[str] = []
        if session.get("reason"):
            known_lines.append(f"- Reason for visit: {session['reason']}")
        if session.get("duration"):
            known_lines.append(f"- Duration: {session['duration']}")
        if collected.get("patient_type"):
            known_lines.append(f"- New/returning: {collected['patient_type']}")
        if session.get("availability_preference"):
            known_lines.append(f"- Availability: {session['availability_preference']}")
        if session.get("selected_slot"):
            known_lines.append(f"- Selected slot: {session['selected_slot']}")
        if collected.get("full_name") or collected.get("name"):
            known_lines.append(f"- Name: {collected.get('full_name') or collected.get('name')}")
        if session.get("phone_number"):
            known_lines.append(f"- Phone: {session['phone_number']}")
        elif collected.get("phone"):
            if session.get("phone_from_twilio"):
                known_lines.append(
                    f"- Phone (auto-detected from caller-ID — do NOT ask for it): {collected['phone']}"
                )
            else:
                known_lines.append(f"- Phone: {collected['phone']}")
        if session.get("selected_location"):
            known_lines.append(f"- Location: {session['selected_location']}")
        if session.get("last_offered_slots"):
            known_lines.append(f"- Slots offered: {session['last_offered_slots']}")
        phone_rule = (
            "\n[PHONE ALREADY KNOWN via caller-ID: Do NOT ask for the caller's phone number. "
            "When you reach that step, say 'I have your number from this call — "
            "I'll use that for the booking.' and move straight on.]"
            if session.get("phone_from_twilio") else ""
        )
        # Inject per-state LLM instructions (only for the 3 states that use LLM)
        state_instruction = LLM_STATE_INSTRUCTIONS.get(call_state, "")
        if state_instruction:
            state_instruction = "\n" + state_instruction

        state_ctx = (
            f"[CALL STATE: {call_state} — greeting already delivered. "
            f"Do not re-introduce yourself or re-ask anything already answered.]\n"
            + ("\n".join(known_lines) if known_lines else "")
            + phone_rule
            + state_instruction
            + "\n[TRANSFER RULE: Never say 'I'll put you through', 'let me transfer you', "
            "'I'll pass you to the team', or any transfer/handoff phrase in your spoken "
            "response UNLESS the caller has explicitly asked to speak to a person OR "
            "mentioned a medical emergency. If you are unsure what the caller wants, "
            "ask a single clarifying question instead of offering a transfer.]"
        )
        # Rules are prepended in priority order so Claude reads them first.
        # SILENCE_RULE is first (most critical), then content rules, then main prompt.
        system_prompt = (
            f"{SILENCE_RULE}\n\n"
            f"{AVAILABILITY_FLOW_RULE}\n\n"
            f"{NAME_COLLECTION_RULE}\n\n"
            f"{NEW_OR_RETURNING_RULE}\n\n"
            f"{PHONE_READBACK_RULE}\n\n"
            f"{INFORMAL_SPEECH_RULE}\n\n"
            f"{date_prefix}\n\n"
            f"{state_ctx}\n\n"
            f"{get_system_prompt(session)}"
        )

        # Bug 3: if we're in COLLECT_AVAILABILITY state, block check_availability
        # on THIS turn so Claude cannot call it on the same turn it asked the question.
        # The flag is read and consumed in _execute_tools.
        if get_call_state(session) == CallState.COLLECT_AVAILABILITY:
            if not session.get("_availability_response_received"):
                session["block_check_availability"] = True
                logger.debug("[ms_llm] block_check_availability set for this turn")

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
            session[F_LAST_BOT_PROMPT] = full_reply
            # Store only the question portion in F_LAST_QUESTION.
            # F_LAST_BOT_PROMPT keeps the full response for fast-path trigger
            # matching; F_LAST_QUESTION is narrowed to the actual question
            # sentence so the re-ask watchdog only replays real questions.
            session[F_LAST_QUESTION] = _question_from_response(full_reply)

        await save_session(call_sid, session)

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

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            logger.info("[ms_llm] iteration=%d model=%s", iteration, model)

            # ── Try Claude streaming ──────────────────────────────────────
            try:
                chunk_text, tool_uses, did_transfer = await self._one_streaming_call(
                    client=client,
                    model=model,
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    session=session,
                    tts_text_queue=tts_text_queue,
                    filler_sent=filler_sent,
                    # Only suppress on first iteration — subsequent iterations
                    # (after tool calls) generate genuinely new text.
                    interim_played=(interim_played and iteration == 1),
                )
                filler_sent = True  # suppress filler on subsequent iterations

            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in (429, 500, 529) and OPENAI_API_KEY:
                    logger.warning("[ms_llm] Claude %s -- switching to GPT fallback", status)
                    reply = await self._gpt_fallback(
                        system_prompt=system_prompt,
                        messages=messages,
                        session=session,
                        tts_text_queue=tts_text_queue,
                    )
                    full_reply += reply
                    return full_reply, False
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
            tool_result_blocks = await self._execute_tools(
                tool_uses, session, call_sid,
            )
            messages.append({"role": "user", "content": tool_result_blocks})

            await save_session(call_sid, session)

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
        first_chunk_deadline = (
            time.monotonic() + LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0
        )
        got_first_chunk      = False
        _first_tts_emitted   = False  # tracks whether first TTS chunk has been sent

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

                # ── Filler guard: check deadline while streaming ───────────
                if not got_first_chunk and not filler_sent:
                    now = time.monotonic()
                    if now >= first_chunk_deadline:
                        if now - self._last_filler_at >= LLM_FILLER_COOLDOWN_SEC:
                            logger.info("[ms_llm] filler phrase triggered")
                            await tts_text_queue.put(FILLER_PHRASE)
                            self._last_filler_at = now
                            filler_sent = True

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

        return full_text, tool_uses, False

    # -----------------------------------------------------------------------
    # Tool execution
    # -----------------------------------------------------------------------

    async def _execute_tools(
        self,
        tool_uses: List[dict],
        session: Dict[str, Any],
        call_sid: Optional[str],
    ) -> List[dict]:
        """
        Execute all tool calls and return the tool_result blocks for Anthropic.
        """
        from app.tools.receptionist_tools import TOOL_EXECUTORS

        result_blocks: List[dict] = []

        for tu in tool_uses:
            tool_name = tu["name"]
            args      = tu["input"]

            logger.info(
                "[ms_llm] tool: name=%s id=%s args=%s",
                tool_name, tu["id"], json.dumps(args, default=str)[:200],
            )

            try:
                # Bug 3: block check_availability if it was called on the same
                # turn the availability question was asked — force Claude to wait
                # for the caller's response on a subsequent turn.
                if tool_name == "check_availability" and session.get("block_check_availability"):
                    logger.warning(
                        "[ms_llm] check_availability BLOCKED — same turn as availability "
                        "question; caller has not yet responded call_sid=%s", call_sid,
                    )
                    session.pop("block_check_availability", None)
                    result = {
                        "status": "blocked",
                        "message": (
                            "You asked the caller about their availability on this same turn. "
                            "Do not check slots yet — wait for the caller to tell you their "
                            "preferred days or times, then call check_availability."
                        ),
                    }
                elif tool_name == "escalate_to_claude":
                    result = await self._exec_escalate(args, session)
                else:
                    executor = TOOL_EXECUTORS.get(tool_name)
                    if executor:
                        # If check_availability is called and succeeds, mark that
                        # availability has been collected so the block doesn't re-fire.
                        if tool_name == "check_availability":
                            session.pop("block_check_availability", None)
                            session["_availability_response_received"] = True
                        result = await executor(args, session)
                        # Fix 2: mark slots as presented the moment check_availability
                        # returns slots so the LLM knows not to re-present them.
                        if tool_name == "check_availability" and session.get("last_offered_slots"):
                            session["slots_presented"] = True
                            logger.info("[ms_llm] slots_presented=True (slots returned by check_availability)")
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
            return {"reply": "I'm sorry, I had a little trouble with that. Could you give me a moment?"}

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
    """
    Advance call state after a fast-path resolution.

    FULL_NAME skips phone collection when phone is already known from Twilio.
    """
    from .config import FastPathTurnType

    # ── FULL_NAME: skip phone collection if Twilio number is already known ────
    if turn_type == FastPathTurnType.FULL_NAME:
        collected = session.get(F_COLLECTED) or {}
        if session.get(F_PHONE_COLLECTED_FROM_TWILIO) and collected.get("phone"):
            # Phone already known — jump straight to availability
            advance_state(session, CallState.COLLECT_AVAILABILITY)
        else:
            advance_state(session, CallState.COLLECT_PHONE)
        return

    # ── Static transitions ────────────────────────────────────────────────────
    _MAP = {
        FastPathTurnType.NEW_RETURNING:  CallState.COLLECT_NAME,
        FastPathTurnType.SLOT_SELECTION: CallState.CONFIRM_BOOKING,
    }
    next_state = _MAP.get(turn_type)
    if next_state:
        advance_state(session, next_state)


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
