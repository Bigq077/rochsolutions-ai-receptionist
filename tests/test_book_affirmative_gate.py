"""
tests/test_book_affirmative_gate.py
-----------------------------------
F20 — book_appointment must require an explicit, unambiguous YES.

Sweep finding F20 (docs/sweep_findings.md, Call 8, user-raised): the book guard
at llm_stream.py checks only that the confirmation QUESTION was asked
(last_bot_prompt contains "shall i go ahead" / "book that in"), NOT that the
caller gave a clear yes. Once the question is asked, a weak/ambiguous/negative
reply could still book. Silence is already safe (no transcript → no turn); the
gap is a *verbal* non-yes.

Fix: allow book_appointment only when (confirm question asked) AND (clear yes),
where clear yes reuses fast_path's _YES_PATTERNS/_NO_PATTERNS as
`is_yes and not is_no`. The caller's utterance is threaded in via a new
`last_user_text` param on _execute_tools. Ambiguous/negative/empty → block.

Bias: a false block just re-asks (safe); a false allow is a wrong booking.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.llm_stream import LLMStream

CONFIRM_PROMPT = (
    "So that's James, Wednesday the 8th of July at nine in the morning "
    "— shall I go ahead and book that in?"
)


async def _run_book(last_bot_prompt: str, last_user_text: str):
    """Drive _execute_tools with a single book_appointment tool_use.

    Returns (mock_executor, parsed_result_dict)."""
    stream = object.__new__(LLMStream)
    session = {"last_bot_prompt": last_bot_prompt}
    tool_uses = [{"name": "book_appointment", "input": {"name": "James"}, "id": "t1"}]
    mock_exec = AsyncMock(return_value={"status": "booked"})
    with patch.dict(
        "app.tools.receptionist_tools.TOOL_EXECUTORS",
        {"book_appointment": mock_exec},
    ):
        blocks = await stream._execute_tools(
            tool_uses, session, "CAtest",
            tts_text_queue=None, last_user_text=last_user_text,
        )
    result = json.loads(blocks[0]["content"])
    return mock_exec, result


@pytest.mark.asyncio
async def test_clear_yes_books():
    """Confirm asked + clear yes → book_appointment executes."""
    mock_exec, result = await _run_book(CONFIRM_PROMPT, "yes please")
    mock_exec.assert_awaited_once()
    assert result.get("status") == "booked", result


@pytest.mark.asyncio
async def test_negative_reply_blocks():
    """Confirm asked but caller said no → blocked, executor never runs."""
    mock_exec, result = await _run_book(CONFIRM_PROMPT, "no, change it to Tuesday")
    mock_exec.assert_not_awaited()
    assert result.get("status") in ("affirmation_required", "confirmation_required"), result


@pytest.mark.asyncio
async def test_ambiguous_reply_blocks():
    """Confirm asked but reply is neither yes nor no → blocked (safe)."""
    mock_exec, result = await _run_book(CONFIRM_PROMPT, "um, I'm not really sure")
    mock_exec.assert_not_awaited()
    assert result.get("status") in ("affirmation_required", "confirmation_required"), result


@pytest.mark.asyncio
async def test_confirm_question_not_asked_still_blocks():
    """Existing behaviour preserved: no confirm phrase → block even on 'yes'."""
    mock_exec, result = await _run_book("How much is a session?", "yes")
    mock_exec.assert_not_awaited()
    assert result.get("status") == "confirmation_required", result
