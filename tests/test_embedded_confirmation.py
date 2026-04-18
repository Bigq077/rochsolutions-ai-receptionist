"""
tests/test_embedded_confirmation.py
====================================
Tests for the three UX upgrades:

  PART 1 — LOCATION: embedded confirmation inside next question
    Booking  → location in COLLECT_REASON question
    Reschedule → location in COLLECT_NAME_RESCHEDULE question
    Cancel     → same state, "cancel" intent variant
    Correction → mid-flow "actually Redditch" → silent overwrite

  PART 2 — NAME: skip fn_confirm for strong tokens, "Thanks {name} —" prefix
    Strong single token → no fn_confirm → straight to sn_normal
    Strong 2-token → no fn_confirm → straight to sn_confirm
    Full round-trip → accept carries prefix to phone question

  PART 3 — PHONE: exact new wording for CONFIRM_PHONE
    Exact sentence emitted when Twilio caller-ID available
    No-Twilio path: skips to COLLECT_PHONE (unchanged)
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.media_streams.flow import (
    BOOKING_FLOW,
    RESCHEDULE_FLOW,
    FlowEngine,
    _CONFIRM_PHONE_INDEX,
)
from app.media_streams.name_collector import NameCollector
from app.media_streams.session import DEFAULT_MS_SESSION


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_session(**overrides) -> Dict[str, Any]:
    s = copy.deepcopy(DEFAULT_MS_SESSION)
    s.update(overrides)
    return s


class _FakeTTSQueue:
    """asyncio.Queue stand-in that supports empty() and get_nowait()."""

    def __init__(self):
        self.items: List[str] = []

    async def put(self, text: str) -> None:
        self.items.append(text)

    def empty(self) -> bool:
        return len(self.items) == 0

    def get_nowait(self) -> str:
        if self.items:
            return self.items.pop()
        raise Exception("empty")

    def last(self) -> str:
        return self.items[-1] if self.items else ""

    def all_text(self) -> str:
        return " ".join(self.items)


async def _noop_llm(instruction: str, allow_tools: bool = True) -> str:
    return ""


def _make_booking_engine(
    session: Dict[str, Any],
    tts: _FakeTTSQueue | None = None,
) -> FlowEngine:
    if tts is None:
        tts = _FakeTTSQueue()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = BOOKING_FLOW
    engine._intent_detected = True
    return engine


def _make_reschedule_engine(
    session: Dict[str, Any],
    tts: _FakeTTSQueue | None = None,
    intent: str = "reschedule",
) -> FlowEngine:
    if tts is None:
        tts = _FakeTTSQueue()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = RESCHEDULE_FLOW
    engine._intent_detected = True
    session["intent"] = intent
    return engine


_COLLECT_REASON_IDX   = next(i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "COLLECT_REASON")
_COLLECT_NAME_IDX     = next(i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "COLLECT_NAME")
_CONFIRM_PHONE_BK_IDX = next(i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "CONFIRM_PHONE")
_CNR_IDX              = next(i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "COLLECT_NAME_RESCHEDULE")


# ════════════════════════════════════════════════════════════════════════════
# PART 1 — LOCATION EMBEDDING
# ════════════════════════════════════════════════════════════════════════════

class TestLocationEmbedBooking:
    """Booking flow: location embedded in COLLECT_REASON question."""

    @pytest.mark.asyncio
    async def test_alcester_in_collect_reason(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="alcester",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        assert "Alcester" in tts.last()
        assert "What brings you in" in tts.last()

    @pytest.mark.asyncio
    async def test_redditch_in_collect_reason(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="redditch",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        assert "Redditch" in tts.last()
        assert "What brings you in" in tts.last()

    @pytest.mark.asyncio
    async def test_no_location_falls_back_to_plain_question(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        assert "What brings you in" in q
        # No clinic name injected
        assert "Alcester" not in q
        assert "Redditch" not in q

    @pytest.mark.asyncio
    async def test_noisy_location_still_embedded(self):
        """Even a low-confidence resolve (soft alias) embeds the location."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="alcester",   # already bound by resolver
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        assert "Alcester" in tts.last()


class TestLocationEmbedReschedule:
    """Reschedule/cancel flow: location embedded in COLLECT_NAME_RESCHEDULE question."""

    @pytest.mark.asyncio
    async def test_reschedule_alcester(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CNR_IDX,
            selected_location="alcester",
            intent="reschedule",
        )
        engine = _make_reschedule_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        assert "Alcester" in q
        assert "What's your first name" in q

    @pytest.mark.asyncio
    async def test_reschedule_redditch(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CNR_IDX,
            selected_location="redditch",
            intent="reschedule",
        )
        engine = _make_reschedule_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        assert "Redditch" in q
        assert "What's your first name" in q

    @pytest.mark.asyncio
    async def test_cancel_intent_different_phrasing(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CNR_IDX,
            selected_location="alcester",
            intent="cancel",
        )
        engine = _make_reschedule_engine(session, tts, intent="cancel")
        await engine.ask_current_question()
        q = tts.last()
        assert "Alcester" in q
        assert "cancel" in q.lower()
        assert "What's your first name" in q

    @pytest.mark.asyncio
    async def test_reschedule_no_location_falls_back(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CNR_IDX,
            intent="reschedule",
        )
        engine = _make_reschedule_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        assert "What's your first name" in q
        assert "Alcester" not in q
        assert "Redditch" not in q


