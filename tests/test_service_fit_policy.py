# tests/test_service_fit_policy.py
"""
Focused tests for service_fit_policy.py — the deterministic service-fit
answer policy evaluator.

Verifies:
- child + age missing → ASK_CHILD_AGE
- child + disallowed age → POLICY_DISALLOW
- child + allowed age → ALLOW_ASSESSMENT_FIRST
- adult MSK condition → ALLOW_ASSESSMENT_FIRST
- adult unsupported condition → REQUIRE_TEAM_CONFIRMATION
- no reason → INSUFFICIENT_KNOWLEDGE_SAFE_REPLY
- no overconfident wording in any template
- condition_is_supported grounded in clinic FAQ
"""
import pytest
from app.media_streams.service_fit_policy import (
    evaluate_service_fit,
    condition_is_supported,
    assessment_first_response,
    POLICY_DISALLOW,
    ASK_CHILD_AGE,
    ALLOW_ASSESSMENT_FIRST,
    REQUIRE_TEAM_CONFIRMATION,
    INSUFFICIENT_KNOWLEDGE_SAFE_REPLY,
)


# ── Clinic fixtures ────────────────────────────────────────────────────────────

def _clinic_no_children() -> dict:
    """Theorem-like: adults only, no children at all."""
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
    }


def _clinic_children_from_16() -> dict:
    """Children accepted from age 16 only."""
    return {
        "patient_policies": {"no_children": False, "min_patient_age": 16},
        "faq": {
            "children_policy": "We see patients aged 16 and over.",
        },
    }


def _clinic_children_all_ages() -> dict:
    """Children accepted, no age restriction."""
    return {
        "patient_policies": {"no_children": False},
        "faq": {
            "conditions_treated": (
                "We treat musculoskeletal conditions including sports injuries, ankle sprains."
            ),
        },
    }


def _clinic_policy_unconfigured() -> dict:
    """Children policy not set (None)."""
    return {
        "patient_policies": {},
        "faq": {
            "conditions_treated": (
                "Sports injuries, ankle pain, back pain."
            ),
        },
    }


def _empty_session() -> dict:
    return {}


# ── Child + age missing ────────────────────────────────────────────────────────

