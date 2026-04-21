# app/media_streams/service_fit_policy.py
"""
Deterministic service-fit answer policy evaluator.

Sits between extracted first-turn signals and the response text for
service-fit / eligibility questions.  Ensures answers are grounded in
clinic-configured knowledge and prevents overconfident capability claims.

Source of truth
---------------
- clinic["patient_policies"]          age/child eligibility rules
- clinic["faq"]["conditions_treated"] explicitly listed treatable conditions
- clinic["faq"]["children_policy"]    configured decline text for children
- clinic["services"]                  list of services offered

Outcome priority order
----------------------
POLICY_DISALLOW                   → clinic cannot see this patient (age / policy)
ASK_CHILD_AGE                     → age required before any answer
SERVICE_AVAILABLE_ONLY            → caller asked "do you offer X?" — availability only
SUITABILITY_CLINICIAN_DECIDES     → "what do you recommend / would X work?" — no recommendation
ALLOW_ASSESSMENT_FIRST            → condition supported; bounded assessment-first reply
REQUIRE_TEAM_CONFIRMATION         → condition outside known scope; safe team reply
INSUFFICIENT_KNOWLEDGE_SAFE_REPLY → no reason given; generic safe reply
"""
from __future__ import annotations

import logging
import re as _re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Outcome constants ──────────────────────────────────────────────────────────

POLICY_DISALLOW                   = "POLICY_DISALLOW"
ASK_CHILD_AGE                     = "ASK_CHILD_AGE"
SERVICE_AVAILABLE_ONLY            = "SERVICE_AVAILABLE_ONLY"
SUITABILITY_CLINICIAN_DECIDES     = "SUITABILITY_CLINICIAN_DECIDES"
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


# ── Suitability / recommendation question detection ───────────────────────────
# These phrases signal that the caller is asking the receptionist to recommend
# or evaluate a treatment modality — which is a clinical decision, not a
# receptionist decision.  Must block speculative modality recommendations.

_SUITABILITY_PHRASES: tuple = (
    "what do you recommend", "what would you recommend",
    "do you recommend", "would you recommend",
    "would it work for me", "would that work for me",
    "work for me", "would it still work", "would that still work",
    "would it help me", "would that help me",
    "would it help", "would that help",
    "do you think", "what do you think",
    "what do you suggest", "what would you suggest",
    "what would be best", "what's best for", "whats best for",
    "would i be better", "would i still", "would i be okay",
    "should i do", "should i try", "should i get",
    "is that the right", "is this the right",
    "is that right for me", "is this right for me",
    "what would help most", "what would help me the most",
    "what would help", "what will help",
    "which is better", "which would be better",
    "what should i do", "what should i try",
    "what would be better", "what would suit",
    "what treatment", "what would the treatment",
    "would you use", "do you use",
    "what would they do for", "what would they use",
    "what would they recommend",
)


def is_suitability_question(text: str) -> bool:
    """Return True if the text is asking for a clinical recommendation or treatment opinion."""
    t = text.lower()
    return any(p in t for p in _SUITABILITY_PHRASES)


# ── Treatment-suitability / best-starting-point detection ─────────────────────
# Broader than is_suitability_question: catches the "would X work for Y",
# "should I book X or something else", "what should I book for Y" family.
# Callers asking this want operational guidance — not a service description and
# not a reassurance answer from an earlier sub-topic.  The receptionist must
# steer toward an assessment / practitioner review as the best starting point.

_TREATMENT_SUITABILITY_PHRASES: tuple = (
    # "would X work for Y" / "would it work for my ..."
    "work for", "works for",
    # "should I book X" / "should I book X or something else"
    "should i book", "should i get an appointment",
    "should i see someone", "should i come in",
    # disjunction: "or something else" / "or should I book"
    "or something else", "or something different",
    "or should i", "or would",
    # "would you recommend X for Y"
    "would you recommend", "do you recommend", "what would you recommend",
    "what do you recommend",
    # "is X the right appointment for Y" / "right for"
    "right for", "right appointment for", "right treatment for",
    "best for", "best appointment for", "best treatment for",
    "most suitable", "most appropriate",
    "suitable for", "appropriate for",
    # "what kind of appointment" / "what should I book"
    "what kind of appointment", "what type of appointment",
    "what appointment", "which appointment",
    "what should i book", "what would be best",
    # "would this treatment be right for me"
    "treatment be right", "treatment right for",
    # "do you think I should see someone ... what should I book"
    "what should i",
)