class TestLocationCorrection:
    """Mid-flow location correction: silent overwrite, no confirmation step."""

    @pytest.mark.asyncio
    async def test_redditch_corrects_alcester(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="alcester",
            new_or_returning="new",
            reason="back pain",
        )
        engine = _make_booking_engine(session, tts)

        # Caller says Redditch mid-flow
        with patch.object(engine, "_detect_intent", return_value="other"):
            await engine.handle_transcript("actually Redditch")

        assert session["selected_location"] == "redditch"

    @pytest.mark.asyncio
    async def test_correction_does_not_reset_flow(self):
        """Flow step must stay the same — no reset."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="alcester",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        original_step = session["flow_step"]

        with patch.object(engine, "_detect_intent", return_value="other"):
            await engine.handle_transcript("no redditch")

        # Location overwritten
        assert session["selected_location"] == "redditch"
        # Flow step unchanged (no reset)
        assert session["flow_step"] == original_step

    @pytest.mark.asyncio
    async def test_weak_signal_does_not_correct(self):
        """Prefix-fallback-only (low-confidence) should NOT silently overwrite."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="alcester",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)

        # "al" alone would be a prefix_fallback — not a hard/soft alias
        # resolver returns prefix_fallback which we exclude from silent correction
        with patch.object(engine, "_detect_intent", return_value="other"):
            await engine.handle_transcript("actually")  # blocked by PREFIX_FALLBACK_BLOCKLIST

        # alcester unchanged
        assert session["selected_location"] == "alcester"


# ════════════════════════════════════════════════════════════════════════════
# PART 2 — NAME: skip fn_confirm for strong tokens
# ════════════════════════════════════════════════════════════════════════════

class TestNameSkipConfirm:
    """NameCollector: strong token goes straight to sn_normal, not fn_confirm."""

    def test_strong_single_token_skips_fn_confirm(self):
        """'quentin' (7 chars) → no 'is that right?', asks for surname directly."""
        s = {}
        action, payload = NameCollector(s).handle("quentin", "Quentin")
        assert action == "ask"
        # Must ask for surname, NOT "I've got Quentin — is that right?"
        assert "surname" in payload.lower() or "last name" in payload.lower()
        assert "is that right" not in payload.lower()

    def test_strong_single_token_stores_first_name(self):
        s = {}
        NameCollector(s).handle("quentin", "Quentin")
        assert s["_nc"]["first_name"] == "Quentin"

    def test_strong_single_token_substate_sn_normal(self):
        from app.media_streams.name_collector import NC_SN_NORMAL
        s = {}
        NameCollector(s).handle("quentin", "Quentin")
        assert s["_nc"]["substate"] == NC_SN_NORMAL

    def test_strong_2_token_skips_fn_confirm_enters_sn_confirm(self):
        """'quentin roch' → no fn_confirm → straight to sn_confirm for surname."""
        from app.media_streams.name_collector import NC_SN_CONFIRM
        s = {}
        action, payload = NameCollector(s).handle("quentin roch", "Quentin Roch")
        assert action == "ask"
        # Should be in sn_confirm, not fn_confirm
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert "Roch" in payload

    def test_weak_4_char_token_still_repairs(self):
        """'john' (4 chars) → still goes to repair (< _FN_STRONG_LEN = 5)."""
        s = {}
        action, payload = NameCollector(s).handle("john", "John")
        assert action in ("ask", "repair")
        assert "is that right" not in payload.lower()

    def test_fn_direct_stored_flag_set(self):
        s = {}
        NameCollector(s).handle("quentin", "Quentin")
        assert s["_nc"].get("_fn_direct_stored") is True

    def test_prefix_name_set_on_full_accept(self):
        """After full accept (surname confirmed), session has _nc_fn_name_prefix."""
        s = {}
        # fn_normal: store "Quentin" directly
        NameCollector(s).handle("quentin", "Quentin")
        # sn_normal: collect "Roch"
        NameCollector(s).handle("roch", "Roch")
        # sn_confirm YES: accept full name
        NameCollector(s).handle("yes", "Yes")
        assert s.get("_nc_fn_name_prefix") == "Quentin"
        assert s.get("full_name") == "Quentin Roch"

    def test_prefix_not_set_when_fn_confirm_used(self):
        """When fn_confirm IS used (negation path), no prefix signal is set."""
        s = {}
        # Negation path still goes through fn_confirm
        NameCollector(s).handle("not sarah it's quentin", "Not Sarah it's Quentin")
        # After first name capture via negation path, no _fn_direct_stored yet
        # (negation path uses _enter_fn_confirm, not _store_fn_direct)
        assert not s.get("_nc", {}).get("_fn_direct_stored")


