"""
Tests for the deterministic first-turn policy gate.

All tests operate on evaluate_policy_gate() directly — pure function,
no flow setup required.
"""
from __future__ import annotations

import pytest

from app.media_streams.policy_gate import (
    evaluate_policy_gate,
    ASK_CHILD_AGE,
    SERVICE_FIT_DISALLOW,
    SERVICE_FIT_ALLOW,
    NO_POLICY_GATE,
)


# ── Minimal session/clinic builders ─────────────────────────────────────────────

def _session(
    child_related: bool = False,
    service_fit: bool = False,
    booking: bool = False,
    faq: bool = False,
    age: int | None = None,
    relationship: str = "son",
) -> dict:
    s: dict = {"_first_turn_extracted": True}
    s["first_turn_child_related"]  = child_related
    s["first_turn_service_fit"]    = service_fit
    s["first_turn_booking_signal"] = booking
    s["first_turn_faq_signal"]     = faq
    if age is not None:
        s["first_turn_age"] = age
    if relationship:
        s["first_turn_patient_relationship"] = relationship
    return s


def _clinic(
    no_children: bool | None = None,
    min_patient_age: int | None = None,
    children_policy_text: str | None = None,
) -> dict:
    c: dict = {"patient_policies": {}}
    if no_children is not None:
        c["patient_policies"]["no_children"] = no_children
    if min_patient_age is not None:
        c["patient_policies"]["min_patient_age"] = min_patient_age
    if children_policy_text:
        c["faq"] = {"children_policy": children_policy_text}
    return c


# ── 1. NO_POLICY_GATE cases ──────────────────────────────────────────────────────

class TestNoPolicyGate:
    def test_no_extraction_returns_no_gate(self):
        result = evaluate_policy_gate({}, _clinic())
        assert result.action == NO_POLICY_GATE

    def test_not_child_related_returns_no_gate(self):
        s = _session(child_related=False, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == NO_POLICY_GATE

    def test_child_related_but_no_intent_returns_no_gate(self):
        # child_related=True but no service_fit / booking / faq
        s = _session(child_related=True, service_fit=False, booking=False, faq=False)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == NO_POLICY_GATE

    def test_adult_booking_returns_no_gate(self):
        s = _session(child_related=False, booking=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == NO_POLICY_GATE


# ── 2. no_children=True: ask age first (son/daughter could be an adult) ─────────

class TestNoChildrenTrueAskAgeFirst:
    """
    no_children=True means 'adults only'.
    When age is unknown, we must ask — the 'son/daughter' could be 19.
    Disallow only when age is confirmed below the adult threshold.
    """

    def test_child_service_fit_age_unknown_asks_age(self):
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == ASK_CHILD_AGE

    def test_child_booking_age_unknown_asks_age(self):
        s = _session(child_related=True, booking=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == ASK_CHILD_AGE

    def test_child_faq_age_unknown_asks_age(self):
        s = _session(child_related=True, faq=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == ASK_CHILD_AGE

    def test_pending_followup_set(self):
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.pending_followup == "child_age"

    def test_age_question_text_present(self):
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.response_text and len(result.response_text) > 5

    def test_minor_age_disallows(self):
        # age=15 < default adult_threshold (18) → disallow
        s = _session(child_related=True, service_fit=True, age=15)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == SERVICE_FIT_DISALLOW

    def test_adult_age_allows(self):
        # age=19 >= adult_threshold (18) → the "son" is an adult
        s = _session(child_related=True, service_fit=True, age=19)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == SERVICE_FIT_ALLOW

    def test_exactly_at_threshold_allows(self):
        s = _session(child_related=True, service_fit=True, age=18)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == SERVICE_FIT_ALLOW

    def test_custom_adult_threshold_respected(self):
        # clinic sets adult_age_threshold=16
        c = _clinic(no_children=True)
        c["patient_policies"]["adult_age_threshold"] = 16
        s = _session(child_related=True, service_fit=True, age=16)
        result = evaluate_policy_gate(s, c)
        assert result.action == SERVICE_FIT_ALLOW

    def test_uses_configured_faq_text_when_disallowing(self):
        policy_text = "We only treat adults, sorry."
        s = _session(child_related=True, service_fit=True, age=10)
        result = evaluate_policy_gate(
            s, _clinic(no_children=True, children_policy_text=policy_text)
        )
        assert result.action == SERVICE_FIT_DISALLOW
        assert result.response_text == policy_text


# ── 3. SERVICE_FIT_DISALLOW: clinic has min_patient_age and age is too young ────

class TestDisallowMinAge:
    def test_age_below_min_disallows(self):
        s = _session(child_related=True, service_fit=True, age=8)
        result = evaluate_policy_gate(s, _clinic(no_children=False, min_patient_age=16))
        assert result.action == SERVICE_FIT_DISALLOW

    def test_age_equal_to_min_allows(self):
        s = _session(child_related=True, service_fit=True, age=16)
        result = evaluate_policy_gate(s, _clinic(no_children=False, min_patient_age=16))
        assert result.action == SERVICE_FIT_ALLOW

    def test_age_above_min_allows(self):
        s = _session(child_related=True, service_fit=True, age=17)
        result = evaluate_policy_gate(s, _clinic(no_children=False, min_patient_age=16))
        assert result.action == SERVICE_FIT_ALLOW


# ── 4. ASK_CHILD_AGE cases ───────────────────────────────────────────────────────

class TestAskChildAge:
    def test_no_children_none_age_unknown_asks_age(self):
        # Policy not configured, age unknown
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic())  # no patient_policies
        assert result.action == ASK_CHILD_AGE
        assert result.pending_followup == "child_age"
        assert result.response_text is not None

    def test_no_children_false_min_age_configured_age_unknown_asks_age(self):
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=False, min_patient_age=16))
        assert result.action == ASK_CHILD_AGE

    def test_age_question_uses_son_pronoun(self):
        s = _session(child_related=True, service_fit=True, relationship="son")
        result = evaluate_policy_gate(s, _clinic())
        assert "your son" in result.response_text

    def test_age_question_uses_daughter_pronoun(self):
        s = _session(child_related=True, service_fit=True, relationship="daughter")
        result = evaluate_policy_gate(s, _clinic())
        assert "your daughter" in result.response_text

    def test_age_question_uses_generic_for_unknown_relationship(self):
        s = _session(child_related=True, service_fit=True, relationship="friend")
        result = evaluate_policy_gate(s, _clinic())
        assert "your child" in result.response_text


# ── 5. SERVICE_FIT_ALLOW cases ───────────────────────────────────────────────────

class TestServiceFitAllow:
    def test_no_children_false_no_min_age_allows(self):
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, _clinic(no_children=False))
        assert result.action == SERVICE_FIT_ALLOW

    def test_no_children_false_age_above_min_allows(self):
        s = _session(child_related=True, service_fit=True, age=18)
        result = evaluate_policy_gate(s, _clinic(no_children=False, min_patient_age=16))
        assert result.action == SERVICE_FIT_ALLOW

    def test_no_children_none_age_known_allows(self):
        # Policy unconfigured but age is already known from extraction
        s = _session(child_related=True, service_fit=True, age=14)
        result = evaluate_policy_gate(s, _clinic())
        assert result.action == SERVICE_FIT_ALLOW


