# tests/test_mistake_recovery.py
"""
Tests for mistake recovery: retry counting, acknowledged re-asks, graceful
exit on third failure, and STATE_READBACK confirmation / correction.

Coverage:
  1. First failure on ask_name → state-specific first_retry phrase
  2. Second failure on ask_name → generic second_retry phrase
  3. Third failure → graceful_exit flag set and flow marked complete
  4. STATE_READBACK confirmation → flow_step set to CONFIRM_BOOKING index
  5. STATE_READBACK correction → slot updated, correction phrase spoken
  6. slot_retry_counts is independent per slot (ask_name vs ask_phone)
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.media_streams.flow import (
    BOOKING_FLOW,
    FlowEngine,
    _CONFIRM_BOOKING_INDEX,
    _phrase_key_for_step,
)
from app.media_streams.session import DEFAULT_MS_SESSION
from app.phrases import RETRY_PHRASES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_session(**overrides) -> Dict[str, Any]:
    """Return a clean session with optional field overrides."""
    s = copy.deepcopy(DEFAULT_MS_SESSION)
    s.update(overrides)
    return s


class _FakeTTSQueue:
    """Minimal asyncio.Queue stand-in that records put() calls."""

    def __init__(self):
        self.items: List[str] = []

    async def put(self, text: str) -> None:
        self.items.append(text)

    def last(self) -> str:
        return self.items[-1] if self.items else ""


async def _noop_llm(instruction: str, allow_tools: bool = True) -> str:
    return ""


def _make_engine(session: Dict[str, Any], tts: _FakeTTSQueue | None = None) -> FlowEngine:
    if tts is None:
        tts = _FakeTTSQueue()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = BOOKING_FLOW
    engine._intent_detected = True
    return engine


# COLLECT_NAME step (step 12 in BOOKING_FLOW, answer_field="full_name")
_COLLECT_NAME_STEP = next(s for s in BOOKING_FLOW if s["state"] == "COLLECT_NAME")
# COLLECT_PHONE step (step 14 in BOOKING_FLOW, answer_field="phone_number")
_COLLECT_PHONE_STEP = next(s for s in BOOKING_FLOW if s["state"] == "COLLECT_PHONE")

# _extract("name") accepts 1-5 words.  Use a 6-word utterance to force None return.
_NAME_FAIL = "um um um um um um"   # 6 words → fails 1-5 check → extract returns None
# _extract("phone") requires ≥10 digits.  "hmm" has none.
_PHONE_FAIL = "hmm"


# ---------------------------------------------------------------------------
# 1. First failure on ask_name → state-specific phrase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_retry_ask_name_uses_specific_phrase():
    """First extraction failure on full_name → ask_name first_retry phrase."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        flow_step=BOOKING_FLOW.index(_COLLECT_NAME_STEP),
        last_question="Who am I booking in today?",
    )
    engine = _make_engine(session, tts)

    # 6 words → _extract("name") returns None (accepts only 1-5 words)
    await engine.handle_transcript(_NAME_FAIL)

    assert session["slot_retry_counts"].get("ask_name") == 1
    spoken = tts.last()
    assert spoken == RETRY_PHRASES["first_retry"]["ask_name"]


# ---------------------------------------------------------------------------
# 2. Second failure on ask_name → generic second_retry phrase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_retry_ask_name_uses_generic_phrase():
    """Second extraction failure on full_name → second_retry default phrase."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        flow_step=BOOKING_FLOW.index(_COLLECT_NAME_STEP),
        last_question="Who am I booking in today?",
        slot_retry_counts={"ask_name": 1},  # already failed once
    )
    engine = _make_engine(session, tts)

    await engine.handle_transcript(_NAME_FAIL)

    assert session["slot_retry_counts"]["ask_name"] == 2
    spoken = tts.last()
    assert spoken == RETRY_PHRASES["second_retry"]["default"]


# ---------------------------------------------------------------------------
# 3. Third failure → graceful_exit flag set and flow marked complete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_third_retry_triggers_graceful_exit():
    """Third extraction failure → graceful_exit=True, request_transfer=True, flow complete."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        flow_step=BOOKING_FLOW.index(_COLLECT_NAME_STEP),
        last_question="Who am I booking in today?",
        slot_retry_counts={"ask_name": 2},  # already failed twice
    )
    engine = _make_engine(session, tts)

    await engine.handle_transcript(_NAME_FAIL)

    assert session["slot_retry_counts"]["ask_name"] == 3
    assert session["graceful_exit"] is True
    assert session["request_transfer"] is True
    assert session["flow_step"] >= len(BOOKING_FLOW)
    assert "call you back" in tts.last()


