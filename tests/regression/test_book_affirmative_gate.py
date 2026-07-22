"""
tests/regression/test_book_affirmative_gate.py
----------------------------------------------
FM-01 — book_appointment must require an explicit, unambiguous caller YES.

Recovered from origin/main's deleted test_book_affirmative_gate and ADAPTED to
the CURRENT _execute_tools signature: the caller's reply is threaded via
`messages` (the real prod call site passes it — llm_stream.py:1062), NOT via
main's old `last_user_text` parameter.

The gap this guards (confirmed live 2026-07-21 on latency-eval, jv-v1-onboarding
and vitaledge-onboarding — `is_yes` absent from llm_stream.py on all three): the
book gate at llm_stream.py:1822 checks only that the confirmation QUESTION was
asked ("shall i go ahead"/"book that in"), then trusts the model to have waited
for a yes. A negative or ambiguous reply — or an affirmative paired with a
correction — could still book.

The comment's mid-readback barge-in ("the barge contains 'yes'") is a separate,
already-covered case: during the readback the question has not yet been asked, so
last_bot_prompt lacks the confirmation phrase and the existing gate blocks it
(test_barge_during_readback_blocks pins that).

Bias: a false block just re-asks (safe); a false allow is a wrong booking.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.media_streams.llm_stream import LLMStream

CONFIRM_PROMPT = (
    "So that's James, Wednesday the 8th of July at nine in the morning "
    "— shall I go ahead and book that in?"
)
# A pure readback: the summary is spoken but the confirmation QUESTION has not
# been asked yet (no "shall i go ahead" / "book that in").
READBACK_ONLY = "So that's James, Wednesday the 8th of July at nine in the morning."


async def _run_book(last_bot_prompt: str, last_user_text: str):
    """Drive _execute_tools with one book_appointment tool_use, threading the
    caller's reply through `messages` (current signature).

    surname_captured / phone_confirmed are pre-set so the surname (Step 7) and
    phone (Step 8) backstops pass and the confirmation gate is the one under
    test. Returns (mock_executor, parsed_result_dict).
    """
    stream = object.__new__(LLMStream)
    session = {
        "last_bot_prompt": last_bot_prompt,
        "surname_captured": True,
        "phone_confirmed": True,
    }
    tool_uses = [{"name": "book_appointment", "input": {"name": "James Rock"}, "id": "t1"}]
    messages = [{"role": "user", "content": last_user_text}]
    mock_exec = AsyncMock(return_value={"status": "booked"})
    with patch.dict(
        "app.tools.receptionist_tools.TOOL_EXECUTORS",
        {"book_appointment": mock_exec},
    ):
        blocks = await stream._execute_tools(
            tool_uses, session, "CAtest",
            tts_text_queue=None, messages=messages,
        )
    return mock_exec, json.loads(blocks[0]["content"])


_BLOCKED = ("affirmation_required", "confirmation_required")


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
    assert result.get("status") in _BLOCKED, result


@pytest.mark.asyncio
async def test_ambiguous_reply_blocks():
    """Confirm asked but reply is neither a clear yes nor no → blocked (safe)."""
    mock_exec, result = await _run_book(CONFIRM_PROMPT, "um, I'm not really sure")
    mock_exec.assert_not_awaited()
    assert result.get("status") in _BLOCKED, result


@pytest.mark.asyncio
async def test_yes_paired_with_correction_blocks():
    """An affirmative paired with a correction ("yes… actually no, Tuesday") must
    not book — the negation wins."""
    mock_exec, result = await _run_book(CONFIRM_PROMPT, "yes, actually no, make it Tuesday")
    mock_exec.assert_not_awaited()
    assert result.get("status") in _BLOCKED, result


@pytest.mark.asyncio
async def test_barge_during_readback_blocks():
    """The comment's barge case: a "yes" barged in mid-readback, before the
    confirmation question is asked, must not book (existing question-asked gate)."""
    mock_exec, result = await _run_book(READBACK_ONLY, "yes")
    mock_exec.assert_not_awaited()
    assert result.get("status") == "confirmation_required", result


@pytest.mark.asyncio
async def test_confirm_question_not_asked_still_blocks():
    """No confirmation phrase in last_bot_prompt → block even on a bare 'yes'."""
    mock_exec, result = await _run_book("How much is a session?", "yes")
    mock_exec.assert_not_awaited()
    assert result.get("status") == "confirmation_required", result
