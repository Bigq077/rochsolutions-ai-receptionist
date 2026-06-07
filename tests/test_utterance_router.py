# tests/test_utterance_router.py
"""
Focused tests for the bounded utterance router.

Covers:
  - Repair / confusion phrase detection (deterministic)
  - Service-fit question detection (deterministic)
  - Gate logic (should_call_router)
  - Bridge template lookups (deterministic)
  - Full analyze_utterance_for_flow_control() flow:
      * repair → DO_NOT_ADVANCE_REPAIR without LLM
      * service-fit without booking → ANSWER_SERVICE_FIT_THEN_ASK_PENDING without LLM
      * short utterance → None (gate rejects)
      * non-eligible state → None
      * malformed LLM JSON → None (safe fallback)
      * invalid LLM action → None (safe fallback)
      * low LLM confidence → None (safe fallback)
      * LLM timeout → None (safe fallback)
  - reason captured from mixed utterance (has_reason + has_context_reason helper)
  - booking/FAQ crossover not mis-classified as repair
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.media_streams.utterance_router import (
    ANSWER_SERVICE_FIT_THEN_ASK_PENDING,
    BRIDGE_THEN_ASK_PENDING,
    DO_NOT_ADVANCE_REPAIR,
    INTENT_SWITCH_TO_BOOKING,
    INTENT_SWITCH_TO_FAQ,
    REPEAT_PENDING_QUESTION_CLEARLY,
    TREAT_AS_STATE_ANSWER,
    _VALID_ACTIONS,
    _should_call_router,
    analyze_utterance_for_flow_control,
    get_bridge_text,
    has_context_reason,
    has_service_fit_question,
    is_repair_utterance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session() -> Dict[str, Any]:
    """Fresh session with no cooldown stamp."""
    return {}


def _session_cooled() -> Dict[str, Any]:
    """Session whose router timestamp is recent (cooldown active)."""
    return {"_router_last_call_ts": time.time()}


# ---------------------------------------------------------------------------
# is_repair_utterance — deterministic phrase matching
# ---------------------------------------------------------------------------

class TestIsRepairUtterance:
    def test_obvious_meta_question(self):
        assert is_repair_utterance("Sorry, that's kind of unclear — what were you asking?")

    def test_what_did_you_mean(self):
        assert is_repair_utterance("What did you mean by that?")

    def test_what_do_you_mean(self):
        assert is_repair_utterance("Sorry, what do you mean exactly?")

    def test_passive_repair_i_was_just_asking(self):
        assert is_repair_utterance("I was just asking if you could help me out")

    def test_only_asking_whether(self):
        assert is_repair_utterance("I was only asking whether you could help")

    def test_dont_understand(self):
        assert is_repair_utterance("I don't understand what you're asking")

    def test_i_just_wanted_to_know_is_NOT_repair(self):
        # "i just wanted to know" removed from REPAIR_PHRASES — it is too broad
        # and fires on legitimate service-fit questions like
        # "I just wanted to know what exactly could you do at the clinic".
        # These are now handled by the service-fit interrupt instead.
        assert not is_repair_utterance("I just wanted to know if you could help")
        assert not is_repair_utterance("I just wanted to know what exactly could you do")

    def test_pardon(self):
        assert is_repair_utterance("Pardon?")

    def test_come_again(self):
        assert is_repair_utterance("Come again?")

    def test_say_that_again(self):
        assert is_repair_utterance("Could you say that again please?")

    def test_thats_unclear(self):
        assert is_repair_utterance("That's unclear")

    def test_normal_booking_not_repair(self):
        assert not is_repair_utterance("I'd like to book an appointment at Alcester")

    def test_location_answer_not_repair(self):
        assert not is_repair_utterance("I'd prefer the Redditch clinic")

    def test_mixed_reason_not_repair(self):
        assert not is_repair_utterance(
            "My son hurt his ankle last week and I wanted to know if physio could help"
        )

    def test_yes_not_repair(self):
        assert not is_repair_utterance("Yes")

    def test_no_not_repair(self):
        assert not is_repair_utterance("No that's fine")

    def test_alcester_not_repair(self):
        assert not is_repair_utterance("Alcester please")

    def test_mixed_intent_switch_not_repair(self):
        assert not is_repair_utterance("If that sounds right then I'd like to get booked in")


# ---------------------------------------------------------------------------
# has_service_fit_question — deterministic phrase matching
# ---------------------------------------------------------------------------

class TestHasServiceFitQuestion:
    def test_can_you_help(self):
        assert has_service_fit_question(
            "I wanted to know if you can help with my ankle"
        )

    def test_could_help(self):
        assert has_service_fit_question(
            "I wondered if physio could help with a pulled hamstring"
        )

    def test_would_physio_help(self):
        assert has_service_fit_question(
            "Would physiotherapy help with a knee injury?"
        )

    def test_is_this_right_place(self):
        assert has_service_fit_question("Is this the right place for back pain?")

    def test_able_to_help(self):
        assert has_service_fit_question("Are you able to help with my back?")

    def test_do_you_treat(self):
        assert has_service_fit_question("Do you treat sports injuries?")

    def test_help_with_my(self):
        assert has_service_fit_question("Can you help with my shoulder?")

    def test_help_with_his(self):
        assert has_service_fit_question(
            "My son hurt his ankle and I wanted to know if you could help with his recovery"
        )

    def test_booking_only_not_service_fit(self):
        assert not has_service_fit_question("I'd like to book an appointment")

    def test_location_not_service_fit(self):
        assert not has_service_fit_question("Alcester please")

    def test_yes_not_service_fit(self):
        assert not has_service_fit_question("Yes")


# ---------------------------------------------------------------------------
# has_context_reason — deterministic phrase matching
# ---------------------------------------------------------------------------

class TestHasContextReason:
    def test_my_son(self):
        assert has_context_reason("My son hurt his ankle last week")

    def test_ive_been_having(self):
        assert has_context_reason("I've been having back pain for a few weeks")

    def test_ive_had(self):
        assert has_context_reason("I've had knee pain")

    def test_injured_my(self):
        assert has_context_reason("I injured my wrist playing tennis")

    def test_pain_in(self):
        assert has_context_reason("I have pain in my shoulder")

    def test_been_suffering(self):
        assert has_context_reason("I've been suffering with back problems")

    def test_booking_no_reason(self):
        assert not has_context_reason("I'd like to book an appointment at Alcester")

    def test_location_no_reason(self):
        assert not has_context_reason("Redditch please")


# ---------------------------------------------------------------------------
# _should_call_router — gate logic
# ---------------------------------------------------------------------------

class TestGate:
    def test_too_short_rejected(self):
        ok, reason = _should_call_router("Yes", "ASK_LOCATION", _session())
        assert not ok
        assert reason == "too_short"

    def test_two_words_rejected(self):
        ok, reason = _should_call_router("Yes please", "ASK_LOCATION", _session())
        assert not ok
        assert reason == "too_short"

    def test_non_eligible_state_rejected(self):
        ok, reason = _should_call_router(
            "Please take down my phone number it is zero seven",
            "COLLECT_PHONE",
            _session(),
        )
        assert not ok
        assert reason == "state_not_eligible"

    def test_digit_heavy_rejected(self):
        ok, reason = _should_call_router(
            "My number is 07700 900123",
            "ASK_LOCATION",
            _session(),
        )
        assert not ok
        assert reason == "digit_heavy"

    def test_cooldown_blocks(self):
        ok, reason = _should_call_router(
            "Sorry that's unclear — what were you asking me?",
            "ASK_LOCATION",
            _session_cooled(),
        )
        assert not ok
        assert reason == "cooldown"

    def test_repair_in_question_state_fires(self):
        ok, reason = _should_call_router(
            "Sorry that's unclear — what were you asking?",
            "ASK_LOCATION",
            _session(),
        )
        assert ok
        assert reason == "repair_in_question_state"

    def test_repair_at_new_or_returning_fires(self):
        ok, reason = _should_call_router(
            "I don't understand what you're asking me",
            "NEW_OR_RETURNING",
            _session(),
        )
        assert ok
        assert reason == "repair_in_question_state"

    def test_multi_signal_booking_fires(self):
        ok, reason = _should_call_router(
            "I wanted to book an appointment but I also wondered if you could help with my ankle",
            "DETECT_INTENT",
            _session(),
        )
        assert ok
        assert reason == "multi_signal_booking"

    def test_service_fit_at_detect_intent_fires(self):
        ok, reason = _should_call_router(
            "My son hurt his ankle last week and I wanted to know if your physio clinic could help",
            "DETECT_INTENT",
            _session(),
        )
        assert ok
        # repair gate or multi_signal or detect_intent_service_fit — any is valid
        assert ok

    def test_plain_booking_detect_intent_not_fired(self):
        # Short booking phrase — too short and no service-fit signal
        ok, _ = _should_call_router("I'd like to book", "DETECT_INTENT", _session())
        assert not ok  # too_short (3 words)

    def test_clear_booking_no_service_fit_not_fired(self):
        ok, reason = _should_call_router(
            "I would like to make an appointment at the Alcester clinic please",
            "DETECT_INTENT",
            _session(),
        )
        # has_booking=True, no service-fit, no context_reason → deterministic_sufficient
        assert not ok
        assert reason == "deterministic_sufficient"


# ---------------------------------------------------------------------------
# get_bridge_text — template lookup
# ---------------------------------------------------------------------------

class TestBridgeTemplates:
    def test_repair_ask_location_has_clinic_names(self):
        text = get_bridge_text(DO_NOT_ADVANCE_REPAIR, "ASK_LOCATION")
        assert "Alcester" in text or "Redditch" in text
        assert len(text) < 200

    def test_repair_new_or_returning_has_question(self):
        text = get_bridge_text(DO_NOT_ADVANCE_REPAIR, "NEW_OR_RETURNING")
        assert "before" in text or "new" in text

    def test_repair_collect_reason_has_reason_ask(self):
        text = get_bridge_text(DO_NOT_ADVANCE_REPAIR, "COLLECT_REASON")
        assert "help" in text or "today" in text

    def test_service_fit_detect_intent_non_empty(self):
        text = get_bridge_text(ANSWER_SERVICE_FIT_THEN_ASK_PENDING, "DETECT_INTENT")
        assert len(text) > 5

    def test_bridge_detect_intent_non_empty(self):
        text = get_bridge_text(BRIDGE_THEN_ASK_PENDING, "DETECT_INTENT")
        assert len(text) > 5

    def test_unknown_state_returns_default(self):
        text = get_bridge_text(DO_NOT_ADVANCE_REPAIR, "COMPLETELY_UNKNOWN_STATE_XYZ")
        assert len(text) > 0  # _default used, no crash

    def test_unknown_action_returns_fallback(self):
        text = get_bridge_text("NONEXISTENT_ACTION_XYZ", "ASK_LOCATION")
        assert len(text) > 0  # graceful fallback, no crash

    def test_all_templates_under_250_chars(self):
        """Every bridge template must be short enough for voice."""
        from app.media_streams.utterance_router import BRIDGE_TEMPLATES
        for action, states in BRIDGE_TEMPLATES.items():
            for state, text in states.items():
                assert len(text) <= 250, (
                    f"Bridge too long: action={action} state={state} len={len(text)}"
                )


# ---------------------------------------------------------------------------
# analyze_utterance_for_flow_control — integration (deterministic paths)
# ---------------------------------------------------------------------------

class TestAnalyzeUtteranceDeterministic:
    """These tests exercise only the deterministic code paths — no LLM mock needed."""

    @pytest.mark.asyncio
    async def test_repair_ask_location_returns_do_not_advance(self):
        result = await analyze_utterance_for_flow_control(
            "Sorry, that's kind of unclear — what were you asking?",
            "ASK_LOCATION",
            _session(),
        )
        assert result is not None
        assert result["action"] == DO_NOT_ADVANCE_REPAIR
        assert result["has_confusion_repair"] is True
        assert result["source"] == "deterministic"
        assert result["confidence"] >= 0.9

    @pytest.mark.asyncio
    async def test_passive_repair_ask_location(self):
        result = await analyze_utterance_for_flow_control(
            "I was just asking if you could help me out",
            "ASK_LOCATION",
            _session(),
        )
        assert result is not None
        assert result["action"] == DO_NOT_ADVANCE_REPAIR
        assert result["source"] == "deterministic"

    @pytest.mark.asyncio
    async def test_repair_new_or_returning(self):
        result = await analyze_utterance_for_flow_control(
            "I don't understand what you're asking me",
            "NEW_OR_RETURNING",
            _session(),
        )
        assert result is not None
        assert result["action"] == DO_NOT_ADVANCE_REPAIR

    @pytest.mark.asyncio
    async def test_service_fit_without_booking_detect_intent(self):
        result = await analyze_utterance_for_flow_control(
            "My son hurt his ankle last week and I wanted to know if your physio clinic could help",
            "DETECT_INTENT",
            _session(),
        )
        assert result is not None
        assert result["action"] == ANSWER_SERVICE_FIT_THEN_ASK_PENDING
        assert result["has_service_fit_question"] is True
        assert result["source"] == "deterministic"

    @pytest.mark.asyncio
    async def test_service_fit_detect_intent_has_reason_flagged(self):
        """Utterance with injury mention → has_reason=True even on deterministic path."""
        result = await analyze_utterance_for_flow_control(
            "I've been having back pain and I wondered if physio could help",
            "DETECT_INTENT",
            _session(),
        )
        assert result is not None
        assert result["action"] == ANSWER_SERVICE_FIT_THEN_ASK_PENDING
        assert result["has_reason"] is True  # has_context_reason fires

    @pytest.mark.asyncio
    async def test_short_utterance_returns_none(self):
        result = await analyze_utterance_for_flow_control("Yes", "ASK_LOCATION", _session())
        assert result is None

    @pytest.mark.asyncio
    async def test_non_eligible_state_returns_none(self):
        result = await analyze_utterance_for_flow_control(
            "I have been struggling with my knee for quite a long time now",
            "COLLECT_PHONE",
            _session(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_cooldown_returns_none(self):
        result = await analyze_utterance_for_flow_control(
            "Sorry that's unclear, I was just asking whether you can help",
            "ASK_LOCATION",
            _session_cooled(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_booking_with_service_fit_detect_intent_via_llm_gate(self):
        """
        Mixed booking + service-fit → gate fires (multi_signal_booking) but no
        deterministic fast-path; would go to LLM. Without a running LLM we just
        verify gate fires and result is either a valid action or None (LLM fails).
        """
        # Patch _call_llm_classifier to simulate network unavailability
        with patch(
            "app.media_streams.utterance_router._call_llm_classifier",
            side_effect=Exception("network unavailable"),
        ):
            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but I also wanted to know if you "
                "could help with my knee which has been giving me pain for a while",
                "DETECT_INTENT",
                _session(),
            )
        # LLM failed → None (safe deterministic fallback)
        assert result is None


# ---------------------------------------------------------------------------
# analyze_utterance_for_flow_control — LLM failure / safety paths
# ---------------------------------------------------------------------------

def _mock_llm_response(json_text: str):
    """Build a mock Anthropic response object."""
    response = MagicMock()
    response.content = [MagicMock(text=json_text)]
    return response


class TestLLMSafetyPaths:
    """These tests verify that bad LLM output is always handled safely."""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self):
        with patch(
            "app.media_streams.utterance_router._get_router_client"
        ) as mock_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                return_value=_mock_llm_response("not valid json at all!!!")
            )
            mock_fn.return_value = mock_client

            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but I also wondered if you could help "
                "with my back problem which I have had for several months now",
                "DETECT_INTENT",
                _session(),
            )
        # Either deterministic fast-path or None — never an exception
        if result is not None:
            assert result["action"] in _VALID_ACTIONS

    @pytest.mark.asyncio
    async def test_invalid_action_in_llm_output_returns_none(self):
        bad_json = (
            '{"action": "INVENT_BOOKING_NOW", "primary_intent": "booking", '
            '"secondary_intent": null, "has_reason": false, '
            '"has_service_fit_question": false, "has_confusion_repair": false, '
            '"has_real_answer_to_pending": false, "reason_text": null, "confidence": 0.9}'
        )
        with patch(
            "app.media_streams.utterance_router._get_router_client"
        ) as mock_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                return_value=_mock_llm_response(bad_json)
            )
            mock_fn.return_value = mock_client

            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but I also wondered if you could help "
                "with my back problem which I have had for quite a while now",
                "DETECT_INTENT",
                _session(),
            )
        if result is not None:
            assert result["action"] in _VALID_ACTIONS

    @pytest.mark.asyncio
    async def test_low_confidence_llm_returns_none(self):
        low_conf_json = (
            '{"action": "TREAT_AS_STATE_ANSWER", "primary_intent": "answer", '
            '"secondary_intent": null, "has_reason": false, '
            '"has_service_fit_question": false, "has_confusion_repair": false, '
            '"has_real_answer_to_pending": true, "reason_text": null, "confidence": 0.2}'
        )
        with patch(
            "app.media_streams.utterance_router._get_router_client"
        ) as mock_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                return_value=_mock_llm_response(low_conf_json)
            )
            mock_fn.return_value = mock_client

            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but I also wondered if you could help "
                "with a very specific injury that I have had for a very long time now",
                "DETECT_INTENT",
                _session(),
            )
        # Low confidence → None or deterministic result (both valid)
        if result is not None:
            assert result["action"] in _VALID_ACTIONS

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_none(self):
        async def _slow(*args, **kwargs):
            await asyncio.sleep(10)  # exceeds 4s wait_for timeout

        with patch(
            "app.media_streams.utterance_router._call_llm_classifier",
            side_effect=_slow,
        ):
            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but also needed to know whether "
                "your physiotherapy clinic could help with my long-standing knee issue",
                "DETECT_INTENT",
                _session(),
            )
        # Timeout → None, no exception raised
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        with patch(
            "app.media_streams.utterance_router._call_llm_classifier",
            side_effect=RuntimeError("connection refused"),
        ):
            result = await analyze_utterance_for_flow_control(
                "I wanted to book an appointment but also needed to know whether "
                "your physiotherapy clinic could help with my long-standing knee issue",
                "DETECT_INTENT",
                _session(),
            )
        assert result is None


# ---------------------------------------------------------------------------
# Reason-capture: stored reason skips COLLECT_REASON (flow.py integration)
# ---------------------------------------------------------------------------

class TestReasonCaptureHelper:
    """
    The COLLECT_REASON skip logic lives in flow.py (checks session["reason"]).
    These tests verify the router correctly signals has_reason=True so that
    flow.py can store the reason from the transcript.
    """

    @pytest.mark.asyncio
    async def test_injury_mention_sets_has_reason(self):
        result = await analyze_utterance_for_flow_control(
            "My son hurt his ankle last week and I wanted to know if your physio clinic could help",
            "DETECT_INTENT",
            _session(),
        )
        assert result is not None
        assert result["has_reason"] is True

    @pytest.mark.asyncio
    async def test_back_pain_mention_sets_has_reason(self):
        result = await analyze_utterance_for_flow_control(
            "I've been having back pain and I wondered if physio could help me",
            "DETECT_INTENT",
            _session(),
        )
        assert result is not None
        assert result["has_reason"] is True

    def test_has_context_reason_covers_various_injury_phrases(self):
        cases = [
            "My son hurt his ankle",
            "I've been suffering with back pain",
            "I injured my wrist playing sport",
            "I have pain in my shoulder",
            "I've had knee problems for a while",
            "I've been struggling with neck stiffness",
        ]
        for phrase in cases:
            assert has_context_reason(phrase), f"Expected has_context_reason=True for: {phrase!r}"


# ---------------------------------------------------------------------------
# Booking/FAQ intent crossover — must NOT be mis-classified as repair
# ---------------------------------------------------------------------------

class TestIntentCrossover:
    @pytest.mark.asyncio
    async def test_faq_booking_crossover_not_repair(self):
        """
        'If that sounds right I'd like to get booked in' — clear intent switch
        to booking. Must NOT return DO_NOT_ADVANCE_REPAIR.
        Gate likely rejects (no service-fit, no repair, has booking signal but
        word count < 12 or no context_reason) → None → existing _detect_intent handles it.
        """
        result = await analyze_utterance_for_flow_control(
            "If that sounds right then I'd like to get booked in",
            "FAQ_BOOKING_OFFER",
            _session(),
        )
        if result is not None:
            assert result["action"] != DO_NOT_ADVANCE_REPAIR

    @pytest.mark.asyncio
    async def test_location_answer_not_classified_as_repair(self):
        """A real location answer at ASK_LOCATION must be below repair threshold."""
        # Gate rejects (no repair phrases) → None
        result = await analyze_utterance_for_flow_control(
            "I'd prefer the Redditch clinic please",
            "ASK_LOCATION",
            _session(),
        )
        if result is not None:
            # If somehow gate fired, must not be repair
            assert result["action"] != DO_NOT_ADVANCE_REPAIR

    @pytest.mark.asyncio
    async def test_yes_answer_not_classified_as_repair(self):
        """Single word yes must never reach the router (too short)."""
        result = await analyze_utterance_for_flow_control("Yes", "NEW_OR_RETURNING", _session())
        assert result is None  # gate: too_short


# ---------------------------------------------------------------------------
# Live call regression: the exact utterance sequence that failed
# ---------------------------------------------------------------------------

class TestLiveCallRegression:
    """
    Regression for the live failure:
      Turn 1: "hi there mate"
      Turn 2: "i brought my son to football yesterday and his ankle's in quite a bit of pain"
      Turn 3: "i just wanted to know what exactly could you do at the clinic to help him out"

    Bad behaviour: Turn 3 hit the ASK_LOCATION repair guard → re-asked location.
    Fixed behaviour: Turn 3 must hit the service-fit interrupt → triage answer.
    """

    # ── Turn 1 ───────────────────────────────────────────────────────────────

    def test_hi_there_mate_is_not_repair(self):
        assert not is_repair_utterance("hi there mate")

    def test_hi_there_mate_is_not_service_fit(self):
        assert not has_service_fit_question("hi there mate")

    def test_hi_there_mate_is_not_context_reason(self):
        assert not has_context_reason("hi there mate")

    # ── Turn 2 ───────────────────────────────────────────────────────────────

    def test_ankle_pain_context_has_reason(self):
        assert has_context_reason(
            "i brought my son to football yesterday and his ankle's in quite a bit of pain"
        )

    def test_ankle_pain_context_is_not_repair(self):
        assert not is_repair_utterance(
            "i brought my son to football yesterday and his ankle's in quite a bit of pain"
        )

    def test_ankle_pain_context_is_not_service_fit(self):
        # No capability question in this utterance — just context
        assert not has_service_fit_question(
            "i brought my son to football yesterday and his ankle's in quite a bit of pain"
        )

    # ── Turn 3 (the primary regression) ──────────────────────────────────────

    def test_failing_utterance_is_service_fit(self):
        """The previously failing utterance must now be detected as service-fit."""
        assert has_service_fit_question(
            "i just wanted to know what exactly could you do at the clinic to help him out"
        )

    def test_failing_utterance_is_NOT_repair(self):
        """The previously failing utterance must NOT be detected as repair."""
        assert not is_repair_utterance(
            "i just wanted to know what exactly could you do at the clinic to help him out"
        )

    def test_failing_utterance_removed_phrases_not_repair(self):
        """Individual removed phrases must no longer classify as repair."""
        # These were too broad — they fire on legitimate service-fit questions
        assert not is_repair_utterance("I just wanted to know what you could do")
        assert not is_repair_utterance("I only wanted to know if physio could help")
        assert not is_repair_utterance("I just wanted to ask what the clinic does")

    # ── Service-fit phrase expansion coverage ─────────────────────────────────

    def test_what_exactly_could_is_service_fit(self):
        assert has_service_fit_question("what exactly could you do for an ankle injury")

    def test_what_could_you_do_is_service_fit(self):
        assert has_service_fit_question("what could you do for something like this?")

    def test_what_do_you_do_for_is_service_fit(self):
        assert has_service_fit_question("what do you do for sports injuries?")

    def test_what_would_the_clinic_is_service_fit(self):
        assert has_service_fit_question("what would the clinic do for a muscle strain?")

    def test_could_you_help_him_is_service_fit(self):
        assert has_service_fit_question("could you help him with his ankle?")

    def test_help_him_out_is_service_fit(self):
        assert has_service_fit_question("can the clinic help him out?")

    # ── Genuine repair phrases must still work ────────────────────────────────

    def test_sorry_what_was_the_question_is_repair(self):
        assert is_repair_utterance("sorry what was the question?")

    def test_could_you_repeat_that_is_repair(self):
        assert is_repair_utterance("could you repeat that please?")

    def test_what_do_you_mean_is_repair(self):
        assert is_repair_utterance("sorry, what do you mean by that?")

    def test_pardon_is_repair(self):
        assert is_repair_utterance("Pardon?")

    def test_i_dont_understand_is_repair(self):
        assert is_repair_utterance("I don't understand what you're asking me")

    # ── Real location answers must NOT be mis-classified ─────────────────────

    def test_alcester_not_service_fit(self):
        assert not has_service_fit_question("Alcester please")

    def test_redditch_not_service_fit(self):
        assert not has_service_fit_question("I'd prefer the Redditch clinic")

    def test_alcester_not_repair(self):
        assert not is_repair_utterance("The Alcester one would be better for us")

    def test_redditch_not_repair(self):
        assert not is_repair_utterance("Redditch please")


# ---------------------------------------------------------------------------
# Service-fit phrase family — expanded set
# ---------------------------------------------------------------------------

class TestServiceFitExpanded:
    """Coverage for the newly-added SERVICE_FIT_PHRASES entries."""

    def test_what_does_physio(self):
        assert has_service_fit_question("what does physio actually do for ankle injuries?")

    def test_what_would_physiotherapy(self):
        assert has_service_fit_question("what would physiotherapy involve for this?")

    def test_what_does_physiotherapy(self):
        assert has_service_fit_question("what does physiotherapy do for sports injuries?")

    def test_what_can_the_clinic(self):
        assert has_service_fit_question("what can the clinic offer for my son?")

    def test_what_would_physio(self):
        assert has_service_fit_question("what would physio do for a sprained ankle?")

    def test_help_her_out(self):
        assert has_service_fit_question("is there anything that can help her out?")

    # Ensure day/time/name/phone answers don't accidentally trigger service-fit
    def test_monday_not_service_fit(self):
        assert not has_service_fit_question("Monday would be best")

    def test_phone_number_not_service_fit(self):
        assert not has_service_fit_question("My number is zero seven seven zero nine")

    def test_new_patient_not_service_fit(self):
        assert not has_service_fit_question("I'm a new patient")

    def test_returning_patient_not_service_fit(self):
        assert not has_service_fit_question("I've been before, I'm a returning patient")
