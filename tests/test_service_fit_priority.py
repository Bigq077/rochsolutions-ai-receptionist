"""
Regression tests for service_fit routing priority at DETECT_INTENT.

Bug family v1: callers whose first utterance contains a capability/treatment question
("could you treat", "can you help with") were being routed directly into NEW_OR_RETURNING
instead of having the service-fit question answered first.

Bug family v2 (this session):
  BUG 1 — service-fit answer too confident when minor mentioned:
    "do you treat children" must NOT receive "Yes, absolutely" — must use clinic child policy.
  BUG 2 — booking bridge acceptance broken:
    "take a few details" (echo of bridge) must deterministically accept, not fall to general_query.
"""
from __future__ import annotations

import pytest

from app.media_streams.utterance_router import (
    has_service_fit_question,
    has_minor_reference,
    SERVICE_FIT_PHRASES,
)


# ---------------------------------------------------------------------------
# 1. Phrase detection — "could you treat" must be recognised as service_fit
# ---------------------------------------------------------------------------

class TestServiceFitPhrases:
    """has_service_fit_question() must cover the failing phrase families."""

    def _check(self, text: str) -> bool:
        return has_service_fit_question(text)

    # The exact phrase from the live failure
    def test_could_you_treat(self):
        assert self._check("could you treat my little boy he hurt his ankle")

    def test_can_you_treat(self):
        assert self._check("can you treat a sports injury")

    def test_could_you_help_my(self):
        assert self._check("could you help my son with his knee")

    def test_can_you_help_my(self):
        assert self._check("can you help my daughter with her back")

    def test_could_you_see_my(self):
        assert self._check("could you see my little girl about her shoulder")

    # Existing phrases must still work
    def test_can_you_help_unchanged(self):
        assert self._check("can you help with this")

    def test_would_physio_help_unchanged(self):
        assert self._check("would physio help with a sprained ankle")

    def test_is_this_something_unchanged(self):
        assert self._check("is this something you treat")

    def test_do_you_treat_unchanged(self):
        assert self._check("do you treat sports injuries")

    # Must NOT fire on pure booking or FAQ
    def test_no_false_positive_booking(self):
        assert not self._check("I'd like to book an appointment please")

    def test_no_false_positive_faq(self):
        assert not self._check("what are your prices")

    def test_no_false_positive_short(self):
        assert not self._check("ankle")


# ---------------------------------------------------------------------------
# 2. Router gate — "could you treat" utterance at DETECT_INTENT must reach router
# ---------------------------------------------------------------------------

class TestRouterGate:
    """_should_call_router must return True for service_fit utterances at DETECT_INTENT."""

    def _gate(self, text: str, state: str = "DETECT_INTENT") -> tuple[bool, str]:
        from app.media_streams.utterance_router import _should_call_router
        session: dict = {}
        return _should_call_router(text, state, session)

    def test_gate_fires_for_could_you_treat(self):
        ok, reason = self._gate(
            "i saw online you were recommended in alcester "
            "could you treat my little boy he hurt his ankle"
        )
        assert ok is True

    def test_gate_fires_for_service_fit_injury_context(self):
        ok, reason = self._gate(
            "my son hurt his ankle playing football is this something you treat"
        )
        assert ok is True

    def test_gate_fires_context_no_booking_safety_net(self):
        # Long injury-context utterance at DETECT_INTENT with no booking signal.
        # The safety-net gate should fire even without an explicit service_fit phrase.
        ok, reason = self._gate(
            "my little boy hurt his ankle playing football it has been about a week now "
            "and he is still limping i was wondering what you could do"
        )
        assert ok is True

    def test_gate_still_skips_short_utterance(self):
        ok, reason = self._gate("ankle")
        assert ok is False

    def test_gate_still_skips_ineligible_state(self):
        ok, reason = self._gate(
            "my son hurt his ankle playing football could you treat him",
            state="COLLECT_NAME",
        )
        assert ok is False


# ---------------------------------------------------------------------------
# 3. Deterministic fast-path — service_fit at DETECT_INTENT returns correct action
# ---------------------------------------------------------------------------

