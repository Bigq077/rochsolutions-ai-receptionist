# app/media_streams/service_fit_policy.py
"""
Deterministic service-fit answer policy evaluator.

Sits between extracted first-turn signals and the response text for
service-fit / eligibility questions.  Ensures answers are grounded in
clinic-configured knowledge and prevents overconfident capability claims.

Pure evaluation — no I/O, no session mutations, no side effects.

Source of truth
---------------
- clinic["patient_policies"]          age/child eligibility rules
- clinic["faq"]["conditions_treated"] explicitly listed treatable conditions
- clinic["faq"]["children_policy"]    configured decline text for children
- clinic["services"]                  list of services offered
"""
from __future__ import annotations

import re as _re
from typing import Any, Dict, List, Optional

# ── Outcome constants ──────────────────────────────────────────────────────────

POLICY_DISALLOW                   = "POLICY_DISALLOW"
ASK_CHILD_AGE                     = "ASK_CHILD_AGE"
ALLOW_ASSESSMENT_FIRST            = "ALLOW_ASSESSMENT_FIRST"
REQUIRE_TEAM_CONFIRMATION         = "REQUIRE_TEAM_CONFIRMATION"
INSUFFICIENT_KNOWLEDGE_SAFE_REPLY = "INSUFFICIENT_KNOWLEDGE_SAFE_REPLY"


class ServiceFitPolicyResult:
    __slots__ = ("action", "response_text", "pending_followup")

    def __init__(
        self,
        action: str,
        response_text: str = "",
        pending_followup: Optional[str] = None,
    ) -> None:
        self.action           = action
        self.response_text    = response_text
        self.pending_followup = pending_followup

    def __repr__(self) -> str:
        return f"ServiceFitPolicyResult(action={self.action!r})"


# ── Condition keyword set ──────────────────────────────────────────────────────
# Grounded in Theorem Health's FAQ "conditions_treated" which explicitly covers:
# back pain, sciatica, neck pain, shoulder pain (incl. frozen shoulder, rotator
# cuff), hip and knee problems, sports injuries, ankle sprains, plantar fasciitis,
# Achilles tendinopathy, tennis/golfer's elbow, post-op rehab, muscle strains,
# joint stiffness.  Also covers Theorem's broader service list.

_SUPPORTED_KEYWORDS: frozenset = frozenset({
    # Pain / symptom descriptors
    "pain", "ache", "aching", "hurt", "hurts", "injury", "injured",
    "sore", "stiff", "stiffness", "swollen", "swelling", "sprain",
    "strain", "fracture", "broken", "torn", "pulled",
    # Body parts — all within physio scope
    "knee", "shoulder", "back", "neck", "hip", "ankle", "wrist", "elbow",
    "foot", "feet", "heel", "toe", "finger", "thumb", "leg", "thigh",
    "calf", "hamstring", "quad", "quadricep", "arm", "muscle", "joint",
    "spine", "disc", "nerve",
    # Specific conditions from conditions_treated FAQ
    "sciatica", "plantar", "fasciitis", "achilles", "tendon", "tendinopathy",
    "tendinitis", "frozen", "rotator", "cuff", "ligament", "cartilage",
    "meniscus", "bursitis", "whiplash", "headache", "migraine",
    # Activity / sport context
    "sports", "sport", "running", "football", "rugby", "tennis", "golf",
    "cycling", "swimming", "gym", "workout", "training", "exercise",
    "posture", "postural",
    # Treatment / service keywords
    "physio", "physiotherapy", "physiotherapist", "rehab", "rehabilitation",
    "assessment", "treatment", "acupuncture", "shockwave", "laser",
    # Post-surgical
    "post-op", "post-operative", "post operative", "surgery", "operation",
    "recovery",
})


def condition_is_supported(reason: str, clinic: Dict[str, Any]) -> bool:
    """
    Return True if the reason/condition text is within the clinic's known scope.

    Checks the clinic's faq.conditions_treated text first (if present), then
    falls back to the keyword set above.  Uses word-level matching to avoid
    false positives like 'ache' matching 'toothache'.
    """
    r = reason.lower()
    # Normalise to bare word tokens (strip punctuation)
    reason_words = frozenset(_re.sub(r"[^\w\s]", " ", r).split())

    # Primary: check the clinic's own conditions_treated FAQ text
    conditions_text = clinic.get("faq", {}).get("conditions_treated", "")
    if conditions_text:
        faq_words = frozenset(
            _re.sub(r"[^\w\s]", " ", conditions_text.lower()).split()
        )
        # Overlap on words ≥ 4 chars to filter noise
        sig_reason = {w for w in reason_words if len(w) >= 4}
        sig_faq    = {w for w in faq_words    if len(w) >= 4}
        if sig_reason & sig_faq:
            return True

    # Fallback: keyword set — word-level match only
    return bool(reason_words & _SUPPORTED_KEYWORDS)