# ---------------------------------------------------------------------------
# 4. STATE_READBACK confirmation → flow_step set to CONFIRM_BOOKING index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_readback_confirmation_advances_to_confirm_booking():
    """When Claude says confirmed=True, flow_step → _CONFIRM_BOOKING_INDEX."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        readback_pending=True,
        readback_delivered=True,
        full_name="James Smith",
        selected_slot="Mon 28 Apr at 09:00",
        selected_slot_speech="Monday the 28th of April at 9 o'clock in the morning",
        reason="back pain",
        phone_number="07700900000",
    )
    engine = _make_engine(session, tts)

    classification = {"confirmed": True, "corrected_slot": None, "new_value": None}
    with patch.object(engine, "_classify_readback_response", new=AsyncMock(return_value=classification)):
        await engine.handle_transcript("Yes that's all correct")

    assert session["readback_pending"] is False
    # ask_current_question runs CONFIRM_BOOKING which auto-completes the flow
    assert session.get("booking_confirmed") is True


# ---------------------------------------------------------------------------
# 5. STATE_READBACK correction → slot updated, correction phrase spoken
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_readback_correction_updates_slot_and_speaks_correction():
    """When Claude returns a correction, the slot is updated and correction phrase spoken."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        readback_pending=True,
        readback_delivered=True,
        full_name="Jamie Smith",
        selected_slot="Mon 28 Apr at 09:00",
        reason="back pain",
        phone_number="07700900000",
    )
    engine = _make_engine(session, tts)

    classification = {
        "confirmed": False,
        "corrected_slot": "full_name",
        "new_value": "James Smith",
    }
    with patch.object(engine, "_classify_readback_response", new=AsyncMock(return_value=classification)):
        await engine.handle_transcript("Actually it's James not Jamie")

    assert session["full_name"] == "James Smith"
    assert session["readback_correction_turn"] is True
    assert session["readback_pending"] is True   # still waiting for second-turn confirm
    spoken = tts.last()
    assert "James Smith" in spoken
    assert "shall I go ahead" in spoken.lower() or "book that in" in spoken.lower()


# ---------------------------------------------------------------------------
# 6. slot_retry_counts is independent per slot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slot_retry_counts_independent_per_slot():
    """Failures on ask_name do not affect ask_phone counter and vice versa."""
    tts = _FakeTTSQueue()
    name_step_idx  = BOOKING_FLOW.index(_COLLECT_NAME_STEP)
    phone_step_idx = BOOKING_FLOW.index(_COLLECT_PHONE_STEP)

    # --- fail ask_name twice (6-word utterance fails _extract("name") 1-5 check) ---
    session = _fresh_session(
        flow_step=name_step_idx,
        last_question="Who am I booking in today?",
    )
    engine = _make_engine(session, tts)
    await engine.handle_transcript(_NAME_FAIL)   # retry 1
    await engine.handle_transcript(_NAME_FAIL)   # retry 2

    assert session["slot_retry_counts"].get("ask_name") == 2
    assert session["slot_retry_counts"].get("ask_phone", 0) == 0

    # --- now fail ask_phone once (no digits → _extract("phone") returns None) ---
    session["flow_step"]     = phone_step_idx
    session["last_question"] = "And the best number to reach you on?"
    await engine.handle_transcript(_PHONE_FAIL)

    assert session["slot_retry_counts"].get("ask_name") == 2  # unchanged
    assert session["slot_retry_counts"].get("ask_phone") == 1  # independent counter


# ---------------------------------------------------------------------------
# Additional: _phrase_key_for_step maps correctly
# ---------------------------------------------------------------------------

def test_phrase_key_for_step_maps_full_name():
    step = {"answer_field": "full_name", "state": "COLLECT_NAME"}
    assert _phrase_key_for_step(step) == "ask_name"


def test_phrase_key_for_step_maps_phone_number():
    step = {"answer_field": "phone_number", "state": "COLLECT_PHONE"}
    assert _phrase_key_for_step(step) == "ask_phone"


def test_phrase_key_for_step_maps_reason():
    step = {"answer_field": "reason", "state": "COLLECT_REASON"}
    assert _phrase_key_for_step(step) == "ask_reason"


def test_phrase_key_for_step_unknown_falls_back_to_default():
    step = {"answer_field": "assessment_confirmed", "state": "CONFIRM_ASSESSMENT"}
    assert _phrase_key_for_step(step) == "default"


# ---------------------------------------------------------------------------
# Additional: STATE_READBACK classification failure → treat as confirmed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_readback_classification_failure_treats_as_confirmed():
    """If Claude call raises, treat as confirmed and advance to CONFIRM_BOOKING."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        readback_pending=True,
        readback_delivered=True,
        full_name="James Smith",
        reason="back pain",
    )
    engine = _make_engine(session, tts)

    with patch.object(
        engine, "_classify_readback_response",
        new=AsyncMock(side_effect=RuntimeError("network error"))
    ):
        await engine.handle_transcript("yeah that's fine")

    assert session["readback_pending"] is False
    # ask_current_question runs CONFIRM_BOOKING which auto-completes the flow
    assert session.get("booking_confirmed") is True


# ---------------------------------------------------------------------------
# Additional: second turn after correction is treated as confirmed regardless
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_readback_second_turn_after_correction_is_confirmed():
    """readback_correction_turn=True: any response advances flow regardless of content."""
    tts = _FakeTTSQueue()
    session = _fresh_session(
        readback_pending=True,
        readback_delivered=True,
        readback_correction_turn=True,
    )
    engine = _make_engine(session, tts)

    # Even an ambiguous response should advance
    await engine.handle_transcript("actually wait no hmm")

    assert session["readback_pending"] is False
    assert session["readback_correction_turn"] is False
    # ask_current_question runs CONFIRM_BOOKING which auto-completes the flow
    assert session.get("booking_confirmed") is True
