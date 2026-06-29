# tests/test_service_fit_policy.py
"""
Focused tests for service_fit_policy.py — the deterministic service-fit
answer policy evaluator.

Covers:
- child age gate: no_children=True + age unknown → ASK_CHILD_AGE (NOT disallow)
- child age gate: no_children=True + minor age → POLICY_DISALLOW
- child age gate: no_children=True + adult age → allow
- suitability / recommendation blocker → SUITABILITY_CLINICIAN_DECIDES
- service availability questions → SERVICE_AVAILABLE_ONLY
- adult MSK condition → ALLOW_ASSESSMENT_FIRST
- unsupported condition → REQUIRE_TEAM_CONFIRMATION
- no overconfident wording in any template
- no speculative modality recommendations in any template
"""
from __future__ import annotations

import pytest
from app.media_streams.service_fit_policy import (
    evaluate_service_fit,
    is_suitability_question,
    is_availability_question,
    detect_service_name,
    clinician_decides_response,
    service_availability_response,
    condition_is_supported,
    assessment_first_response,
    _team_confirmation_response,
    POLICY_DISALLOW,
    ASK_CHILD_AGE,
    SERVICE_AVAILABLE_ONLY,
    SUITABILITY_CLINICIAN_DECIDES,
    ALLOW_ASSESSMENT_FIRST,
    REQUIRE_TEAM_CONFIRMATION,
    INSUFFICIENT_KNOWLEDGE_SAFE_REPLY,
)


# ── Overconfidence / speculation guard ───────────────────────────────────────────

_OVERCONFIDENT_PHRASES = (
    "yes, absolutely",
    "yes absolutely",
    "that's exactly the kind",
    "absolutely — that",
    "absolutely — we",
    "absolutely, yes",
)

_SPECULATIVE_PHRASES = (
    "typically",
    "shockwave therapy",
    "usually they would",
    "hands-on treatment, exercise",
    "exercise and possibly",
)


def _assert_no_overconfidence(text: str) -> None:
    t = text.lower()
    for phrase in _OVERCONFIDENT_PHRASES:
        assert phrase not in t, (
            f"Overconfident phrase {phrase!r} found: {text!r}"
        )


def _assert_no_speculative_recommendation(text: str) -> None:
    t = text.lower()
    for phrase in _SPECULATIVE_PHRASES:
        assert phrase not in t, (
            f"Speculative phrase {phrase!r} found: {text!r}"
        )


# ── Clinic fixtures ────────────────────────────────────────────────────────────

def _clinic_no_children() -> dict:
    """Adults only (no_children=True). Age unknown → must ask. Minor → decline."""
    return {
        "patient_policies": {"no_children": True},
        "faq": {
            "children_policy": (
                "We see adults only — we don't currently offer paediatric physiotherapy."
            ),
            "conditions_treated": (
                "We treat back pain, neck pain, shoulder pain, sports injuries, "
                "ankle sprains, knee problems, hip problems, muscle strains."
            ),
        },
        "services": [
            "Physiotherapy Assessment",
            "Acupuncture",
            "Shockwave Therapy",
            "Class IV Laser Therapy",
        ],
    }


def _clinic_children_from_16() -> dict:
    """Children accepted from age 16 only."""
    return {
        "patient_policies": {"no_children": False, "min_patient_age": 16},
        "faq": {
            "children_policy": "We see patients aged 16 and over.",
            "conditions_treated": "Musculoskeletal conditions including sports injuries.",
        },
        "services": ["Physiotherapy Assessment"],
    }


def _clinic_children_all_ages() -> dict:
    """Children accepted, no age restriction."""
    return {
        "patient_policies": {"no_children": False},
        "faq": {
            "conditions_treated": (
                "Sports injuries, ankle pain, back pain."
            ),
        },
        "services": ["Physiotherapy Assessment"],
    }


def _clinic_policy_unconfigured() -> dict:
    """Children policy not set (None)."""
    return {
        "patient_policies": {},
        "faq": {
            "conditions_treated": "Sports injuries, ankle pain, back pain.",
        },
        "services": ["Physiotherapy Assessment"],
    }


