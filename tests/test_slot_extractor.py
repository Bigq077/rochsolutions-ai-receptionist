"""
Unit tests for app/slot_extractor.py

Coverage:
  1. Full sentence with reason + day pre-fills correctly
  2. Ambiguous utterance returns empty dict
  3. Utterance with only a greeting returns empty dict
  4. Exception in Claude call returns empty dict safely
  5. Pre-filled slot has source="pre-filled" in session (via brain._prefill_slots_from_utterance)
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claude_response(payload: dict) -> MagicMock:
    """Build a mock Anthropic response whose .content[0].text is payload as JSON."""
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(payload))]
    return msg


# ---------------------------------------------------------------------------
# 1. Full sentence with reason + day pre-fills correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_sentence_extracts_reason_and_day():
    """
    "I've got terrible shoulder pain and need to see someone this week" should
    extract reason_for_visit and preferred_day.
    """
    from app.slot_extractor import extract_slots_from_utterance

    mock_response = _make_claude_response({
        "reason_for_visit": "shoulder pain",
        "preferred_day":    "this week",
        "preferred_time":   None,
        "patient_name":     None,
        "phone_number":     None,
    })

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        result = await extract_slots_from_utterance(
            "I've got terrible shoulder pain and need to see someone this week",
            {},
        )

    assert result.get("reason_for_visit") == "shoulder pain"
    assert result.get("preferred_day") == "this week"
    assert "preferred_time" not in result
    assert "patient_name" not in result
    assert "phone_number" not in result


# ---------------------------------------------------------------------------
# 2. Ambiguous utterance returns empty dict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ambiguous_utterance_returns_empty():
    """
    "I think maybe some time next month perhaps" is ambiguous; all slots null.
    """
    from app.slot_extractor import extract_slots_from_utterance

    mock_response = _make_claude_response({
        "reason_for_visit": None,
        "preferred_day":    None,
        "preferred_time":   None,
        "patient_name":     None,
        "phone_number":     None,
    })

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        result = await extract_slots_from_utterance(
            "I think maybe some time next month perhaps", {}
        )

    assert result == {}


# ---------------------------------------------------------------------------
# 3. Utterance with only a greeting returns empty dict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_greeting_only_returns_empty():
    """
    "Hi there, good morning!" contains no booking slots.
    """
    from app.slot_extractor import extract_slots_from_utterance

    mock_response = _make_claude_response({
        "reason_for_visit": None,
        "preferred_day":    None,
        "preferred_time":   None,
        "patient_name":     None,
        "phone_number":     None,
    })

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        result = await extract_slots_from_utterance("Hi there, good morning!", {})

    assert result == {}


# ---------------------------------------------------------------------------
# 4. Exception in Claude call returns empty dict safely
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exception_returns_empty_dict():
    """
    If the Anthropic API raises, extract_slots_from_utterance must return {}
    and not re-raise — the state machine must continue unaffected.
    """
    from app.slot_extractor import extract_slots_from_utterance

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=RuntimeError("network timeout")
        )
        mock_get.return_value = mock_client

        result = await extract_slots_from_utterance("bad knee, Monday please", {})

    assert result == {}


# ---------------------------------------------------------------------------
# 5. Pre-filled slot has source="pre-filled" in session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prefilled_slot_has_source_in_session():
    """
    After brain._prefill_slots_from_utterance merges extractions into the
    session, each merged slot must carry source="pre-filled".
    """
    from app.flows.brain import _prefill_slots_from_utterance

    mock_response = _make_claude_response({
        "reason_for_visit": "bad knee",
        "preferred_day":    "Monday",
        "preferred_time":   None,
        "patient_name":     None,
        "phone_number":     None,
    })

    session = {"collected": {}}

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        updated = await _prefill_slots_from_utterance(
            "my knee is giving me trouble, can I come Monday?", session
        )

    collected = updated["collected"]
    assert collected.get("reason") == "bad knee"
    assert collected.get("reason_source") == "pre-filled"
    assert collected.get("time_pref") == "Monday"
    assert collected.get("time_pref_source") == "pre-filled"


# ---------------------------------------------------------------------------
# 6. Already-filled slot is not overwritten
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_slot_not_overwritten():
    """
    If collected["reason"] is already set, a new extraction must not overwrite it.
    """
    from app.flows.brain import _prefill_slots_from_utterance

    mock_response = _make_claude_response({
        "reason_for_visit": "back pain",
        "preferred_day":    None,
        "preferred_time":   None,
        "patient_name":     None,
        "phone_number":     None,
    })

    session = {"collected": {"reason": "shoulder injury", "reason_source": "spoken"}}

    with patch("app.slot_extractor._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        updated = await _prefill_slots_from_utterance(
            "actually I have back pain", session
        )

    assert updated["collected"]["reason"] == "shoulder injury"
    assert updated["collected"]["reason_source"] == "spoken"
