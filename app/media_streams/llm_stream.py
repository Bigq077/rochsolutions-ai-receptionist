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
  - If matched with needs_llm_followup=False: play response, skip LLM
  - If matched with needs_llm_followup=True: play interim response, then LLM
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
)
from .chunker import ResponseChunker
from .fast_path import try_fast_path
from .session import save_session, advance_state, CallState

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
            # needs_llm_followup=True: interim queued, continue to LLM

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
        if collected.get("full_name") or collected.get("name"):
            known_lines.append(f"- Name: {collected.get('full_name') or collected.get('name')}")
        if collected.get("phone"):
            known_lines.append(f"- Phone: {collected['phone']}")
        if session.get("selected_location"):
            known_lines.append(f"- Location: {session['selected_location']}")
        if collected.get("patient_type"):
            known_lines.append(f"- New/returning: {collected['patient_type']}")
        if session.get("last_offered_slots"):
            known_lines.append(f"- Slots offered: {session['last_offered_slots']}")
        state_ctx = (
            f"[CALL STATE: {call_state} — greeting already delivered. "
            f"Do not re-introduce yourself or re-ask anything already answered.]\n"
            + ("\n".join(known_lines) if known_lines else "")
        )
        system_prompt = f"{date_prefix}\n\n{state_ctx}\n\n{get_system_prompt(session)}"

        # ── Step 6-8: LLM streaming with tool loop ───────────────────────
        history: List[dict] = session.setdefault("conversation_history", [])
        messages: List[dict] = list(history[-MAX_HISTORY_TURNS:])
        messages.append({"role": "user", "content": user_text})

        tools       = _build_claude_tools()
        full_reply  = ""     # assembled from all chunks for history
        transfer_initiated = False

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
            )
        except Exception as exc:
            logger.error("[ms_llm] streaming_tool_loop error: %r", exc)
            full_reply = SAFE_FALLBACK_PHRASE
            await tts_text_queue.put(SAFE_FALLBACK_PHRASE)

        # ── Step 9: Update history ───────────────────────────────────────
        if not transfer_initiated:
            _append_history(session, user_text, full_reply)
            session[F_LAST_BOT_PROMPT] = full_reply
            session[F_LAST_QUESTION]   = full_reply

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
            if session.pop("request_transfer", False):
                logger.info("[ms_llm] transfer requested call_sid=%s", call_sid)
                if on_transfer:
                    await on_transfer()
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
        got_first_chunk = False

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
                if tool_name == "escalate_to_claude":
                    result = await self._exec_escalate(args, session)
                else:
                    executor = TOOL_EXECUTORS.get(tool_name)
                    if executor:
                        result = await executor(args, session)
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

                if session.pop("request_transfer", False):
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
    """Advance call state after a fast-path resolution."""
    from .config import FastPathTurnType
    _MAP = {
        FastPathTurnType.CLINIC_SELECTION:  CallState.NEW_OR_RETURNING,
        FastPathTurnType.NEW_RETURNING:     CallState.COLLECT_NAME,
        FastPathTurnType.FULL_NAME:         CallState.COLLECT_PHONE_PART_ONE,
        FastPathTurnType.PHONE_FIRST_FIVE:  CallState.COLLECT_PHONE_PART_TWO,
        FastPathTurnType.PHONE_LAST_SIX:    CallState.COLLECT_AVAILABILITY,
        FastPathTurnType.SLOT_SELECTION:    CallState.CONFIRM_BOOKING,
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