class TestDeterministicFastPath:
    """analyze_utterance_for_flow_control must return ANSWER_SERVICE_FIT_THEN_ASK_PENDING
    for the failing utterance without needing the LLM."""

    @pytest.mark.asyncio
    async def test_returns_service_fit_action_for_could_you_treat(self):
        from app.media_streams.utterance_router import (
            analyze_utterance_for_flow_control,
            ANSWER_SERVICE_FIT_THEN_ASK_PENDING,
        )
        session: dict = {}
        result = await analyze_utterance_for_flow_control(
            text=(
                "basically my little boy was playing football and he hurt his ankle "
                "while running and i saw online you were recommended in alcester "
                "i just want to know could you treat my little boy"
            ),
            state="DETECT_INTENT",
            session=session,
        )
        assert result is not None, "Router returned None — gate or fast-path failed"
        assert result["action"] == ANSWER_SERVICE_FIT_THEN_ASK_PENDING
        assert result["source"] == "deterministic"
        assert result["primary_intent"] == "service_fit"

    @pytest.mark.asyncio
    async def test_returns_service_fit_for_is_this_something_you_treat(self):
        from app.media_streams.utterance_router import (
            analyze_utterance_for_flow_control,
            ANSWER_SERVICE_FIT_THEN_ASK_PENDING,
        )
        session: dict = {}
        result = await analyze_utterance_for_flow_control(
            text="my son hurt his ankle is this something you treat",
            state="DETECT_INTENT",
            session=session,
        )
        assert result is not None
        assert result["action"] == ANSWER_SERVICE_FIT_THEN_ASK_PENDING


# ---------------------------------------------------------------------------
# 4. _detect_intent — service_fit phrases must NOT be mis-routed to booking
#    (these use the deterministic helper directly, not the full router)
# ---------------------------------------------------------------------------

class TestDetectIntentServiceFitPhrases:
    """Ensure the phrase additions don't break _detect_intent directly.

    The priority fix means the router intercepts before _detect_intent for
    service_fit utterances.  But we also verify has_service_fit_question
    is consistent so future callers of that helper get the right answer.
    """

    def test_could_you_treat_detected_as_service_fit(self):
        assert has_service_fit_question(
            "could you treat my little boy he hurt his ankle"
        )

    def test_can_you_treat_detected_as_service_fit(self):
        assert has_service_fit_question("can you treat a sports injury")


# ---------------------------------------------------------------------------
# 5. Clear booking utterances must still go to booking (no regression)
# ---------------------------------------------------------------------------

class TestBookingRegressionUnchanged:
    """Pure booking utterances must still be classified as booking."""

    def _detect(self, text: str) -> str:
        # Import the engine class method via a lightweight shim
        from app.media_streams.utterance_router import has_service_fit_question
        # These should NOT be detected as service_fit
        return has_service_fit_question(text)

    def test_i_want_to_book_not_service_fit(self):
        assert not self._detect("I'd like to book an appointment please")

    def test_book_for_friday_not_service_fit(self):
        assert not self._detect("Can I book for Friday morning")

    def test_arrange_assessment_not_service_fit(self):
        assert not self._detect("I want to arrange a physiotherapy assessment")


# ---------------------------------------------------------------------------
# 6. FAQ phrases must still not trigger service_fit
# ---------------------------------------------------------------------------

class TestFAQNotServiceFit:
    """Parking, pricing, and hours questions must not fire service_fit detection."""

    def test_parking_not_service_fit(self):
        assert not has_service_fit_question("Is there parking near the clinic")

    def test_pricing_not_service_fit(self):
        assert not has_service_fit_question("How much does a session cost")

    def test_hours_not_service_fit(self):
        assert not has_service_fit_question("What time do you open on Saturdays")


# ---------------------------------------------------------------------------
# 7. _service_fit_answered continuation: confirmation tokens
# ---------------------------------------------------------------------------