# ── 6. Theorem Health integration: must always disallow children ─────────────────

class TestTheoremHealthPolicy:
    """
    Theorem has no_children=True (adults only).

    Age-unknown → ASK_CHILD_AGE (the caller's son could be 19).
    Age confirmed minor (< 18) → SERVICE_FIT_DISALLOW with FAQ text.
    Age confirmed adult (>= 18) → SERVICE_FIT_ALLOW.
    """

    def _theorem_clinic(self) -> dict:
        from app.clinic_config import get_clinic
        return get_clinic("theorem")

    def test_theorem_child_age_unknown_asks_age(self):
        """Son/daughter with no age — must ask age, not immediately decline."""
        s = _session(child_related=True, service_fit=True)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == ASK_CHILD_AGE

    def test_theorem_child_booking_age_unknown_asks_age(self):
        s = _session(child_related=True, booking=True)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == ASK_CHILD_AGE

    def test_theorem_minor_age_declined(self):
        s = _session(child_related=True, service_fit=True, age=12)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == SERVICE_FIT_DISALLOW

    def test_theorem_minor_age_15_declined(self):
        s = _session(child_related=True, service_fit=True, age=15)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == SERVICE_FIT_DISALLOW

    def test_theorem_adult_son_age_19_allowed(self):
        s = _session(child_related=True, service_fit=True, age=19)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == SERVICE_FIT_ALLOW

    def test_theorem_response_text_is_configured_faq_when_minor(self):
        s = _session(child_related=True, service_fit=True, age=10)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        expected = self._theorem_clinic().get("faq", {}).get("children_policy", "")
        assert result.response_text == expected
        assert "adult" in result.response_text.lower()

    def test_theorem_adult_booking_not_gated(self):
        s = _session(child_related=False, booking=True)
        result = evaluate_policy_gate(s, self._theorem_clinic())
        assert result.action == NO_POLICY_GATE


# ── 7. Mixed booking + child + service-fit ───────────────────────────────────────

class TestMixedBookingChildServiceFit:
    def test_all_three_signals_no_children_true_age_unknown(self):
        # Age unknown: must ask age first (son could be 19)
        s = _session(child_related=True, service_fit=True, booking=True)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == ASK_CHILD_AGE

    def test_all_three_signals_no_children_true_minor_age(self):
        # Age known and minor: disallow
        s = _session(child_related=True, service_fit=True, booking=True, age=12)
        result = evaluate_policy_gate(s, _clinic(no_children=True))
        assert result.action == SERVICE_FIT_DISALLOW

    def test_all_three_signals_no_children_none_asks_age(self):
        s = _session(child_related=True, service_fit=True, booking=True)
        result = evaluate_policy_gate(s, _clinic())
        assert result.action == ASK_CHILD_AGE

    def test_all_three_signals_no_children_false_no_min_allows(self):
        s = _session(child_related=True, service_fit=True, booking=True)
        result = evaluate_policy_gate(s, _clinic(no_children=False))
        assert result.action == SERVICE_FIT_ALLOW
