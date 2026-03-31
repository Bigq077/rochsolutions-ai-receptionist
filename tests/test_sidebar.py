"""
Tests for sidebar detection and knowledge-base answering.

Coverage:
  1. Pricing question mid-flow → detected as sidebar
  2. Giving a name when asked for a name → NOT detected as sidebar
  3. Second sidebar on same state → loop prevention triggers, not answered
  4. KB miss → graceful "I'm not sure" response
  5. detect_sidebar exception → returns False, flow continues
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claude_response(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


# ---------------------------------------------------------------------------
# 1. Pricing question mid-flow → detected as sidebar (YES)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pricing_question_detected_as_sidebar():
    from app.sidebar_handler import detect_sidebar

    with patch("app.sidebar_handler._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_claude_response("YES")
        )
        mock_get.return_value = mock_client

        result = await detect_sidebar("how much does it cost?", "BOOK_NAME")

    assert result is True


# ---------------------------------------------------------------------------
# 2. Giving a name when asked for a name → NOT detected as sidebar (NO)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_name_answer_not_sidebar():
    from app.sidebar_handler import detect_sidebar

    with patch("app.sidebar_handler._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_claude_response("NO")
        )
        mock_get.return_value = mock_client

        result = await detect_sidebar("My name is John Smith", "BOOK_NAME")

    assert result is False


# ---------------------------------------------------------------------------
# 3. Second sidebar on same state → loop prevention triggers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_sidebar_triggers_loop_prevention():
    from app.flows.brain import _handle_sidebar

    session = {
        "state": "BOOK_PHONE",
        "sidebar_count_per_state": {"BOOK_PHONE": 1},  # already answered once
    }

    with patch("app.sidebar_handler._get_client") as mock_get:
        mock_client = MagicMock()
        # detect_sidebar is called — returns True (is a sidebar)
        mock_client.messages.create = AsyncMock(
            return_value=_make_claude_response("YES")
        )
        mock_get.return_value = mock_client

        handled, reply = await _handle_sidebar("do you have evening slots?", session)

    assert handled is True
    assert "let me confirm" in reply.lower()
    # Counter must NOT increment again (loop prevention does not consume a count)
    assert session["sidebar_count_per_state"]["BOOK_PHONE"] == 1
    # answer_sidebar must NOT have been called (no KB lookup)
    assert "I'm not sure" not in reply


# ---------------------------------------------------------------------------
# 4. KB miss → graceful "I'm not sure" response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kb_miss_returns_graceful_response():
    from app.knowledge_base import answer_sidebar

    with patch("app.knowledge_base._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_claude_response(
                "I'm not sure about that, but I can get someone to call you back."
            )
        )
        mock_get.return_value = mock_client

        result = await answer_sidebar(
            "do you offer hydrotherapy?", {"pricing": "£60 initial assessment"}
        )

    assert "not sure" in result.lower()
    assert "call you back" in result.lower()


# ---------------------------------------------------------------------------
# 5. detect_sidebar exception → returns False, flow continues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_sidebar_exception_returns_false():
    from app.sidebar_handler import detect_sidebar

    with patch("app.sidebar_handler._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )
        mock_get.return_value = mock_client

        result = await detect_sidebar("how much does it cost?", "BOOK_NAME")

    # Must return False — never propagate, never break the state machine
    assert result is False


# ---------------------------------------------------------------------------
# 6. First sidebar increments counter and returns answer + return phrase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_sidebar_answered_and_counter_incremented():
    from app.flows.brain import _handle_sidebar

    session = {
        "state": "BOOK_NAME",
        "sidebar_count_per_state": {},
    }

    with patch("app.sidebar_handler._get_client") as mock_detect_client, \
         patch("app.knowledge_base._get_client") as mock_kb_client:

        mock_detect = MagicMock()
        mock_detect.messages.create = AsyncMock(
            return_value=_make_claude_response("YES")
        )
        mock_detect_client.return_value = mock_detect

        mock_kb = MagicMock()
        mock_kb.messages.create = AsyncMock(
            return_value=_make_claude_response(
                "Initial assessment is £60 for 45 minutes."
            )
        )
        mock_kb_client.return_value = mock_kb

        handled, reply = await _handle_sidebar("how much does it cost?", session)

    assert handled is True
    assert "£60" in reply or "coming back" in reply.lower()
    assert session["sidebar_count_per_state"].get("BOOK_NAME") == 1


# ---------------------------------------------------------------------------
# 7. _truncate_to_last_sentence helper
# ---------------------------------------------------------------------------

def test_truncate_to_last_sentence():
    from app.flows.brain import _truncate_to_last_sentence

    text = "Initial assessment is £60. Follow-up is £45. Sports massage is £45."
    # truncate to 50 chars — should end at a sentence boundary
    result = _truncate_to_last_sentence(text, 50)
    assert result.endswith(".")
    assert len(result) <= 50

    # No truncation needed when under limit
    short = "It costs £60."
    assert _truncate_to_last_sentence(short, 100) == short