def is_treatment_suitability_question(text: str) -> bool:
    """
    Return True if the caller is asking a treatment-suitability /
    best-starting-point question — e.g. "Would acupuncture work for ankle
    pain?", "Should I book physio or massage for back pain?", "What kind of
    appointment would be best for knee pain?".

    Broader than is_suitability_question, which is tuned for
    "what do you recommend" style clinician-decides questions.
    """
    t = (text or "").lower()
    return any(p in t for p in _TREATMENT_SUITABILITY_PHRASES)


# Body-area / problem phrases — used to include the caller's concern in the
# best-starting-point response so the answer is directional, not generic.
_BODY_AREAS: tuple = (
    "ankle", "knee", "shoulder", "back", "neck", "hip", "wrist", "elbow",
    "foot", "feet", "heel", "toe", "leg", "thigh", "calf", "hamstring",
    "arm", "hand", "finger", "thumb", "spine", "disc", "jaw",
)


def _detect_problem_phrase(text: str) -> str:
    """
    Extract a short "<body-area> pain/injury" style phrase from the utterance
    so the response can echo the caller's concern.  Returns "" if nothing
    clear is found.
    """
    t = (text or "").lower()
    for area in _BODY_AREAS:
        if area in t:
            for suffix in (" pain", " ache", " injury", " problem", " issue",
                           " soreness", " stiffness", " strain", " sprain"):
                if area + suffix in t:
                    return area + suffix
            return area
    for noun in ("pain", "injury", "ache", "soreness", "stiffness"):
        if noun in t:
            return noun
    return ""


def best_starting_point_response(text: str) -> str:
    """
    Safe, operational "best starting point" answer for treatment-suitability
    questions.  Acknowledges the concern if identifiable, steers toward an
    assessment, and leaves treatment choice to the practitioner.

    Never diagnoses, never endorses a specific modality as correct for a
    condition, never rejects one.
    """
    problem = _detect_problem_phrase(text)
    service = detect_service_name(text)
    if problem and service:
        return (
            f"For {problem}, the best starting point is usually an assessment "
            f"so the practitioner can recommend the most suitable treatment, "
            f"whether that includes {service.lower()} or something else. "
            f"If you\u2019d like to get that arranged, I can take a few details."
        )
    if problem:
        return (
            f"For {problem}, the best starting point is usually an assessment "
            f"so the practitioner can recommend the most suitable treatment. "
            f"If you\u2019d like to get that arranged, I can take a few details."
        )
    if service:
        return (
            f"That would depend on your symptoms. The best starting point is "
            f"usually an assessment so the practitioner can advise on whether "
            f"{service.lower()} or something else would be most suitable. "
            f"If you\u2019d like to get that arranged, I can take a few details."
        )
    return (
        "That would depend on your symptoms. The best starting point is "
        "usually an assessment so the practitioner can recommend the most "
        "suitable treatment from there. "
        "If you\u2019d like to get that arranged, I can take a few details."
    )


# ── Service availability question detection ───────────────────────────────────
# "Do you offer X?" — pure availability query; answer only with what's listed.

_AVAILABILITY_PHRASES: tuple = (
    "do you offer", "do you have", "do you provide", "do you do",
    "do you also", "do you use", "do you give",
    "can i get", "can you do", "can you offer", "can you provide",
    "is that something you", "is that available", "is it available",
    "available at your", "available at the", "available there",
    "is that offered", "is it offered", "is that on offer",
    "do you include", "do you cover",
)

# Canonical service name mapping (word/phrase → display name from clinic["services"])
# Covers Theorem Health's full service list.
_SERVICE_NAME_MAP: Dict[str, str] = {
    "acupuncture":           "Acupuncture",
    "auricular acupuncture": "Auricular Acupuncture",
    "shockwave":             "Shockwave Therapy",
    "shockwave therapy":     "Shockwave Therapy",
    "laser":                 "Class IV Laser Therapy",
    "laser therapy":         "Class IV Laser Therapy",
    "class iv":              "Class IV Laser Therapy",
    "psychotherapy":         "Psychotherapy",
    "hypnotherapy":          "Psychotherapy",
    "reiki":                 "Reiki and Energy Healing",
    "energy healing":        "Reiki and Energy Healing",
    "massage":               "Wellness and Stress Relief Massage",
    "wellness massage":      "Wellness and Stress Relief Massage",
    "rehabilitation":        "Remedial Rehabilitation",
    "rehab":                 "Remedial Rehabilitation",
    "prescribing":           "Prescribing",
    "prescriptions":         "Prescribing",
    "physio":                "Physiotherapy",
    "physiotherapy":         "Physiotherapy",
}