class TestNamePrefixInPhoneQuestion:
    """Integration: 'Thanks {name} —' prefix folded into CONFIRM_PHONE question."""

    @pytest.mark.asyncio
    async def test_thanks_prefix_on_confirm_phone(self):
        """After name accepted via direct path, CONFIRM_PHONE includes 'Thanks {name}'."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_NAME_IDX,
            selected_location="alcester",
            new_or_returning="new",
            phone_from_twilio=True,
            twilio_from="+441234567890",
        )
        engine = _make_booking_engine(session, tts)

        # Turn 1: give first name (strong token → no fn_confirm)
        await engine.handle_transcript("my name is quentin")
        # Turn 2: give surname
        await engine.handle_transcript("roch")
        # Turn 3: confirm surname
        await engine.handle_transcript("yes")

        # Now we should be at CONFIRM_PHONE — check prefix was set
        all_spoken = tts.all_text()
        # The phone question should contain "Thanks Quentin"
        assert "Thanks Quentin" in all_spoken or session.get("_nc_transition_prefix", "").startswith("Thanks Quentin")


# ════════════════════════════════════════════════════════════════════════════
# PART 3 — PHONE: exact new wording
# ════════════════════════════════════════════════════════════════════════════

_PHONE_EXACT = (
    "If you'd like me to use the number you're calling from "
    "for the booking, please say yes please."
)


class TestPhoneQuestion:
    """CONFIRM_PHONE (booking) must use the exact new sentence."""

    @pytest.mark.asyncio
    async def test_exact_phone_question_wording(self):
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CONFIRM_PHONE_BK_IDX,
            phone_from_twilio=True,
            twilio_from="+441234567890",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        assert tts.last() == _PHONE_EXACT

    @pytest.mark.asyncio
    async def test_no_twilio_skips_to_collect_phone(self):
        """When no Twilio number, CONFIRM_PHONE must skip to COLLECT_PHONE."""
        tts = _FakeTTSQueue()
        _COLLECT_PHONE_IDX = next(
            i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "COLLECT_PHONE"
        )
        session = _fresh_session(
            flow_step=_CONFIRM_PHONE_BK_IDX,
            phone_from_twilio=False,
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        # Should have advanced to COLLECT_PHONE
        assert session["flow_step"] == _COLLECT_PHONE_IDX

    @pytest.mark.asyncio
    async def test_old_auto_accept_phrasing_gone(self):
        """'I'll use the number you're calling from.' must NOT be spoken."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CONFIRM_PHONE_BK_IDX,
            phone_from_twilio=True,
            twilio_from="+441234567890",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        all_spoken = tts.all_text()
        assert "I'll use the number" not in all_spoken

    @pytest.mark.asyncio
    async def test_reschedule_confirm_phone_unaffected(self):
        """RESCHEDULE_FLOW CONFIRM_PHONE keeps its own booking-lookup wording."""
        tts = _FakeTTSQueue()
        _RSCH_CP_IDX = next(
            i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "CONFIRM_PHONE"
        )
        session = _fresh_session(
            flow_step=_RSCH_CP_IDX,
            phone_from_twilio=True,
            twilio_from="+441234567890",
            intent="reschedule",
        )
        engine = _make_reschedule_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        # Must NOT use the booking wording
        assert q != _PHONE_EXACT
        # Should mention booking/associated
        assert "booking" in q.lower() or "associated" in q.lower()

    @pytest.mark.asyncio
    async def test_phone_confirm_armed_on_emit(self):
        """phone_confirm_armed must be True after CONFIRM_PHONE question is spoken."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_CONFIRM_PHONE_BK_IDX,
            phone_from_twilio=True,
            twilio_from="+441234567890",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        assert session.get("phone_confirm_armed") is True


# ════════════════════════════════════════════════════════════════════════════
# PART 1 EXTRA — Combined flow scenario
# ════════════════════════════════════════════════════════════════════════════

class TestFullFlowEmbedding:
    """Smoke-test: location → reason → name → phone all chain correctly."""

    @pytest.mark.asyncio
    async def test_booking_reason_question_has_location(self):
        """After location is resolved, COLLECT_REASON embeds the clinic name."""
        tts = _FakeTTSQueue()
        session = _fresh_session(
            flow_step=_COLLECT_REASON_IDX,
            selected_location="redditch",
            new_or_returning="new",
        )
        engine = _make_booking_engine(session, tts)
        await engine.ask_current_question()
        q = tts.last()
        assert "Redditch" in q
        assert "brings you in" in q.lower()