# ── Response template builders ─────────────────────────────────────────────────

def _age_question(relationship: str) -> str:
    _PRONOUNS: Dict[str, str] = {
        "son":      "your son",
        "daughter": "your daughter",
        "teenager": "your teenager",
        "teen":     "your teenager",
        "baby":     "your baby",
        "toddler":  "your toddler",
        "child":    "your child",
    }
    pronoun = _PRONOUNS.get(relationship.lower(), "your child")
    return (
        f"Of course \u2014 just so I can make sure we can help, "
        f"how old is {pronoun}?"
    )


def assessment_first_response(reason: str = "") -> str:
    """
    Bounded, clinic-safe, assessment-first response.

    Deliberately avoids 'Yes, absolutely' / 'Yes, certainly' phrasing unless
    the evaluator has confirmed an explicit allow outcome.
    """
    if reason:
        return (
            "That\u2019s something the clinic can look at. "
            "They\u2019d typically start with a physiotherapy assessment "
            "to understand what\u2019s going on and put together a plan from there. "
            "If you\u2019d like to get that arranged, I can take a few details."
        )
    return (
        "The clinic would usually start with a physiotherapy assessment "
        "to understand the issue and work out the best approach from there. "
        "If you\u2019d like to get that arranged, I can take a few details."
    )


def _team_confirmation_response() -> str:
    return (
        "That\u2019s something the team would be best placed to advise on. "
        "The usual first step is a physiotherapy assessment, "
        "and they\u2019ll confirm the right approach from there. "
        "If you\u2019d like to get that arranged, I can take a few details."
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def evaluate_service_fit(
    session: Dict[str, Any],
    clinic: Dict[str, Any],
    reason: str = "",
    child_related: bool = False,
    age: Optional[int] = None,
    relationship: str = "child",
) -> ServiceFitPolicyResult:
    """
    Evaluate the correct response action for a service-fit or eligibility question.

    Consumes clinic config (patient_policies, faq, services) to produce a
    deterministic, confidence-capped outcome.  Never fabricates capability claims.

    Actions in priority order:
      POLICY_DISALLOW                   → clinic cannot see this patient
      ASK_CHILD_AGE                     → age required before any answer
      ALLOW_ASSESSMENT_FIRST            → condition supported; assessment-first reply
      REQUIRE_TEAM_CONFIRMATION         → condition outside known scope; safe reply
      INSUFFICIENT_KNOWLEDGE_SAFE_REPLY → no reason given; generic safe reply
    """
    policies   = clinic.get("patient_policies", {})
    no_children: Optional[bool] = policies.get("no_children")
    min_age: Optional[int]      = policies.get("min_patient_age")

    # ── 1. Child / minor patient gate ───────────────────────────────────────────
    if child_related:
        # Clinic explicitly adults-only
        if no_children is True:
            resp = clinic.get("faq", {}).get("children_policy") or (
                "I\u2019m sorry \u2014 the clinic sees adults only and doesn\u2019t "
                "currently offer paediatric physiotherapy."
            )
            return ServiceFitPolicyResult(action=POLICY_DISALLOW, response_text=resp)

        # Clinic accepts patients above a minimum age
        if no_children is False and min_age is not None:
            if age is None:
                return ServiceFitPolicyResult(
                    action=ASK_CHILD_AGE,
                    response_text=_age_question(relationship),
                    pending_followup="child_age",
                )
            if age < min_age:
                resp = clinic.get("faq", {}).get("children_policy") or (
                    f"I\u2019m sorry \u2014 the clinic sees patients aged {min_age} and over."
                )
                return ServiceFitPolicyResult(action=POLICY_DISALLOW, response_text=resp)
            # age >= min_age → fall through to condition check

        # Policy unconfigured (no_children is None) and age unknown
        if no_children is None and age is None:
            return ServiceFitPolicyResult(
                action=ASK_CHILD_AGE,
                response_text=_age_question(relationship),
                pending_followup="child_age",
            )

        # no_children is None and age is known, or no_children=False with no min_age
        # → proceed to condition check below (cautiously)

    # ── 2. Condition-based evaluation ───────────────────────────────────────────
    if not reason:
        return ServiceFitPolicyResult(
            action=INSUFFICIENT_KNOWLEDGE_SAFE_REPLY,
            response_text=assessment_first_response(""),
        )

    if condition_is_supported(reason, clinic):
        return ServiceFitPolicyResult(
            action=ALLOW_ASSESSMENT_FIRST,
            response_text=assessment_first_response(reason),
        )

    return ServiceFitPolicyResult(
        action=REQUIRE_TEAM_CONFIRMATION,
        response_text=_team_confirmation_response(),
    )