def _empty_session() -> dict:
    return {}


def _theorem() -> dict:
    from app.clinic_config import get_clinic
    return get_clinic("theorem")


# ── 1. Child + age missing ─────────────────────────────────────────────────────

class TestChildAgeMissing:
    """
    Core fix: no_children=True + age=None must → ASK_CHILD_AGE.
    "My son" could be 19 — we must ask before deciding.
    """

    def test_no_children_true_age_missing_asks_age(self):
        """The primary bug: missing age must NOT produce immediate disallow."""
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == ASK_CHILD_AGE

    def test_no_children_true_age_missing_sets_pending_followup(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=None,
        )
        assert result.pending_followup == "child_age"

    def test_no_children_true_age_missing_response_is_question(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=None,
        )
        # Must be a question, not a decline
        assert "?" in result.response_text
        assert "adults only" not in result.response_text.lower()

    def test_min_age_configured_age_missing(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == ASK_CHILD_AGE
        assert result.pending_followup == "child_age"

    def test_policy_unconfigured_age_missing(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_policy_unconfigured(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == ASK_CHILD_AGE

    def test_son_pronoun_in_age_question(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            child_related=True,
            age=None,
            relationship="son",
        )
        assert "your son" in result.response_text.lower()

    def test_daughter_pronoun_in_age_question(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            child_related=True,
            age=None,
            relationship="daughter",
        )
        assert "your daughter" in result.response_text.lower()

    def test_no_booking_language_in_age_question(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=None,
        )
        assert "take a few details" not in result.response_text.lower()
        assert "book" not in result.response_text.lower()


# ── 2. Child + disallowed age ─────────────────────────────────────────────────

class TestChildDisallowedAge:
    def test_no_children_true_minor_disallows(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=10,
        )
        assert result.action == POLICY_DISALLOW

    def test_no_children_true_age_17_disallows(self):
        # 17 < default adult_threshold (18)
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=17,
        )
        assert result.action == POLICY_DISALLOW

    def test_disallow_uses_faq_text(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=10,
        )
        assert "adults only" in result.response_text.lower()

    def test_min_age_below_threshold_disallows(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            child_related=True,
            age=14,
        )
        assert result.action == POLICY_DISALLOW

    def test_no_booking_language_in_disallow(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=5,
        )
        assert "take a few details" not in result.response_text.lower()
        assert "book" not in result.response_text.lower()


# ── 3. Child + allowed age ────────────────────────────────────────────────────

class TestChildAllowedAge:
    def test_no_children_true_adult_age_18_allows(self):
        """'My son' who is 18 — adults-only clinic can see him."""
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=18,
            reason="ankle injury",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_no_children_true_adult_age_19_allows(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=19,
            reason="knee pain",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_min_age_met_allows(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            child_related=True,
            age=16,
            reason="sports injury",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_allowed_response_mentions_assessment(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=19,
            reason="ankle injury",
        )
        assert "assessment" in result.response_text.lower()

    def test_allowed_response_no_speculation(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            child_related=True,
            age=19,
            reason="ankle injury",
        )
        _assert_no_speculative_recommendation(result.response_text)


# ── 4. Suitability / recommendation blocker ──────────────────────────────────

class TestSuitabilityBlocker:
    """
    'What do you recommend?' / 'Would acupuncture work for me?' →
    SUITABILITY_CLINICIAN_DECIDES. Receptionist must never recommend modalities.
    """

    def _eval(self, text: str, reason: str = "ankle injury") -> str:
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text=text,
            reason=reason,
        )
        return result.action

    def test_what_do_you_recommend(self):
        assert self._eval("what do you recommend for my ankle") == SUITABILITY_CLINICIAN_DECIDES

    def test_would_acupuncture_work_for_me(self):
        assert self._eval("would acupuncture work for me") == SUITABILITY_CLINICIAN_DECIDES

    def test_scared_of_needles_would_still_work(self):
        assert self._eval(
            "i'm scared of needles would acupuncture still work for me"
        ) == SUITABILITY_CLINICIAN_DECIDES

    def test_what_would_be_best(self):
        assert self._eval("what would be best for my knee") == SUITABILITY_CLINICIAN_DECIDES

    def test_do_you_think_it_would_help(self):
        assert self._eval("do you think that would help") == SUITABILITY_CLINICIAN_DECIDES

    def test_should_i_do_shockwave(self):
        assert self._eval("should i do shockwave therapy") == SUITABILITY_CLINICIAN_DECIDES

    def test_which_is_better(self):
        assert self._eval("which is better acupuncture or laser") == SUITABILITY_CLINICIAN_DECIDES

    def test_what_would_they_recommend(self):
        assert self._eval("what would they recommend for my back") == SUITABILITY_CLINICIAN_DECIDES

    def test_clinician_decides_response_no_speculation(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="what do you recommend for my ankle",
        )
        _assert_no_speculative_recommendation(result.response_text)

    def test_clinician_decides_response_references_clinician(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="what would be best for my shoulder",
        )
        safe_words = ["clinician", "assessment", "decide", "suitable", "discuss", "proper"]
        found = any(w in result.response_text.lower() for w in safe_words)
        assert found

    def test_plain_service_fit_not_blocked(self):
        """Normal 'can you help with ankle?' must NOT be blocked by suitability check."""
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="can you help with a knee injury",
            reason="knee injury",
        )
        assert result.action != SUITABILITY_CLINICIAN_DECIDES

    def test_availability_question_not_blocked_as_suitability(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="do you offer acupuncture",
        )
        assert result.action != SUITABILITY_CLINICIAN_DECIDES


# ── 5. Service availability questions ────────────────────────────────────────

class TestServiceAvailability:
    """'Do you offer acupuncture?' → SERVICE_AVAILABLE_ONLY, short bounded answer."""

    def test_do_you_offer_acupuncture(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="do you offer acupuncture",
        )
        assert result.action == SERVICE_AVAILABLE_ONLY

    def test_do_you_have_shockwave(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="do you have shockwave therapy",
        )
        assert result.action == SERVICE_AVAILABLE_ONLY

    def test_availability_response_mentions_service(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="do you offer acupuncture",
        )
        assert "acupuncture" in result.response_text.lower()

    def test_availability_response_no_speculation(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            original_text="do you offer acupuncture",
        )
        _assert_no_speculative_recommendation(result.response_text)
        _assert_no_overconfidence(result.response_text)

    def test_theorem_acupuncture_available(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_theorem(),
            original_text="do you offer acupuncture",
        )
        assert result.action == SERVICE_AVAILABLE_ONLY
        assert "acupuncture" in result.response_text.lower()


# ── 6. Adult MSK service-fit ──────────────────────────────────────────────────

class TestAdultServiceFit:
    def test_ankle_injury_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_back_pain_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="back pain",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_response_mentions_assessment(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="hurt my ankle playing football",
        )
        assert "assessment" in result.response_text.lower()

    def test_no_overconfidence(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="shoulder pain",
        )
        _assert_no_overconfidence(result.response_text)

    def test_no_speculation(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
        )
        _assert_no_speculative_recommendation(result.response_text)

    def test_no_reason_insufficient_knowledge(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="",
        )
        assert result.action == INSUFFICIENT_KNOWLEDGE_SAFE_REPLY


# ── 7. Theorem Health real-world scenarios ────────────────────────────────────

class TestTheoremScenarios:
    """Scenarios against actual Theorem Health clinic config."""

    @pytest.fixture
    def theorem(self):
        from app.clinic_config import get_clinic
        return get_clinic("theorem")

    def test_son_ankle_age_unknown_asks_age(self, theorem):
        """'My son hurt his ankle' + no age → ask age, not decline."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="son hurt his ankle playing football",
            child_related=True,
            age=None,
            relationship="son",
        )
        assert result.action == ASK_CHILD_AGE

    def test_son_under_7_age_declined(self, theorem):
        """Under 7 → declined (Theorem sees patients aged 7 and over)."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            child_related=True,
            age=5,
            reason="ankle sprain",
        )
        assert result.action == POLICY_DISALLOW

    def test_son_age_15_allowed(self, theorem):
        """A 15-year-old is over the minimum age of 7 → seen."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            child_related=True,
            age=15,
            reason="ankle sprain",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_son_adult_age_allowed(self, theorem):
        """'My son is 19' — over the minimum age, so Theorem should see him."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            child_related=True,
            age=19,
            reason="ankle injury",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_adult_ankle_assessment_first(self, theorem):
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="hurt my ankle playing football",
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST
        _assert_no_overconfidence(result.response_text)
        _assert_no_speculative_recommendation(result.response_text)

    def test_what_do_you_recommend_blocked(self, theorem):
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            original_text="what do you recommend for my ankle",
            reason="ankle injury",
        )
        assert result.action == SUITABILITY_CLINICIAN_DECIDES

    def test_acupuncture_availability_answered(self, theorem):
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            original_text="do you offer acupuncture",
        )
        assert result.action == SERVICE_AVAILABLE_ONLY


