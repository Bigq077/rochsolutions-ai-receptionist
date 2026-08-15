"""Job 3c.1 / CAce1457d1 — accepting a slot must not re-list the same offer.

On the live call the caller said "that works for me"; the model re-called
check_availability; Spec I had wiped last_offered_slots on the new turn so
Acuity re-ran (~24s); and when the mid-turn guard DID fire, already_retrieved
told the model to "present the existing slots". Either path forced a second
accept.

Pins:
  1. "that works for me" with last_offered_slots set → slot_offer_still_live,
     Acuity executor not called, message forbids re-listing.
  2. Spec I does not clear the offer while v3_awaiting_slot_selection is live.
  3. "anything later?" still takes the unspoken-batch path (not the accept arm).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.media_streams.config import FORCE_TEXT_NEXT_ITERATION
from app.media_streams.llm_stream import LLMStream
from app.tools.slot_followup import utterance_accepts_offered_slot

_OFFERED = [
    {"start": "2026-08-15T15:00:00+01:00", "end": "2026-08-15T15:30:00+01:00"},
    {"start": "2026-08-15T16:00:00+01:00", "end": "2026-08-15T16:30:00+01:00"},
]
_DAYS = [
    {
        "date": "2026-08-15",
        "day_label": "Friday",
        "slot_times": ["15:00", "16:00", "17:00"],
        "slot_times_spoken": ["three", "four", "five"],
        "slots": [
            {"start": "2026-08-15T15:00:00+01:00", "end": "2026-08-15T15:30:00+01:00"},
            {"start": "2026-08-15T16:00:00+01:00", "end": "2026-08-15T16:30:00+01:00"},
            {"start": "2026-08-15T17:00:00+01:00", "end": "2026-08-15T17:30:00+01:00"},
        ],
    }
]


def test_utterance_accepts_that_works_for_me():
    assert utterance_accepts_offered_slot("that works for me")
    assert utterance_accepts_offered_slot("yeah that works")
    assert utterance_accepts_offered_slot("anything later?") is False
    assert utterance_accepts_offered_slot("a different day") is False


async def _run_check(user_text: str, session: dict):
    stream = object.__new__(LLMStream)
    tool_uses = [{"name": "check_availability", "input": {}, "id": "t1"}]
    messages = [{"role": "user", "content": user_text}]
    mock_exec = AsyncMock(return_value={"status": "ok", "available_days": _DAYS})
    with patch.dict(
        "app.tools.receptionist_tools.TOOL_EXECUTORS",
        {"check_availability": mock_exec},
    ):
        blocks = await stream._execute_tools(
            tool_uses, session, "CAtest_3c1", tts_text_queue=None, messages=messages,
        )
    return mock_exec, json.loads(blocks[0]["content"]), session


@pytest.mark.asyncio
async def test_that_works_for_me_does_not_relist_or_hit_acuity():
    session = {
        "last_offered_slots": list(_OFFERED),
        "available_days": _DAYS,
        "v3_awaiting_slot_selection": True,
    }
    mock_exec, result, session = await _run_check("that works for me", session)
    mock_exec.assert_not_awaited()
    assert result.get("status") == "slot_offer_still_live", result
    msg = (result.get("message") or "").lower()
    assert "do not present the existing slots again" in msg
    assert "present the existing slots to the caller" not in msg
    assert session.get(FORCE_TEXT_NEXT_ITERATION) is True


@pytest.mark.asyncio
async def test_anything_later_still_serves_unspoken_batch():
    session = {
        "last_offered_slots": list(_OFFERED),
        "available_days": _DAYS,
        "v3_awaiting_slot_selection": True,
    }
    mock_exec, result, _ = await _run_check("anything later?", session)
    mock_exec.assert_not_awaited()
    assert result.get("status") != "slot_offer_still_live", result
    payload = json.dumps(result).lower()
    assert "17:00" in payload or "five" in payload or "more" in payload


def test_spec_i_keeps_cache_while_awaiting_slot_selection():
    import re
    src = Path("app/media_streams/connection.py").read_text()
    # FAQ path + main dispatch path.
    assert src.count("slot cache kept — awaiting") == 2
    # Spec I clear is gated off while selection is live (both sites).
    gated = re.findall(
        r"last_offered_slots.*?is not None\s*"
        r"and not self\.session\.get\(\s*\"v3_awaiting_slot_selection\"\s*\)",
        src,
        flags=re.S,
    )
    assert len(gated) >= 2, gated