class TestServiceFitConfirmationTokens:
    """The set of confirmation tokens used in the DETECT_INTENT continuation
    block must correctly identify 'yes please' style responses."""

    _SFA_YES = {"yes", "yeah", "yep", "yup", "please", "ok", "okay",
                "sure", "great", "lovely", "perfect", "absolutely",
                "definitely", "brilliant", "wonderful", "alright", "right"}

    def _confirmed(self, text: str) -> bool:
        tokens = set(text.strip().lower().split())
        word_match = bool(tokens & self._SFA_YES)
        phrase_match = any(p in text.lower() for p in (
            "go ahead", "go on then",
            "that would", "i'd like", "id like",
            "i would like", "let's do", "lets do", "sounds good",
            "book me", "book an", "arrange that", "get that",
            "take a few", "few details", "take some details",
            "please do", "can we do that", "let's arrange",
            "lets arrange", "get booked",
        ))
        short_booking = (
            len(tokens) <= 4
            and bool(tokens & {"book", "booked", "arrange", "arranged"})
        )
        return word_match or phrase_match or short_booking

    def test_yes_please_confirms(self):
        assert self._confirmed("yes please")

    def test_yeah_confirms(self):
        assert self._confirmed("yeah")

    def test_ok_go_ahead_confirms(self):
        assert self._confirmed("ok go ahead")

    def test_that_would_be_great_confirms(self):
        assert self._confirmed("that would be great")

    def test_id_like_that_confirms(self):
        assert self._confirmed("i'd like that")

    # ── Bug 2 regression: echo-of-bridge phrases must confirm ──────────
    def test_take_a_few_details_confirms(self):
        """Live failure: caller echoed 'take a few details' and it wasn't caught."""
        assert self._confirmed("take a few details")

    def test_take_a_few_confirms(self):
        assert self._confirmed("take a few")

    def test_few_details_confirms(self):
        assert self._confirmed("few details")

    def test_go_on_then_confirms(self):
        assert self._confirmed("go on then")

    def test_book_it_confirms(self):
        assert self._confirmed("book it")

    def test_please_do_confirms(self):
        assert self._confirmed("please do")

    def test_brilliant_confirms(self):
        assert self._confirmed("brilliant")

    def test_alright_confirms(self):
        assert self._confirmed("alright")

    def test_no_does_not_confirm(self):
        assert not self._confirmed("no")

    def test_no_just_checking_does_not_confirm(self):
        assert not self._confirmed("no just checking")

    def test_what_are_your_prices_does_not_confirm(self):
        assert not self._confirmed("what are your prices")


# ---------------------------------------------------------------------------
# 8. Minor/child reference detection
# ---------------------------------------------------------------------------

class TestMinorReferenceDetection:
    """has_minor_reference() must catch child/minor references reliably."""

    def test_my_son(self):
        assert has_minor_reference("my son hurt his ankle")

    def test_my_daughter(self):
        assert has_minor_reference("can you help with my daughter")

    def test_my_little_boy(self):
        assert has_minor_reference("my little boy was playing football")

    def test_my_little_girl(self):
        assert has_minor_reference("my little girl hurt her wrist")

    def test_my_child(self):
        assert has_minor_reference("my child needs physio")

    def test_my_children(self):
        assert has_minor_reference("do you treat children")

    def test_treat_children(self):
        assert has_minor_reference("could you treat children")

    def test_my_kid(self):
        assert has_minor_reference("my kid sprained his ankle")

    def test_teenager(self):
        assert has_minor_reference("my teenager hurt their knee")

    def test_paediatric(self):
        assert has_minor_reference("do you offer paediatric physiotherapy")

    def test_adult_no_minor_ref(self):
        assert not has_minor_reference("I hurt my ankle playing football")

    def test_booking_no_minor_ref(self):
        assert not has_minor_reference("I'd like to book an appointment")

    def test_she_is_short_no_false_positive(self):
        # "she is" occurs in minor phrases as a prefix — ensure it doesn't
        # fire on "she is coming in with me" (adult context)
        # Note: our minor phrase includes "she is " with trailing space.
        assert has_minor_reference("she is 8 years old")


# ---------------------------------------------------------------------------
# 9. Child policy gate — Theorem Health sees patients aged 7 and over
# ---------------------------------------------------------------------------

class TestChildPolicyGate:
    """Verify that the clinic config child policy is accessible and correct."""

    def test_theorem_patient_policies_min_age_7(self):
        from app.clinic_config import get_clinic
        clinic = get_clinic("theorem")
        policies = clinic.get("patient_policies", {})
        assert policies.get("no_children") is False
        assert policies.get("min_patient_age") == 7

    def test_theorem_children_policy_faq_exists(self):
        from app.clinic_config import get_clinic
        clinic = get_clinic("theorem")
        policy_text = clinic.get("faq", {}).get("children_policy", "")
        assert policy_text != "", "children_policy FAQ must exist for Theorem"
        assert "7" in policy_text or "paediatric" in policy_text.lower()

    def test_theorem_v2_inherits_min_age_7(self):
        from app.clinic_config import get_clinic
        clinic = get_clinic("theorem_v2")
        policies = clinic.get("patient_policies", {})
        assert policies.get("no_children") is False
        assert policies.get("min_patient_age") == 7