def is_availability_question(text: str) -> bool:
    """Return True if the text is asking whether a service is offered (not whether it suits them)."""
    t = text.lower()
    return any(p in t for p in _AVAILABILITY_PHRASES)


def detect_service_name(text: str) -> Optional[str]:
    """
    Extract a canonical service name from the caller's text.

    Tries longer phrases first to avoid substring false-positives
    (e.g. 'auricular acupuncture' before 'acupuncture').
    Returns None if no known service is mentioned.
    """
    t = text.lower()
    for key in sorted(_SERVICE_NAME_MAP, key=len, reverse=True):
        if key in t:
            return _SERVICE_NAME_MAP[key]
    return None


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

    Avoids 'typically' and speculative modality mentions.
    Uses 'first step is an assessment' framing throughout.
    """
    if reason:
        return (
            "That\u2019s something the clinic can look at. "
            "The first step would be a physiotherapy assessment "
            "to understand what\u2019s going on and work out the best approach from there. "
            "If you\u2019d like to get that arranged, I can take a few details."
        )
    return (
        "The first step would be a physiotherapy assessment "
        "to understand the issue and work out the best approach from there. "
        "If you\u2019d like to get that arranged, I can take a few details."
    )


def clinician_decides_response() -> str:
    """
    Safe response for suitability / recommendation questions.

    The receptionist must not recommend modalities or imply a treatment plan.
    """
    return (
        "That\u2019s something the clinician would decide with you after an assessment. "
        "They\u2019ll talk through what\u2019s most suitable once they\u2019ve had "
        "a chance to examine things properly. "
        "If you\u2019d like to arrange an appointment, I can take a few details."
    )


def service_availability_response(service_name: str, clinic: Dict[str, Any]) -> str:
    """
    Short, bounded response for pure service-availability questions.

    Checks clinic["services"] list; falls back to a safe non-committal answer
    if the service is not found.
    """
    services: List[str] = clinic.get("services", [])
    service_lower = service_name.lower()
    for s in services:
        if service_lower in s.lower():
            return (
                f"Yes \u2014 {s.lower()} is one of the services offered at the clinic. "
                "If you\u2019d like to book an appointment, I can take a few details."
            )
    # Service not in configured list — don't fabricate
    return (
        "I\u2019m not sure if the clinic currently offers that specifically \u2014 "
        "I\u2019d recommend calling the team directly to check. "
        "If you\u2019d like to get in touch, I can pass on a message."
    )


def _team_confirmation_response() -> str:
    return (
        "That\u2019s something the team would be best placed to advise on. "
        "The first step would be a physiotherapy assessment, "
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
    original_text: str = "",
) -> ServiceFitPolicyResult:
    """
    Evaluate the correct response action for a service-fit or eligibility question.

    Consumes clinic config (patient_policies, faq, services) to produce a
    deterministic, confidence-capped outcome.  Never fabricates capability claims.

    Actions in priority order (see module docstring):
      POLICY_DISALLOW                   → clinic cannot see this patient
      ASK_CHILD_AGE                     → age required before any answer
      SERVICE_AVAILABLE_ONLY            → availability query answered from services list
      SUITABILITY_CLINICIAN_DECIDES     → recommendation/suitability → clinician decides
      ALLOW_ASSESSMENT_FIRST            → condition supported; bounded reply
      REQUIRE_TEAM_CONFIRMATION         → outside known scope; safe reply
      INSUFFICIENT_KNOWLEDGE_SAFE_REPLY → no reason given; generic safe reply
    """
    policies   = clinic.get("patient_policies", {})
    no_children: Optional[bool] = policies.get("no_children")
    min_age: Optional[int]      = policies.get("min_patient_age")

    # ── 1. Child / minor patient gate ───────────────────────────────────────────
    if child_related:
        # "Adults only" does NOT mean disallow immediately when age is unknown.
        # "My son" could be 19. Ask age first; evaluate once confirmed.
        adult_threshold: int = policies.get("adult_age_threshold", 18)

        if no_children is True:
            if age is None:
                logger.info(
                    "service_fit_policy: child_detected age_missing -> ASK_CHILD_AGE"
                )
                return ServiceFitPolicyResult(
                    action=ASK_CHILD_AGE,
                    response_text=_age_question(relationship),
                    pending_followup="child_age",
                )
            if age < adult_threshold:
                resp = clinic.get("faq", {}).get("children_policy") or (
                    "I\u2019m sorry \u2014 the clinic sees adults only and doesn\u2019t "
                    "currently offer paediatric physiotherapy."
                )
                logger.info(
                    "service_fit_policy: child age=%d -> CHILD_DISALLOWED", age
                )
                return ServiceFitPolicyResult(action=POLICY_DISALLOW, response_text=resp)
            # age >= adult_threshold: the "son/daughter" is an adult — fall through
            logger.info(
                "service_fit_policy: child age=%d >= adult_threshold=%d -> adult allowed",
                age, adult_threshold,
            )

        # Clinic accepts patients above a minimum age
        elif no_children is False and min_age is not None:
            if age is None:
                logger.info(
                    "service_fit_policy: child_detected age_missing -> ASK_CHILD_AGE"
                )
                return ServiceFitPolicyResult(
                    action=ASK_CHILD_AGE,
                    response_text=_age_question(relationship),
                    pending_followup="child_age",
                )
            if age < min_age:
                resp = clinic.get("faq", {}).get("children_policy") or (
                    f"I\u2019m sorry \u2014 the clinic sees patients aged {min_age} and over."
                )
                logger.info(
                    "service_fit_policy: child age=%d < min_age=%d -> CHILD_DISALLOWED",
                    age, min_age,
                )
                return ServiceFitPolicyResult(action=POLICY_DISALLOW, response_text=resp)
            # age >= min_age → fall through to condition check

        # Policy unconfigured (no_children is None) and age unknown
        elif no_children is None and age is None:
            logger.info(
                "service_fit_policy: child_detected age_missing policy_unknown -> ASK_CHILD_AGE"
            )
            return ServiceFitPolicyResult(
                action=ASK_CHILD_AGE,
                response_text=_age_question(relationship),
                pending_followup="child_age",
            )

        # Otherwise (no_children=None + age known, or no_children=False + no min_age,
        # or no_children=True + age >= adult_threshold): proceed to checks below.

    # ── 2. Service availability question ───────────────────────────────────────
    # "Do you offer acupuncture?" → answer from configured services list only.
    if original_text and is_availability_question(original_text):
        svc = detect_service_name(original_text)
        if svc:
            resp = service_availability_response(svc, clinic)
            logger.info(
                "service_fit_policy: service=%s -> SERVICE_AVAILABLE_ONLY", svc
            )
            return ServiceFitPolicyResult(
                action=SERVICE_AVAILABLE_ONLY, response_text=resp
            )

    # ── 3. Suitability / recommendation blocker ─────────────────────────────────
    # "What do you recommend?" / "Would acupuncture work for me?" →
    # Clinician decides — receptionist must not speculate about treatment.
    if original_text and is_suitability_question(original_text):
        logger.info(
            "service_fit_policy: suitability_question -> CLINICIAN_DECIDES; "
            "recommendation_blocker applied"
        )
        return ServiceFitPolicyResult(
            action=SUITABILITY_CLINICIAN_DECIDES,
            response_text=clinician_decides_response(),
        )

    # ── 4. Condition-based evaluation ───────────────────────────────────────────
    if not reason:
        logger.info(
            "service_fit_policy: no_reason -> INSUFFICIENT_KNOWLEDGE_SAFE_REPLY; "
            "confidence_cap applied"
        )
        return ServiceFitPolicyResult(
            action=INSUFFICIENT_KNOWLEDGE_SAFE_REPLY,
            response_text=assessment_first_response(""),
        )

    if condition_is_supported(reason, clinic):
        logger.info(
            "service_fit_policy: condition_supported reason=%r -> ALLOW_ASSESSMENT_FIRST; "
            "confidence_cap applied",
            reason[:40],
        )
        return ServiceFitPolicyResult(
            action=ALLOW_ASSESSMENT_FIRST,
            response_text=assessment_first_response(reason),
        )

    logger.info(
        "service_fit_policy: condition_unsupported reason=%r -> REQUIRE_TEAM_CONFIRMATION; "
        "confidence_cap applied",
        reason[:40],
    )
    return ServiceFitPolicyResult(
        action=REQUIRE_TEAM_CONFIRMATION,
        response_text=_team_confirmation_response(),
    )
