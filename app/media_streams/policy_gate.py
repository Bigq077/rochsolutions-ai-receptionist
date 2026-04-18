# app/media_streams/policy_gate.py
"""
Deterministic first-turn policy gate.

Consumes extracted first-turn signals already in session (set by
first_turn_extractor.py) together with clinic config to decide whether
a policy-sensitive case needs special handling *before* normal intent
routing runs.

Pure evaluation — no I/O, no side effects, no session mutations.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ── Gate outcome constants ──────────────────────────────────────────────────────

ASK_CHILD_AGE        = "ASK_CHILD_AGE"
SERVICE_FIT_DISALLOW = "SERVICE_FIT_DISALLOW"
SERVICE_FIT_ALLOW    = "SERVICE_FIT_ALLOW"
NO_POLICY_GATE       = "NO_POLICY_GATE"


class PolicyGateResult:
    __slots__ = ("action", "response_text", "pending_followup")

    def __init__(
        self,
        action: str,
        response_text: Optional[str] = None,
        pending_followup: Optional[str] = None,
    ) -> None:
        self.action           = action
        self.response_text    = response_text
        self.pending_followup = pending_followup

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"PolicyGateResult(action={self.action!r}, "
            f"pending_followup={self.pending_followup!r})"
        )


_NO_GATE = PolicyGateResult(action=NO_POLICY_GATE)


# ── Public API ──────────────────────────────────────────────────────────────────

def evaluate_policy_gate(
    session: Dict[str, Any],
    clinic: Dict[str, Any],
) -> PolicyGateResult:
    """
    Evaluate whether extracted first-turn signals require a policy gate.

    Only acts when ``session["_first_turn_extracted"]`` is set.
    Fires only for child-related utterances that carry an intent signal
    (service_fit, booking, or FAQ).

    Returns ``NO_POLICY_GATE`` for non-sensitive cases so the caller can
    continue to normal routing without any conditional branching.

    Does NOT mutate session or clinic.
    """
    if not session.get("_first_turn_extracted"):
        return _NO_GATE

    child_related  = session.get("first_turn_child_related", False)
    service_fit    = session.get("first_turn_service_fit",   False)
    booking_signal = session.get("first_turn_booking_signal", False)
    faq_signal     = session.get("first_turn_faq_signal",    False)
    age: Optional[int] = session.get("first_turn_age")

    # Gate fires only when child-related AND at least one intent signal present
    if not child_related:
        return _NO_GATE
    if not (service_fit or booking_signal or faq_signal):
        return _NO_GATE

    policies    = clinic.get("patient_policies", {})
    no_children = policies.get("no_children")   # True | False | None
    relationship = session.get("first_turn_patient_relationship", "child")

    # ── Clinic does not treat minor patients (no_children=True) ─────────────
    # "adults only" does NOT mean "disallow immediately" when age is unknown.
    # "My son" could be 19 years old.  Ask age first; disallow only when the
    # age is confirmed to be below the adult threshold.
    if no_children is True:
        adult_threshold: int = policies.get("adult_age_threshold", 18)
        if age is None:
            q = _age_question(relationship)
            return PolicyGateResult(
                action=ASK_CHILD_AGE,
                response_text=q,
                pending_followup="child_age",
            )
        if age < adult_threshold:
            _default_decline = (
                "I\u2019m sorry \u2014 the clinic sees adults only and doesn\u2019t "
                "currently offer paediatric physiotherapy."
            )
            response = clinic.get("faq", {}).get("children_policy", _default_decline)
            return PolicyGateResult(action=SERVICE_FIT_DISALLOW, response_text=response)
        # age >= adult_threshold: the "son/daughter" is an adult patient → allow
        return PolicyGateResult(action=SERVICE_FIT_ALLOW)

    # ── Clinic accepts children (no_children=False) ───────────────────────────
    if no_children is False:
        min_age: Optional[int] = policies.get("min_patient_age")
        if min_age is not None:
            if age is not None:
                if age < min_age:
                    _default_min = (
                        f"I\u2019m sorry \u2014 we see patients aged {min_age} and over."
                    )
                    response = clinic.get("faq", {}).get(
                        "children_policy", _default_min
                    )
                    return PolicyGateResult(
                        action=SERVICE_FIT_DISALLOW, response_text=response
                    )
                # age >= min_age → allowed
                return PolicyGateResult(action=SERVICE_FIT_ALLOW)
            # min_age configured but age unknown → ask
            q = _age_question(relationship)
            return PolicyGateResult(
                action=ASK_CHILD_AGE,
                response_text=q,
                pending_followup="child_age",
            )
        # no_children=False and no min_age → children freely accepted
        return PolicyGateResult(action=SERVICE_FIT_ALLOW)

    # ── Policy not configured (no_children is None) ───────────────────────────
    # Cannot give a confident capability answer without age information.
    if age is not None:
        # Age known but policy unconfigured — allow; normal routing can proceed
        return PolicyGateResult(action=SERVICE_FIT_ALLOW)

    q = _age_question(relationship)
    return PolicyGateResult(
        action=ASK_CHILD_AGE,
        response_text=q,
        pending_followup="child_age",
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _age_question(relationship: str) -> str:
    _PRONOUNS: Dict[str, str] = {
        "son":      "your son",
        "daughter": "your daughter",
        "teenager": "your teenager",
    }
    pronoun = _PRONOUNS.get(relationship, "your child")
    return (
        f"Of course \u2014 just so I can make sure we can help, "
        f"how old is {pronoun}?"
    )
