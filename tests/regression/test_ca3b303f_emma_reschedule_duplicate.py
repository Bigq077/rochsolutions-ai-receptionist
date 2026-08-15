# tests/regression/test_ca3b303f_emma_reschedule_duplicate.py
"""
CA3b303f — Emma Clifton, theorem_v3, 2026-08-14.

Caller asked to MOVE Tue 1 Sep 17:00 ~a week later. Susie:
  1. looked up the appointment (reschedule purpose),
  2. called book_appointment instead of reschedule_appointment → Sep 8 booked,
     Sep 1 left in place,
  3. then looped the B-44 "I've got two appointments… is that the one?" script
     ~5× while trying to cancel Sep 1, because every same-id re-lookup reset the
     spoken latches and the identity gate blocked cancel forever.

Two deterministic guards:
  A. same appointment_id re-emit keeps LOOKUP_*_SPOKEN latches
  B. book_appointment blocked while an active reschedule lookup sits on session
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.media_streams.llm_stream import LLMStream, _note_write_result
from app.tools.receptionist_tools import (
    LOOKUP_NAME_SPOKEN_KEY,
    LOOKUP_PURPOSE_KEY,
    LOOKUP_SLOT_SPOKEN_KEY,
    _note_lookup_ambiguity,
)

CONFIRM_PROMPT = (
    "So that's Emma Clifton, Tuesday the 8th of September at five in the "
    "evening — shall I go ahead and book that in?"
)


def test_same_appointment_id_reemit_keeps_spoken_latches():
    """Model re-calls lookup_patient on the same id must not wipe B-42/B-54."""
    session = {
        "_lookup_appointment_id": "1754340001",
        LOOKUP_NAME_SPOKEN_KEY: True,
        LOOKUP_SLOT_SPOKEN_KEY: True,
    }
    _note_lookup_ambiguity(
        session, 2, prev_appointment_id="1754340001",
    )
    assert session[LOOKUP_NAME_SPOKEN_KEY] is True
    assert session[LOOKUP_SLOT_SPOKEN_KEY] is True


def test_different_appointment_id_still_clears_spoken_latches():
    """next=true / a new match must still force a fresh name+slot read-back."""
    session = {
        "_lookup_appointment_id": "1754340002",
        LOOKUP_NAME_SPOKEN_KEY: True,
        LOOKUP_SLOT_SPOKEN_KEY: True,
    }
    _note_lookup_ambiguity(
        session, 2, prev_appointment_id="1754340001",
    )
    assert session[LOOKUP_NAME_SPOKEN_KEY] is False
    assert session[LOOKUP_SLOT_SPOKEN_KEY] is False


def test_fresh_lookup_without_prev_clears_latches():
    """First emit (no previous id) still starts with both latches clear."""
    session = {
        "_lookup_appointment_id": "1754340001",
        LOOKUP_NAME_SPOKEN_KEY: True,
        LOOKUP_SLOT_SPOKEN_KEY: True,
    }
    _note_lookup_ambiguity(session, 2)
    assert session[LOOKUP_NAME_SPOKEN_KEY] is False
    assert session[LOOKUP_SLOT_SPOKEN_KEY] is False


async def _run_book(session: dict, last_user_text: str = "yes please"):
    stream = object.__new__(LLMStream)
    session = {
        "last_bot_prompt": CONFIRM_PROMPT,
        "surname_captured": True,
        "phone_confirmed": True,
        **session,
    }
    tool_uses = [{
        "name": "book_appointment",
        "input": {"name": "Emma Clifton"},
        "id": "t1",
    }]
    messages = [{"role": "user", "content": last_user_text}]
    mock_exec = AsyncMock(return_value={"status": "booked", "success": True})
    with patch.dict(
        "app.tools.receptionist_tools.TOOL_EXECUTORS",
        {"book_appointment": mock_exec},
    ):
        blocks = await stream._execute_tools(
            tool_uses, session, "CA3b303f",
            tts_text_queue=None, messages=messages,
        )
    return mock_exec, json.loads(blocks[0]["content"]), session


@pytest.mark.asyncio
async def test_book_blocked_while_reschedule_lookup_active():
    """Emma path: reschedule lookup live → book_appointment must not create #2."""
    mock_exec, result, _ = await _run_book({
        LOOKUP_PURPOSE_KEY: "reschedule",
        "_lookup_appointment_id": "1754340001",
    })
    mock_exec.assert_not_awaited()
    assert result.get("status") == "reschedule_required", result
    assert "reschedule_appointment" in result.get("message", "")


@pytest.mark.asyncio
async def test_book_allowed_without_reschedule_purpose():
    """Ordinary new booking is untouched."""
    mock_exec, result, _ = await _run_book({})
    mock_exec.assert_awaited_once()
    assert result.get("status") == "booked", result


def test_successful_write_clears_lookup_purpose():
    """After a real cancel/reschedule/book, a later book must not stay blocked."""
    session = {
        LOOKUP_PURPOSE_KEY: "reschedule",
        "_lookup_appointment_id": "1754340001",
    }
    out = _note_write_result(
        session, "reschedule_appointment", {"success": True},
    )
    assert out.get("success") is True
    assert LOOKUP_PURPOSE_KEY not in session
