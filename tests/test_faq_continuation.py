"""
tests/test_faq_continuation.py
--------------------------------
Tests for FAQ follow-up, transition phrase, partial topic signal, and repair logic.
Covers bug families 1–6 from the FAQ conversation breakdown analysis.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.flows.triage_legacy import (
    FAQ_DETOUR,
    TRIAGE,
    _is_faq_transition,
    _partial_faq_signal,
    triage_turn,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal session in FAQ_DETOUR (mid-booking)
# ---------------------------------------------------------------------------

def _faq_detour_session(return_state: str = "BOOK_NAME") -> dict:
    return {
        "state": FAQ_DETOUR,
        "return_state": return_state,
        "faq_topic": "shockwave",
        "faq_last_topic": "FAQ_SERVICE_EXPLAIN",
        "collected": {},
        "conversation_history": [],
        "clinic_id": "demo",
    }


def _triage_session(faq_last_topic: str | None = None) -> dict:
    s = {
        "state": TRIAGE,
        "collected": {},
        "conversation_history": [],
        "clinic_id": "demo",
    }
    if faq_last_topic:
        s["faq_last_topic"] = faq_last_topic
    return s


# ---------------------------------------------------------------------------
# 1.  _is_faq_transition — unit tests
# ---------------------------------------------------------------------------

class TestIsFaqTransition:
    def test_next_question(self):
        assert _is_faq_transition("that kind of brings on my next question") is True

    def test_brings_me_to(self):
        assert _is_faq_transition("that brings me to my next question") is True

    def test_another_question(self):
        assert _is_faq_transition("I have another question") is True

    def test_one_more_question(self):
        assert _is_faq_transition("just one more question") is True

    def test_one_more_thing(self):
        assert _is_faq_transition("one more thing actually") is True

    def test_also_wanted_to_ask(self):
        assert _is_faq_transition("I also wanted to ask something") is True

    def test_real_question_not_transition(self):
        assert _is_faq_transition("what are your prices?") is False

    def test_empty_not_transition(self):
        assert _is_faq_transition("") is False

    def test_continue_not_transition(self):
        assert _is_faq_transition("continue") is False


# ---------------------------------------------------------------------------
# 2.  _partial_faq_signal — unit tests
# ---------------------------------------------------------------------------

class TestPartialFaqSignal:
    def test_prices_keyword(self):
        assert _partial_faq_signal("and i want to know if your clinic") is None

    def test_prices_partial(self):
        assert _partial_faq_signal("how much does it") == "FAQ_PRICES"

    def test_hours_partial(self):
        assert _partial_faq_signal("when are you open") == "FAQ_HOURS"

    def test_shockwave_service(self):
        assert _partial_faq_signal("tell me about shockwave") == "FAQ_SERVICE_EXPLAIN"

    def test_physio_partial(self):
        assert _partial_faq_signal("about your physio") == "FAQ_SERVICES"

    def test_insurance_partial(self):
        assert _partial_faq_signal("do you work with insurance") == "FAQ_INSURANCE"

    def test_no_signal(self):
        assert _partial_faq_signal("um okay") is None

    def test_empty(self):
        assert _partial_faq_signal("") is None


# ---------------------------------------------------------------------------
# 3.  Transition phrase in FAQ_DETOUR → continuation prompt, no repair
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_phrase_in_faq_detour_returns_continuation():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, _ = await triage_turn("that brings me to my next question", session)
    assert "what would you like to know" in reply.lower()


@pytest.mark.asyncio
async def test_transition_phrase_variant_in_faq_detour():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, _ = await triage_turn("actually that kind of brings on my next question", session)
    assert "what would you like to know" in reply.lower()


# ---------------------------------------------------------------------------
# 4.  Existing FAQ intents answered in FAQ_DETOUR (not just SERVICE_EXPLAIN)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_faq_prices_answered_in_detour():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, updated = await triage_turn("how much do sessions cost?", session)
    assert "85" in reply or "65" in reply  # price figures
    assert updated.get("faq_last_topic") == "FAQ_PRICES"
    assert updated.get(f"fallback_count_{FAQ_DETOUR}", 0) == 0


@pytest.mark.asyncio
async def test_faq_hours_answered_in_detour():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, updated = await triage_turn("what are your opening hours?", session)
    assert updated.get("faq_last_topic") == "FAQ_HOURS"


@pytest.mark.asyncio
async def test_faq_location_answered_in_detour():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, updated = await triage_turn("where are you located?", session)
    assert updated.get("faq_last_topic") == "FAQ_LOCATION"


# ---------------------------------------------------------------------------
# 5.  Partial topic signal → targeted clarification, not generic repair
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_service_signal_asks_clarification():
    """Fragmented 'and i want to know if your clinic' has no topic → _partial_faq_signal=None → fallback.
    But 'about your shockwave' DOES have service signal → targeted clarification."""
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, _ = await triage_turn("about your shockwave", session)
    # Should ask which service, not generic repair
    assert "shockwave" in reply.lower() or "service" in reply.lower()


@pytest.mark.asyncio
async def test_partial_price_signal_answered():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, updated = await triage_turn("how much does it", session)
    assert "85" in reply or "fee" in reply.lower() or updated.get("faq_last_topic") == "FAQ_PRICES"


# ---------------------------------------------------------------------------
# 6.  Fallback counter resets on successful FAQ answer in FAQ_DETOUR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_counter_resets_on_faq_answer():
    session = _faq_detour_session()
    session[f"fallback_count_{FAQ_DETOUR}"] = 2  # simulate two prior fallbacks
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        _, updated = await triage_turn("what are your prices?", session)
    assert updated.get(f"fallback_count_{FAQ_DETOUR}", 0) == 0


# ---------------------------------------------------------------------------
# 7.  Multi-question FAQ continuity in FAQ_DETOUR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_question_faq_continuity():
    """
    Simulate 3 consecutive questions in FAQ_DETOUR without triggering generic repair.
    Turn 1: real FAQ question (prices)
    Turn 2: transition phrase
    Turn 3: another real question (hours)
    """
    session = _faq_detour_session()
    clinic_mock = {"name": "Demo", "locations": []}

    with patch("app.flows.triage_legacy.get_clinic", return_value=clinic_mock):
        # Turn 1 — direct FAQ question
        r1, session = await triage_turn("how much does it cost?", session)
        assert "85" in r1 or "65" in r1

        # Turn 2 — transition phrase
        r2, session = await triage_turn("that brings me to my next question", session)
        assert "what would you like to know" in r2.lower()

        # Turn 3 — another direct FAQ question
        r3, session = await triage_turn("what are your opening hours?", session)
        assert session.get("faq_last_topic") == "FAQ_HOURS"
        # State must still be FAQ_DETOUR after all 3 turns
        assert session["state"] == FAQ_DETOUR


# ---------------------------------------------------------------------------
# 8.  faq_last_topic set in TRIAGE after FAQ answers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triage_faq_service_explain_sets_last_topic():
    session = _triage_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        _, updated = await triage_turn("what is shockwave therapy?", session)
    assert updated.get("faq_last_topic") == "FAQ_SERVICE_EXPLAIN"


@pytest.mark.asyncio
async def test_triage_faq_hours_sets_last_topic():
    session = _triage_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        _, updated = await triage_turn("what are your opening hours?", session)
    assert updated.get("faq_last_topic") == "FAQ_HOURS"


# ---------------------------------------------------------------------------
# 9.  Transition phrase in TRIAGE after a previous FAQ answer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triage_transition_after_faq():
    session = _triage_session(faq_last_topic="FAQ_HOURS")
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        reply, _ = await triage_turn("that brings me to my next question", session)
    assert "what would you like to know" in reply.lower()


# ---------------------------------------------------------------------------
# 10.  Generic repair still works for truly empty / unusable input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_truly_empty_input_triggers_mishear():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo", "locations": []}):
        # Empty string hits the top-level empty-input guard before FAQ_DETOUR
        reply, _ = await triage_turn("", session)
    # Should return some kind of repair/mishear, not crash
    assert isinstance(reply, str) and len(reply) > 0


# ---------------------------------------------------------------------------
# 11.  Insurance inline in FAQ_DETOUR (no state transition)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insurance_answered_inline_in_detour():
    session = _faq_detour_session()
    with patch("app.flows.triage_legacy.get_clinic", return_value={"name": "Demo Clinic", "locations": []}):
        reply, updated = await triage_turn("do you accept insurance?", session)
    # Must stay in FAQ_DETOUR — no transition to INS_COLLECT_INSURER
    assert updated["state"] == FAQ_DETOUR
    assert updated.get("faq_last_topic") == "FAQ_INSURANCE"
    # Answer should mention self-pay or insurer
    assert "insur" in reply.lower() or "self-pay" in reply.lower() or "claim" in reply.lower()
