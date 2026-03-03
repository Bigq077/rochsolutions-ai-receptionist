# app/flows/conversation.py
"""
Phase 3 conversation handler — replaces the state machine.

handle_turn(user_text, session) is called from /twilio/turn when PHASE3_ENABLED=true.
It runs a tool-calling loop using the Anthropic Messages API (Claude Sonnet 4.6),
executing receptionist tools until Claude produces a final spoken response.

Anthropic API format notes:
  - system prompt is a separate parameter, not part of the messages list
  - tool results go back as a {"role": "user", "content": [tool_result_blocks]} message
  - response.content is a list of TextBlock / ToolUseBlock objects
  - response.stop_reason == "tool_use"  → more tools to run
  - response.stop_reason == "end_turn"  → final spoken response

Latency optimisations (no quality reduction):
  - Parallel tool execution via asyncio.gather() — multiple tools in one turn run concurrently
  - Hybrid model: Haiku for turns after simple-only tools; Sonnet for booking/calendar/transfer
  - max_tokens capped at 400 — phone responses are always 1-3 sentences
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

# Redis key that tracks how many times Phase 3 fell back to _SAFE_FALLBACK.
# Exposed on /health so ops teams can see if the model is struggling.
_FALLBACK_COUNTER_KEY = "metrics:phase3:fallbacks"


async def _increment_fallback_counter() -> None:
    """Increment the Phase 3 safe-fallback counter in Redis (best-effort)."""
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.incr(_FALLBACK_COUNTER_KEY)
    except Exception:
        pass  # Never let a metrics failure disrupt call handling


_SAFE_FALLBACK = (
    "I'm sorry, something went wrong on my end. "
    "Could you give me just a moment and try again?"
)

MAX_TOOL_ITERATIONS = 6

# Maximum number of messages kept in persistent conversation_history.
# Each exchange = 2 entries (user + assistant).  20 entries = 10 exchanges,
# which is more than enough context for a phone call while preventing Redis
# session bloat and Anthropic token-limit errors on very long calls.
MAX_HISTORY_TURNS = 20

# Tools that don't need Sonnet-quality reasoning for the follow-up response.
# After a turn where ONLY these tools ran, the next LLM call uses Haiku.
_FAST_TOOLS: Set[str] = {
    "collect_and_store",
    "get_clinic_info",
    "log_call_outcome",
    "send_followup_sms",
}

# Tools that require Sonnet for the follow-up (complex confirmation, edge cases).
_QUALITY_TOOLS: Set[str] = {
    "check_availability",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "transfer_to_human",
}


def _get_client():
    """Return an AsyncAnthropic client."""
    import os
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return AsyncAnthropic(api_key=api_key)


def _pick_model(tools_just_run: Set[str]) -> str:
    """
    Choose the LLM model for the next API call.

    - No previous tools (first call): always Sonnet — Claude needs to plan strategy.
    - Only fast/simple tools ran: Haiku is sufficient to formulate the follow-up question.
    - Any quality/booking tool ran: keep Sonnet — complex confirmation or edge cases.
    """
    from app.config import RECEPTIONIST_MODEL, HAIKU_MODEL
    if tools_just_run and not (tools_just_run & _QUALITY_TOOLS):
        return HAIKU_MODEL
    return RECEPTIONIST_MODEL


def _extract_text(content_blocks) -> str:
    """Pull the first TextBlock text out of a response content list."""
    for block in content_blocks or []:
        if getattr(block, "type", None) == "text" and block.text:
            return block.text.strip()
    return ""


def _blocks_to_dicts(content_blocks) -> List[Dict]:
    """
    Convert Anthropic SDK content block objects to plain dicts so they
    can be safely appended to the messages list for the next API call.
    """
    out = []
    for block in content_blocks:
        t = getattr(block, "type", None)
        if t == "text":
            out.append({"type": "text", "text": block.text})
        elif t == "tool_use":
            out.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,  # already a dict from the SDK
            })
    return out


async def _run_tool(
    block,
    session: Dict[str, Any],
    executors: Dict,
) -> Tuple[str, Dict]:
    """Execute a single tool call and return (tool_use_id, result_dict)."""
    tool_name = block.name
    tool_input = block.input  # dict, already parsed by SDK

    executor = executors.get(tool_name)
    if executor is None:
        result = {"error": f"Unknown tool: {tool_name}"}
    else:
        try:
            result = await executor(tool_input, session)
        except Exception as exc:
            logger.error("Tool %s raised: %r", tool_name, exc, exc_info=True)
            result = {"error": str(exc)}

    logger.info(
        "TOOL %s | input=%s | result=%s",
        tool_name,
        json.dumps(tool_input, default=str),
        json.dumps(result, default=str),
    )
    return block.id, result


async def handle_turn(
    user_text: str,
    session: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Main entry point.

    Takes the caller's latest utterance and the mutable session dict.
    Returns (spoken_reply, updated_session).
    Never raises — always returns a safe spoken string on error.
    """
    from app.config import RECEPTIONIST_MODEL
    from app.prompts.susie_system_prompt import get_system_prompt
    from app.tools.receptionist_tools import TOOL_SCHEMAS, TOOL_EXECUTORS

    try:
        # ------------------------------------------------------------------ #
        # 1. Append the user's turn to conversation history
        # ------------------------------------------------------------------ #
        history: List[Dict] = session.setdefault("conversation_history", [])
        history.append({"role": "user", "content": user_text})

        # Ensure call_start_time is set (used by build_call_summary at call end)
        if not session.get("call_start_time"):
            session["call_start_time"] = datetime.utcnow().isoformat() + "Z"

        # ------------------------------------------------------------------ #
        # 2. Build working messages list
        #    We keep a separate list that grows with tool-use intermediates
        #    so the persistent history only ever stores clean text turns.
        # ------------------------------------------------------------------ #
        messages: List[Dict] = list(history)  # shallow copy — we only append

        system_prompt = get_system_prompt(session)
        client = _get_client()

        iterations = 0
        reply_text = _SAFE_FALLBACK
        last_assistant_text = ""  # text Claude said alongside a tool call (may be reused)
        last_tools_run: Set[str] = set()  # tools executed in the previous iteration

        # ------------------------------------------------------------------ #
        # 3. Tool execution loop
        # ------------------------------------------------------------------ #
        while iterations < MAX_TOOL_ITERATIONS:
            iterations += 1

            # Hybrid model selection: first call always Sonnet; subsequent calls
            # use Haiku if only fast/simple tools ran in the previous iteration.
            model = _pick_model(last_tools_run) if iterations > 1 else RECEPTIONIST_MODEL

            logger.info(
                "handle_turn iteration=%d model=%s last_tools=%s",
                iterations,
                model,
                last_tools_run or "none",
            )

            response = await client.messages.create(
                model=model,
                max_tokens=400,
                system=system_prompt,
                messages=messages,
                tools=TOOL_SCHEMAS,
                timeout=12.0,  # Stay well within Twilio's 15-second webhook limit
            )

            stop_reason = response.stop_reason
            content = response.content or []

            tool_blocks = [b for b in content if getattr(b, "type", None) == "tool_use"]

            logger.info(
                "handle_turn iteration=%d stop_reason=%s tool_blocks=%d content_types=%s",
                iterations,
                stop_reason,
                len(tool_blocks),
                [getattr(b, "type", "?") for b in content],
            )

            # ---- Final response (no more tools) ---- #
            if stop_reason == "end_turn" or not tool_blocks:
                raw_text = _extract_text(content)

                # Anthropic sometimes returns empty content after a tool call
                # when Claude already spoke in the same turn as the tool (iteration N
                # had content_types=['text','tool_use']).  Reuse that earlier text.
                if not raw_text and last_assistant_text:
                    logger.info(
                        "handle_turn: empty final response — reusing text from previous "
                        "iteration (stop_reason=%s)",
                        stop_reason,
                    )
                    raw_text = last_assistant_text

                if not raw_text:
                    logger.warning(
                        "handle_turn: empty text from Anthropic — using SAFE_FALLBACK "
                        "(stop_reason=%s, content_types=%s)",
                        stop_reason,
                        [getattr(b, "type", "?") for b in content],
                    )
                reply_text = raw_text or _SAFE_FALLBACK
                break

            # ---- Tool calls to execute ---- #
            # Save any text Claude spoke alongside this tool call so we can
            # reuse it if the next response comes back empty (Anthropic sometimes
            # sends empty content after a combined text+tool_use turn).
            last_assistant_text = _extract_text(content)

            # Append the full assistant turn (may include both text + tool_use blocks)
            messages.append({
                "role": "assistant",
                "content": _blocks_to_dicts(content),
            })

            # Execute all tool calls in parallel — safe because asyncio is single-threaded
            # (coroutines interleave but don't truly run concurrently, so session dict
            # mutations from different tools cannot race each other).
            raw_results = await asyncio.gather(
                *[_run_tool(block, session, TOOL_EXECUTORS) for block in tool_blocks]
            )

            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, default=str),
                }
                for tool_use_id, result in raw_results
            ]

            # Track which tools ran so the next iteration can pick the right model
            last_tools_run = {block.name for block in tool_blocks}

            # Tool results go back as a single user message
            messages.append({"role": "user", "content": tool_results})

        else:
            # Hit the max-iteration guard — return safe fallback
            logger.warning("handle_turn hit MAX_TOOL_ITERATIONS (%d)", MAX_TOOL_ITERATIONS)
            await _increment_fallback_counter()
            reply_text = _SAFE_FALLBACK

        # ------------------------------------------------------------------ #
        # 4. Store final reply in persistent history and session.
        #    FIX #3: Prune to MAX_HISTORY_TURNS to prevent Redis session
        #    bloat and Anthropic token-limit errors on long calls.
        # ------------------------------------------------------------------ #
        history.append({"role": "assistant", "content": reply_text})
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]
            logger.info(
                "handle_turn: pruned conversation_history to last %d turns",
                MAX_HISTORY_TURNS,
            )
        session["conversation_history"] = history
        session["last_bot_prompt"] = reply_text

        return reply_text, session

    except Exception as exc:
        logger.error("handle_turn critical error: %r", exc, exc_info=True)
        await _increment_fallback_counter()
        return _SAFE_FALLBACK, session