# ── 8. is_suitability_question helper ────────────────────────────────────────

class TestIsSuitabilityQuestion:
    def test_what_do_you_recommend(self):
        assert is_suitability_question("what do you recommend for my ankle")

    def test_would_acupuncture_work(self):
        assert is_suitability_question("would acupuncture work for me")

    def test_do_you_think(self):
        assert is_suitability_question("do you think that would help")

    def test_should_i_try(self):
        assert is_suitability_question("should i try shockwave therapy")

    def test_what_would_be_best(self):
        assert is_suitability_question("what would be best for my back")

    def test_which_is_better(self):
        assert is_suitability_question("which is better acupuncture or physio")

    def test_plain_service_fit_not_suitability(self):
        assert not is_suitability_question("my son hurt his ankle can you help")

    def test_booking_not_suitability(self):
        assert not is_suitability_question("i'd like to book an appointment")

    def test_availability_not_suitability(self):
        assert not is_suitability_question("do you offer acupuncture")


# ── 9. is_availability_question + detect_service_name ────────────────────────

class TestAvailabilityHelpers:
    def test_do_you_offer(self):
        assert is_availability_question("do you offer acupuncture")

    def test_do_you_have(self):
        assert is_availability_question("do you have shockwave therapy")

    def test_is_that_available(self):
        assert is_availability_question("is acupuncture available at your clinic")

    def test_plain_question_not_availability(self):
        assert not is_availability_question("my ankle hurts can you help")

    def test_detect_acupuncture(self):
        assert detect_service_name("do you offer acupuncture") == "Acupuncture"

    def test_detect_shockwave(self):
        assert detect_service_name("is shockwave therapy available") == "Shockwave Therapy"

    def test_detect_laser(self):
        assert detect_service_name("do you have laser therapy") == "Class IV Laser Therapy"

    def test_auricular_beats_plain_acupuncture(self):
        # Longer match wins
        assert detect_service_name("auricular acupuncture") == "Auricular Acupuncture"

    def test_no_service_returns_none(self):
        assert detect_service_name("my ankle hurts") is None


# ── 10. Template regression: no speculative wording ──────────────────────────

class TestTemplateRegression:
    def test_assessment_first_no_typically(self):
        resp = assessment_first_response("ankle injury")
        assert "typically" not in resp.lower()

    def test_assessment_first_no_modality_chain(self):
        resp = assessment_first_response("ankle injury")
        _assert_no_speculative_recommendation(resp)

    def test_assessment_first_mentions_assessment(self):
        assert "assessment" in assessment_first_response("knee pain").lower()

    def test_team_confirmation_no_typically(self):
        resp = _team_confirmation_response()
        assert "typically" not in resp.lower()

    def test_clinician_decides_no_speculation(self):
        resp = clinician_decides_response()
        _assert_no_speculative_recommendation(resp)

    def test_clinician_decides_references_clinician(self):
        resp = clinician_decides_response()
        assert "clinician" in resp.lower()
