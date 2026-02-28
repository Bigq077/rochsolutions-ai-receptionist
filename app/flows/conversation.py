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
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_SAFE_FALLBACK = (
    "I'm sorry, something went wrong on my end. "
    "Could you give me just a moment and try again?"
)

MAX_TOOL_ITERATIONS = 6


def _get_client():
    """Return a cached AsyncAnthropic client."""
    import os
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return AsyncAnthropic(api_key=api_key)


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

        # ------------------------------------------------------------------ #
        # 3. Tool execution loop
        # ------------------------------------------------------------------ #
        while iterations < MAX_TOOL_ITERATIONS:
            iterations += 1

            response = await client.messages.create(
                model=RECEPTIONIST_MODEL,
                max_tokens=1024,
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
            # Append the full assistant turn (may include both text + tool_use blocks)
            messages.append({
                "role": "assistant",
                "content": _blocks_to_dicts(content),
            })

            # Execute every tool call in this turn
            tool_results = []
            for block in tool_blocks:
                tool_name = block.name
                tool_input = block.input  # dict, already parsed by SDK

                executor = TOOL_EXECUTORS.get(tool_name)
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

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })

            # Tool results go back as a single user message
            messages.append({"role": "user", "content": tool_results})

        else:
            # Hit the max-iteration guard — return safe fallback
            logger.warning("handle_turn hit MAX_TOOL_ITERATIONS (%d)", MAX_TOOL_ITERATIONS)
            reply_text = _SAFE_FALLBACK

        # ------------------------------------------------------------------ #
        # 4. Store final reply in persistent history and session
        # ------------------------------------------------------------------ #
        history.append({"role": "assistant", "content": reply_text})
        session["conversation_history"] = history
        session["last_bot_prompt"] = reply_text

        return reply_text, session

    except Exception as exc:
        logger.error("handle_turn critical error: %r", exc, exc_info=True)
        return _SAFE_FALLBACK, session