class TestChildAgeMissing:
    def test_no_children_policy_true(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == POLICY_DISALLOW

    def test_no_children_uses_faq_text(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert "adults only" in result.response_text.lower()

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
        assert result.pending_followup == "child_age"

    def test_ask_age_response_contains_child_word(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=None,
            relationship="son",
        )
        assert "son" in result.response_text.lower()

    def test_no_confident_yes_when_asking_age(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_policy_unconfigured(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == ASK_CHILD_AGE
        _assert_no_overconfidence(result.response_text)


# ── Child + disallowed age ────────────────────────────────────────────────────

class TestChildDisallowedAge:
    def test_under_min_age_disallowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=8,
        )
        assert result.action == POLICY_DISALLOW

    def test_disallow_response_uses_faq_text(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=8,
        )
        assert "16" in result.response_text

    def test_no_booking_language_in_disallow(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=5,
        )
        assert result.action == POLICY_DISALLOW
        assert "take a few details" not in result.response_text.lower()
        assert "book" not in result.response_text.lower()

    def test_no_overconfidence_in_disallow(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=10,
        )
        _assert_no_overconfidence(result.response_text)


# ── Child + allowed age ────────────────────────────────────────────────────────

class TestChildAllowedAge:
    def test_age_at_min_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle sprain",
            child_related=True,
            age=16,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_age_above_min_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="knee pain",
            child_related=True,
            age=17,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_children_all_ages_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_all_ages(),
            reason="ankle sprain",
            child_related=True,
            age=10,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_allowed_response_assessment_first(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=16,
        )
        assert "assessment" in result.response_text.lower()

    def test_no_overconfidence_in_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="sports injury",
            child_related=True,
            age=17,
        )
        _assert_no_overconfidence(result.response_text)


# ── Adult MSK service-fit ──────────────────────────────────────────────────────

class TestAdultServiceFit:
    def test_ankle_injury_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=False,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_back_pain_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="back pain",
            child_related=False,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_sports_injury_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="football sports injury",
            child_related=False,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST

    def test_ankle_response_assessment_first(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="hurt my ankle playing football",
            child_related=False,
        )
        assert "assessment" in result.response_text.lower()

    def test_no_overconfidence_adult_allowed(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="shoulder pain",
            child_related=False,
        )
        _assert_no_overconfidence(result.response_text)

    def test_adult_no_reason_insufficient_knowledge(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="",
            child_related=False,
        )
        assert result.action == INSUFFICIENT_KNOWLEDGE_SAFE_REPLY
        assert "assessment" in result.response_text.lower()


# ── Unsupported / outside scope ────────────────────────────────────────────────

class TestUnsupportedCondition:
    def test_dental_not_supported(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="toothache",
            child_related=False,
        )
        assert result.action == REQUIRE_TEAM_CONFIRMATION

    def test_team_confirmation_no_overconfidence(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="toothache",
            child_related=False,
        )
        _assert_no_overconfidence(result.response_text)

    def test_team_confirmation_response_bounded(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="toothache",
            child_related=False,
        )
        # Should still mention assessment as first step
        assert "assessment" in result.response_text.lower()


# ── condition_is_supported ────────────────────────────────────────────────────

class TestConditionIsSupported:
    def test_ankle_supported(self):
        assert condition_is_supported("ankle injury", _clinic_no_children())

    def test_back_pain_supported(self):
        assert condition_is_supported("back pain", _clinic_no_children())

    def test_sports_supported(self):
        assert condition_is_supported("sports injury", _clinic_no_children())

    def test_knee_supported(self):
        assert condition_is_supported("knee problem", _clinic_no_children())

    def test_dental_not_supported(self):
        assert not condition_is_supported("toothache", _clinic_no_children())

    def test_empty_reason_not_supported(self):
        assert not condition_is_supported("", _clinic_no_children())


# ── Response template confidence cap ──────────────────────────────────────────

class TestConfidenceCap:
    def test_assessment_first_no_overconfidence(self):
        _assert_no_overconfidence(assessment_first_response("ankle injury"))

    def test_assessment_first_empty_no_overconfidence(self):
        _assert_no_overconfidence(assessment_first_response(""))

    def test_disallow_theorem_uses_faq(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_no_children(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == POLICY_DISALLOW
        assert "adults only" in result.response_text.lower()

    def test_ask_age_no_booking_offer(self):
        result = evaluate_service_fit(
            session=_empty_session(),
            clinic=_clinic_children_from_16(),
            reason="ankle injury",
            child_related=True,
            age=None,
        )
        assert result.action == ASK_CHILD_AGE
        assert "take a few details" not in result.response_text.lower()
        assert "book" not in result.response_text.lower()


# ── Theorem Health real-world scenarios ───────────────────────────────────────

class TestTheoremScenarios:
    """Scenarios against the actual Theorem Health clinic config."""

    @pytest.fixture
    def theorem(self):
        from app.clinic_config import get_clinic
        return get_clinic("theorem")

    def test_son_ankle_injury_no_age(self, theorem):
        """'My son hurt his ankle — do you treat children?' → POLICY_DISALLOW (no_children=True)"""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="son hurt his ankle playing football",
            child_related=True,
            age=None,
            relationship="son",
        )
        assert result.action == POLICY_DISALLOW
        assert "adults only" in result.response_text.lower()

    def test_son_ankle_injury_with_age(self, theorem):
        """Even with age known, Theorem disallows all children (no_children=True)."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="ankle sprain",
            child_related=True,
            age=15,
        )
        assert result.action == POLICY_DISALLOW

    def test_adult_ankle_assessment_first(self, theorem):
        """Adult ankle injury → assessment-first, not overconfident."""
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="hurt my ankle playing football",
            child_related=False,
        )
        assert result.action == ALLOW_ASSESSMENT_FIRST
        _assert_no_overconfidence(result.response_text)

    def test_adult_no_reason_safe_reply(self, theorem):
        result = evaluate_service_fit(
            session={},
            clinic=theorem,
            reason="",
            child_related=False,
        )
        assert result.action == INSUFFICIENT_KNOWLEDGE_SAFE_REPLY
        _assert_no_overconfidence(result.response_text)


# ── Helper ────────────────────────────────────────────────────────────────────

_OVERCONFIDENT_PHRASES = (
    "yes, absolutely",
    "yes absolutely",
    "that's exactly the kind",
    "absolutely — that",
    "absolutely — we",
    "absolutely, yes",
)


def _assert_no_overconfidence(text: str) -> None:
    t = text.lower()
    for phrase in _OVERCONFIDENT_PHRASES:
        assert phrase not in t, (
            f"Overconfident phrase {phrase!r} found in response: {text!r}"
        )
