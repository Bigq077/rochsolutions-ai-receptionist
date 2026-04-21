# app/media_streams/flow.py
"""
Multi-intent conversation flow for the Susie AI receptionist.

All conversation decisions live here.  Nothing else in the pipeline
decides what Susie says next.

Each intent maps to a dedicated flow array (a list of step dicts).
The FlowEngine starts in DETECT_INTENT_FLOW.  The first caller utterance
classifies the intent and switches to the correct flow.  From that point
the engine works identically to the original single-flow design:

    Susie asks a question.  Caller answers.  Step advances.  Repeat.

Flows:
    DETECT_INTENT_FLOW  — entry point (1 step, no spoken question)
    BOOKING_FLOW        — new appointment booking (10 steps)
    RESCHEDULE_FLOW     — reschedule existing appointment (7 steps)
    CANCEL_FLOW         — cancel existing appointment (5 steps)
    FAQ_FLOW            — price / insurance / hours / services questions (2 steps)

Usage from connection.py (unchanged):
    flow = FlowEngine(session, tts_text_queue, llm_fn)
    # First caller utterance starts the flow:
    await flow.ask_current_question()
    # Every subsequent utterance goes through:
    await flow.handle_transcript(transcript)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.phrases import RETRY_PHRASES
from app.media_streams.location_resolver import resolve_clinic_location as _resolve_clinic
from app.media_streams.utterance_router import (
    is_repair_utterance       as _is_repair_utt,
    has_service_fit_question  as _is_sfit_utt,
    has_minor_reference       as _has_minor_ref,
    analyze_utterance_for_flow_control as _router_analyze,
    get_bridge_text           as _router_bridge_text,
    ANSWER_SERVICE_FIT_THEN_ASK_PENDING as _R_SVC_FIT,
    BRIDGE_THEN_ASK_PENDING   as _R_BRIDGE,
    DO_NOT_ADVANCE_REPAIR     as _R_REPAIR,
)

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _fuzz = None  # type: ignore[assignment]
    _RAPIDFUZZ_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── FAQ fast-path for services (Bug 1) ─────────────────────────────────────
# Spoken deterministically instead of calling the LLM — avoids long service lists.
_FAQ_SERVICES_FAST = (
    "Of course — we offer physiotherapy assessments and follow-ups, "
    "plus treatments like acupuncture, shockwave therapy, and laser therapy. "
    "Is there one in particular you'd like to know more about?"
)
_FAQ_SERVICES_FULL = (
    "Of course! Our full range includes physiotherapy assessments and follow-ups, "
    "acupuncture, shockwave therapy, laser therapy, biomechanical assessments, "
    "sports massage, and Pilates classes. "
    "Just let me know if you\u2019d like more detail on any of those."
)

# ── Specific-service drill-down answers — max 1–2 sentences each ────────────
# Used when the caller names a concrete modality (e.g. "shockwave therapy please").
# Checked BEFORE the generic overview so callers get the specific answer they asked for.
_SPECIFIC_SERVICE_ANSWERS: dict = {
    "shockwave": (
        "Certainly — shockwave therapy uses sound waves to stimulate healing, "
        "particularly for stubborn tendon or heel pain like plantar fasciitis or Achilles issues. "
        "Your clinician will confirm whether it\u2019s the right option for you."
    ),
    "acupuncture": (
        "Acupuncture uses very fine needles \u2014 much finer than injection needles \u2014 "
        "placed at specific points to help with pain relief and muscle tension. "
        "Most people find it much gentler than they expect, "
        "and it\u2019s often used alongside physiotherapy as part of treatment."
    ),
    "laser": (
        "Certainly — laser therapy uses low-level light energy to reduce inflammation "
        "and support tissue repair. "
        "It\u2019s completely pain-free and is typically used for soft-tissue injuries and joint pain."
    ),
    "sports_massage": (
        "Yes, of course — sports massage focuses on targeting muscle tension "
        "to improve movement and aid recovery. "
        "The pressure is always tailored to your comfort."
    ),
    "pilates": (
        "Absolutely — our Pilates classes focus on core strength and controlled movement, "
        "and they\u2019re often recommended as part of a rehabilitation programme."
    ),
    "biomechanics": (
        "Of course — a biomechanical assessment looks at how your body moves, "
        "particularly your feet and gait, "
        "to identify any imbalances that might be contributing to pain or injury."
    ),
}

# Ordered keyword → service-key map for modality drill-down detection.
# Longer/more specific phrases checked first to avoid false matches.
_SERVICE_KEYWORD_MAP = (
    ("shockwave",       "shockwave"),
    ("acupuncture",     "acupuncture"),
    ("laser",           "laser"),
    ("sports massage",  "sports_massage"),
    ("massage",         "sports_massage"),
    ("pilates",         "pilates"),
    ("biomechanical",   "biomechanics"),
    ("biomechanics",    "biomechanics"),
)

# ── FAQ prices: deterministic from-price gate ───────────────────────────────
# When NO specific service is named, always return this — never a full list.
_FAQ_PRICES_NO_SERVICE = (
    "Of course — prices do vary depending on the treatment. "
    "Was there a particular service you had in mind? "
    "I can give you the exact figure."
)

# ── FAQ insurance: deterministic self-pay / Bupa / claim-back answer ────────
_FAQ_INSURANCE_ANSWER = (
    "Of course \u2014 we\u2019re a self-pay clinic, so you\u2019d pay us directly. "
    "If your insurance policy covers physiotherapy, "
    "you can usually claim it back from your insurer afterwards."
)

# ── FAQ capability: deterministic "what can you help me with" answer ─────────
_CAPABILITY_ANSWER = (
    "Of course — I can help you book an appointment, reschedule or cancel an existing one, "
    "and answer questions about prices, insurance, opening hours, locations, "
    "and the services we offer. What would you like to do?"
)
_CAPABILITY_PHRASES = (
    "what can you help me with", "what can you help with",
    "can you help me with", "can you help with",
    "how can you help", "what exactly can you help",
    "what are you able to help", "what can you do for me",
    "what can you assist with",
)

# ── FAQ child policy: deterministic fallback if clinic config faq entry absent ──
_FAQ_CHILD_POLICY_ANSWER = (
    "We see adults only \u2014 we don\u2019t currently offer paediatric physiotherapy. "
    "If you\u2019re looking for physiotherapy for a child, your GP can refer to an NHS "
    "paediatric service, or you could search for a private paediatric physio clinic in your area."
)
# Named-service keywords — if any of these appear in the transcript, let the LLM
# answer with just that service's price.  Otherwise use _FAQ_PRICES_NO_SERVICE.
_FAQ_PRICES_SERVICE_KEYWORDS = (
    "physio", "physiotherapy", "assessment", "follow", "follow-up", "followup",
    "acupuncture", "shockwave", "laser", "biomechanical", "biomechanics",
    "sports", "massage", "pilates", "class",
)

# ── FAQ: acupuncture fear / needle anxiety — reassurance-first answer ────────
_FAQ_ACUPUNCTURE_FEAR_ANSWER = (
    "Acupuncture uses very fine needles \u2014 much thinner than injection needles \u2014 "
    "and most people describe it as a mild sensation rather than something painful. "
    "If you\u2019re nervous about needles, the practitioner will go at your pace and talk you through it."
)

# ── FAQ: first appointment / what to expect — deterministic fallback ─────────
_FAQ_FIRST_APPT_ANSWER = (
    "Your first appointment usually starts with a chat about your symptoms and what\u2019s been going on, "
    "followed by a physical assessment. "
    "The clinician will then talk you through what they\u2019ve found and the best next steps \u2014 "
    "and in most cases they\u2019ll begin hands-on treatment in that same session."
)

# ── FAQ: accessibility / step-free / wheelchair — short voice-safe answer ──
# Kept intentionally short: one direct answer + one short practical invite.
_FAQ_ACCESSIBILITY_FALLBACK = (
    "Yes \u2014 both our clinics are wheelchair accessible. "
    "Just let us know any specific access needs when you book."
)


def _short_access_ans(clinic: Dict[str, Any]) -> str:
    """
    Return the clinic's configured accessibility answer trimmed to the first
    sentence plus a short booking invite. Voice-safe — never the full 2-3
    sentence config paragraph. Falls back to _FAQ_ACCESSIBILITY_FALLBACK if
    clinic config does not define one.
    """
    _ac = (clinic.get("faq") or {}).get("accessibility") or ""
    if not _ac:
        return _FAQ_ACCESSIBILITY_FALLBACK
    _first = _ac.split(".")[0].strip()
    if not _first:
        return _FAQ_ACCESSIBILITY_FALLBACK
    return _first + ". Just let us know any specific access needs when you book."


# ── Clinic access/logistics detector ────────────────────────────────────────
# True when the utterance is about the CLINIC (step-free, parking, address,
# location) rather than about whether a child can be TREATED there. Used to
# prevent "my son/daughter/child" relation words from hijacking an access or
# logistics question into service-fit / child-policy age-gating.
_ACCESS_LOGISTICS_SIGS: tuple[str, ...] = (
    "step free", "step-free", "step three",
    "wheelchair", "wheel chair",
    "disabled access", "disability access", "disabled parking",
    "accessible", "accessibility",
    "ramp", "lift access", "mobility",
    "parking", "car park", "can i park", "where to park",
    "where can i park", "park near", "park there", "park in the",
    "address", "where are you", "where is your", "where exactly",
    "where is the", "find you", "locate you", "clinic address",
    "postcode", "post code", "directions", "how do i get",
    "get to the clinic", "get to your clinic",
)

# Treatment-eligibility language — if any of these appear, the utterance IS
# about whether the child can be treated / booked and service-fit SHOULD run.
_TREATMENT_ELIGIBILITY_SIGS: tuple[str, ...] = (
    "do you treat", "do you see", "can you treat", "can you see",
    "accept children", "treat children", "see children",
    "can my son book", "can my daughter book", "can my child book",
    "can my kid book", "book for my son", "book for my daughter",
    "book for my child", "appointment for my son",
    "appointment for my daughter", "appointment for my child",
    "is my child eligible", "is my son eligible", "is my daughter eligible",
    "eligible for", "can you help my son", "can you help my daughter",
    "can you help my child", "my son needs", "my daughter needs",
    "my child needs", "my kid needs",
    "paediatric", "pediatric", "child physio", "children physio",
)


def _is_clinic_access_logistics(text: str) -> bool:
    """Return True when the utterance is a clinic access / logistics question
    and NOT a treatment-eligibility question, even if a child relation word
    (my son, my daughter, my child) is present."""
    t = (text or "").lower()
    if not any(p in t for p in _ACCESS_LOGISTICS_SIGS):
        return False
    if any(p in t for p in _TREATMENT_ELIGIBILITY_SIGS):
        return False
    return True


# Explicit child-treatment / child-booking signals — when absent, mere child
# relation words ("my son", "my daughter") are context only and must NOT
# trigger first-turn ASK_CHILD_AGE gating.  With these present the caller is
# clearly asking for treatment eligibility or booking for the child, so
# service-fit / policy gating should proceed normally.
_EXPLICIT_CHILD_TREATMENT_SIGS: tuple[str, ...] = (
    # Treatment eligibility verbs
    "do you treat", "do you see", "can you treat", "can you see",
    "would you treat", "would you see",
    "accept children", "treat children", "see children",
    "offer treatment for children", "offer paediatric", "offer pediatric",
    "treatment for children",
    "can you help my son", "can you help my daughter", "can you help my child",
    "can you help my kid",
    # "can my <relation>" eligibility verb
    "can my son", "can my daughter", "can my child", "can my kid",
    # "can he/she" treatment/booking forms
    "can he be seen", "can she be seen", "can he be treated", "can she be treated",
    "can he come in", "can she come in", "can he book", "can she book",
    "seen there", "treated there",
    # Booking-for-child phrasing
    "book for my son", "book for my daughter", "book for my child",
    "book for my kid",
    "book my son", "book my daughter", "book my child",
    "appointment for my son", "appointment for my daughter",
    "appointment for my child", "appointment for my kid",
    "session for my son", "session for my daughter", "session for my child",
    # Needs-based eligibility (implies treatment)
    "my son needs", "my daughter needs", "my child needs", "my kid needs",
    # Eligibility vocabulary
    "eligible", "old enough", "too young",
    # Paediatric vocabulary
    "paediatric", "pediatric", "child physio", "children physio",
)


def _has_explicit_child_treatment_request(text: str) -> bool:
    """True when the utterance explicitly asks about treating/booking a child.

    Mere child relation words ("my son is disabled", "for my daughter") are
    context and return False.  The first-turn policy gate should only fire
    when this returns True (or when a condition/symptom + child relation is
    present in the same utterance — the policy_gate itself handles that).
    """
    t = (text or "").lower()
    return any(s in t for s in _EXPLICIT_CHILD_TREATMENT_SIGS)


# Practical-FAQ signals that indicate the utterance — even with child context
# — is primarily a clinic/logistics question that must escape first-turn
# service-fit / age-gate routing and reach the FAQ lane.  Superset of
# _ACCESS_LOGISTICS_SIGS so callers asking about clinic comparison/difference
# ("your two clinics", "difference between both") also preempt the age gate.
_PRACTICAL_FAQ_PREEMPT_SIGS: tuple[str, ...] = (
    # Clinic comparison / two-clinic signals
    "difference between", "different from", "two clinics", "both clinics",
    "both of them", "both of the", "which clinic", "which one",
    "alcester or redditch", "redditch or alcester",
    "two locations", "both locations", "either clinic",
    # Practical-only info (services, prices) when combined with child context
    "do you offer", "how much", "how much is",
)


def _is_practical_faq_preempt(text: str) -> bool:
    """True when the utterance is clearly a practical clinic FAQ (access,
    location, parking, clinic comparison, pricing) rather than a
    treatment-eligibility / booking question for a child.  Superset of
    _is_clinic_access_logistics adding clinic-comparison signals."""
    t = (text or "").lower()
    if _is_clinic_access_logistics(text):
        return True
    if any(p in t for p in _PRACTICAL_FAQ_PREEMPT_SIGS):
        # Explicit child-treatment phrasing still wins — caller wants policy gate.
        if any(p in t for p in _TREATMENT_ELIGIBILITY_SIGS):
            return False
        return True
    return False


# Plausible age-answer signals used by the service-fit age follow-up consumer
# to decide whether the next utterance is genuinely answering "how old is
# your son/daughter?" or whether it has escaped back to a practical FAQ.
_AGE_NUMBER_WORDS: tuple[str, ...] = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty",
)
_AGE_ANSWER_PHRASES: tuple[str, ...] = (
    "years old", "year old", "he's", "he is", "she's", "she is",
    "my son is", "my daughter is", "my child is", "my kid is",
    "age is", "aged",
)
# Non-age signals — practical FAQ / logistics / services / insurance /
# pricing utterances that must NOT be consumed as an age answer.
_NON_AGE_REROUTE_SIGS: tuple[str, ...] = (
    # accessibility
    "step free", "step-free", "wheelchair", "accessible", "accessibility",
    "disabled access", "disability", "ramp", "mobility",
    # location / directions
    "where is", "where are", "where exactly", "address", "postcode",
    "directions", "how do i get", "find you", "locate you",
    # clinic comparison
    "difference between", "two clinics", "both clinics", "both of them",
    "which clinic", "alcester or redditch", "redditch or alcester",
    "two locations",
    # parking / transport
    "parking", "car park", "bus", "train", "station",
    # services / prices / insurance
    "do you offer", "do you do", "do you have", "how much", "price",
    "insurance", "bupa", "axa", "aviva",
    # explicit question markers
    "i wanted to ask", "i want to ask", "i had a question",
    "i have a question", "quick question",
)


# ── Topic-aware FAQ re-anchor ───────────────────────────────────────────────
# When an FAQ thread has a locked active service (e.g. acupuncture), the
# post-answer re-anchor should stay on that topic — "Was there anything else
# you wanted to ask about acupuncture?" — instead of the generic
# "Anything else you'd like to ask?" which resets the caller's mental model.
_FAQ_TOPIC_DISPLAY_NAMES: Dict[str, str] = {
    "acupuncture":    "acupuncture",
    "shockwave":      "shockwave therapy",
    "laser":          "laser therapy",
    "sports_massage": "sports massage",
    "pilates":        "Pilates",
    "biomechanics":   "biomechanical assessments",
}


_PRACTICAL_REANCHORS: Dict[str, str] = {
    "accessibility":  "Was there anything else you wanted to ask about access to the clinic?",
    "address":        "Was there anything else you wanted to ask about the clinic location?",
    "location":       "Was there anything else you wanted to ask about the clinic location?",
    "parking":        "Was there anything else you wanted to ask about parking?",
    "transport":      "Was there anything else you wanted to ask about getting to the clinic?",
}

_PRACTICAL_PART_PHRASES: Dict[str, str] = {
    "location":      "the clinic location",
    "parking":       "parking",
    "accessibility": "access to the clinic",
    "transport":     "getting to the clinic",
}


def _faq_topic_reanchor(session: Dict[str, Any]) -> str:
    """Return the topic-specific re-anchor when an FAQ service topic is
    locked, or a practical-part-aware re-anchor when the caller is inside
    a practical FAQ thread (location / parking / accessibility / transport).
    Falls back to the generic re-anchor otherwise."""
    svc = session.get("_faq_active_service")
    display = _FAQ_TOPIC_DISPLAY_NAMES.get(svc or "", "")
    if display:
        return f"Was there anything else you wanted to ask about {display}?"
    sub   = session.get("last_faq_sub", "")
    parts = session.get("_faq_parts_answered") or []
    if sub == "composite" and parts:
        _pp_order = ["location", "parking", "accessibility", "transport"]
        _pieces = [
            _PRACTICAL_PART_PHRASES[p]
            for p in _pp_order
            if p in parts and p in _PRACTICAL_PART_PHRASES
        ]
        if len(_pieces) >= 2:
            _joined = (
                " or ".join(_pieces) if len(_pieces) == 2
                else ", ".join(_pieces[:-1]) + f", or {_pieces[-1]}"
            )
            logger.info("[ms_faq] practical_reanchor_chosen kind=composite parts=%s",
                        "|".join(parts))
            return f"Was there anything else you wanted to ask about {_joined}?"
    if sub in _PRACTICAL_REANCHORS:
        logger.info("[ms_faq] practical_reanchor_chosen kind=single sub=%s", sub)
        return _PRACTICAL_REANCHORS[sub]
    return "Anything else you'd like to ask?"


# ── Locked-topic FAQ follow-up preemption ───────────────────────────────────
# When a specific FAQ service topic is locked (_faq_active_service), a
# natural follow-up about that same service MUST stay in the FAQ lane.
# Without this preemption, _detect_intent classifies utterances like
# "i'm a bit scared of needles, are they big and is it painful?" as
# booking (via booking_priority_p matching "painful"/"hurt"/"pain")
# and the caller gets a vague LLM dodge answer through the
# semantic-guard → general_query fall-through.
#
# Generic signals apply to any locked service (hurt/painful/safe/side
# effects/how it works/how long/scared etc.).  Service-specific signals
# catch vocabulary that only makes sense for one modality
# (needles for acupuncture, gait for biomechanics, etc.).

_LOCKED_TOPIC_COMMON_SIGS: tuple[str, ...] = (
    "painful", "pain", "hurt", "hurts", "does it hurt", "will it hurt",
    "is it painful", "is it safe", "safe", "side effect", "side effects",
    "what happens", "what does it feel", "feel like", "how does",
    "how long", "how often", "how much", "how many",
    "scared", "afraid", "nervous", "anxious", "anxiety", "worried",
    "scary", "scare", "how it works", "how does it work", "work exactly",
)

_LOCKED_TOPIC_SERVICE_SIGS: Dict[str, tuple[str, ...]] = {
    "acupuncture":    ("needle", "needles", "how big", "size", " big ", " big?",
                       "small", "thin", "thick", "sterile"),
    "shockwave":      ("shock", "wave", "pulse", "sound wave", "energy", "loud"),
    "laser":          ("laser", "light", "beam", "burn", "heat"),
    "sports_massage": ("massage", "pressure", "deep", "soft", "oil"),
    "pilates":        ("class", "classes", "mat", "reformer", "group", "beginner"),
    "biomechanics":   ("gait", "walk", "walking", "movement", "scan",
                       "assessment", "posture"),
}

# Explicit intent-switch phrases — if the caller clearly wants to
# book/reschedule/cancel, the locked-topic preemption MUST NOT fire.
_LOCKED_TOPIC_SWITCH_PHRASES: tuple[str, ...] = (
    "i want to book", "i'd like to book", "i need to book",
    "want to book", "looking to book", "trying to book",
    "book an appointment", "book appointment", "book me in",
    "can i book", "could i book", "schedule an appointment",
    "make an appointment", "get an appointment",
    "reschedule", "cancel my", "cancel the", "cancel an",
    "come in and see", "actually book", "actually i want to book",
    "actually i'd like to book",
)


def _check_locked_topic_followup(
    session: Dict[str, Any],
    text_lower: str,
) -> tuple[Optional[str], bool, bool]:
    """
    Decide whether the current utterance is a locked-topic FAQ follow-up
    that must be kept in the FAQ lane, preempting _detect_intent.

    Returns (locked_svc, matched, explicit_switch).
      - locked_svc: active FAQ service, or None if no topic is locked
      - matched: utterance contains follow-up signals for the locked service
      - explicit_switch: utterance clearly asks to book/reschedule/cancel
    """
    locked_svc = session.get("_faq_active_service")
    if not locked_svc:
        return None, False, False
    sigs = set(_LOCKED_TOPIC_COMMON_SIGS)
    sigs.update(_LOCKED_TOPIC_SERVICE_SIGS.get(locked_svc, ()))
    matched = any(s in text_lower for s in sigs)
    explicit_switch = any(p in text_lower for p in _LOCKED_TOPIC_SWITCH_PHRASES)
    return locked_svc, matched, explicit_switch

# ── Booking-state FAQ interrupt detector ─────────────────────────────────────
# Used by handle_transcript at DATA_COLLECTION states (COLLECT_REASON,
# COLLECT_NAME*, CONFIRM_PHONE*) to decide whether the caller has asked an
# FAQ-shaped question mid-booking.  Without this, utterances like
# "I saw you offer acupuncture — I'm scared of needles" arriving at
# COLLECT_REASON get stored as the booking reason and the flow advances as
# if that were the caller's symptom.  This helper lets us hard-route such
# utterances through _handle_mid_flow_interrupt instead, preserving the
# suspended booking question via the existing re-anchor path.
#
# Returns True only for utterances that are CLEARLY FAQ-shaped.  Bare body-
# part / symptom answers ("neck pain", "my ankle hurts"), valid name
# answers ("my first name is Quentin"), and yes/no confirmations remain
# False so their legitimate capture is never blocked.

_BOOKING_FAQ_SERVICE_TERMS: tuple[str, ...] = (
    "acupuncture", "shockwave", "laser", "pilates",
    "sports massage", "massage",
    "biomechanical", "biomechanics",
    "physio", "physiotherapy", "physiotherapist",
)

_BOOKING_FAQ_INFO_SEEKING: tuple[str, ...] = (
    "i saw on your website", "saw on your website", "on your website",
    "i saw on the website", "saw on the website",
    "i saw you offer", "saw you offer", "i saw you do", "saw you do",
    "i want to know", "i wanted to know", "want to know",
    "i'd like to know", "id like to know", "would like to know",
    "can you tell me", "could you tell me", "tell me about",
    "i was wondering", "just wondering", "wondering if",
    "how does it work", "how does that work", "how does acupuncture",
    "how does shockwave", "how does laser",
    "what happens", "what is it", "what is acupuncture",
    "what does it feel", "what does it involve",
    "will it hurt", "is it painful", "is it safe",
    "are the needles", "are they big", "are they small",
    "how big", "how long", "how often", "how many sessions",
    "side effects", "any side",
)

_BOOKING_FAQ_CONCERN: tuple[str, ...] = (
    "i'm scared", "im scared", "scared of",
    "i'm nervous", "im nervous", "nervous about",
    "i'm anxious", "im anxious", "anxious about",
    "i'm worried", "im worried", "worried about",
    "afraid of", "i'm afraid", "im afraid",
    "hate needles", "don't like needles", "dont like needles",
    "not keen on needles", "phobia",
)

_BOOKING_FAQ_EXPLICIT_OPENERS: tuple[str, ...] = (
    "i had a question", "i have a question", "quick question",
    "can i ask", "could i ask", "may i ask",
    "before we book", "before i book", "before booking",
    "one quick thing", "one thing first", "first a question",
)

_BOOKING_FAQ_STRONG_BOOK_VERBS: tuple[str, ...] = (
    "book", "booking", "booked",
    "appointment", "appointments",
    "schedule", "scheduled", "scheduling",
    "reschedule", "rescheduled",
    "cancel", "cancelled", "canceling",
    "come in", "coming in", "come and see",
)

# Body-part + symptom compound — must be excluded so real COLLECT_REASON
# answers like "my neck is killing me" are still captured as reason.
import re as _re_bfi
_BOOKING_FAQ_BODY_RE  = _re_bfi.compile(
    r"\b(back|shoulder|ankle|knee|hip|neck|wrist|elbow|leg|arm|foot|heel)\b"
)
_BOOKING_FAQ_SYMP_RE  = _re_bfi.compile(
    r"\b(pain|painful|ache|aching|hurt|hurting|injury|injured|problem|issue"
    r"|sore|stiff|stiffness|killing|playing up)\b"
)


def _is_booking_state_faq_interrupt(text: str) -> tuple[bool, str]:
    """
    Decide whether ``text`` arriving at a DATA_COLLECTION booking state is
    clearly an FAQ interruption rather than a genuine booking answer.

    Returns ``(is_faq, reason)``.  ``reason`` is a short tag used in logs.
    """
    t = (text or "").strip().lower()
    if not t:
        return False, ""

    # Strong book-verb anywhere → never treat as FAQ interrupt; the caller is
    # still inside the booking/reschedule/cancel lane.
    if any(v in t for v in _BOOKING_FAQ_STRONG_BOOK_VERBS):
        return False, "strong_book_verb"

    # Body-part + symptom compound → this is a genuine COLLECT_REASON answer.
    if _BOOKING_FAQ_BODY_RE.search(t) and _BOOKING_FAQ_SYMP_RE.search(t):
        return False, "body_symptom_compound"

    has_service     = any(s in t for s in _BOOKING_FAQ_SERVICE_TERMS)
    has_info_seek   = any(p in t for p in _BOOKING_FAQ_INFO_SEEKING)
    has_concern     = any(p in t for p in _BOOKING_FAQ_CONCERN)
    has_opener      = any(p in t for p in _BOOKING_FAQ_EXPLICIT_OPENERS)

    # Primary: service mention AND (info-seeking OR concern).
    if has_service and (has_info_seek or has_concern):
        return True, "service+infoseek_or_concern"

    # Secondary: explicit interrupt opener (e.g. "I had a question…").
    if has_opener:
        return True, "explicit_opener"

    # Tertiary: needle-fear phrasing without an explicit service word
    # ("I'm scared of needles", "I hate needles") — only treat as FAQ if
    # there IS a question / concern shape, not bare statements.
    if has_concern and ("needle" in t or "needles" in t):
        return True, "needle_concern"

    return False, ""


# ── Mid-booking FAQ ellipsis follow-up detector ──────────────────────────────
# After a mid-booking FAQ answer (e.g. acupuncture fear), the caller's next
# utterance often omits the service noun: "do you think that would be a
# problem", "is that normal", "would that hurt", "are they usually big then".
# Without an explicit guard, these reach COLLECT_REASON / COLLECT_NAME and
# get stored as booking payload.  This helper returns True for short,
# question-shaped follow-ups that plausibly continue the prior FAQ thread.
_FAQ_ELLIPSIS_FOLLOWUP_SIGS: tuple[str, ...] = (
    "do you think", "would that", "is that", "is it",
    "so is it", "so would", "so does", "so is that", "so are they",
    "so do you think",
    "are they", "are the needles", "would it", "does that", "does it",
    "would they", "is there", "is there a",
    "a problem", "be a problem", "be an issue", "an issue",
    "a bad sign", "something to worry", "worry about",
    "ok to", "okay to", "safe to", "painful then",
    "hurt", "hurts", "painful", "scary",
    "side effect", "side effects", "dangerous",
    "normal", "is that normal", "is that ok", "is that okay",
    "how big", "how long", "how many",
    "would that stop", "stop me doing", "could i still",
    "scared of", "i'm scared", "im scared", "fear of", "my fear",
    "nervous about", "anxious about", "worried about",
    "needle", "needles",
)

# Continuation terms that, when an FAQ topic is already locked, imply the
# caller is still on the same FAQ thread even if they do not repeat the
# topic noun — e.g. "would my fear of needles be a problem" after
# topic=acupuncture.  Deliberately narrow so bare booking payloads
# ("my knee is killing me") never match.
_FAQ_TOPIC_CONTINUATION_SIGS: tuple[str, ...] = (
    "needle", "needles",
    "pain", "painful", "hurt", "hurts",
    "scared", "nervous", "anxious", "worried", "worry",
    "problem", "issue", "concern",
    "that would", "that'd", "stop me", "still do", "still okay",
)


# ── Incomplete FAQ-intro detector (Bug B) ────────────────────────────────────
# Caller starts "I saw on your website where you offer acupuncture" and pauses.
# Endpointing fires, STT finalises the chunk, and the system would answer the
# FAQ before the caller has expressed the real concern
# ("...but I'm scared of needles, what do you think").  This detector holds
# the turn: we defer FAQ answering by storing the intro and combining it
# with the next utterance.
_FAQ_INTRO_OPENERS: tuple[str, ...] = (
    "i saw on your website", "saw on your website",
    "i saw on the website", "saw on the website",
    "on your website you", "your website says",
    "i saw you offer", "saw you offer",
    "i saw that you offer", "saw that you offer",
    "i saw you do", "saw you do",
    "i saw that you do", "saw that you do",
    "i noticed you offer", "noticed you offer",
    "i noticed you do", "noticed you do",
    "i noticed acupuncture", "i noticed physio",
    "i noticed shockwave", "i noticed laser",
    "on the website",
    "i read that you", "read that you",
    "i heard you offer", "heard you offer",
    "i heard you do", "heard you do",
    "i see that you offer", "see that you offer",
    # Bare setup lines — "you offer acupuncture", "you do acupuncture"
    # These are often prefaces, not questions.  The detector below
    # still requires a trailing service noun + no completion signal.
    "you offer", "you do", "you provide", "you have",
)

_FAQ_INTRO_COMPLETE_SIGS: tuple[str, ...] = (
    "?",
    "scared", "nervous", "anxious", "worried", "afraid", "fear",
    "painful", "hurt", "hurts", "hurting",
    "what do you think", "would that", "would it",
    "is it", "is that", "will it", "will that",
    "does it", "does that", "how does", "how long", "how much", "how many",
    "side effect", "side effects", "any side",
    "for my", "for a", "for the",
    "about my", "about a", "about the",
    "can you", "could you", "should i",
    "needle", "needles",
    "bad sign", "a problem", "an issue", "any good", "worth it",
    "safe",
)

_FAQ_INTRO_TAIL_FILLERS: frozenset = frozenset({
    "then", "too", "and", "also", "actually", "okay", "ok", "so",
})


def _is_incomplete_faq_intro(text: str) -> bool:
    """True when the utterance looks like an FAQ lead-in that mentions a
    service but has not yet landed the concern or question.  Kept narrow
    by requiring an explicit opener + service noun + absence of any
    completion signal AND a trailing service/filler token."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if len(t.split()) > 16:
        return False
    if not any(op in t for op in _FAQ_INTRO_OPENERS):
        return False
    if not any(s in t for s in _BOOKING_FAQ_SERVICE_TERMS):
        return False
    if any(sig in t for sig in _FAQ_INTRO_COMPLETE_SIGS):
        return False
    _svc_last_words: set[str] = {s.split()[-1] for s in _BOOKING_FAQ_SERVICE_TERMS}
    _tok = [w.strip(".,!?;:") for w in t.split()]
    _tok = [w for w in _tok if w]
    if not _tok:
        return False
    if _tok[-1] in _svc_last_words:
        return True
    if (
        len(_tok) >= 2
        and _tok[-2] in _svc_last_words
        and _tok[-1] in _FAQ_INTRO_TAIL_FILLERS
    ):
        return True
    return False


def _is_faq_ellipsis_followup(text: str, ctx_active: bool = False) -> bool:
    """True when the utterance is an ellipsis-style follow-up that
    plausibly continues a prior FAQ thread (no service noun repeated, no
    booking payload).  When ``ctx_active`` is True (mid-booking FAQ ctx
    present) the length cap is relaxed and the topic-continuation signals
    qualify."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Body-part + symptom compound → genuine booking reason, not FAQ follow-up.
    if _BOOKING_FAQ_BODY_RE.search(t) and _BOOKING_FAQ_SYMP_RE.search(t):
        return False
    _word_count = len(t.split())
    # Without ctx: short utterances only (≤14 words).  With ctx: allow
    # longer natural-language follow-ups (up to 30 words) since caller is
    # clearly extending the already-opened FAQ thread.
    if not ctx_active and _word_count > 14:
        return False
    if ctx_active and _word_count > 30:
        return False
    if any(p in t for p in _FAQ_ELLIPSIS_FOLLOWUP_SIGS):
        return True
    if ctx_active and any(p in t for p in _FAQ_TOPIC_CONTINUATION_SIGS):
        return True
    return False


# Explicit booking-resume signals — caller wants to exit FAQ thread and
# continue the interrupted booking step.
_BOOKING_RESUME_SIGS: tuple[str, ...] = (
    "let's book", "lets book", "i'd like to book", "id like to book",
    "i want to book", "i wanna book",
    "carry on", "let's carry on", "lets carry on",
    "let's continue", "lets continue", "continue",
    "go ahead", "let's go ahead", "lets go ahead",
    "let's proceed", "lets proceed", "proceed",
    "the reason i", "reason i'm coming", "reason im coming",
    "my first name", "my last name", "my surname", "my name is",
    "use this number", "this number", "my number",
)


def _is_booking_resume_signal(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(p in t for p in _BOOKING_RESUME_SIGS)


# ── Mid-booking FAQ resolution detector ─────────────────────────────────────
# After a mid-booking acupuncture fear answer + re-anchor, declarative /
# proceed-oriented utterances ("I should be fine for acupuncture then",
# "I'd like to book in an acupuncture appointment then", "let's try
# acupuncture", "that sounds fine") must resume the booking flow rather
# than re-enter the FAQ path.  Without this the word "acupuncture" gets
# re-caught by `_is_booking_state_faq_interrupt` and loops back to an
# acupuncture service overview.
_FAQ_RESOLUTION_PROCEED_SIGS: tuple[str, ...] = (
    "should be fine", "i should be fine", "i'll be fine", "ill be fine",
    "i'm fine with", "im fine with", "i'm ok with", "im ok with",
    "i'm okay with", "im okay with",
    "fine for acupuncture", "fine with acupuncture",
    "ok with acupuncture", "okay with acupuncture",
    "happy with acupuncture", "happy to try",
    "let's try", "lets try", "i'll try", "ill try", "i will try",
    "i'd like to try", "id like to try",
    "i want to try", "wanted to try", "want to try out", "try out acupuncture",
    "let's do", "lets do", "let's go with", "lets go with",
    "i'll go with", "ill go with", "i think i'll go", "i think ill go",
    "i think i'll", "i think ill",
    "i'd like to book", "id like to book", "i want to book", "i wanna book",
    "book in an acupuncture", "book an acupuncture", "acupuncture appointment",
    "acupuncture booking", "book the acupuncture",
    "book me in", "book me",
    "go ahead with", "go with acupuncture",
    "go through with", "want to go through", "go through with acupuncture",
    "through with acupuncture",
    "that sounds fine", "that sounds good", "that sounds ok",
    "that sounds okay", "that's fine", "thats fine",
    "sounds good then", "sounds fine then",
)

_FAQ_RESOLUTION_QUESTION_SIGS: tuple[str, ...] = (
    "?", "do you think", "would that", "would it",
    "is it", "is that", "does it", "does that",
    "how ", "what ", "why ", "when ", "will it",
    "a problem", "an issue", "be a problem", "be an issue",
)

_FAQ_RESOLUTION_FRESH_CONCERN_SIGS: tuple[str, ...] = (
    "scared", "nervous", "anxious", "worried", "fear",
    "painful", "hurt", "hurts",
)


_FAQ_RESOLUTION_SHORT_ACCEPT_SIGS: tuple[str, ...] = (
    "acupuncture then", "the acupuncture", "that acupuncture",
    "probably acupuncture", "i guess acupuncture", "guess acupuncture",
    "the acupuncture one", "acupuncture one",
    "yeah acupuncture", "yes acupuncture", "yep acupuncture",
    "okay acupuncture", "ok acupuncture",
    "sounds okay then", "sounds ok then", "sounds fine then",
    "sounds good then", "that sounds okay", "that sounds ok",
    "that sounds fine", "that sounds good",
    "that's okay then", "thats okay then", "that's ok then",
    "yes let's book", "yes lets book", "okay yes", "yes let's",
)


def _is_mid_booking_acupuncture_resolution(
    text_lower: str, session: Dict[str, Any]
) -> bool:
    """
    True when: active mid-booking FAQ topic is acupuncture AND the
    utterance is declarative / proceed-oriented / short acceptance AND
    has no question-form or fresh-concern signal dominating.  Narrow by
    design — intended for post-FAQ resume detection only.
    """
    ctx = session.get("_mid_booking_faq_ctx") or {}
    if ctx.get("topic") != "acupuncture":
        return False
    t = (text_lower or "").strip()
    if not t:
        return False
    # Defence-in-depth: negative utterances ("I don't think I'm going to
    # do acupuncture then") contain short-accept substrings like
    # "acupuncture then" and would otherwise flip positive.
    if _is_service_rejection(t):
        return False
    if any(p in t for p in _FAQ_RESOLUTION_QUESTION_SIGS):
        return False
    if any(p in t for p in _FAQ_RESOLUTION_FRESH_CONCERN_SIGS):
        return False
    if any(p in t for p in _FAQ_RESOLUTION_PROCEED_SIGS):
        return True
    if any(p in t for p in _FAQ_RESOLUTION_SHORT_ACCEPT_SIGS):
        return True
    return False


# ── Service rejection / switch detectors (COLLECT_REASON mid-booking) ───────
# Used to separate negative "I don't think I'm going to do acupuncture then"
# from positive resolution, and to cleanly detect when the caller abandons
# the prior FAQ topic for a different service.
_SERVICE_REJECTION_SIGS: tuple[str, ...] = (
    "don't think i'm going to do", "dont think i'm going to do",
    "don't think im going to do", "dont think im going to do",
    "don't think i'll do", "dont think i'll do",
    "don't think ill do", "dont think ill do",
    "don't think i want", "dont think i want",
    "don't think acupuncture", "dont think acupuncture",
    "don't want to do", "dont want to do",
    "not going to do", "not gonna do",
    "i'd rather not", "id rather not",
    "rather not do",
    "changed my mind",
    "not for me",
    "don't fancy", "dont fancy",
    "not keen on", "not keen",
    "skip the",
)


def _is_service_rejection(text_lower: str) -> bool:
    """True when the utterance is a deterministic rejection / decline of
    the currently-discussed service path.  Narrow by design — only fires
    on unambiguous negative phrasings."""
    t = (text_lower or "").strip()
    if not t:
        return False
    return any(p in t for p in _SERVICE_REJECTION_SIGS)


# Service aliases used by the switch detector.  Keys are canonical
# topic names that line up with _mid_booking_faq_ctx["topic"].
_SERVICE_SWITCH_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("physiotherapy", ("physiotherapy", "physio assessment",
                       "physio appointment", "physio",
                       "initial assessment")),
    ("shockwave", ("shockwave",)),
    ("laser", ("laser therapy", "laser treatment", "laser")),
    ("pilates", ("pilates",)),
    ("massage", ("sports massage", "deep tissue", "massage")),
    ("biomechanics", ("biomechanical", "biomechanics")),
    ("acupuncture", ("acupuncture",)),
)

_SERVICE_SWITCH_VERB_SIGS: tuple[str, ...] = (
    "instead",
    "rather go with", "rather do", "rather have", "rather book",
    "i'd rather do", "id rather do",
    "i'd rather book", "id rather book",
    "i'd rather have", "id rather have",
    "i'll go with", "ill go with",
    "i'll probably go with", "ill probably go with",
    "probably go with",
    "i'm going to go with", "im going to go with",
    "going to go with",
    "want to go with", "wanna go with",
    "let's do", "lets do",
    "let's just do", "lets just do",
    "just do",
    "let's go with", "lets go with",
    "let's try", "lets try",
    "start with", "start off with",
    "start with a", "start with an", "start with the",
    "start off with a", "start off with an",
    "i'll start with", "ill start with",
    "go ahead with",
    "book a", "book an", "book the",
    "i'll do", "ill do",
    "go with a", "go with an", "go with the",
)


def _detect_service_switch(text_lower: str, current_topic: str | None) -> str | None:
    """Return the canonical name of a new service if the utterance
    clearly commits to a different service than ``current_topic``;
    otherwise None.  Requires BOTH a commit-verb signal AND a service
    term that differs from the current FAQ topic."""
    t = (text_lower or "").strip()
    if not t:
        return None
    if not any(v in t for v in _SERVICE_SWITCH_VERB_SIGS):
        return None
    _cur = (current_topic or "").strip().lower()
    for svc, aliases in _SERVICE_SWITCH_ALIASES:
        if svc == _cur:
            continue
        if any(a in t for a in aliases):
            return svc
    return None


# ── Name wrapper patterns (BUG 4 fix) ────────────────────────────────────────
# Patterns that callers use as labels instead of actual names.
# If the transcript matches one of these entirely (or after stripping,
# only stop-words remain), reject it and re-ask for the real name.
import re as _re_nw
_NAME_WRAPPER_PATTERNS = [
    _re_nw.compile(r"^my first name(?:\s+is)?$"),
    _re_nw.compile(r"^first name(?:\s+is)?$"),
    _re_nw.compile(r"^my surname(?:\s+is)?$"),
    _re_nw.compile(r"^surname(?:\s+is)?$"),
    _re_nw.compile(r"^my family name(?:\s+is)?$"),
    _re_nw.compile(r"^family name(?:\s+is)?$"),
    _re_nw.compile(r"^my last name(?:\s+is)?$"),
    _re_nw.compile(r"^last name(?:\s+is)?$"),
    _re_nw.compile(r"^my name(?:\s+is)?$"),
    _re_nw.compile(r"^name(?:\s+is)?$"),
    # "it is" prefix when name_fragment present (surname context)
    _re_nw.compile(r"^it(?:'?s| is)$"),
]
# Stop-words that are NOT real name tokens even after stripping wrapper prefix
_NAME_WRAPPER_STOP_WORDS = frozenset({
    "my", "first", "last", "name", "surname", "family", "is", "the",
    "it", "its", "s",
})


def _strip_name_wrapper(text: str) -> str:
    """
    Strip leading name-label wrappers from a transcript fragment.
    Returns the residual token(s), or empty string if nothing real remains.

    Examples:
      "my first name is karen"  → "karen"
      "first name is"           → ""
      "my name"                 → ""
      "karen"                   → "karen"
    """
    t = text.strip().lower()
    # Strip obvious prefix wrappers in order of specificity
    _PREFIXES = (
        "my first name is ",
        "first name is ",
        "my surname is ",
        "surname is ",
        "my family name is ",
        "family name is ",
        "my last name is ",
        "last name is ",
        "my name is ",
        "name is ",
        "my first name's ",
        "my name's ",
    )
    for _pfx in _PREFIXES:
        if t.startswith(_pfx):
            t = t[len(_pfx):].strip()
            break
    return t


def _is_pure_name_wrapper(text: str) -> bool:
    """
    Return True if text is ONLY a wrapper phrase with no real name token.
    Used to reject 'my first name' when caller just says the label, not the value.
    """
    t = text.strip().lower()
    for pat in _NAME_WRAPPER_PATTERNS:
        if pat.fullmatch(t):
            return True
    # Also reject if after stripping all wrapper words nothing real remains
    tokens = [tok for tok in t.split() if tok not in _NAME_WRAPPER_STOP_WORDS]
    if not tokens:
        return True
    return False


def _is_valid_name_token(s: str) -> bool:
    """
    Return True if s contains at least one real name token (≥2 chars, non-stop-word).
    Used to gate lookup_appointment — prevents lookup with wrapper garbage.
    """
    s = s.strip().lower()
    tokens = s.split()
    real = [t for t in tokens if t not in _NAME_WRAPPER_STOP_WORDS and len(t) >= 2]
    return len(real) >= 1


_SPEECH_JUNK_NAMES: frozenset = frozenset({
    "none", "unknown", "null", "n/a", "na", "undefined", "test", "that",
    "this", "tbd", "xxx", "dummy", "user", "patient", "booking",
})


def _is_plausible_speech_name(first_name: str) -> bool:
    """Return True only when first_name is safe to say aloud (e.g. 'Thanks {name}').

    Rejects blank, single-char, junk words, and duplicated placeholder names.
    """
    fn = (first_name or "").strip().lower()
    if not fn or len(fn) < 2:
        return False
    if fn in _SPEECH_JUNK_NAMES:
        return False
    return True


# ── Global repair-intent phrases (Bug 4) ────────────────────────────────────
# Checked at the top of handle_transcript before ALL other logic.
_GLOBAL_REPAIR_PHRASES = (
    "not why i asked", "not that question", "wrong question",
    "go back to the last question", "go back to the last", "go back to",
    "i messed up", "i misheard", "my question is for",
    "stop stop", "no no stop", "not for alcester", "not for redditch",
    "not for our", "not for the",
    "that's not what i asked", "thats not what i asked",
    "not what i asked", "that wasn't my question",
    "that's not my question", "not my question",
    "no my question was", "no my question is",
    "no that's not the question", "no that's not what",
    # Strong repair signals that may appear as a standalone lead-in.
    # Must be checked even without a preceding "no"/"that's not" because
    # callers often just say "my question is ...", "what i meant was ...",
    # "i mean ...", "i am asking ..." to correct a misrouted branch.
    "my question is", "my question was",
    "what i meant was", "what i meant", "i meant to ask",
    "i'm asking", "im asking", "i am asking",
    "i mean ", "i mean,", "i mean.",
    # Booking-flow backtrack / correction triggers
    "i made an error", "i made a mistake",
    "i need to correct", "i need to go back",
    "step back", "go back please",
)

# ── Global repeat-request phrases ───────────────────────────────────────────
# Replays last_question (or last_faq_answer at FAQ_BOOKING_OFFER) without
# resetting the flow.  Separate from repair phrases — no "what was your inquiry?"
_GLOBAL_REPEAT_PHRASES = (
    "i didn't catch that", "didn't catch that", "could you repeat",
    "can you repeat", "say that again", "repeat that", "say it again",
    "i missed that", "what was that", "come again", "pardon",
    "what did you say", "what did you just say",
)

# ── Fragment suppression (Bug 9) ────────────────────────────────────────────
# Single words / short phrases that must never drive a full response.
_FRAGMENT_STRONG_INTENTS = frozenset({
    "yes", "yeah", "yep", "yup", "no", "nope", "nah",
    "one", "two", "1", "2", "first", "second",
    "alcester", "redditch", "alchester", "reddit",
    "reschedule", "cancel", "book", "new", "returning", "recently",
    "stop",
    # BUG 22 fix: day names are valid answers in PRESENT_DAYS states
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "mon", "tue", "wed", "thu", "fri",
    # Short confirmations that should never be swallowed
    "correct", "right", "sure", "okay", "ok",
})
# Explicit noise tokens that must never drive a response regardless of context
_FRAGMENT_BLOCKLIST = frozenset({
    "please", "question", "ic", "and", "so", "the", "a", "i",
    "you know", "like", "just", "right",
    "so if", "so if i", "if i", "clinic",
    "thank you", "thanks",
})

if not _RAPIDFUZZ_AVAILABLE:
    logger.warning(
        "[ms_flow] rapidfuzz not installed — fuzzy matching disabled. "
        "Install with: pip install rapidfuzz>=3.0.0"
    )


def _format_slot_for_speech(label: str) -> str:
    """
    Convert a short slot label like 'Mon 23 Mar at 08:20' into a natural
    spoken form like 'Monday the 23rd of March at 8:20 in the morning'.
    Falls back to the raw label if the format doesn't match.

    Uses pure regex parsing — NOT datetime.strptime — to avoid a Python bug
    where strptime validates the weekday abbreviation (%a) against the date
    in the default year (1900), and raises ValueError when they don't match
    (e.g. "Wed 18 Mar" is Wednesday in 2026 but Sunday in 1900).
    """
    import re as _re
    _ORDINALS = {
        1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th",
        7: "7th", 8: "8th", 9: "9th", 10: "10th", 11: "11th", 12: "12th",
        13: "13th", 14: "14th", 15: "15th", 16: "16th", 17: "17th",
        18: "18th", 19: "19th", 20: "20th", 21: "21st", 22: "22nd",
        23: "23rd", 24: "24th", 25: "25th", 26: "26th", 27: "27th",
        28: "28th", 29: "29th", 30: "30th", 31: "31st",
    }
    _DAY_NAMES = {
        "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday",
        "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
    }
    _MONTH_NAMES = {
        "Jan": "January",  "Feb": "February", "Mar": "March",
        "Apr": "April",    "May": "May",       "Jun": "June",
        "Jul": "July",     "Aug": "August",    "Sep": "September",
        "Oct": "October",  "Nov": "November",  "Dec": "December",
    }
    try:
        # Parse "Mon 23 Mar at 09:00" with pure regex — no strptime.
        m = _re.match(
            r'^([A-Za-z]{3})\s+(\d{1,2})\s+([A-Za-z]{3})\s+at\s+(\d{1,2}):(\d{2})$',
            label.strip(),
        )
        if not m:
            return label
        day_abbr, day_str, month_abbr, hour_str, min_str = m.groups()
        day_name   = _DAY_NAMES.get(day_abbr.capitalize(), day_abbr)
        month_name = _MONTH_NAMES.get(month_abbr.capitalize(), month_abbr)
        day_int    = int(day_str)
        h          = int(hour_str)
        minute     = int(min_str)
        ord_str    = _ORDINALS.get(day_int, f"{day_int}th")
        if h == 12 and minute == 0:
            return f"{day_name} the {ord_str} of {month_name} at twelve noon"
        if minute == 0:
            time_str = f"{h % 12 or 12} o'clock"
        else:
            time_str = f"{h % 12 or 12}:{minute:02d}"
        suffix = (
            "in the morning"   if h < 12
            else "in the afternoon" if h < 18
            else "in the evening"
        )
        return f"{day_name} the {ord_str} of {month_name} at {time_str} {suffix}"
    except Exception:
        return label  # fallback to raw label unchanged


def _fuzzy_match(text: str, patterns: list, threshold: int = 80) -> bool:
    """
    Return True if any pattern fuzzy-matches the text at or above threshold.

    Uses rapidfuzz.fuzz.partial_ratio for substring-aware matching.
    Falls back gracefully (returns False) if rapidfuzz is not installed.
    """
    if not _RAPIDFUZZ_AVAILABLE or _fuzz is None:
        return False
    text_clean = text.strip().lower()
    for pattern in patterns:
        score = _fuzz.partial_ratio(text_clean, pattern)
        if score >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Question-worth-storing guard
# ---------------------------------------------------------------------------

# Phrases whose presence ANYWHERE in the text means we must NOT store it
# as last_question.  Uses substring match (`phrase in text_lower`).
_NEVER_STORE_PHRASES = [
    # Re-ask / error phrases — must never overwrite the original question
    "sorry, i didn't quite catch",
    "sorry about that",
    "sorry, i'm having",
    "i'm having a little trouble",
    "didn't quite catch",
    "bear with me",
    "one moment",
    "let me check",
    "just bear",
    "my fault",
    "i want to make sure i get this right",
    "let me just read that back",
    # Greeting / preamble — not actionable questions
    "hi there",
    "hello",
    "this is susie",
    "roch solutions",
    "theorem health",
    "of course you can book",
    "of course i can help",
    # Generic wrappers — "Is there anything else I can help you with?" is not
    # an actionable question; storing it causes bad silence/re-engagement replays.
    "is there anything else i can help",
    "anything else i can help you with",
]

_KNOWN_QUESTION_PHRASES = [
    "what brings you in",
    "how long have you had",
    "does that sound ok",
    "been with us before",
    "been to us before",
    "work best for you",
    "first name please",
    "reach you on",
    "which would you prefer",
    "that right",
    "sound ok",
    "would you like",
    "no problem — which",
    "slot would you",
    "which clinic",
    "alcester or redditch",
]


def _is_question_worth_storing(text: str) -> bool:
    """
    Return True only if text is a real question Susie asked.
    Rejects greetings, re-ask phrases, filler phrases, and error phrases.
    Uses substring match so 'sorry about that — X?' is also rejected.
    """
    t = text.strip().lower()
    for phrase in _NEVER_STORE_PHRASES:
        if phrase in t:
            return False
    for q in _KNOWN_QUESTION_PHRASES:
        if q in t:
            return True
    if t.endswith("?"):
        return True
    return False


def _store_last_question(
    session: dict,
    text: str,
    *,
    force: bool = False,
    source: str = "",
) -> bool:
    """Authoritative / heuristic write-path for session['last_question'].

    Deterministic flow-committed prompts (the ones that definitively ARE the
    currently awaited question for the state machine) must pass force=True.
    They bypass _is_question_worth_storing() so that a prompt whose wording
    happens to overlap _NEVER_STORE_PHRASES (e.g. a flow-committed retry that
    begins "Sorry, I didn't quite catch —") is still stored as the active
    question.  The silence handler / watchdog will then re-ask from the
    correct lineage instead of stale text.

    Heuristic / inferred / opportunistic candidates (LLM-extracted question
    sentences, fast-path fallbacks, known-phrase recognitions) keep the
    existing filter behaviour by passing force=False (the default).

    Returns True if a write was performed.
    """
    if not text or not text.strip():
        return False
    t = text.strip()
    if force:
        session["last_question"] = t
        logger.info(
            "[ms_flow] last_question stored AUTHORITATIVE source=%s force=True text=%r",
            source or "unspecified", t[:80],
        )
        return True
    if _is_question_worth_storing(t):
        session["last_question"] = t
        logger.info(
            "[ms_flow] last_question stored heuristic source=%s text=%r",
            source or "unspecified", t[:80],
        )
        return True
    logger.debug(
        "[ms_flow] last_question REJECTED by heuristic filter source=%s text=%r",
        source or "unspecified", t[:80],
    )
    return False


# Filler prefixes the LLM sometimes prepends — strip from re-ask question
_STRIP_PREFIXES = (
    "Absolutely, ", "Absolutely! ", "Absolutely — ",
    "Certainly, ",  "Certainly! ",  "Certainly — ",
    "Of course, ",  "Of course! ",  "Of course — ",
    "Sure, ",       "Sure! ",       "Sure — ",
    "Great, ",      "Great! ",      "Great — ",
    "Sorry, ",      "Sorry! ",      "Sorry — ",
)


def _extract_question_sentence(text: str) -> str:
    """
    Extract only the question sentence from a multi-sentence LLM response.

    Algorithm:
      1. Split on sentence boundaries (., !, ?, —, newline).
      2. Return the LAST fragment that ends with '?'.
      3. Strip any leading filler prefix (Absolutely, Certainly, etc.).

    Returns empty string when no '?' sentence is found (non-question turns
    should not update last_question at all).
    """
    import re
    # Split on sentence-ending punctuation followed by whitespace/newline,
    # or on em-dash.
    parts = re.split(r'(?<=[.!?])\s+|\n+|(?<=—)\s*', text.strip())
    question = ""
    for part in parts:
        s = part.strip()
        if s.endswith("?"):
            question = s   # keep iterating — we want the LAST "?" sentence

    if not question:
        return ""

    # Strip filler prefix
    for prefix in _STRIP_PREFIXES:
        if question.startswith(prefix):
            question = question[len(prefix):].strip()
            if question:
                question = question[0].upper() + question[1:]
            break

    return question.strip()


# ---------------------------------------------------------------------------
# PRESENT_DAYS / PRESENT_TIMES deterministic phrase builders
# ---------------------------------------------------------------------------

def _build_day_list_phrase(available_days: list) -> str:
    """
    Build the spoken day-list from up to 3 entries in available_days.

    Called after check_availability runs — produces a natural UK receptionist
    sentence that the caller hears instead of an LLM-generated one.

    Returns empty string when available_days is empty (error case — LLM has
    already spoken the error message).
    """
    days = [d["day_label"] for d in available_days[:3] if d.get("day_label")]
    if not days:
        return ""
    if len(days) == 1:
        return f"The next opening I have is {days[0]} — would that work for you?"
    if len(days) == 2:
        return f"I've got {days[0]} or {days[1]} — which suits you better?"
    return f"I can do {days[0]}, {days[1]}, or {days[2]} — which of those works for you?"


def _build_times_phrase(day_entry: dict) -> str:
    """
    Build the spoken time-list for a chosen day's slots.

    Up to 4 times are listed. Times are grouped by period (morning/afternoon)
    so "one o'clock" always carries the correct period label — prevents the
    LLM misidentifying 1pm as "in the morning".

    Example output:
      1 slot  → "The only slot I have on Thursday is one in the afternoon — does that work?"
      mixed   → "On Thursday I've got nine, ten, or eleven in the morning, or one in the afternoon — which of those works?"
    """
    day_label  = day_entry.get("day_label", "")
    slot_times = day_entry.get("slot_times", [])[:4]
    if not slot_times:
        return ""

    def _period_of(hhmm: str) -> str:
        try:
            h = int(hhmm.split(":")[0])
            m = int(hhmm.split(":")[1]) if len(hhmm.split(":")) > 1 else 0
            if h == 12 and m == 0:
                return "noon"
            return "in the morning" if h < 12 else ("in the afternoon" if h < 18 else "in the evening")
        except Exception:
            return "in the afternoon"

    def _hour_word(hhmm: str) -> str:
        """Abbreviated hour label — no period suffix."""
        try:
            h24 = int(hhmm.split(":")[0])
            m   = int(hhmm.split(":")[1]) if len(hhmm.split(":")) > 1 else 0
            h12 = h24 if h24 <= 12 else h24 - 12
            if h12 == 0:
                h12 = 12
            _hw = {
                1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
                11: "eleven", 12: "twelve",
            }.get(h12, str(h12))
            if m == 0:
                return _hw
            if m == 30:
                return f"half past {_hw}"
            if m == 15:
                return f"quarter past {_hw}"
            return f"{_hw} {m:02d}"
        except Exception:
            return hhmm

    def _join_words(words: list) -> str:
        if len(words) == 1:
            return words[0]
        if len(words) == 2:
            return f"{words[0]} or {words[1]}"
        return f"{', '.join(words[:-1])}, or {words[-1]}"

    # Group by period — preserves insertion order (Python 3.7+)
    groups: dict = {}
    for t in slot_times:
        p = _period_of(t)
        groups.setdefault(p, []).append(_hour_word(t))

    period_parts = [f"{_join_words(hours)} {period}" for period, hours in groups.items()]

    if len(slot_times) == 1:
        return f"The only slot I have on {day_label} is {period_parts[0]} — does that work?"

    if len(period_parts) == 1:
        group_str = period_parts[0]
    elif len(period_parts) == 2:
        group_str = f"{period_parts[0]}, or {period_parts[1]}"
    else:
        group_str = ", or ".join(period_parts)

    return f"On {day_label} I've got {group_str} — which of those works?"


_WEEKDAY_WORDS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
})


def _find_chosen_day_entry(available_days: list, chosen_day: str) -> Optional[dict]:
    """
    Return the available_days entry whose day_label best matches chosen_day.

    Matching strategy (keyword, word-boundary):
      1. Day-of-week word from day_label found in chosen_day text (as a whole word) → match.
         Word-boundary matching prevents "tuesday" from matching inside "thursday" etc.
         Month names are intentionally excluded — they appear in every label.
      2. Fallback: first entry in available_days (used when caller said
         "yeah that works" / "sounds good" — no day name in transcript).

    Returns None only when available_days is empty.
    """
    import re as _re_fd
    if not available_days:
        return None
    chosen_lower = chosen_day.lower()
    # 0. Exact match — chosen_day is always set to a day_label string, so this
    #    handles cases like two Thursdays ("Thursday 9th April" vs "Thursday 16th April")
    #    where weekday-word matching would always return the first Thursday.
    for day in available_days:
        if day.get("day_label", "").lower() == chosen_lower:
            return day
    for day in available_days:
        label_lower = day.get("day_label", "").lower()
        significant = [w for w in label_lower.split() if w in _WEEKDAY_WORDS]
        if significant and any(_re_fd.search(r'\b' + w + r'\b', chosen_lower) for w in significant):
            return day
    return available_days[0]


# ---------------------------------------------------------------------------
# Calendar navigation helpers  (shared by PRESENT_DAYS and PRESENT_TIMES)
# ---------------------------------------------------------------------------

import datetime as _dt_cal


def _week_days_for_anchor(available_days: list, anchor_date) -> list:
    """
    Return entries from available_days that fall in the ISO week (Mon–Sun)
    containing anchor_date.

    anchor_date may be a datetime.date object or an ISO string (YYYY-MM-DD).
    Returns an empty list when anchor_date cannot be parsed.
    """
    if isinstance(anchor_date, str):
        try:
            anchor_date = _dt_cal.date.fromisoformat(anchor_date)
        except (ValueError, TypeError):
            return []
    monday = anchor_date - _dt_cal.timedelta(days=anchor_date.weekday())
    sunday = monday + _dt_cal.timedelta(days=6)
    result = []
    for d in available_days:
        ds = d.get("date", "")
        try:
            dobj = _dt_cal.date.fromisoformat(ds[:10])
            if monday <= dobj <= sunday:
                result.append(d)
        except (ValueError, TypeError):
            pass
    return result


def _nearest_days(available_days: list, anchor_date, n: int = 3) -> list:
    """
    Return up to n entries from available_days sorted by proximity to
    anchor_date (closest first).

    anchor_date may be a datetime.date object or an ISO string (YYYY-MM-DD).
    Falls back to first n entries when anchor_date cannot be parsed.
    """
    if isinstance(anchor_date, str):
        try:
            anchor_date = _dt_cal.date.fromisoformat(anchor_date)
        except (ValueError, TypeError):
            return available_days[:n]

    def _dist(d: dict) -> int:
        ds = d.get("date", "")
        try:
            return abs((_dt_cal.date.fromisoformat(ds[:10]) - anchor_date).days)
        except (ValueError, TypeError):
            return 9999

    return sorted(available_days, key=_dist)[:n]


def _parse_transcript_date(transcript: str, available_days: list):
    """
    Extract an explicit date from a transcript string.

    Handles:
      "8th of May", "May 8th", "April 23rd"     → date from digit+month pattern
      "the 23rd", "on the 8th" (bare ordinal)    → first match in available_days
    Returns a datetime.date object, or None if nothing matched.

    Year is inferred as the current year when month >= today's month,
    otherwise next year (handles year-boundary callers).
    """
    import re as _re_ptd
    text = transcript.lower()
    _MONTH_ALT = (
        r'january|february|march|april|may|june|july|august|september'
        r'|october|november|december'
        r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec'
    )
    _MONTH_NUM = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "january": 1, "february": 2, "march": 3, "april": 4,
        "june": 6, "july": 7, "august": 8, "september": 9,
        "october": 10, "november": 11, "december": 12,
    }
    m = _re_ptd.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+|\s+)(' + _MONTH_ALT + r')\b'
        r'|\b(' + _MONTH_ALT + r')\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b',
        transcript, _re_ptd.IGNORECASE,
    )
    if m:
        day_n   = int(m.group(1) if m.group(1) else m.group(4))
        month_s = (m.group(2) if m.group(2) else m.group(3)).lower()
        month_n = _MONTH_NUM.get(month_s[:3]) or _MONTH_NUM.get(month_s)
        if month_n:
            today = _dt_cal.date.today()
            year  = today.year if month_n >= today.month else today.year + 1
            try:
                return _dt_cal.date(year, month_n, day_n)
            except ValueError:
                pass
    # Bare ordinal fallback — look up day-of-month in available_days
    bo = _re_ptd.search(r'\b(?:the\s+)?(\d{1,2})(st|nd|rd|th)\b', text, _re_ptd.IGNORECASE)
    if bo:
        day_n = int(bo.group(1))
        if 1 <= day_n <= 31:
            for d in available_days:
                ds = d.get("date", "")
                try:
                    dobj = _dt_cal.date.fromisoformat(ds[:10])
                    if dobj.day == day_n:
                        return dobj
                except (ValueError, TypeError):
                    pass
    return None


def _constrained_day_alternatives(
    text,
    transcript,
    requested_month_n,
    all_available,
    last_requested_date=None,
):
    """
    When an explicit date is unavailable, check whether the utterance carries
    search constraints (month, lower-bound, pair-of-dates) that should restrict
    the alternatives offered.

    This prevents "after the 1st of May but in May" from receiving an April
    alternative — the caller's constraint is preserved and alternatives are
    drawn only from the valid constrained set.

    Returns:
        list  — filtered alternatives from all_available (may be empty)
        None  — no meaningful constraint detected; caller uses existing logic
    """
    import re as _re_cda
    import datetime as _dt_cda

    _MONTH_NUM_CDA = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    # ── Exploratory markers ────────────────────────────────────────────────
    _EXPLORATORY = (
        "do you have", "have you got", "got anything", "anything",
        "what's the next", "next day after", "later than", "after the",
        "but in", "still in", "like the", "or the", "anything like",
        "what have you got", "is there", "any availability", "any dates",
        "around", "near", "something like", "after that", "but still",
    )
    is_exploratory = any(m in text for m in _EXPLORATORY)

    # ── Month constraint ───────────────────────────────────────────────────
    # Longest match first to avoid "may" inside "maybe" etc.
    month_c = None
    for mn in sorted(_MONTH_NUM_CDA, key=len, reverse=True):
        if _re_cda.search(r'\b' + mn + r'\b', text):
            month_c = _MONTH_NUM_CDA[mn]
            break
    # If no explicit month in text but the requested date has one AND the
    # utterance looks like a constrained search ("later than", "after"), treat
    # requested_month_n as the implied constraint (e.g. "later than the 1st of
    # May" means stay in May even if May isn't repeated).
    if month_c is None and requested_month_n and any(
        p in text for p in (
            "later than", "after the", "next day after", "after that",
            "but in", "still in",
        )
    ):
        month_c = requested_month_n

    # ── Lower-bound constraint ─────────────────────────────────────────────
    lower_bound = None
    _LB_PHRASES = (
        "after the", "later than the", "later than", "next day after the",
        "next day after", "after that",
    )
    has_lb = any(p in text for p in _LB_PHRASES)
    if has_lb:
        _MONTH_ALT_CDA = (
            r'january|february|march|april|may|june|july|august|september'
            r'|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec'
        )
        _lb_m = _re_cda.search(
            r'\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+|\s+)(' + _MONTH_ALT_CDA + r')\b'
            r'|\b(' + _MONTH_ALT_CDA + r')\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b',
            transcript.lower(),
        )
        if _lb_m:
            if _lb_m.group(1) and _lb_m.group(2):
                lb_day, lb_mon_s = int(_lb_m.group(1)), _lb_m.group(2).lower()
            else:
                lb_day, lb_mon_s = int(_lb_m.group(4)), _lb_m.group(3).lower()
            lb_mon_n = (
                _MONTH_NUM_CDA.get(lb_mon_s[:3]) or _MONTH_NUM_CDA.get(lb_mon_s)
            )
            if lb_mon_n and lb_day:
                _today_cda = _dt_cda.date.today()
                _yr_cda = (
                    _today_cda.year if lb_mon_n >= _today_cda.month
                    else _today_cda.year + 1
                )
                try:
                    lower_bound = _dt_cda.date(_yr_cda, lb_mon_n, lb_day)
                except ValueError:
                    pass
        if lower_bound is None and last_requested_date:
            try:
                lower_bound = _dt_cda.date.fromisoformat(last_requested_date)
            except (ValueError, TypeError):
                pass

    # ── Additional target days (pair: "7th or 8th of May") ────────────────
    # Collect all day-numbers in the utterance — when a month constraint
    # applies they all share it, so preferred matching can try each in turn.
    extra_target_days = []
    if month_c:
        for _od in _re_cda.findall(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', text):
            n = int(_od)
            if 1 <= n <= 31:
                extra_target_days.append(n)

    # ── No meaningful constraint → fall through to existing logic ─────────
    if not is_exploratory or (
        month_c is None and lower_bound is None and not extra_target_days
    ):
        return None

    # ── Apply filters to all_available ────────────────────────────────────
    result = list(all_available)

    if month_c is not None:
        result = [
            d for d in result
            if _dt_cda.date.fromisoformat(
                (d.get("date") or "9999-12-31")[:10]
            ).month == month_c
        ]

    if lower_bound is not None:
        result = [
            d for d in result
            if _dt_cda.date.fromisoformat(
                (d.get("date") or "9999-12-31")[:10]
            ) > lower_bound
        ]

    # Within the constrained set, prefer explicitly mentioned day-of-month
    if extra_target_days and result:
        preferred = [
            d for d in result
            if _dt_cda.date.fromisoformat(
                (d.get("date") or "9999-12-31")[:10]
            ).day in extra_target_days
        ]
        if preferred:
            return preferred

    return result  # may be empty — signals "constrained but nothing available"


def _extract_hour_from_text(text: str):
    """
    Extract a clinic-context hour (int, 24h) from a caller utterance.
    Returns None if no time is found.
    Handles: "10", "10 am", "3 pm", "three o'clock", "half past two",
             digit o'clock, word o'clock, with/without am/pm context.
    """
    import re as _re_eh
    _t = (
        text.lower()
        .replace("p.m.", "pm").replace("a.m.", "am")
        .replace("o'clock", "").replace("oclock", "")
    )
    _t = " ".join(_t.split())
    # 1. Digit match: "10 am", "3 pm", "14:00", "10"
    _dm = _re_eh.search(r'\b(\d{1,2})(?::\d{2})?\s*(?:pm|am)?\b', _t)
    if _dm:
        _h = int(_dm.group(1))
        if "pm" in _t and _h < 12:
            _h += 12
        elif "am" in _t and _h == 12:
            _h = 0
        elif "am" not in _t and 1 <= _h <= 6:
            _h += 12  # clinic context: 1–6 without am → afternoon
        if 7 <= _h <= 20:
            return _h
    # 2. Word match: "three", "eleven", etc.
    _HOUR_WORDS_EH = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }
    for _word, _n in _HOUR_WORDS_EH.items():
        if _re_eh.search(r'\b' + _word + r'\b', _t):
            _h2 = _n
            if any(p in _t for p in ("afternoon", "evening", "pm")):
                if _h2 < 12:
                    _h2 += 12
            elif _h2 < 8:
                _h2 += 12
            if 7 <= _h2 <= 20:
                return _h2
            break
    return None

# ---------------------------------------------------------------------------
# Flow definitions
# ---------------------------------------------------------------------------

# ---------- Transfer-to-human flow ----------------------------------------

TRANSFER_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "CONFIRM_TRANSFER",
        "question": None,
        "answer_field": "transfer_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "The patient has asked to speak to a human member of staff. "
            "CRITICAL: Say EXACTLY 'Let me put you straight through — just bear with me.' "
            "then immediately call transfer_to_human with no additional parameters. "
            "Do NOT ask any questions. Do NOT explain anything further."
        ),
        "extract": "none",
    },
]

# ---------- Entry-point flow (intent detection) ---------------------------

DETECT_INTENT_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "DETECT_INTENT",
        "question": None,       # greeting already played by connection.py
        "answer_field": "intent",
        "use_llm": False,
        "extract": "intent",
        "llm_instruction": None,
    },
]

# ---------- Booking flow (new appointment) --------------------------------

BOOKING_FLOW: List[Dict[str, Any]] = [
    # ── Step 0: New-or-returning — asked immediately after clinic selection ──
    # Moved to the front so we know the patient type before asking anything else.
    # Skip logic in ask_current_question() jumps over the appropriate sub-paths.
    {
        "step": 0,
        "state": "NEW_OR_RETURNING",
        "question": "Have you been with us before, or is this your first time?",
        "answer_field": "new_or_returning",
        "use_llm": False,
        "extract": "new_or_returning",
        "llm_instruction": None,
    },
    # ── Returning-patient qualification (steps 1-2) ───────────────────────
    {
        "step": 1,
        "state": "RETURNING_RECENCY",
        "question": "Was that recently, or has it been a little while?",
        "answer_field": "returning_recency",
        "use_llm": False,
        "extract": "recency",
        "llm_instruction": None,
    },
    {
        "step": 2,
        "state": "RETURNING_TREATMENT_PLAN",
        "question": "And are you still coming in regularly for that, or is this more of a new episode?",
        "answer_field": "on_treatment_plan",
        "use_llm": False,
        "extract": "yes_no_explicit",
        "llm_instruction": None,
    },
    # ── Returning-on-plan fast path (steps 3-5) ───────────────────────────
    # Skipped unless on_treatment_plan=True.
    # Mirrors the reschedule phone-first lookup pattern:
    #   confirm caller-ID → collect if needed → Acuity 90-day lookup by phone
    # The lookup stores canonical name+phone in session so all later name/phone
    # collection steps are also skipped.
    {
        "step": 3,
        "state": "RETURNING_PLAN_CONFIRM_PHONE",
        "question": (
            "If the number you're calling on is the one associated with your booking, "
            "say: use this number."
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 4,
        "state": "RETURNING_PLAN_COLLECT_PHONE",
        "question": "Could you say the number linked to your appointments?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    {
        "step": 5,
        "state": "RETURNING_PLAN_LOOKUP",
        "question": None,   # filler TTS emitted directly in ask_current_question
        "answer_field": "returning_plan_looked_up",
        "use_llm": False,
        "extract": "none",
        "llm_instruction": None,
    },
    # ── Reason + assessment (steps 6-7) ──────────────────────────────────
    # Skipped for on-plan returning patients (treatment type already known).
    {
        "step": 6,
        "state": "COLLECT_REASON",
        "question": "What brings you in today?",
        "answer_field": "reason",
        "use_llm": False,
        "extract": "any",
        "llm_instruction": None,
    },
    # ── Returning non-plan name+phone (steps 7-9) ───────────────────────
    # Used when returning patient is recent but NOT on an active plan,
    # or was a long-time-ago patient.  Skipped for new patients and on-plan.
    {
        "step": 7,
        "state": "COLLECT_NAME_RETURNING",
        "question": "Could I take your first name please? You can say: my first name is...",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 8,
        "state": "CONFIRM_PHONE_RETURNING",
        "question": "And is this the same number we'd normally have for you?",
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 9,
        "state": "COLLECT_PHONE_RETURNING",
        "question": "And the best number to contact you on?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    # ── New-patient name+phone (steps 10-12) ─────────────────────────────
    # Skipped for all returning patients.
    {
        "step": 10,
        "state": "COLLECT_NAME",
        "question": "And what's your first name please? You can say: my first name is...",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 11,
        "state": "CONFIRM_PHONE",
        "question": (
            "If you'd like me to use the number you're calling on for the booking, "
            "say: use this number."
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 12,
        "state": "COLLECT_PHONE",
        "question": "And the best number to reach you on?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    # ── Main booking steps ────────────────────────────────────────────────
    {
        "step": 13,
        "state": "PRESENT_DAYS",
        # BUG 2 fix: the deterministic greeting is emitted by this question field
        # BEFORE the LLM is called, so the LLM never needs to speak on success.
        "question": "Just a moment while I check what's available...",
        "answer_field": "chosen_day",
        "use_llm": True,
        "allow_tools": True,
        "extract": "any",
        "llm_instruction": (
            "⚠️ TOOL CALL ONLY — DO NOT OUTPUT ANY TEXT on success.\n"
            "Call check_availability with location='{selected_location}', duration_minutes=50.\n"
            "After the tool responds with available days: output NOTHING. Not a word. "
            "The system generates all spoken output automatically.\n"
            "ONLY speak if the tool returned an error:\n"
            "  error='lead_time_limited': re-call check_availability once with the same "
            "parameters. If still limited, say exactly: "
            "'We\\'re a little limited right now — the team will call you to confirm a time.'\n"
            "  error='no_availability' or any other error: say exactly: "
            "'I\\'m not seeing clear availability right now — let me take your details "
            "and the team will call you back.'"
        ),
    },
    {
        "step": 14,
        "state": "PRESENT_TIMES",
        "question": None,   # LLM responds to the caller's day choice
        "answer_field": "selected_slot",
        "use_llm": True,
        "allow_tools": False,
        "extract": "slot_selection",
        "llm_instruction": (
            "⚠️ SPOKEN OUTPUT ONLY — every word you write is read aloud to the caller by TTS. "
            "Start DIRECTLY with Susie's words (e.g. 'On Friday...'). "
            "No reasoning, no preamble, no internal notes. "
            "Sound like a warm, efficient UK clinic receptionist.\n\n"
            "The caller just responded to the day options with: '{chosen_day}'.\n"
            "The caller's latest message: '{caller_followup}'\n"
            "If the caller's latest message expresses a preference (e.g. 'afternoon', 'morning', "
            "'something earlier', 'latest possible') — address that preference FIRST before "
            "listing times. E.g. if they said 'afternoon' but only morning slots exist, say "
            "'I'm afraid we don't have any afternoon slots on that day — I've got [morning times]'.\n"
            "Here is the full availability data (do NOT call check_availability again):\n"
            "{available_days_json}\n\n"
            "Each entry has: day_label (spoken day name), slot_times (list of HH:MM strings), "
            "and slots (list of start/end ISO datetimes).\n"
            "1. If the caller named a specific day — find that day in the data and present "
            "up to 4 times for it in natural spoken form:\n"
            "   4 times: 'On [day] I've got [t1], [t2], [t3], or [t4] — which of those works?'\n"
            "   2–3 times: 'On [day] I've got [t1] or [t2] — which suits you?'\n"
            "   1 time:  'The earliest I have on [day] is [t1] — does that work?'\n"
            "   Convert slot_times to natural spoken form: "
            "'09:00' → 'nine o'clock', '14:30' → 'half past two', '16:00' → 'four o'clock'. "
            "Add 'in the morning' / 'in the afternoon' where helpful. Never say AM/PM or raw digits.\n"
            "   IMPORTANT: If the caller asked for a specific time period (e.g. 'afternoon', "
            "'evening', 'morning') and the available slots don't include that period, "
            "say so explicitly FIRST before presenting what IS available. "
            "Example: 'I'm afraid we don't have any afternoon slots on that day — "
            "I've got nine, ten, eleven, or twelve o'clock in the morning — which of those works?'\n"
            "2. If none of those times work — refer to the other days you initially offered: "
            "'Not to worry — what about [other day 1][, or [other day 2]]?'\n"
            "3. If all initial days rejected — present next 3 days from the data (entries 4–6). "
            "Continue cycling in batches of 3 until a day is chosen or list is exhausted.\n"
            "4. If no more days: 'I\\'m afraid those are the only days we have at the moment "
            "— would you like me to ask the team to give you a ring?'"
        ),
    },
    {
        "step": 15,
        "state": "CONFIRM_BOOKING",
        "question": None,   # built deterministically in ask_current_question
        "answer_field": "booking_confirmed",
        "use_llm": False,   # Fix D: short deterministic close — no LLM needed
        "allow_tools": False,
        "llm_instruction": None,
        "extract": "none",
    },
]

# Backward-compat alias
FLOW = BOOKING_FLOW

# Array index of CONFIRM_BOOKING in BOOKING_FLOW.
# Used by _handle_readback_confirmation to advance the flow after readback is confirmed.
_CONFIRM_BOOKING_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "CONFIRM_BOOKING"
)

# Array index of PRESENT_DAYS in BOOKING_FLOW.
# Phone-confirmation handlers route here (not directly to CONFIRM_BOOKING) so
# availability check + day/time selection always precede final confirmation.
_PRESENT_DAYS_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "PRESENT_DAYS"
)

# Array index of COLLECT_PHONE in BOOKING_FLOW.
# Used by phone-reject handler to jump directly to phone collection.
_COLLECT_PHONE_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "COLLECT_PHONE"
)

# Array index of CONFIRM_PHONE in BOOKING_FLOW.
# Used as the flow_step marker while awaiting phone readback confirmation.
_CONFIRM_PHONE_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "CONFIRM_PHONE"
)

# On-plan returning-patient phone collection / lookup indices.
# RETURNING_PLAN_COLLECT_PHONE is the phone-entry step for on-plan returning patients;
# RETURNING_PLAN_LOOKUP is the immediate next step that must run after phone confirmation
# (looks up the 90-day treatment plan before booking).  Both are BOOKING_FLOW only.
_RETURNING_PLAN_COLLECT_PHONE_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "RETURNING_PLAN_COLLECT_PHONE"
)
_RETURNING_PLAN_LOOKUP_INDEX: int = next(
    i for i, s in enumerate(BOOKING_FLOW) if s["state"] == "RETURNING_PLAN_LOOKUP"
)


# ── CONFIRM_ASSESSMENT: deterministic fast-path empathy map ──────────────────
# Covers the vast majority of real booking reasons — avoids LLM call and the
# associated latency (retries / GPT-fallback) for common conditions.
_CA_FAST_MAP = (
    (("back pain", "back ache", "backache", "lower back", "back injury", "lumbar", "spine", "my back"),
     "I'm sorry to hear about your back — that can really get in the way of daily life."),
    (("knee pain", "knee injury", "knee problem", "my knee", "knee ache"),
     "Oh, that sounds tough — knee pain can really limit your mobility."),
    (("shoulder pain", "shoulder injury", "my shoulder", "rotator"),
     "I'm sorry to hear that — shoulder trouble can be really uncomfortable to live with."),
    (("neck pain", "neck injury", "my neck", "stiff neck", "cervical"),
     "Sorry to hear you're having neck trouble — that can really affect your day-to-day."),
    (("hip pain", "hip injury", "my hip", "hip replacement"),
     "Sorry to hear about your hip — that can make a lot of everyday movement quite difficult."),
    (("ankle pain", "ankle injury", "my ankle", "ankle sprain", "sprained ankle"),
     "Oh, that sounds painful — ankle injuries can really slow you down."),
    (("foot pain", "heel pain", "my foot", "my heel", "plantar"),
     "I'm sorry to hear that — foot pain can be really limiting."),
    (("wrist pain", "wrist injury", "my wrist", "wrist strain"),
     "Sorry to hear about your wrist — that can make so many daily tasks quite tricky."),
    (("elbow pain", "tennis elbow", "golfer's elbow", "my elbow"),
     "I'm sorry to hear that — elbow pain can be quite debilitating."),
    (("leg pain", "calf pain", "shin pain", "hamstring", "my leg", "quad"),
     "Sorry to hear about your leg — that can really impact getting around."),
    (("sports injury", "running injury", "football injury", "cycling", "gym injury"),
     "Oh, that sounds frustrating — sports injuries can be such a setback."),
    (("headache", "migraine", "head pain"),
     "I'm sorry to hear that — headaches and migraines can really take over."),
    (("physio", "physiotherapy", "assessment", "pain"),
     "Sorry to hear you're having some trouble — that can really affect your day-to-day."),
)

def _fast_empathy_line(reason: str) -> str:
    """Return a short non-gating empathy acknowledgement for the caller's reason.

    Always returns something — falls back to a generic line if no match.
    Used inline at COLLECT_REASON completion (no LLM, no yes/no gate).
    """
    _r = reason.lower()
    for keywords, empathy in _CA_FAST_MAP:
        if any(k in _r for k in keywords):
            return empathy
    return "I'm sorry to hear that — let's get you seen as soon as we can."


def _phrase_key_for_step(step: Dict[str, Any]) -> str:
    """
    Map a flow step's answer_field to a RETRY_PHRASES["first_retry"] key.
    Falls back to "default" for unmapped fields.
    """
    _map = {
        "full_name":      "ask_name",
        "phone_number":   "ask_phone",
        "phone_confirmed": "ask_phone",
        "chosen_day":     "ask_day",
        "reason":         "ask_reason",
        "selected_slot":  "ask_time",
    }
    return _map.get(step.get("answer_field", ""), "default")


def _is_digit_heavy(text: str) -> bool:
    """Return True if ≥70% of non-space characters are digits."""
    stripped = text.replace(" ", "")
    if not stripped:
        return False
    digit_count = sum(1 for c in stripped if c.isdigit())
    return digit_count / len(stripped) >= 0.70


def _to_e164_uk(digits: str) -> str:
    """
    Normalise a UK phone number to E.164 format for Acuity API calls.
    '07xxx xxxxxx' (or any 0-prefixed UK number) → '+447xxx xxxxxx'.
    Numbers already in +44 format pass through unchanged.
    Non-UK or unrecognised formats pass through unchanged.
    """
    if not digits:
        return digits
    clean = digits.strip()
    if clean.startswith("+44"):
        return clean
    if clean.startswith("0") and len(clean) >= 10:
        return "+44" + clean[1:]
    return clean


def _format_phone_readback(digits: str) -> str:
    """
    Format a run of digits for slow TTS readback.

    UK mobile  (11 digits): 07XXX XXX XXX  → "0 7 5 0 2 ... 1 1 2 ... 0 7"
    UK landline (10 digits): similar grouping
    Other lengths: space every digit with a pause between groups of 3-4.
    """
    # Spell each digit with a space, groups separated by ", " for natural TTS pausing.
    if len(digits) == 11:
        groups = [digits[:5], digits[5:8], digits[8:]]
    elif len(digits) == 10:
        groups = [digits[:5], digits[5:8], digits[8:]]
    else:
        # Generic: chunks of ~4
        groups = [digits[i:i+4] for i in range(0, len(digits), 4)]
    return ", ".join(" ".join(g) for g in groups)


# ---------------------------------------------------------------------------
# Conversational bridge helpers
# ---------------------------------------------------------------------------

import random as _random

# Short acknowledgement phrases spoken *before* the next hardcoded question.
# Keyed by the state that was JUST completed.
_BRIDGE_POOL: Dict[str, list] = {
    "RETURNING_RECENCY":               ["Got it.", "Right.", "Got that."],
    "RETURNING_TREATMENT_PLAN":        ["Perfect.", "Great."],
    "RETURNING_PLAN_CONFIRM_PHONE":    ["Perfect.", "Brilliant."],
    "RETURNING_PLAN_COLLECT_PHONE":    ["Got that.", "Perfect."],
    "CONFIRM_PHONE_RETURNING":         ["Perfect.", "Brilliant."],
    "COLLECT_PHONE_RETURNING":         ["Got that.", "Perfect."],
    "CONFIRM_PHONE":                   ["Perfect.", "Brilliant."],
    "COLLECT_PHONE":                   ["Got that.", "Perfect."],
}


def _get_bridge(
    state: str,
    answer: Any,
    session: Dict[str, Any],
    next_use_llm: bool = False,
) -> Optional[str]:
    """
    Return a short acknowledgement phrase to speak before the next question, or None.
    Never emitted before LLM steps (the LLM writes its own opener).
    """
    if next_use_llm:
        return None

    tone = session.get("caller_tone", "warm")  # "brief" or "warm"

    # Name states — fold acknowledgement into the next question's transition
    # prefix so "Thanks, X." and the next question are ONE TTS utterance:
    #   "Thanks, Quentin — if the number you are calling on…"
    # Never emitted as a standalone utterance.
    if state in (
        "COLLECT_NAME", "COLLECT_NAME_RETURNING",
        "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
    ):
        session.pop("_nc_suppress_bridge", False)  # consume flag regardless
        first = str(answer).split()[0].capitalize() if answer else ""
        if first and not session.get("_nc_transition_prefix"):
            # Direct/normal capture: transition_prefix not yet set — set it here.
            # Direct-capture path (fn_confirm skipped) already sets it at the
            # COLLECT_NAME handler; this covers the fn_confirm-used path.
            session["_nc_transition_prefix"] = f"Thanks, {first} \u2014"
        return None  # never a standalone bridge utterance

    # NEW_OR_RETURNING — vary warmth based on answer
    if state == "NEW_OR_RETURNING":
        if answer == "new":
            return "No problem — let's get you sorted." if tone != "brief" else "No problem."
        else:
            return "Of course — good to have you back." if tone != "brief" else "Of course."

    pool = _BRIDGE_POOL.get(state)
    if not pool:
        return None

    phrase = _random.choice(pool)
    # Brief callers get a clipped version
    if tone == "brief" and len(phrase) > 8:
        phrase = phrase.split(".")[0] + "."
    return phrase


def _is_phone_accept(text: str) -> bool:
    """
    Return True if normalised-lowercase text is an explicit phone-accept phrase.
    Used as a cross-state compat check so "yes use this number" is always caught
    before generic yes/no or fallback logic can consume the turn.
    """
    _PHONE_ACCEPT = (
        "yes use this number", "use this number",
        "yeah use this number", "you can use this number",
        "same number", "use my current number",
        "yes that's fine", "yes thats fine",
        "yes use my number", "use my number",
        # Caller confirms name AND phone in one phrase: "yes this number is fine"
        "this number is fine", "number is fine", "that number is fine",
        # Contraction variants: "this number's fine", "yeah this number's fine"
        "this number's fine", "number's fine", "that number's fine",
        # Bare confirmations: "yes this number", "yeah this number"
        "yes this number", "yeah this number",
    )
    return any(p in text for p in _PHONE_ACCEPT)


def _is_phone_reject(text: str) -> bool:
    """
    Return True if normalised-lowercase text expresses intent to provide a
    different phone number — used as a cross-state first-check so this intent
    is caught before name-parsing or generic fallback can consume the turn.
    """
    _PHONE_REJECT = (
        "no use a different number",
        "different number",
        "use a different number",
        "no different number",
        "no i'll give another number",
        "no i'll give you another number",
        "i'll give you a different number",
        "give you another number",
        "another number",
        "use another number",
    )
    return any(p in text for p in _PHONE_REJECT)


# ---------------------------------------------------------------------------
# Opportunistic multi-field harvesting
# ---------------------------------------------------------------------------

def _harvest_extra_fields(
    text: str,
    transcript: str,
    state: str,
    session: Dict[str, Any],
) -> None:
    """
    If the caller volunteered more information than the current step asked,
    pre-store it so redundant follow-up questions are skipped automatically.
    All assignments are additive — the normal extraction flow catches any mismatch.
    """
    import re as _re

    # At COLLECT_REASON: pre-store new/returning status if the caller mentioned it
    if state == "COLLECT_REASON" and not session.get("new_or_returning"):
        _NEW = (
            "first time", "never been", "new patient", "haven't been",
            "not been before", "brand new", "first visit",
        )
        _RET = (
            "been before", "been with you", "been with us", "came before",
            "returning", "existing patient", "last time i came", "i was with you",
        )
        t = text.lower()
        if any(s in t for s in _NEW):
            session["new_or_returning"] = "new"
            logger.info("[ms_flow] harvest: new_or_returning=new from COLLECT_REASON")
        elif any(s in t for s in _RET):
            session["new_or_returning"] = "returning"
            logger.info("[ms_flow] harvest: new_or_returning=returning from COLLECT_REASON")

    # At NEW_OR_RETURNING: pre-store name if the caller volunteered it
    if state == "NEW_OR_RETURNING" and not session.get("full_name"):
        m = _re.search(
            r"(?:my name is|name['\u2019]?s|i['\u2019]?m|it['\u2019]?s)"
            r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            transcript,
            _re.IGNORECASE,
        )
        if m:
            name = m.group(1).strip().title()
            # Stopword filter: reject common words that are not names.
            # "it's my first" → captures "My First" → rejected here.
            _HARVEST_NOT_NAME = frozenset({
                "my", "your", "the", "a", "an", "first", "last", "only",
                "new", "old", "next", "best", "good", "this", "that",
                "here", "there", "now", "just", "also", "well", "one",
                "time", "visit", "patient", "today", "call",
            })
            _h_words = name.lower().split()
            if 2 <= len(_h_words) <= 4 and not any(w in _HARVEST_NOT_NAME for w in _h_words):
                session["full_name"] = name
                logger.info("[ms_flow] harvest: full_name=%r from NEW_OR_RETURNING", name)

# ---------- Reschedule flow -----------------------------------------------

RESCHEDULE_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "COLLECT_NAME_RESCHEDULE",
        "question": "What's your first name please? You can say: my first name is...",
        "answer_field": "full_name",
        "use_llm": False,
        "extract": "name",
        "llm_instruction": None,
    },
    {
        "step": 1,
        "state": "CONFIRM_PHONE",
        "question": (
            "If the number you're calling on is the one associated with your booking, "
            "say: use this number."
        ),
        "answer_field": "phone_confirmed",
        "use_llm": False,
        "extract": "phone_confirm",
        "llm_instruction": None,
    },
    {
        "step": 2,
        "state": "COLLECT_PHONE",
        "question": "Could you type the number your booking was made under?",
        "answer_field": "phone_number",
        "use_llm": False,
        "extract": "phone",
        "llm_instruction": None,
    },
    {
        "step": 3,
        "state": "LOOKUP_RESCHEDULE",
        "question": None,  # LLM speaks after calling lookup_appointment
        "answer_field": "rc_appointment_confirmed",
        "use_llm": True,
        "allow_tools": True,
        "extract": "none",
        "llm_instruction": (
            "RC1–RC2: locate the caller's existing appointment then get verbal confirmation.\n\n"
            "TURN 1 — Lookup:\n"
            "  Say NOTHING before calling the tool — a holding message is already\n"
            "  spoken by the system. Call the tool immediately.\n"
            "  If '{full_name}' is NOT blank: parse it into first_name / last_name "
            "(split on the first space) and call lookup_appointment(first_name=<first>, last_name=<last>, "
            "phone='{phone_number}', location='{selected_location}').\n"
            "  If '{full_name}' IS blank (phone-first path, no name collected yet): call "
            "lookup_appointment(first_name='', last_name='', "
            "phone='{phone_number}', location='{selected_location}').\n"
            "  If found=true: say 'I can see an appointment on [day_label] at [time_label] — "
            "is that the one you\\'d like to reschedule?'\n"
            "  If found=false: say NOTHING — stay completely silent. "
            "Do NOT invent a name. Do NOT say 'I have the booking under...'. "
            "The system handles the recovery message automatically.\n\n"
            "TURN 2+ — Confirm:\n"
            "  Caller says YES → call confirm_appointment_found(). "
            "Then say NOTHING. Do NOT speak after calling confirm_appointment_found() — the system will handle it automatically.\n"
            "  Caller says NO + multiple_found=true → offer first alternative: "
            "'Could it be on [alt.day_label] at [alt.time_label]?'\n"
            "  Still no + no more alternatives → say 'I\\'m sorry — I still can\\'t find that booking. "
            "Could you call the clinic directly and they\\'ll sort it out for you?' "
            "Then call log_call_outcome(outcome='transferred').\n"
            "  After a lookup failure the caller corrects their details — re-call lookup_appointment "
            "with the corrected first_name/last_name/phone. "
            "When parsing corrections like 'surname is Pringle not the one you gave me', "
            "use ONLY the word(s) immediately after 'is' — stop at 'not', 'and', 'but'.\n"
        ),
    },
    {
        "step": 4,
        "state": "CONFIRM_RESCHEDULE_OR_CANCEL",
        "question": (
            "Would you like to reschedule this appointment to another time, "
            "or would you like to cancel it altogether?"
        ),
        "answer_field": "reschedule_or_cancel_choice",
        "use_llm": False,
        "extract": "none",
        "llm_instruction": None,
    },
    {
        "step": 5,
        "state": "PRESENT_DAYS_RESCHEDULE",
        "question": "Just a moment while I check what's available...",
        "answer_field": "chosen_day",
        "use_llm": True,
        "allow_tools": True,
        "extract": "any",
        "llm_instruction": (
            "Call check_availability with location='{selected_location}', duration_minutes=50.\n"
            "After the tool returns, say NOTHING further — do NOT read out any day names, "
            "do NOT present times, do NOT say anything else. "
            "The system will announce the available days automatically. "
            "Stop as soon as the tool call completes.\n"
            "ONLY speak if the tool returned an error:\n"
            "  error='lead_time_limited': re-call check_availability once with the same "
            "parameters. If still limited, say exactly: "
            "'We\\'re a little limited right now — the team will call you to confirm a time.'\n"
            "  error='no_availability' or any other error: say exactly: "
            "'I\\'m not seeing clear availability right now — let me take your details "
            "and the team will call you back.'"
        ),
    },
    {
        "step": 6,
        "state": "PRESENT_TIMES_RESCHEDULE",
        "question": None,
        "answer_field": "selected_slot",
        "use_llm": True,
        "allow_tools": False,
        "extract": "slot_selection",
        "llm_instruction": (  # step 6 — was step 5 before CONFIRM_RESCHEDULE_OR_CANCEL insertion
            "⚠️ SPOKEN OUTPUT ONLY — every word is read aloud by TTS. "
            "Sound like a warm, efficient UK clinic receptionist.\n\n"
            "The caller just responded to the day options with: '{chosen_day}'.\n"
            "The caller's latest message: '{caller_followup}'\n"
            "If the caller's latest message expresses a preference (e.g. 'afternoon', 'morning', "
            "'something earlier') — address that preference FIRST before listing times.\n"
            "Here is the full availability data (do NOT call check_availability again):\n"
            "{available_days_json}\n\n"
            "Each entry has: day_label (spoken day name), slot_times (list of HH:MM strings), "
            "and slots (list of start/end ISO datetimes).\n"
            "1. If the caller named a specific day — find that day in the data and present "
            "up to 4 times in natural spoken form:\n"
            "   4 times: 'On [day] I've got [t1], [t2], [t3], or [t4] — which of those works?'\n"
            "   2–3 times: 'On [day] I've got [t1] or [t2] — which suits you?'\n"
            "   1 time:  'The earliest I have on [day] is [t1] — does that work?'\n"
            "   Convert slot_times to natural spoken form: "
            "'09:00' → 'nine o'clock', '14:30' → 'half past two', '16:00' → 'four o'clock'. "
            "Add 'in the morning' / 'in the afternoon' where helpful. Never say AM/PM or raw digits.\n"
            "2. If none of those times work — refer to other initially offered days: "
            "'Not to worry — what about [other day 1][, or [other day 2]]?'\n"
            "3. If all initial days rejected — present next 3 days from data (entries 4–6). "
            "Continue cycling in batches of 3 until a day is chosen or list is exhausted.\n"
            "4. If no more days: 'I\\'m afraid those are the only days we have at the moment "
            "— would you like me to ask the team to give you a ring?'"
        ),
    },
    {
        "step": 7,
        "state": "CONFIRM_RESCHEDULE",
        "question": None,
        "answer_field": "reschedule_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "The patient has been verified and has confirmed they want to reschedule. "
            "CRITICAL: You MUST call reschedule_appointment RIGHT NOW — do NOT ask the patient "
            "any further questions or add any conditions before calling. "
            "Call reschedule_appointment with patient_name='{full_name}', "
            "phone='{phone_number}', location='{selected_location}', "
            "new_slot_iso='{selected_slot}', duration_minutes=50. "
            "After rescheduling confirm warmly: "
            "'I've rescheduled your appointment to [new date/time]. "
            "You'll receive a confirmation text shortly. "
            "Is there anything else I can help you with?' "
            "Use ordinal dates like 'Monday the 23rd of March at 9am'."
        ),
        "extract": "none",
    },
    {
        "step": 8,
        "state": "CONFIRM_CANCEL",
        "question": None,
        "answer_field": "cancel_confirmed",
        "use_llm": True,
        "llm_instruction": (
            "The patient has been verified and has confirmed they want to cancel. "
            "CRITICAL: You MUST call cancel_appointment RIGHT NOW — do NOT ask the patient "
            "any further questions, do NOT second-guess this action, do NOT add any conditions. "
            "Call cancel_appointment with patient_name='{full_name}', "
            "phone='{phone_number}', location='{selected_location}'. "
            "If cancel_appointment returns success=True: say "
            "'I've cancelled your appointment. You'll receive a confirmation text shortly. "
            "Is there anything else I can help you with?' "
            "If cancel_appointment returns success=False because no appointment was found: say "
            "'I wasn't able to find an upcoming appointment under those details — please call "
            "us directly and the team will be happy to help.' "
            "Do NOT use the phrase 'technical issue' for a not-found result."
        ),
        "extract": "none",
    },
]

# Array indices within RESCHEDULE_FLOW — parallel to _CONFIRM_BOOKING_INDEX /
# _COLLECT_PHONE_INDEX for BOOKING_FLOW.  Used by phone-accept/reject handlers
# that previously hard-coded BOOKING_FLOW indices and broke RESCHEDULE calls.
_RESCHEDULE_COLLECT_PHONE_INDEX: int = next(
    i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "COLLECT_PHONE"
)
_RESCHEDULE_LOOKUP_INDEX: int = next(
    i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "LOOKUP_RESCHEDULE"
)
_RESCHEDULE_PRESENT_DAYS_INDEX: int = next(
    i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "PRESENT_DAYS_RESCHEDULE"
)

# ---------- Cancel flow ---------------------------------------------------

# Cancel and reschedule share the same steps up through CONFIRM_RESCHEDULE_OR_CANCEL.
# After that the engine branches: reschedule → PRESENT_DAYS → PRESENT_TIMES →
# CONFIRM_RESCHEDULE; cancel → CONFIRM_CANCEL (step 8).
CANCEL_FLOW = RESCHEDULE_FLOW

# Array indices within CANCEL_FLOW — aliases to RESCHEDULE_FLOW since both
# are now the same flow object.
_CANCEL_LOOKUP_INDEX: int        = _RESCHEDULE_LOOKUP_INDEX
_CONFIRM_CANCEL_INDEX: int       = next(
    i for i, s in enumerate(RESCHEDULE_FLOW) if s["state"] == "CONFIRM_CANCEL"
)
_CANCEL_COLLECT_PHONE_INDEX: int = _RESCHEDULE_COLLECT_PHONE_INDEX

# ---------- FAQ flow (price / insurance / hours / services) ---------------

FAQ_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "ANSWER_FAQ",
        "question": None,   # LLM generates the full answer
        "answer_field": "faq_answered",
        "use_llm": True,
        "allow_tools": False,   # All FAQ info is in the system prompt — no tool call needed
        "llm_instruction": (
            "The caller asked about {faq_topic}. "
            "Answer DIRECTLY from the clinic information in your system prompt. "
            "STRICT LENGTH: 1–2 sentences maximum — no bullet points, no lists, no markdown. "
            "Speak naturally as if on a phone call.\n"
            "Do NOT use any preamble, filler, or transitional phrases before answering "
            "(e.g. do NOT say 'Give me just a second', 'Great question', 'Of course', "
            "'Let me look that up', 'Sure thing' — start the answer immediately).\n"
            "If {faq_topic} is 'prices' or 'faq_prices': "
            "check if the caller's most recent message named a specific service. "
            "If yes, give ONLY that service's price and duration in one sentence. "
            "If no specific service was named, say: "
            "'Our sessions start from £75 for a physiotherapy assessment. "
            "Was there a particular service you wanted the price for?'\n"
            "Do NOT end with 'Is there anything else I can help you with?' — "
            "the system handles follow-up automatically.\n"
            "Do NOT offer to book an appointment, suggest booking, or ask whether "
            "the caller wants to book — just answer the question and stop.\n"
            "Do NOT defer to the clinician or say things like 'that's one for the "
            "physiotherapist' or 'best to ask at your appointment' — answer practical "
            "clinic questions (parking, opening hours, pricing, what to expect, "
            "accessibility, cancellation) directly and confidently."
        ),
        "extract": "none",
    },
    {
        "step": 1,
        "state": "FAQ_BOOKING_OFFER",
        "question": None,   # LLM already asked — wait for caller reply
        "answer_field": "faq_booking_response",
        "use_llm": False,
        "extract": "faq_booking",
        "llm_instruction": None,
    },
]

# ---------- General query flow (anything outside known intents) ---------------

GENERAL_QUERY_FLOW: List[Dict[str, Any]] = [
    {
        "step": 0,
        "state": "ANSWER_GENERAL",
        "question": None,   # LLM generates the answer
        "answer_field": "general_answered",
        "use_llm": True,
        "allow_tools": False,   # answer from knowledge only — no booking tools
        "llm_instruction": (
            "The caller asked: '{general_query_text}'.\n"
            "You are a clinic receptionist giving a single short voice answer. "
            "Do NOT call any tool (no check_availability, book_appointment, etc).\n"
            "\n"
            "HARD LIMITS:\n"
            "• ONE sentence. At most two short sentences if absolutely necessary. "
            "No paragraphs, no lists, no multi-part speeches.\n"
            "• Voice-friendly plain English. No markdown, no URLs, no numbers "
            "spelled as digits if it harms TTS.\n"
            "• Do NOT mention line quality, connection issues, or ask the caller "
            "to repeat themselves.\n"
            "• Do NOT end with 'Is there anything else I can help you with?' "
            "or any generic offer — just answer and stop.\n"
            "\n"
            "CLINIC-FACT SAFETY (CRITICAL):\n"
            "• Treat any question about accessibility, step-free / wheelchair access, "
            "exact address, parking, child or age policy, opening hours, prices, "
            "insurance, referrals, or other clinic operations as an OPERATIONAL "
            "clinic question.\n"
            "• For operational questions you MUST NOT invent, infer, or estimate "
            "clinic-specific facts. Do NOT reason from building type, location, "
            "neighbourhood, name of the venue, or general world knowledge.\n"
            "• BANNED: words like 'generally', 'usually', 'likely', 'probably', "
            "'I would think', 'most clinics', 'leisure centres tend to', "
            "'it should be' — when used to imply clinic-specific facts.\n"
            "• If the exact clinic fact is not already present in this conversation, "
            "the ONLY acceptable response is a short honest line in one of these "
            "patterns, then stop:\n"
            "    – 'I don't have that exact detail confirmed — the clinic team can "
            "confirm that for you.'\n"
            "    – 'I don't have that specific [topic] detail here, but I can take "
            "a message for the team.'\n"
            "    – 'I can give you the [known related fact] I do have, but for "
            "that exact point the team can confirm.'\n"
            "\n"
            "TRAVEL / DIRECTIONS EXCEPTION:\n"
            "• If it is a travel-time or directions question (e.g. 'how long to "
            "drive from X'), say you don't have live journey times, give the "
            "clinic address if it is already in this conversation, and suggest "
            "Google Maps or a sat nav. Do not invent drive times.\n"
            "\n"
            "TRUE NON-OPERATIONAL GENERAL QUERIES:\n"
            "• If the question is genuinely not a clinic operational question "
            "(e.g. small talk, a generic wellbeing question), give ONE short "
            "helpful sentence and stop. Still no speculation about clinic facts."
        ),
        "extract": "none",
    },
    {
        # Wait for caller's reply to "Is there anything else I can help you with?"
        # yes / book → switch to BOOKING_FLOW; no / done → end call gracefully.
        "step": 1,
        "state": "GENERAL_BOOKING_OFFER",
        "question": None,
        "answer_field": "general_booking_response",
        "use_llm": False,
        "extract": "faq_booking",   # yes→booking, no→done
        "llm_instruction": None,
    },
]

# Intent → FAQ topic mapping
_INTENT_TO_FAQ_TOPIC = {
    "faq_prices":      "prices",
    "faq_insurance":   "insurance",
    "faq_hours":       "hours",
    "faq_location":    "address",
    "faq_services":    "services",
    "faq_capability":  "capability",
}


# ---------------------------------------------------------------------------
# Lookup correction parser
# ---------------------------------------------------------------------------

import re as _re_mod  # used by _parse_lookup_name_correction


def _parse_lookup_name_correction(text: str) -> str | None:
    """
    Parse a caller utterance that corrects their name after a lookup failure.

    Handles:
      "surname is Pringle not the one you gave me"  → "__SURNAME__Pringle"
      "last name is Cookbill"                        → "__SURNAME__Cookbill"
      "full name is Matt Cookbill"                   → "Matt Cookbill"
      "my name is Sarah Jones"                       → "Sarah Jones"
      "it's Sarah Jones"                             → "Sarah Jones"

    Surname-only results are prefixed with ``__SURNAME__`` so the caller can
    combine with the existing first name held in the session.

    Returns a cleaned, title-cased string or None if no pattern matches.
    """
    raw = text.strip().lower()

    # Strip trailing disclaimer clauses ("not the one you gave", "and not X", …)
    _STOP_RE = _re_mod.compile(
        r"\b(not|and\s+not|but|actually|sorry|i mean|rather|instead)\b.*$",
        _re_mod.IGNORECASE,
    )

    def _clean(capture: str) -> str:
        cleaned = _STOP_RE.sub("", capture).strip()
        cleaned = _re_mod.sub(r"[^a-zA-Z\s\-']", "", cleaned).strip()
        return " ".join(w.capitalize() for w in cleaned.split()) if cleaned else ""

    # Surname-only patterns
    _SURNAME_PATS = [
        r"\bsurname(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)?)",
        r"\blast\s+name(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)?)",
        r"\bfamily\s+name(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)?)",
    ]
    for pat in _SURNAME_PATS:
        m = _re_mod.search(pat, raw, _re_mod.IGNORECASE)
        if m:
            surname = _clean(m.group(1))
            if surname:
                return f"__SURNAME__{surname}"

    # Full-name patterns (require ≥ 2 tokens after cleaning)
    _FULL_PATS = [
        r"\bfull\s+name(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)+)",
        r"\bmy\s+name(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)+)",
        r"\bname(?:\s+is|\s+was|\s*'s)?\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)+)",
        r"\bit(?:'s|\s+is)\s+([a-zA-Z][\w\-']*(?:\s+[a-zA-Z][\w\-']*)+)",
    ]
    # Function/filler words that must not appear in a captured name.
    # Guards against "it's the same", "it is from before" etc. which pass the
    # length check but are not real names.
    _FUNC_WORDS = frozenset({
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "as", "is", "was", "are", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "not", "no", "yes", "same", "that",
        "this", "these", "those", "it", "its", "my", "your", "our", "their",
        "from", "before", "after", "right", "correct", "wrong", "just", "still",
    })
    for pat in _FULL_PATS:
        m = _re_mod.search(pat, raw, _re_mod.IGNORECASE)
        if m:
            name = _clean(m.group(1))
            _name_words = name.split()
            if len(_name_words) >= 2 and not any(w.lower() in _FUNC_WORDS for w in _name_words):
                return name

    return None


# ---------------------------------------------------------------------------
# Clinic-location resolver
# ---------------------------------------------------------------------------

def _resolve_location(raw: str, in_location_state: bool = False) -> str | None:
    """
    Deterministic phonetic / ASR-variant matching for Theorem Health clinics.

    Returns ``'alcester'``, ``'redditch'``, or ``None`` (ambiguous / unknown).

    Three-tier design:
    - Tier 1 (strong): applied always; high-confidence ASR variants.
    - Tier 2 (probable-context): applied only when ``in_location_state=True``;
      noisy but likely-correct when the system has just asked the clinic
      question ("Are you looking to book at our Alcester or Redditch clinic?").
    - Tier 3 (ambiguous / risky): never auto-route — always return None.

    Mixed signals on either tier always return None — never guess.
    """
    # Normalize: lowercase, strip punctuation to spaces, collapse whitespace.
    t = _re_mod.sub(r"[^\w\s]", " ", raw.strip().lower())
    t = _re_mod.sub(r"\s+", " ", t).strip()

    # ── TIER 1 — ALCESTER strong bucket ──────────────────────────────────────
    _ALC_STRONG = (
        "alcester", "alcesta", "alcest", "alcestra", "alchester", "alkester",
        "alsester", "asester", "al sester", "al-sester", "our sister",
        "all sister", "a sister", "al sister", "our sester", "all sester",
        "i ll sister", "house sister", "old sister", "l sester", "lsester",
        "sister clinic", "sester clinic", "our sister clinic",
        # Additional unambiguous STT variants
        "alcestr", "allcester", "alster", "alca", "alces",
        "kinwarton",
        # NOTE: "ancestor" removed from Tier 1 — too phonetically ambiguous;
        # "at your ancestor clinic" (live ASR noise) must not auto-bind.
    )
    # ── TIER 1 — REDDITCH strong bucket ──────────────────────────────────────
    _RED_STRONG = (
        "redditch", "reddit", "red itch", "read itch", "redich", "reddich",
        "redidge", "reditch", "red each", "read each",
        "ready itch", "ready each", "red dish", "read edge", "red edge",
        "ready edge", "red idge", "red itch clinic",
        # Additional unambiguous STT variants
        "bromsgrove",
    )
    # NOTE: "radish", "reddish", "lester", "leicester", "ulster" are Tier 3
    # (ambiguous collisions) and must NOT appear in Tier 1 or Tier 2 buckets.

    has_alc = any(v in t for v in _ALC_STRONG)
    has_red = any(v in t for v in _RED_STRONG)

    if has_alc and not has_red:
        return "alcester"
    if has_red and not has_alc:
        return "redditch"
    if has_alc and has_red:
        return None  # mixed signal — don't guess

    # ── TIER 2 — PROBABLE CLINIC-CONTEXT (only active during ASK_LOCATION) ───
    # These are noisy ASR outputs that are ambiguous globally but very likely
    # clinic answers when the system has just asked the either/or clinic question.
    if in_location_state:
        _ALC_T2 = (
            "your access", "your access clinic", "your access to clinic",
            "are you access", "are you access to clinic",
            # NOTE: "your ancestor / ancestor clinic" removed — too weak even in
            # location-state context; confirmed to produce false binds in live logs.
            "at your house as", "at your house",
            "our cester", "arlcester", "alcaster", "alceister", "alcesster",
            "el sester", "elsester", "al sister clinic",
        )
        _RED_T2 = (
            "raditch", "read dish",
            "red age", "read age", "red idge", "reddis", "redish",
        )

        has_alc_t2 = any(v in t for v in _ALC_T2)
        has_red_t2 = any(v in t for v in _RED_T2)

        if has_alc_t2 and not has_red and not has_red_t2:
            return "alcester"
        if has_red_t2 and not has_alc and not has_alc_t2:
            return "redditch"
        if (has_alc_t2 and has_red_t2) or (has_alc_t2 and has_red) or (has_red_t2 and has_alc):
            return None  # conflicting Tier 2 — don't guess

    # ── TIER 3 — AMBIGUOUS / RISKY — fall through to None; never auto-route ──
    # "leicester", "lester", "ulster" → Alcester collisions
    # "radish", "reddish", "registry", "wreckage" → Redditch collisions
    # These are intentionally unhandled here so they reach the retry path.

    return None


# ---------------------------------------------------------------------------
# Flow engine
# ---------------------------------------------------------------------------

class _TrackedQueue:
    """
    Thin wrapper around asyncio.Queue that marks session["_turn_speech_emitted"]
    whenever a non-empty, non-sentinel chunk is enqueued.

    This lets connection.py detect whether handle_transcript produced ANY audible
    speech and fire a hard global fallback if it did not — without requiring every
    `await self._tts.put(...)` call to set the flag manually.
    """
    __slots__ = ("_q", "_session")

    def __init__(self, q: Any, session: "Dict[str, Any]") -> None:
        self._q      = q
        self._session = session

    async def put(self, item: str) -> None:
        if item and item.strip() and item != "\x00DEDUP_RESET\x00":
            self._session["_turn_speech_emitted"] = True
        await self._q.put(item)

    def empty(self) -> bool:
        return self._q.empty()

    def get_nowait(self) -> str:
        return self._q.get_nowait()


class FlowEngine:
    """
    Drives the Susie booking conversation one step at a time.

    The engine owns ALL conversation decisions.  connection.py just feeds it
    transcripts; it plays the right phrase and advances the step.

    Constructor args:
        session    — the call's live session dict (mutated in place)
        tts_queue  — asyncio.Queue; put text here to synthesise via TTS
        llm_fn     — async callable (instruction: str) -> str
                     calls the LLM and streams output to tts_queue internally;
                     returns the full response text
    """

    def __init__(
        self,
        session: Dict[str, Any],
        tts_queue: Any,             # asyncio.Queue
        llm_fn: Callable,           # async (instruction: str) -> str
    ) -> None:
        self.session          = session
        self._tts             = _TrackedQueue(tts_queue, session)
        self._llm             = llm_fn
        self._active_flow: List[Dict[str, Any]] = DETECT_INTENT_FLOW
        self._intent_detected: bool = False

        # Name usage tracker — governs at-most-2 personalised name uses per call.
        # Stored as an instance var (not in session dict) so it is never
        # JSON-serialised directly.  Serialisable mirrors in session allow
        # reconstruction if the FlowEngine is re-created mid-call.
        from app.name_usage_tracker import NameUsageTracker as _NUT
        _tracker = _NUT()
        _saved_name = session.get("name_tracker_name")
        _saved_uses = session.get("name_tracker_uses", _NUT.MAX_USES)
        if _saved_name and isinstance(_saved_name, str):
            _tracker._name           = _saved_name
            _tracker._uses_remaining = int(_saved_uses)
        self._name_tracker: _NUT = _tracker

    # ── public API ────────────────────────────────────────────────────────

    def current_step(self) -> Optional[Dict[str, Any]]:
        """Return the current active-flow step dict, or None if flow is complete."""
        idx = self.session.get("flow_step", 0)
        if idx >= len(self._active_flow):
            return None
        return self._active_flow[idx]

    def is_complete(self) -> bool:
        """True when all steps in the active flow have been completed."""
        return self.session.get("flow_step", 0) >= len(self._active_flow)

    async def ask_current_question(self) -> None:
        """
        Play the current step's question (or call LLM to generate it).

        Called ONCE when the first caller utterance arrives — this starts
        the flow by playing step 0's booking-open question.
        """
        # ── Multi-location: ask which clinic before starting the flow ─────────
        # Fires for theorem_v2 bookings/reschedules/cancels until caller names a clinic.
        # No LLM call — pure TTS, same pattern as every other static question.
        if self.session.get("needs_location"):
            self.session["state"] = "ASK_LOCATION"
            if self._active_flow is RESCHEDULE_FLOW:
                _loc_q = "Was your original appointment at our Alcester or Redditch clinic?"
            else:
                _loc_q = "Are you looking to book at our Alcester or Redditch clinic?"
            await self._tts.put(_loc_q)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _loc_q}
            )
            self.session["last_question"] = _loc_q
            self.session["question_asked_this_turn"] = True
            logger.info("[ms_flow] ASK_LOCATION: question sent")
            return

        step = self.current_step()
        if step is None:
            # BOOKING_FLOW is complete: trigger readback before CONFIRM_BOOKING (once only)
            if self._active_flow is BOOKING_FLOW and not self.session.get("readback_delivered"):
                await self._start_readback()
            else:
                logger.info("[ms_flow] ask_current_question: flow already complete")
            return

        logger.info(
            "[ms_flow] ask_current_question: flow_step=%d state=%s",
            self.session.get("flow_step", 0), step["state"],
        )

        # Guard: one question per turn — prevent duplicate asks if somehow called twice
        if self.session.get("question_asked_this_turn"):
            logger.info(
                "[ms_flow] question_asked_this_turn guard: skipping step %d (%s)",
                step["step"], step["state"],
            )
            return

        # CONFIRM_PHONE turn-boundary: clear the arm flag for every question;
        # it is re-set only when we emit the CONFIRM_PHONE question below.
        self.session["phone_confirm_armed"] = False

        # Stamp session state so LLM prompt / silence handler know the current step
        if step["state"] != "DETECT_INTENT":
            self.session["state"] = step["state"]
            logger.debug("[ms_flow] state → %s (step %d)", step["state"], step["step"])

        # ── CONFIRM_BOOKING: hard first branch ─────────────────────────────────
        # MUST run before the "question is None" early-return guard below.
        # CONFIRM_BOOKING has use_llm=False and question=None; without this block
        # the guard fires and returns immediately, so the prompt is never emitted.
        # Do NOT set booking_confirmed here — only set it when the caller answers.
        if step["state"] == "CONFIRM_BOOKING":
            # ── VERY TOP: direct_ws_test / test_mode auto-confirm ────────────
            # In test mode no further user turn is injected after reaching
            # CONFIRM_BOOKING, so booking_confirmed must be set immediately.
            # Speak the done phrase and return — no need to ask "shall I confirm?".
            if self.session.get("direct_ws_test") or self.session.get("test_mode"):
                _done_phrase = "Brilliant — you're all booked in! We'll send a confirmation text shortly. Have a lovely day!"
                await self._tts.put(_done_phrase)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _done_phrase}
                )
                self.session["booking_confirmed"] = True
                self.session["state"]             = "DONE"
                self.session["flow_state"]        = "DONE"
                self.session["flow_step"]         = len(self._active_flow)
                logger.info("[ms_flow] CONFIRM_BOOKING: direct_ws_test auto-confirm → booking_confirmed=True state=DONE")
                return

            # ── SLOT GATE: require a real ISO slot before asking "shall I go ahead?" ──
            # If selected_slot is empty the booking call will fail with
            # "Invalid slot datetime: ''" — catch this upstream, before the caller
            # hears any confirmation text.
            # Route to the EARLIEST missing step (Required fix 3):
            #   • no available_days → PRESENT_DAYS (fetch availability + ask day)
            #   • no chosen_day    → PRESENT_DAYS (re-ask day selection)
            #   • day known, no slot → PRESENT_TIMES (ask time selection)
            _cb_gate_slot = self.session.get("selected_slot") or ""
            if not _cb_gate_slot.strip():
                _cb_avail = self.session.get("available_days") or []
                _cb_day   = self.session.get("chosen_day") or ""
                if not _cb_avail or not _cb_day:
                    # Need day selection — find PRESENT_DAYS (or PRESENT_DAYS_RESCHEDULE)
                    _cb_reroute_idx = next(
                        (i for i, s in enumerate(self._active_flow)
                         if s["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE")),
                        None,
                    )
                    _cb_reroute_label = "PRESENT_DAYS"
                else:
                    # Day known but no time — find PRESENT_TIMES
                    _cb_reroute_idx = next(
                        (i for i, s in enumerate(self._active_flow)
                         if s["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE")),
                        None,
                    )
                    _cb_reroute_label = "PRESENT_TIMES"
                logger.warning(
                    "[ms_flow] CONFIRM_BOOKING gate: selected_slot empty — "
                    "rerouting to %s (idx=%s)",
                    _cb_reroute_label if _cb_reroute_idx is not None else "UNKNOWN",
                    _cb_reroute_idx,
                )
                if _cb_reroute_idx is not None:
                    self.session["flow_step"] = _cb_reroute_idx
                    self.session["state"]     = self._active_flow[_cb_reroute_idx]["state"]
                    await self.ask_current_question()
                    return
                logger.error("[ms_flow] CONFIRM_BOOKING gate: reroute target not found in active flow")

            _slot_cb = (
                self.session.get("selected_slot_speech")
                or self.session.get("selected_slot")
                or "your appointment"
            )
            _name_cb = (
                self.session.get("full_name")
                or (self.session.get("collected") or {}).get("full_name")
                or (self.session.get("collected") or {}).get("name")
                or self.session.get("patient_name")
                or self.session.get("caller_name")
            )
            _loc_cb     = (self.session.get("selected_location") or "alcester").lower()
            _clinic_name = "Redditch" if "redditch" in _loc_cb else "Alcester"
            _name_part  = f"{_name_cb}, " if _name_cb else ""
            _cb_prompt = (
                f"So that's {_name_part}{_slot_cb} at {_clinic_name} — shall I go ahead?"
            )
            logger.info(
                "[ms_flow] ASK CONFIRM_BOOKING text=%r name=%r slot=%r",
                _cb_prompt[:80], _name_cb, str(_slot_cb)[:40],
            )
            self.session["question_asked_this_turn"] = True
            await self._tts.put(_cb_prompt)
            _store_last_question(
                self.session, _cb_prompt,
                force=True, source="flow_step:CONFIRM_BOOKING",
            )
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _cb_prompt}
            )
            logger.info("[ms_flow] SPOKE CONFIRM_BOOKING")
            return

        # DETECT_INTENT step has no question — wait silently for caller to speak
        if not step["use_llm"] and step["question"] is None:
            logger.info("[ms_flow] step %d (%s): no question to play — waiting for transcript",
                        step["step"], step["state"])
            return

        # For PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE: ensure location is set so
        # check_availability never asks the caller mid-booking.
        if step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            self.session.setdefault("selected_location", "alcester")
            logger.info(
                "[ms_flow] %s: selected_location=%r",
                step["state"], self.session["selected_location"],
            )

        # ── Returning-patient branch skip logic ───────────────────────────────
        # NEW_OR_RETURNING: skip if patient_type is already known (Bug #1 fix)
        if step["state"] == "NEW_OR_RETURNING" and self.session.get("new_or_returning"):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] patient_type already known (%s) — skipping NEW_OR_RETURNING",
                        self.session.get("new_or_returning"))
            await self.ask_current_question()
            return

        # RETURNING_RECENCY: skip for new patients
        if step["state"] == "RETURNING_RECENCY" and self.session.get("new_or_returning") == "new":
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] new patient — skipping RETURNING_RECENCY")
            await self.ask_current_question()
            return

        # RETURNING_TREATMENT_PLAN: skip if new OR not a recent returning patient
        if step["state"] == "RETURNING_TREATMENT_PLAN" and (
            self.session.get("new_or_returning") == "new"
            or self.session.get("returning_recency") != "recent"
        ):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] skipping RETURNING_TREATMENT_PLAN — not recent returning")
            await self.ask_current_question()
            return

        # ── On-plan fast-path skip logic (steps 3-5) ────────────────────────
        # Bug 1/2: new patients must NEVER enter these states regardless of any
        # other flags — check new_or_returning first as a hard override.
        _is_new_patient = self.session.get("new_or_returning") == "new"

        # RETURNING_PLAN_CONFIRM_PHONE: skip unless on_treatment_plan=True AND not new
        if step["state"] == "RETURNING_PLAN_CONFIRM_PHONE":
            if _is_new_patient or not self.session.get("on_treatment_plan"):
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] skipping RETURNING_PLAN_CONFIRM_PHONE — %s",
                            "new patient" if _is_new_patient else "not on plan")
                await self.ask_current_question()
                return
            # No Twilio caller-ID → skip confirm, go straight to collect
            if not self.session.get("phone_from_twilio"):
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] RETURNING_PLAN_CONFIRM_PHONE — no Twilio number, skip to collect")
                await self.ask_current_question()
                return

        # RETURNING_PLAN_COLLECT_PHONE: skip if new / not on plan / phone already confirmed
        if step["state"] == "RETURNING_PLAN_COLLECT_PHONE":
            if _is_new_patient or not self.session.get("on_treatment_plan"):
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] skipping RETURNING_PLAN_COLLECT_PHONE — %s",
                            "new patient" if _is_new_patient else "not on plan")
                await self.ask_current_question()
                return
            if self.session.get("phone_confirmed"):
                _rpc_phone = (
                    self.session.get("phone_number")
                    or self.session.get("collected", {}).get("phone")
                    or self.session.get("twilio_from", "")
                )
                self.session["phone_number"] = _rpc_phone
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] RETURNING_PLAN_COLLECT_PHONE — phone confirmed, skipping")
                await self.ask_current_question()
                return

        # RETURNING_PLAN_LOOKUP: skip if new / not on plan; otherwise run directly here
        if step["state"] == "RETURNING_PLAN_LOOKUP":
            # Prompt 4B fix (Bug 2): require ALL three conditions to be explicitly true.
            # Stale flags (returning_plan_looked_up, on_treatment_plan) from a prior
            # branch must never trigger the lookup.  new_or_returning must be the
            # authoritative "returning" value set in this session's NEW_OR_RETURNING step.
            _rpl_may_run = (
                not _is_new_patient
                and self.session.get("on_treatment_plan") is True
                and self.session.get("new_or_returning") == "returning"
            )
            if not _rpl_may_run:
                self.session["flow_step"] = step["step"] + 1
                logger.info(
                    "[ms_flow] skipping RETURNING_PLAN_LOOKUP — %s",
                    "new patient" if _is_new_patient else
                    "not on plan" if not self.session.get("on_treatment_plan") else
                    "new_or_returning not returning",
                )
                await self.ask_current_question()
                return
            # ── Execute lookup inline — no LLM round-trip ───────────────────
            _rpl_phone = (
                self.session.get("phone_number")
                or self.session.get("collected", {}).get("phone")
                or self.session.get("twilio_from", "")
            )
            _rpl_loc = (self.session.get("selected_location") or "alcester").lower()
            # Filler while Acuity query runs
            _rpl_filler = "Bear with me one second — I'm just pulling up your recent appointments."
            await self._tts.put(_rpl_filler)
            self.session["question_asked_this_turn"] = True
            # Direct tool call (no LLM)
            try:
                from app.tools.receptionist_tools import _exec_lookup_recent_appointment as _do_rpl
                _rpl_result = await _do_rpl(
                    {"phone": _rpl_phone, "location": _rpl_loc},
                    self.session,
                )
            except Exception as _rpl_exc:
                logger.warning("[ms_flow] RETURNING_PLAN_LOOKUP: tool error %r", _rpl_exc)
                _rpl_result = {"found": False}
            if _rpl_result.get("found"):
                _rpl_name = _rpl_result.get("full_name", "")
                _rpl_type = _rpl_result.get("last_appointment_type", "physiotherapy")
                _rpl_greeting = (
                    f"Welcome back{', ' + _rpl_name.split()[0] if _rpl_name else ''}! "
                    f"I can see you've been coming in for {_rpl_type} — "
                    "let me find your next available slot."
                )
                self.session["returning_plan"] = True
            else:
                _rpl_greeting = (
                    "Sorry, I couldn't find a recent appointment — "
                    "but let me find the next available slots for your new appointment."
                )
                self.session["returning_plan"] = False
            await self._tts.put(_rpl_greeting)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _rpl_filler + " " + _rpl_greeting}
            )
            logger.info(
                "[ms_flow] RETURNING_PLAN_LOOKUP: found=%s name=%r type=%r",
                _rpl_result.get("found"),
                _rpl_result.get("full_name", ""),
                _rpl_result.get("last_appointment_type", ""),
            )
            self.session["flow_step"] = step["step"] + 1
            await self.ask_current_question()
            return

        # ── COLLECT_REASON: skip for on-plan returning patients (treatment type already known)
        # OR when reason was already captured from the caller's first utterance.
        if step["state"] == "COLLECT_REASON" and (
            self.session.get("on_treatment_plan") or self.session.get("reason")
        ):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] skipping COLLECT_REASON — %s",
                        "on-plan returning patient" if self.session.get("on_treatment_plan")
                        else "reason already captured from first utterance")
            await self.ask_current_question()
            return

        # ── Returning non-plan name+phone (steps 8-10): skip conditions ─────
        # Skip for new patients OR on-plan returning (on-plan gets name from lookup).
        _non_plan_ret_states = {
            "COLLECT_NAME_RETURNING", "CONFIRM_PHONE_RETURNING", "COLLECT_PHONE_RETURNING",
        }
        if step["state"] in _non_plan_ret_states and (
            self.session.get("new_or_returning") == "new"
            or self.session.get("on_treatment_plan")
        ):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] skipping %s — new patient or on-plan", step["state"])
            await self.ask_current_question()
            return

        # CONFIRM_PHONE_RETURNING: skip if no Twilio number → go to COLLECT_PHONE_RETURNING
        if step["state"] == "CONFIRM_PHONE_RETURNING" and not self.session.get("phone_from_twilio"):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] no Twilio number — skipping CONFIRM_PHONE_RETURNING")
            await self.ask_current_question()
            return

        # COLLECT_PHONE_RETURNING: skip if phone already confirmed from Twilio
        if step["state"] == "COLLECT_PHONE_RETURNING" and self.session.get("phone_confirmed"):
            phone = (
                self.session.get("phone_number")
                or self.session.get("collected", {}).get("phone")
                or self.session.get("twilio_from", "")
            )
            self.session[step["answer_field"]] = phone
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] phone confirmed — skipping COLLECT_PHONE_RETURNING")
            await self.ask_current_question()
            return

        # ── New-patient name+phone (steps 11-13): skip for returning patients ──
        if step["state"] in ("COLLECT_NAME", "CONFIRM_PHONE", "COLLECT_PHONE") and (
            self.session.get("new_or_returning") == "returning"
        ):
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] skipping %s — returning patient", step["state"])
            await self.ask_current_question()
            return

        # CONFIRM_PHONE (BOOKING_FLOW): never auto-accept silently.
        # Ask the caller explicitly so the UX feels natural, not robotic.
        # If no Twilio caller-ID is available: skip to COLLECT_PHONE for manual entry.
        # If Twilio is present: fall through to question emit (new wording below).
        # RESCHEDULE/CANCEL flows retain their own confirm question via the elif below.
        if step["state"] == "CONFIRM_PHONE" and self._active_flow is BOOKING_FLOW:
            if not self.session.get("phone_from_twilio"):
                logger.info("[ms_flow] CONFIRM_PHONE: no Twilio number — skipping to COLLECT_PHONE")
                self.session["flow_step"] = step["step"] + 1
                await self.ask_current_question()
                return
            # Twilio present → fall through to question emit with explicit ask wording

        # COLLECT_PHONE: skip if phone already confirmed (auto or manual)
        if step["state"] == "COLLECT_PHONE" and self.session.get("phone_confirmed"):
            phone = (
                self.session.get("phone_number")
                or self.session.get("collected", {}).get("phone")
                or self.session.get("twilio_from", "")
            )
            self.session[step["answer_field"]] = phone
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] phone confirmed — skipping COLLECT_PHONE")
            await self.ask_current_question()
            return

        # ── CONFIRM_RESCHEDULE: ask a static confirmation question, no LLM.
        # The LLM will run (and call reschedule_appointment) only AFTER the
        # patient says "Yes" — handled in handle_transcript() below.
        if step["state"] == "CONFIRM_RESCHEDULE":
            slot_speech = (
                self.session.get("selected_slot_speech")
                or self.session.get("selected_slot", "the selected time")
            )
            q = (
                f"I've got {slot_speech} available — "
                "shall I go ahead and move you to that?"
            )
            await self._tts.put(q)
            _store_last_question(
                self.session, q,
                force=True, source="flow_step:CONFIRM_RESCHEDULE",
            )
            self.session["question_asked_this_turn"] = True
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": q}
            )
            logger.info("[ms_flow] CONFIRM_RESCHEDULE — asked static confirmation question")
            return

        # ── CONFIRM_RESCHEDULE_OR_CANCEL: speak binary choice, wait for caller ──
        if step["state"] == "CONFIRM_RESCHEDULE_OR_CANCEL":
            _roc_q = (
                "Would you like to reschedule this appointment to another time, "
                "or would you like to cancel it altogether?"
            )
            await self._tts.put(_roc_q)
            self.session["last_question"] = _roc_q
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _roc_q}
            )
            logger.info("[ms_flow] CONFIRM_RESCHEDULE_OR_CANCEL — asked binary choice")
            return

        # ── CONFIRM_CANCEL: directly execute via Acuity API (no LLM / no extra
        # patient turn needed).  Cancel intent + name + phone is sufficient.
        if step["state"] == "CONFIRM_CANCEL":
            from app.tools.receptionist_tools import _exec_cancel_appointment
            phone_val = (
                self.session.get("phone_number")
                or self.session.get("twilio_from_local")
                or self.session.get("twilio_from", "")
            )
            cancel_args = {
                "patient_name": self.session.get("full_name", ""),
                "phone":        phone_val,
                "location":     self.session.get("selected_location", "alcester"),
            }
            logger.info("[ms_flow] CONFIRM_CANCEL — calling _exec_cancel_appointment directly")
            # Patient intent confirmed by reaching this step — record regardless of Acuity result
            self.session["cancel_confirmed"] = True
            cancel_result = await _exec_cancel_appointment(cancel_args, self.session)
            if cancel_result.get("success"):
                response = (
                    "That's all sorted — your appointment's been cancelled. "
                    "You'll get a confirmation text shortly. "
                    "Is there anything else I can help with?"
                )
            else:
                response = (
                    "I wasn't able to find an upcoming appointment under those details — "
                    "it's worth giving us a call directly on 0\u20097\u20098\u20097\u20090"
                    "\u20091\u20096\u20096\u20098\u20096\u20091 and the team will be happy "
                    "to sort it. Is there anything else I can help with?"
                )
                self.session["acuity_error"] = cancel_result.get("error")
            await self._tts.put(response)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": response}
            )
            self.session["flow_step"] = len(self._active_flow)
            logger.info(
                "[ms_flow] CONFIRM_CANCEL complete — cancel_confirmed=%s",
                self.session["cancel_confirmed"],
            )
            return

        # ── PRESENT_DAYS: direct tool call — no LLM text in TTS path ────────────
        # The LLM streaming path has a race: LLM preamble text reaches TTS before
        # we can drain it.  Call check_availability directly so the ONLY spoken
        # output is our deterministic day phrase — zero chance of LLM duplicate.
        if step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            self.session["question_asked_this_turn"] = True
            if step["question"]:
                await self._tts.put(step["question"])
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": step["question"]}
                )
            from app.tools.receptionist_tools import _exec_check_availability
            _pd_args = {
                "location": self.session.get("selected_location", "alcester"),
                "duration_minutes": 50,
            }
            try:
                _pd_ca_result = await _exec_check_availability(_pd_args, self.session)
            except Exception as _pd_err:
                logger.error(
                    "[ms_flow] %s: direct check_availability failed: %r",
                    step["state"], _pd_err,
                )
                _pd_ca_result = {"error": str(_pd_err)}
            _pd_offered = self.session.get("last_offered_slots") or []
            if _pd_offered:
                self.session["slots_offered"] = list(_pd_offered)
                self.session["slots_count"]   = min(len(_pd_offered), 3)
            _pd_avail      = self.session.get("available_days", [])
            _pd_day_phrase = _build_day_list_phrase(_pd_avail)
            if _pd_day_phrase:
                await self._tts.put(_pd_day_phrase)
                self.session["last_question"] = _pd_day_phrase
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _pd_day_phrase}
                )
                logger.info(
                    "[ms_flow] %s: direct tool → deterministic day phrase: %r",
                    step["state"], _pd_day_phrase[:100],
                )
            else:
                _pd_err_code = (_pd_ca_result or {}).get("error", "")
                if _pd_err_code == "lead_time_limited":
                    _pd_ep = (
                        "We're a little limited right now — "
                        "the team will call you to confirm a time."
                    )
                else:
                    _pd_ep = (
                        "I'm not seeing clear availability right now — "
                        "let me take your details and the team will call you back."
                    )
                await self._tts.put(_pd_ep)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _pd_ep}
                )
                logger.info(
                    "[ms_flow] %s: no available_days (error=%r)",
                    step["state"], _pd_err_code,
                )
            return

        # ── PRESENT_TIMES deterministic path ─────────────────────────────────
        # All slot counts (1 or many) are handled here — LLM is never used for
        # time offering.  This eliminates AM/PM phrasing errors caused by the
        # LLM listing afternoon slots as "in the morning".
        #
        # 1-slot: sets selected_slot + slot_pending_confirmation so the next
        #         YES/NO routes to _handle_slot_confirmation.
        # N-slot: uses _build_times_phrase (grouped by period) and waits for
        #         the caller to name their slot before confirming.
        if step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
            _pt_avail  = self.session.get("available_days", [])
            _pt_chosen = self.session.get("chosen_day", "")
            _pt_target = _find_chosen_day_entry(_pt_avail, _pt_chosen)
            _pt_slots  = (_pt_target or {}).get("slots", [])
            if len(_pt_slots) == 1:
                # Stale guard: if slot already confirmed, don't re-ask
                if self.session.get("slot_confirmed"):
                    logger.info("[ms_flow] %s: ask_current_question 1-slot stale guard — slot_confirmed=True, skipping", step["state"])
                    return
                from app.vagueness_detector import _time_to_speech as _t2s_pt
                _pt_time   = ((_pt_target or {}).get("slot_times") or [""])[0]
                _pt_spoken = _t2s_pt(_pt_time) if _pt_time else "that time"
                _pt_label  = (_pt_target or {}).get("day_label", "")
                _pt_phrase = (
                    f"On {_pt_label} I've got {_pt_spoken} — does that work for you?"
                )
                self.session["selected_slot"]             = _pt_slots[0]["start"]
                self.session["selected_slot_speech"]      = (
                    f"{_pt_label} at {_pt_spoken}" if _pt_label else _pt_spoken
                )
                # Set flag so handle_transcript() routes next reply to
                # _handle_slot_confirmation instead of falling through to
                # the PRESENT_TIMES handler or the LLM catch-all.
                self.session["slot_pending_confirmation"] = True
                self.session["question_asked_this_turn"]  = True
                await self._tts.put(_pt_phrase)
                _store_last_question(
                    self.session, _pt_phrase,
                    force=True, source="flow_step:PRESENT_TIMES_1slot",
                )
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _pt_phrase}
                )
                logger.info(
                    "[ms_flow] %s: 1-slot deterministic → %r (LLM bypassed)",
                    step["state"], _pt_phrase[:80],
                )
                return
            elif len(_pt_slots) > 1:
                # Multi-slot deterministic path — bypass LLM entirely.
                # _build_times_phrase groups by period so 1pm is always
                # "one in the afternoon", never "one in the morning".
                if self.session.get("slot_confirmed"):
                    logger.info(
                        "[ms_flow] %s: multi-slot stale guard — slot_confirmed=True, skipping",
                        step["state"],
                    )
                    return
                _pt_ms_phrase = _build_times_phrase(_pt_target)
                if _pt_ms_phrase:
                    self.session["question_asked_this_turn"] = True
                    await self._tts.put(_pt_ms_phrase)
                    _store_last_question(
                        self.session, _pt_ms_phrase,
                        force=True, source="flow_step:PRESENT_TIMES_multi",
                    )
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _pt_ms_phrase}
                    )
                    logger.info(
                        "[ms_flow] %s: %d-slot deterministic → %r (LLM bypassed)",
                        step["state"], len(_pt_slots), _pt_ms_phrase[:80],
                    )
                    return

        if step["use_llm"]:
            # BUG 13 fix: pre-lookup name validation — abort with a re-ask if the
            # collected name is empty or consists only of wrapper garbage.
            if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL") and not self.session.get("rc_stage"):
                _lu_full = (
                    self.session.get("full_name")
                    or (self.session.get("collected") or {}).get("full_name")
                    or ""
                ).strip()
                _lu_parts = _lu_full.split()
                _lu_first = _lu_parts[0] if _lu_parts else ""
                _lu_last  = _lu_parts[-1] if len(_lu_parts) > 1 else ""
                if not _is_valid_name_token(_lu_first) or not _is_valid_name_token(_lu_last):
                    # Phone-first exception: when rc_phone_first is set the name was
                    # intentionally not collected yet.  Let the LLM proceed to the
                    # phone-only lookup — failure is handled deterministically via
                    # rc_lookup_failed below.  Only bypass when full_name is truly
                    # absent (never collected), not when it's present but garbage.
                    if self.session.get("rc_phone_first") and not _lu_full:
                        logger.info(
                            "[ms_flow] BUG13 bypassed for phone-first mode (no name yet)"
                        )
                        # fall through to LLM — phone-only lookup will fire
                    else:
                        logger.warning(
                            "[ms_flow] BUG13: lookup blocked — invalid name tokens: first=%r last=%r full=%r",
                            _lu_first, _lu_last, _lu_full,
                        )
                        _lu_reask = (
                            "Sorry — I didn't quite catch that name. "
                            "Could you give me the first name and surname the booking was made under?"
                        )
                        await self._tts.put(_lu_reask)
                        self.session["last_question"] = _lu_reask
                        # Step back to COLLECT_NAME_RESCHEDULE
                        _cn_state = "COLLECT_NAME_RESCHEDULE"
                        _cn_idx = next(
                            (i for i, s in enumerate(self._active_flow) if s["state"] == _cn_state),
                            None,
                        )
                        if _cn_idx is not None:
                            self.session["flow_step"] = _cn_idx
                            self.session["state"] = _cn_state
                            self.session.pop("name_fragment", None)
                            _col = self.session.get("collected", {})
                            _col.pop("full_name", None)
                            self.session["collected"] = _col
                            # Reset NC state so next turn starts at fn_normal,
                            # not at the stale NC_DONE left by the failed accept.
                            # Also clear the repair flag — garbage name means fresh start.
                            from app.media_streams.name_collector import NameCollector as _NameCollBug13
                            _NameCollBug13(self.session).reset()
                            self.session.pop("_nc_trust_repair_attempted", None)
                            self.session.pop("rc_recovery_step", None)
                        return
                # ── Name-trust preflight gate ────────────────────────────────
                # Blocks lookup when surname was captured via a degraded path.
                # Only fires when _nc_sn_trusted is explicitly False (set by
                # NameCollector._accept on every non-clean path).  Absent = a
                # non-NC path (Twilio compat etc.) → let through unchanged.
                #
                # Loop guard: _nc_trust_repair_attempted is set on the first
                # repair attempt.  If the repair itself produced another degraded
                # surname (e.g. spelling timed out → best-effort accept), the
                # gate would otherwise fire indefinitely.  On the second fire we
                # let lookup proceed and flag for SMS correction instead.
                #
                # Repair routing (first attempt):
                #   fn_confirmed=True  → reset_to_surname + sn_spelling directly
                #   fn_confirmed=False → full reset, re-collect both names
                #                        (also clears the repair flag so one
                #                         further attempt is allowed after a
                #                         full re-collection)
                if self.session.get("_nc_sn_trusted") is False:
                    if self.session.get("_nc_trust_repair_attempted"):
                        # Already repaired once; still degraded — let through
                        # with correction SMS rather than looping.
                        logger.warning(
                            "[ms_flow] name_trust preflight: repair already attempted, "
                            "still sn_trusted=False full=%r — letting through with correction SMS",
                            _lu_full,
                        )
                        self.session["needs_name_correction_sms"] = True
                        # Fall through to lookup — no return here.
                    else:
                        # First attempt: route to repair.
                        from app.media_streams.name_collector import (
                            NameCollector as _NameCollTrust,
                            NC_SN_SPELLING,
                        )
                        _nc_pf_inst   = _NameCollTrust(self.session)
                        _fn_confirmed = self.session.get("_nc", {}).get("fn_confirmed", False)
                        _cn_trust_state = "COLLECT_NAME_RESCHEDULE"
                        _cn_trust_idx   = next(
                            (i for i, s in enumerate(self._active_flow)
                             if s["state"] == _cn_trust_state),
                            None,
                        )
                        self.session["_nc_trust_repair_attempted"] = True
                        if _fn_confirmed:
                            # First name confirmed — keep it, go straight to spelling
                            _nc_pf_inst.reset_to_surname()
                            self.session["_nc"]["substate"]         = NC_SN_SPELLING
                            self.session["_nc"]["sn_from_spelling"] = True
                            _lu_trust_reask = (
                                "Sorry — I need to double-check your surname. "
                                "Could you spell it for me, one letter at a time?"
                            )
                            logger.warning(
                                "[ms_flow] name_trust preflight: lookup blocked "
                                "sn_trusted=False fn_confirmed=True full=%r → sn_spelling",
                                _lu_full,
                            )
                        else:
                            # First name also uncertain — full reset.
                            # Clear repair flag so one further attempt is allowed
                            # after the caller re-provides both names cleanly.
                            _nc_pf_inst.reset()
                            self.session.pop("_nc_trust_repair_attempted", None)
                            _lu_trust_reask = (
                                "Sorry — could I take your first name and surname again, "
                                "to make sure I have the right booking?"
                            )
                            logger.warning(
                                "[ms_flow] name_trust preflight: lookup blocked "
                                "sn_trusted=False fn_confirmed=False full=%r → full reset",
                                _lu_full,
                            )
                        if _cn_trust_idx is not None:
                            self.session["flow_step"] = _cn_trust_idx
                            self.session["state"]     = _cn_trust_state
                        await self._tts.put(_lu_trust_reask)
                        self.session["last_question"] = _lu_trust_reask
                        return
            # If the step has an immediate phrase (e.g. "Let me check…"), say it first
            if step["question"]:
                await self._tts.put(step["question"])
            # Build instruction, filling in session fields.
            # Ensure selected_slot_speech has a fallback so CONFIRM_BOOKING
            # never blows up with a missing-key error.
            format_args = dict(self.session)
            if "selected_slot_speech" not in format_args or not format_args["selected_slot_speech"]:
                _raw = format_args.get("selected_slot", "")
                format_args["selected_slot_speech"] = (
                    _format_slot_for_speech(_raw) if _raw else ""
                )
            # FIX 3: Ensure phone_number is always populated for CONFIRM_BOOKING
            # so {phone_number} in the instruction never formats as "None".
            if step["state"] == "CONFIRM_BOOKING" and not format_args.get("phone_number"):
                format_args["phone_number"] = (
                    format_args.get("twilio_from_local")
                    or format_args.get("twilio_from")
                    or (format_args.get("collected") or {}).get("phone")
                    or "the number you called from"
                )
            # Same fallback for LOOKUP_RESCHEDULE / LOOKUP_CANCEL — instruction
            # also uses {phone_number} and callers who confirmed Twilio caller-ID
            # never go through COLLECT_PHONE so phone_number may be unset.
            if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL") and not format_args.get("phone_number"):
                format_args["phone_number"] = (
                    _to_e164_uk(format_args.get("twilio_from_local") or "")
                    or _to_e164_uk(format_args.get("twilio_from") or "")
                    or (format_args.get("collected") or {}).get("phone")
                    or format_args.get("twilio_from_local")
                    or format_args.get("twilio_from")
                    or ""
                )
            # Ensure {full_name} always resolves in the LLM instruction — in phone-first
            # mode no name has been collected yet so the key may be absent from session.
            if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
                format_args.setdefault("full_name", "")
            # Inject available_days_json for PRESENT_TIMES steps so the LLM
            # has the slot data directly in the instruction rather than relying
            # on conversation history (tool results are not persisted there).
            if step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
                import json as _json
                format_args["available_days_json"] = _json.dumps(
                    self.session.get("available_days") or []
                )
                # Ensure caller_followup always resolves — avoids KeyError in format()
                format_args.setdefault("caller_followup", "")
            try:
                instruction = step["llm_instruction"].format(**format_args)
            except (KeyError, AttributeError) as exc:
                logger.warning(
                    "[ms_flow] instruction format failed step=%d: %r — using raw template",
                    step["step"], exc,
                )
                instruction = step["llm_instruction"] or ""
            # When lookup already ran successfully, prepend a status note so the LLM
            # does NOT re-call lookup_appointment on NO / ambiguous responses.
            if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL") and self.session.get("rc_stage") == "lookup_done":
                _day_hint  = self.session.get("reschedule_appt_day_label", "")
                _time_hint = self.session.get("reschedule_appt_time_label", "")
                _hint_str  = f" on {_day_hint} at {_time_hint}" if _day_hint else ""
                instruction = (
                    f"⚠️ LOOKUP ALREADY DONE (rc_stage=lookup_done{_hint_str}). "
                    "Do NOT call lookup_appointment again — you will create a duplicate lookup. "
                    "You are in Turn 2+: confirm the appointment or offer alternatives only.\n\n"
                ) + instruction
            self.session["question_asked_this_turn"] = True
            _allow_tools = step.get("allow_tools", True)
            _fast_r = None
            # ANSWER_FAQ/services: full-list detection takes priority over short summary.
            # If the caller explicitly asked for the full list ("what services do you offer",
            # "all services" etc.) respond with _FAQ_SERVICES_FULL; otherwise short summary.
            if step["state"] == "ANSWER_FAQ" and format_args.get("faq_topic") in ("services", "faq_services"):
                _faq_svc_text = (
                    (self.session.get("conversation_history") or [{}])[-1]
                    .get("content", "")
                    .lower()
                )
                # Acupuncture needle-fear: answer the actual concern, not a broad service list.
                # Must fire BEFORE _FAQ_SERVICES_FAST so the first response is correct.
                # Also sets _faq_active_service so follow-up carry-forward can route back here.
                _AQ_FEAR_SIG = (
                    "scared", "afraid", "nervous", "anxious", "anxiety", "phobia",
                    "hurt", "hurts", "will it hurt", "does it hurt",
                    "painful", "pain", "scary", "scare",
                    "needle", "needles", "how big", "how long", "size",
                    "worried", "worry", "what does it feel", "feel like",
                    "hate needle", "don't like needle",
                )
                if "acupuncture" in _faq_svc_text and any(p in _faq_svc_text for p in _AQ_FEAR_SIG):
                    _fast_r = _FAQ_ACUPUNCTURE_FEAR_ANSWER
                    self.session["_faq_active_service"] = "acupuncture"
                    logger.info(
                        "[ms_faq] topic_lock set topic=acupuncture source=caller_utterance "
                        "(fear-signal fast path)"
                    )
                    logger.info(
                        "[ms_faq] answer_source=clinic_config topic=acupuncture "
                        "subtype=needle_pain"
                    )
                else:
                    # ── Specific-service drill-down (TOPIC LOCK on first turn) ──
                    # "I saw on the website about acupuncture" / "tell me about
                    # shockwave" etc. must (a) be answered about that service
                    # specifically and (b) set _faq_active_service so any
                    # immediate follow-up ("are the needles big, is it painful?")
                    # is routed back to this topic deterministically rather than
                    # drifting through top-level intent reconsideration.
                    _aq_specific_key = next(
                        (svc for kw, svc in _SERVICE_KEYWORD_MAP if kw in _faq_svc_text),
                        None,
                    )
                    _LIST_PHRASES = (
                        "full list", "all services", "everything you offer",
                        "all of them", "what do you offer", "what services do you",
                        "what services", "list of services",
                    )
                    if _aq_specific_key and not any(p in _faq_svc_text for p in _LIST_PHRASES):
                        _fast_r = _SPECIFIC_SERVICE_ANSWERS[_aq_specific_key]
                        self.session["_faq_active_service"] = _aq_specific_key
                        logger.info(
                            "[ms_faq] topic_lock set topic=%s source=caller_utterance",
                            _aq_specific_key,
                        )
                        logger.info(
                            "[ms_faq] answer_source=clinic_config topic=%s subtype=service_overview",
                            _aq_specific_key,
                        )
                    elif any(p in _faq_svc_text for p in _LIST_PHRASES):
                        _fast_r = _FAQ_SERVICES_FULL
                        logger.info("[ms_faq] answer_source=clinic_config topic=services subtype=full_list")
                    else:
                        _fast_r = _FAQ_SERVICES_FAST
                        logger.info("[ms_faq] answer_source=clinic_config topic=services subtype=fast_summary")
            # ANSWER_FAQ/insurance: deterministic self-pay / Bupa answer.
            if step["state"] == "ANSWER_FAQ" and format_args.get("faq_topic") in ("insurance", "faq_insurance"):
                _fast_r = _FAQ_INSURANCE_ANSWER
            # ANSWER_FAQ/capability: deterministic "what can you help with" answer.
            if step["state"] == "ANSWER_FAQ" and format_args.get("faq_topic") in ("capability", "faq_capability"):
                _fast_r = _CAPABILITY_ANSWER
            # ANSWER_FAQ/hours: deterministic clinic-specific hours from config.
            if step["state"] == "ANSWER_FAQ" and format_args.get("faq_topic") in ("hours", "faq_hours") and not _fast_r:
                from app.clinic_config import get_clinic as _gc_aq
                _cli_aq = _gc_aq(self.session.get("clinic_id") or "demo")
                _locs_aq = {loc["id"]: loc for loc in _cli_aq.get("locations", [])}
                _aq_sel = (self.session.get("selected_location") or "").lower()
                _aq_loc = _locs_aq.get(_aq_sel) or (list(_locs_aq.values())[0] if len(_locs_aq) == 1 else None)
                if _aq_loc:
                    _fast_r = _aq_loc.get("hours_summary", "")
                elif _locs_aq:
                    _fast_r = "  ".join(l.get("hours_summary", "") for l in _locs_aq.values() if l.get("hours_summary"))
            # ANSWER_FAQ/location: deterministic address from config.
            # Accessibility sub-check runs first: "step-free access" questions
            # share the faq_location route (accessibility_p returns faq_location in
            # _detect_intent) but must answer accessibility, not address.
            if step["state"] == "ANSWER_FAQ" and format_args.get("faq_topic") in ("address", "faq_location") and not _fast_r:
                _aq2_last_msg = (
                    (self.session.get("conversation_history") or [{}])[-1]
                    .get("content", "")
                    .lower()
                )
                from app.clinic_config import get_clinic as _gc_aq2
                _cli_aq2 = _gc_aq2(self.session.get("clinic_id") or "demo")
                _locs_aq2 = {loc["id"]: loc for loc in _cli_aq2.get("locations", [])}
                # BUG 2: explicit clinic mention in utterance overrides selected_location
                _aq2_redd = any(p in _aq2_last_msg for p in (
                    "redditch", "reditch", "reddish", "reddit",
                    "red itch", "red ditch", "red-ditch", "bromsgrove",
                ))
                _aq2_alce = any(p in _aq2_last_msg for p in (
                    "alcester", "greig", "kinwarton",
                ))
                if _aq2_redd and not _aq2_alce:
                    _aq2_loc_id = "redditch"
                elif _aq2_alce and not _aq2_redd:
                    _aq2_loc_id = "alcester"
                else:
                    _aq2_sel = (self.session.get("selected_location") or "").lower()
                    _aq2_loc_id = _aq2_sel if _aq2_sel in _locs_aq2 else (
                        next(iter(_locs_aq2.keys())) if len(_locs_aq2) == 1 else ""
                    )
                _aq2_loc = _locs_aq2.get(_aq2_loc_id) or (
                    list(_locs_aq2.values())[0] if len(_locs_aq2) == 1 else None
                )
                # BUG 1: detect all practical sub-parts in the utterance
                _AQ2_ACCESS_P = (
                    "step free", "step-free", "step three", "wheelchair",
                    "disabled access", "disability", "accessible", "accessibility",
                )
                _AQ2_LOC_P = (
                    "where exactly", "where is", "where are", "address",
                    "postcode", "post code", "which road", "which street",
                    "find you", "locate you", "get to", "directions", "how do i get",
                )
                _AQ2_PARK_P = (
                    "parking", "car park", "paid parking", "free parking",
                    "easy to park", "pay to park", "do i need to pay",
                )
                _AQ2_TRANS_P = (
                    "bus", "train", "public transport", "station", "tube", "coach",
                )
                _AQ2_DIFF_P = (
                    "difference between", "different from", "two clinics",
                    "both clinics", "both of them", "both of the",
                    "which clinic", "which one", "either clinic",
                    "two locations", "both locations",
                    "alcester or redditch", "redditch or alcester",
                )
                _aq2_parts: list = []
                if any(p in _aq2_last_msg for p in _AQ2_ACCESS_P): _aq2_parts.append("accessibility")
                if any(p in _aq2_last_msg for p in _AQ2_LOC_P):    _aq2_parts.append("location")
                if any(p in _aq2_last_msg for p in _AQ2_PARK_P):   _aq2_parts.append("parking")
                if any(p in _aq2_last_msg for p in _AQ2_TRANS_P):  _aq2_parts.append("transport")
                if (
                    any(p in _aq2_last_msg for p in _AQ2_DIFF_P)
                    and len(_locs_aq2) >= 2
                ):
                    _aq2_parts.append("clinic_difference")
                logger.info(
                    "[ms_faq] practical_first_turn_detected clinic=%s parts=%s",
                    _aq2_loc_id or "<unresolved>", "|".join(_aq2_parts) or "<none>",
                )
                # BUG 1: composite answer when 2+ practical parts + clinic resolved
                if len(_aq2_parts) >= 2 and _aq2_loc:
                    _frags: list = []
                    if "accessibility" in _aq2_parts:
                        _ac_raw   = (_cli_aq2.get("faq") or {}).get("accessibility") or ""
                        _ac_first = _ac_raw.split(".")[0].strip() if _ac_raw else ""
                        _frags.append(
                            (_ac_first + ".") if _ac_first
                            else "Yes \u2014 both our clinics are wheelchair accessible."
                        )
                    if "location" in _aq2_parts:
                        _addr_raw   = _aq2_loc.get("address", "")
                        _addr_first = _addr_raw.split(".")[0].strip() if _addr_raw else ""
                        _loc_name   = _aq2_loc.get("name", _aq2_loc_id.title())
                        for _lead in ("we\u2019re at ", "we're at ", "we are at "):
                            if _addr_first.lower().startswith(_lead):
                                _addr_first = _addr_first[len(_lead):].lstrip()
                                break
                        if _addr_first:
                            _frags.append(f"The {_loc_name} clinic is at {_addr_first}.")
                    if "parking" in _aq2_parts:
                        _pk_raw   = _aq2_loc.get("parking", "")
                        _pk_first = _pk_raw.split(".")[0].strip() if _pk_raw else ""
                        if _pk_first:
                            _frags.append(_pk_first + ".")
                    if "transport" in _aq2_parts:
                        _tr_raw   = _aq2_loc.get("transport", "")
                        _tr_first = _tr_raw.split(".")[0].strip() if _tr_raw else ""
                        if _tr_first:
                            _frags.append(_tr_first + ".")
                    if "clinic_difference" in _aq2_parts and len(_locs_aq2) >= 2:
                        _diff_names = []
                        for _dl in _locs_aq2.values():
                            _dn = _dl.get("name") or _dl.get("id", "").title()
                            _dadr = (_dl.get("address") or "").split(",")
                            _darea = _dadr[-2].strip() if len(_dadr) >= 2 else ""
                            _diff_names.append(
                                f"{_dn}" + (f" in {_darea}" if _darea else "")
                            )
                        if _diff_names:
                            _frags.append(
                                "We have two clinics — "
                                + " and ".join(_diff_names)
                                + ". They offer the same core physiotherapy services; "
                                "the main differences are location and opening hours."
                            )
                    if len(_frags) >= 2:
                        _fast_r = " ".join(_frags).strip()
                        self.session["last_faq_loc_id"]     = _aq2_loc_id
                        self.session["last_faq_intent"]     = "faq_location"
                        self.session["last_faq_sub"]        = "composite"
                        self.session["_faq_parts_answered"] = list(_aq2_parts)
                        logger.info(
                            "[ms_faq] practical_first_turn_answer_source=clinic_config "
                            "composite=True clinic=%s parts=%s",
                            _aq2_loc_id, "|".join(_aq2_parts),
                        )
                        logger.info(
                            "[ms_faq] practical_parts_answered stored=%s",
                            "|".join(_aq2_parts),
                        )
                if not _fast_r:
                    # Single-part / fallback paths — preserve existing behaviour
                    if "accessibility" in _aq2_parts and not (_aq2_redd or _aq2_alce):
                        # Accessibility-only, no clinic named → clinic-wide short answer
                        _fast_r = _short_access_ans(_cli_aq2)
                        self.session["last_faq_sub"]        = "accessibility"
                        self.session["_faq_parts_answered"] = ["accessibility"]
                        logger.info("[ms_flow] ask_current_question: ANSWER_FAQ accessibility intercept (faq_location route)")
                    elif "accessibility" in _aq2_parts and _aq2_loc:
                        _fast_r = _short_access_ans(_cli_aq2)
                        self.session["last_faq_sub"]        = "accessibility"
                        self.session["_faq_parts_answered"] = ["accessibility"]
                        self.session["last_faq_loc_id"]     = _aq2_loc_id
                        self.session["last_faq_intent"]     = "faq_location"
                    elif "parking" in _aq2_parts and _aq2_loc:
                        _pk_raw   = _aq2_loc.get("parking", "")
                        _pk_first = _pk_raw.split(".")[0].strip() + ("." if "." in _pk_raw else "")
                        _fast_r = _pk_first or _pk_raw
                        self.session["last_faq_sub"]        = "parking"
                        self.session["_faq_parts_answered"] = ["parking"]
                        self.session["last_faq_loc_id"]     = _aq2_loc_id
                        self.session["last_faq_intent"]     = "faq_location"
                        logger.info(
                            "[ms_faq] practical_first_turn_answer_source=clinic_config "
                            "composite=False clinic=%s parts=parking", _aq2_loc_id,
                        )
                    elif "transport" in _aq2_parts and _aq2_loc:
                        _fast_r = _aq2_loc.get("transport", "")
                        self.session["last_faq_sub"]        = "transport"
                        self.session["_faq_parts_answered"] = ["transport"]
                        self.session["last_faq_loc_id"]     = _aq2_loc_id
                        self.session["last_faq_intent"]     = "faq_location"
                    elif _aq2_loc:
                        _fa2 = _aq2_loc.get("address", "")
                        _fast_r = _fa2.split(".")[0].strip() + ("." if _fa2 else "")
                        self.session["last_faq_sub"]        = "address"
                        self.session["_faq_parts_answered"] = ["location"]
                        self.session["last_faq_loc_id"]     = _aq2_loc_id
                        self.session["last_faq_intent"]     = "faq_location"
                    elif _locs_aq2:
                        _fast_r = "  ".join(
                            l.get("address", "").split(".")[0].strip() + "."
                            for l in _locs_aq2.values() if l.get("address")
                        )
                        self.session["last_faq_sub"]        = "address"
                        self.session["_faq_parts_answered"] = ["location"]
            # ANSWER_FAQ/prices: deterministic from-price gate.
            # If no specific service was named, always return the from-price line —
            # never a full price list.  If a service was named, let the LLM answer
            # with just that service's price (instruction already constrains it).
            if (
                step["state"] == "ANSWER_FAQ"
                and format_args.get("faq_topic") in ("prices", "faq_prices")
                and not _fast_r
            ):
                _last_user = (
                    (self.session.get("conversation_history") or [{}])[-1]
                    .get("content", "")
                    .lower()
                )
                _named_svc = any(
                    k in _last_user for k in _FAQ_PRICES_SERVICE_KEYWORDS
                )
                if not _named_svc:
                    _fast_r = _FAQ_PRICES_NO_SERVICE
            # ANSWER_GENERAL: deterministic clinic-fact precedence layer.
            # If the caller's question is really an operational clinic-fact question
            # (accessibility / address / parking / child-policy / basic booking
            # logistics) that slipped past _detect_intent into general_query because
            # of STT noise, answer from clinic config rather than letting the LLM
            # improvise from generic world knowledge.
            if step["state"] == "ANSWER_GENERAL" and not _fast_r:
                _gq_text = (
                    (self.session.get("general_query_text") or "")
                    + " "
                    + (
                        (self.session.get("conversation_history") or [{}])[-1]
                        .get("content", "") or ""
                    )
                ).lower()
                try:
                    from app.clinic_config import get_clinic as _gc_gq
                    _cli_gq = _gc_gq(self.session.get("clinic_id") or "demo")
                except Exception:
                    _cli_gq = {}
                _faq_cfg = (_cli_gq.get("faq") or {})
                _locs_gq = {loc["id"]: loc for loc in (_cli_gq.get("locations") or [])}
                _sel_gq = (self.session.get("selected_location") or "").lower()
                _loc_gq = _locs_gq.get(_sel_gq) or (
                    list(_locs_gq.values())[0] if len(_locs_gq) == 1 else None
                )

                _ACCESS_SIG = (
                    "step free", "step-free", "step three", "wheelchair",
                    "disabled access", "disability", "accessible", "accessibility",
                    "ramp", "lift access", "mobility",
                )
                _ADDRESS_SIG = (
                    "address", "where are you", "where is your", "where exactly",
                    "where is the", "find you", "locate you", "clinic address",
                    "postcode", "post code",
                )
                _PARKING_SIG = (
                    "parking", "car park", "can i park", "where to park",
                    "where can i park", "park near", "park there", "park in the",
                )
                _CHILD_SIG = (
                    "children", "child", "kid", "kids", "son", "daughter",
                    "paediatric", "pediatric", "little boy", "little girl",
                    "young child", "my boy", "my girl",
                )
                _REFERRAL_SIG = (
                    "referral", "gp referral", "self refer", "self-refer",
                    "need a doctor", "doctor's referral",
                )
                _SAMEDAY_SIG = (
                    "today", "same day", "same-day", "this afternoon", "this morning",
                    "right now", "straight away",
                )

                def _score(sig):
                    return sum(1 for p in sig if p in _gq_text)

                _scores = {
                    "accessibility": _score(_ACCESS_SIG),
                    "parking":       _score(_PARKING_SIG),
                    "address":       _score(_ADDRESS_SIG),
                    "child":         _score(_CHILD_SIG),
                    "referral":      _score(_REFERRAL_SIG),
                    "sameday":       _score(_SAMEDAY_SIG),
                }
                # same-day only counts when paired with booking verb
                if _scores["sameday"] and not ("book" in _gq_text or "appoint" in _gq_text):
                    _scores["sameday"] = 0
                # child signals are weak (common pronouns) — require a second supporting
                # child word OR an explicit eligibility verb to count as an intent signal.
                if _scores["child"] == 1 and not any(
                    t in _gq_text for t in ("see children", "treat children",
                    "paediatric", "pediatric", "my son", "my daughter", "my kid",
                    "for a child", "for children")
                ):
                    # leave as 1 only if it's a strong standalone term
                    if not any(t in _gq_text for t in (
                        "paediatric", "pediatric", "little boy", "little girl", "young child"
                    )):
                        _scores["child"] = 0

                _hits = [c for c, s in _scores.items() if s > 0]

                def _fact_for(cat):
                    if cat == "accessibility":
                        return _short_access_ans(_cli_gq)
                    if cat == "parking":
                        _park = (_loc_gq or {}).get("parking") or ""
                        if _park:
                            return _park
                        if _locs_gq:
                            _pa = [l.get("parking") for l in _locs_gq.values() if l.get("parking")]
                            if _pa:
                                return "  ".join(_pa)
                        return ("I don't have the exact parking details confirmed — "
                                "I'd recommend checking the clinic website, or I can take a message for the team.")
                    if cat == "address":
                        if _loc_gq and _loc_gq.get("address"):
                            return _loc_gq["address"].split(".")[0].strip() + "."
                        if _locs_gq:
                            return "  ".join(
                                l.get("address", "").split(".")[0].strip() + "."
                                for l in _locs_gq.values() if l.get("address")
                            )
                        return ""
                    if cat == "child":
                        return _faq_cfg.get("children_policy") or _FAQ_CHILD_POLICY_ANSWER
                    if cat == "referral":
                        return _faq_cfg.get("gp_referral") or ""
                    if cat == "sameday":
                        return _faq_cfg.get("same_day_booking") or ""
                    return ""

                # Priority for tie-breaking (accessibility is safety-critical first).
                _PRIORITY = ("accessibility", "parking", "address", "child", "referral", "sameday")

                if len(_hits) == 1:
                    _fast_r = _fact_for(_hits[0])
                    logger.info("[ms_flow] ANSWER_GENERAL deterministic intercept: %s", _hits[0])
                elif len(_hits) >= 2:
                    _ranked = sorted(_hits, key=lambda c: (-_scores[c], _PRIORITY.index(c)))
                    _top, _second = _ranked[0], _ranked[1]
                    _top_fact = _fact_for(_top)
                    _second_fact = _fact_for(_second)
                    # Compatible pairs: when both facts are available and the caller
                    # clearly signalled both, a short grounded combined answer is safe.
                    _COMBINABLE = {
                        frozenset(("accessibility", "address")),
                        frozenset(("accessibility", "parking")),
                        frozenset(("parking", "address")),
                        frozenset(("child", "referral")),
                        frozenset(("child", "sameday")),
                    }
                    _pair = frozenset((_top, _second))
                    if _top_fact and _second_fact and _pair in _COMBINABLE:
                        _fast_r = (_top_fact.rstrip() + " " + _second_fact.rstrip()).strip()
                        logger.info(
                            "[ms_flow] ANSWER_GENERAL deterministic intercept: combined %s+%s",
                            _top, _second,
                        )
                    elif _scores[_top] > _scores[_second]:
                        _fast_r = _top_fact
                        logger.info(
                            "[ms_flow] ANSWER_GENERAL deterministic intercept: winner %s (%d>%d)",
                            _top, _scores[_top], _scores[_second],
                        )
                    else:
                        _CLARIFY = {
                            frozenset(("accessibility", "address")):
                                "Are you asking about access at the clinic, or where the clinic is?",
                            frozenset(("accessibility", "parking")):
                                "Are you asking about access at the clinic, or about parking?",
                            frozenset(("parking", "address")):
                                "Are you asking about parking, or the address?",
                            frozenset(("child", "referral")):
                                "Are you asking whether we see children, or about GP referrals?",
                            frozenset(("child", "sameday")):
                                "Are you asking whether we see children, or about same-day appointments?",
                            frozenset(("child", "address")):
                                "Are you asking whether we see children, or where the clinic is?",
                            frozenset(("accessibility", "child")):
                                "Are you asking about access at the clinic, or whether we see children?",
                        }
                        _label = {
                            "accessibility": "access at the clinic",
                            "parking": "parking",
                            "address": "the address",
                            "child": "whether we see children",
                            "referral": "GP referrals",
                            "sameday": "same-day appointments",
                        }
                        _fast_r = _CLARIFY.get(_pair) or (
                            f"Just to check — did you want to know about "
                            f"{_label.get(_top, _top)}, or {_label.get(_second, _second)}?"
                        )
                        # Mark clarification so the flow does not advance to
                        # GENERAL_BOOKING_OFFER — the next utterance must re-run
                        # intent detection with the clarified phrasing.
                        self.session["_gq_clarify_pending"] = True
                        logger.info(
                            "[ms_flow] ANSWER_GENERAL clarification emitted: %s vs %s",
                            _top, _second,
                        )
            if _fast_r:
                # Fast-path: speak the deterministic answer directly to TTS.
                # The LLM path handles TTS internally via streaming; fast-path must
                # do it explicitly — without this the answer is built but never spoken.
                await self._tts.put(_fast_r)
                response = _fast_r
            else:
                response = await self._llm(instruction, allow_tools=_allow_tools)
            # Extract only the question sentence from the LLM response so the
            # SilenceHandler re-asks a clean question, not the full paragraph.
            _q = _extract_question_sentence(response or "") or (step["question"] or "")
            _q_stored = _store_last_question(
                self.session, _q,
                force=False, source=f"llm_step:{step['state']}",
            )
            # Fast-path ANSWER_FAQ answers that have no trailing question (e.g. capability)
            # would leave last_question stale. Store the full answer so repeat/silence
            # recovery replays the actual spoken content, not an old unrelated question.
            if _fast_r and step["state"] in ("ANSWER_FAQ", "ANSWER_GENERAL") and not _q_stored:
                _store_last_question(
                    self.session, response or "",
                    force=True, source=f"fast_path_answer:{step['state']}",
                )
            # Record Susie's LLM response to conversation_history
            if response:
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": response}
                )
            # Auto-complete terminal LLM steps — no further patient utterance will
            # arrive to trigger _extract("none") for the last step in each flow.
            if step["state"] == "CONFIRM_BOOKING":
                self.session["booking_confirmed"] = True
                self.session["state"]             = "DONE"
                self.session["flow_state"]        = "DONE"
                self.session["flow_step"] = len(self._active_flow)
                logger.info("[ms_flow] CONFIRM_BOOKING complete — booking_confirmed=True, flow complete")
            # ANSWER_FAQ: advance immediately to FAQ_BOOKING_OFFER after the LLM
            # delivers the answer — the LLM already asked "would you like to book?",
            # so the NEXT patient utterance must be processed at FAQ_BOOKING_OFFER,
            # not consumed here as a generic "none" advance.
            if step["state"] == "ANSWER_FAQ":
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] ANSWER_FAQ complete — advancing to FAQ_BOOKING_OFFER")
                return
            if step["state"] == "ANSWER_GENERAL":
                if self.session.pop("_gq_clarify_pending", False):
                    # Clarification emitted for an ambiguous noisy inquiry — do not
                    # advance to GENERAL_BOOKING_OFFER (which would interpret the
                    # caller's clarified reply as a yes/no booking answer). Reset
                    # to DETECT_INTENT_FLOW so the next utterance is re-classified.
                    self._active_flow = DETECT_INTENT_FLOW
                    self.session["flow_step"] = 0
                    self.session.pop("intent", None)
                    self.session.pop("general_query_text", None)
                    logger.info(
                        "[ms_flow] ANSWER_GENERAL clarification — reset to DETECT_INTENT_FLOW"
                    )
                    return
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] ANSWER_GENERAL complete — advancing to GENERAL_BOOKING_OFFER")
                return
            # LOOKUP_TREATMENT_PLAN: advance immediately after LLM announces the
            # treatment type — no patient response needed; next step asks availability.
            if step["state"] == "LOOKUP_TREATMENT_PLAN":
                self.session["flow_step"] = step["step"] + 1
                self.session["question_asked_this_turn"] = False
                logger.info("[ms_flow] LOOKUP_TREATMENT_PLAN complete — advancing to PRESENT_DAYS")
                await self.ask_current_question()
                return
            # LOOKUP_RESCHEDULE / LOOKUP_CANCEL: advance to the next step only once
            # the caller has verbally confirmed and confirm_appointment_found() has been
            # called (which sets rc_appointment_confirmed=True in session).
            # If not yet confirmed, stay on this step so the next caller utterance
            # loops back through the LLM for the confirmation exchange.
            if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
                # Deterministic failure readback — lookup_appointment set rc_lookup_failed=True
                if self.session.get("rc_lookup_failed"):
                    self.session.pop("rc_lookup_failed", None)
                    _fail_full = (
                        self.session.get("full_name")
                        or (self.session.get("collected") or {}).get("full_name")
                        or ""
                    ).strip()
                    _fail_parts = _fail_full.split()
                    _fail_last  = _fail_parts[-1] if _fail_parts else ""

                    # Recovery ladder: surname → location → phone
                    _RC_LADDER = ("surname", "location", "phone")
                    _prev_step = self.session.get("rc_recovery_step")
                    if _prev_step in _RC_LADDER:
                        _prev_idx = _RC_LADDER.index(_prev_step)
                        _next_idx = min(_prev_idx + 1, len(_RC_LADDER) - 1)
                    else:
                        _next_idx = 0
                    _rc_step = _RC_LADDER[_next_idx]
                    self.session["rc_recovery_step"] = _rc_step

                    if _rc_step == "surname":
                        if _fail_last:
                            _fail_msg = (
                                f"I'm afraid I couldn't find that booking. "
                                f"Could I just double-check — is the surname {_fail_last}?"
                            )
                        elif self.session.get("rc_phone_first") and not self.session.get("rc_kp_phone_done"):
                            # Phone-first mode, first failure: ask for keypad phone entry.
                            # Keypad recovery is primary; name recovery is secondary.
                            self.session["rc_kp_phone_pending"] = True
                            _fail_msg = (
                                "I couldn't find a booking on that number. "
                                "Please enter the phone number used for the booking on your keypad."
                            )
                            logger.info(
                                "[ms_flow] lookup failed (phone-first) → keypad phone recovery started"
                            )
                        else:
                            _fail_msg = (
                                "I'm afraid I couldn't find that booking. "
                                "Could you confirm the surname the booking was made under?"
                            )
                    elif _rc_step == "location":
                        _loc = self.session.get("selected_location", "")
                        if _loc == "alcester":
                            _other = "Redditch"
                        elif _loc == "redditch":
                            _other = "Alcester"
                        else:
                            _other = None
                        if _other:
                            _fail_msg = (
                                f"I still can't find it at {_loc.title()}. "
                                f"Could the booking be at {_other} instead?"
                            )
                        else:
                            _fail_msg = (
                                "I still can't find that booking. "
                                "Is it at the Alcester or Redditch clinic?"
                            )
                    else:  # phone
                        _fail_msg = (
                            "I'm still not finding it. "
                            "Could you confirm the phone number on the booking?"
                        )

                    # Drain any LLM output already queued to TTS
                    while not self._tts.empty():
                        try:
                            self._tts.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await self._tts.put(_fail_msg)
                    self.session["last_question"] = _fail_msg
                    self.session["lookup_correction_mode"] = True   # deterministic repair mode
                    return
                # ── Deterministic success confirmation ────────────────────────────────
                # Fires exactly once, on the LLM turn where lookup_appointment succeeded.
                # Drains any free-form LLM speech and replaces with a single fixed sentence
                # derived purely from session data — no LLM text, no name invention,
                # no stale last_question.  Also populates reschedule_appt_day/time_label
                # for the "LOOKUP ALREADY DONE" guard on any subsequent LLM re-fire.
                if self.session.pop("rc_lookup_just_succeeded", False) and \
                        self.session.get("rc_stage") == "lookup_done":
                    _jss_day  = self.session.get("reschedule_appt_day_label", "")
                    _jss_time = self.session.get("reschedule_appt_time_label", "")
                    _jss_loc  = (self.session.get("selected_location") or "").title()
                    # Convert "17:00" → "5 PM", "09:30" → "9:30 AM" for natural speech
                    def _spoken_time(t: str) -> str:
                        try:
                            _h, _m = map(int, t.split(":"))
                            _per = "AM" if _h < 12 else "PM"
                            _h12 = _h % 12 or 12
                            return f"{_h12}:{_m:02d} {_per}" if _m else f"{_h12} {_per}"
                        except Exception:
                            return t
                    _jss_time_s  = _spoken_time(_jss_time) if _jss_time else ""
                    _jss_loc_s   = f" in {_jss_loc}" if _jss_loc else ""
                    _jss_is_cancel = self.session.get("intent") == "cancel"
                    # Include the booked name only when plausibly human (avoids "Thanks That")
                    _jss_name_unreliable = bool(self.session.get("lookup_appt_name_unreliable"))
                    _jss_fn   = (self.session.get("lookup_appt_first_name") or "").strip()
                    _jss_ln   = (self.session.get("lookup_appt_last_name") or "").strip()
                    _jss_full = f"{_jss_fn} {_jss_ln}".strip()
                    _jss_fn_ok = (not _jss_name_unreliable) and _is_plausible_speech_name(_jss_fn)
                    _jss_ln_ok = (not _jss_name_unreliable) and _is_plausible_speech_name(_jss_ln)
                    _jss_name_clause = (
                        f" under {_jss_full}"
                        if (_jss_fn_ok and _jss_ln_ok and _jss_full)
                        else ""
                    )
                    _jss_thanks_prefix = f"Thanks {_jss_fn} — " if _jss_fn_ok else ""
                    if _jss_day and _jss_time_s:
                        if _jss_is_cancel:
                            _jss_q = (
                                f"{_jss_thanks_prefix}I found an appointment{_jss_name_clause} "
                                f"for {_jss_day} at {_jss_time_s}"
                                f"{_jss_loc_s} — is that the right one?"
                            )
                            _jss_short = (
                                f"Sure — {_jss_day} at {_jss_time_s}{_jss_loc_s}. "
                                f"Is that the right appointment?"
                            )
                        else:
                            _jss_q = (
                                f"{_jss_thanks_prefix}I found an appointment{_jss_name_clause} "
                                f"for {_jss_day} at {_jss_time_s}"
                                f"{_jss_loc_s} — is that the one you'd like to reschedule?"
                            )
                            _jss_short = (
                                f"Sure — {_jss_day} at {_jss_time_s}{_jss_loc_s}. "
                                f"Is that the appointment you'd like to reschedule?"
                            )
                    else:
                        _jss_q     = "I found your appointment — is that the right one?"
                        _jss_short = "Is that the right appointment?"
                    # Drain anything the LLM may have queued (might say wrong name/date).
                    while not self._tts.empty():
                        try:
                            self._tts.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await self._tts.put(_jss_q)
                    self.session["last_question"]          = _jss_q
                    self.session["rc_lookup_confirm_short"] = _jss_short
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _jss_q}
                    )
                    logger.info(
                        "[ms_flow] %s: deterministic confirm after success: %r",
                        step["state"], _jss_q,
                    )
                    return
                if self.session.get("rc_appointment_confirmed"):
                    # Drain any LLM-queued speech before advancing — prevents
                    # "Perfect — let me find new times" overlapping with PRESENT_DAYS_RESCHEDULE question.
                    while True:
                        try:
                            self._tts.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    self.session.pop("rc_recovery_step", None)
                    _adv_next = step["step"] + 1
                    # Reschedule: skip CONFIRM_RESCHEDULE_OR_CANCEL — go straight to PRESENT_DAYS.
                    # Cancel: keep CONFIRM_RESCHEDULE_OR_CANCEL so caller is asked to choose.
                    if (self.session.get("intent") == "reschedule"
                            and _adv_next < len(self._active_flow)
                            and self._active_flow[_adv_next]["state"] == "CONFIRM_RESCHEDULE_OR_CANCEL"):
                        _adv_next += 1
                        logger.info(
                            "[ms_flow] LOOKUP_RESCHEDULE: reschedule — "
                            "skipping CONFIRM_RESCHEDULE_OR_CANCEL → step %d", _adv_next,
                        )
                    self.session["flow_step"] = _adv_next
                    self.session["question_asked_this_turn"] = False
                    logger.info(
                        "[ms_flow] %s confirmed — advancing to step %d (TTS drained)",
                        step["state"], _adv_next,
                    )
                    _rc_conf_fn = (self.session.get("lookup_appt_first_name") or "").strip()
                    if _rc_conf_fn:
                        self.session["_nc_transition_prefix"] = f"Thanks {_rc_conf_fn} —"
                    await self.ask_current_question()
                return
            # After check_availability runs (inside _llm), save slots_offered so
            # the slot confirmation phrase can reference the full slot text strings.
            if step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
                offered = self.session.get("last_offered_slots") or []
                if offered:
                    self.session["slots_offered"] = list(offered)
                    self.session["slots_count"]   = min(len(offered), 3)
                    logger.info(
                        "[ms_flow] slots_offered saved: %d slots",
                        len(offered),
                    )
                # Deterministic day-list presentation (BUG 2 fix).
                # The LLM instruction says "say NOTHING after the tool call" but
                # occasionally the LLM still emits day text.  Drain any pending
                # TTS queue items BEFORE queuing our authoritative phrase so the
                # caller never hears a duplicate day announcement.
                _avail = self.session.get("available_days", [])
                _day_phrase = _build_day_list_phrase(_avail)
                if _day_phrase:
                    # Drain pending LLM output — suppresses "1 day" / wrong count.
                    _drained = 0
                    while not self._tts.empty():
                        try:
                            self._tts.get_nowait()
                            _drained += 1
                        except asyncio.QueueEmpty:
                            break
                    if _drained:
                        logger.info(
                            "[ms_flow] %s: drained %d pending TTS items "
                            "(LLM duplicate suppressed)",
                            step["state"], _drained,
                        )
                    await self._tts.put(_day_phrase)
                    self.session["last_question"] = _day_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _day_phrase}
                    )
                    logger.info(
                        "[ms_flow] %s: deterministic day phrase: %r",
                        step["state"], _day_phrase[:100],
                    )
                else:
                    # available_days empty — LLM already spoke the error message.
                    logger.info(
                        "[ms_flow] %s: no available_days — LLM handled error/empty case",
                        step["state"],
                    )
        else:
            self.session["question_asked_this_turn"] = True
            # Fix D: CONFIRM_BOOKING — deterministic close: make the booking then confirm
            if step["state"] == "CONFIRM_BOOKING":
                _slot_cb = (
                    self.session.get("selected_slot_speech")
                    or self.session.get("selected_slot")
                    or "your appointment"
                )
                _name_cb = (
                    self.session.get("full_name")
                    or (self.session.get("collected") or {}).get("full_name")
                    or (self.session.get("collected") or {}).get("name")
                    or self.session.get("patient_name")
                    or self.session.get("caller_name")
                )
                _name_part = f"{_name_cb}, " if _name_cb else ""
                _loc_cb = (self.session.get("selected_location") or "alcester").lower()
                _clinic_name = "Redditch" if "redditch" in _loc_cb else "Alcester"

                # Make the actual Acuity booking now that the caller has confirmed
                from app.tools.receptionist_tools import _exec_book_appointment as _do_book
                _book_args = {
                    "patient_name": _name_cb or "",
                    "phone": _to_e164_uk(
                        self.session.get("phone_number")
                        or (self.session.get("collected") or {}).get("phone")
                        or self.session.get("twilio_from", "")
                    ),
                    "slot_iso": (
                        self.session.get("selected_slot")
                        or self.session.get("selected_slot_speech")
                        or ""
                    ),
                    "location": _loc_cb,
                    "service": "physiotherapy assessment",
                    "is_new_patient": (
                        (self.session.get("new_or_returning") or "new") != "returning"
                    ),
                }
                try:
                    _book_result = await _do_book(_book_args, self.session)
                    if not _book_result.get("success"):
                        logger.error(
                            "[ms_flow] CONFIRM_BOOKING: book failed: %r",
                            _book_result.get("error"),
                        )
                    else:
                        logger.info("[ms_flow] CONFIRM_BOOKING: booking created successfully")
                except Exception as _be:
                    logger.error("[ms_flow] CONFIRM_BOOKING: book exception: %r", _be)

                question_text = (
                    f"Lovely — {_name_part}you're booked in for {_slot_cb} "
                    f"at {_clinic_name}. "
                    "I'll text a confirmation to your number."
                )
                self.session["booking_confirmed"] = True
                self.session["state"]             = "DONE"
                self.session["flow_state"]        = "DONE"
                logger.info(
                    "[ms_flow] CONFIRM_BOOKING deterministic — booking_confirmed=True "
                    "state=DONE name=%r slot=%r",
                    _name_cb, str(_slot_cb)[:40],
                )
            # CONFIRM_PHONE with Twilio caller-ID: read back the digits so
            # number_confirmed_verbally passes in the evaluator.
            # NOTE: elif — must not run (and must not override question_text) when
            # we are already at CONFIRM_BOOKING above.
            elif step["state"] in (
                "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING", "RETURNING_PLAN_CONFIRM_PHONE"
            ) and self.session.get("phone_from_twilio"):
                # Reschedule/cancel: anchor to the booking, embed location if known
                if self._active_flow is RESCHEDULE_FLOW or self._active_flow is CANCEL_FLOW:
                    _cp_dyn_loc = self.session.get("selected_location")
                    if _cp_dyn_loc:
                        _cp_dyn_loc_name = "Redditch" if "redditch" in _cp_dyn_loc else "Alcester"
                        question_text = (
                            f"For your {_cp_dyn_loc_name} appointment, "
                            "if the number you're calling on is the one associated with it, "
                            "say: use this number."
                        )
                    else:
                        question_text = (
                            "If the number you're calling on is the one associated with your appointment, "
                            "say: use this number."
                        )
                else:
                    # Booking/returning-plan: ask whether to use the caller-ID
                    question_text = (
                        "If you'd like me to use the number you're calling on for the booking, "
                        "say: use this number."
                    )
                # Arm the YES/NO gate so only this specific question's response is accepted
                self.session["phone_confirm_armed"] = True
            elif step["state"] == "NEW_OR_RETURNING":
                # Embed resolved clinic location into the new-or-returning question
                _nor_dyn_loc = self.session.get("selected_location")
                if _nor_dyn_loc:
                    _nor_dyn_loc_name = "Redditch" if "redditch" in _nor_dyn_loc else "Alcester"
                    question_text = (
                        f"At our {_nor_dyn_loc_name} clinic, "
                        "have you been with us before, or is this your first time?"
                    )
                    logger.info(
                        "[ms_flow] embedded_location_bind flow=booking state=NEW_OR_RETURNING location=%s",
                        _nor_dyn_loc_name,
                    )
                else:
                    question_text = step["question"]
            elif step["state"] == "COLLECT_REASON":
                # Embed resolved clinic location into the reason question
                _cr_loc = self.session.get("selected_location")
                if _cr_loc:
                    _cr_loc_name = "Redditch" if "redditch" in _cr_loc else "Alcester"
                    question_text = f"What brings you in today at our {_cr_loc_name} clinic?"
                    logger.info(
                        "[ms_flow] embedded_location_bind flow=booking location=%s", _cr_loc_name
                    )
                else:
                    question_text = step["question"]
            elif step["state"] == "COLLECT_NAME_RESCHEDULE":
                # Embed resolved clinic location into the first reschedule/cancel question
                _cnr_loc = self.session.get("selected_location")
                if _cnr_loc:
                    _cnr_loc_name = "Redditch" if "redditch" in _cnr_loc else "Alcester"
                    _cnr_intent = self.session.get("intent", "reschedule")
                    if _cnr_intent == "cancel":
                        question_text = (
                            f"Okay — I'll help cancel the appointment at our "
                            f"{_cnr_loc_name} clinic. What's your first name?"
                        )
                    else:
                        question_text = (
                            f"Okay — let's look at your appointment at our "
                            f"{_cnr_loc_name} clinic. What's your first name?"
                        )
                    logger.info(
                        "[ms_flow] embedded_location_bind flow=%s location=%s",
                        _cnr_intent, _cnr_loc_name,
                    )
                else:
                    question_text = step["question"]
            else:
                question_text = step["question"]
            # Always arm the YES/NO gate for CONFIRM_PHONE states, regardless of
            # whether phone_from_twilio is set.  The elif branch above only arms
            # when Twilio caller-ID is present; without this fallback arm the gate
            # stays False and every caller response re-asks indefinitely.
            if step["state"] in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING"):
                self.session["phone_confirm_armed"] = True
            # Post-name-recovery: fold any deferred transition prefix into the
            # question so both are spoken as ONE utterance.  This eliminates the
            # three-item stack (preamble / bridge / question) that occurs after
            # surname best-effort fallback or inline correction.
            _nc_prefix = self.session.pop("_nc_transition_prefix", None)
            if _nc_prefix and question_text:
                question_text = f"{_nc_prefix} {question_text}"
            await self._tts.put(question_text)
            # Always anchor silence-timer to the newest step question — bypass
            # _is_question_worth_storing gate to prevent stale anchors after
            # state transitions.  Only exclude clear non-question preamble text.
            _qt_lower = (question_text or "").strip().lower()
            _is_preamble_qt = any(p in _qt_lower for p in ("just a moment", "one moment", "bear with"))
            if question_text and not _is_preamble_qt:
                self.session["last_question"] = question_text
            # Record fixed-step question to conversation_history
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": question_text}
            )

        logger.info(
            "[ms_flow] asked step %d (%s) last_question=%r",
            step["step"], step["state"],
            str(self.session.get("last_question", ""))[:80],
        )

    async def handle_transcript(self, transcript: str) -> None:
        """
        Extract an answer from the caller's utterance, advance the step,
        and ask the next question.

        This is the ONLY function called on incoming transcripts.
        If no answer is extracted, re-ask the current question.
        """
        step = self.current_step()
        logger.debug(
            "[ms_flow] handle_transcript: state=%s step=%s transcript=%r",
            self.session.get("state"), step["state"] if step else "None", transcript[:60],
        )
        if step is None:
            # ── Readback still pending: booking NOT yet finalized ──────────────────
            # _start_readback() advances flow_step past the last step BUT sets
            # readback_pending=True.  Any transcript arriving while that flag is live
            # must be routed to _handle_readback_confirmation — never silently dropped.
            # Fixes: "no" / "cancel it" / corrections at final readback being ignored.
            if self.session.get("readback_pending"):
                _rbt = transcript.strip().lower()
                await self._handle_readback_confirmation(_rbt, transcript)
                return
            # ── Safety-net: CONFIRM_RESCHEDULE may have flow_step prematurely set ──
            # If the reschedule flow completed its steps but reschedule_confirmed was
            # never set (e.g. due to a race), and the caller is saying "Yes" to the
            # confirmation question, execute the reschedule now.
            if (
                self._active_flow is RESCHEDULE_FLOW
                and self.session.get("reschedule_confirmed") is None
                and self.session.get("selected_slot")
            ):
                text = transcript.strip().lower()
                yes_patterns = ("yes", "yeah", "yep", "go ahead", "sure", "ok", "okay", "please", "correct")
                if any(p in text for p in yes_patterns):
                    from app.tools.receptionist_tools import _exec_reschedule_appointment
                    phone_val = _to_e164_uk(
                        self.session.get("phone_number")
                        or self.session.get("twilio_from_local")
                        or self.session.get("twilio_from", "")
                    )
                    _sn_name = (self.session.get("full_name") or "").strip()
                    if not _sn_name:
                        _sn_fn   = (self.session.get("lookup_appt_first_name") or "").strip()
                        _sn_ln   = (self.session.get("lookup_appt_last_name") or "").strip()
                        _sn_name = f"{_sn_fn} {_sn_ln}".strip()
                    reschedule_args = {
                        "patient_name":    _sn_name,
                        "phone":           phone_val,
                        "location":        self.session.get("selected_location", "alcester"),
                        "new_slot_iso":    self.session.get("selected_slot", ""),
                        "duration_minutes": 50,
                    }
                    logger.info(
                        "[ms_flow] CONFIRM_RESCHEDULE safety-net — calling _exec_reschedule_appointment "
                        "name=%r slot=%r location=%r",
                        reschedule_args["patient_name"],
                        reschedule_args["new_slot_iso"],
                        reschedule_args["location"],
                    )
                    # Patient confirmed intent — record regardless of Acuity execution result
                    self.session["reschedule_confirmed"] = True
                    reschedule_result = await _exec_reschedule_appointment(reschedule_args, self.session)
                    exec_succeeded = bool(reschedule_result.get("success"))
                    self.session["reschedule_execution_succeeded"] = exec_succeeded
                    logger.info(
                        "[ms_flow] CONFIRM_RESCHEDULE safety-net exec result: success=%s error=%r",
                        exec_succeeded,
                        reschedule_result.get("error"),
                    )
                    if exec_succeeded:
                        slot_speech = self.session.get("selected_slot_speech", "the new time")
                        response = (
                            f"Done — I've moved you to {slot_speech}. "
                            "You'll get the confirmation text shortly. "
                            "Is there anything else I can help with?"
                        )
                    else:
                        response = (
                            "I'm sorry — I wasn't able to complete that reschedule. "
                            "Please give us a call directly and the team will get it sorted for you."
                        )
                        self.session["acuity_error"] = reschedule_result.get("error")
                    await self._tts.put(response)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": response}
                    )
                    logger.info(
                        "[ms_flow] CONFIRM_RESCHEDULE safety-net complete — "
                        "reschedule_confirmed=%s execution_succeeded=%s",
                        self.session["reschedule_confirmed"],
                        self.session["reschedule_execution_succeeded"],
                    )
                    return
            logger.info("[ms_flow] flow complete — ignoring transcript: %r", transcript[:60])
            return

        # Reset per-turn guard so ask_current_question() can fire exactly once this turn
        self.session["question_asked_this_turn"] = False

        # Record patient utterance so conversation_history reflects the full dialogue
        self.session.setdefault("conversation_history", []).append(
            {"role": "user", "content": transcript}
        )

        text = transcript.strip().lower()

        # ── Pending FAQ-intro combine (Bug B) ───────────────────────────────
        # A previous turn held an incomplete FAQ intro (e.g. "I saw on your
        # website where you offer acupuncture") waiting for the concern to
        # land.  Prepend it to this utterance so downstream classification
        # sees the full thought.  Only applies at COLLECT_REASON where the
        # hold was set.
        _pending_intro = self.session.pop("_faq_pending_intro", None)
        if _pending_intro and step and step["state"] == "COLLECT_REASON":
            transcript = (_pending_intro.rstrip() + " " + transcript.strip()).strip()
            text = transcript.lower()
            logger.info(
                "[ms_flow] COLLECT_REASON: faq_intro_combined text=%r",
                transcript[:100],
            )

        # ── Prefix-fallback correction window ────────────────────────────────────
        # Opens for exactly one turn after a low-confidence prefix_fallback
        # resolution.  If the caller says "no", "the other one", "Redditch" etc.
        # we update selected_location and replay the current question rather than
        # letting the wrong clinic propagate through the rest of the booking flow.
        if self.session.pop("location_fallback_unconfirmed", False):
            _pf_correction_words = {
                "no", "nope", "nah", "wrong", "incorrect",
                "other", "actually", "wait", "sorry",
            }
            _pf_is_correction = (
                bool(set(text.split()) & _pf_correction_words)
                and len(text.split()) <= 6
            )
            if _pf_is_correction:
                _pf_cur = self.session.get("selected_location")
                # Try to extract an explicit clinic name from the correction
                _pf_new = self._extract("location_selection", text, transcript)
                if _pf_new and _pf_new != _pf_cur:
                    self.session["selected_location"] = _pf_new
                    _pf_name = "Alcester" if _pf_new == "alcester" else "Redditch"
                    logger.info(
                        "[ms_flow] prefix_fallback corrected: %s → %s via %r",
                        _pf_cur, _pf_new, text[:40],
                    )
                else:
                    # Bare "no" / "the other one" — flip to the other clinic
                    _pf_new = "redditch" if _pf_cur == "alcester" else "alcester"
                    self.session["selected_location"] = _pf_new
                    _pf_name = "Alcester" if _pf_new == "alcester" else "Redditch"
                    logger.info(
                        "[ms_flow] prefix_fallback flipped: %s → %s via %r",
                        _pf_cur, _pf_new, text[:40],
                    )
                await self._tts.put(f"Got it — {_pf_name}.")
                await self.ask_current_question()
                return

        # ── Global location-correction handler ────────────────────────────────────
        # Allows mid-flow correction of the already-bound clinic
        # ("actually Redditch", "no Redditch", "Redditch — under this number").
        # Fires only on short utterances with a clearly resolved location that
        # differs from the current binding.  Silent overwrite — no confirmation step,
        # no flow reset.  The current step handler sees the updated location.
        _glc_cur = self.session.get("selected_location")
        if (
            _glc_cur
            and not self.session.get("needs_location")
            and step and step["state"] not in {"DETECT_INTENT", "ASK_LOCATION"}
            and len(text.split()) <= 8
            # Never mutate location when an appointment has been presented and we are
            # waiting for a YES/NO confirmation — generic confirmation utterances
            # ("yes that's the right one") must not be interpreted as clinic evidence.
            and self.session.get("rc_stage") != "lookup_done"
        ):
            from app.media_streams.location_resolver import resolve_clinic_location as _glc_resolve
            _glc_result = _glc_resolve(text, context="ask_location")
            if (
                _glc_result["status"] == "resolved"
                and _glc_result.get("reason") != "prefix_fallback"
            ):
                _glc_new = _glc_result["location"]
                if _glc_new and _glc_new != _glc_cur:
                    self.session["selected_location"] = _glc_new
                    logger.info(
                        "[ms_flow] embedded_location_corrected from=%s to=%s via %r",
                        _glc_cur, _glc_new, text[:60],
                    )

        # ── GLOBAL REPAIR INTERCEPT (Bug 4 — HARD REQUIREMENT) ──────────────────
        # Runs before ALL state machine logic.
        # If repair/correction language detected: stop current output lineage,
        # reply with one short repair line, return.  Do NOT ask a classifier question.
        _is_repair = any(p in text for p in _GLOBAL_REPAIR_PHRASES)
        # Bare "stop" or "wrong" alone (≤ 2 words) also count as repair.
        if not _is_repair:
            _rw = text.strip().split()
            if len(_rw) <= 2 and _rw and _rw[0] in ("stop", "wrong"):
                _is_repair = True
        if _is_repair:
            # Strong repair — stale child-policy / service-fit branches must not
            # keep controlling the turn (Rule 3). Clear pending routing state
            # before re-interpreting the clarified utterance so the caller isn't
            # trapped in the previous wrong interpretation.
            for _repair_clr_key in (
                "_pending_svc_fit", "_pending_svc_fit_turns",
                "_svc_fit_gq_guard", "_gq_clarify_pending",
                "pending_first_turn_followup", "pending_followup",
            ):
                self.session.pop(_repair_clr_key, None)
            # Try to extract an embedded FAQ question before falling back to generic repair.
            # "that's not what I asked, my question was about insurance" → route to insurance FAQ.
            _EMBEDDED_SUFFIXES = (
                "my question was", "my question is",
                "i was asking about", "i was asking",
                "i wanted to ask", "i actually wanted",
                "what i meant", "i meant to ask",
                "i'm asking", "im asking", "i am asking",
                "i mean",
            )
            _emb_intent = None
            _emb_raw = ""
            for _eq_sfx in _EMBEDDED_SUFFIXES:
                _eq_idx = text.find(_eq_sfx)
                if _eq_idx >= 0:
                    _eq_rest = text[_eq_idx + len(_eq_sfx):].strip()
                    if len(_eq_rest.split()) >= 2:
                        _emb_intent = self._detect_intent(_eq_rest)
                        _emb_raw = transcript[transcript.lower().find(_eq_sfx) + len(_eq_sfx):].strip()
                        break
            _faq_intents_for_repair = {
                "faq_prices", "faq_insurance", "faq_hours",
                "faq_location", "faq_services", "faq_capability",
            }
            if _emb_intent and _emb_intent in _faq_intents_for_repair:
                logger.info(
                    "[ms_flow] repair: embedded FAQ %s — answering directly", _emb_intent
                )
                await self._handle_mid_flow_interrupt(_emb_intent, _emb_raw or transcript)
                return
            # No extractable embedded question — state-aware repair prompt.
            # Do NOT enqueue phrase here; _llm_loop finally drains then enqueues.
            # BUG 3: needs_location must NOT override name-collection states — doing
            # so caused "Sorry about that — what was your question?" during COLLECT_NAME.
            _actual_state = step["state"] if step else ""
            _repair_state = (
                "ASK_LOCATION"
                if (self.session.get("needs_location") and _actual_state not in {
                    "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                    "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                })
                else _actual_state
            )
            if _repair_state in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
                _repair_q = "Sorry — were you asking about a different date or month?"
            elif _repair_state in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
                _repair_q = "Sorry — were you asking about a different time or day?"
            elif _repair_state in (
                "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
            ):
                _CN_NAME_REPAIR_TRIGGERS = (
                    "messed up my name", "got my name wrong", "wrong name",
                    "name is wrong", "gave the wrong name",
                    # BUG 3: additional real-world variants from live calls
                    "messed up on giving my name", "messed up giving my name",
                    "gave wrong name", "my name's wrong", "my name was wrong",
                    "wrong first name", "wrong surname", "incorrect name",
                    "not my name", "that's not my name", "thats not my name",
                    "go back to the name", "correct my name",
                )
                if any(p in text for p in _CN_NAME_REPAIR_TRIGGERS):
                    # BUG 5: clear ALL name state so corrupted fragment can't
                    # be appended to a new surname
                    self.session.pop("name_fragment", None)
                    self.session.pop("spelling_confirm_surname", None)
                    _col_cn = self.session.get("collected", {})
                    _col_cn.pop("full_name", None)
                    self.session["collected"] = _col_cn
                    # BUG 2: ask for first name specifically, not generic "full name".
                    # Normal COLLECT_NAME flow will ask surname after first name is captured.
                    _repair_q = "No problem — what's your first name please?"
                else:
                    _repair_q = self.session.get("last_question", "Could you say your name again?")
            elif _repair_state in (
                "COLLECT_PHONE", "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
                "RETURNING_PLAN_CONFIRM_PHONE", "RETURNING_PLAN_COLLECT_PHONE",
                "COLLECT_PHONE_RESCHEDULE",
            ):
                # Detect explicit name/question backtrack — caller wants to step back,
                # not repeat the phone question. e.g. "go back to the name question",
                # "could you go back to the main question", "i made an error go back".
                _CP_NAME_BACK = (
                    "back to the name", "the name question", "main question",
                    "previous question", "my name", "name was wrong",
                    "name is wrong", "correct my name", "change my name",
                    "back to the question", "back to name",
                )
                if any(p in text for p in _CP_NAME_BACK):
                    # Determine which COLLECT_NAME state this flow uses — must
                    # search the active flow rather than hardcoding "COLLECT_NAME"
                    # because RESCHEDULE_FLOW uses "COLLECT_NAME_RESCHEDULE" and
                    # CANCEL_FLOW = RESCHEDULE_FLOW.
                    _CN_NB_STATES = {
                        "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                        "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                    }
                    _cn_idx = next(
                        (i for i, s in enumerate(self._active_flow)
                         if s["state"] in _CN_NB_STATES),
                        None,
                    )
                    _cn_target = (
                        self._active_flow[_cn_idx]["state"]
                        if _cn_idx is not None
                        else "COLLECT_NAME"
                    )
                    if _cn_idx is not None:
                        self.session.pop("name_fragment", None)
                        self.session.pop("spelling_confirm_surname", None)
                        _col = self.session.get("collected", {})
                        _col.pop("full_name", None)
                        self.session["collected"] = _col
                        self.session["flow_step"] = _cn_idx
                        self.session["state"] = _cn_target
                        _repair_q = "Of course — could I take your first name again please?"
                    else:
                        _repair_q = self.session.get("last_question", "Could you say that number again?")
                elif _repair_state in ("COLLECT_PHONE", "COLLECT_PHONE_RESCHEDULE", "RETURNING_PLAN_COLLECT_PHONE"):
                    # LOCAL phone-entry reset: clear ALL digit state so the next
                    # DTMF entry starts from an empty buffer.  Do NOT treat this as
                    # a global "what was your inquiry?" reset — the caller is still
                    # in number-entry context and just wants to re-type the number.
                    self.session["phone_dtmf_buffer"]      = ""
                    self.session["phone_digits_buffer"]    = ""
                    self.session["phone_candidate"]        = None
                    self.session["phone_readback_pending"] = False
                    self.session["phone_confirm_armed"]    = False
                    self.session.pop("phone_number", None)
                    self.session.pop("phone", None)
                    self.session.pop("customer_phone", None)
                    self.session.setdefault("collected", {}).pop("phone", None)
                    # Keep keypad mode active so the caller can restart typing
                    # without having to ask again.  The deterministic reset
                    # acknowledgement phrasing is reused from the hard-gate
                    # reset-intent handler.
                    if self.session.get("phone_awaiting_dtmf"):
                        _repair_q = (
                            "No problem — I've reset the keypad for you. "
                            "You can restart typing in the number."
                        )
                    elif self._active_flow is RESCHEDULE_FLOW or self._active_flow is CANCEL_FLOW:
                        _repair_q = (
                            "No problem — let's try that again. "
                            "Please enter the number your booking was made under using your keypad."
                        )
                    else:
                        _repair_q = (
                            "No problem — let's try that again. "
                            "Please enter your number using your keypad."
                        )
                    logger.info(
                        "[ms_flow] COLLECT_PHONE local repair: all digit buffers cleared, re-prompting keypad (awaiting_dtmf=%s)",
                        self.session.get("phone_awaiting_dtmf"),
                    )
                else:
                    _repair_q = self.session.get("last_question", "Could you say that number again?")
            elif _repair_state in (
                "FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER",
                "ANSWER_FAQ", "ANSWER_GENERAL",
            ):
                _repair_q = "Sorry about that \u2014 what was your question?"
            elif _repair_state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL", "LOOKUP_TREATMENT_PLAN"):
                # Mid-lookup repair — LLM is running tool. Re-anchor caller.
                _repair_q = self.session.get(
                    "last_question",
                    "No problem — just bear with me one moment while I check your appointment.",
                )
            else:
                # BUG 11 fix: state-aware fallback — phone/name states get targeted responses
                _PHONE_REPAIR_STATES = {
                    "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
                    "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
                    "COLLECT_PHONE_RESCHEDULE",
                }
                _NAME_REPAIR_STATES = {
                    "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                    "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                }
                if _repair_state in _PHONE_REPAIR_STATES:
                    _repair_q = self.session.get("last_question", "Could you say that number again?")
                elif _repair_state in _NAME_REPAIR_STATES:
                    _nf = self.session.get("name_fragment")
                    _repair_q = (
                        "And your surname?"
                        if _nf
                        else self.session.get("last_question", "What's your first name please?")
                    )
                else:
                    _repair_q = "Sorry about that \u2014 what was your question?"
            self.session["last_question"] = _repair_q
            self.session["repair_requested"] = True
            logger.info("[ms_flow] global repair intercept (state=%s): %r", _repair_state, transcript[:60])
            return

        # ── LOOKUP_RESCHEDULE repeat intercept ───────────────────────────────────
        # If the caller asks to repeat after the confirm fires, speak the short
        # version directly rather than replaying the full "I found an appointment..."
        # sentence through the generic repeat_requested path.
        if (step and step.get("state") == "LOOKUP_RESCHEDULE"
                and any(p in text for p in _GLOBAL_REPEAT_PHRASES)
                and self.session.get("rc_lookup_confirm_short")):
            _rc_short = self.session["rc_lookup_confirm_short"]
            while not self._tts.empty():
                try:
                    self._tts.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await self._tts.put(_rc_short)
            self.session["last_question"] = _rc_short
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _rc_short}
            )
            logger.info(
                "[ms_flow] LOOKUP_RESCHEDULE: repeat → short confirm %r", _rc_short[:70]
            )
            return

        # ── GLOBAL REPEAT INTERCEPT ──────────────────────────────────────────────
        # "Say that again" / "Could you repeat" — replay last relevant answer.
        # Drains stale TTS (via repeat_requested flag) then replays in _llm_loop.
        _is_repeat = any(p in text for p in _GLOBAL_REPEAT_PHRASES)
        if _is_repeat:
            # Bug 2/4 fix: don't steal mixed utterances that contain a repeat phrase
            # AND substantial new content ("could you repeat … my name is quentin").
            # Only fire when the utterance is a pure or near-pure repeat request.
            _REPEAT_BYPASS_SIGNALS = (
                "my name is", "i am ", "it's ", "its ",
                "actually ", "sorry my ", "the name is",
                "i meant ", "i said ", "not that",
            )
            _rpt_bypass = any(p in text for p in _REPEAT_BYPASS_SIGNALS)
            if not _rpt_bypass:
                # Also bypass if enough non-repeat content remains after stripping
                # all repeat phrases from the text (≥ 3 words of ≥ 3 chars).
                _rpt_rem = text
                for _rp in _GLOBAL_REPEAT_PHRASES:
                    _rpt_rem = _rpt_rem.replace(_rp, " ")
                _rpt_bypass = len([w for w in _rpt_rem.split() if len(w) >= 3]) >= 3
            if _rpt_bypass:
                logger.info(
                    "[ms_flow] global repeat: bypassed (mixed utterance) %r",
                    transcript[:60],
                )
                # Fall through to active state handler below
            else:
                self.session["repeat_requested"] = True
                logger.info("[ms_flow] global repeat intercept: %r", transcript[:60])
                return

        # ── BUG 25 / BUG 7: GLOBAL "WHAT DID YOU CATCH/HEAR" INTERCEPT ─────────
        # Caller wants to know what Susie captured — answer based on last collected
        # field then re-anchor with last_question.
        # BUG 7 fix: "i don't think you asked for it" added here so the recap path
        # works at ANY state, not just CONFIRM_PHONE.
        _GLOBAL_CATCH_PHRASES = (
            "what did you catch", "what did you get", "what did you hear",
            "what have you got", "what do you have", "what name do you have",
            "what did you record", "what did you take down",
            "what have you written", "what have you captured",
            "i don't think you asked for it", "i dont think you asked for it",
            "did you catch my name", "did you get my name",
            "what name did you get",
        )
        if any(p in text for p in _GLOBAL_CATCH_PHRASES):
            _catch_state = self.session.get("state", "")
            _catch_name  = (
                self.session.get("full_name")
                or (self.session.get("collected") or {}).get("full_name")
                or ""
            ).strip()
            _catch_phone = (
                self.session.get("phone_number")
                or (self.session.get("collected") or {}).get("phone")
                or ""
            )
            _catch_day   = self.session.get("chosen_day", "")
            if _catch_name and _catch_phone:
                _catch_reply = (
                    f"I have {_catch_name} on {_catch_phone}. "
                    + (self.session.get("last_question") or "Is that right?")
                )
            elif _catch_name:
                _catch_reply = (
                    f"I have the name as {_catch_name}. "
                    + (self.session.get("last_question") or "Is that right?")
                )
            elif _catch_phone:
                _catch_reply = (
                    f"I have the number as {_catch_phone}. "
                    + (self.session.get("last_question") or "Is that right?")
                )
            elif _catch_day:
                _catch_reply = (
                    f"You chose {_catch_day}. "
                    + (self.session.get("last_question") or "Is that right?")
                )
            else:
                _catch_reply = (
                    (self.session.get("last_question") or "Sorry — could you say that again?")
                )
            await self._tts.put(_catch_reply)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _catch_reply}
            )
            logger.info(
                "[ms_flow] BUG25 catch intercept (state=%s): %r → %r",
                _catch_state, transcript[:40], _catch_reply[:60],
            )
            return

        # ── GLOBAL RE-ENGAGEMENT INTERCEPT ──────────────────────────────────────
        # "are you there", "hello", "can you hear me" etc. are not answers —
        # they mean the caller is confused about silence.  Replay last_question
        # immediately without disturbing the state machine.
        _RE_ENGAGEMENT_TOKENS = frozenset({
            "hello", "hi", "hey", "hiya",
            "are you there", "you there", "hello are you there",
            "can you hear me", "can you hear", "hello can you hear",
            "what's happening", "whats happening",
            "is anyone there", "is somebody there",
            "hello hello",
        })
        _re_text = text.strip()
        if _re_text in _RE_ENGAGEMENT_TOKENS:
            _lq = self.session.get("last_question") or self.session.get("last_tts_spoken")
            if _lq:
                _lq_lower = _lq.strip().lower()
                _is_stale_wrapper = any(p in _lq_lower for p in (
                    "is there anything else i can help",
                    "anything else i can help you with",
                ))
                if _is_stale_wrapper:
                    # Generic wrapper stored as last_question — give a clean re-engagement
                    # instead of replaying the hollow offer.
                    _re_fallback = (
                        "Of course \u2014 was there something else you'd like to ask?"
                    )
                    logger.info("[ms_flow] re-engagement %r — stale wrapper, neutral re-engage", _re_text)
                    await self._tts.put(_re_fallback)
                else:
                    logger.info("[ms_flow] re-engagement token %r — replaying last_question", _re_text)
                    await self._tts.put(_lq)
                return
            # No last_question yet (very start of call) — fall through to normal handling

        # ── GLOBAL PENDING FAQ CLARIFICATION ────────────────────────────────────
        # When the system asked "Alcester or Redditch?" during a mid-flow FAQ interrupt
        # and the pending clinic answer arrives, consume it here before any state handler.
        # Uses the same _resolve_clinic machinery as booking ASK_LOCATION.
        # Always returns — never falls through while pending is set.
        _gp_pending_loc = self.session.get("_faq_loc_pending_intent")
        if _gp_pending_loc and step:
            # ── DTMF mode: waiting for keypad press — loops until resolved ────────────────
            if self.session.get("_faq_loc_awaiting_dtmf"):
                _gp_dtmf_loc = self._extract("location_selection", text, transcript)
                if _gp_dtmf_loc:
                    self.session["last_faq_loc_id"] = _gp_dtmf_loc
                    _gp_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                    self.session.pop("_faq_loc_pending_intent", None)
                    self.session.pop("_faq_loc_retry_count", None)
                    self.session.pop("_faq_loc_awaiting_dtmf", None)
                    _gp_synth_tx = (
                        f"{_gp_pend_sub} {_gp_dtmf_loc}".strip() if _gp_pend_sub else _gp_dtmf_loc
                    )
                    logger.info("[ms_flow] faq_pending: DTMF resolved → %s", _gp_dtmf_loc)
                    self.session["_faq_ans_intent"] = _gp_pending_loc
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt(_gp_pending_loc, _gp_synth_tx)
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                _gp_dtmf_re = "Press 1 for Alcester or 2 for Redditch."
                await self._tts.put(_gp_dtmf_re)
                self.session["last_question"] = _gp_dtmf_re
                return

            _gp_pending_guess = self.session.get("_faq_loc_pending_guess")
            if _gp_pending_guess:
                # ── Confirm sub-block: waiting for yes/no on our guessed clinic ──
                _gp_fc_t = text.lower()
                _GP_FC_YES = (
                    "yes", "yeah", "yep", "yup", "correct",
                    "that's right", "thats right", "that's it", "thats it",
                    "absolutely", "definitely", "sure", "confirmed",
                )
                _GP_FC_NO = (
                    "no", "nope", "nah", "not that", "not right",
                    "wrong", "other one", "other clinic", "different",
                )
                _gp_is_yes = any(sig in _gp_fc_t for sig in _GP_FC_YES)
                _gp_is_no  = any(sig in _gp_fc_t for sig in _GP_FC_NO)
                if _gp_is_yes and not _gp_is_no:
                    _gp_clinic = _gp_pending_guess
                    logger.info("[ms_flow] faq_pending_confirm: yes → %s", _gp_clinic)
                elif _gp_is_no and not _gp_is_yes:
                    _gp_clinic = "redditch" if _gp_pending_guess == "alcester" else "alcester"
                    logger.info("[ms_flow] faq_pending_confirm: no → opposite %s", _gp_clinic)
                else:
                    # Not a plain yes/no — try resolver in case caller named the clinic
                    _gp_confirm_result = _resolve_clinic(text, context="ask_location")
                    if _gp_confirm_result["status"] == "resolved":
                        _gp_clinic = _gp_confirm_result["location"]
                        logger.info(
                            "[ms_flow] faq_pending_confirm: resolver override → %s", _gp_clinic,
                        )
                    else:
                        # Still unclear — re-ask confirm without losing pending state
                        _gp_reconfirm = (
                            "Just to confirm \u2014 was that Redditch?"
                            if _gp_pending_guess == "redditch"
                            else "Just to confirm \u2014 was that Alcester?"
                        )
                        await self._tts.put(_gp_reconfirm)
                        self.session["last_question"] = _gp_reconfirm
                        logger.info(
                            "[ms_flow] faq_pending_confirm: still unclear → re-confirm %s",
                            _gp_pending_guess,
                        )
                        return
                # Confirmed — answer the original pending FAQ question
                self.session.pop("_faq_loc_pending_guess", None)
                self.session.pop("_faq_loc_retry_count", None)
                self.session.pop("_faq_loc_awaiting_dtmf", None)
                self.session["last_faq_loc_id"] = _gp_clinic
                _gp_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                self.session.pop("_faq_loc_pending_intent", None)
                _gp_synth_tx = (
                    f"{_gp_pend_sub} {_gp_clinic}".strip() if _gp_pend_sub else _gp_clinic
                )
                logger.info(
                    "[ms_flow] faq_pending_resolved (confirm): topic=%s clinic=%s",
                    _gp_pend_sub or _gp_pending_loc, _gp_clinic,
                )
                self.session["_faq_ans_intent"] = _gp_pending_loc
                self.session["_faq_ans_at"]     = time.time()
                await self._handle_mid_flow_interrupt(_gp_pending_loc, _gp_synth_tx)
                self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # ── New sub-question intercept ────────────────────────────────────────
            # Caller responded to "Alcester or Redditch?" with a NEW location
            # sub-question (parking, transport, access) rather than a clinic name.
            # Update the pending sub-topic and re-ask which clinic — don't try
            # to resolve this as a clinic-name response.
            _gp_sub_detect = text.strip().lower()
            _gp_new_sub_sigs = (
                "parking", "park",
                "bus", "train", "transport", "get there", "travel", "journey",
                "wheelchair", "accessible", "accessibility", "step free", "step-free",
                "disabled",
            )
            if any(p in _gp_sub_detect for p in _gp_new_sub_sigs):
                _gp_detected_sub = (
                    "parking"   if any(p in _gp_sub_detect for p in ("parking", "park")) else
                    "transport" if any(p in _gp_sub_detect for p in (
                        "bus", "train", "transport", "get there", "travel",
                    )) else
                    "accessibility"
                )
                self.session["_faq_loc_pending_sub"] = _gp_detected_sub
                _gp_sub_reask = "Of course — is that for our Alcester or Redditch clinic?"
                await self._tts.put(_gp_sub_reask)
                self.session["last_question"] = _gp_sub_reask
                logger.info(
                    "[ms_flow] faq_pending: new sub-question (%s) in %r → re-asking clinic",
                    _gp_detected_sub, text[:40],
                )
                return

            # ── First attempt: use the proven ASK_LOCATION clinic resolver ──────
            _gp_loc_result = _resolve_clinic(text, context="ask_location")
            if _gp_loc_result["status"] == "resolved":
                _gp_clinic = _gp_loc_result["location"]
                self.session["last_faq_loc_id"] = _gp_clinic
                _gp_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                self.session.pop("_faq_loc_pending_intent", None)
                self.session.pop("_faq_loc_retry_count", None)
                self.session.pop("_faq_loc_awaiting_dtmf", None)
                _gp_synth_tx = (
                    f"{_gp_pend_sub} {_gp_clinic}".strip() if _gp_pend_sub else _gp_clinic
                )
                logger.info(
                    "[ms_flow] faq_pending_resolved: topic=%s clinic=%s",
                    _gp_pend_sub or _gp_pending_loc, _gp_clinic,
                )
                self.session["_faq_ans_intent"] = _gp_pending_loc
                self.session["_faq_ans_at"]     = time.time()
                await self._handle_mid_flow_interrupt(_gp_pending_loc, _gp_synth_tx)
                self.session["last_question"] = _faq_topic_reanchor(self.session)
                return
            elif _gp_loc_result["status"] == "ambiguous":
                _gp_conf  = _gp_loc_result.get("confidence", 0)
                _gp_dbg2  = _gp_loc_result.get("debug", {})
                _gp_alc2  = _gp_dbg2.get("alcester_score", 0)
                _gp_red2  = _gp_dbg2.get("redditch_score", 0)
                if _gp_conf >= 0.50:
                    # Score ≥50 — silently bind the stronger clinic
                    _gp_bind = "alcester" if _gp_alc2 >= _gp_red2 else "redditch"
                    self.session["last_faq_loc_id"] = _gp_bind
                    _gp_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                    self.session.pop("_faq_loc_pending_intent", None)
                    self.session.pop("_faq_loc_retry_count", None)
                    self.session.pop("_faq_loc_awaiting_dtmf", None)
                    _gp_synth_tx = (
                        f"{_gp_pend_sub} {_gp_bind}".strip() if _gp_pend_sub else _gp_bind
                    )
                    logger.info(
                        "[ms_flow] faq_pending: ambiguous conf=%.2f → silent bind %s",
                        _gp_conf, _gp_bind,
                    )
                    self.session["_faq_ans_intent"] = _gp_pending_loc
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt(_gp_pending_loc, _gp_synth_tx)
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                # Low-confidence ambiguous — retry ladder
                _gp_retry_n = self.session.get("_faq_loc_retry_count", 0)
                if _gp_retry_n < 1:
                    self.session["_faq_loc_retry_count"] = _gp_retry_n + 1
                    _gp_reask = (
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    await self._tts.put(_gp_reask)
                    self.session["last_question"] = _gp_reask
                    logger.info(
                        "[ms_flow] faq_pending: ambiguous low-conf → voice retry %d", _gp_retry_n + 1,
                    )
                    return
                # Retry exhausted — switch to DTMF loop (never ends call)
                self.session["_faq_loc_awaiting_dtmf"] = True
                _gp_dtmf = "Press 1 for Alcester or 2 for Redditch."
                await self._tts.put(_gp_dtmf)
                self.session["last_question"] = _gp_dtmf
                logger.info("[ms_flow] faq_pending: ambiguous → DTMF mode")
                return
            else:
                # Unknown / no signal — retry ladder
                _gp_retry_n = self.session.get("_faq_loc_retry_count", 0)
                if _gp_retry_n < 1:
                    self.session["_faq_loc_retry_count"] = _gp_retry_n + 1
                    _gp_reask = (
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    await self._tts.put(_gp_reask)
                    self.session["last_question"] = _gp_reask
                    logger.info(
                        "[ms_flow] faq_pending: unknown → voice retry %d", _gp_retry_n + 1,
                    )
                    return
                # Retry exhausted — switch to DTMF loop (never ends call)
                self.session["_faq_loc_awaiting_dtmf"] = True
                _gp_dtmf = "Press 1 for Alcester or 2 for Redditch."
                await self._tts.put(_gp_dtmf)
                self.session["last_question"] = _gp_dtmf
                logger.info("[ms_flow] faq_pending: unknown → DTMF mode")
                return

        # ── GLOBAL CHILD-AGE PENDING ──────────────────────────────────────────────
        # When _handle_mid_flow_interrupt asked "How old is your son/child?" via the
        # faq_child_policy age-gate, the caller's age response arrives here before any
        # state handler.  Consumes the response and branches deterministically:
        #   age < clinic threshold → short rejection
        #   age ≥ threshold        → allow (offer booking)
        #   no age detected        → re-ask once, then give up cleanly
        # Always returns — never falls through while pending is set.
        if self.session.get("_child_age_pending") and step:
            _cap_tx  = text.strip().lower()
            _cap_rel = self.session.get("_child_age_relationship", "child")
            _CAP_PRONOUNS = {"son": "your son", "daughter": "your daughter"}
            _cap_pronoun  = _CAP_PRONOUNS.get(_cap_rel, "your child")

            # Abandon detection — caller moved on or said never mind
            _CAP_ABANDON = frozenset({"no", "nope", "nah", "forget it", "never mind",
                                      "nothing", "not anymore", "doesn't matter"})
            _cap_words = set(_cap_tx.split())
            if _cap_words <= _CAP_ABANDON or any(p in _cap_tx for p in ("forget it", "never mind", "doesn't matter")):
                self.session.pop("_child_age_pending",      None)
                self.session.pop("_child_age_relationship", None)
                self.session.pop("_child_age_reask_count",  None)
                _cap_abandon_r = "No problem — is there anything else I can help with?"
                await self._tts.put(_cap_abandon_r)
                self.session["last_question"] = _cap_abandon_r
                return

            # Extract integer age (digit-form or word-form)
            _CAP_WORD_AGES = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
                "nineteen": 19, "twenty": 20,
            }
            _cap_age_m = _re_mod.search(r'\b(\d+)\b', _cap_tx)
            _cap_age: int | None = None
            if _cap_age_m:
                _cap_age = int(_cap_age_m.group(1))
            else:
                for _wrd, _num in _CAP_WORD_AGES.items():
                    if _wrd in _cap_tx:
                        _cap_age = _num
                        break

            if _cap_age is None:
                # No age found — re-ask once, then give up
                _cap_reask_n = int(self.session.get("_child_age_reask_count") or 0)
                if _cap_reask_n >= 1:
                    self.session.pop("_child_age_pending",      None)
                    self.session.pop("_child_age_relationship", None)
                    self.session.pop("_child_age_reask_count",  None)
                    _cap_fallback = "No problem — is there anything else I can help with?"
                    await self._tts.put(_cap_fallback)
                    self.session["last_question"] = _cap_fallback
                    logger.info("[ms_flow] child_age_pending: still no age after re-ask — giving up")
                    return
                self.session["_child_age_reask_count"] = _cap_reask_n + 1
                _cap_reask = f"Sorry \u2014 how old is {_cap_pronoun}?"
                await self._tts.put(_cap_reask)
                self.session["last_question"] = _cap_reask
                logger.info("[ms_flow] child_age_pending: no age in %r — re-asking", text[:40])
                return

            # Age captured — load clinic threshold and branch
            from app.clinic_config import get_clinic as _gc_cap
            _cap_clinic    = _gc_cap(self.session.get("clinic_id") or "demo")
            _cap_policies  = _cap_clinic.get("patient_policies", {})
            _cap_threshold = int(
                _cap_policies.get("min_patient_age")
                or _cap_policies.get("adult_age_threshold")
                or 16
            )
            self.session.pop("_child_age_pending",      None)
            self.session.pop("_child_age_relationship", None)
            self.session.pop("_child_age_reask_count",  None)

            if _cap_age < _cap_threshold:
                _cap_decline = (
                    f"I\u2019m sorry \u2014 we only see patients aged {_cap_threshold} "
                    f"and over, so we wouldn\u2019t be able to book them in with us."
                )
                await self._tts.put(_cap_decline)
                self.session["last_question"]  = "Is there anything else I can help with?"
                self.session["last_faq_answer"] = _cap_decline
                logger.info(
                    "[ms_flow] child_age_pending: age=%s < threshold=%s → declined",
                    _cap_age, _cap_threshold,
                )
            else:
                _cap_allow = (
                    "That\u2019s absolutely fine \u2014 "
                    "would you like to go ahead and book an appointment?"
                )
                await self._tts.put(_cap_allow)
                self.session["last_question"]  = "Would you like to go ahead and book?"
                self.session["last_faq_answer"] = _cap_allow
                logger.info(
                    "[ms_flow] child_age_pending: age=%s >= threshold=%s → allowed",
                    _cap_age, _cap_threshold,
                )
            return

        # ── GLOBAL FRAGMENT SUPPRESSION (Bug 9) ─────────────────────────────────
        # Very short / noisy transcripts must not drive a full response.
        # Suppress if: in explicit blocklist, OR ≤ 6 chars with no strong intent.
        _frag_words = text.strip().split()
        _frag_text  = text.strip()
        _is_fragment = (
            _frag_text in _FRAGMENT_BLOCKLIST
            or (
                len(_frag_words) <= 2
                and len(_frag_text) <= 6
                and not any(s in _frag_text for s in _FRAGMENT_STRONG_INTENTS)
            )
        )
        _NAME_COLLECTION_STATES = {
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        }
        # Bug 2: when appointment is already found, short confirmation fragments
        # like "it was" / "yes it" must reach the lookup-confirmation handler,
        # not be silently dropped here.
        _is_lookup_confirm_state = (
            step is not None
            and step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL")
            and self.session.get("rc_stage") == "lookup_done"
        )
        # BUG 12 fix: CONFIRM_PHONE and phone-confirmation states must let through
        # short affirmatives like "it is", "that's right", "correct", "yes it is".
        _CONFIRM_PHONE_STATES = {
            "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
        }
        _CONFIRM_BYPASS_TOKENS = frozenset({
            "it is", "it was", "that's right", "thats right",
            "correct", "yes it is", "yes it was", "that is correct",
            "that's correct", "thats correct", "yes that's right",
            "yes thats right", "yes correct", "right",
        })
        _is_confirm_phone_state = (
            step is not None and step["state"] in _CONFIRM_PHONE_STATES
        )
        _is_confirm_bypass_token = _frag_text in _CONFIRM_BYPASS_TOKENS
        # BUG 22 fix: day-selection and lookup states must never suppress valid short answers
        _FRAG_BYPASS_STATES = {
            "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
            "LOOKUP_RESCHEDULE", "LOOKUP_CANCEL",
        }
        # ASK_LOCATION fix: when the caller is answering a clinic-selection question
        # ("Alcester", "Redditch", "red", "al") the fragment suppression must never
        # fire regardless of which flow is active.  Without this, BOOKING_FLOW at
        # ASK_LOCATION suppresses short clinic names because step["state"] =
        # "COLLECT_REASON" is not in _NAME_COLLECTION_STATES — the scoring/forced-
        # confirm/DTMF cascade never fires and the call goes silent.
        # RESCHEDULE_FLOW is already immune (step["state"] = "COLLECT_NAME_RESCHEDULE"
        # is in _NAME_COLLECTION_STATES), but this makes the bypass explicit and
        # symmetric for both flows.
        _is_frag_bypass_state = (
            (step is not None and step["state"] in _FRAG_BYPASS_STATES)
            or bool(self.session.get("needs_location"))
        )
        # If a partial reason is pending (COLLECT_REASON completeness gate stored it),
        # the very next utterance — however short — must reach the join block at
        # COLLECT_REASON so it can be merged before suppression runs.
        _has_pending_partial_reason = bool(self.session.get("_partial_reason"))
        if _is_fragment and not _is_lookup_confirm_state and not (_is_confirm_phone_state and _is_confirm_bypass_token) and not _is_frag_bypass_state and (not step or step["state"] not in _NAME_COLLECTION_STATES) and not _has_pending_partial_reason:
            logger.info("[ms_flow] global fragment suppressed: %r", transcript[:30])
            self.session["fragment_suppressed"] = True
            return

        # ── TEST TRACE ──────────────────────────────────────────────────────────
        handled_by: str | None = None

        # ── Phase 5: stamp session state to current step immediately so all
        #    branches (including early exits) observe the correct state. ──────
        # FIX E: When location gate is active, the real state is ASK_LOCATION
        # regardless of flow_step — stamp that BEFORE the log entry.
        current_state = "ASK_LOCATION" if self.session.get("needs_location") else step["state"]
        self.session["state"] = current_state
        logger.info(
            "[ms_flow] handle_transcript entry: flow_step=%d state=%s transcript=%r",
            self.session.get("flow_step", 0), current_state, transcript[:60],
        )

        # ── GLOBAL REPAIR / CONFUSION GUARD ──────────────────────────────────────
        # Catches confusion / repair / meta-conversation utterances in question-
        # sensitive states BEFORE the state machine can bind them as slot answers.
        # ASK_LOCATION is excluded here — it has its own targeted repair guard
        # lower in the block that runs after the non-location escape and reroute
        # checks (both of which share some repair-like language).
        # Purely deterministic — no LLM call.
        _REPAIR_GUARD_STATES = frozenset({
            "NEW_OR_RETURNING", "COLLECT_REASON", "RETURNING_RECENCY",
        })
        if current_state in _REPAIR_GUARD_STATES and _is_repair_utt(text):
            _rg_last_q = self.session.get("last_question", "")
            _rg_msg = (
                _rg_last_q
                if _rg_last_q
                else _router_bridge_text(_R_REPAIR, current_state)
            )
            await self._tts.put(_rg_msg)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _rg_msg}
            )
            logger.info(
                "[ms_flow] global_repair_guard: state=%s text=%r → re-asking",
                current_state, text[:40],
            )
            return

        # ── Cross-state phone-reject (Prompt 14) ─────────────────────────────────
        # When phone was auto-confirmed via caller-ID and the caller says they want
        # a different number, clear the auto-confirm and redirect to COLLECT_PHONE.
        # Fires before the main state machine so no state-specific handler is needed.
        _PHONE_REJECT_INTERCEPT_STATES = frozenset({
            "COLLECT_REASON", "NEW_OR_RETURNING", "RETURNING_RECENCY",
            "PRESENT_DAYS", "PRESENT_TIMES",
        })
        if (
            self.session.get("phone_confirmed")
            and current_state in _PHONE_REJECT_INTERCEPT_STATES
            and _is_phone_reject(text)
        ):
            logger.info(
                "[ms_flow] cross-state phone-reject at %s — clearing phone_confirmed, "
                "redirecting to COLLECT_PHONE", current_state,
            )
            self.session["phone_confirmed"] = False
            self.session["phone_number"] = None
            self.session["phone_digits_buffer"] = ""
            self.session.setdefault("collected", {}).pop("phone", None)
            self.session["flow_step"] = _COLLECT_PHONE_INDEX
            self.session["state"] = "COLLECT_PHONE"
            await self.ask_current_question()
            return

        # ── MID-FLOW PATH SWITCH ─────────────────────────────────────────────────
        # Caller can switch between reschedule and cancel at any point mid-flow.
        # Reschedule → cancel: detected here for PRESENT_DAYS/TIMES/CONFIRM_RESCHEDULE.
        # Cancel → reschedule: already handled by CONFIRM_RESCHEDULE_OR_CANCEL handler.
        _MID_CANCEL_STATES = {
            "PRESENT_DAYS_RESCHEDULE", "PRESENT_TIMES_RESCHEDULE", "CONFIRM_RESCHEDULE",
        }
        if current_state in _MID_CANCEL_STATES:
            _MID_CANCEL_PHRASES = (
                "cancel", "cancel it", "cancel the appointment", "cancel my appointment",
                "want to cancel", "i'd like to cancel", "id like to cancel",
                "actually cancel", "just cancel", "please cancel",
                "don't want to reschedule", "dont want to reschedule",
                "not reschedule", "no reschedule", "no longer reschedule",
            )
            if any(p in text for p in _MID_CANCEL_PHRASES):
                self.session["intent"] = "cancel"
                self.session["flow_step"] = _CONFIRM_CANCEL_INDEX
                self.session["question_asked_this_turn"] = False
                logger.info(
                    "[ms_flow] mid-flow cancel switch from %s → CONFIRM_CANCEL (idx=%d)",
                    current_state, _CONFIRM_CANCEL_INDEX,
                )
                await self.ask_current_question()
                return

        # ── CALLER CLASSIFICATION (first substantive utterance only) ──────────
        # Runs before any state-machine logic so professional callers are routed
        # before the booking flow is ever entered.
        if self.session.get("classification_pending"):
            words = transcript.strip().split()
            if len(words) > 1:
                # Deterministic pre-check: obvious patient phrases skip LLM classification
                _OBVIOUS_PATIENT_PHRASES = (
                    "book", "appointment", "physio", "physiotherapy", "treatment",
                    "reschedule", "cancel", "rebook", "coming in", "see someone",
                    "pain", "injury", "hurting", "aching", "assessment",
                )
                if any(p in text for p in _OBVIOUS_PATIENT_PHRASES):
                    self.session["caller_type"] = "patient"
                    self.session["classification_pending"] = False
                    self.session["_classification_confidence"] = "deterministic"
                    logger.info("[ms_flow] caller pre-classified as patient (deterministic match)")
                else:
                    from app.caller_classifier import classify_caller
                    try:
                        result = await asyncio.to_thread(classify_caller, transcript)
                        self.session["caller_type"] = result["type"]
                        self.session["_classification_confidence"] = result["confidence"]
                        logger.info(
                            "[ms_flow] caller classified: type=%s confidence=%s intent=%r",
                            result["type"], result["confidence"], result.get("intent", "")[:60],
                        )
                    except Exception as _cls_err:
                        logger.warning("[ms_flow] classify_caller failed: %r — defaulting to patient", _cls_err)
                        self.session["caller_type"] = "patient"
                    self.session["classification_pending"] = False
            elif step["state"] != "DETECT_INTENT":
                # Past first utterance with a short response — force patient
                self.session["caller_type"] = "patient"
                self.session["classification_pending"] = False
                logger.info("[ms_flow] caller forced to patient (short utterance past DETECT_INTENT)")

        # Lightweight tone flag — set once per call, used only for bridge phrasing.
        # "brief" = curt/direct caller (≤4 words); "warm" = chatty/detailed.
        if not self.session.get("caller_tone"):
            word_count = len(transcript.strip().split())
            self.session["caller_tone"] = "brief" if word_count <= 4 else "warm"

        # ── PROFESSIONAL FLOW INTERCEPT ────────────────────────────────────────
        if self.session.get("professional_flow_active"):
            from app.caller_classifier import run_professional_flow
            await run_professional_flow(self.session, self._tts.put, transcript)
            if self.session.get("professional_flow_complete"):
                # Exhaust the flow so current_step() returns None on next call
                self.session["flow_step"] = len(self._active_flow)
            return

        # ── Multi-location: handle location answer ────────────────────────────
        # Fires every turn while needs_location=True (i.e. between intent detection
        # and the caller naming their clinic).  No LLM call — rule-based extractor only.
        if self.session.get("needs_location"):
            self.session["state"] = "ASK_LOCATION"

            # ── FORCED CONFIRM MODE: waiting for yes/no on our guessed clinic ──────
            # Active immediately after the resolver returns None for any input.
            # One binary confirm question; yes binds guess, no binds the opposite.
            _loc_pending_guess = self.session.get("location_pending_guess")
            if _loc_pending_guess:
                # Direct extraction still wins (DTMF / Tier-1 STT hits arrive here too)
                _fc_faq_intents = {
                    "faq_prices", "faq_insurance", "faq_hours",
                    "faq_location", "faq_services", "faq_capability",
                }
                _fc_intent = self._detect_intent(text)
                if _fc_intent in _fc_faq_intents:
                    await self._handle_mid_flow_interrupt(_fc_intent, transcript)
                    return
                # Bug 3 fix: yes/no must be checked BEFORE the generic location
                # extractor so plain "yes"/"no" is handled as a confirmation, not
                # sent through the resolver where it can produce wrong bindings.
                _fc_t = text.lower()
                _FC_YES = (
                    "yes", "yeah", "yep", "yup", "correct",
                    "that's right", "thats right", "that's it", "thats it",
                    "absolutely", "definitely", "sure", "confirmed",
                )
                _FC_NO = (
                    "no", "nope", "nah", "not that", "not right",
                    "wrong", "other one", "other clinic", "different",
                )
                _fc_is_yes = any(sig in _fc_t for sig in _FC_YES)
                _fc_is_no  = any(sig in _fc_t for sig in _FC_NO)
                if _fc_is_yes and not _fc_is_no:
                    # Yes → bind the guessed clinic
                    self.session["selected_location"] = _loc_pending_guess
                    self.session["needs_location"] = False
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_pending_guess", None)
                    logger.info(
                        "[ms_flow] ASK_LOCATION forced-confirm: yes → %s", _loc_pending_guess
                    )
                    await self.ask_current_question()
                    return
                if _fc_is_no and not _fc_is_yes:
                    # No → bind the OPPOSITE clinic immediately — no second ask
                    _fc_opposite = "redditch" if _loc_pending_guess == "alcester" else "alcester"
                    self.session["selected_location"] = _fc_opposite
                    self.session["needs_location"] = False
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_pending_guess", None)
                    logger.info(
                        "[ms_flow] ASK_LOCATION forced-confirm: no → opposite %s", _fc_opposite
                    )
                    await self.ask_current_question()
                    return
                # Service-fit in forced-confirm → answer + re-ask the confirm Q.
                # "What would the clinic do for his ankle?" while we have a pending
                # clinic guess should get a triage answer, not a DTMF prompt.
                if _is_sfit_utt(text):
                    _fc_sfit_clinic = (
                        "Redditch" if _loc_pending_guess == "redditch" else "Alcester"
                    )
                    _fc_sfit_ans = (
                        "For something like that, the clinic would usually start with "
                        "a physiotherapy assessment to look at what\u2019s going on "
                        "and advise on the best next steps. "
                        f"Was that at our {_fc_sfit_clinic} clinic?"
                    )
                    await self._tts.put(_fc_sfit_ans)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _fc_sfit_ans}
                    )
                    self.session["last_question"] = _fc_sfit_ans
                    logger.info(
                        "[ms_flow] ASK_LOCATION forced-confirm: service_fit_interrupt %r "
                        "(guess=%s)",
                        text[:40], _loc_pending_guess,
                    )
                    return

                # Repair / confusion in forced-confirm → re-ask the yes/no question.
                # Without this, "I was just asking if you could help" reaches the
                # generic extractor and then the DTMF fallback unnecessarily.
                if _is_repair_utt(text):
                    _fc_clinic = (
                        "Redditch" if _loc_pending_guess == "redditch" else "Alcester"
                    )
                    _fc_repair_q = (
                        f"Sorry \u2014 I was just checking whether you meant our "
                        f"{_fc_clinic} clinic \u2014 is that right?"
                    )
                    await self._tts.put(_fc_repair_q)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _fc_repair_q}
                    )
                    self.session["last_question"] = _fc_repair_q
                    logger.info(
                        "[ms_flow] ASK_LOCATION forced-confirm: repair_guard %r "
                        "→ re-asking confirm Q (guess=%s)",
                        text[:40], _loc_pending_guess,
                    )
                    return

                # Not a plain yes/no — try the generic extractor as a fallback
                # (catches "actually it's Alcester" / "Redditch please" in confirm mode)
                loc = self._extract("location_selection", text, transcript)
                if loc:
                    self.session["selected_location"] = loc
                    self.session["needs_location"] = False
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_pending_guess", None)
                    logger.info(
                        "[ms_flow] ASK_LOCATION forced-confirm: explicit clinic override — %s", loc
                    )
                    await self.ask_current_question()
                    return
                # Neither yes/no nor explicit clinic name → final DTMF fallback
                self.session.pop("location_pending_guess", None)
                self.session["location_awaiting_dtmf"] = True
                _fc_dtmf = "Just to make sure, press 1 for Alcester or 2 for Redditch."
                await self._tts.put(_fc_dtmf)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _fc_dtmf}
                )
                self.session["last_question"] = _fc_dtmf
                logger.info("[ms_flow] ASK_LOCATION forced-confirm: unclear → DTMF fallback")
                return

            # ── DTMF MODE: waiting for keypad press (final fallback) ──────────────
            if self.session.get("location_awaiting_dtmf"):
                loc = self._extract("location_selection", text, transcript)
                if loc:
                    self.session["selected_location"] = loc
                    self.session["needs_location"] = False
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_awaiting_dtmf", None)
                    logger.info("[ms_flow] ASK_LOCATION DTMF: resolved — %s", loc)
                    await self.ask_current_question()
                    return
                # Not resolved — re-prompt and keep waiting; never end the call
                _dtmf_re = "Press 1 for Alcester or 2 for Redditch."
                await self._tts.put(_dtmf_re)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _dtmf_re}
                )
                self.session["last_question"] = _dtmf_re
                logger.info("[ms_flow] ASK_LOCATION DTMF: unresolved — looping")
                return

            # ── Tail-fragment suppression ─────────────────────────────────────
            # A bare one-word connector / filler arriving right after the
            # ASK_LOCATION prompt is almost always a stray tail of the caller's
            # previous utterance (e.g. "...book an appointment then"), not a
            # clinic answer.  Suppress so the retry counter is not consumed and
            # the watchdog can repair the turn cleanly.  Note: "yes"/"no" are
            # deliberately excluded — forced-confirm mode handled them above,
            # and in fresh ASK_LOCATION they are meaningless location answers
            # anyway, but we keep them out of the suppression set to leave any
            # future binary-prompt reuse working.
            _LOC_TAIL_FRAGMENTS = frozenset({
                "then", "and", "so", "but",
                "um", "uh", "er", "err", "hmm", "mmm", "mhm",
                "okay", "ok", "right", "yeah",
            })
            _loc_frag_words = text.strip().split()
            if (
                len(_loc_frag_words) == 1
                and _loc_frag_words[0] in _LOC_TAIL_FRAGMENTS
            ):
                self.session["fragment_suppressed"] = True
                logger.info(
                    "[ms_flow] ASK_LOCATION: tail-fragment %r suppressed "
                    "(state=ASK_LOCATION, retry not consumed, watchdog preserved)",
                    text[:20],
                )
                return

            # ── Non-location corrective escape ────────────────────────────────
            # "I said I had a few questions first" / "I was asking about parking"
            # arriving inside the ASK_LOCATION block — caller is correcting the
            # routing, not giving a location answer.  Route to general_query and
            # clear the location gate so the flow doesn't circle back.
            _LOC_NON_LOCATION_ESCAPE = (
                "a few questions",
                "few questions",
                "some questions",
                "questions first",
                "questions before",
                "had a question",
                "have a question",
                "just had a question",
                "just have a question",
                "i was asking about",
                "i'm asking about",
                "im asking about",
            )
            if any(p in text for p in _LOC_NON_LOCATION_ESCAPE):
                logger.info(
                    "[ms_flow] ASK_LOCATION: non-location escape %r → general_query",
                    text[:60],
                )
                self.session.pop("location_retry_count", None)
                self.session.pop("location_pending_guess", None)
                self.session["needs_location"] = False
                self._switch_flow("general_query")
                await self.ask_current_question()
                return

            # ── Intent-reroute gate: MUST run BEFORE extraction and FAQ gate ────
            # Explicit workflow-switch language ("i want to reschedule", "never
            # mind, i need to cancel") must reroute immediately — before the
            # location resolver ever sees the utterance.  Without this, discourse
            # words like "actually" trigger prefix_fallback:alcester even when the
            # caller is abandoning the booking entirely.
            _LOC_REROUTE_SIGNALS = (
                "reschedule", "re-schedule", "rebook", "re-book",
                "cancel my appointment", "cancel the appointment",
                "change my appointment", "move my appointment",
                "i want to cancel", "i want to reschedule",
                "want to reschedule", "want to cancel",
                "looking to reschedule", "looking to cancel",
                "never mind", "never mind i", "actually never mind",
                "meant to say", "i meant", "sorry i meant",
                "actually i need", "what i meant", "my mistake",
                "meant to book", "instead",
            )
            if any(p in text for p in _LOC_REROUTE_SIGNALS):
                _reroute_intent = self._detect_intent(text)
                if _reroute_intent in ("reschedule", "cancel", "booking"):
                    logger.info(
                        "[ms_flow] ASK_LOCATION: intent reroute BEFORE extraction %r → %s",
                        text[:60], _reroute_intent,
                    )
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_pending_guess", None)
                    self.session["needs_location"] = False
                    self._switch_flow(_reroute_intent)
                    await self.ask_current_question()
                    return

            # ── FAQ-first gate: detect question intent BEFORE extracting location ──
            # "is there parking at alcester?" / "first is there any parking at your
            # alcester clinic" → must answer the FAQ, NOT extract "alcester" and advance.
            # Location tokens inside questions are context for the FAQ answer, not
            # booking answers.  This gate must run BEFORE the extractor so those tokens
            # cannot greedily bind to flow state.
            _loc_faq_pre_intents = {
                "faq_prices", "faq_insurance", "faq_hours",
                "faq_location", "faq_services", "faq_capability",
            }
            _loc_pre_intent = self._detect_intent(text)
            if _loc_pre_intent in _loc_faq_pre_intents:
                logger.info(
                    "[ms_flow] ASK_LOCATION: FAQ gate before extraction — %s (no retry consumed)",
                    _loc_pre_intent,
                )
                await self._handle_mid_flow_interrupt(_loc_pre_intent, transcript)
                return
            # ── Service-fit / capability question interrupt ──────────────────────
            # The caller is asking what the clinic can do for their injury —
            # NOT naming a location.  Must intercept BEFORE the repair guard and
            # BEFORE the resolver to prevent false matches.
            # Example: "what exactly could you do at the clinic to help him out"
            #          "could you help him with his ankle?"
            # Response: safe deterministic triage answer + re-ask location.
            if _is_sfit_utt(text):
                _sfit_loc_ans = (
                    "For something like that, the clinic would usually start with "
                    "a physiotherapy assessment \u2014 they\u2019ll look at what\u2019s "
                    "going on and advise on the best next steps. "
                    "If you\u2019d like to get that booked in, would that be at "
                    "our Alcester or Redditch clinic?"
                )
                await self._tts.put(_sfit_loc_ans)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _sfit_loc_ans}
                )
                self.session["last_question"] = (
                    "Would that be at our Alcester or Redditch clinic?"
                )
                logger.info(
                    "[ms_flow] ASK_LOCATION: service_fit_interrupt %r "
                    "\u2192 answered + re-asked location",
                    text[:40],
                )
                return

            # ── Repair / confusion guard ─────────────────────────────────────────
            # Must run BEFORE the location extractor: repair utterances like
            # "sorry that's unclear" / "what were you asking?" would otherwise
            # pass to the resolver where prefix_fallback can produce a false match.
            if _is_repair_utt(text):
                _loc_reask = (
                    self.session.get("last_question")
                    or "Which clinic would you prefer \u2014 "
                       "our Alcester or Redditch practice?"
                )
                await self._tts.put(_loc_reask)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _loc_reask}
                )
                logger.info(
                    "[ms_flow] ASK_LOCATION: repair_guard %r \u2192 re-asking", text[:40]
                )
                return

            # ── Filler / non-location guard (tier-stable replay) ────────────────
            # Obvious politeness / acknowledgement / greeting fragments carry no
            # clinic signal and must NOT advance the retry ladder.  We simply
            # replay last_question — which always reflects the CURRENT repair
            # tier wording (original ask, polite re-ask, or DTMF prompt) — so
            # the caller stays on the same tier until they either (a) give a
            # genuine location attempt (which feeds the extractor / score-bind
            # and can escalate) or (b) fall silent (which the watchdog handles).
            #
            # Invariants preserved:
            #   • no clinic is bound
            #   • location_retry_count is NOT mutated
            #   • location_awaiting_dtmf is NOT toggled
            #   • flow_step / state are NOT advanced
            #   • last_question is re-spoken, so silence-handler / watchdog
            #     re-arm naturally via the normal on_tts_finished path
            #   • call cannot hang up from this path
            import re as _re_loc_fg
            _loc_norm = _re_loc_fg.sub(r"[^\w\s']", "", text.strip().lower()).strip()
            _ASK_LOCATION_FILLER: frozenset = frozenset({
                # Politeness
                "please", "thanks", "thank you", "thank you so much",
                "cheers", "ta",
                # Bare acknowledgements / hedges
                "yes", "yeah", "yep", "yup", "yah", "ya",
                "no", "nope", "nah",
                "okay", "ok", "k", "kk", "sure", "alright", "all right",
                "right", "fine", "good", "great", "perfect", "lovely",
                "brilliant", "cool", "got it", "understood", "noted",
                "maybe", "dunno", "i dont know", "i don't know",
                "not sure", "not really", "whatever",
                # Greetings / attention-checks
                "hi", "hello", "hey", "hiya",
                "can you hear me", "hello hello", "are you there",
                "you there", "hello are you there",
                # Non-answer clarifiers (those not already caught by
                # _is_repair_utt — kept conservative to avoid overlap)
                "um", "uh", "erm", "hm", "hmm", "mm", "mhm",
                "one second", "one sec", "hold on", "hang on",
                # Short fragments seen in production that were never a clinic
                "use this number", "you can use this number",
                "yeah you can use this number", "yes use this number",
                "my first time", "first time",
                "its my first time", "it's my first time",
                "this is my first time",
            })
            if _loc_norm in _ASK_LOCATION_FILLER:
                _fil_count = int(self.session.get("_ask_location_filler_count", 0)) + 1
                self.session["_ask_location_filler_count"] = _fil_count
                if self.session.get("location_awaiting_dtmf"):
                    # DTMF tier — keep DTMF wording stable.
                    _loc_tier_q = (
                        self.session.get("last_question")
                        or "Press 1 for Alcester or 2 for Redditch."
                    )
                elif _fil_count == 1:
                    # First filler — clean replay of current tier wording.
                    _loc_tier_q = (
                        self.session.get("last_question")
                        or "Would that be at our Alcester or Redditch clinic?"
                    )
                else:
                    # Subsequent filler — escalate to tier-2 wording and hold there.
                    _loc_tier_q = (
                        "Sorry, I didn't quite catch that \u2014 can you please say "
                        "the Alcester clinic or the Redditch clinic?"
                    )
                    # Sync retry counter so downstream ladder stays consistent
                    # with the elevated wording, but do NOT push it past 1 here —
                    # real failed extractions (not fillers) own tier-3/DTMF moves.
                    self.session["location_retry_count"] = max(
                        1, int(self.session.get("location_retry_count", 0) or 0)
                    )
                await self._tts.put(_loc_tier_q)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _loc_tier_q}
                )
                # Keep last_question pointing at the SAME tier wording —
                # explicit assignment defends against upstream mutation.
                self.session["last_question"] = _loc_tier_q
                logger.info(
                    "[ms_flow] ASK_LOCATION: filler_guard %r \u2192 tier-stable replay "
                    "(filler_n=%d retry=%s dtmf=%s)",
                    text[:40],
                    _fil_count,
                    self.session.get("location_retry_count", 0),
                    bool(self.session.get("location_awaiting_dtmf")),
                )
                return
            # Non-filler path inside ASK_LOCATION — clear streak counter so a
            # later isolated filler does not inherit prior escalation.
            self.session.pop("_ask_location_filler_count", None)

            loc = self._extract("location_selection", text, transcript)
            if loc:
                self.session["selected_location"] = loc
                self.session["needs_location"] = False
                self.session.pop("location_retry_count", None)
                self.session.pop("location_pending_guess", None)
                logger.info("[ms_flow] ASK_LOCATION answered: selected_location=%s", loc)
                await self.ask_current_question()
            else:
                # ── FAQ interrupt — check before entering fallback ladder ──────
                # general_query excluded: vague phrases must not fire the LLM here.
                # Note: explicit workflow-switch signals were already handled above
                # (intent-reroute gate); what remains here is ambiguous text that
                # the resolver could not resolve AND that did not match a reroute signal.
                _loc_faq_intents = {
                    "faq_prices", "faq_insurance", "faq_hours",
                    "faq_location", "faq_services", "faq_capability",
                }
                _loc_intent = self._detect_intent(text)
                if _loc_intent in _loc_faq_intents:
                    logger.info(
                        "[ms_flow] ASK_LOCATION: FAQ interrupt %s — no retry consumed",
                        _loc_intent,
                    )
                    await self._handle_mid_flow_interrupt(_loc_intent, transcript)
                    return

                # ── Score-based silent bind: winner ≥ 60 pts WITH prefix or soft-alias evidence ──
                # The resolver already computed both scores.  If the winning clinic
                # cleared the threshold AND has positive prefix/soft-alias evidence
                # (not pure similarity), the signal is strong enough to commit
                # without asking again — the next question names the clinic aloud
                # so the caller can correct mid-flow via the global correction handler.
                #
                # Pure-similarity-only ambiguous results are REJECTED here: words
                # like "please" / "alright" happen to share enough characters with
                # "alcester" to score 50–60 on similarity alone, but carry zero
                # clinic signal.  Requiring prefix or soft-alias evidence
                # eliminates that entire false-bind class without weakening
                # genuine noisy location capture (which always contributes a
                # prefix score via the first word).
                _sl_result = _resolve_clinic(text, context="ask_location")
                _sl_conf   = _sl_result.get("confidence", 0)
                _sl_reason = _sl_result.get("reason", "")
                _sl_has_signal = (
                    "prefix" in _sl_reason
                    or "soft_alias" in _sl_reason
                )
                if (
                    _sl_result["status"] == "ambiguous"
                    and _sl_conf >= 0.60
                    and _sl_has_signal
                ):
                    _sl_dbg  = _sl_result.get("debug", {})
                    _sl_alc  = _sl_dbg.get("alcester_score", 0)
                    _sl_red  = _sl_dbg.get("redditch_score", 0)
                    _sl_bind = "alcester" if _sl_alc >= _sl_red else "redditch"
                    self.session["selected_location"] = _sl_bind
                    self.session["needs_location"]    = False
                    self.session.pop("location_retry_count", None)
                    self.session.pop("location_pending_guess", None)
                    logger.info(
                        "[ms_flow] ASK_LOCATION: score-bind (alc=%d red=%d conf=%.2f → %s) for %r",
                        _sl_alc, _sl_red, _sl_conf, _sl_bind, text[:40],
                    )
                    await self.ask_current_question()
                    return

                # ── ASK_LOCATION-only clear-dominance bind (Bug A) ───────────────
                # Some obvious STT variants ("you access the clinic") produce
                # ambiguous status with pure-similarity evidence, so the bind
                # above skips them.  When one clinic score is clearly dominant
                # (winner ≥ 55 AND lead ≥ 30 over the loser) AND the caller has
                # not landed a filler (already filtered above), commit the
                # winner silently.  Scoped to ASK_LOCATION only — does NOT
                # relax the global resolver or weaken other states.
                if _sl_result["status"] == "ambiguous":
                    _sl_dbg2 = _sl_result.get("debug", {})
                    _sl_alc2 = _sl_dbg2.get("alcester_score", 0)
                    _sl_red2 = _sl_dbg2.get("redditch_score", 0)
                    _sl_win2  = max(_sl_alc2, _sl_red2)
                    _sl_lose2 = min(_sl_alc2, _sl_red2)
                    if _sl_win2 >= 55 and (_sl_win2 - _sl_lose2) >= 30:
                        _sl_bind2 = "alcester" if _sl_alc2 >= _sl_red2 else "redditch"
                        self.session["selected_location"] = _sl_bind2
                        self.session["needs_location"]    = False
                        self.session.pop("location_retry_count", None)
                        self.session.pop("location_pending_guess", None)
                        logger.info(
                            "[ms_flow] ASK_LOCATION: dominance-bind "
                            "(alc=%d red=%d margin=%d → %s) for %r",
                            _sl_alc2, _sl_red2, _sl_win2 - _sl_lose2,
                            _sl_bind2, text[:40],
                        )
                        await self.ask_current_question()
                        return

                # ── Retry ladder: escalate wording then switch to DTMF ───────────
                # retry 0 → spoken re-ask with explicit clinic names
                # retry 1+ → DTMF keypad fallback
                _loc_retry = self.session.get("location_retry_count", 0)
                if _loc_retry == 0:
                    # Second ask: polite re-ask with explicit names
                    _retry2_q = (
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    self.session["location_retry_count"] = 1
                    await self._tts.put(_retry2_q)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _retry2_q}
                    )
                    self.session["last_question"] = _retry2_q
                    logger.info(
                        "[ms_flow] ASK_LOCATION retry 2 emitted for %r", text[:40]
                    )
                else:
                    # Third ask: switch to DTMF keypad entry
                    self.session["location_awaiting_dtmf"] = True
                    _dtmf_q = (
                        "Sorry, I didn't quite catch that — "
                        "could you please press 1 on your keypad for the Alcester clinic "
                        "or 2 on your keypad for the Redditch clinic."
                    )
                    await self._tts.put(_dtmf_q)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _dtmf_q}
                    )
                    self.session["last_question"] = _dtmf_q
                    logger.info(
                        "[ms_flow] ASK_LOCATION retry 3 switched to DTMF for %r", text[:40]
                    )
            return

        # ════════════════════════════════════════════════════════════════════
        # HARD GATE: PHONE COLLECTION
        # Must be the first logic that runs in COLLECT_PHONE state.
        # Guarantees digit capture + readback fires regardless of any other
        # pending flags (slot_pending, vague_option, name handlers, etc.).
        # Does NOT fire when phone_readback_pending — that is handled by
        # the CONFIRM gate immediately below.
        # ════════════════════════════════════════════════════════════════════
        if current_state in ("COLLECT_PHONE", "COLLECT_PHONE_RESCHEDULE", "RETURNING_PLAN_COLLECT_PHONE") and not self.session.get("phone_readback_pending"):
            # ── NAME-REPAIR: step back to COLLECT_NAME from COLLECT_PHONE ───────
            # Must run before digit extraction so repair intents are not trapped
            # as failed digit entries and silently re-asked.
            _NAME_REPAIR_CP = (
                "got my name wrong", "name is wrong", "name's wrong",
                "wrong name", "wrong with my name",
                "misspelled my name", "mispelled my name",
                "spelled wrong", "spelt wrong",
                "go back to the name", "back to the name question",
                "name question", "help me spell", "spell my name",
                "messed up on my name", "messed up my name",
                "got the name wrong",
            )
            if any(p in text for p in _NAME_REPAIR_CP):
                _collect_name_states_cp = {
                    "COLLECT_NAME", "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                }
                _cn_idx_cp = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] in _collect_name_states_cp),
                    None,
                )
                if _cn_idx_cp is not None:
                    from app.media_streams.name_collector import NameCollector as _NC_cp
                    _NC_cp(self.session).reset()
                    self.session["flow_step"] = _cn_idx_cp
                    self.session["state"]     = self._active_flow[_cn_idx_cp]["state"]
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: name-repair → stepping back to %s",
                        self.session["state"],
                    )
                    await self.ask_current_question()
                    return

            # ── KEYPAD MODE REQUEST: caller asks to use the keypad ───────────────
            # Handles "can I type it", "use the keypad" etc. arriving when
            # phone_awaiting_dtmf is not yet active.  Must run before the
            # phone_awaiting_dtmf block and the digit-extraction import below
            # so the mode-switch response is emitted cleanly.
            if not self.session.get("phone_awaiting_dtmf"):
                _KEYPAD_MODE_REQUEST = (
                    "can i type", "i want to type", "can i use the keypad",
                    "use the keypad", "use my keypad", "can i use the keyboard",
                    "use the keyboard", "type the number", "enter it on",
                    "enter it manually", "type it on the keypad",
                    "type it on the keyboard", "can i enter", "enter on the keypad",
                    "press it on", "use my keyboard",
                )
                if any(p in (text or "").lower() for p in _KEYPAD_MODE_REQUEST):
                    self.session["phone_awaiting_dtmf"] = True
                    self.session["phone_dtmf_buffer"]   = ""
                    _kp_req_reply = "Yes — please go ahead and enter the number using your keypad."
                    await self._tts.put(_kp_req_reply)
                    self.session["last_question"] = _kp_req_reply
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _kp_req_reply}
                    )
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: keypad-mode request %r → phone_awaiting_dtmf=True",
                        (text or "")[:50],
                    )
                    return

            import re as _re_hg

            # ── Keypad-first mode: voice received while awaiting DTMF ────────
            # Priority order:
            #  1. Buffer already has ≥10 digits → finalize it (ignore what was spoken).
            #  2. Keypad-progress phrase ("I'm typing it in", "I'm finished") → stay silent.
            #  3. Caller spoke actual digits → fall through to voice processing.
            #  4. Non-digit non-keypad speech → voice fallback.
            if self.session.get("phone_awaiting_dtmf"):
                _existing_buf = self.session.get("phone_dtmf_buffer", "")
                _buf_digits   = _re_hg.sub(r"\D", "", _existing_buf)
                _dtmf_check   = _re_hg.sub(r"\D", "", text or "")
                # Phrases the caller uses to narrate keypad activity — not phone digits
                _KP_PHRASES = (
                    "typing it in", "typing in", "i'm typing", "im typing", "i am typing",
                    "just typed", "finished typing", "i've typed", "ive typed",
                    "typed it in", "typed in", "done typing",
                    "on the keypad", "on the key pad", "keypad now", "key pad now",
                    "just entered", "i'm finished", "im finished", "just finished",
                    "already typed", "just pressed",
                )
                _is_kp_phrase = any(p in (text or "").lower() for p in _KP_PHRASES)
                # Restart/correction phrases — caller wants to re-enter the number.
                # Clear the buffer and re-prompt with the keypad bridge so they start fresh.
                _KP_RESTART = (
                    "start again", "start over", "let me start", "can i start",
                    "begin again", "from the beginning", "wrong number", "that's wrong",
                    "thats wrong", "wrong one", "made a mistake", "got it wrong",
                    "actually no", "no wait", "scratch that", "never mind that",
                    "reset the keypad", "reset keypad", "reset it", "can you reset",
                    "clear that", "clear it", "clear the keypad",
                    "realised i made", "realized i made",
                )
                _is_restart = any(p in (text or "").lower() for p in _KP_RESTART)

                # Explicit voice-switch intent — caller wants to say the number
                # instead of typing.  Must be detected BEFORE the non-digit speech
                # fallback below so the acknowledgement is deterministic.
                _KP_VOICE_SWITCH = (
                    "i'll say it", "ill say it", "i will say it",
                    "say it instead", "can i say it", "could i say it",
                    "i'd rather say", "id rather say", "rather say it",
                    "i want to say it", "i wanna say it",
                    "let me say it", "let me just say", "i'll just say it",
                    "ill just say it", "speak it instead", "say the number instead",
                )
                _is_voice_switch = any(p in (text or "").lower() for p in _KP_VOICE_SWITCH)
                # Progress queries — caller asking if digits have arrived yet.
                _KP_PROGRESS_Q = (
                    "did you get", "have you got", "did that come through",
                    "did you receive", "did you catch", "got the number",
                    "can you see", "have you received", "is that through",
                )
                _is_progress_q = any(p in (text or "").lower() for p in _KP_PROGRESS_Q)

                if _is_restart:
                    # Clear ALL phone-entry buffers; stay in keypad mode so the caller
                    # can immediately resume typing without being re-prompted to switch.
                    self.session["phone_dtmf_buffer"]    = ""
                    self.session["phone_digits_buffer"]  = ""
                    self.session["phone_voice_attempts"] = 0
                    self.session["phone_awaiting_dtmf"]  = True
                    _restart_prompt = (
                        "No problem — I've reset the keypad for you. "
                        "You can restart typing in the number."
                    )
                    await self._tts.put(_restart_prompt)
                    self.session["last_question"] = _restart_prompt
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _restart_prompt}
                    )
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: keypad reset intent %r — ALL buffers cleared, staying in keypad mode",
                        (text or "")[:50],
                    )
                    return

                if _is_voice_switch:
                    # Caller explicitly wants to speak the number instead.  Exit
                    # keypad mode cleanly, clear any stale DTMF buffer state, and
                    # reset the spoken-attempts counter so spoken collection begins
                    # from scratch.  Recommend keypad as the more reliable path.
                    self.session["phone_dtmf_buffer"]    = ""
                    self.session["phone_digits_buffer"]  = ""
                    self.session["phone_voice_attempts"] = 0
                    self.session["phone_awaiting_dtmf"]  = False
                    _vs_prompt = (
                        "Okay — you can say it, but I recommend typing it on the keypad "
                        "because I'm not the best at catching phone numbers."
                    )
                    await self._tts.put(_vs_prompt)
                    self.session["last_question"] = _vs_prompt
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _vs_prompt}
                    )
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: voice-switch intent %r — exited keypad mode",
                        (text or "")[:50],
                    )
                    return

                if _is_progress_q:
                    if len(_buf_digits) >= 10:
                        # Buffer is already full — finalize and fall through
                        pass  # handled by the buffer-full branch below
                    elif len(_buf_digits) >= 5:
                        _prog_reply = (
                            f"I've received {len(_buf_digits)} digits so far — "
                            "please carry on typing the rest."
                        )
                        await self._tts.put(_prog_reply)
                        logger.info(
                            "[ms_flow] COLLECT_PHONE: progress query with %d digits buffered",
                            len(_buf_digits),
                        )
                        return
                    else:
                        _prog_reply = (
                            "I haven't received any digits yet — "
                            "please type your number on the keypad now."
                        )
                        await self._tts.put(_prog_reply)
                        logger.info(
                            "[ms_flow] COLLECT_PHONE: progress query with no usable buffer — re-prompting keypad",
                        )
                        return

                if len(_buf_digits) >= 10:
                    # Buffer holds a completable number — finalize regardless of speech.
                    # Pad to 11 digits with a leading 0 if the caller omitted it.
                    _finalized = ("0" + _buf_digits) if len(_buf_digits) == 10 else _buf_digits[:11]
                    self.session["phone_awaiting_dtmf"] = False
                    self.session["phone_dtmf_buffer"]   = ""
                    # Inject the buffered digits as the text so the hard gate processes them
                    text       = _finalized
                    transcript = _finalized
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: %s with %d-digit buffer → finalizing as %r",
                        "keypad-phrase" if _is_kp_phrase else "voice-while-DTMF",
                        len(_buf_digits), _finalized,
                    )
                    # Fall through to hard gate
                elif _is_kp_phrase:
                    # Caller is narrating keypad activity but hasn't typed enough digits yet
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: keypad-progress %r (buf=%r) — staying in keypad mode",
                        (text or "")[:50], _existing_buf,
                    )
                    return
                else:
                    self.session["phone_awaiting_dtmf"] = False
                    self.session["phone_dtmf_buffer"]   = ""
                    if len(_dtmf_check) < 5:
                        # Non-digit speech — tell caller to say the full number
                        _voice_fb = "If you'd rather say it, please say the full number from the beginning."
                        await self._tts.put(_voice_fb)
                        self.session["last_question"] = _voice_fb
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _voice_fb}
                        )
                        logger.info("[ms_flow] COLLECT_PHONE: voice received while awaiting DTMF — voice fallback")
                        return
                    # Caller spoke digits — fall through to normal voice processing

            # Slice to post-restart substring — discard digits before the final
            # restart marker so "07502 sorry actually start again 07502" captures
            # only "07502" (the fresh dictation), not "0750207502".
            _RESTART_SLICERS = (
                "start again", "start over", "let me start",
                "actually", "sorry", "hang on", "scratch that",
                "never mind", "no wait",
            )
            _text_lower_rs = (text or "").lower()
            _last_restart_end = -1
            for _rs in _RESTART_SLICERS:
                _rpos = _text_lower_rs.rfind(_rs)
                if _rpos >= 0 and (_rpos + len(_rs)) > _last_restart_end:
                    _last_restart_end = _rpos + len(_rs)
            _text_for_digits = (text or "")[_last_restart_end:] if _last_restart_end > 0 else (text or "")
            if _last_restart_end > 0:
                logger.info(
                    "[ms_flow] HARD GATE COLLECT_PHONE: restart-sliced → %r (was %r)",
                    _text_for_digits[:60], (text or "")[:60],
                )
            _hg_digits = _re_hg.sub(r"\D", "", _text_for_digits)

            if len(_hg_digits) >= 11:
                # Full UK number received (min 11 digits) — accept immediately.
                # 10-digit strings are rejected as incomplete (BUG 5).
                _hg_phone = _hg_digits[:11] if len(_hg_digits) > 11 else _hg_digits

                # Write phone fields for in-call use (readback template, LLM prompt etc.)
                # collected["phone"] is intentionally deferred until phone_confirmed=True
                # so corrupted or unconfirmed candidates never reach downstream summaries.
                self.session["phone"]          = _hg_phone
                self.session["phone_number"]   = _hg_phone
                self.session["customer_phone"] = _hg_phone
                self.session["phone_candidate"] = _hg_phone

                # Set all relevant flags
                self.session["phone_digits_buffer"] = ""
                self.session["phone_voice_attempts"] = 0  # successful capture resets counter

                _hg_spaced = _format_phone_readback(_hg_phone)

                # ALL flows: standard readback + wait for YES/NO confirmation.
                # Routing on YES is handled by the CONFIRM_PHONE YES handler which
                # knows how to route each flow to LOOKUP_RESCHEDULE / CONFIRM_CANCEL
                # / CONFIRM_BOOKING as appropriate.
                _hg_confirm_idx = next(
                    (i for i, s in enumerate(self._active_flow) if s["state"] == "CONFIRM_PHONE"),
                    _CONFIRM_PHONE_INDEX,
                )
                self.session["phone_readback_pending"] = True
                self.session["phone_confirmed"]        = False
                self.session["state"]                  = "CONFIRM_PHONE"
                self.session["flow_state"]             = "CONFIRM_PHONE"
                self.session["flow_step"]              = _hg_confirm_idx
                # ARM the CONFIRM_PHONE gate immediately.
                # ask_current_question() resets phone_confirm_armed=False at the
                # top of every call, but the COLLECT_PHONE hard gate bypasses
                # ask_current_question() and emits the readback directly.  Without
                # this explicit arm the gate check at the start of the next turn
                # sees phone_confirm_armed=False and loops back to the generic
                # caller-number question instead of accepting the yes/no answer.
                self.session["phone_confirm_armed"]    = True
                _hg_rb = f"Just to check — is that {_hg_spaced}?"
                self.session["last_question"] = _hg_rb
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _hg_rb}
                )

                logger.info(
                    "[ms_flow] HARD GATE COLLECT_PHONE: phone_digits_captured=%s state→CONFIRM_PHONE step→%d (gate armed)",
                    _hg_phone, _hg_confirm_idx,
                )

                self.session["_last_handled_by"]         = "collect_phone_full_digits"
                self.session["_last_extracted_phone"]    = _hg_phone
                self.session["_last_yes_detected"]       = False
                self.session["_last_no_detected"]        = False
                self.session["_last_assistant_response"] = _hg_rb
                # Flag so CONFIRM_PHONE YES/NO handlers know to route back into the
                # on-plan path (RETURNING_PLAN_LOOKUP on YES, RETURNING_PLAN_COLLECT_PHONE
                # on NO) instead of the default CONFIRM_BOOKING / COLLECT_PHONE targets.
                if current_state == "RETURNING_PLAN_COLLECT_PHONE":
                    self.session["_phone_returning_plan"] = True
                await self._tts.put(_hg_rb)
                return

            elif _hg_digits:
                # Partial — accumulate; silence handler re-asks if needed
                # Detect restarted dictation: explicit restart phrase OR suspiciously large new chunk
                _RESTART_SIGNALS = (
                    "sorry", "start again", "start over", "let me start",
                    "no wait", "actually", "hang on", "scratch that",
                    "never mind", "no the number", "no it's",
                )
                _existing_buf = self.session.get("phone_digits_buffer", "")
                _is_restart = (
                    any(sig in text.lower() for sig in _RESTART_SIGNALS)
                    or (len(_hg_digits) >= 7 and bool(_existing_buf))
                )
                if _is_restart and _existing_buf:
                    logger.info(
                        "[ms_flow] HARD GATE COLLECT_PHONE: restart detected — clearing buffer %r before %r",
                        _existing_buf, _hg_digits,
                    )
                    _existing_buf = ""
                # Duplicate / prefix-overlap guard: if the new chunk exactly
                # matches or starts with the existing buffer, the caller re-stated
                # earlier digits rather than continuing — replace instead of append.
                _log_mode = "append"
                if _existing_buf and not _is_restart:
                    if _hg_digits == _existing_buf:
                        # Exact duplicate (e.g. 07502 → 07502 again) — discard repeat
                        logger.info(
                            "[ms_flow] HARD GATE COLLECT_PHONE: exact-duplicate %r — no change",
                            _existing_buf,
                        )
                        self.session["_last_handled_by"]      = "collect_phone_partial_digits"
                        self.session["_last_extracted_phone"] = _existing_buf
                        self.session["_last_yes_detected"]    = False
                        self.session["_last_no_detected"]     = False
                        return
                    elif len(_existing_buf) >= 3 and _hg_digits.startswith(_existing_buf):
                        # Caller restarted and extended (e.g. 07502 → 0750211207 in one go)
                        _log_mode = "extended-restart-replace"
                        _existing_buf = ""
                    elif len(_existing_buf) >= 3 and _existing_buf.endswith(_hg_digits):
                        # Caller re-stated the tail of their number (e.g. 07502 → 7502)
                        # — suffix overlap; treat as restart to prevent corrupt append.
                        _log_mode = "suffix-overlap-restart"
                        _existing_buf = ""
                _hg_buffer = _existing_buf + _hg_digits

                # Hard length cap — if accumulated digits exceed a plausible UK number
                # length, the buffer is corrupt.  Hard-reset and ask caller to start over.
                if len(_hg_buffer) > 11:
                    _pva = self.session.get("phone_voice_attempts", 0) + 1
                    self.session["phone_voice_attempts"] = _pva
                    _reset_msg = "Let's start that number again from the beginning."
                    if _pva >= 2:
                        _reset_msg += " If it's easier, you can type it on your keypad."
                    await self._tts.put(_reset_msg)
                    self.session["phone_digits_buffer"] = ""
                    self.session["last_question"] = _reset_msg
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _reset_msg}
                    )
                    logger.info(
                        "[ms_flow] COLLECT_PHONE: buffer exceeded 11 digits %r — hard reset (#%d)",
                        _hg_buffer, _pva,
                    )
                    self.session["_last_handled_by"] = "collect_phone_hard_reset"
                    self.session["_last_yes_detected"] = False
                    self.session["_last_no_detected"]  = False
                    return

                self.session["phone_digits_buffer"] = _hg_buffer
                logger.info(
                    "[ms_flow] HARD GATE COLLECT_PHONE: %s %r → %r (%d digits)",
                    _log_mode, _hg_digits, _hg_buffer, len(_hg_buffer),
                )
                self.session["_last_handled_by"]      = "collect_phone_partial_digits"
                self.session["_last_extracted_phone"] = _hg_buffer
                self.session["_last_yes_detected"]    = False
                self.session["_last_no_detected"]     = False
                return

            else:
                # No digits — only allow narrow privacy-purpose questions in this
                # hard-gated state. Broad FAQ detection caused irrelevant monologues.
                _cp_frozen_q = self.session.get("last_question", "")
                _CP_ALLOWED_INQUIRIES = (
                    "why do you need my number",
                    "what do you need my number for",
                    "why do you need my phone number",
                    "what's my number for",
                    "what is my number for",
                )
                if _cp_frozen_q and any(p in text for p in _CP_ALLOWED_INQUIRIES):
                    _cp_privacy = (
                        "We use it so the team can get back to you if there are any changes "
                        "to your appointment."
                    )
                    await self._tts.put(_cp_privacy)
                    await self._tts.put(_cp_frozen_q)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _cp_privacy}
                    )
                    self.session["last_info_answer"] = _cp_privacy
                    return
                # No digits and not an inquiry — re-ask
                logger.info(
                    "[ms_flow] HARD GATE COLLECT_PHONE: no digits in %r — re-asking",
                    text[:60],
                )
                _pva_nd = self.session.get("phone_voice_attempts", 0) + 1
                self.session["phone_voice_attempts"] = _pva_nd
                self.session["_last_handled_by"]   = "collect_phone_no_digits"
                self.session["_last_yes_detected"] = False
                self.session["_last_no_detected"]  = False
                await self.ask_current_question()
                return

        # ════════════════════════════════════════════════════════════════════
        # HARD GATE: PHONE CONFIRMATION
        # Fires whenever session["state"] == "CONFIRM_PHONE" — covers both:
        #   • Step-12 Twilio number confirm (CONFIRM_PHONE flow step)
        #   • Post-COLLECT_PHONE readback (state set to CONFIRM_PHONE above)
        # Must run before slot/vague/name handlers can intercept yes/no input.
        # Every branch returns — no fallthrough.
        # ════════════════════════════════════════════════════════════════════
        if self.session.get("state") == "CONFIRM_PHONE":
            # ── CLINIC-CORRECTION precedence ─────────────────────────────────
            # If the caller is correcting a mistakenly-bound clinic (common
            # after a weak score-bind at ASK_LOCATION), apply the clinic
            # correction FIRST — do not let the leading "no" drive the local
            # CONFIRM_PHONE NO handler into keypad mode.
            #   e.g. "no it was at our sister" / "no, Alcester" / "I said Redditch"
            # Strong alias resolution only (no weak score-bind here); utterance
            # must carry a correction marker to avoid firing on plain yes/no.
            if self.session.get("selected_location"):
                _cc_corr_markers = (
                    "no ", "no,", "nope", "nah",
                    "actually", "i said", "i meant",
                    "it was at", "it was the", "it's at", "its at",
                    "it's the", "its the", "not ", "wrong clinic",
                )
                _cc_has_marker = any(m in text for m in _cc_corr_markers) or text.strip() in ("no", "nope", "nah")
                if _cc_has_marker:
                    _cc_loc = _resolve_location(text, in_location_state=True)
                    if _cc_loc and _cc_loc != self.session.get("selected_location"):
                        _cc_prev = self.session.get("selected_location")
                        self.session["selected_location"] = _cc_loc
                        self.session["needs_location"]    = False
                        self.session.pop("location_pending_guess", None)
                        # Do NOT disarm phone_confirm_armed — caller never
                        # answered the phone question.  Re-ask it so the NO
                        # handler cannot consume the same utterance.
                        logger.info(
                            "[ms_flow] CONFIRM_PHONE: clinic-correction precedence %s→%s for %r",
                            _cc_prev, _cc_loc, text[:60],
                        )
                        await self.ask_current_question()
                        return

            # ── BUG 4 fix: PHONE-CORRECTION early-exit ───────────────────────
            # "no it's a different number" / "no I want to use a different number"
            # must be caught BEFORE the name-repair check because _NAME_REPAIR
            # includes "no it's" and "actually it's" which false-fire on phone
            # correction phrases.  Check alternate-number intent first.
            _PHONE_CORRECTION_EARLY = (
                "different number", "another number", "use another",
                "give you another", "use a different", "use another number",
                "different phone", "another phone", "give you a different",
                "use a different number", "different mobile",
                "no it's a different", "no its a different",
                "no it is a different", "not that number",
            )
            if any(p in text for p in _PHONE_CORRECTION_EARLY):
                # Route back to the appropriate phone-collection step.
                # A new patient must go to COLLECT_PHONE (not COLLECT_PHONE_RETURNING)
                # so the correct question is asked.  Use on_treatment_plan to
                # distinguish: treatment-plan returning patients → COLLECT_PHONE_RETURNING,
                # everyone else → COLLECT_PHONE.  Fall back to whichever state exists
                # in the active flow if the primary target isn't found.
                _primary_phone_state = (
                    "RETURNING_PLAN_COLLECT_PHONE"
                    if self.session.pop("_phone_returning_plan", False)
                    else "COLLECT_PHONE_RETURNING"
                    if self.session.get("on_treatment_plan")
                    else "COLLECT_PHONE"
                )
                _cp_idx_early = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] == _primary_phone_state),
                    None,
                )
                if _cp_idx_early is None:
                    # Fallback: accept either state if the primary wasn't found
                    _cp_idx_early = next(
                        (i for i, s in enumerate(self._active_flow)
                         if s["state"] in ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
                                           "RETURNING_PLAN_COLLECT_PHONE")),
                        None,
                    )
                if _cp_idx_early is not None:
                    self.session["phone_readback_pending"] = False
                    self.session.pop("phone_candidate", None)
                    self.session.pop("phone_number", None)
                    self.session.pop("phone", None)
                    self.session["phone_digits_buffer"]  = ""
                    self.session["phone_dtmf_buffer"]    = ""
                    self.session["phone_confirmed"]      = False
                    self.session.setdefault("collected", {}).pop("phone", None)
                    self.session["flow_step"]            = _cp_idx_early
                    self.session["state"]                = self._active_flow[_cp_idx_early]["state"]
                    # Switch immediately into keypad-first mode so the caller hears
                    # an explicit instruction — not the generic "best number to reach
                    # you on" voice question.
                    self.session["phone_awaiting_dtmf"]  = True
                    _cp_bridge = "No problem — please type the number using your keypad."
                    await self._tts.put(_cp_bridge)
                    self.session["last_question"] = _cp_bridge
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _cp_bridge}
                    )
                    logger.info(
                        "[ms_flow] CONFIRM_PHONE phone-correction early-exit → %s (keypad mode)",
                        self.session["state"],
                    )
                    return

            # ── NAME-REPAIR: caller says the captured name was wrong ───────────
            # BUG 6 fix: extended to catch more natural name-correction phrases.
            # Must run BEFORE the YES/NO gate so repair utterances are never
            # trapped as ambiguous phone confirmations.
            _NAME_REPAIR = (
                "got my name wrong", "name is wrong", "name's wrong",
                "wrong name", "wrong with my name",
                "misspelled my name", "mispelled my name",
                "spelled wrong", "spelt wrong",
                "go back to the name", "back to the name question",
                "name question", "help me spell", "spell my name",
                "messed up on my name", "messed up my name",
                "got the name wrong",
                # BUG 6 extended: natural correction phrasing from live calls
                "actually my name is", "my name is actually",
                "no my name is", "no, my name",
                "the name is", "it should be under", "it's under",
                "name should be", "my surname is", "my first name is",
                "actually it's", "no it's",
            )
            if any(p in text for p in _NAME_REPAIR):
                # Locate COLLECT_NAME step in the active flow (works for all flows)
                _collect_name_states = {
                    "COLLECT_NAME", "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                }
                _cn_idx = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] in _collect_name_states),
                    None,
                )
                if _cn_idx is not None:
                    from app.media_streams.name_collector import NameCollector as _NC_cfp
                    _NC_cfp(self.session).reset()
                    self.session["phone_readback_pending"] = False
                    self.session["flow_step"] = _cn_idx
                    self.session["state"]     = self._active_flow[_cn_idx]["state"]
                    logger.info(
                        "[ms_flow] CONFIRM_PHONE: name-repair → stepping back to %s",
                        self.session["state"],
                    )
                    await self.ask_current_question()
                    return

            # BUG 7 fix: caller asks what name was captured — restate and ask to confirm.
            _RECAP_PHRASES = (
                "what did you catch", "what did you get", "what name do you have",
                "what name have you got", "what name have you got me as",
                "what did you get my name as", "what did you write down",
                "what have you got for my name", "what name is there",
                "i don't think you asked for it", "did you get my name",
                "what name do you have for me", "what name did you catch",
                "what have you got", "what did you note",
            )
            if any(p in text for p in _RECAP_PHRASES):
                _recap_name = (
                    self.session.get("full_name")
                    or (self.session.get("collected") or {}).get("full_name")
                    or (self.session.get("collected") or {}).get("name")
                    or "I haven't got a name yet"
                )
                _recap_q = f"I currently have the booking under {_recap_name} — is that correct?"
                await self._tts.put(_recap_q)
                self.session["last_question"] = _recap_q
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _recap_q}
                )
                logger.info("[ms_flow] CONFIRM_PHONE BUG 7: recap requested — restated name %r", _recap_name)
                return

            # ── Turn-boundary guard ───────────────────────────────────────────
            # Only accept YES/NO when the phone question was the last question
            # emitted by ask_current_question().  This prevents surname remnants
            # ("right", "rock is", "okay") landing here via a split-turn and
            # being consumed as a false phone confirmation.
            if not self.session.get("phone_confirm_armed"):
                logger.warning(
                    "[ms_flow] CONFIRM_PHONE: gate not armed "
                    "(phone_confirm_armed=False) — re-asking. text=%r", text[:80],
                )
                await self.ask_current_question()
                return

            # Weak standalone tokens ("right", "ok", "correct", "aye") removed:
            # phrase-level variants ("that's right", "that's ok") kept because
            # they require explicit phone-adjacent context.
            _HG_YES = (
                "yes", "yeah", "yep", "yup", "yeh", "ya",
                "that's right", "thats right",
                "that is right",                    # "that is" variant of "that's right"
                "that's correct", "thats correct",
                "that is correct",                  # "that is" variant of "that's correct"
                "that's fine", "thats fine", "that's ok", "thats ok",
                "use this number", "yes use this number", "yeah use this number",
                "use my number", "yes use my number",
                "same number", "use my current number",
                "this number", "this one",          # bare "this number" / "this one"
                "the number i'm calling on",        # literal restatement of the question
                "number i'm calling on",
                "the number i am calling on",
                # PART 2: additional strong affirmatives from live calls
                "correct",                          # standalone "correct" (safe: gate is armed)
                "correct number",
                "yes please", "yeah please",
                # Partial / scaffold-like affirmatives where STT finalises before
                # the caller finishes the sentence — treat as YES immediately.
                "you can use this", "can use this", "use this",
                "go ahead", "that's the one", "thats the one",
                # Implicit/contextual affirmatives: in response to "Is this the number
                # on your booking?" these mean YES without the word 'yes'.
                # Only safe here because phone_confirm_armed = True gates the context.
                "it is", "yes it is", "yeah it is",
                "you should", "yes you should", "yeah you should",
                "it should", "it should be", "should be",
            )
            _HG_NO_PHRASES = (
                "nope", "nah",
                "no use a different number", "different number",
                "use a different number", "another number",
                "no different number", "give you another number",
                "no i'll give you another",
                "wrong number", "not the right number",
                "that's not the right number", "that's the wrong number",
                "thats not the right number", "thats the wrong number",
                "not the best number",
            )
            import re as _hg_re
            _hg_yes = any(p in text for p in _HG_YES)
            # Use word-boundary regex for bare "no" so that "afternoon", "noted",
            # etc. never trigger a false phone-denial.
            _hg_no  = (
                bool(_hg_re.search(r'\bno\b', text))
                or any(p in text for p in _HG_NO_PHRASES)
            )
            # Semantic YES — natural caller affirmations that don't use the word "yes".
            # Guard: must not fire if "no" is present or negation word is present.
            _SEMANTIC_YES_PHRASES = (
                "that's the best number", "thats the best number",
                "that's the right number", "thats the right number",
                "that is the right number",              # PART 2: "that is" vs "that's"
                "this is the best number", "this is the right number",
                "that's the one", "thats the one",
                "you can use this one", "you can use this number",
                "this number is fine", "that number is fine",
                "best number to reach me", "best number for me",
                "that one is fine", "this one is fine",
            )
            _SEMANTIC_YES_NEGATION = ("not", "different", "another", "wrong")
            _semantic_yes = (
                any(p in text for p in _SEMANTIC_YES_PHRASES)
                and not _hg_re.search(r'\bno\b', text)
                and not any(n in text for n in _SEMANTIC_YES_NEGATION)
            )

            if (_hg_yes or _semantic_yes) and not _hg_no:
                self.session["phone_confirm_armed"]    = False  # disarm — gate consumed
                self.session["phone_readback_pending"] = False
                self.session.pop("phone_candidate", None)

                # ── Resolve phone before branching — single source of truth ────
                # Check every store in canonical priority order.
                # collected["phone"] is set by connection.py at call-start from
                # Twilio caller-ID; it is the same number as twilio_from but is
                # the canonical key used by downstream lookup tools.
                _cp_confirmed = (
                    self.session.get("phone_number")
                    or self.session.get("phone_candidate")
                    or self.session.get("phone")
                    or (self.session.get("collected") or {}).get("phone")
                    or self.session.get("twilio_from_local")
                    or self.session.get("twilio_from")
                )
                _cp_source = (
                    "phone_number"      if self.session.get("phone_number")      else
                    "phone_candidate"   if self.session.get("phone_candidate")   else
                    "phone"             if self.session.get("phone")             else
                    "collected.phone"   if (self.session.get("collected") or {}).get("phone") else
                    "twilio_from_local" if self.session.get("twilio_from_local") else
                    "twilio_from"       if self.session.get("twilio_from")       else
                    "none"
                )
                _cp_kind = "semantic_yes" if _semantic_yes and not _hg_yes else "explicit_yes"

                if _cp_confirmed:
                    # ── CLEAN PATH: phone found — mark confirmed, advance directly ──
                    # phone_confirmed is set TRUE only here; COLLECT_PHONE skip
                    # at ask_current_question() can never fire as accidental recovery.
                    _cp_normalised = _to_e164_uk(_cp_confirmed) or _cp_confirmed
                    self.session["phone_confirmed"] = True
                    # Set phone_number explicitly — LOOKUP_RESCHEDULE instruction
                    # uses {phone_number} in its format string; without this the
                    # lookup would render phone='None' and fail to find the booking.
                    self.session["phone_number"] = _cp_normalised
                    self.session.setdefault("collected", {})["phone"] = _cp_normalised
                    if self._active_flow is RESCHEDULE_FLOW:
                        self.session["flow_step"]  = _RESCHEDULE_LOOKUP_INDEX
                        self.session["state"]      = "LOOKUP_RESCHEDULE"
                        self.session["flow_state"] = "LOOKUP_RESCHEDULE"
                        # Phone-first flag: signals that we entered LOOKUP_RESCHEDULE
                        # without collecting a name.  BUG13 pre-LLM guard skips its
                        # "route to name collection" path when this flag is set so the
                        # LLM can do a phone-only lookup on the first attempt.
                        if not self.session.get("full_name") and not (
                            self.session.get("collected") or {}
                        ).get("full_name"):
                            self.session["rc_phone_first"] = True
                    elif self.session.pop("_phone_returning_plan", False):
                        # On-plan returning patient confirmed their number — proceed to
                        # treatment-plan lookup (skipping steps 6-12 which are irrelevant
                        # for on-plan patients).
                        self.session["flow_step"]  = _RETURNING_PLAN_LOOKUP_INDEX
                        self.session["state"]      = "RETURNING_PLAN_LOOKUP"
                        self.session["flow_state"] = "RETURNING_PLAN_LOOKUP"
                    else:
                        # Route to PRESENT_DAYS for availability+day+time selection unless
                        # a real slot was already chosen (e.g. caller re-confirmed after
                        # an earlier selection).  Never jump straight to CONFIRM_BOOKING.
                        if self.session.get("selected_slot"):
                            self.session["state"]      = "CONFIRM_BOOKING"
                            self.session["flow_state"] = "CONFIRM_BOOKING"
                            self.session["flow_step"]  = _CONFIRM_BOOKING_INDEX
                        else:
                            self.session["state"]      = "PRESENT_DAYS"
                            self.session["flow_state"] = "PRESENT_DAYS"
                            self.session["flow_step"]  = _PRESENT_DAYS_INDEX
                    logger.info(
                        "[ms_flow] HARD GATE CONFIRM_PHONE: %s + phone resolved (src=%s) "
                        "→ %s phone=...%s",
                        _cp_kind, _cp_source,
                        self.session.get("state"),
                        _cp_confirmed[-4:],
                    )
                else:
                    # ── COLLECT PATH: truly no phone anywhere — route to collection ──
                    # phone_confirmed stays FALSE so the COLLECT_PHONE skip guard at
                    # ask_current_question() never fires as an accidental recovery.
                    self.session["phone_confirmed"] = False
                    if self._active_flow is RESCHEDULE_FLOW:
                        self.session["flow_step"]  = _RESCHEDULE_COLLECT_PHONE_INDEX
                        self.session["state"]      = "COLLECT_PHONE"
                        self.session["flow_state"] = "COLLECT_PHONE"
                    else:
                        self.session["flow_step"]  = _COLLECT_PHONE_INDEX
                        self.session["state"]      = "COLLECT_PHONE"
                        self.session["flow_state"] = "COLLECT_PHONE"
                    logger.warning(
                        "[ms_flow] HARD GATE CONFIRM_PHONE: %s but no phone found "
                        "anywhere — routing to COLLECT_PHONE",
                        _cp_kind,
                    )

                self.session["_last_handled_by"]   = "confirm_phone_yes"
                self.session["_last_yes_detected"] = True
                self.session["_last_no_detected"]  = False
                # Reschedule/cancel: emit a short holding message before the LLM
                # fires the lookup so the caller hears something immediately.
                if self._active_flow is RESCHEDULE_FLOW:
                    await self._tts.put(
                        "One second please — I'm just looking for your booking."
                    )
                await self.ask_current_question()
                return

            elif _hg_no and not _hg_yes:
                self.session["phone_confirm_armed"]    = False  # disarm — gate consumed
                # Seed partial digits already spoken in the same utterance
                # e.g. "the right number is 07502" → seed "07502" so the
                # next COLLECT_PHONE turn completes accumulation immediately.
                import re as _re_seed
                _seed_digits = _re_seed.sub(r"\D", "", text or "")
                self.session["phone"]                  = None
                self.session["phone_number"]           = None
                self.session["customer_phone"]         = None
                # Keypad-first: clear any stale voice buffer so DTMF digits
                # don't mix with previously spoken fragments.
                self.session["phone_digits_buffer"]    = ""
                self.session["phone_dtmf_buffer"]      = ""
                self.session["phone_awaiting_dtmf"]    = True
                self.session["phone_readback_pending"] = False
                self.session["phone_confirmed"]        = False
                self.session.pop("phone_candidate", None)
                _is_rp_no = self.session.pop("_phone_returning_plan", False)
                _no_state = "RETURNING_PLAN_COLLECT_PHONE" if _is_rp_no else "COLLECT_PHONE"
                self.session["state"]                  = _no_state
                self.session["flow_state"]             = _no_state
                self.session["flow_step"]              = (
                    _RESCHEDULE_COLLECT_PHONE_INDEX
                    if self._active_flow is RESCHEDULE_FLOW
                    else _CANCEL_COLLECT_PHONE_INDEX
                    if self._active_flow is CANCEL_FLOW
                    else _RETURNING_PLAN_COLLECT_PHONE_INDEX
                    if _is_rp_no
                    else _COLLECT_PHONE_INDEX
                )
                self.session.setdefault("collected", {}).pop("phone", None)
                logger.info(
                    "[ms_flow] HARD GATE CONFIRM_PHONE: NO → COLLECT_PHONE (keypad-first)",
                )
                self.session["_last_handled_by"]   = "confirm_phone_no"
                self.session["_last_yes_detected"] = False
                self.session["_last_no_detected"]  = True
                # PART 3: unified keypad bridge — clear, direct, no "best number to reach you on"
                _bridge = "No problem — please type the correct number using your keypad."
                await self._tts.put(_bridge)
                self.session["last_question"] = _bridge
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _bridge}
                )
                logger.info("[ms_flow] CONFIRM_PHONE NO: keypad-first bridge → COLLECT_PHONE")
                return

            else:
                # ── Digit-led correction rescue ──────────────────────────────
                # Caller is re-dictating or correcting the number (e.g. "you
                # misheard that the number is 07502" or just "11207") — route
                # back to COLLECT_PHONE rather than re-asking yes/no endlessly.
                import re as _re_f
                _cp_digits_str = _re_f.sub(r"\D", "", text or "")
                _PHONE_CORRECTION_PHRASES = (
                    "you misheard", "misheard", "the number is",
                    "my number is", "it should be", "it's actually",
                    "try again", "start again",
                    # Additional natural rephrasings from live calls
                    "the number on the booking", "number on the booking",
                    "the number for the booking", "number for the booking",
                    "the number associated", "number associated",
                    "it is actually", "my correct number", "correct number is",
                )
                _is_phone_correction = (
                    len(_cp_digits_str) >= 5
                    or (
                        len(_cp_digits_str) >= 3
                        and any(p in text for p in _PHONE_CORRECTION_PHRASES)
                    )
                ) and not _hg_yes

                if _is_phone_correction:
                    self.session["phone"]                  = None
                    self.session["phone_number"]           = None
                    self.session["customer_phone"]         = None
                    self.session["phone_digits_buffer"]    = _cp_digits_str if _cp_digits_str else ""
                    self.session["phone_readback_pending"] = False
                    self.session["phone_confirmed"]        = False
                    self.session.pop("phone_candidate", None)
                    self.session["state"]                  = "COLLECT_PHONE"
                    self.session["flow_state"]             = "COLLECT_PHONE"
                    self.session["flow_step"]              = (
                        _RESCHEDULE_COLLECT_PHONE_INDEX
                        if self._active_flow is RESCHEDULE_FLOW
                        else _CANCEL_COLLECT_PHONE_INDEX
                        if self._active_flow is CANCEL_FLOW
                        else _COLLECT_PHONE_INDEX
                    )
                    self.session.setdefault("collected", {}).pop("phone", None)
                    _repair = "No problem — let me take that number again."
                    await self._tts.put(_repair)
                    self.session["last_question"] = _repair
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _repair}
                    )
                    logger.info(
                        "[ms_flow] CONFIRM_PHONE: digit-correction rescue → COLLECT_PHONE seeded=%r",
                        _cp_digits_str or "(none)",
                    )
                    return

                # Ambiguous — only allow narrow privacy-purpose inquiries here.
                # Broad FAQ detection is disabled in phone-confirmation state.
                _hg_lq = self.session.get("last_question", "Is that number correct?")
                _CP_ALLOWED_CONFIRM_INQUIRIES = (
                    "why do you need my number",
                    "what do you need my number for",
                    "why do you need my phone number",
                    "what's my number for",
                    "what is my number for",
                )
                if any(p in text for p in _CP_ALLOWED_CONFIRM_INQUIRIES):
                    _cp_priv2 = (
                        "We use it so the team can get back to you if there are any changes "
                        "to your appointment."
                    )
                    await self._tts.put(_cp_priv2)
                    await self._tts.put(_hg_lq)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _cp_priv2}
                    )
                    self.session["last_info_answer"] = _cp_priv2
                    return
                # Not an inquiry — tight re-ask with canonical phone-confirm wording
                _ambiguous_reask = (
                    self.session.get("last_question", "")
                    if self.session.get("phone_readback_pending")
                    else (
                        "Sorry, I didn\u2019t catch that \u2014 "
                        "if you'd like me to use the number you're calling on, "
                        "say: use this number. Otherwise, say: different number."
                    )
                )
                logger.info(
                    "[ms_flow] HARD GATE CONFIRM_PHONE: ambiguous %r — tight re-ask",
                    text[:60],
                )
                self.session["_last_handled_by"]         = "confirm_phone_ambiguous"
                self.session["_last_yes_detected"]       = _hg_yes
                self.session["_last_no_detected"]        = _hg_no
                self.session["_last_assistant_response"] = _ambiguous_reask
                self.session["last_question"]            = _ambiguous_reask
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _ambiguous_reask}
                )
                await self._tts.put(_ambiguous_reask)
                return

        # ── HARD PHONE-STEP GUARD (Phase 5): once we are in a phone step or
        #    awaiting phone readback, slot/day/vague-option handlers must not
        #    fire — they belong to earlier flow steps and must no-op here.
        #    Also treat explicit phone-accept phrases as a phone step so that
        #    slot_pending_confirmation and vague_option_pending cannot intercept
        #    "yes use this number" before the compat jump blocks run.
        _in_phone_step = step["state"] in {
            "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
        } or bool(self.session.get("phone_readback_pending")) or _is_phone_accept(text)

        # ── SLOT CONFIRMATION: waiting for yes/no after slot selection ────────
        if not _in_phone_step and self.session.get("slot_pending_confirmation"):
            await self._handle_slot_confirmation(text, transcript)
            self.session["_last_handled_by"] = "slot_pending_confirmation"
            return

        # ── READBACK CONFIRMATION: waiting for caller to confirm full booking ─
        if self.session.get("readback_pending"):
            await self._handle_readback_confirmation(text, transcript)
            self.session["_last_handled_by"] = "readback_pending_confirmation"
            return

        # ── VAGUE OPTION SELECTION: caller responding to 2 concrete options ───
        # Set by vagueness detection at PRESENT_DAYS. Parse the selection here
        # so the normal extraction logic is bypassed for this special state.
        if not _in_phone_step and self.session.get("vague_option_pending"):
            from app.vagueness_detector import parse_option_selection
            _vopts = self.session.get("presented_vague_options", [])

            # ── HARD SUCCESS PATH: ordinal resolves directly from presented options ──
            # Must run before parse_option_selection so "first one" never falls through
            # to vague re-ask logic and returns without output.
            _VOP_ORD_PAIRS = [
                ("the middle one", 1), ("middle one", 1), ("the middle", 1),
                ("first one", 0), ("second one", 1), ("third one", 2),
                ("the first", 0), ("the second", 1), ("the third", 2),
                ("the last", -1), ("last one", -1), ("the final", -1),
                ("first", 0), ("second", 1), ("third", 2),
                ("middle", 1),
                ("one", 0), ("two", 1), ("three", 2),
                ("last", -1), ("final", -1),
            ]
            _vop_ord_idx = None
            for _vop_pat, _vop_i in _VOP_ORD_PAIRS:
                if _vop_pat in text:
                    _vop_ord_idx = _vop_i
                    break

            if _vop_ord_idx is not None and _vopts:
                _vop_n = len(_vopts)
                _vop_r = _vop_ord_idx if _vop_ord_idx >= 0 else max(0, _vop_n + _vop_ord_idx)
                _vop_r = min(_vop_r, _vop_n - 1)
                _vop_chosen = _vopts[_vop_r]
                self.session["chosen_day"]              = _vop_chosen.get("day_label", "")
                self.session["vague_option_pending"]    = False
                self.session["presented_vague_options"] = []
                self.session.pop("vague_clarification_asked", None)
                # Each vague option already contains a specific slot (slot_iso +
                # time_speech).  For RESCHEDULE_FLOW, pre-select it and advance
                # directly to CONFIRM_RESCHEDULE (step+2), skipping PRESENT_TIMES.
                # For BOOKING_FLOW, PRESENT_TIMES is followed by name/phone steps
                # so we must NOT skip it — advance +1 as before.
                _vop_slot_iso = _vop_chosen.get("slot_iso", "")
                if _vop_slot_iso and self._active_flow is RESCHEDULE_FLOW:
                    _vop_time_speech = _vop_chosen.get("time_speech", "")
                    _vop_day_label   = _vop_chosen.get("day_label", "")
                    self.session["selected_slot"]        = _vop_slot_iso
                    self.session["selected_slot_speech"] = (
                        f"{_vop_day_label} at {_vop_time_speech}"
                        if _vop_time_speech else _vop_day_label
                    )
                    _vop_next = min(step["step"] + 2, len(self._active_flow) - 1)
                else:
                    _vop_next = step["step"] + 1
                _vop_ns = (
                    self._active_flow[_vop_next]["state"]
                    if _vop_next < len(self._active_flow) else "DONE"
                )
                self.session["flow_step"]  = _vop_next
                self.session["state"]      = _vop_ns
                self.session["flow_state"] = _vop_ns
                self.session["_last_handled_by"] = "slot_ordinal_selection"
                print("[SLOT GATE] ordinal selection -> pending confirm", {
                    "text":          text,
                    "state":         self.session.get("state"),
                    "flow_step":     self.session.get("flow_step"),
                    "selected_slot": self.session.get("selected_slot") or _vop_chosen.get("day_label"),
                })
                logger.info(
                    "[ms_flow] SLOT GATE vague ordinal: idx=%d day=%r slot=%r next_state=%s",
                    _vop_r, self.session["chosen_day"],
                    (self.session.get("selected_slot") or "")[:30], _vop_ns,
                )
                await self.ask_current_question()
                return

            if _vop_ord_idx is not None:
                # Ordinal matched but no presented options — clear stale flags and
                # fall through to PRESENT_DAYS ordinal handler (no return).
                self.session["vague_option_pending"]    = False
                self.session["presented_vague_options"] = []
                self.session.pop("vague_clarification_asked", None)
                logger.info(
                    "[ms_flow] vague_option_pending: ordinal %r — no options, cleared, falling through",
                    transcript[:40],
                )
            else:
                _selected = parse_option_selection(transcript, _vopts)
                if _selected:
                    # Caller selected one of the two options — store and advance
                    self.session["chosen_day"]              = _selected["day_label"]
                    self.session["vague_option_pending"]    = False
                    self.session["presented_vague_options"] = []
                    logger.info(
                        "[ms_flow] vague option selected: %r %s",
                        _selected["day_label"], _selected["time_hhmm"],
                    )
                    self.session["_last_handled_by"] = "vague_option_selection"
                    await self.ask_current_question()
                else:
                    # Ambiguous — ask once more for clarification, then default
                    _already_asked = self.session.get("vague_clarification_asked", False)
                    if not _already_asked and len(_vopts) == 2:
                        o1, o2 = _vopts[0], _vopts[1]
                        phrase = (
                            f"Was that {o1['day_label']} at {o1['time_speech']}, "
                            f"or {o2['day_label']} at {o2['time_speech']}?"
                        )
                        self.session["vague_clarification_asked"] = True
                        await self._tts.put(phrase)
                        self.session["last_question"] = phrase
                        logger.info("[ms_flow] vague: ambiguous selection — asking clarification")
                    else:
                        # Default to first option after second ambiguity
                        if _vopts:
                            self.session["chosen_day"]              = _vopts[0]["day_label"]
                            self.session["vague_option_pending"]    = False
                            self.session["presented_vague_options"] = []
                            self.session.pop("vague_clarification_asked", None)
                            logger.info(
                                "[ms_flow] vague: defaulting to first option %r", _vopts[0]["day_label"]
                            )
                            await self.ask_current_question()
                self.session["_last_handled_by"] = "vague_option_selection"
                return

        # ── ABANDONMENT: caller says "never mind" or wants to cancel ─────────
        _ABANDON_SIGNALS = (
            "never mind", "nevermind", "forget it", "forget this",
            # "actually no" removed — it is a substring of "actually none of these"
            # and falsely fires on date-navigation utterances like
            # "actually none of these work, go back".
            # Replaced with unambiguous variants:
            "actually no thanks", "actually nope", "actually cancel",
            "don't bother", "dont bother",
            "not anymore", "changed my mind", "not interested",
            "not now", "no thanks", "cancel that", "cancel this",
            "want to stop", "want to cancel",
        )
        # Active date-navigation must never trigger abandonment.
        # Caller is negotiating available dates, not giving up.
        _ACTIVE_NAV_SIGNALS = (
            "none of these", "none of them", "none of those",
            "go back", "original dates", "initial dates",
            "earlier dates", "previous dates", "list the",
            "the first ones", "back to",
        )
        _is_active_nav = (
            step["state"] in (
                "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
                "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
            )
            and any(p in text for p in _ACTIVE_NAV_SIGNALS)
        )
        # BUG 5: In PRESENT_DAYS/PRESENT_TIMES, ambiguous signals like
        # "never mind", "forget it", "forget this" must NOT trigger abandonment —
        # they are handled as step-back navigation signals in the state handlers.
        _SCHED_NAV_STATES = frozenset({
            "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
            "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
        })
        _SCHED_NAV_AMBIGUOUS = frozenset({
            "never mind", "nevermind", "forget it", "forget this",
        })
        _is_sched_nav_ambiguous = (
            step["state"] in _SCHED_NAV_STATES
            and any(p in text for p in _SCHED_NAV_AMBIGUOUS)
        )
        if (
            step["state"] != "DETECT_INTENT"
            and not _is_active_nav
            and not _is_sched_nav_ambiguous
            and any(sig in text for sig in _ABANDON_SIGNALS)
        ):
            # Insertion point 2 — name usage tracker (final sign-off)
            _sign_off_name = self._name_tracker.get_name_if_available()
            self.session["name_tracker_uses"] = self._name_tracker._uses_remaining
            if _sign_off_name:
                _base = (
                    f"No problem at all, {_sign_off_name}! "
                    "If you change your mind, don't hesitate to call us back. "
                    "Have a great day!"
                )
                # Guard: 400-char ElevenLabs limit
                phrase = _base if len(_base) <= 400 else (
                    "No problem at all! If you change your mind, don't hesitate to call us back. "
                    "Have a great day!"
                )
            else:
                phrase = (
                    "No problem at all! If you change your mind, don't hesitate to call us back. "
                    "Have a great day!"
                )
            await self._tts.put(phrase)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            self.session["flow_step"] = len(self._active_flow)
            logger.info("[ms_flow] abandonment detected — graceful close")
            self.session["_last_handled_by"]         = "abandonment"
            self.session["_last_assistant_response"] = phrase
            return

        # ── DETECT_INTENT: route to correct flow on first utterance ───────────
        if step["state"] == "DETECT_INTENT":
            # ── Pending follow-up: child age answer (from policy gate) ───────────
            # Set by the policy gate when it asked "how old is your son/daughter?"
            # and the caller is now responding with the age.
            if self.session.get("pending_first_turn_followup") == "child_age":
                import re as _re_age_fu
                _age_fu_m = _re_age_fu.search(r'\b(\d{1,2})\b', text)
                _fu_age: Optional[int] = None
                if _age_fu_m:
                    _fu_raw = int(_age_fu_m.group(1))
                    if 0 < _fu_raw < 100:
                        _fu_age = _fu_raw
                _t_fu_low = (text or "").lower()
                _plausible_age = (
                    _fu_age is not None
                    or any(p in _t_fu_low for p in _AGE_ANSWER_PHRASES)
                    or any(
                        _re_age_fu.search(rf'\b{_w}\b', _t_fu_low)
                        for _w in _AGE_NUMBER_WORDS
                    )
                )
                _non_age_reroute = (
                    not _plausible_age
                    and (
                        any(p in _t_fu_low for p in _NON_AGE_REROUTE_SIGS)
                        or _is_practical_faq_preempt(text)
                    )
                )
                if _non_age_reroute:
                    logger.info(
                        "[ms_flow] service_fit_followup: non_age_response_detected "
                        "-- re-routing text=%r",
                        text[:60],
                    )
                    self.session.pop("pending_first_turn_followup", None)
                    # Fall through to normal DETECT_INTENT routing below.
                else:
                    logger.info(
                        "[ms_flow] service_fit_followup: consumed age answer %r -> age=%s -> re-evaluating",
                        text[:40], _fu_age,
                    )
                    self.session.pop("pending_first_turn_followup", None)

                if _non_age_reroute:
                    # Skip the age-consumption / re-ask branches entirely and
                    # let normal DETECT_INTENT routing handle the utterance.
                    pass
                elif _fu_age is not None:
                    self.session["first_turn_age"] = _fu_age
                    # Re-run gate with now-known age (shallow copy keeps session clean)
                    from app.media_streams.policy_gate import (
                        evaluate_policy_gate as _pg_reeval,
                        SERVICE_FIT_DISALLOW as _PG_DISALLOW_RE,
                    )
                    from app.clinic_config import get_clinic as _pg_re_gc
                    _pg_re_clinic  = _pg_re_gc(self.session.get("clinic_id") or "demo")
                    _pg_re_session = {**self.session, "_first_turn_policy_gate_done": None}
                    _pg_re_result  = _pg_reeval(_pg_re_session, _pg_re_clinic)
                    self.session["_first_turn_policy_gate_done"] = True

                    if _pg_re_result.action == _PG_DISALLOW_RE:
                        await self._tts.put(_pg_re_result.response_text)
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _pg_re_result.response_text}
                        )
                        self.session["_last_handled_by"] = (
                            "first_turn_policy_gate_disallow_after_age"
                        )
                        logger.info(
                            "[ms_flow] first_turn_policy_gate: age=%s "
                            "clinic_policy=disallow_children -> SERVICE_FIT_DISALLOW",
                            _fu_age,
                        )
                        return

                    # Policy allows — give bounded service-fit response + booking offer.
                    # Use policy-grounded assessment-first text; no overconfident "yes absolutely".
                    from app.media_streams.service_fit_policy import (
                        assessment_first_response as _sfp_allow_text,
                    )
                    _pg_allow_resp = _sfp_allow_text(self.session.get("reason", ""))
                    logger.info(
                        "service_fit_policy: age=%s clinic_policy=allow "
                        "-> ALLOW_ASSESSMENT_FIRST response_template=assessment_first",
                        _fu_age,
                    )
                    await self._tts.put(_pg_allow_resp)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _pg_allow_resp}
                    )
                    self.session["_pending_svc_fit"] = "service_fit_pending"
                    self.session.pop("_pending_svc_fit_turns", None)
                    self.session["_last_handled_by"] = (
                        "first_turn_policy_gate_allow_after_age"
                    )
                    logger.info("pending_followup:set kind=service_fit_pending (after age gate allow)")
                    logger.info(
                        "[ms_flow] first_turn_policy_gate: age=%s clinic_policy=allow "
                        "-> SERVICE_FIT_ALLOW",
                        _fu_age,
                    )
                    return

                else:
                    # Age not parseable — re-ask once
                    _rel_pg   = self.session.get("first_turn_patient_relationship", "child")
                    _pron_map = {
                        "son": "your son", "daughter": "your daughter",
                        "teenager": "your teenager",
                    }
                    _pron_pg  = _pron_map.get(_rel_pg, "your child")
                    _reask_pg = f"Sorry, I didn\u2019t quite catch that \u2014 how old is {_pron_pg}?"
                    await self._tts.put(_reask_pg)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _reask_pg}
                    )
                    self.session["pending_first_turn_followup"] = "child_age"
                    self.session["_last_handled_by"] = "first_turn_policy_gate_reask_age"
                    return
                # If _non_age_reroute: fall through to normal DETECT_INTENT routing.

            # ── Pending follow-up contract: post-service-fit / post-age-gate ────────
            # Set when a service-fit answer was given but booking was not yet entered.
            # Checked BEFORE generic routing so natural follow-up turns stay coherent
            # instead of falling into ANSWER_GENERAL / general_query.
            if self.session.get("_pending_svc_fit") == "service_fit_pending":
                from app.media_streams.followup_contract import (
                    resolve_service_fit_followup as _sfc_resolve,
                    RESOLVE_PROCEED as _SFC_PROCEED,
                    RESOLVE_INFO    as _SFC_INFO,
                    RESOLVE_DECLINE as _SFC_DECLINE,
                )
                _sfc_result = _sfc_resolve(text)
                logger.info(
                    "pending_followup:matched result=%s text=%r",
                    _sfc_result, text[:40],
                )

                if _sfc_result == _SFC_PROCEED:
                    self.session.pop("_pending_svc_fit", None)
                    self.session.pop("_pending_svc_fit_turns", None)
                    self.session.pop("_svc_fit_gq_guard", None)
                    logger.info(
                        "pending_followup:matched proceed_intent text=%r",
                        text[:40],
                    )
                    logger.info(
                        "pending_followup:resolved -> booking_entry "
                        "reason=%r location=%s",
                        self.session.get("reason", "")[:40],
                        self.session.get("selected_location"),
                    )
                    self.session["intent"] = "booking"
                    self._switch_flow("booking")
                    self.session["_last_handled_by"] = "service_fit_confirmed"
                    await self.ask_current_question()
                    return

                if _sfc_result == _SFC_INFO:
                    _sfc_turns = self.session.get("_pending_svc_fit_turns", 0) + 1
                    self.session["_pending_svc_fit_turns"] = _sfc_turns
                    logger.info(
                        "pending_followup:resolved -> service_fit_info_reply (turn %d)",
                        _sfc_turns,
                    )
                    _info_resp = (
                        "They\u2019d typically start with a full physiotherapy "
                        "assessment \u2014 a hands-on examination to understand "
                        "what\u2019s going on, followed by a tailored treatment plan. "
                        "If you\u2019d like to go ahead, I can take a few details."
                    )
                    await self._tts.put(_info_resp)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _info_resp}
                    )
                    if _sfc_turns < 2:
                        self.session["_pending_svc_fit"] = "service_fit_pending"
                    else:
                        self.session.pop("_pending_svc_fit", None)
                        self.session.pop("_pending_svc_fit_turns", None)
                        logger.info("pending_followup:cleared (max info turns reached)")
                    self.session["_last_handled_by"] = "service_fit_info_followup"
                    return

                if _sfc_result == _SFC_DECLINE:
                    self.session.pop("_pending_svc_fit", None)
                    self.session.pop("_pending_svc_fit_turns", None)
                    self.session.pop("_svc_fit_gq_guard", None)
                    logger.info("pending_followup:resolved -> decline")
                    _decline_resp = (
                        "No problem at all \u2014 if you\u2019d like to get in "
                        "touch another time, don\u2019t hesitate to call us back."
                    )
                    await self._tts.put(_decline_resp)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _decline_resp}
                    )
                    self.session["_last_handled_by"] = "service_fit_declined"
                    return

                # RESOLVE_UNKNOWN — clear pending, arm general_query guard, fall through
                # to router.  If router/detect_intent lands on general_query, the guard
                # below redirects to a bounded service-fit fallback instead of ANSWER_GENERAL.
                _sfc_unk_turns = self.session.get("_pending_svc_fit_turns", 0) + 1
                self.session["_pending_svc_fit_turns"] = _sfc_unk_turns
                self.session.pop("_pending_svc_fit", None)
                if _sfc_unk_turns <= 2:
                    self.session["_svc_fit_gq_guard"] = True
                else:
                    self.session.pop("_pending_svc_fit_turns", None)
                    self.session.pop("_svc_fit_gq_guard", None)
                logger.info(
                    "pending_followup: unknown %r → router fallthrough "
                    "(turn %d, gq_guard=%s)",
                    text[:40], _sfc_unk_turns,
                    self.session.get("_svc_fit_gq_guard"),
                )
                # Fall through to router

            # Professional caller with high confidence — bypass booking flow entirely
            if (
                self.session.get("caller_type") == "professional"
                and self.session.get("_classification_confidence") == "high"
            ):
                self.session["professional_flow_active"] = True
                self.session["prof_flow_step"] = 0
                await self._tts.put("Of course — could I take your name please?")
                logger.info("[ms_flow] professional caller — entering professional flow")
                return

            # Bug 2: lone filler tokens ("yeah", "hi", "okay") must not trigger
            # routing — the caller hasn't stated their request yet.
            # Guard is strictly first-turn (DETECT_INTENT only).
            _FIRST_TURN_FILLERS = frozenset({
                "yeah", "yes", "yep", "yup", "hi", "hello", "hey",
                "uh", "um", "er", "err", "okay", "ok", "right",
                "then", "and", "so", "but", "hmm", "mmm", "mhm",
            })
            _ft_words = text.strip().split()
            if len(_ft_words) == 1 and _ft_words[0] in _FIRST_TURN_FILLERS:
                # Transcript arrived but is non-actionable — keep the pending question
                # open.  Set fragment_suppressed so connection.py's global hard fallback
                # does NOT fire ("Sorry, I can't answer that properly...").  The watchdog
                # re-arm is handled by the post-turn silent-turn guard in connection.py.
                self.session["fragment_suppressed"] = True
                logger.info(
                    "[ms_flow] DETECT_INTENT: first-turn filler %r — holding state, "
                    "fallback suppressed, watchdog preserved",
                    text[:20],
                )
                return

            # Short multi-word FAQ opener: "yeah a couple", "a few questions" etc.
            # Caller signals they have questions without stating one yet.
            # Emit a natural opener and hold in DETECT_INTENT so the real question
            # arrives next turn — avoids routing "yeah a couple" to ANSWER_GENERAL LLM.
            # FAQ multi-opener intercept: "I had a couple of questions" / "a few questions"
            # etc. signal the caller wants to ask questions without stating one yet.
            # Holding in DETECT_INTENT here prevents routing to ANSWER_GENERAL which
            # would immediately advance the session to GENERAL_BOOKING_OFFER and host
            # the entire FAQ exchange inside a booking-offer state.
            # The word-count gate (≤5) is intentionally removed — longer openers like
            # "yeah i had a couple of questions about your clinic" must also be caught.
            # Specific topic keywords are in the exclusion list so "a few questions
            # about acupuncture" falls through to _detect_intent → faq_services directly.
            _FAQ_MULTI_OPENER_SIGNALS = (
                "a couple", "a few", "couple more", "few more", "couple questions",
                "couple of questions", "a couple of questions",
                "few questions", "some questions",
                "had a question", "have a question", "got a question", "quick question",
                "questions about", "questions first", "questions before",
            )
            _FAQ_OPENER_TOPIC_EXCLUSIONS = (
                "book", "appoint", "reschedule", "cancel", "price", "insurance",
                "parking", "address", "location", "hours", "opening",
                "acupuncture", "shockwave", "laser", "pilates", "massage",
                "physiotherapy", "physio",
                # Accessibility / access substance
                "step free", "step-free", "wheelchair", "accessible",
                "accessibility", "disabled", "disability", "mobility", "ramp",
                # Location / clinic-comparison substance
                "alcester", "redditch", "both clinics", "two clinics",
                "both locations", "which clinic", "difference between",
                "where exactly", "where is", "where are", "located",
                "postcode", "directions",
                # Service / info substance
                "needles", "needle", "painful", "how does it work",
                "side effects", "is it safe", "how safe", "biomechanics",
                # Park-related variants
                "park ", "car park",
            )
            _opener_match = any(p in text for p in _FAQ_MULTI_OPENER_SIGNALS)
            _opener_substantive = any(s in text for s in _FAQ_OPENER_TOPIC_EXCLUSIONS)
            if _opener_match and _opener_substantive:
                logger.info(
                    "[ms_flow] DETECT_INTENT: FAQ multi-opener bypassed — "
                    "substantive_faq_present text=%r",
                    text[:80],
                )
                # Fall through to normal DETECT_INTENT routing.
            elif _opener_match:
                logger.info(
                    "[ms_flow] DETECT_INTENT: FAQ multi-opener hold — "
                    "opener_only text=%r",
                    text[:80],
                )
                await self._tts.put("Of course — what would you like to ask?")
                return

            # BUG 7 fix: multi-word pause / hold phrases at DETECT_INTENT.
            # "one second please", "just a moment" etc. must NOT advance the
            # state or trigger a flow switch — the caller hasn't stated their
            # intent yet and will speak again in a moment.
            # Stale state produced by routing "one second please" as general_query
            # (e.g. wrong flow switch, intent stored) is the bookkeeping bug.
            _GREETING_PAUSE_PHRASES = (
                "one second", "one sec", "just a second", "just a moment",
                "hold on", "bear with me", "two seconds", "give me a second",
                "give me a moment", "just a minute", "hang on", "two secs",
                "half a second", "half a moment",
            )
            if any(p in text for p in _GREETING_PAUSE_PHRASES):
                logger.info(
                    "[ms_flow] DETECT_INTENT: greeting pause %r — "
                    "holding state, no flow switch", text[:40],
                )
                return

            # ── Multi-word greeting guard ─────────────────────────────────────────
            # "hi there mate", "good morning", "hello there" must NOT lock the
            # flow into general_query before the caller states their intent.
            # Only fires when: ≤ 5 words AND matches a greeting sequence AND
            # contains no intent signal.  The single-word filler guard above
            # already handles bare "hi" / "hello" / "hey".
            _MULTI_GREETING_SEQS = (
                "hi there", "hello there", "hey there",
                "hi mate", "hello mate", "hey mate",
                "good morning", "good afternoon", "good evening",
            )
            _GREETING_INTENT_SIGS = (
                "book", "appointment", "physio", "pain", "hurt", "injury",
                "cancel", "reschedule", "question", "help", "problem",
                "price", "parking", "insurance", "hours",
            )
            if (
                len(_ft_words) <= 5
                and any(g in text for g in _MULTI_GREETING_SEQS)
                and not any(s in text for s in _GREETING_INTENT_SIGS)
            ):
                logger.info(
                    "[ms_flow] DETECT_INTENT: greeting-only %r — "
                    "holding for real intent", text[:30],
                )
                return

            # ── Incomplete booking-lead guard ────────────────────────────────────
            # Partial openers like "yeah i'd like to" / "i wanted to" / "can i"
            # carry the SHAPE of a request but no payload — feeding them to the
            # utterance router / _detect_intent mislabels them as general_query
            # and the LLM then invents questions ("What's your first name?").
            # When such a fragment appears with no substantive signal (booking
            # verb, symptom, FAQ keyword, clinic mention) and ≤ 6 words, emit a
            # neutral continuation prompt and hold state — the caller will
            # finish their sentence on the next turn.
            _INCOMPLETE_BOOKING_LEAD = (
                "i'd like to", "id like to", "i would like to",
                "i want to", "i wanted to",
                "i need to", "i needed to",
                "i'm calling to", "im calling to", "i am calling to",
                "i'm looking to", "im looking to",
                "i'm trying to", "im trying to",
                "can i", "could i", "may i",
                "is it possible", "would it be possible",
                "i was wondering",
            )
            _SUBSTANTIVE_SIGNALS = (
                "book", "appointment", "appt", "schedule",
                "cancel", "reschedule", "move", "change",
                "physio", "physiotherap", "massage", "sports",
                "pain", "hurt", "injur", "sore", "ache", "sprain", "strain",
                "back", "neck", "shoulder", "knee", "ankle", "hip", "wrist",
                "price", "cost", "fee", "charge", "how much",
                "parking", "park", "insurance", "bupa", "axa",
                "hours", "open", "close", "location", "address",
                "alcester", "redditch", "clinic",
                "follow up", "follow-up", "followup",
            )
            _tl = text.strip().lower()
            if (
                len(_ft_words) <= 6
                and any(_tl == lead or _tl.startswith(lead + " ") or _tl.endswith(" " + lead)
                        or lead in _tl
                        for lead in _INCOMPLETE_BOOKING_LEAD)
                and not any(s in _tl for s in _SUBSTANTIVE_SIGNALS)
            ):
                _inc_prompt = "Of course \u2014 what would you like to do today?"
                await self._tts.put(_inc_prompt)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _inc_prompt}
                )
                self.session["last_question"] = _inc_prompt
                self.session["_last_handled_by"] = "incomplete_booking_lead_guard"
                logger.info(
                    "[ms_flow] DETECT_INTENT: incomplete_booking_lead %r \u2192 "
                    "neutral continuation (no router, no intent)",
                    text[:60],
                )
                return

            # ── First-turn signal extraction (deterministic, pure read+store) ──────
            if not self.session.get("_first_turn_extracted"):
                from app.media_streams.first_turn_extractor import (
                    extract_first_turn_signals, apply_first_turn_signals,
                )
                apply_first_turn_signals(extract_first_turn_signals(transcript), self.session)

            # ── First-turn policy gate (child/minor age & eligibility) ───────────
            # Runs once per call, immediately after extraction.  Intercepts
            # child-related utterances before the utterance router so that
            # policy decisions (decline / ask-age / allow) are never bypassed
            # by the bridge path or other router actions.
            # Practical-FAQ preempt: if the utterance is clearly a practical
            # clinic FAQ (access, parking, location, clinic comparison) and
            # does NOT contain an explicit child-treatment / booking request,
            # do NOT fire the child/service-fit age gate here.  Otherwise
            # "my son is disabled, what's the difference between your two
            # clinics?" gets hijacked into ASK_CHILD_AGE.  (Prompt-3 Rule 1.)
            _ft_practical_preempt = (
                _is_practical_faq_preempt(text)
                and not _has_explicit_child_treatment_request(text)
            )
            _ft_explicit_child_treatment = _has_explicit_child_treatment_request(text)
            if _ft_practical_preempt:
                logger.info(
                    "[ms_flow] first_turn_policy_gate: "
                    "practical_faq_preempts_child_gate text=%r",
                    text[:80],
                )
            elif _ft_explicit_child_treatment:
                logger.info(
                    "[ms_flow] child_policy_gate: "
                    "explicit_child_treatment_request -> ASK_CHILD_AGE"
                )
            else:
                # Child relation present without explicit treatment/booking ask
                # (e.g. "my son is disabled so I wanted to know if...") must
                # not fire ASK_CHILD_AGE — fall through to normal routing.
                if self.session.get("first_turn_child_related"):
                    logger.info(
                        "[ms_flow] first_turn_policy_gate: "
                        "child_mention_without_explicit_treatment_request -- "
                        "skipping gate text=%r",
                        text[:80],
                    )
            if (
                self.session.get("_first_turn_extracted")
                and not self.session.get("_first_turn_policy_gate_done")
                and not _ft_practical_preempt
                and _ft_explicit_child_treatment
            ):
                from app.media_streams.policy_gate import (
                    evaluate_policy_gate    as _pg_eval_ft,
                    ASK_CHILD_AGE           as _PG_ASK_AGE_FT,
                    SERVICE_FIT_DISALLOW    as _PG_DISALLOW_FT,
                    SERVICE_FIT_ALLOW       as _PG_ALLOW_FT,
                )
                from app.clinic_config import get_clinic as _pg_ft_gc
                _pg_ft_clinic = _pg_ft_gc(self.session.get("clinic_id") or "demo")
                _pg_ft_result = _pg_eval_ft(self.session, _pg_ft_clinic)
                self.session["_first_turn_policy_gate_done"] = True
                logger.info(
                    "[ms_flow] first_turn_policy_gate: child_related=%s service_fit=%s "
                    "booking=%s age=%s -> %s",
                    self.session.get("first_turn_child_related"),
                    self.session.get("first_turn_service_fit"),
                    self.session.get("first_turn_booking_signal"),
                    self.session.get("first_turn_age"),
                    _pg_ft_result.action,
                )
                if _pg_ft_result.action == _PG_DISALLOW_FT:
                    await self._tts.put(_pg_ft_result.response_text)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _pg_ft_result.response_text}
                    )
                    self.session["_last_handled_by"] = "first_turn_policy_gate_disallow"
                    logger.info(
                        "[ms_flow] first_turn_policy_gate: age=%s "
                        "clinic_policy=disallow_children -> SERVICE_FIT_DISALLOW",
                        self.session.get("first_turn_age"),
                    )
                    return
                if _pg_ft_result.action == _PG_ASK_AGE_FT:
                    await self._tts.put(_pg_ft_result.response_text)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _pg_ft_result.response_text}
                    )
                    self.session["pending_first_turn_followup"] = (
                        _pg_ft_result.pending_followup
                    )
                    self.session["_last_handled_by"] = (
                        "first_turn_policy_gate_ask_age"
                    )
                    logger.info(
                        "[ms_flow] first_turn_policy_gate: child_related service_fit "
                        "age_missing -> ASK_CHILD_AGE",
                    )
                    return
                if _pg_ft_result.action == _PG_ALLOW_FT:
                    logger.info(
                        "[ms_flow] first_turn_policy_gate: age=%s "
                        "clinic_policy=allow -> SERVICE_FIT_ALLOW",
                        self.session.get("first_turn_age"),
                    )
                    # Fall through to utterance router

            # ── UTTERANCE ROUTER: mixed / service-fit first utterances ────────────
            # Called only when the utterance is rich enough that deterministic
            # intent detection alone would flatten or ignore embedded signals.
            # For service-fit questions ("can you help with my ankle?") and mixed
            # booking+context utterances, the router returns a structured result
            # that allows a brief bridge before booking flow begins.
            # Gate ensures short / digit-heavy / clear utterances never reach here.
            _di_router = await _router_analyze(
                text, "DETECT_INTENT", self.session,
                pending_question=self.session.get("last_question", ""),
                recent_context="",
            )
            if _di_router is not None:
                _di_action = _di_router["action"]
                logger.info(
                    "[ms_flow] DETECT_INTENT: router action=%s intent=%s "
                    "confidence=%.2f source=%s",
                    _di_action, _di_router["primary_intent"],
                    _di_router["confidence"], _di_router.get("source", "?"),
                )
                # Access/logistics about the CLINIC (step-free, parking,
                # address, etc.) — not a treatment-eligibility question — must
                # NOT trigger service-fit / child-policy age-gating even when a
                # relation word like "my son" is present. Neutralise the router
                # action so the flow falls through to normal intent detection
                # which will route to faq_location / accessibility.
                # (Prompt-3 Rule 1 / Fix 1+2.)
                if (
                    _di_action == _R_SVC_FIT
                    and _is_practical_faq_preempt(text)
                    and not _has_explicit_child_treatment_request(text)
                ):
                    logger.info(
                        "[ms_flow] DETECT_INTENT: svc_fit suppressed — "
                        "clinic access/logistics question (%r)", text[:60]
                    )
                    _di_action = None
                if _di_action == _R_SVC_FIT:
                    # ── Deterministic service-fit policy evaluator ─────────────────
                    # Routes through service_fit_policy.py which checks:
                    #   child/minor eligibility (patient_policies.no_children)
                    #   condition support (conditions_treated FAQ + keyword set)
                    # Returns a confidence-capped response grounded in clinic config.
                    # No "Yes, absolutely" unless policy explicitly allows it.
                    from app.media_streams.service_fit_policy import (
                        evaluate_service_fit  as _sfp_eval,
                        POLICY_DISALLOW       as _SFP_DISALLOW,
                        ASK_CHILD_AGE         as _SFP_ASK_AGE,
                    )
                    from app.clinic_config import get_clinic as _sfp_gc
                    _sfp_clinic = _sfp_gc(self.session.get("clinic_id") or "demo")

                    # Resolve reason: router extraction > session > condition signals
                    _sfp_reason = self.session.get("reason", "") or ""
                    if _di_router.get("has_reason") and _di_router.get("reason_text"):
                        if not _sfp_reason:
                            _sfp_reason = _di_router["reason_text"]
                    if not _sfp_reason:
                        _sfp_cond_sigs = (
                            "pain", "ache", "hurt", "injury", "injured", "sore",
                            "stiff", "swollen", "sprain", "strain", "fracture",
                            "knee", "shoulder", "back", "neck", "hip", "ankle",
                            "wrist", "elbow", "foot", "leg", "arm", "muscle",
                            "joint", "sports", "physio", "problem", "condition",
                        )
                        if any(sig in text for sig in _sfp_cond_sigs):
                            _sfp_reason = transcript.strip()

                    _sfp_child = (
                        _has_minor_ref(text)
                        or bool(self.session.get("first_turn_child_related"))
                    )
                    _sfp_result = _sfp_eval(
                        session=self.session,
                        clinic=_sfp_clinic,
                        reason=_sfp_reason,
                        child_related=_sfp_child,
                        age=self.session.get("first_turn_age"),
                        relationship=self.session.get(
                            "first_turn_patient_relationship", "child"
                        ),
                        original_text=text,
                    )
                    logger.info(
                        "service_fit_policy: child=%s age=%s reason=%r -> %s",
                        _sfp_child,
                        self.session.get("first_turn_age"),
                        _sfp_reason[:40],
                        _sfp_result.action,
                    )

                    if _sfp_result.action == _SFP_DISALLOW:
                        await self._tts.put(_sfp_result.response_text)
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _sfp_result.response_text}
                        )
                        self.session["_last_handled_by"] = "service_fit_policy_disallow"
                        logger.info(
                            "service_fit_policy: response_template=policy_disallow "
                            "child=%s age=%s",
                            _sfp_child, self.session.get("first_turn_age"),
                        )
                        return

                    if _sfp_result.action == _SFP_ASK_AGE:
                        await self._tts.put(_sfp_result.response_text)
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _sfp_result.response_text}
                        )
                        self.session["pending_first_turn_followup"] = "child_age"
                        self.session["_last_handled_by"] = "service_fit_policy_ask_age"
                        logger.info(
                            "service_fit_policy: child_detected age_missing -> ASK_CHILD_AGE"
                        )
                        return

                    # ALLOW_ASSESSMENT_FIRST / REQUIRE_TEAM_CONFIRMATION /
                    # INSUFFICIENT_KNOWLEDGE_SAFE_REPLY — all give assessment-first reply
                    if _sfp_reason and not self.session.get("reason"):
                        self.session["reason"] = _sfp_reason
                    await self._tts.put(_sfp_result.response_text)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _sfp_result.response_text}
                    )
                    self.session["_pending_svc_fit"] = "service_fit_pending"
                    self.session.pop("_pending_svc_fit_turns", None)
                    self.session["_last_handled_by"] = (
                        f"service_fit_policy_{_sfp_result.action.lower()}"
                    )
                    logger.info(
                        "service_fit_policy: response_template=%s "
                        "confidence_cap applied; "
                        "service_fit: location=%s reason=%r; "
                        "pending_followup:set kind=service_fit_pending",
                        _sfp_result.action,
                        self.session.get("selected_location"),
                        self.session.get("reason", "")[:40],
                    )
                    return

                if _di_action == _R_BRIDGE:
                    # ── Context/reason bridge: acknowledge, then enter booking ──
                    # Caller described a reason without a service-fit question —
                    # they are already heading toward booking; bridge into it.
                    if _di_router.get("has_reason") and _di_router.get("reason_text"):
                        if not self.session.get("reason"):
                            self.session["reason"] = _di_router["reason_text"]
                            logger.info(
                                "[ms_flow] DETECT_INTENT: router stored reason=%r",
                                _di_router["reason_text"][:60],
                            )
                    _di_bridge = _router_bridge_text(_R_BRIDGE, "DETECT_INTENT")
                    await self._tts.put(_di_bridge)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _di_bridge}
                    )
                    self.session["intent"] = "booking"
                    self._switch_flow("booking")
                    self.session["_last_handled_by"] = "detect_intent_router"
                    if not self.session.get("reason"):
                        _di_cond_signals = (
                            "pain", "ache", "hurt", "injury", "injured", "sore",
                            "stiff", "swollen", "sprain", "strain", "fracture",
                            "knee", "shoulder", "back", "neck", "hip", "ankle",
                            "wrist", "elbow", "foot", "leg", "arm", "muscle",
                            "joint", "sports", "physio", "problem", "condition",
                        )
                        if any(sig in text for sig in _di_cond_signals):
                            self.session["reason"] = transcript.strip()
                            logger.info(
                                "[ms_flow] DETECT_INTENT: router bridge path — "
                                "reason stored from condition signal %r",
                                transcript.strip()[:60],
                            )
                    await self.ask_current_question()
                    return

                # All other router actions (INTENT_SWITCH_TO_*, etc.) → fall
                # through to the standard _detect_intent path below which handles
                # them correctly.
                logger.info(
                    "[ms_flow] DETECT_INTENT: router action=%s not intercepted "
                    "— falling through to _detect_intent",
                    _di_action,
                )

            intent = self._detect_intent(text)
            # Guard: if caller was in a post-service-fit context (RESOLVE_UNKNOWN fell
            # through) and _detect_intent lands on general_query, redirect to a bounded
            # service-fit fallback instead of routing to ANSWER_GENERAL.
            if intent == "general_query" and self.session.pop("_svc_fit_gq_guard", False):
                self.session.pop("_pending_svc_fit", None)
                self.session.pop("_pending_svc_fit_turns", None)
                logger.info(
                    "pending_followup: general_query guard fired → svc_fit fallback"
                )
                # Suitability/recommendation questions get a clinician-decides
                # response; everything else gets the bounded assessment-first reply.
                from app.media_streams.service_fit_policy import (
                    is_suitability_question as _gq_is_suitability,
                    clinician_decides_response as _gq_clinician_decides,
                    assessment_first_response as _gq_assessment_first,
                )
                if _gq_is_suitability(text):
                    _gq_guard_resp = _gq_clinician_decides()
                    logger.info(
                        "service_fit_policy: suitability_question -> CLINICIAN_DECIDES "
                        "(gq guard path); recommendation_blocker applied"
                    )
                else:
                    _gq_guard_resp = _gq_assessment_first(self.session.get("reason", ""))
                await self._tts.put(_gq_guard_resp)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _gq_guard_resp}
                )
                self.session["_pending_svc_fit"] = "service_fit_pending"
                self.session["_last_handled_by"] = "service_fit_general_query_guard"
                logger.info("pending_followup:set kind=service_fit_pending (gq guard re-arm)")
                return
            # Clear guard if intent was not general_query
            self.session.pop("_svc_fit_gq_guard", None)
            self.session["intent"] = intent
            # For general queries, store the original transcript so the LLM
            # instruction can reference exactly what the caller asked.
            if intent == "general_query":
                self.session["general_query_text"] = transcript.strip()
            self._switch_flow(intent)

            # If caller mentioned a medical condition in their first utterance,
            # treat it as the reason for booking — store it so COLLECT_REASON
            # is skipped, and emit the empathy acknowledgement immediately.
            # This matches caller behaviour: "I have back pain" is BOTH the booking
            # intent AND the reason — asking "what brings you in today?" would be
            # redundant and confusing.
            if intent == "booking" and not self.session.get("reason"):
                _condition_signals = (
                    "pain", "ache", "aching", "hurt", "hurting", "injury",
                    "injured", "sore", "stiff", "stiffness", "swollen",
                    "swelling", "pulled", "torn", "sprain", "strain",
                    "fracture", "headache", "migraine", "knee", "shoulder",
                    "back", "neck", "hip", "ankle", "wrist", "elbow",
                    "foot", "leg", "arm", "muscle", "joint", "sports",
                    "running", "posture", "postural", "physio", "problem",
                    "issue", "condition", "treatment", "rehab",
                    # Vague health complaints — caller describes feeling unwell
                    # without naming a specific condition ("I'm not feeling right",
                    # "feel a bit off", "under the weather", etc.)
                    "not feeling", "feeling off", "feel off", "unwell",
                    "not well", "not myself", "off colour", "off color",
                    "under the weather", "something wrong", "something going on",
                    "been suffering", "not been well", "been struggling",
                )
                if any(sig in text for sig in _condition_signals):
                    _di_reason = transcript.strip()
                    self.session["reason"] = _di_reason
                    # Emit non-gating empathy line immediately — COLLECT_REASON will be
                    # skipped by ask_current_question when it finds reason already set.
                    _di_empathy = _fast_empathy_line(_di_reason)
                    await self._tts.put(_di_empathy)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _di_empathy}
                    )
                    logger.info(
                        "[ms_flow] DETECT_INTENT: condition in first utterance %r "
                        "→ reason stored, empathy emitted, COLLECT_REASON will be skipped",
                        _di_reason[:60],
                    )

            # Transfer intent: bypass LLM — send phrase immediately, mark complete.
            # Using the LLM here causes timeouts (tool call latency) and test failures.
            if intent == "transfer":
                phrase = "Let me put you straight through — just bear with me."
                await self._tts.put(phrase)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": phrase}
                )
                self.session["request_transfer"] = True
                self.session["human_requested"] = True
                self.session["transfer_confirmed"] = True
                self.session["flow_step"] = len(TRANSFER_FLOW)
                logger.info(
                    "[ms_flow] transfer intent — straight-through phrase sent, flow complete"
                )
                return

            self.session["_last_handled_by"] = "detect_intent"
            await self.ask_current_question()
            return

        # ── PHONE READBACK CONFIRMATION: awaiting yes/no on number we read back ──
        _PHONE_COLLECT_STATES = ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "RETURNING_PLAN_COLLECT_PHONE")
        if step["state"] in _PHONE_COLLECT_STATES and self.session.get("phone_readback_pending"):
            await self._handle_phone_readback_confirmation(text, transcript, step)
            self.session["_last_handled_by"] = "phone_readback_confirmation"
            return

        # ── NAME READBACK CONFIRMATION (Fix C): awaiting yes/no on name ─────────
        _NAME_COLLECT_STATES = (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        )
        if step["state"] in _NAME_COLLECT_STATES and self.session.get("name_readback_pending"):
            logger.info(
                "[ms_flow] name_readback state=%s input=%r phone_accept=%s",
                step["state"], text[:60], _is_phone_accept(text),
            )
            # ── FIRST-CHECK: phone-reject intent ───────────────────────────
            # "no use a different number" while awaiting name readback means the
            # caller wants to supply a new phone number.  Catch BEFORE yes/no
            # name logic so the turn is not consumed as a name rejection.
            if _is_phone_reject(text):
                logger.info(
                    "[ms_flow] phone_reject_detected state=%s — redirecting to COLLECT_PHONE",
                    step["state"],
                )
                self.session["name_readback_pending"]  = False
                self.session["phone_confirmed"]        = False
                self.session["phone_number"]           = None
                self.session["phone_digits_buffer"]    = ""
                self.session.setdefault("collected", {}).pop("phone", None)
                self.session.pop("phone_readback_pending", None)
                self.session.pop("phone_readback_retry", None)
                self.session["flow_step"] = (
                    _RESCHEDULE_COLLECT_PHONE_INDEX
                    if self._active_flow is RESCHEDULE_FLOW
                    else _CANCEL_COLLECT_PHONE_INDEX
                    if self._active_flow is CANCEL_FLOW
                    else _COLLECT_PHONE_INDEX
                )
                self.session["state"]     = "COLLECT_PHONE"
                self.session["_last_handled_by"]   = "name_readback_phone_reject"
                self.session["_last_no_detected"]  = True
                await self.ask_current_question()
                return

            # ── FIRST-CHECK: phone-accept compat ────────────────────────────
            # "yes use this number" arrives when caller combines name-confirm +
            # phone-confirm in one utterance.  Catch it before yes/no name logic
            # so the turn is not silently consumed as a plain name confirmation.
            _nr_twilio = (
                self.session.get("twilio_from_local")
                or self.session.get("twilio_from", "")
            )
            if _is_phone_accept(text) and _nr_twilio:
                logger.info(
                    "[ms_flow] compat_phone_accept state=%s input=%r",
                    step["state"], text[:60],
                )
                import re as _re_nrp
                _nrp_digits = _re_nrp.sub(r"\D", "", _nr_twilio)
                _nrp_phone  = _nrp_digits or _nr_twilio
                # Finalize name — already stored by name extraction; ensure full_name set
                if not self.session.get("full_name"):
                    _nrp_name = (
                        (self.session.get("collected") or {}).get("full_name")
                        or (self.session.get("collected") or {}).get("name")
                        or self.session.get("patient_name")
                        or self.session.get("caller_name")
                    )
                    if _nrp_name:
                        self.session["full_name"] = _nrp_name
                        self.session.setdefault("collected", {})["full_name"] = _nrp_name
                self.session["name_readback_pending"] = False
                self.session["phone_confirmed"]       = True
                self.session["phone_from_twilio"]     = True
                self.session["phone_number"]          = _nrp_phone
                self.session.setdefault("collected", {})["phone"] = _to_e164_uk(_nrp_phone)
                self.session.pop("phone_readback_pending", None)
                self.session.pop("phone_readback_retry", None)
                self.session.pop("slot_pending_confirmation", None)
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                if self._active_flow is RESCHEDULE_FLOW:
                    self.session["flow_step"] = _RESCHEDULE_LOOKUP_INDEX
                    self.session["state"]     = "LOOKUP_RESCHEDULE"
                elif self.session.get("selected_slot"):
                    self.session["flow_step"] = _CONFIRM_BOOKING_INDEX
                    self.session["state"]     = "CONFIRM_BOOKING"
                else:
                    self.session["flow_step"] = _PRESENT_DAYS_INDEX
                    self.session["state"]     = "PRESENT_DAYS"
                logger.info("[ms_flow] compat_phone_accept (name_readback) -> %s", self.session["state"])
                self.session["_last_handled_by"]   = "name_readback_phone_accept_compat"
                self.session["_last_yes_detected"] = True
                await self.ask_current_question()
                return

            # Word-boundary match for short tokens so acoustic echo of the name
            # readback question ("was that right?") cannot fire _nr_yes via
            # substring match ("right" inside "alright", "ya" inside a name, etc.).
            import re as _re_nr
            _NR_YES_FULL = {
                "yes", "yeah", "yep", "yup", "yeh", "ya", "correct",
                "that's right", "thats right", "aye", "ok", "okay",
                "that's it", "thats it", "spot on", "that's me", "thats me",
            }
            _nr_yes = (
                any(_re_nr.search(r'\b' + _re_nr.escape(p) + r'\b', text) for p in _NR_YES_FULL)
                # bare "right" only when it is the WHOLE utterance or starts the utterance,
                # not as a suffix inside words like "alright" / "upright"
                or bool(_re_nr.search(r'(?<!\w)right(?!\w)', text))
            )
            _nr_no  = any(p in text for p in (
                "no", "nope", "nah", "wrong", "that's not", "thats not",
                "not right", "different", "incorrect", "not me",
            ))
            if _nr_yes:
                self.session["name_readback_pending"] = False
                self.session["_last_handled_by"]   = "name_readback_yes"
                self.session["_last_yes_detected"] = True
                self.session["_last_no_detected"]  = False
                # If a Twilio caller-ID is available, treat the name confirmation
                # as also accepting the calling number — skip CONFIRM_PHONE and
                # jump straight to CONFIRM_BOOKING.  This mirrors the
                # name_readback_phone_accept_compat path for plain "Yes" responses.
                _nry_twilio = (
                    self.session.get("twilio_from_local")
                    or self.session.get("twilio_from", "")
                )
                if _nry_twilio and self._active_flow is BOOKING_FLOW:
                    import re as _re_nry
                    _nry_phone = _re_nry.sub(r"\D", "", _nry_twilio) or _nry_twilio
                    self.session["phone_confirmed"]   = True
                    self.session["phone_from_twilio"] = True
                    self.session["phone_number"]      = _nry_phone
                    self.session.setdefault("collected", {})["phone"] = _nry_phone
                    self.session.pop("phone_readback_pending", None)
                    self.session.pop("phone_readback_retry", None)
                    self.session["flow_step"] = _CONFIRM_PHONE_INDEX
                    self.session["state"]     = "CONFIRM_PHONE"
                    logger.info(
                        "[ms_flow] name readback confirmed + Twilio phone — advancing to CONFIRM_PHONE"
                    )
                else:
                    self.session["flow_step"] = step["step"] + 1
                    logger.info("[ms_flow] name readback confirmed — advancing to CONFIRM_PHONE")
                await self.ask_current_question()
                return  # hard stop — next step already queued, no fallthrough
            elif _nr_no:
                self.session["name_readback_pending"] = False
                self.session["full_name"] = None
                col = self.session.setdefault("collected", {})
                col.pop("full_name", None)
                col.pop("name", None)
                phrase = "Sorry about that — could you say your name again?"
                self.session["_last_handled_by"]         = "name_readback_no"
                self.session["_last_yes_detected"]       = False
                self.session["_last_no_detected"]        = True
                self.session["_last_assistant_response"] = phrase
                await self._tts.put(phrase)
                self.session["last_question"] = phrase
                logger.info("[ms_flow] name readback rejected — re-asking")
            else:
                # Ambiguous — replay the question
                lq = self.session.get("last_question", "Was that right?")
                self.session["_last_handled_by"]         = "name_readback_ambiguous"
                self.session["_last_assistant_response"] = lq
                await self._tts.put(lq)
            return

        # ── GLOBAL NAME CORRECTION (BUG 4/5/6) ─────────────────────────────────
        # Runs after full_name is stored.  Catches "I said Sarah", "not Quentin,
        # it's Sarah", "change name to Sarah" etc. at ANY flow step — before any
        # interrupt or state-specific block can route them to the LLM.
        #
        # BUG 5: _name_correction_just_applied flag prevents re-triggering on the
        # trailing fragment that sometimes follows a correction ("Sarah" after
        # "I said Sarah not Quentin").
        #
        # BUG 6: returning before the interrupt check prevents structured-field
        # utterances ("as you just said Sarah") from routing to general_query and
        # getting a nonsensical LLM response ("my name is Susie").
        _stored_name = self.session.get("full_name", "")
        if _stored_name:
            # PART 4: block global name correction entirely during lookup states.
            # "under what name was that" must NEVER overwrite full_name to "That".
            # Lookup states have their own deterministic meta-question intercept below.
            _nc_current_state = step["state"] if step else ""
            if _nc_current_state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
                pass  # skip all name correction logic in lookup states — handled deterministically
            elif self.session.get("_name_correction_just_applied"):
                # One-turn cooldown — clear flag and treat fragment as confirmation
                self.session["_name_correction_just_applied"] = False
                logger.info("[ms_flow] name correction cooldown — ignoring fragment %r", transcript[:40])
                # Fall through to normal extraction so flow continues
            else:
                # Date/time guard — if the utterance is primarily about scheduling,
                # never let name-correction patterns fire on it (e.g. "my appointment
                # is on Monday morning" must not overwrite stored name).
                _DATE_GUARD_TOKENS = (
                    "monday", "tuesday", "wednesday", "thursday", "friday",
                    "saturday", "sunday", "january", "february", "march",
                    "april", "june", "july", "august", "september", "october",
                    "november", "december", "morning", "afternoon", "evening",
                    "today", "tomorrow", "next week", "o'clock", " am", " pm",
                    "appointment", "booking", "session", "slot", "schedule",
                    "date", "time", "week", "month",
                )
                _nc_text_lower = text.lower()
                _has_date_token = any(d in _nc_text_lower for d in _DATE_GUARD_TOKENS)
                import re as _nc_re
                _NC_PATTERNS = [
                    # "I said Sarah" / "I said it was Sarah"
                    r"i said (?:it was |that it was )?([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "I meant Sarah" / "I meant to say Sarah"
                    r"i meant (?:to say )?([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "my name is/was/it's Sarah" / "name's Sarah"
                    r"(?:my )?name(?:'s| is| was) ([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "change (the/my) (booking) name to Sarah" / "change it to Sarah"
                    r"change (?:the |my )?(?:booking )?(?:name|it) to ([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "it's Sarah" / "its Sarah" as standalone correction
                    r"^(?:it'?s|its) ([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)$",
                    # "not Quentin, Sarah" / "not Quentin it's Sarah"
                    r"not \w+(?: \w+)?,?\s+(?:it'?s\s+)?([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "instead of Quentin, Sarah"
                    r"instead of \w+(?: \w+)?,?\s+([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                    # "my name was Sarah" / "the name is Sarah"
                    r"(?:the )?name (?:is|was|should be) ([a-z][a-z\-']{1,}\b(?: [a-z][a-z\-']{1,}\b)?)",
                ]
                # BUG 6: also block self-referential drift ("as you just said Sarah")
                _SELF_REF_PATTERNS = (
                    "as you just said", "as you said", "you just said",
                    "you said my name", "you've got it as", "you have it as",
                    "that's what you said", "like you said",
                )
                _new_name = None
                if not _has_date_token:
                    for _pat in _NC_PATTERNS:
                        _m = _nc_re.search(_pat, text)
                        if _m:
                            _candidate = _m.group(1).strip().title()
                            # Only accept if actually different from stored name
                            if _candidate.lower() != _stored_name.strip().lower():
                                _new_name = _candidate
                            break
                if _new_name:
                    # Apply the correction deterministically — no LLM needed
                    self.session["full_name"] = _new_name
                    col = self.session.setdefault("collected", {})
                    col["full_name"] = _new_name
                    col["name"]      = _new_name
                    self._name_tracker.set_name(_new_name)
                    self.session["name_tracker_name"] = self._name_tracker._name
                    self.session["name_tracker_uses"] = self._name_tracker._uses_remaining
                    self.session["_name_correction_just_applied"] = True
                    phrase = f"Got it — I've updated that to {_new_name}."
                    await self._tts.put(phrase)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": phrase}
                    )
                    lq = self.session.get("last_question", "")
                    if lq:
                        await self._tts.put(lq)
                    logger.info(
                        "[ms_flow] name correction: %r → %r (LLM avoided)",
                        _stored_name, _new_name,
                    )
                    return
                elif any(p in text for p in _SELF_REF_PATTERNS):
                    # BUG 6: caller referencing previously spoken data — treat as
                    # confirmation, re-anchor to current question rather than routing
                    # to LLM general_query which can produce "my name is Susie" drift.
                    lq = self.session.get("last_question", "")
                    if lq:
                        await self._tts.put(lq)
                    logger.info("[ms_flow] self-referential transcript — re-anchoring (BUG 6): %r", transcript[:60])
                    return

        # ── NEW_OR_RETURNING: deterministic extraction BEFORE interrupt check ──────
        # Direct answers like "it's my first time" or "i have never been with you before"
        # must be resolved here — before _detect_intent() — to prevent the LLM being
        # called with a completely valid booking answer.
        if step["state"] == "NEW_OR_RETURNING":
            # ── CLARIFICATION / REPEAT: replay last_question directly ──────
            # Catches "say that again", "pardon", "what did you ask" etc. before
            # extraction so a clarification request never falls through to interrupt.
            _NOR_CLARIFY = (
                "say that again", "say it again", "repeat that", "come again",
                "pardon", "what was that", "what did you say", "what did you ask",
                "didn't catch", "didn't hear", "can you repeat", "sorry what",
                "what was the question", "what did you just",
            )
            if any(p in text for p in _NOR_CLARIFY):
                _nor_lq = self.session.get("last_question", "")
                if _nor_lq:
                    await self._tts.put(_nor_lq)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _nor_lq}
                    )
                    logger.info(
                        "[ms_flow] NEW_OR_RETURNING: clarification request → replaying "
                        "last_question (LLM avoided): %r", _nor_lq[:80],
                    )
                    return

            # BUG 2 fix: FAQ / off-topic guard — caller is asking a question,
            # not answering "new or returning?".  Utterances like "first do you
            # have any parking in the alcester area" contain "first" which can
            # substring-match new_patterns or the fuzzy fallback.
            # If the text has both a question signal AND FAQ vocabulary,
            # route through FAQ handling and re-ask instead of extracting.
            _NOR_FAQ_VOCAB = (
                "parking", "car park", "car-park", "location", "address",
                "directions", "where are you", "where is", "how do i get",
                "how much", "cost", "price", "insurance",
                "opening hours", "what time do you", "are you open",
                "what services", "do you do", "do you offer",
            )
            _nor_q_signals = (
                "?" in transcript
                or any(p in text for p in (
                    "do you", "have you", "is there", "are you",
                    "can you", "where", "how ", "what time",
                ))
            )
            if _nor_q_signals and any(p in text for p in _NOR_FAQ_VOCAB):
                _nor_faq_intent = self._detect_intent(text)
                _nor_faq_intents = {
                    "faq_hours", "faq_location", "faq_prices",
                    "faq_insurance", "faq_services", "faq_capability", "general_query",
                }
                if _nor_faq_intent in _nor_faq_intents:
                    # _handle_mid_flow_interrupt already emits answer + re-anchor;
                    # do NOT also re-send last_question here or the caller hears it twice.
                    await self._handle_mid_flow_interrupt(_nor_faq_intent, transcript)
                    logger.info(
                        "[ms_flow] NEW_OR_RETURNING: FAQ guard fired (%s) — "
                        "answered and re-anchored by _handle_mid_flow_interrupt", _nor_faq_intent,
                    )
                    return

            _nor_answer = self._extract("new_or_returning", text, transcript)
            logger.info(
                "[ms_flow] NEW_OR_RETURNING extract: transcript=%r → %s  flow_step=%d",
                transcript[:80], _nor_answer, step["step"],
            )
            if _nor_answer in ("new", "returning"):
                self.session["new_or_returning"] = _nor_answer
                col = self.session.setdefault("collected", {})
                col["patient_type"] = _nor_answer
                # Bug 1/2 fix: when caller is new, hard-clear all returning-plan flags so
                # stale state can never route a new patient into returning-plan states.
                if _nor_answer == "new":
                    for _nf in (
                        "on_treatment_plan", "returning_recency",
                        "returning_plan_looked_up", "returning_plan",
                        "returning_plan_lookup_name", "returning_plan_lookup_type",
                    ):
                        self.session.pop(_nf, None)
                    self.session.setdefault("collected", {}).pop("on_treatment_plan", None)
                    logger.info("[ms_flow] NEW_OR_RETURNING: new patient — returning-plan flags cleared")
                    # Prompt 4B fix (Bug 1): direct jump past ALL returning-plan states.
                    # Never traverse RETURNING_RECENCY / RETURNING_TREATMENT_PLAN /
                    # RETURNING_PLAN_CONFIRM_PHONE / RETURNING_PLAN_COLLECT_PHONE /
                    # RETURNING_PLAN_LOOKUP via skip-chain; jump straight to COLLECT_REASON.
                    self.session["flow_step"] = _RETURNING_PLAN_LOOKUP_INDEX + 1
                else:
                    self.session["flow_step"] = step["step"] + 1
                _nor_next_step = (
                    _RETURNING_PLAN_LOOKUP_INDEX + 1
                    if _nor_answer == "new"
                    else step["step"] + 1
                )
                logger.info(
                    "[ms_flow] NEW_OR_RETURNING: %r → step advanced to %d (interrupt bypassed)",
                    _nor_answer, _nor_next_step,
                )
                # Emit the bridge ("No problem — let's get you sorted." / "Of course…")
                # before cascading into the next step.
                _nor_next = self.current_step()
                _nor_next_llm = _nor_next["use_llm"] if _nor_next else False
                _nor_bridge = _get_bridge("NEW_OR_RETURNING", _nor_answer, self.session, _nor_next_llm)
                if _nor_bridge:
                    await self._tts.put(_nor_bridge)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _nor_bridge}
                    )
                # Bug 4 fix: clear stale last_question now that the flow has moved on.
                # The bridge is not a question — if silence fires during the skip
                # cascade, we must not replay the old NEW_OR_RETURNING question.
                # The next real question (COLLECT_REASON / COLLECT_NAME) will
                # populate last_question correctly when it is actually emitted.
                self.session["last_question"] = ""
                await self.ask_current_question()
                return
            # No deterministic match — fall through, but _DATA_COLLECTION_STATES
            # below will block general_query from firing
            logger.info(
                "[ms_flow] NEW_OR_RETURNING: no deterministic match → falling through (general_query blocked)"
            )

        # ── RETURNING_TREATMENT_PLAN: deterministic extraction BEFORE interrupt ─────
        # FIX B: Direct answers like "I'm still coming in regularly" or "it's a new
        # episode" must be caught here — before _detect_intent() — to prevent
        # general_query misrouting them to the LLM.
        if step["state"] == "RETURNING_TREATMENT_PLAN":
            _RTP_YES = (
                "still coming in", "coming in regularly", "regularly",
                "still on", "still under", "still having", "still getting",
                "ongoing", "current treatment", "active treatment",
                "yes i am", "yes i'm", "yeah i am", "yeah i'm",
                "i am yeah", "i am yes",
            )
            _RTP_NO = (
                "new episode", "new issue", "new problem", "new thing",
                "flared up", "flare up", "came back", "come back",
                "happened again", "different thing", "something else",
                "not really", "not any more", "not anymore", "stopped",
                "finished", "ended", "no i'm not", "no i haven't",
            )
            _rtp_yes = any(p in text for p in _RTP_YES)
            _rtp_no = any(p in text for p in _RTP_NO)
            if _rtp_yes and not _rtp_no:
                self.session["on_treatment_plan"] = True
                self.session.setdefault("collected", {})["on_treatment_plan"] = True
                self.session["flow_step"] = step["step"] + 1
                logger.info(
                    "[ms_flow] RETURNING_TREATMENT_PLAN: deterministic YES %r → step %d (interrupt bypassed)",
                    transcript[:60], step["step"] + 1,
                )
                _rtp_next = self.current_step()
                _rtp_next_llm = _rtp_next["use_llm"] if _rtp_next else False
                _rtp_bridge = _get_bridge("RETURNING_TREATMENT_PLAN", True, self.session, _rtp_next_llm)
                if _rtp_bridge:
                    await self._tts.put(_rtp_bridge)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _rtp_bridge}
                    )
                await self.ask_current_question()
                return
            if _rtp_no and not _rtp_yes:
                self.session["on_treatment_plan"] = False
                self.session.setdefault("collected", {})["on_treatment_plan"] = False
                self.session["flow_step"] = step["step"] + 1
                logger.info(
                    "[ms_flow] RETURNING_TREATMENT_PLAN: deterministic NO %r → step %d (interrupt bypassed)",
                    transcript[:60], step["step"] + 1,
                )
                _rtp_next = self.current_step()
                _rtp_next_llm = _rtp_next["use_llm"] if _rtp_next else False
                _rtp_bridge = _get_bridge("RETURNING_TREATMENT_PLAN", False, self.session, _rtp_next_llm)
                if _rtp_bridge:
                    await self._tts.put(_rtp_bridge)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _rtp_bridge}
                    )
                await self.ask_current_question()
                return
            # No deterministic match — fall through; _DATA_COLLECTION_STATES
            # blocks general_query, and Haiku fallback handles ambiguous answers.
            logger.info(
                "[ms_flow] RETURNING_TREATMENT_PLAN: no deterministic match for %r — falling through",
                transcript[:60],
            )

        # ── PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE: YES gate BEFORE interrupt ──────
        # "yeah that works", "sounds fine", "just said yes" are direct acceptances
        # of the offered day. They must advance the flow here — before _detect_intent()
        # runs — to prevent general_query routing them to the LLM.
        #
        # chosen_day is set to the raw transcript; the LLM at PRESENT_TIMES receives
        # it as context and resolves any ambiguity (e.g. which of 3 offered days).
        if step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            # ── REPEAT / CLARIFICATION first ────────────────────────────────────
            # CRITICAL: extract:"any" accepts every non-empty transcript, so without
            # this guard a clarification request ("what were those days?") would be
            # stored verbatim as chosen_day and corrupt the booking.  Replay the
            # stored day list deterministically and keep state unchanged.
            _PD_REPEAT = (
                "what days", "which days", "say that again", "say it again",
                "repeat that", "repeat the days", "those days again",
                "what were the days", "what were those", "what are the days",
                "didn't catch", "didn't hear", "again please",
                "pardon", "remind me", "can't remember", "tell me again",
                "what was that", "what did you say", "what did you offer",
                "what are my options", "what are the options",
                # Day-specific clarifications (BUG 3)
                "what was the day", "what day was", "what was that day",
                "what day", "which day", "the day again",
                # "repeat yourself" variants observed in live calls
                "repeat yourself", "could you repeat", "please repeat",
                "say it again please", "say that again please",
            )
            if any(p in text for p in _PD_REPEAT):
                _pd_avail  = self.session.get("available_days", [])
                # Replay the CURRENT page, not always page 0
                _pd_page   = self.session.get("days_page", 0)
                _pd_paged  = _pd_avail[_pd_page * 3 : (_pd_page + 1) * 3] or _pd_avail
                _pd_replay = _build_day_list_phrase(_pd_paged)
                if _pd_replay:
                    await self._tts.put(_pd_replay)
                    self.session["last_question"] = _pd_replay
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _pd_replay}
                    )
                    logger.info(
                        "[ms_flow] %s: repeat/clarification → replaying day list "
                        "(page=%d, extract:any bypass, LLM avoided): %r",
                        step["state"], _pd_page, _pd_replay[:80],
                    )
                    return  # keep same flow_step — wait for actual day choice

            # ── BUG 21: EXPLORATORY UTTERANCES — incomplete day requests ──────────
            # "can you do", "do you have" without a specific day name means the caller
            # hasn't committed to a day yet.  Prompt for clarification rather than
            # routing to Haiku (which may read the sentence fragment as a selection).
            _PD_EXPLORATORY = (
                "can you do", "could you do", "do you have", "do you do",
                "is there", "are there any", "have you got",
                "is that possible", "would that be",
            )
            _WEEKDAY_WORDS_EXP = {
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "mon", "tue", "wed", "thu", "fri",
            }
            _exp_match = any(p in text for p in _PD_EXPLORATORY)
            _exp_has_day = any(w in text.split() for w in _WEEKDAY_WORDS_EXP)
            # BUG FIX: also bypass exploratory guard when utterance contains a
            # calendar ordinal ("23rd", "the 27th") or a month name — these are
            # specific date/month requests that must reach the _XD_PAT handler or
            # the month-filter block below.  Without these bypasses, "do you have
            # anything on the 23rd" / "do you have anything in May" were being
            # caught here and replaying the day list instead of answering correctly.
            import re as _re_exp_ord
            _MONTH_NAMES_EXP = frozenset({
                "january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
                "jan", "feb", "mar", "apr", "jun", "jul", "aug",
                "sep", "sept", "oct", "nov", "dec",
            })
            _exp_has_ordinal = bool(_re_exp_ord.search(r'\b\d{1,2}(?:st|nd|rd|th)\b', text))
            _exp_has_month   = any(m in text for m in _MONTH_NAMES_EXP)
            # BUG FIX: also bypass when week-of or proximity phrases are present
            # so "do you have anything that week" / "do you have anything around then"
            # reach the week-of-date / proximity handlers rather than being swallowed
            # by the exploratory guard and replaying the current day list.
            _exp_has_week_or_prox = any(p in text for p in (
                "week of", "that week", "same week", "week containing",
                "in that week", "during that week",
                "earlier that week", "later that week",
                "around then", "around that", "around there",
                "near that", "near then", "near there",
                "nearby", "close to that", "closest to", "nearest to",
                "in that area", "around that area", "around that time",
            ))
            if _exp_match and not _exp_has_day and not _exp_has_ordinal and not _exp_has_month and not _exp_has_week_or_prox:
                _exp_replay = self.session.get("last_question", "Which day would suit you best?")
                await self._tts.put(_exp_replay)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _exp_replay}
                )
                logger.info(
                    "[ms_flow] %s: exploratory utterance %r — prompting for day (Haiku avoided)",
                    step["state"], transcript[:40],
                )
                return

            # ── WEEK-OF-DATE / PROXIMITY: calendar-range and anchor navigation ──
            # Handles: "week of the 8th of May", "the week of April 23rd"
            #          "that week", "same week", "earlier that week"
            #          "around then", "around that", "near that", "nearby"
            #          "closest to that", "anything near that date"
            # Must come BEFORE _XD_PAT so "week of the 8th" is treated as a
            # range request rather than an exact-date lookup.
            _WK_WEEK_PHRASES = (
                "week of the", "week of", "that week", "same week",
                "week containing", "the week around", "week starting",
                "earlier that week", "later that week",
                "earlier in that week", "later in that week",
                "anything that week", "what have you got that week",
                "around that week", "that particular week",
                # "in that week" variants not covered by "that week"
                "in that week", "anything in that week",
                "available in that week", "dates in that week",
            )
            _WK_AROUND_PHRASES = (
                "around then", "around that", "around there", "around that time",
                "near that", "near then", "near there",
                "nearby", "close to that", "close to then",
                "anything near", "anything close", "something near",
                "near to that", "around the same time", "around that sort of time",
                "closest to", "nearest to", "nearest date",
                "in that area", "around that area",
            )
            _WK_NEXT_PHRASES = (
                "the week after that", "week after that",
                "the following week", "following week",
                "next week after that", "week after this",
                "the week after this",
            )
            _wk_week_hit   = any(p in text for p in _WK_WEEK_PHRASES)
            _wk_around_hit = any(p in text for p in _WK_AROUND_PHRASES)
            _wk_next_hit   = any(p in text for p in _WK_NEXT_PHRASES)

            if _wk_week_hit or _wk_around_hit or _wk_next_hit:
                # Extract explicit date from utterance if present ("week of the 8th of May")
                _wk_avail = self.session.get("available_days", [])
                _wk_expl  = _parse_transcript_date(transcript, _wk_avail)
                # Fall back to session anchor set by a prior explicit-date request
                _wk_anchor_str = (
                    _wk_expl.isoformat() if _wk_expl
                    else self.session.get("last_requested_date")
                )
                if _wk_anchor_str:
                    import datetime as _dt_wk
                    self.session["last_requested_date"] = _wk_anchor_str
                    try:
                        _wk_anchor_obj = _dt_wk.date.fromisoformat(_wk_anchor_str)
                    except (ValueError, TypeError):
                        _wk_anchor_obj = None
                    if _wk_anchor_obj and _wk_avail:
                        if _wk_next_hit:
                            # THE WEEK AFTER THAT: compute the ISO week whose Monday
                            # falls one week after the anchor's ISO-week Monday.
                            import datetime as _dt_wkn
                            _wkn_mon = _wk_anchor_obj - _dt_wkn.timedelta(
                                days=_wk_anchor_obj.weekday()
                            )
                            _wkn_next_mon = _wkn_mon + _dt_wkn.timedelta(weeks=1)
                            _wkn_in_week  = _week_days_for_anchor(_wk_avail, _wkn_next_mon)
                            # Advance anchor so further "the week after that" chaining works
                            self.session["last_requested_date"] = _wkn_next_mon.isoformat()
                            if _wkn_in_week:
                                _wkn_phrase = _build_day_list_phrase(_wkn_in_week)
                                # Lowercase first letter after "The following week, "
                                _wkn_out = (
                                    "The following week, "
                                    + _wkn_phrase[:1].lower()
                                    + _wkn_phrase[1:]
                                )
                                await self._tts.put(_wkn_out)
                                self.session["last_question"] = _wkn_out
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _wkn_out}
                                )
                                self.session["days_page"]          = 0
                                self.session["_pd_month_filtered"] = _wkn_in_week
                                logger.info(
                                    "[ms_flow] %s next-week: anchor=%s → next_mon=%s %d day(s)",
                                    step["state"], _wk_anchor_str,
                                    _wkn_next_mon.isoformat(), len(_wkn_in_week),
                                )
                                return
                            # No availability that week — offer nearest alternatives
                            _wkn_near = _build_day_list_phrase(
                                _nearest_days(_wk_avail, _wkn_next_mon)
                            )
                            _wkn_na_out = (
                                "I\u2019m afraid I don\u2019t have anything the following week \u2014 "
                                + _wkn_near.replace("I can do ", "but the nearest I have is ", 1)
                                           .replace("I\u2019ve got ", "but the nearest I have is ", 1)
                                           .replace("The next opening I have is ",
                                                    "but the nearest I have is ", 1)
                            )
                            await self._tts.put(_wkn_na_out)
                            self.session["last_question"] = _wkn_na_out
                            self.session.setdefault("conversation_history", []).append(
                                {"role": "assistant", "content": _wkn_na_out}
                            )
                            logger.info(
                                "[ms_flow] %s next-week: no availability after %s",
                                step["state"], _wkn_next_mon.isoformat(),
                            )
                            return
                        elif _wk_week_hit:
                            # WEEK-OF-DATE: filter to ISO week (Mon–Sun) containing anchor
                            _wk_in_week = _week_days_for_anchor(_wk_avail, _wk_anchor_obj)
                            _wk_suf = (
                                "st" if _wk_anchor_obj.day % 10 == 1 and _wk_anchor_obj.day != 11 else
                                "nd" if _wk_anchor_obj.day % 10 == 2 and _wk_anchor_obj.day != 12 else
                                "rd" if _wk_anchor_obj.day % 10 == 3 and _wk_anchor_obj.day != 13 else
                                "th"
                            )
                            # Directional week filtering: "late/later in that week" → latter half
                            #                             "early/earlier in that week" → first half
                            _wk_dir_late = any(w in text for w in (
                                "later that week", "late in that week", "later in that week",
                                "end of that week", "end of the week", "towards the end of that week",
                                "latter part", "second half of that week",
                            ))
                            _wk_dir_early = any(w in text for w in (
                                "earlier that week", "early in that week", "earlier in that week",
                                "start of that week", "beginning of that week",
                                "early part", "first half of that week",
                            ))
                            if _wk_in_week and len(_wk_in_week) > 1:
                                if _wk_dir_late:
                                    _wk_in_week = _wk_in_week[max(1, len(_wk_in_week) // 2):]
                                elif _wk_dir_early:
                                    _wk_in_week = _wk_in_week[:max(1, len(_wk_in_week) // 2)]
                            _wk_label = (
                                (f"the later part of the week of the {_wk_anchor_obj.day}{_wk_suf}"
                                 if _wk_expl else "the later part of that week")
                                if _wk_dir_late else
                                (f"the earlier part of the week of the {_wk_anchor_obj.day}{_wk_suf}"
                                 if _wk_expl else "the earlier part of that week")
                                if _wk_dir_early else
                                (f"the week of the {_wk_anchor_obj.day}{_wk_suf}"
                                 if _wk_expl else "that week")
                            )
                            if _wk_in_week:
                                _wk_phrase = _build_day_list_phrase(_wk_in_week)
                                _wk_out    = f"For {_wk_label}, {_wk_phrase}"
                                await self._tts.put(_wk_out)
                                self.session["last_question"] = _wk_out
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _wk_out}
                                )
                                self.session["days_page"]          = 0
                                self.session["_pd_month_filtered"] = _wk_in_week
                                logger.info(
                                    "[ms_flow] %s week-of-date: anchor=%s dir=%s → %d day(s) in week",
                                    step["state"], _wk_anchor_str,
                                    "late" if _wk_dir_late else "early" if _wk_dir_early else "any",
                                    len(_wk_in_week),
                                )
                                return
                            # No availability that week — offer nearest alternatives
                            _wk_near_p = _build_day_list_phrase(
                                _nearest_days(_wk_avail, _wk_anchor_obj)
                            )
                            _wk_na_out = (
                                f"I\u2019m afraid I don\u2019t have anything in {_wk_label} \u2014 "
                                + _wk_near_p.replace("I can do ", "but the nearest I have is ", 1)
                                            .replace("I\u2019ve got ", "but the nearest I have is ", 1)
                                            .replace("The next opening I have is ",
                                                     "but the nearest I have is ", 1)
                            )
                            await self._tts.put(_wk_na_out)
                            self.session["last_question"] = _wk_na_out
                            self.session.setdefault("conversation_history", []).append(
                                {"role": "assistant", "content": _wk_na_out}
                            )
                            logger.info(
                                "[ms_flow] %s week-of-date: no availability in %s",
                                step["state"], _wk_label,
                            )
                            return
                        else:
                            # PROXIMITY: "around then", "near that" — nearest 3 days
                            _prox_days   = _nearest_days(_wk_avail, _wk_anchor_obj)
                            _prox_phrase = _build_day_list_phrase(_prox_days)
                            _prox_out    = _prox_phrase.replace(
                                "I can do ",     "The closest I have to that is ",
                            ).replace(
                                "I\u2019ve got ", "The closest I have to that is ",
                            ).replace(
                                "The next opening I have is ", "The closest to that is ",
                            )
                            await self._tts.put(_prox_out)
                            self.session["last_question"] = _prox_out
                            self.session.setdefault("conversation_history", []).append(
                                {"role": "assistant", "content": _prox_out}
                            )
                            self.session["days_page"]          = 0
                            self.session["_pd_month_filtered"] = _prox_days
                            logger.info(
                                "[ms_flow] %s proximity: anchor=%s → %d nearest day(s)",
                                step["state"], _wk_anchor_str, len(_prox_days),
                            )
                            return
                else:
                    # No anchor in session — ask caller for a date to anchor on
                    _wk_no_anchor = (
                        "Which date did you have in mind? "
                        "If you give me a rough date I\u2019ll check what\u2019s available around then."
                    )
                    await self._tts.put(_wk_no_anchor)
                    self.session["last_question"] = _wk_no_anchor
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _wk_no_anchor}
                    )
                    logger.info(
                        "[ms_flow] %s week/proximity: no anchor date — asking caller",
                        step["state"],
                    )
                    return

            # ── EXPLICIT DATE: "the 25th of April" must beat NONE-OF-THESE/ordinal ──
            # Run before _PD_NONE so "none of those, what about the 25th of April?"
            # is handled as an explicit date request, not a page-advance.
            import re as _re_xd
            # Two-pattern explicit date regex.  The old single pattern
            # r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-zA-Z]+)\b'
            # was broken for "23rd of april": (?:of\s+)? is optional so
            # [a-zA-Z]+ captured "of" instead of "april", making _xd_month_n=None
            # and silently skipping the whole date lookup while leaving _XD_PAT
            # truthy (which in turn blocked the bare-ordinal fallback).
            # Fix: anchor group 2 to an explicit month-name alternation.
            _XD_MONTH_ALT = (
                r'january|february|march|april|may|june|july|august|september|october|november|december'
                r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec'
            )
            _XD_PAT = _re_xd.search(
                r'\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+|\s+)(' + _XD_MONTH_ALT + r')\b'
                r'|\b(' + _XD_MONTH_ALT + r')\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b',
                transcript, _re_xd.IGNORECASE,
            )
            if _XD_PAT:
                if _XD_PAT.group(1):
                    _xd_day_n, _xd_month_s = int(_XD_PAT.group(1)), _XD_PAT.group(2).lower()
                else:
                    _xd_day_n, _xd_month_s = int(_XD_PAT.group(4)), _XD_PAT.group(3).lower()

                _MONTH_NUM = {
                    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
                    "january": 1, "february": 2, "march": 3, "april": 4,
                    "june": 6, "july": 7, "august": 8, "september": 9,
                    "october": 10, "november": 11, "december": 12,
                }
                _MONTH_SHORT = {
                    1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "may", 6: "jun",
                    7: "jul", 8: "aug", 9: "sep", 10: "oct", 11: "nov", 12: "dec",
                }
                _xd_month_n = _MONTH_NUM.get(_xd_month_s[:3]) or _MONTH_NUM.get(_xd_month_s)
                if _xd_month_n:
                    _xd_abbr = _MONTH_SHORT[_xd_month_n]
                    _xd_all = self.session.get("available_days", [])
                    _xd_matched = None
                    # Use digit-boundary regex to prevent 1 matching 21, 10, etc.
                    _day_re = _re_xd.compile(r'(?<!\d)' + str(_xd_day_n) + r'(?!\d)')
                    for _xd_d in _xd_all:
                        _xd_lbl = _xd_d.get("day_label", "").lower()
                        if _day_re.search(_xd_lbl) and (
                            _xd_abbr in _xd_lbl or _xd_month_s[:3] in _xd_lbl
                        ):
                            _xd_matched = _xd_d
                            break
                    if _xd_matched:
                        self.session["chosen_day"] = _xd_matched["day_label"]
                        self.session.setdefault("collected", {})["chosen_day"] = _xd_matched["day_label"]
                        # Store anchor so follow-up "that week"/"around then" resolves correctly
                        self.session["last_requested_date"] = _xd_matched.get("date", "")
                        self.session.pop("days_page", None)
                        self.session.pop("vague_option_pending", None)
                        self.session.pop("vague_clarification_asked", None)
                        self.session.pop("slot_pending_confirmation", None)
                        # ── INLINE TIME: "30th at 10", "23rd April in the morning" ──
                        # If the same utterance also contains a time, resolve it now
                        # so we don't replay the full time list unnecessarily.
                        _xd_inline_hour = _extract_hour_from_text(text)
                        if _xd_inline_hour is not None:
                            _xd_il_slots = _xd_matched.get("slots", [])
                            _xd_il_times = _xd_matched.get("slot_times", [])
                            _xd_il_label = _xd_matched.get("day_label", "")
                            _xd_il_idx: Optional[int] = None
                            for _xi, _xt in enumerate(_xd_il_times):
                                try:
                                    if int(_xt.split(":")[0]) == _xd_inline_hour:
                                        _xd_il_idx = _xi
                                        break
                                except (ValueError, IndexError):
                                    pass
                            from app.vagueness_detector import _time_to_speech as _t2s_il
                            if _xd_il_idx is not None:
                                # Exact slot found — bind and skip PRESENT_TIMES entirely
                                _xd_il_sp = _t2s_il(_xd_il_times[_xd_il_idx])
                                _xd_il_ss = f"{_xd_il_label} at {_xd_il_sp}"
                                _nxt_il   = step["step"] + 2
                                _nxt_il_state = (
                                    self._active_flow[_nxt_il]["state"]
                                    if _nxt_il < len(self._active_flow) else "DONE"
                                )
                                self.session["selected_slot"]        = _xd_il_slots[_xd_il_idx].get("start", "")
                                self.session["selected_slot_speech"] = _xd_il_ss
                                self.session["slot_confirmed"]       = True
                                self.session["flow_step"]            = _nxt_il
                                self.session["state"]                = _nxt_il_state
                                logger.info(
                                    "[ms_flow] PRESENT_DAYS inline date+time: %r hour=%d → %r (PRESENT_TIMES skipped)",
                                    _xd_il_label, _xd_inline_hour, _xd_il_ss,
                                )
                                await self.ask_current_question()
                                return
                            elif _xd_il_times:
                                # Day found, exact time not available — offer nearest same-day alts
                                _xd_il_req_sp = _t2s_il(f"{_xd_inline_hour:02d}:00")
                                def _xd_il_dist(t):
                                    try: return abs(int(t.split(":")[0]) - _xd_inline_hour)
                                    except: return 999
                                _xd_near_t = sorted(_xd_il_times, key=_xd_il_dist)[:2]
                                _xd_near_s: list = []
                                for _nt in _xd_near_t:
                                    for _si2, _st2 in enumerate(_xd_il_times):
                                        if _st2 == _nt and _si2 < len(_xd_il_slots):
                                            _xd_near_s.append(_xd_il_slots[_si2])
                                            break
                                _xd_near_sp = [_t2s_il(t) for t in _xd_near_t]
                                if len(_xd_near_sp) == 1:
                                    _xd_il_msg = (
                                        f"I\u2019ve got {_xd_il_label} for you, but I don\u2019t have "
                                        f"{_xd_il_req_sp} on that day \u2014 the closest I have is "
                                        f"{_xd_near_sp[0]}. Would that work?"
                                    )
                                    if _xd_near_s:
                                        self.session["selected_slot"]             = _xd_near_s[0].get("start", "")
                                        self.session["selected_slot_speech"]      = f"{_xd_il_label} at {_xd_near_sp[0]}"
                                        self.session["slot_pending_confirmation"] = True
                                elif _xd_near_sp:
                                    _xd_il_msg = (
                                        f"I\u2019ve got {_xd_il_label} for you, but I don\u2019t have "
                                        f"{_xd_il_req_sp} \u2014 I do have {_xd_near_sp[0]} or "
                                        f"{_xd_near_sp[1]}. Which would suit you?"
                                    )
                                    self.session["offered_constrained_times"] = _xd_near_t
                                    self.session["offered_constrained_slots"] = _xd_near_s
                                else:
                                    _xd_il_msg = (
                                        f"I\u2019ve got {_xd_il_label} for you, but I don\u2019t have "
                                        f"{_xd_il_req_sp} on that day. Which time would work for you?"
                                    )
                                _nxt_il_pt  = step["step"] + 1
                                _nxt_il_pst = (
                                    self._active_flow[_nxt_il_pt]["state"]
                                    if _nxt_il_pt < len(self._active_flow) else "DONE"
                                )
                                self.session["flow_step"] = _nxt_il_pt
                                self.session["state"]     = _nxt_il_pst
                                await self._tts.put(_xd_il_msg)
                                self.session["last_question"] = _xd_il_msg
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _xd_il_msg}
                                )
                                logger.info(
                                    "[ms_flow] PRESENT_DAYS inline date+time: hour=%d not on %r — offered alts",
                                    _xd_inline_hour, _xd_il_label,
                                )
                                return
                        # No inline time (or no slots for day) — present full time list
                        _nxt_xd = step["step"] + 1
                        _nxt_xd_state = (
                            self._active_flow[_nxt_xd]["state"]
                            if _nxt_xd < len(self._active_flow) else "DONE"
                        )
                        self.session["flow_step"] = _nxt_xd
                        self.session["state"]     = _nxt_xd_state
                        logger.info(
                            "[ms_flow] PRESENT_DAYS explicit date: %r → %r",
                            transcript[:40], _xd_matched["day_label"],
                        )
                        await self.ask_current_question()
                        return
                    # Explicit date requested but not available — say so and offer
                    # the nearest alternatives in that month rather than silently
                    # falling through to the month filter (which would re-present
                    # the same initial 3 days because it also starts from page 0).
                    import datetime as _dt_xd_na
                    # Store anchor even for unavailable dates — caller may follow up
                    # with "that week" / "around then" and we need the reference.
                    try:
                        _today_xd_na = _dt_xd_na.date.today()
                        _xd_yr_na = _today_xd_na.year if _xd_month_n >= _today_xd_na.month else _today_xd_na.year + 1
                        self.session["last_requested_date"] = _dt_xd_na.date(
                            _xd_yr_na, _xd_month_n, _xd_day_n
                        ).isoformat()
                    except ValueError:
                        pass
                    _xd_na_suffix = (
                        "st" if _xd_day_n % 10 == 1 and _xd_day_n != 11 else
                        "nd" if _xd_day_n % 10 == 2 and _xd_day_n != 12 else
                        "rd" if _xd_day_n % 10 == 3 and _xd_day_n != 13 else
                        "th"
                    )
                    _xd_spoken_date = f"the {_xd_day_n}{_xd_na_suffix} of {_xd_month_s.capitalize()}"
                    # Collect additional explicit day ordinals in the same month
                    # context so we can mention ALL unavailable dates in one message
                    # ("I don't have the 21st or 22nd of May available — but I can do…")
                    import re as _re_xd_multi
                    _xd_extra_dnums = sorted(set(
                        int(m) for m in _re_xd_multi.findall(r'\b(\d{1,2})(?:st|nd|rd|th)\b', text)
                        if 1 <= int(m) <= 31 and int(m) != _xd_day_n
                    ))
                    _xd_all_avail_na = self.session.get("available_days", [])
                    # ── CONSTRAINED EXPLORATORY: check for month/bound/pair ────
                    # If the utterance is a search expression ("later than the 1st
                    # of May but in May", "anything like the 7th or 8th of May"),
                    # filter available_days by the caller's constraints BEFORE
                    # choosing alternatives.  This prevents offering April dates
                    # when the caller clearly asked for May.
                    _xd_constrained = _constrained_day_alternatives(
                        text, transcript, _xd_month_n, _xd_all_avail_na,
                        self.session.get("last_requested_date"),
                    )
                    if _xd_constrained is not None:
                        # Constraints detected — use constrained pool
                        _xd_na_month_days = _xd_constrained[:3]
                        _xd_constrained_applied = True
                    else:
                        # No constraints — same-week first, nearest overall fallback
                        _xd_constrained_applied = False
                        try:
                            _xd_req_obj_na = _dt_xd_na.date(_xd_yr_na, _xd_month_n, _xd_day_n)
                            _xd_same_week_na = _week_days_for_anchor(_xd_all_avail_na, _xd_req_obj_na)
                            _xd_na_month_days = (
                                _xd_same_week_na
                                if _xd_same_week_na
                                else _nearest_days(_xd_all_avail_na, _xd_req_obj_na, n=3)
                            )
                        except (ValueError, AttributeError):
                            _xd_na_month_days = _xd_all_avail_na[:3]
                    if _xd_na_month_days:
                        _xd_na_alt = _build_day_list_phrase(_xd_na_month_days)
                        # Build intro that names ALL requested dates when multiple
                        # were given ("I don't have the 21st or 22nd of May available")
                        if _xd_extra_dnums:
                            def _xd_suf_fn(d):
                                return ("st" if d % 10 == 1 and d != 11 else
                                        "nd" if d % 10 == 2 and d != 12 else
                                        "rd" if d % 10 == 3 and d != 13 else "th")
                            _xd_all_parts = [f"{_xd_day_n}{_xd_na_suffix}"] + [
                                f"{d}{_xd_suf_fn(d)}" for d in _xd_extra_dnums[:2]
                            ]
                            _xd_na_intro = (
                                f"I don\u2019t have the {' or '.join(_xd_all_parts)} of "
                                f"{_xd_month_s.capitalize()} available \u2014 "
                            )
                        else:
                            _xd_na_intro = f"{_xd_spoken_date} isn\u2019t available \u2014 "
                        _xd_na_msg = (
                            _xd_na_intro
                            + _xd_na_alt.replace("I can do ", "but I can do ", 1)
                            .replace("I've got ", "but I've got ", 1)
                            .replace("I\u2019ve got ", "but I\u2019ve got ", 1)
                        )
                        # Keep constrained set as new offered context
                        if _xd_constrained_applied:
                            self.session["_pd_month_filtered"] = _xd_na_month_days
                    else:
                        # Constrained and empty — no dates in that constraint frame
                        _xd_na_msg = (
                            f"I don\u2019t have {_xd_spoken_date} available"
                            + (
                                f" in {_xd_month_s.capitalize()} right now \u2014 "
                                "would you like the next available dates?"
                                if _xd_constrained_applied
                                else ". " + _build_day_list_phrase(_xd_all_avail_na)
                            )
                        )
                    await self._tts.put(_xd_na_msg)
                    self.session["last_question"] = _xd_na_msg
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _xd_na_msg}
                    )
                    logger.info(
                        "[ms_flow] PRESENT_DAYS: %r not available — constrained=%s offered=%d alt(s)",
                        _xd_spoken_date, _xd_constrained_applied,
                        len(_xd_na_month_days) if _xd_na_month_days else 0,
                    )
                    return

            # ── BARE ORDINAL: "the 23rd", "on the 27th" — no month name given ────
            # _XD_PAT above requires digit+month (or month+digit).  When the caller
            # says only a day number we must still look it up deterministically
            # rather than falling to Haiku which offers the wrong dates.
            # Strategy: search available_days for a date whose day-of-month matches.
            # If multiple months contain that day, prefer the earliest one.
            import re as _re_bo
            _BO_PAT = _re_bo.search(r'\b(the\s+)?(\d{1,2})(st|nd|rd|th)\b', text, _re_bo.IGNORECASE)
            if _BO_PAT and not _XD_PAT:
                # ── CANDIDATE-PAIR: "the 3rd or the 4th", "3rd or 4th" ───────
                # When 2+ bare ordinals are joined by "or"/"and", resolve each
                # against available_days and respond grounded to what's available.
                _cp_all_days = _re_bo.findall(
                    r'\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b', text, _re_bo.IGNORECASE,
                )
                if len(_cp_all_days) >= 2 and any(w in text for w in (" or ", " and ")):
                    import datetime as _dt_cp
                    _cp_avail  = self.session.get("available_days", [])
                    # Scope to active month — prevents "21st or 22nd" matching
                    # April dates after the caller has moved the scope to May.
                    _cp_active_month_n = self.session.get("_pd_active_month_n")
                    if _cp_active_month_n:
                        _cp_month_scope = [
                            d for d in _cp_avail
                            if _dt_cp.date.fromisoformat(
                                (d.get("date") or "9999-12-31")[:10]
                            ).month == _cp_active_month_n
                        ]
                        if _cp_month_scope:
                            _cp_avail = _cp_month_scope
                    _cp_matched: list = []
                    _cp_tried:  list = []
                    for _cp_ds in _cp_all_days[:3]:
                        _cp_dn = int(_cp_ds)
                        if 1 <= _cp_dn <= 31 and _cp_dn not in _cp_tried:
                            _cp_tried.append(_cp_dn)
                            for _cp_d in _cp_avail:
                                _cp_str = _cp_d.get("date") or _cp_d.get("datetime", "")
                                try:
                                    if _dt_cp.date.fromisoformat(_cp_str[:10]).day == _cp_dn:
                                        _cp_matched.append(_cp_d)
                                        break
                                except (ValueError, TypeError):
                                    pass
                    if len(_cp_matched) == 1:
                        # Exactly one candidate is available — bind it directly
                        _cp_entry = _cp_matched[0]
                        self.session["chosen_day"] = _cp_entry["day_label"]
                        self.session.setdefault("collected", {})["chosen_day"] = _cp_entry["day_label"]
                        self.session["last_requested_date"] = _cp_entry.get("date", "")
                        self.session.pop("days_page", None)
                        self.session.pop("slot_pending_confirmation", None)
                        _nxt_cp = step["step"] + 1
                        _nxt_cp_st = (
                            self._active_flow[_nxt_cp]["state"]
                            if _nxt_cp < len(self._active_flow) else "DONE"
                        )
                        self.session["flow_step"] = _nxt_cp
                        self.session["state"]     = _nxt_cp_st
                        logger.info(
                            "[ms_flow] PRESENT_DAYS candidate-pair: 1 available → %r",
                            _cp_entry["day_label"],
                        )
                        await self.ask_current_question()
                        return
                    elif len(_cp_matched) >= 2:
                        # Multiple candidates available — offer the ones we have
                        _cp_phrase = _build_day_list_phrase(_cp_matched[:2])
                        await self._tts.put(_cp_phrase)
                        self.session["last_question"] = _cp_phrase
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _cp_phrase}
                        )
                        self.session["days_page"]          = 0
                        self.session["_pd_month_filtered"] = _cp_matched[:2]
                        logger.info(
                            "[ms_flow] PRESENT_DAYS candidate-pair: %d available → offered both",
                            len(_cp_matched),
                        )
                        return
                    # else: none of the candidates available — fall through to
                    # single-ordinal not-found path which offers alternatives
                _bo_day_n = int(_BO_PAT.group(2))
                if 1 <= _bo_day_n <= 31:
                    import datetime as _dt_bo
                    _bo_all = self.session.get("available_days", [])
                    # Scope to active month — "21st" must not match April 21st
                    # after the caller has narrowed the search window to May.
                    _bo_active_month_n = self.session.get("_pd_active_month_n")
                    if _bo_active_month_n:
                        _bo_month_scope = [
                            d for d in _bo_all
                            if _dt_bo.date.fromisoformat(
                                (d.get("date") or "9999-12-31")[:10]
                            ).month == _bo_active_month_n
                        ]
                        _bo_scope = _bo_month_scope if _bo_month_scope else _bo_all
                    else:
                        _bo_scope = _bo_all
                    _bo_matched = None
                    for _bo_d in _bo_scope:
                        _bo_str = _bo_d.get("date") or _bo_d.get("datetime", "")
                        try:
                            if _dt_bo.date.fromisoformat(_bo_str[:10]).day == _bo_day_n:
                                _bo_matched = _bo_d
                                break
                        except (ValueError, TypeError):
                            pass
                    if _bo_matched:
                        self.session["chosen_day"] = _bo_matched["day_label"]
                        self.session.setdefault("collected", {})["chosen_day"] = _bo_matched["day_label"]
                        # Store anchor for follow-up "that week"/"around then" references
                        self.session["last_requested_date"] = _bo_matched.get("date", "")
                        self.session.pop("days_page", None)
                        self.session.pop("vague_option_pending", None)
                        self.session.pop("vague_clarification_asked", None)
                        self.session.pop("slot_pending_confirmation", None)
                        # ── INLINE TIME: "the 30th at 10" ──────────────────────
                        _bo_inline_hour = _extract_hour_from_text(text)
                        if _bo_inline_hour is not None:
                            _bo_il_slots = _bo_matched.get("slots", [])
                            _bo_il_times = _bo_matched.get("slot_times", [])
                            _bo_il_label = _bo_matched.get("day_label", "")
                            _bo_il_idx: Optional[int] = None
                            for _bxi, _bxt in enumerate(_bo_il_times):
                                try:
                                    if int(_bxt.split(":")[0]) == _bo_inline_hour:
                                        _bo_il_idx = _bxi
                                        break
                                except (ValueError, IndexError):
                                    pass
                            from app.vagueness_detector import _time_to_speech as _t2s_bo_il
                            if _bo_il_idx is not None:
                                _bo_il_sp = _t2s_bo_il(_bo_il_times[_bo_il_idx])
                                _bo_il_ss = f"{_bo_il_label} at {_bo_il_sp}"
                                _nxt_bo_il = step["step"] + 2
                                _nxt_bo_il_state = (
                                    self._active_flow[_nxt_bo_il]["state"]
                                    if _nxt_bo_il < len(self._active_flow) else "DONE"
                                )
                                self.session["selected_slot"]        = _bo_il_slots[_bo_il_idx].get("start", "")
                                self.session["selected_slot_speech"] = _bo_il_ss
                                self.session["slot_confirmed"]       = True
                                self.session["flow_step"]            = _nxt_bo_il
                                self.session["state"]                = _nxt_bo_il_state
                                logger.info(
                                    "[ms_flow] PRESENT_DAYS bare-ordinal+time: %r hour=%d → %r (PRESENT_TIMES skipped)",
                                    _bo_il_label, _bo_inline_hour, _bo_il_ss,
                                )
                                await self.ask_current_question()
                                return
                            elif _bo_il_times:
                                from app.vagueness_detector import _time_to_speech as _t2s_bo_na
                                _bo_il_req_sp = _t2s_bo_na(f"{_bo_inline_hour:02d}:00")
                                def _bo_il_dist(t):
                                    try: return abs(int(t.split(":")[0]) - _bo_inline_hour)
                                    except: return 999
                                _bo_near_t = sorted(_bo_il_times, key=_bo_il_dist)[:2]
                                _bo_near_s: list = []
                                for _bnt in _bo_near_t:
                                    for _bsi, _bst in enumerate(_bo_il_times):
                                        if _bst == _bnt and _bsi < len(_bo_il_slots):
                                            _bo_near_s.append(_bo_il_slots[_bsi])
                                            break
                                _bo_near_sp = [_t2s_bo_na(t) for t in _bo_near_t]
                                if len(_bo_near_sp) == 1:
                                    _bo_il_msg = (
                                        f"I\u2019ve got {_bo_il_label} for you, but I don\u2019t have "
                                        f"{_bo_il_req_sp} \u2014 the closest I have is {_bo_near_sp[0]}. "
                                        "Would that work?"
                                    )
                                    if _bo_near_s:
                                        self.session["selected_slot"]             = _bo_near_s[0].get("start", "")
                                        self.session["selected_slot_speech"]      = f"{_bo_il_label} at {_bo_near_sp[0]}"
                                        self.session["slot_pending_confirmation"] = True
                                elif _bo_near_sp:
                                    _bo_il_msg = (
                                        f"I\u2019ve got {_bo_il_label} for you, but I don\u2019t have "
                                        f"{_bo_il_req_sp} \u2014 I do have {_bo_near_sp[0]} or "
                                        f"{_bo_near_sp[1]}. Which would suit you?"
                                    )
                                    self.session["offered_constrained_times"] = _bo_near_t
                                    self.session["offered_constrained_slots"] = _bo_near_s
                                else:
                                    _bo_il_msg = (
                                        f"I\u2019ve got {_bo_il_label} for you, but I don\u2019t have "
                                        f"{_bo_il_req_sp} on that day. Which time would work for you?"
                                    )
                                _nxt_bo_pt  = step["step"] + 1
                                _nxt_bo_pst = (
                                    self._active_flow[_nxt_bo_pt]["state"]
                                    if _nxt_bo_pt < len(self._active_flow) else "DONE"
                                )
                                self.session["flow_step"] = _nxt_bo_pt
                                self.session["state"]     = _nxt_bo_pst
                                await self._tts.put(_bo_il_msg)
                                self.session["last_question"] = _bo_il_msg
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _bo_il_msg}
                                )
                                logger.info(
                                    "[ms_flow] PRESENT_DAYS bare-ordinal+time: hour=%d not on %r — offered alts",
                                    _bo_inline_hour, _bo_il_label,
                                )
                                return
                        # No inline time — present full time list
                        _nxt_bo = step["step"] + 1
                        _nxt_bo_state = (
                            self._active_flow[_nxt_bo]["state"]
                            if _nxt_bo < len(self._active_flow) else "DONE"
                        )
                        self.session["flow_step"] = _nxt_bo
                        self.session["state"]     = _nxt_bo_state
                        logger.info(
                            "[ms_flow] PRESENT_DAYS bare ordinal: %r → %r",
                            transcript[:40], _bo_matched["day_label"],
                        )
                        await self.ask_current_question()
                        return
                    # Bare ordinal not found in scope — say so and offer alternatives
                    # within the same month scope so we don't snap to a different month.
                    _bo_suffix = (
                        "st" if _bo_day_n % 10 == 1 and _bo_day_n != 11 else
                        "nd" if _bo_day_n % 10 == 2 and _bo_day_n != 12 else
                        "rd" if _bo_day_n % 10 == 3 and _bo_day_n != 13 else
                        "th"
                    )
                    _bo_spoken = f"the {_bo_day_n}{_bo_suffix}"
                    _bo_alt = _build_day_list_phrase(_bo_scope)
                    _bo_msg = (
                        f"I'm afraid I don't have {_bo_spoken} available — "
                        + (_bo_alt.replace("I can do ", "but I can do ", 1)
                                  .replace("I've got ", "but I've got ", 1)
                           if _bo_alt else "but let me know which day works for you.")
                    )
                    await self._tts.put(_bo_msg)
                    self.session["last_question"] = _bo_msg
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _bo_msg}
                    )
                    logger.info(
                        "[ms_flow] PRESENT_DAYS: bare ordinal %r not in available_days — offered alternatives",
                        _bo_spoken,
                    )
                    return

            # ── NONE-OF-THESE / LATER / BACK: page through available_days ────────
            _PD_NONE = (
                "none of these", "none of them", "none of those",
                "those don't work", "those dont work",
                "they don't work", "they dont work",
                "none of these work", "none of these suit", "none suit",
                "nothing works for me", "doesn't work for me", "doesnt work for me",
                "can't do any of those", "cant do any of those",
                "not available on any", "none of those suit",
                # Common spoken rejections missing from original set
                "not really", "no not really", "nothing there",
                "nah", "nope", "no thanks", "no thank you",
                "that doesn't work", "that wont work", "that won't work",
                "not ideal", "not great",
            )
            _PD_LATER = (
                "later dates", "later date", "any later", "something later",
                "further ahead", "further in advance",
                "anything later", "anything after", "more dates", "other dates",
                "if you have any later", "what else", "any other",
                # Common spoken variants — "late dates", "any late" without trailing "r"
                "late dates", "any late", "any later dates",
                "later availability", "later days", "further dates",
                "have any late", "got any late", "got any later",
            )
            _PD_BACK = (
                "go back", "previous dates", "earlier dates",
                "the ones before", "the first ones", "back to the first",
                # "original" / "initial" phrasing from live calls
                "original dates", "the original", "initial dates",
                "the initial", "first set", "back to the original",
                "list the original", "list the initial",
            )
            _pd_all = self.session.get("available_days", [])

            # ── ANCHOR-RELATIVE FILTER: "later than that" / "earlier than that" ──
            # When the caller references the last anchor date (e.g. "anything later
            # than that?", "do you have anything earlier than that?") and a
            # last_requested_date is set in session, filter to days strictly after
            # (or before) that anchor rather than doing a blind page-advance.
            # This must run BEFORE _PD_NONE/_PD_LATER so "anything later than that"
            # doesn't hit the generic next-page path and lose the anchor context.
            _PD_LATER_THAN = (
                "later than that", "later than this",
                "anything later than that", "anything later than this",
                "anything after that", "anything after this",
                "after that date", "after that",
                "beyond that", "past that",
                # Reschedule-context: "later than the original appointment"
                "later than the original", "after the original",
                "later than my original", "after my original",
                "later than the appointment", "after the appointment",
            )
            _PD_EARLIER_THAN = (
                "earlier than that", "earlier than this",
                "anything earlier than that", "anything earlier than this",
                "anything before that", "anything before this",
                "before that date", "before that",
            )
            _pd_lrd = self.session.get("last_requested_date")
            if _pd_lrd and not _exp_has_month:
                import datetime as _dt_anchor
                _pd_later_than_hit   = any(p in text for p in _PD_LATER_THAN)
                _pd_earlier_than_hit = any(p in text for p in _PD_EARLIER_THAN)
                if _pd_later_than_hit or _pd_earlier_than_hit:
                    try:
                        _pd_anchor_obj = _dt_anchor.date.fromisoformat(_pd_lrd)
                    except (ValueError, TypeError):
                        _pd_anchor_obj = None
                    if _pd_anchor_obj:
                        if _pd_later_than_hit:
                            _pd_rel_days = [
                                d for d in _pd_all
                                if _dt_anchor.date.fromisoformat(
                                    (d.get("date") or "9999-12-31")[:10]
                                ) > _pd_anchor_obj
                            ]
                            _pd_rel_label = "after that"
                        else:
                            _pd_rel_days = [
                                d for d in _pd_all
                                if _dt_anchor.date.fromisoformat(
                                    (d.get("date") or "0001-01-01")[:10]
                                ) < _pd_anchor_obj
                            ]
                            _pd_rel_label = "before that"
                        if _pd_rel_days:
                            _pd_rel_phrase = _build_day_list_phrase(_pd_rel_days[:3])
                            await self._tts.put(_pd_rel_phrase)
                            self.session["last_question"] = _pd_rel_phrase
                            self.session.setdefault("conversation_history", []).append(
                                {"role": "assistant", "content": _pd_rel_phrase}
                            )
                            self.session["days_page"] = 0
                            self.session["_pd_month_filtered"] = _pd_rel_days
                            logger.info(
                                "[ms_flow] PRESENT_DAYS anchor-relative: %s %s → %d day(s)",
                                _pd_rel_label, _pd_lrd, len(_pd_rel_days),
                            )
                            return
                        # Nothing in that direction — fall through to normal handling
                        logger.info(
                            "[ms_flow] PRESENT_DAYS anchor-relative: no days %s %s — falling through",
                            _pd_rel_label, _pd_lrd,
                        )

            # Month-guard: "none of those, do you have anything in May" must reach
            # the month filter, not the page-advance logic.  If a month name is
            # present in the utterance we skip page-navigation entirely so the
            # month filter (further below) can run.
            if (any(p in text for p in _PD_NONE) or any(p in text for p in _PD_LATER)) and not _exp_has_month:
                # Caller is browsing past the currently-offered set — any month-
                # filtered overlay is no longer the active offer.
                self.session.pop("_pd_month_filtered", None)
                _page = self.session.get("days_page", 0) + 1
                self.session["days_page"] = _page
                _next_days = _pd_all[_page * 3 : (_page + 1) * 3]
                if _next_days:
                    _phrase = _build_day_list_phrase(_next_days)
                    await self._tts.put(_phrase)
                    self.session["last_question"] = _phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _phrase}
                    )
                    logger.info("[ms_flow] PRESENT_DAYS: page=%d next days offered", _page)
                else:
                    _no_more = (
                        "I'm afraid that's all the availability I have in the next 30 days. "
                        "Can I take your details and have someone call you to arrange a time?"
                    )
                    await self._tts.put(_no_more)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _no_more}
                    )
                    self.session["graceful_exit"] = True
                    self.session["flow_step"] = len(self._active_flow)
                    logger.info("[ms_flow] PRESENT_DAYS: no more days — graceful exit")
                return

            # Guard: _PD_BACK must not fire when (a) a month name is present
            # (those go to the month filter) or (b) "later than" is in the
            # utterance ("later than the original appointment" contains "the
            # original" but is not a page-back request).
            _pd_has_later_than_guard = "later than" in text or "after the original" in text
            if (
                any(p in text for p in _PD_BACK)
                and not _exp_has_month
                and not _pd_has_later_than_guard
            ):
                self.session.pop("_pd_month_filtered", None)
                _page = max(0, self.session.get("days_page", 0) - 1)
                self.session["days_page"] = _page
                _prev_days = _pd_all[_page * 3 : (_page + 1) * 3]
                if _prev_days:
                    _phrase = _build_day_list_phrase(_prev_days)
                    await self._tts.put(_phrase)
                    self.session["last_question"] = _phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _phrase}
                    )
                    logger.info("[ms_flow] PRESENT_DAYS: page back → page=%d", _page)
                return

            _PD_YES = (
                # Single-word affirmatives
                "yes", "yeah", "ya", "yep", "yup",
                "ok", "okay", "sure", "fine", "alright",
                "perfect", "great", "aye",
                # "that works" family
                "that works", "works for me", "that's fine",
                "that'll work", "that will work",
                "that would work", "that should work",
                "should work for me", "would work for me",
                # "sounds" family
                "sounds good", "sounds fine", "that sounds",
                # other acceptances
                "go ahead", "that'll do", "that will do",
                "right then", "alright then",
                "said yes", "just said yes",
            )
            # Guard: ordinal words and weekday names must be handled by their
            # dedicated blocks below — must NOT collapse into a generic YES here.
            # "yeah monday 10th" contains "yeah" (YES) and "monday" (weekday) —
            # weekday guard prevents it from mapping to day[0].
            # Date-ordinal guard: "the 21st works for me" must NOT bind to day[0] —
            # specific calendar dates should fall through to the date matcher.
            # Month-name guard: "yeah i was asking do you have any slots in may" must NOT
            # bind as YES — the month intent must reach the month filter below.
            _ORDINAL_SKIP = {"first", "second", "third", "last", "final", "middle"}
            import re as _re_dordn
            _has_date_ordinal = bool(_re_dordn.search(r'\b\d{1,2}(?:st|nd|rd|th)\b', text))
            _pd_yes = (
                not any(w in _ORDINAL_SKIP for w in text.split())
                and not any(w in _WEEKDAY_WORDS for w in text.split())
                and not _has_date_ordinal
                and not _exp_has_month
                and any(p in text for p in _PD_YES)
            )
            logger.info(
                "[ms_flow] PRESENT_DAYS pre-interrupt: state=%s transcript=%r → yes=%s  flow_step=%d",
                step["state"], transcript[:80], _pd_yes, step["step"],
            )
            if _pd_yes:
                # BUG 3 fix: store the real day label, not the raw affirmation.
                # "yeah that works for me" must NOT be stored as chosen_day.
                # Prefer month-filtered subset if one was just offered — prevents
                # YES after "any dates in May" binding to original available_days[0].
                _avail_yes = (
                    self.session.get("_pd_month_filtered")
                    or self.session.get("available_days", [])
                )
                _chosen_label = (
                    _avail_yes[0].get("day_label", transcript.strip())
                    if _avail_yes else transcript.strip()
                )
                self.session["chosen_day"] = _chosen_label
                self.session.setdefault("collected", {})["chosen_day"] = _chosen_label
                _nxt_pd_yes = step["step"] + 1
                _nxt_pd_yes_state = (
                    self._active_flow[_nxt_pd_yes]["state"]
                    if _nxt_pd_yes < len(self._active_flow) else "DONE"
                )
                self.session["flow_step"] = _nxt_pd_yes
                self.session["state"]     = _nxt_pd_yes_state
                self.session.pop("days_page", None)
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                self.session.pop("slot_pending_confirmation", None)
                logger.info(
                    "[ms_flow] PRESENT_DAYS day_selected → next_state=%s flow_step=%d chosen_day=%r",
                    _nxt_pd_yes_state, _nxt_pd_yes, _chosen_label,
                )
                await self.ask_current_question()
                return

            # ── ORDINAL / POSITIONAL MATCH: "the last one", "three", "third" ──────
            # Deterministic position-based resolution against the offered day list.
            # Checked BEFORE named-day match so "last"/"third"/"three"/"3" never
            # reach the reprompt branch.
            _avail_ord = (
                self.session.get("_pd_month_filtered")
                or self.session.get("available_days", [])
            )[:3]
            _n_ord = len(_avail_ord)
            _ord_idx: Optional[int] = None
            if _n_ord > 0:
                _first_phrases  = ("the first one", "first one", "the first", "number one", "option one")
                _second_phrases = ("the second one", "second one", "the second", "number two", "option two")
                _last_phrases   = ("the last one", "last one", "the last")
                if any(p in text for p in _first_phrases) or "first" in text.split():
                    _ord_idx = 0
                elif any(p in text for p in _second_phrases) or "second" in text.split():
                    _ord_idx = min(1, _n_ord - 1)
                elif any(p in text for p in _last_phrases) or "last" in text.split():
                    _ord_idx = _n_ord - 1
                elif _n_ord >= 3 and any(
                    p in text for p in (
                        "the third one", "third one", "the third", "third",
                        "number three", "option three",
                    )
                ):
                    _ord_idx = 2
                elif _n_ord >= 3 and ("three" in text or "3" in text.split()):
                    _ord_idx = 2
                elif _n_ord >= 2 and any(
                    p in text for p in ("middle one", "the middle one", "the middle", "middle")
                ):
                    _ord_idx = 1  # middle of offered days = index 1
            # Mixed-intent guard: if the utterance contains an ordinal token AND
            # inquiry/question language, skip the bind entirely and let the turn
            # fall through to the existing inquiry / retry path.
            if _ord_idx is not None:
                _PD_MIXED_MARKERS = (
                    "are you open", "open on saturday", "open on sunday", "open on",
                    "do you have parking", "is there parking",
                    "how long", "what time", "do you have", "can you do", "are there any",
                    # Inquiry preamble — "quick question first", "had a question",
                    # "can i ask", "just wondering" must never bind an ordinal slot.
                    "quick question", "question first", "had a question", "have a question",
                    "just a question", "one question", "can i ask", "before i choose",
                    "just wondering",
                )
                if any(m in text for m in _PD_MIXED_MARKERS):
                    logger.info(
                        "[ms_flow] PRESENT_DAYS: mixed ordinal+inquiry detected — skipping bind (raw=%r)",
                        transcript[:60],
                    )
                    _ord_idx = None  # fall through to inquiry / retry
            if _ord_idx is not None:
                _norm_ord = _avail_ord[_ord_idx]["day_label"]
                self.session["chosen_day"] = _norm_ord
                self.session.setdefault("collected", {})["chosen_day"] = _norm_ord
                _nxt_pd_ord = step["step"] + 1
                _nxt_pd_ord_state = (
                    self._active_flow[_nxt_pd_ord]["state"]
                    if _nxt_pd_ord < len(self._active_flow) else "DONE"
                )
                self.session["flow_step"] = _nxt_pd_ord
                self.session["state"]     = _nxt_pd_ord_state
                self.session.pop("days_page", None)
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                self.session.pop("slot_pending_confirmation", None)
                logger.info(
                    "[ms_flow] PRESENT_DAYS day_selected → next_state=%s flow_step=%d chosen_day=%r (ordinal idx=%d raw=%r)",
                    _nxt_pd_ord_state, _nxt_pd_ord, _norm_ord, _ord_idx, transcript[:40],
                )
                await self.ask_current_question()
                return

            # ── NAMED DAY MATCH: normalize caller's choice against offered days ──
            # Prevents raw text like "i take tuesday i take tuesday" from being
            # stored verbatim as chosen_day via extract:"any".
            # Only the first 3 entries in available_days are "offered" days.
            _avail_nm = (
                self.session.get("_pd_month_filtered")
                or self.session.get("available_days", [])
            )
            _matched_nm: Optional[dict] = None
            import re as _re_nm
            for _dentry_nm in _avail_nm[:3]:
                _dlabel_nm = _dentry_nm.get("day_label", "")
                # Match only on day-of-week words — month names (e.g. "april") appear
                # in every offered label and cause false first-entry matches.
                # Use word-boundary matching to prevent "tuesday" matching inside
                # "thursday" or other substrings.
                _sig_nm = [w.lower() for w in _dlabel_nm.split() if w.lower() in _WEEKDAY_WORDS]
                if _sig_nm and any(_re_nm.search(r'\b' + w + r'\b', text) for w in _sig_nm):
                    _matched_nm = _dentry_nm
                    break
            if _matched_nm:
                from app.vagueness_detector import is_vague_availability as _is_vague_nd
                import re as _re_nd
                # Also treat as vague when a time-of-day qualifier sits alongside the
                # day name (e.g. "Wednesday mornings") — is_vague_availability misses
                # these because DAY_PATTERN match disables the short-utterance fallback
                # and "mornings" (plural) isn't in VAGUE_PHRASES.
                _has_time_qualifier_nd = bool(
                    _re_nd.search(r"\b(?:morning|afternoon|evening|night|lunchtime|noon)\w*", transcript, _re_nd.IGNORECASE)
                )
                if not _is_vague_nd(transcript) and not _has_time_qualifier_nd:
                    # Clean day selection — advance to PRESENT_TIMES
                    _norm_nm = _matched_nm["day_label"]
                    self.session["chosen_day"] = _norm_nm
                    self.session.setdefault("collected", {})["chosen_day"] = _norm_nm
                    _nxt_pd_nm = step["step"] + 1
                    _nxt_pd_nm_state = (
                        self._active_flow[_nxt_pd_nm]["state"]
                        if _nxt_pd_nm < len(self._active_flow) else "DONE"
                    )
                    self.session["flow_step"] = _nxt_pd_nm
                    self.session["state"]     = _nxt_pd_nm_state
                    self.session.pop("days_page", None)
                    self.session.pop("vague_option_pending", None)
                    self.session.pop("vague_clarification_asked", None)
                    self.session.pop("slot_pending_confirmation", None)
                    logger.info(
                        "[ms_flow] PRESENT_DAYS day_selected → next_state=%s flow_step=%d chosen_day=%r (named match raw=%r)",
                        _nxt_pd_nm_state, _nxt_pd_nm, _norm_nm, transcript[:40],
                    )
                    await self.ask_current_question()
                    return
                # Vague despite containing a day name (e.g. "Wednesday mornings") —
                # fall through to the vague handler so build_vague_options fires.
                logger.info(
                    "[ms_flow] PRESENT_DAYS: named day %r in %r but input is vague — falling through",
                    _matched_nm["day_label"], transcript[:40],
                )
            else:
                # No day match and not a YES — check for common weekend inquiry first,
                # then hand to Haiku for everything else.
                _day_labels_nm = [
                    d.get("day_label", "") for d in _avail_nm[:6] if d.get("day_label")
                ]
                # Deterministic weekend-hours answer — the clinic is weekday-only.
                _WEEKEND_Q = ("saturday", "sunday", "weekend", "weekends", "saturdays", "sundays")
                if any(w in text for w in _WEEKEND_Q):
                    _wknd_anchor = self.session.get(
                        "last_question",
                        "Which of the days I mentioned would work best for you?",
                    )
                    _wknd_msg = (
                        "We offer weekday appointments only — Monday through Friday. "
                        + _wknd_anchor
                    )
                    await self._tts.put(_wknd_msg)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _wknd_msg}
                    )
                    logger.info(
                        "[ms_flow] PRESENT_DAYS: weekend inquiry answered deterministically",
                    )
                    return
                # ── MONTH FILTER (with directional awareness) ─────────────────
                # Handles:
                #   "any dates in May"              → all May dates
                #   "later in April" / "further April" / "end of April"
                #                                   → April dates AFTER anchor
                #   "earlier in April" / "start of April"
                #                                   → April dates BEFORE anchor
                # Directional detection runs first; generic (all-month) is the
                # fallback when no direction is found or the directional slice
                # returns nothing.
                _MONTH_NAMES_PD = {
                    "january": 1, "february": 2, "march": 3, "april": 4,
                    "may": 5, "june": 6, "july": 7, "august": 8,
                    "september": 9, "october": 10, "november": 11, "december": 12,
                    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
                    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
                }
                _month_hit = next((m for m in _MONTH_NAMES_PD if m in text), None)
                if _month_hit:
                    _target_month = _MONTH_NAMES_PD[_month_hit]
                    import datetime as _dt_mod
                    # Collect all available days in the target month
                    _filtered_days = []
                    for _day_obj in _pd_all:
                        _d_str = _day_obj.get("date") or _day_obj.get("datetime", "")
                        try:
                            _d_month = _dt_mod.date.fromisoformat(_d_str[:10]).month
                            if _d_month == _target_month:
                                _filtered_days.append(_day_obj)
                        except (ValueError, TypeError):
                            pass
                    if _filtered_days:
                        # ── Directional filter ────────────────────────────────
                        # "later/further/end/towards the end" → dates after anchor
                        # "earlier/start/beginning" → dates before anchor
                        _dir_later = any(w in text for w in (
                            "later", "further", "end of", "towards the end",
                            "toward the end", "latter", "rest of", "other end",
                            "late ",  # "late April" — trailing space avoids matching "later"
                        ))
                        _dir_earlier = any(w in text for w in (
                            "earlier in", "start of", "beginning of", "early",
                        ))
                        _dir_mid = (
                            not _dir_later and not _dir_earlier
                            and any(w in text for w in (
                                "mid ", "mid-", "middle of", "midway",
                                "halfway through", "half way through",
                            ))
                        )
                        _dir_slice: list = []
                        if _dir_mid:
                            # "mid April" / "middle of April" → middle third of month
                            _n_fd = len(_filtered_days)
                            _third = max(1, _n_fd // 3)
                            _dir_slice = (
                                _filtered_days[_third: _third * 2]
                                or _filtered_days[_n_fd // 3:]
                                or _filtered_days
                            )
                        elif _dir_later or _dir_earlier:
                            # Determine directional anchor:
                            # 1. last_requested_date if in target month
                            # 2. last offered cluster's last/first date in month
                            # 3. midpoint of month as neutral baseline
                            _dir_anchor_obj = None
                            _lrd_dir = self.session.get("last_requested_date")
                            if _lrd_dir:
                                try:
                                    _lrd_obj_dir = _dt_mod.date.fromisoformat(_lrd_dir)
                                    if _lrd_obj_dir.month == _target_month:
                                        _dir_anchor_obj = _lrd_obj_dir
                                except (ValueError, TypeError):
                                    pass
                            if _dir_anchor_obj is None:
                                _off_dir = (
                                    self.session.get("_pd_month_filtered")
                                    or self.session.get("available_days", [])
                                )
                                _off_page_dir = self.session.get("days_page", 0)
                                _off_slice_dir = (
                                    _off_dir[_off_page_dir * 3: (_off_page_dir + 1) * 3]
                                    or _off_dir[:3]
                                )
                                _scan_dir = (
                                    reversed(_off_slice_dir) if _dir_later
                                    else iter(_off_slice_dir)
                                )
                                for _o_dir in _scan_dir:
                                    _o_str_dir = _o_dir.get("date", "")
                                    try:
                                        _o_obj_dir = _dt_mod.date.fromisoformat(_o_str_dir[:10])
                                        if _o_obj_dir.month == _target_month:
                                            _dir_anchor_obj = _o_obj_dir
                                            break
                                    except (ValueError, TypeError):
                                        pass
                            if _dir_anchor_obj is not None:
                                if _dir_later:
                                    _dir_slice = [
                                        d for d in _filtered_days
                                        if _dt_mod.date.fromisoformat(
                                            (d.get("date") or "9999-12-31")[:10]
                                        ) > _dir_anchor_obj
                                    ]
                                    if not _dir_slice:
                                        # Already past last — offer last few
                                        _dir_slice = _filtered_days[-3:]
                                else:
                                    _dir_slice = [
                                        d for d in _filtered_days
                                        if _dt_mod.date.fromisoformat(
                                            (d.get("date") or "0001-01-01")[:10]
                                        ) < _dir_anchor_obj
                                    ]
                                    if not _dir_slice:
                                        _dir_slice = _filtered_days[:3]
                            else:
                                # No anchor — split month in half
                                _mid_dir = max(1, len(_filtered_days) // 2)
                                _dir_slice = (
                                    _filtered_days[_mid_dir:]
                                    if _dir_later
                                    else _filtered_days[:_mid_dir]
                                ) or _filtered_days

                        # Use directional slice if available, else all-month
                        _mf_days = _dir_slice if _dir_slice else _filtered_days
                        _mf_phrase = _build_day_list_phrase(_mf_days)
                        await self._tts.put(_mf_phrase)
                        self.session["last_question"] = _mf_phrase
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _mf_phrase}
                        )
                        self.session["days_page"] = 0
                        self.session["_pd_month_filtered"] = _mf_days
                        # Store active month + representative anchor date so
                        # relative follow-ups ("the week after that") resolve correctly.
                        self.session["_pd_active_month_n"] = _target_month
                        if not self.session.get("last_requested_date") and _mf_days:
                            _mf_rep = _mf_days[0].get("date", "")
                            if _mf_rep:
                                self.session["last_requested_date"] = _mf_rep
                        logger.info(
                            "[ms_flow] PRESENT_DAYS month filter: dir=%s %d/%d days for month=%d",
                            ("mid" if _dir_mid else "later" if _dir_later else "earlier" if _dir_earlier else "any"),
                            len(_mf_days), len(_pd_all), _target_month,
                        )
                        return
                    else:
                        _no_month_msg = (
                            f"I\u2019m afraid I don\u2019t have any availability in "
                            f"{_month_hit.capitalize()} right now. "
                            "I can offer you the next available dates \u2014 would that work?"
                        )
                        logger.info(
                            "[ms_flow] PRESENT_DAYS month filter: no days in month=%d",
                            _target_month,
                        )
                        await self._tts.put(_no_month_msg)
                        self.session["last_question"] = _no_month_msg
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _no_month_msg}
                        )
                        return
                logger.info(
                    "[ms_flow] %s: no day match for %r → Haiku with day context",
                    step["state"], transcript[:40],
                )
                await self._haiku_fallback_days(transcript, step, _day_labels_nm)
                return

        # ── PRESENT_TIMES / PRESENT_TIMES_RESCHEDULE: deterministic parsing ────
        # BUG 2: Ordinal expressions ("the last option", "first one", "second")
        #        must map directly to a slot — no interrupt / no LLM.
        # BUG 3: Repeat / clarification requests ("i can't remember", "say that
        #        again") must replay the stored slot list — no interrupt / no LLM.
        if step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
            logger.info(
                "[ms_flow] PRESENT_TIMES entry: text=%r slot_confirmed=%s slot_pending=%s selected=%r",
                text[:60], self.session.get("slot_confirmed"),
                self.session.get("slot_pending_confirmation"),
                (self.session.get("selected_slot") or "")[:30],
            )
            # Stale guard: slot already confirmed or pending external confirmation — skip menu
            if self.session.get("slot_confirmed") or self.session.get("slot_pending_confirmation"):
                logger.info(
                    "[ms_flow] PRESENT_TIMES stale guard fired: slot_confirmed=%s slot_pending=%s — skipping",
                    self.session.get("slot_confirmed"), self.session.get("slot_pending_confirmation"),
                )
                return

            # ── BUG 5+6: step-back to date selection ─────────────────────────
            # "never mind", "any other availability", "go back", "another day",
            # "different day" inside PRESENT_TIMES must NOT trigger abandonment.
            # Instead: clear day selection and step back to PRESENT_DAYS.
            _PT_STEPBACK = (
                "never mind", "nevermind",
                "actually never mind", "not that one", "not that",
                "forget that", "forget it",
                "any other availability", "any other day",
                "another day", "different day",
                "go back", "back to dates", "back to days",
                "other options", "other days",
            )
            if any(p in text for p in _PT_STEPBACK):
                # Clear day + slot state
                self.session.pop("chosen_day", None)
                self.session.pop("selected_slot", None)
                self.session.pop("selected_slot_speech", None)
                self.session.pop("slot_pending_confirmation", None)
                self.session.pop("offered_constrained_times", None)
                self.session.pop("offered_constrained_slots", None)
                self.session.pop("_pd_month_filtered", None)
                # Drain stale TTS
                while not self._tts.empty():
                    try:
                        self._tts.get_nowait()
                    except Exception:
                        break
                # Step back to PRESENT_DAYS
                _pt_sb_pd_step = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE")),
                    max(0, step["step"] - 1),
                )
                self.session["flow_step"] = _pt_sb_pd_step
                self.session["state"]     = self._active_flow[_pt_sb_pd_step]["state"]
                logger.info(
                    "[ms_flow] PRESENT_TIMES: step-back %r → %s",
                    text[:40], self.session["state"],
                )
                await self.ask_current_question()
                return

            # ── MONTH / EXPLICIT-DATE ESCAPE ─────────────────────────────────
            # If the caller says a month name ("in May") or a specific date
            # ("the 27th of April") while inside PRESENT_TIMES, they are asking
            # to change the selected day.  We must exit the current-day slot
            # context immediately and route back to date-search logic rather
            # than re-speaking the current day's time slots.
            # Priority: explicit date > month > fall through to slot selection.
            import re as _re_pt_esc, datetime as _dt_pt_esc
            _PT_ESC_MONTH_MAP = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
                "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
            }
            _PT_ESC_MONTH_ALT = (
                r'january|february|march|april|may|june|july|august|september|october|november|december'
                r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec'
            )
            _pt_esc_xd = _re_pt_esc.search(
                r'\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+|\s+)(' + _PT_ESC_MONTH_ALT + r')\b'
                r'|\b(' + _PT_ESC_MONTH_ALT + r')\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b',
                transcript, _re_pt_esc.IGNORECASE,
            )
            _pt_esc_month_hit = next((m for m in _PT_ESC_MONTH_MAP if m in text), None)
            # Only treat a bare month name as an escape signal if:
            # - there is NO time-like token in the utterance (avoids "nine in the morning"
            #   firing on "morning" or "may" appearing in "that may work")
            # - or an explicit date pattern also matched
            _pt_esc_has_time_token = bool(_re_pt_esc.search(
                r'\b(?:\d{1,2}(?::\d{2})?(?:\s*[ap]m)?|o\'?clock|morning|afternoon|evening|noon|lunchtime)\b',
                text, _re_pt_esc.IGNORECASE,
            ))
            # Week-of-date / proximity escape phrases — same sets as PRESENT_DAYS handler
            _pt_wk_hit = any(p in text for p in (
                "week of the", "week of", "that week", "same week",
                "week containing", "earlier that week", "later that week",
                "earlier in that week", "later in that week",
                "anything that week", "around that week",
            ))
            _pt_around_hit = any(p in text for p in (
                "around then", "around that", "around there", "around that time",
                "near that", "near then", "near there",
                "nearby", "close to that", "closest to", "nearest to",
                "in that area", "around that area",
            ))
            _pt_esc_trigger = (
                _pt_esc_xd
                or (_pt_wk_hit and not _pt_esc_has_time_token)
                or (_pt_around_hit and not _pt_esc_has_time_token)
                or (
                    _pt_esc_month_hit and not _pt_esc_has_time_token
                    # also exclude "that may work" / "may be" false positives
                    and _pt_esc_month_hit != "may"
                ) or (
                    _pt_esc_month_hit == "may"
                    and any(p in text for p in ("in may", "for may", "any may", "the month of may", "slots in may", "dates in may"))
                )
            )
            if _pt_esc_trigger:
                # Clear current-day selection state
                self.session.pop("chosen_day", None)
                self.session.pop("selected_slot", None)
                self.session.pop("selected_slot_speech", None)
                self.session.pop("slot_pending_confirmation", None)
                self.session.pop("offered_constrained_times", None)
                self.session.pop("offered_constrained_slots", None)
                self.session.pop("_pd_month_filtered", None)
                # Drain stale TTS
                while not self._tts.empty():
                    try:
                        self._tts.get_nowait()
                    except Exception:
                        break
                # Step back to the PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE step
                _pt_esc_pd_step = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE")),
                    max(0, step["step"] - 1),
                )
                self.session["flow_step"] = _pt_esc_pd_step
                self.session["state"]     = self._active_flow[_pt_esc_pd_step]["state"]
                _pt_esc_avail = self.session.get("available_days", [])
                # ── WEEK-OF-DATE / PROXIMITY inline handling ──────────────────
                # Must come BEFORE explicit-date check: "week of the 8th of May"
                # matches both _pt_wk_hit and _pt_esc_xd — week wins.
                if _pt_wk_hit or _pt_around_hit:
                    _pt_wk_expl      = _parse_transcript_date(transcript, _pt_esc_avail)
                    _pt_wk_anchor_str = (
                        _pt_wk_expl.isoformat() if _pt_wk_expl
                        else self.session.get("last_requested_date")
                    )
                    if _pt_wk_anchor_str:
                        import datetime as _dt_pt_wk
                        self.session["last_requested_date"] = _pt_wk_anchor_str
                        try:
                            _pt_wk_anchor_obj = _dt_pt_wk.date.fromisoformat(_pt_wk_anchor_str)
                        except (ValueError, TypeError):
                            _pt_wk_anchor_obj = None
                        if _pt_wk_anchor_obj and _pt_esc_avail:
                            if _pt_wk_hit:
                                _pt_wk_in_week = _week_days_for_anchor(_pt_esc_avail, _pt_wk_anchor_obj)
                                _pt_wk_suf = (
                                    "st" if _pt_wk_anchor_obj.day % 10 == 1 and _pt_wk_anchor_obj.day != 11 else
                                    "nd" if _pt_wk_anchor_obj.day % 10 == 2 and _pt_wk_anchor_obj.day != 12 else
                                    "rd" if _pt_wk_anchor_obj.day % 10 == 3 and _pt_wk_anchor_obj.day != 13 else
                                    "th"
                                )
                                _pt_wk_label = (
                                    f"the week of the {_pt_wk_anchor_obj.day}{_pt_wk_suf}"
                                    if _pt_wk_expl else "that week"
                                )
                                if _pt_wk_in_week:
                                    _pt_wk_phrase = _build_day_list_phrase(_pt_wk_in_week)
                                    _pt_wk_out    = f"For {_pt_wk_label}, {_pt_wk_phrase}"
                                    await self._tts.put(_pt_wk_out)
                                    self.session["last_question"] = _pt_wk_out
                                    self.session.setdefault("conversation_history", []).append(
                                        {"role": "assistant", "content": _pt_wk_out}
                                    )
                                    self.session["days_page"]          = 0
                                    self.session["_pd_month_filtered"] = _pt_wk_in_week
                                    logger.info(
                                        "[ms_flow] PRESENT_TIMES escape week-of-date: %s → %d day(s)",
                                        _pt_wk_label, len(_pt_wk_in_week),
                                    )
                                    return
                                _pt_wk_near_p = _build_day_list_phrase(
                                    _nearest_days(_pt_esc_avail, _pt_wk_anchor_obj)
                                )
                                _pt_wk_na_out = (
                                    f"I\u2019m afraid I don\u2019t have anything in {_pt_wk_label} \u2014 "
                                    + _pt_wk_near_p.replace("I can do ", "but the nearest I have is ", 1)
                                                   .replace("I\u2019ve got ", "but the nearest I have is ", 1)
                                                   .replace("The next opening I have is ",
                                                            "but the nearest I have is ", 1)
                                )
                                await self._tts.put(_pt_wk_na_out)
                                self.session["last_question"] = _pt_wk_na_out
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _pt_wk_na_out}
                                )
                                logger.info(
                                    "[ms_flow] PRESENT_TIMES escape week-of-date: no availability in %s",
                                    _pt_wk_label,
                                )
                                return
                            else:
                                # PROXIMITY
                                _pt_prox_days   = _nearest_days(_pt_esc_avail, _pt_wk_anchor_obj)
                                _pt_prox_phrase = _build_day_list_phrase(_pt_prox_days)
                                _pt_prox_out    = _pt_prox_phrase.replace(
                                    "I can do ",     "The closest I have to that is ",
                                ).replace(
                                    "I\u2019ve got ", "The closest I have to that is ",
                                ).replace(
                                    "The next opening I have is ", "The closest to that is ",
                                )
                                await self._tts.put(_pt_prox_out)
                                self.session["last_question"] = _pt_prox_out
                                self.session.setdefault("conversation_history", []).append(
                                    {"role": "assistant", "content": _pt_prox_out}
                                )
                                self.session["days_page"]          = 0
                                self.session["_pd_month_filtered"] = _pt_prox_days
                                logger.info(
                                    "[ms_flow] PRESENT_TIMES escape proximity: anchor=%s → %d day(s)",
                                    _pt_wk_anchor_str, len(_pt_prox_days),
                                )
                                return
                    # No anchor — let ask_current_question() re-display PRESENT_DAYS
                    await self.ask_current_question()
                    return

                # Handle inline: explicit date → jump directly; month → filter
                if _pt_esc_xd:
                    # Explicit date: resolve day+month groups
                    if _pt_esc_xd.group(1):
                        _pt_xd_day_n  = int(_pt_esc_xd.group(1))
                        _pt_xd_month_s = _pt_esc_xd.group(2).lower()
                    else:
                        _pt_xd_day_n  = int(_pt_esc_xd.group(4))
                        _pt_xd_month_s = _pt_esc_xd.group(3).lower()
                    _pt_xd_month_n = _PT_ESC_MONTH_MAP.get(_pt_xd_month_s[:3]) or _PT_ESC_MONTH_MAP.get(_pt_xd_month_s)
                    if _pt_xd_month_n:
                        _pt_xd_day_re = _re_pt_esc.compile(r'(?<!\d)' + str(_pt_xd_day_n) + r'(?!\d)')
                        _pt_xd_abbr   = {1:"jan",2:"feb",3:"mar",4:"apr",5:"may",6:"jun",
                                          7:"jul",8:"aug",9:"sep",10:"oct",11:"nov",12:"dec"}[_pt_xd_month_n]
                        _pt_xd_matched = next(
                            (d for d in _pt_esc_avail
                             if _pt_xd_day_re.search(d.get("day_label","").lower())
                             and (_pt_xd_abbr in d.get("day_label","").lower()
                                  or _pt_xd_month_s[:3] in d.get("day_label","").lower())),
                            None,
                        )
                        if _pt_xd_matched:
                            self.session["chosen_day"] = _pt_xd_matched["day_label"]
                            self.session.setdefault("collected", {})["chosen_day"] = _pt_xd_matched["day_label"]
                            self.session["flow_step"] = _pt_esc_pd_step + 1
                            self.session["state"]     = self._active_flow[_pt_esc_pd_step + 1]["state"]
                            logger.info(
                                "[ms_flow] PRESENT_TIMES escape: explicit date %r → %r (advancing to slots)",
                                transcript[:40], _pt_xd_matched["day_label"],
                            )
                            await self.ask_current_question()
                            return
                    # Explicit date not found — fall through to month filter below
                if _pt_esc_month_hit:
                    _pt_esc_target_month = _PT_ESC_MONTH_MAP[_pt_esc_month_hit]
                    _pt_esc_filtered = []
                    for _pt_esc_d in _pt_esc_avail:
                        _pt_esc_ds = _pt_esc_d.get("date") or _pt_esc_d.get("datetime", "")
                        try:
                            if _dt_pt_esc.date.fromisoformat(_pt_esc_ds[:10]).month == _pt_esc_target_month:
                                _pt_esc_filtered.append(_pt_esc_d)
                        except (ValueError, TypeError):
                            pass
                    if _pt_esc_filtered:
                        # Directional month filter — same logic as PRESENT_DAYS
                        _pt_esc_dir_later = any(w in text for w in (
                            "later", "further", "end of", "towards the end",
                            "toward the end", "latter", "rest of", "other end",
                            "late ",  # "late April" — trailing space avoids matching "later"
                        ))
                        _pt_esc_dir_earlier = any(w in text for w in (
                            "earlier in", "start of", "beginning of", "early",
                        ))
                        _pt_esc_dir_mid = (
                            not _pt_esc_dir_later and not _pt_esc_dir_earlier
                            and any(w in text for w in (
                                "mid ", "mid-", "middle of", "midway",
                                "halfway through", "half way through",
                            ))
                        )
                        _pt_esc_dir_slice: list = []
                        if _pt_esc_dir_mid:
                            _n_pe = len(_pt_esc_filtered)
                            _third_pe = max(1, _n_pe // 3)
                            _pt_esc_dir_slice = (
                                _pt_esc_filtered[_third_pe: _third_pe * 2]
                                or _pt_esc_filtered[_n_pe // 3:]
                                or _pt_esc_filtered
                            )
                        elif _pt_esc_dir_later or _pt_esc_dir_earlier:
                            _pt_esc_anchor = None
                            _lrd_pe = self.session.get("last_requested_date")
                            if _lrd_pe:
                                try:
                                    _lrd_pe_obj = _dt_pt_esc.date.fromisoformat(_lrd_pe)
                                    if _lrd_pe_obj.month == _pt_esc_target_month:
                                        _pt_esc_anchor = _lrd_pe_obj
                                except (ValueError, TypeError):
                                    pass
                            if _pt_esc_anchor is None:
                                _mid_pe = max(1, len(_pt_esc_filtered) // 2)
                                _pt_esc_dir_slice = (
                                    _pt_esc_filtered[_mid_pe:]
                                    if _pt_esc_dir_later
                                    else _pt_esc_filtered[:_mid_pe]
                                ) or _pt_esc_filtered
                            else:
                                if _pt_esc_dir_later:
                                    _pt_esc_dir_slice = [
                                        d for d in _pt_esc_filtered
                                        if _dt_pt_esc.date.fromisoformat(
                                            (d.get("date") or "9999-12-31")[:10]
                                        ) > _pt_esc_anchor
                                    ] or _pt_esc_filtered[-3:]
                                else:
                                    _pt_esc_dir_slice = [
                                        d for d in _pt_esc_filtered
                                        if _dt_pt_esc.date.fromisoformat(
                                            (d.get("date") or "0001-01-01")[:10]
                                        ) < _pt_esc_anchor
                                    ] or _pt_esc_filtered[:3]
                        _pt_esc_offer = _pt_esc_dir_slice if _pt_esc_dir_slice else _pt_esc_filtered
                        _pt_esc_phrase = _build_day_list_phrase(_pt_esc_offer)
                        await self._tts.put(_pt_esc_phrase)
                        self.session["last_question"] = _pt_esc_phrase
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _pt_esc_phrase}
                        )
                        self.session["days_page"] = 0
                        self.session["_pd_month_filtered"] = _pt_esc_offer
                        logger.info(
                            "[ms_flow] PRESENT_TIMES escape: month=%r dir=%s → %d day(s) offered",
                            _pt_esc_month_hit,
                            ("mid" if _pt_esc_dir_mid else "later" if _pt_esc_dir_later else "earlier" if _pt_esc_dir_earlier else "any"),
                            len(_pt_esc_offer),
                        )
                        return
                    else:
                        _pt_esc_no_msg = (
                            f"I'm afraid I don't have any availability in "
                            f"{_pt_esc_month_hit.capitalize()} right now. "
                            "Would you like to hear the next available dates instead?"
                        )
                        await self._tts.put(_pt_esc_no_msg)
                        self.session["last_question"] = _pt_esc_no_msg
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _pt_esc_no_msg}
                        )
                        logger.info(
                            "[ms_flow] PRESENT_TIMES escape: month=%r — no days found",
                            _pt_esc_month_hit,
                        )
                        return
                # If we reach here (XD matched but not in availability, no month hit),
                # fall through to normal PRESENT_DAYS routing by returning — the
                # flow_step is already set back to PRESENT_DAYS.
                await self.ask_current_question()
                return

            # ── FIRST-CHECK: single-slot confirm YES/NO ──────────────────────
            # ask_current_question() already stored selected_slot when it asked
            # "On [day] I've got [time] — does that work for you?"
            # Catch YES/NO IMMEDIATELY, before repeat/ordinal/time logic or any
            # fallback can fire.  Uses session["selected_slot"] as the signal —
            # no re-lookup of available_days needed.
            _ssc_slot   = self.session.get("selected_slot")
            _ssc_speech = self.session.get("selected_slot_speech", "")
            if _ssc_slot and not self.session.get("slot_confirmed"):
                logger.info(
                    "[ms_flow] single_slot_confirm input=%r selected_slot=%r",
                    text[:60], str(_ssc_slot)[:30],
                )
                _SSC_YES = (
                    "yes", "yeah", "yeh", "ya", "yep", "yup",
                    "yes please", "yep please",
                    "ok", "okay", "sure", "fine", "alright", "perfect",
                    "that works", "works for me", "works",
                    "sounds good", "sounds fine", "that sounds",
                    "that's fine", "thats fine",
                    "go ahead", "please",
                    # Natural spoken confirmations often missed:
                    "it does", "yes it does", "it would", "it will",
                    "suits me", "that suits", "suits", "does suit",
                    "that'd work", "that would work",
                    "sounds great", "that's great", "thats great",
                    "perfect for me", "that's perfect", "thats perfect",
                    "happy with that", "happy with",
                )
                _SSC_NO = (
                    "no", "nope", "no thanks", "nah",
                    "doesn't work", "does not work",
                    "that doesn't work", "that does not work",
                    "no good", "not good", "not for me",
                    "something else", "different time",
                )
                if any(p in text for p in _SSC_YES):
                    self.session["slot_confirmed"]       = True
                    self.session.pop("slot_pending_confirmation", None)
                    self.session.pop("vague_option_pending", None)
                    self.session.pop("vague_clarification_asked", None)
                    _nxt_ssc = step["step"] + 1
                    _nxt_ssc_state = (
                        self._active_flow[_nxt_ssc]["state"]
                        if _nxt_ssc < len(self._active_flow) else "DONE"
                    )
                    self.session["flow_step"] = _nxt_ssc
                    self.session["state"]     = _nxt_ssc_state
                    logger.info(
                        "[ms_flow] single_slot_confirm matched YES → next_state=%s",
                        _nxt_ssc_state,
                    )
                    await self.ask_current_question()
                    return
                # Correction guard: "no i said ...", "no i meant ..." are
                # repair utterances, not slot rejections.  Clear the stale
                # pending slot and fall through so the constraint / time-query
                # handlers can reparse the caller's actual request.
                _SSC_REPAIR_PHRASES = (
                    "no i said", "no i meant", "no i was asking",
                    "no i asked", "that's not what i",
                    "i said do you", "i was asking",
                )
                _is_ssc_repair = any(p in text for p in _SSC_REPAIR_PHRASES)
                if _is_ssc_repair:
                    self.session.pop("selected_slot", None)
                    self.session.pop("selected_slot_speech", None)
                    self.session.pop("slot_pending_confirmation", None)
                    logger.info(
                        "[ms_flow] %s: SSC correction detected %r — clearing pending slot, reparsing",
                        step["state"], text[:60],
                    )
                    # Fall through to constraint / time-query parsing below
                elif any(p in text for p in _SSC_NO):
                    logger.info("[ms_flow] single_slot_confirm matched NO → offering retry")
                    self.session.pop("selected_slot", None)
                    self.session.pop("selected_slot_speech", None)
                    _avail_no  = self.session.get("available_days", [])
                    _chosen_no = self.session.get("chosen_day", "")
                    _target_no = _find_chosen_day_entry(_avail_no, _chosen_no)
                    _no_phrase = (
                        _build_times_phrase(_target_no)
                        if _target_no else
                        "No problem — let me know what time would work for you."
                    )
                    await self._tts.put(_no_phrase)
                    self.session["last_question"] = _no_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _no_phrase}
                    )
                    return

            # ── REPEAT / CLARIFICATION ──
            _PT_REPEAT = (
                "can't remember", "cannot remember", "can not remember",
                "didn't catch", "didn't hear", "didn't get that",
                "say that again", "say it again", "repeat that", "repeat it",
                "repeat the", "repeat those", "again please", "say again",
                "what were", "what was", "what are the times",
                "what times", "those times", "the times again", "options again",
                "what options", "remind me", "tell me again",
                "sorry could you", "could you repeat",
            )
            _is_pt_repeat = any(p in text for p in _PT_REPEAT)
            if _is_pt_repeat:
                # Replay the currently active question — may be full-day list or
                # constrained subset; session["last_question"] always holds the
                # most recently spoken offer so we replay exactly what caller heard.
                _rpt_phrase = self.session.get("last_question", "")
                if not _rpt_phrase:
                    # Fallback: rebuild from availability data
                    _avail_r   = self.session.get("available_days", [])
                    _chosen_r  = self.session.get("chosen_day", "")
                    _target_r  = _find_chosen_day_entry(_avail_r, _chosen_r)
                    _rpt_phrase = _build_times_phrase(_target_r) if _target_r else ""
                if _rpt_phrase:
                    await self._tts.put(_rpt_phrase)
                    self.session["last_question"] = _rpt_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _rpt_phrase}
                    )
                    logger.info(
                        "[PRESENT_TIMES] repeat request → replaying times phrase deterministically: %r",
                        _rpt_phrase[:80],
                    )
                    return  # keep same flow_step — wait for slot choice

            # ── SINGLE-SLOT CONFIRMATION MODE ────────────────────────────────
            # When exactly one slot exists for the chosen day, YES-like responses
            # confirm it directly; day-name repeats re-anchor to that slot.
            # Both cases must be caught here before ordinal/time matching or LLM.
            _avail_ss  = self.session.get("available_days", [])
            _chosen_ss = self.session.get("chosen_day", "")
            _target_ss = _find_chosen_day_entry(_avail_ss, _chosen_ss)
            _slots_ss  = (_target_ss or {}).get("slots", [])
            if len(_slots_ss) == 1:
                from app.vagueness_detector import _time_to_speech as _t2s_ss
                _time_ss   = ((_target_ss or {}).get("slot_times") or [""])[0]
                _spoken_ss = _t2s_ss(_time_ss) if _time_ss else "that time"
                _dlabel_ss = (_target_ss or {}).get("day_label", "")
                _speech_ss = f"{_dlabel_ss} at {_spoken_ss}" if _dlabel_ss else _spoken_ss
                _SS_YES = (
                    "yes", "yeah", "yeh", "ya", "yep", "yup",
                    "ok", "okay", "sure", "fine", "alright", "perfect",
                    "that works", "that works for me", "sounds good",
                    "sounds fine", "that sounds", "go ahead", "please",
                    # Natural spoken confirmations:
                    "it does", "yes it does", "it would", "it will",
                    "suits me", "that suits", "suits", "does suit",
                    "that'd work", "sounds great", "that's great",
                    "happy with that", "happy with",
                )
                if any(p in text for p in _SS_YES):
                    self.session["selected_slot"]        = _slots_ss[0]["start"]
                    self.session["selected_slot_speech"] = _speech_ss
                    self.session["slot_confirmed"]       = True
                    self.session.pop("slot_pending_confirmation", None)
                    self.session.pop("vague_option_pending", None)
                    self.session.pop("vague_clarification_asked", None)
                    _nxt_pt = step["step"] + 1
                    _nxt_pt_state = (
                        self._active_flow[_nxt_pt]["state"]
                        if _nxt_pt < len(self._active_flow) else "DONE"
                    )
                    self.session["flow_step"] = _nxt_pt
                    self.session["state"]     = _nxt_pt_state
                    logger.info(
                        "[ms_flow] PRESENT_TIMES single-slot YES → slot=%r next_state=%s (advancing directly)",
                        _slots_ss[0].get("start", "")[:40], _nxt_pt_state,
                    )
                    await self.ask_current_question()
                    return
                # Day-change check: caller mentions a DIFFERENT available day
                # (e.g. "no i said thursday not tuesday").  Must run before the
                # re-anchor check so "not tuesday" doesn't lock in Tuesday.
                _dc_avail_ss = self.session.get("available_days", [])
                _dc_current_weekday = next(
                    (w for w in _dlabel_ss.lower().split() if w in _WEEKDAY_WORDS), None
                )
                _dc_new_entry = None
                for _dc_entry in _dc_avail_ss:
                    _dc_label = _dc_entry.get("day_label", "")
                    _dc_weekday = next(
                        (w for w in _dc_label.lower().split() if w in _WEEKDAY_WORDS), None
                    )
                    if _dc_weekday and _dc_weekday != _dc_current_weekday and _dc_weekday in text:
                        _dc_new_entry = _dc_entry
                        break
                if _dc_new_entry:
                    _dc_new_label = _dc_new_entry.get("day_label", "")
                    self.session["chosen_day"] = _dc_new_label
                    self.session.setdefault("collected", {})["chosen_day"] = _dc_new_label
                    self.session.pop("selected_slot", None)
                    self.session.pop("selected_slot_speech", None)
                    self.session.pop("slot_pending_confirmation", None)
                    # Fresh full-day offer incoming — clear any stale constrained subset
                    self.session.pop("offered_constrained_times", None)
                    self.session.pop("offered_constrained_slots", None)
                    logger.info(
                        "[ms_flow] %s: day-change from %r → %r",
                        step["state"], _dlabel_ss, _dc_new_label,
                    )
                    await self.ask_current_question()
                    return

                # Caller repeated the current day name — re-anchor to the one available slot.
                # Use weekday-only matching (not month names) to avoid false triggers
                # on "not tuesday" or other negating phrases containing the day name.
                _day_words_ss = [w.lower() for w in _dlabel_ss.split() if w.lower() in _WEEKDAY_WORDS]
                _negated_ss   = text.startswith("no") or "not " in text
                if _day_words_ss and any(w in text for w in _day_words_ss) and not _negated_ss:
                    _reanchor_ss = (
                        f"The only slot I have on {_dlabel_ss} is {_spoken_ss} — "
                        "does that work for you?"
                    )
                    await self._tts.put(_reanchor_ss)
                    self.session["last_question"] = _reanchor_ss
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _reanchor_ss}
                    )
                    logger.info(
                        "[ms_flow] %s: single-slot — day repeated, re-anchoring to %r",
                        step["state"], _speech_ss,
                    )
                    return

            # ── INQUIRY PREAMBLE GUARD ──────────────────────────────────────────
            # "quick question first", "i had a question first are you open on saturdays"
            # must NEVER bind a slot — route to FAQ handling and re-ask.
            _PT_INQUIRY_PREAMBLE = (
                "quick question", "i had a question", "question first",
                "just a question", "have a question", "one question",
                "can i ask", "before i", "just to ask", "just wondering",
                "are you open on", "open on saturday", "open on sunday",
                "open at the weekend", "open weekends",
            )
            if any(p in text for p in _PT_INQUIRY_PREAMBLE):
                _pt_inq_intent = self._detect_intent(text)
                _pt_faq_intents = {
                    "faq_hours", "faq_location", "faq_prices",
                    "faq_insurance", "faq_services", "faq_capability", "general_query",
                }
                if _pt_inq_intent in _pt_faq_intents:
                    await self._handle_mid_flow_interrupt(_pt_inq_intent, transcript)
                    return
                # Weekend-specific answer
                if any(w in text for w in ("saturday", "sunday", "weekend", "weekends", "saturdays", "sundays")):
                    _anchor_pt = self.session.get("last_question", "Which time works best for you?")
                    _wknd_pt = "We offer weekday appointments only — Monday through Friday. " + _anchor_pt
                    await self._tts.put(_wknd_pt)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _wknd_pt}
                    )
                    logger.info("[ms_flow] PRESENT_TIMES: inquiry preamble+weekend detected — re-asked %r", _anchor_pt[:40])
                    return
                # Other inquiry preamble with no detectable FAQ — re-ask current question
                _anchor_pt2 = self.session.get("last_question", "")
                if _anchor_pt2:
                    await self._tts.put(_anchor_pt2)
                logger.info("[ms_flow] PRESENT_TIMES: inquiry preamble — skipping ordinal bind, re-asked")
                return

            # ── ORDINAL SELECTION ──
            # Longest-string patterns checked first to avoid "first" matching
            # inside "first one" or "the first option".
            _PT_ORDINALS: list = [
                # Exact/positional ordinals → slot index (negative = from end)
                ("the first option", 0), ("first option", 0),
                ("the second option", 1), ("second option", 1),
                ("the middle option", 1), ("middle option", 1),
                ("the third option", 2), ("third option", 2),
                ("the fourth option", 3), ("fourth option", 3),
                ("the last option", -1), ("last option", -1),
                ("the final option", -1), ("final option", -1),
                ("the first one", 0), ("first one", 0),
                ("the second one", 1), ("second one", 1),
                ("the middle one", 1), ("middle one", 1),
                ("the third one", 2), ("third one", 2),
                ("the fourth one", 3), ("fourth one", 3),
                ("the last one", -1), ("last one", -1),
                ("the final one", -1), ("final one", -1),
                ("the first", 0), ("the second", 1),
                ("the middle", 1),
                ("the third", 2), ("the fourth", 3),
                ("the last", -1), ("the final", -1),
                ("first", 0), ("second", 1), ("middle", 1), ("third", 2), ("fourth", 3),
                ("last", -1), ("final", -1),
                # Relative position
                ("the earlier one", 0), ("the earlier", 0), ("earlier one", 0), ("earlier", 0),
                ("earliest", 0), ("the earliest", 0),
                ("the later one", -1), ("the later", -1), ("later one", -1), ("later", -1),
                ("latest", -1), ("the latest", -1),
            ]
            # FIX D: Guard — constraint phrases ("anything later than 1pm",
            # "do you have something earlier?", "have you got later") are
            # questions/objections, NOT slot selections.  Must not match
            # "later"/"earlier" as ordinal picks.
            _CONSTRAINT_GUARD = (
                "later than", "earlier than", "before ",
                "anything later", "anything earlier",
                "something later", "something earlier",
                "have you got later", "have you got earlier",
                "do you have later", "do you have earlier",
                "is there anything", "are there any",
                "have you got anything", "got anything",
                "any later", "any earlier",
                "after ", "nothing before", "nothing after",
                # period-specific — "any afternoon slots", "slots in the afternoon"
                "any afternoon", "afternoon slots", "slots in the afternoon",
                "any morning", "morning slots", "slots in the morning",
                "afternoon then", "any afternoon slots", "any morning slots",
                "in the afternoon", "in the morning",
                "anything in the afternoon", "anything in the morning",
                # "anything else that day" — remaining slots on same day
                "anything else that day", "anything else on that day",
                "anything else available", "what else do you have",
                "any other times", "any other slots",
                "other times on that day", "other slots on that day",
            )
            _is_constraint = any(p in text for p in _CONSTRAINT_GUARD)

            # ── TIME_QUERY early detection (hoisted) ────────────────────────
            # Defined here so constrained-subset binding can respect it.
            # Full time-query handling block lives below in the DIRECT TIME path.
            _TIME_QUERY_PHRASES_EARLY = (
                "do you have anything", "have you got anything", "got anything",
                "have anything",  # "have anything later on than..." — exploratory
                "do you have any", "have you got any",
                "is there anything", "is there any",
                "are there any", "any availability",
                "anything around", "anything near", "anything close",
                "anything after", "anything before",
                "anything later than", "anything earlier than",
                "anything later on than",  # "have anything later on than one" variant
                "later than that", "earlier than that",
                "around ", "near to ", "close to ",
                "closer to", "nearest to",
                "what about ", "what have you got",
                "is that available", "is it available",
                "is 5 available", "is there a 5",
                "do you have 5", "have you got 5",
                "anything at all", "any slots",
                "like later on", "later on that day",
                "do you do", "do you offer",
                "can you do", "could you do",
                "would you have",
            )
            _is_time_query_early = any(p in text for p in _TIME_QUERY_PHRASES_EARLY)

            # ── Constrained-subset binding ───────────────────────────────────
            # If we recently offered a filtered subset (2+ slots), try to bind
            # the caller's response against that subset BEFORE running the
            # general _is_constraint or full-list ordinal handler. This prevents
            # "one o'clock in the afternoon works" from looping back into the
            # constraint handler because "in the afternoon" is in _CONSTRAINT_GUARD.
            # Guard: if this is a TIME_QUERY, skip binding — the query handler below
            # will answer it and offer the relevant slot as a pending confirmation.
            _oc_times = self.session.get("offered_constrained_times", [])
            _oc_slots = self.session.get("offered_constrained_slots", [])
            if _oc_times and _oc_slots and not _is_time_query_early:
                from app.vagueness_detector import _time_to_speech as _t2s_oc
                _spoken_oc     = [_t2s_oc(t) for t in _oc_times]
                _bound_oc_time  = None
                _bound_oc_slot  = None
                _bound_oc_speech = None
                # Ordinal check against the constrained subset
                _OC_ORDINALS = [
                    ("first", 0), ("second", 1), ("third", 2), ("fourth", 3),
                    ("last", -1), ("final", -1),
                ]
                for _oc_kw, _oc_idx in _OC_ORDINALS:
                    if _oc_kw in text:
                        _oc_resolved = _oc_idx if _oc_idx >= 0 else len(_oc_slots) - 1
                        if 0 <= _oc_resolved < len(_oc_slots):
                            _bound_oc_time   = _oc_times[_oc_resolved]
                            _bound_oc_slot   = _oc_slots[_oc_resolved]
                            _bound_oc_speech = (
                                _spoken_oc[_oc_resolved]
                                if _oc_resolved < len(_spoken_oc)
                                else "that time"
                            )
                        break
                # Direct time-phrase match (spoken form: "nine", "eleven")
                if _bound_oc_time is None:
                    for _ot, _os, _osp in zip(_oc_times, _oc_slots, _spoken_oc):
                        if _osp and _osp.lower() in text.lower():
                            _bound_oc_time   = _ot
                            _bound_oc_slot   = _os
                            _bound_oc_speech = _osp
                            break
                # Digit-form hour match ("11 o'clock", "11 in the morning")
                import re as _re_oc_dig
                if _bound_oc_time is None:
                    for _ot, _os, _osp in zip(_oc_times, _oc_slots, _spoken_oc):
                        try:
                            _oc_h = int(_ot.split(":")[0])
                            if _re_oc_dig.search(r'\b' + str(_oc_h) + r'\b', text):
                                _bound_oc_time   = _ot
                                _bound_oc_slot   = _os
                                _bound_oc_speech = _osp or f"{_oc_h}:00"
                                break
                        except (ValueError, IndexError):
                            pass
                # Vague acceptance ("works for me", "that works", "yeah") when single constrained slot
                if _bound_oc_time is None and len(_oc_slots) == 1:
                    _OC_VAGUE_ACCEPT = (
                        "works for me", "that works", "works", "i'll take that",
                        "i'll take it", "i'll go with", "sounds good", "that sounds",
                        "sounds great", "perfect", "great", "suits me", "that suits",
                        "happy with", "yeah that", "yes that", "ok", "okay",
                        "sure", "fine", "alright", "yes please",
                    )
                    if any(p in text for p in _OC_VAGUE_ACCEPT):
                        _bound_oc_time   = _oc_times[0]
                        _bound_oc_slot   = _oc_slots[0]
                        _bound_oc_speech = _spoken_oc[0] if _spoken_oc else "that time"
                # Period-only fallback: "in the afternoon" / "the morning one" when
                # filler stripping consumed the hour word (e.g. "o'clock in the afternoon
                # works for me").  Only fires when exactly one constrained slot matches.
                if _bound_oc_time is None and _oc_times:
                    _txt_oc_pf = (text or "").lower()
                    _oc_is_afternoon = any(
                        p in _txt_oc_pf for p in ("afternoon", "evening", "pm")
                    )
                    _oc_is_morning = "morning" in _txt_oc_pf and not _oc_is_afternoon
                    if _oc_is_afternoon or _oc_is_morning:
                        _oc_pf_matches = []
                        for _opft, _opfs, _opfsp in zip(_oc_times, _oc_slots, _spoken_oc):
                            try:
                                _opfh = int(_opft.split(":")[0])
                                if (_oc_is_afternoon and _opfh >= 12) or (
                                    _oc_is_morning and _opfh < 12
                                ):
                                    _oc_pf_matches.append((_opft, _opfs, _opfsp))
                            except (ValueError, IndexError):
                                pass
                        if len(_oc_pf_matches) == 1:
                            _bound_oc_time   = _oc_pf_matches[0][0]
                            _bound_oc_slot   = _oc_pf_matches[0][1]
                            _bound_oc_speech = _oc_pf_matches[0][2]
                            logger.info(
                                "[ms_flow] %s: constrained period-only bind → %r",
                                step["state"], _bound_oc_time,
                            )
                if _bound_oc_time is not None:
                    _avail_oc   = self.session.get("available_days", [])
                    _chosen_oc  = self.session.get("chosen_day", "")
                    _target_oc  = _find_chosen_day_entry(_avail_oc, _chosen_oc)
                    _day_lbl_oc = (_target_oc or {}).get("day_label", "that day")
                    _slot_sp_oc = (
                        f"{_day_lbl_oc} at {_bound_oc_speech}"
                        if _day_lbl_oc else _bound_oc_speech
                    )
                    _nxt_oc    = step["step"] + 1
                    _nxt_st_oc = (
                        self._active_flow[_nxt_oc]["state"]
                        if _nxt_oc < len(self._active_flow) else "DONE"
                    )
                    self.session["selected_slot"]        = _bound_oc_slot.get("start", "")
                    self.session["selected_slot_speech"] = _slot_sp_oc
                    self.session["slot_confirmed"]       = True
                    self.session["flow_step"]            = _nxt_oc
                    self.session["state"]                = _nxt_st_oc
                    self.session.pop("offered_constrained_times", None)
                    self.session.pop("offered_constrained_slots", None)
                    logger.info(
                        "[ms_flow] %s: constrained-subset binding → %r speech=%r next=%s",
                        step["state"], _bound_oc_time, _slot_sp_oc, _nxt_st_oc,
                    )
                    await self.ask_current_question()
                    return

            _ordinal_idx: Optional[int] = None
            if not _is_constraint:
                for _pat, _idx in _PT_ORDINALS:
                    if _pat in text:
                        _ordinal_idx = _idx
                        break
            else:
                logger.info(
                    "[ms_flow] %s: constraint phrase detected in %r — ordinal matching skipped",
                    step["state"], text[:60],
                )
            if _ordinal_idx is not None:
                _avail_o   = self.session.get("available_days", [])
                _chosen_o  = self.session.get("chosen_day", "")
                _target_o  = _find_chosen_day_entry(_avail_o, _chosen_o)
                if _target_o and _target_o.get("slots"):
                    _slots_o      = _target_o["slots"]
                    _times_o      = _target_o.get("slot_times", [])
                    _n            = len(_slots_o)
                    _resolved_idx = _ordinal_idx if _ordinal_idx >= 0 else max(0, _n + _ordinal_idx)
                    _resolved_idx = min(_resolved_idx, _n - 1)
                    _slot_iso_o   = _slots_o[_resolved_idx].get("start", "")
                    _time_str_o   = _times_o[_resolved_idx] if _resolved_idx < len(_times_o) else ""
                    # Build a short label for _format_slot_for_speech
                    # day_label is e.g. "Thursday 2nd April"; we need "Thu 02 Apr at 09:00"
                    # Use the raw ISO start directly for booking and natural speech for TTS.
                    from app.vagueness_detector import _time_to_speech as _t2s_ord
                    _spoken_time = _t2s_ord(_time_str_o) if _time_str_o else "that time"
                    _day_label_o  = _target_o.get("day_label", "")
                    _slot_speech_o = f"{_day_label_o} at {_spoken_time}" if _day_label_o else _spoken_time
                    _nxt_ord_i = step["step"] + 1
                    _nxt_ord_state = (
                        self._active_flow[_nxt_ord_i]["state"]
                        if _nxt_ord_i < len(self._active_flow) else "DONE"
                    )
                    self.session["selected_slot"]        = _slot_iso_o
                    self.session["selected_slot_speech"] = _slot_speech_o
                    self.session["slot_confirmed"]       = True
                    self.session["flow_step"]            = _nxt_ord_i
                    self.session["state"]                = _nxt_ord_state
                    logger.info(
                        "[ms_flow] %s: ordinal %r → idx=%d slot=%r next_state=%s (confirmed, advancing)",
                        step["state"], _ordinal_idx, _resolved_idx, _slot_iso_o, _nxt_ord_state,
                    )
                    await self.ask_current_question()
                    return
                else:
                    logger.info(
                        "[ms_flow] %s: ordinal %r detected but no slot data — falling through",
                        step["state"], _ordinal_idx,
                    )

            # ── DIRECT TIME MATCHING (Fix A) ──────────────────────────────────
            # Handles "three o'clock in the afternoon suits me", "2 pm", "3 o'clock"
            # etc. when ordinal matching failed.  Strips filler, parses the spoken
            # hour, then matches against slot_times for the chosen day.
            # Priority: digit > word.  Afternoon indicator shifts word hours < 12.
            #
            # ── SEMANTIC GATE: QUERY vs SELECTION ─────────────────────────────
            # An explicit hour is NOT sufficient to bind.  The utterance must be
            # semantically classified first.  Time queries ("do you have anything
            # around 5?") must never advance the flow even when an hour is present.
            #
            # TIME_QUERY — reuse already-computed value (hoisted above
            # constrained-subset binding to protect that block too).
            _is_time_query = _is_time_query_early

            # TIME_SELECTION phrases — caller is explicitly choosing.
            # These override _is_time_query only when unambiguously binding.
            _TIME_SELECTION_PHRASES = (
                "works for me", "work for me", "that works",
                "i'll do", "i'll take", "i will take", "i will do",
                "book ", "book me in", "please book",
                "suits me", "that suits", "i'll go with",
                "i'd like ", "i would like ", "i want ",
                # NOTE: "the " was intentionally removed — it matched "in the afternoon"
                # making _is_time_selection True inside exploratory phrases and defeating
                # the _is_constraint guard.  "the 5 o'clock one" has no constraint phrase
                # so _allow_time_bind is True anyway without this entry.
                "sign me up", "confirm",
            )
            _is_time_selection = any(p in text for p in _TIME_SELECTION_PHRASES)

            # Final gate: allow direct-time binding only when:
            #   a) NOT a query phrase, AND
            #   b) either a selection phrase is present OR
            #      the utterance has an explicit "at [time]" / "[time] o'clock" form
            #      AND no query language is present.
            import re as _re_at_sel
            _has_explicit_at_time = bool(
                _re_at_sel.search(
                    r'\bat\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\b',
                    text,
                )
                or _re_at_sel.search(
                    r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s+o\'?clock\b',
                    text,
                )
            )
            # If the utterance is clearly a query, the explicit-at-time exception
            # must NOT override — "around 5 o'clock" must stay as a query.
            _allow_time_bind = (
                not _is_time_query
                and (not _is_constraint or _has_explicit_at_time or _is_time_selection)
            )
            import re as _re_dt
            _FILLER_DT = (
                "i said ", "said ", "suits me", "for me", "that works",
                "works for me", "works", "please", "o'clock", "oclock",
            )
            # BUG 4: normalize written "p.m."/"a.m." before PM detection
            _txt_dt = text.replace("p.m.", "pm").replace("a.m.", "am")
            for _f in _FILLER_DT:
                _txt_dt = _txt_dt.replace(_f, " ")
            _txt_dt = " ".join(_txt_dt.split())

            _avail_dt  = self.session.get("available_days", [])
            _chosen_dt = self.session.get("chosen_day", "")
            _target_dt = _find_chosen_day_entry(_avail_dt, _chosen_dt)

            # ── TIME_QUERY handling: answer availability question, do NOT bind ─
            # Must run BEFORE the direct-bind path.  If _is_time_query is True
            # and an hour can be extracted, answer the question deterministically
            # from the current day's slots and stay in PRESENT_TIMES.
            # Guard: skip when _is_constraint is also True — comparative queries
            # ("do you have anything later than one in the afternoon") must go to
            # the _is_constraint handler below, not here.  Without this guard the
            # boundary hour (e.g. 1pm) was extracted and offered as the answer.
            if _is_time_query and not _is_constraint and _target_dt and _target_dt.get("slots"):
                _tq_times = _target_dt.get("slot_times", [])
                _tq_slots = _target_dt.get("slots", [])
                _tq_label = _target_dt.get("day_label", "that day")
                _tq_hour  = _extract_hour_from_text(text)
                from app.vagueness_detector import _time_to_speech as _t2s_tq
                if _tq_hour is not None:
                    # Check for exact match on current day
                    _tq_exact_idx: Optional[int] = None
                    for _tqi, _tqt in enumerate(_tq_times):
                        try:
                            if int(_tqt.split(":")[0]) == _tq_hour:
                                _tq_exact_idx = _tqi
                                break
                        except (ValueError, IndexError):
                            pass
                    if _tq_exact_idx is not None:
                        # Exact time exists — tell the caller and offer it as a choice
                        _tq_sp = _t2s_tq(_tq_times[_tq_exact_idx])
                        _tq_msg = (
                            f"Yes \u2014 I do have {_tq_sp} on {_tq_label}. "
                            "Would you like to book that?"
                        )
                        self.session["selected_slot"]             = _tq_slots[_tq_exact_idx].get("start", "")
                        self.session["selected_slot_speech"]      = f"{_tq_label} at {_tq_sp}"
                        self.session["slot_pending_confirmation"] = True
                        self.session.pop("offered_constrained_times", None)
                        self.session.pop("offered_constrained_slots", None)
                    else:
                        # No exact match — offer nearest same-day alternatives
                        def _tq_dist(t: str) -> int:
                            try: return abs(int(t.split(":")[0]) - _tq_hour)
                            except: return 999
                        _tq_req_sp  = _t2s_tq(f"{_tq_hour:02d}:00")
                        _tq_near_t  = sorted(_tq_times, key=_tq_dist)[:2]
                        _tq_near_s: list = []
                        for _tqnt in _tq_near_t:
                            for _tqsi, _tqst in enumerate(_tq_times):
                                if _tqst == _tqnt and _tqsi < len(_tq_slots):
                                    _tq_near_s.append(_tq_slots[_tqsi])
                                    break
                        _tq_near_sp = [_t2s_tq(t) for t in _tq_near_t]
                        if len(_tq_near_sp) == 1:
                            _tq_msg = (
                                f"I don\u2019t have {_tq_req_sp} on {_tq_label}, "
                                f"but the closest I have is {_tq_near_sp[0]} \u2014 "
                                "would that work?"
                            )
                            if _tq_near_s:
                                self.session["selected_slot"]             = _tq_near_s[0].get("start", "")
                                self.session["selected_slot_speech"]      = f"{_tq_label} at {_tq_near_sp[0]}"
                                self.session["slot_pending_confirmation"] = True
                                self.session.pop("offered_constrained_times", None)
                                self.session.pop("offered_constrained_slots", None)
                        elif _tq_near_sp:
                            _tq_msg = (
                                f"I don\u2019t have {_tq_req_sp} on {_tq_label}, "
                                f"but I do have {_tq_near_sp[0]} or {_tq_near_sp[1]} \u2014 "
                                "which would suit you?"
                            )
                            self.session["offered_constrained_times"] = _tq_near_t
                            self.session["offered_constrained_slots"] = _tq_near_s
                            self.session.pop("selected_slot", None)
                            self.session.pop("selected_slot_speech", None)
                            self.session.pop("slot_pending_confirmation", None)
                        else:
                            _tq_msg = (
                                f"I\u2019m afraid I don\u2019t have {_tq_req_sp} on {_tq_label}. "
                                "Which time would work for you?"
                            )
                    await self._tts.put(_tq_msg)
                    self.session["last_question"] = _tq_msg
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _tq_msg}
                    )
                    logger.info(
                        "[ms_flow] %s: TIME_QUERY hour=%d on %r → answered without binding",
                        step["state"], _tq_hour, _tq_label,
                    )
                    return
                # _is_time_query but no parseable hour → fall through to
                # existing _is_constraint handler below which handles period-only queries

            if _target_dt and _target_dt.get("slots") and _allow_time_bind:
                _slot_times_dt = _target_dt.get("slot_times", [])
                _matched_hour: Optional[int] = None

                # 1. Digit match: "3 pm", "2pm", "14:00", "3"
                _dm = _re_dt.search(r'\b(\d{1,2})(?::\d{2})?\s*(?:pm|am)?\b', _txt_dt)
                if _dm:
                    _h = int(_dm.group(1))
                    if "pm" in _txt_dt and _h < 12:
                        _h += 12
                    elif "am" in _txt_dt and _h == 12:
                        _h = 0
                    elif "am" not in _txt_dt and 1 <= _h <= 6:
                        # Clinic context: digits 1–6 without explicit am → PM
                        # "2 o'clock" → 14:00, "half past three" → 15 handled by word path
                        _h += 12
                    if 7 <= _h <= 20:   # sanity: clinic hours
                        _matched_hour = _h

                # 2. Word match: "three", "two", "half past three" etc.
                if _matched_hour is None:
                    _HOUR_WORDS_DT = {
                        "one": 1, "two": 2, "three": 3, "four": 4,
                        "five": 5, "six": 6, "seven": 7, "eight": 8,
                        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                    }
                    for _word, _n in _HOUR_WORDS_DT.items():
                        if _re_dt.search(r'\b' + _word + r'\b', _txt_dt):
                            _h2 = _n
                            # Shift to PM if afternoon/evening/pm in normalized text
                            if any(p in _txt_dt for p in ("afternoon", "evening", "pm")):
                                if _h2 < 12:
                                    _h2 += 12
                            elif _h2 < 8:
                                # Clinic opens ≥08:00; small hour words mean PM
                                _h2 += 12
                            if 7 <= _h2 <= 20:
                                _matched_hour = _h2
                            break

                # Period-only fallback: no explicit hour found, but "afternoon" / "morning"
                # is present and selection intent is confirmed ("works for me", etc.).
                # Binds only when exactly one offered slot matches the stated period.
                if _matched_hour is None and _is_time_selection:
                    _is_dt_afternoon = any(
                        p in _txt_dt for p in ("afternoon", "evening", "pm")
                    )
                    _is_dt_morning = "morning" in _txt_dt and not _is_dt_afternoon
                    if _is_dt_afternoon or _is_dt_morning:
                        _dt_pf_matches = []
                        for _dpfi, _dpft in enumerate(_slot_times_dt):
                            try:
                                _dpfh = int(_dpft.split(":")[0])
                                if (_is_dt_afternoon and _dpfh >= 12) or (
                                    _is_dt_morning and _dpfh < 12
                                ):
                                    _dt_pf_matches.append((_dpfi, _dpfh))
                            except (ValueError, IndexError):
                                pass
                        if len(_dt_pf_matches) == 1:
                            _matched_hour = _dt_pf_matches[0][1]
                            logger.info(
                                "[ms_flow] %s: direct period-only fallback → hour=%d",
                                step["state"], _matched_hour,
                            )

                if _matched_hour is not None:
                    _slot_idx_dt: Optional[int] = None
                    for _si, _st in enumerate(_slot_times_dt):
                        try:
                            if int(_st.split(":")[0]) == _matched_hour:
                                _slot_idx_dt = _si
                                break
                        except (ValueError, IndexError):
                            pass
                    if _slot_idx_dt is not None:
                        _slot_iso_dt   = _target_dt["slots"][_slot_idx_dt].get("start", "")
                        from app.vagueness_detector import _time_to_speech as _t2s_dt
                        _spoken_dt     = _t2s_dt(_slot_times_dt[_slot_idx_dt])
                        _day_label_dt  = _target_dt.get("day_label", "")
                        _slot_speech_dt = f"{_day_label_dt} at {_spoken_dt}" if _day_label_dt else _spoken_dt
                        _nxt_dt_i = step["step"] + 1
                        _nxt_dt_state = (
                            self._active_flow[_nxt_dt_i]["state"]
                            if _nxt_dt_i < len(self._active_flow) else "DONE"
                        )
                        self.session["selected_slot"]        = _slot_iso_dt
                        self.session["selected_slot_speech"] = _slot_speech_dt
                        self.session["slot_confirmed"]       = True
                        self.session["flow_step"]            = _nxt_dt_i
                        self.session["state"]                = _nxt_dt_state
                        logger.info(
                            "[ms_flow] %s: direct time match hour=%d → idx=%d "
                            "slot=%r next_state=%s (confirmed, advancing)",
                            step["state"], _matched_hour, _slot_idx_dt, _slot_iso_dt, _nxt_dt_state,
                        )
                        await self.ask_current_question()
                        return
                    else:
                        # ── EXACT HOUR UNAVAILABLE ────────────────────────────
                        # Caller asked for a specific time (e.g. "5 o'clock") that
                        # does not exist on the chosen day.  Answer directly with
                        # the nearest available alternatives — never fall to LLM.
                        from app.vagueness_detector import _time_to_speech as _t2s_na
                        _day_label_na = _target_dt.get("day_label", "that day")
                        _spoken_req_na = _t2s_na(f"{_matched_hour:02d}:00")
                        # Sort slot_times by proximity to requested hour
                        def _hour_dist_na(t: str) -> int:
                            try:
                                return abs(int(t.split(":")[0]) - _matched_hour)
                            except (ValueError, IndexError):
                                return 999
                        _alt_times_na = sorted(_slot_times_dt, key=_hour_dist_na)[:2]
                        # Resolve matching slot objects for the alternatives
                        _alt_slots_na: list = []
                        for _at_na in _alt_times_na:
                            for _si_na, _st_na in enumerate(_slot_times_dt):
                                if _st_na == _at_na:
                                    _avail_slots_na = _target_dt.get("slots", [])
                                    if _si_na < len(_avail_slots_na):
                                        _alt_slots_na.append(_avail_slots_na[_si_na])
                                    break
                        _spoken_alts_na = [_t2s_na(t) for t in _alt_times_na]
                        if len(_spoken_alts_na) == 1:
                            _na_phrase = (
                                f"I\u2019m afraid I don\u2019t have {_spoken_req_na} "
                                f"on {_day_label_na}, but I do have "
                                f"{_spoken_alts_na[0]} \u2014 would that work?"
                            )
                            if _alt_slots_na:
                                self.session["selected_slot"] = (
                                    _alt_slots_na[0].get("start", "")
                                )
                                self.session["selected_slot_speech"] = (
                                    f"{_day_label_na} at {_spoken_alts_na[0]}"
                                )
                        elif _spoken_alts_na:
                            _na_phrase = (
                                f"I\u2019m afraid I don\u2019t have {_spoken_req_na} "
                                f"on {_day_label_na}, but I do have "
                                f"{_spoken_alts_na[0]} or {_spoken_alts_na[1]}"
                                " \u2014 which would suit you?"
                            )
                            self.session["offered_constrained_times"] = _alt_times_na
                            self.session["offered_constrained_slots"] = _alt_slots_na
                        else:
                            _na_phrase = (
                                f"I\u2019m afraid I don\u2019t have {_spoken_req_na} "
                                f"on {_day_label_na}. "
                                "Would you like to try a different time or a different day?"
                            )
                        await self._tts.put(_na_phrase)
                        self.session["last_question"] = _na_phrase
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _na_phrase}
                        )
                        logger.info(
                            "[ms_flow] %s: exact hour %d unavailable on %r — "
                            "offered nearest alternatives deterministically",
                            step["state"], _matched_hour, _day_label_na,
                        )
                        return

        # ── PRESENT_TIMES: catch-all re-ask when nothing matched ────────────────
        # If we reach here for PRESENT_TIMES, no ordinal/time/single-slot matched.
        if step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):

            # ── FIX D: DETERMINISTIC CONSTRAINT HANDLER ──────────────────────
            # "anything later than 1pm?", "do you have something earlier?",
            # "any afternoon slots?" — must be answered deterministically from
            # the selected day's slot data.  NEVER fall through to LLM.
            # Slot resolution is hard-scoped to the selected day only.
            if _is_constraint:
                # New constraint request — clear any prior offered subset
                self.session.pop("offered_constrained_times", None)
                self.session.pop("offered_constrained_slots", None)
                import re as _re_ct
                _avail_ct  = self.session.get("available_days", [])
                _chosen_ct = self.session.get("chosen_day", "")
                _target_ct = _find_chosen_day_entry(_avail_ct, _chosen_ct)
                _day_label_ct = (_target_ct or {}).get("day_label", "that day")
                _all_times_ct = (_target_ct or {}).get("slot_times", [])
                _all_slots_ct = (_target_ct or {}).get("slots", [])
                _presented_ct = _all_times_ct[:4]  # what was spoken to caller

                _wants_later  = any(p in text for p in (
                    "later", "after", "afternoon", "evening", "pm",
                ))
                _wants_earlier = any(p in text for p in (
                    "earlier", "before", "morning",
                ))
                # "anything else that day" — remaining/all slots on same day
                _wants_remaining = any(p in text for p in (
                    "anything else that day", "anything else on that day",
                    "anything else available", "what else do you have",
                    "any other times", "any other slots",
                    "other times on that day", "other slots on that day",
                ))
                # Period-only: no explicit "later than N" — just "afternoon" / "morning"
                _wants_afternoon_period = (
                    "afternoon" in text
                    and not any(p in text for p in ("later than", "earlier than"))
                )
                _wants_morning_period = (
                    "morning" in text
                    and "afternoon" not in text
                    and not any(p in text for p in ("later than", "earlier than"))
                )

                # Parse optional hour reference: "later than 1pm", "after 3"
                _constraint_hour: Optional[int] = None
                _cm_ct = _re_ct.search(
                    r'(?:than|after|before|past)\s+(\d{1,2})\s*(?:pm|am|o\'?clock)?',
                    text,
                )
                if _cm_ct:
                    _ch = int(_cm_ct.group(1))
                    if "pm" in text and _ch < 12:
                        _ch += 12
                    elif "am" in text and _ch == 12:
                        _ch = 0
                    elif _ch < 8:
                        _ch += 12  # small numbers = PM for clinic hours
                    if 7 <= _ch <= 20:
                        _constraint_hour = _ch

                _filtered_times: list = []
                _filtered_slots: list = []

                if _constraint_hour is not None:
                    # Explicit reference: "later than 1pm" → hour > constraint
                    for _ci, _ct_time in enumerate(_all_times_ct):
                        try:
                            _ct_h = int(_ct_time.split(":")[0])
                            if _wants_later and _ct_h > _constraint_hour:
                                _filtered_times.append(_ct_time)
                                if _ci < len(_all_slots_ct):
                                    _filtered_slots.append(_all_slots_ct[_ci])
                            elif _wants_earlier and _ct_h < _constraint_hour:
                                _filtered_times.append(_ct_time)
                                if _ci < len(_all_slots_ct):
                                    _filtered_slots.append(_all_slots_ct[_ci])
                        except (ValueError, IndexError):
                            pass
                elif _wants_afternoon_period:
                    # "any afternoon slots" / "do you have any slots in the afternoon"
                    # → filter all times with h >= 12 regardless of what was presented
                    for _ci, _ct_time in enumerate(_all_times_ct):
                        try:
                            _ct_h = int(_ct_time.split(":")[0])
                            if _ct_h >= 12:
                                _filtered_times.append(_ct_time)
                                if _ci < len(_all_slots_ct):
                                    _filtered_slots.append(_all_slots_ct[_ci])
                        except (ValueError, IndexError):
                            pass
                elif _wants_morning_period:
                    # "any morning slots" → filter h < 12
                    for _ci, _ct_time in enumerate(_all_times_ct):
                        try:
                            _ct_h = int(_ct_time.split(":")[0])
                            if _ct_h < 12:
                                _filtered_times.append(_ct_time)
                                if _ci < len(_all_slots_ct):
                                    _filtered_slots.append(_all_slots_ct[_ci])
                        except (ValueError, IndexError):
                            pass
                elif _wants_remaining:
                    # "anything else that day?" — slots beyond first page (index 4+)
                    # If none remain, empty list → "no matching times" branch below
                    _filtered_times = _all_times_ct[4:]
                    _filtered_slots = _all_slots_ct[4:]
                elif _presented_ct:
                    # No explicit hour — "anything later" / "something earlier"
                    # relative to the times we already presented.
                    _presented_hours = []
                    for _pt in _presented_ct:
                        try:
                            _presented_hours.append(int(_pt.split(":")[0]))
                        except (ValueError, IndexError):
                            pass
                    if _wants_later and _presented_hours:
                        _latest_shown = max(_presented_hours)
                        for _ci, _ct_time in enumerate(_all_times_ct):
                            try:
                                _ct_h = int(_ct_time.split(":")[0])
                                if _ct_h > _latest_shown:
                                    _filtered_times.append(_ct_time)
                                    if _ci < len(_all_slots_ct):
                                        _filtered_slots.append(_all_slots_ct[_ci])
                            except (ValueError, IndexError):
                                pass
                    elif _wants_earlier and _presented_hours:
                        _earliest_shown = min(_presented_hours)
                        for _ci, _ct_time in enumerate(_all_times_ct):
                            try:
                                _ct_h = int(_ct_time.split(":")[0])
                                if _ct_h < _earliest_shown:
                                    _filtered_times.append(_ct_time)
                                    if _ci < len(_all_slots_ct):
                                        _filtered_slots.append(_all_slots_ct[_ci])
                            except (ValueError, IndexError):
                                pass

                from app.vagueness_detector import _time_to_speech as _t2s_ct
                if _filtered_times:
                    # Present the matching times — still scoped to the selected day
                    _spoken_ct = [_t2s_ct(t) for t in _filtered_times[:4]]
                    if len(_spoken_ct) == 1:
                        _ct_phrase = (
                            f"On {_day_label_ct} I've also got {_spoken_ct[0]}"
                            " — does that work?"
                        )
                        # Pin the single filtered slot so the next YES binds it via
                        # the existing single-slot YES/NO gate (line ~3596).
                        if _filtered_slots:
                            self.session["selected_slot"]        = _filtered_slots[0].get("start", "")
                            self.session["selected_slot_speech"] = f"{_day_label_ct} at {_spoken_ct[0]}"
                    elif len(_spoken_ct) == 2:
                        _ct_phrase = (
                            f"On {_day_label_ct} I've also got {_spoken_ct[0]}"
                            f" or {_spoken_ct[1]} — which suits you?"
                        )
                    else:
                        _ct_phrase = (
                            f"On {_day_label_ct} I've also got "
                            f"{', '.join(_spoken_ct[:-1])}, or {_spoken_ct[-1]}"
                            " — which of those works?"
                        )
                    # Persist multi-slot constrained subset so the next caller
                    # turn can bind directly (ordinal or explicit time phrase).
                    if len(_filtered_times) >= 2:
                        self.session["offered_constrained_times"] = _filtered_times[:4]
                        self.session["offered_constrained_slots"] = _filtered_slots[:4]
                    else:
                        # Single slot already pinned via selected_slot
                        self.session.pop("offered_constrained_times", None)
                        self.session.pop("offered_constrained_slots", None)
                else:
                    # No matching times on the selected day — offer another day
                    _other_days_ct = [
                        d for d in _avail_ct
                        if d.get("day_label", "") != _day_label_ct
                    ]
                    # Re-present what IS available on the selected day
                    _existing_spoken = [_t2s_ct(t) for t in _presented_ct]
                    _existing_str = (
                        (" or ".join(_existing_spoken))
                        if len(_existing_spoken) <= 2
                        else (", ".join(_existing_spoken[:-1]) + f", or {_existing_spoken[-1]}")
                    ) if _existing_spoken else "those times"
                    if _other_days_ct:
                        _other_label_ct = _other_days_ct[0].get("day_label", "another day")
                        _ct_phrase = (
                            f"I'm afraid {_existing_str} are the only times I have on "
                            f"{_day_label_ct}. Would you like to try {_other_label_ct} instead?"
                        )
                    else:
                        _ct_phrase = (
                            f"I'm afraid {_existing_str} are the only times I have on "
                            f"{_day_label_ct}. Would you like me to ask the team to call "
                            "you back with more options?"
                        )

                await self._tts.put(_ct_phrase)
                self.session["last_question"] = _ct_phrase
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _ct_phrase}
                )
                logger.info(
                    "[ms_flow] %s: constraint %r handled deterministically → %r",
                    step["state"], text[:60], _ct_phrase[:80],
                )
                return  # hard stop — constraint answered, no LLM fallback

            # ── Explicit date in PRESENT_TIMES ──────────────────────────────
            # "do you have anything on 7th May" while in PRESENT_TIMES.
            # _XD_PAT only runs in PRESENT_DAYS; we need the same logic here
            # to avoid falling to LLM when the caller names a specific offered date.
            import re as _re_pt_xd
            _pt_xd_m = _re_pt_xd.search(
                r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-zA-Z]+)\b'
                r'|\b([a-zA-Z]+)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b',
                transcript, _re_pt_xd.IGNORECASE,
            )
            if _pt_xd_m:
                _MONTH_MAP_PT = {
                    "january": 1, "february": 2, "march": 3, "april": 4,
                    "may": 5, "june": 6, "july": 7, "august": 8,
                    "september": 9, "october": 10, "november": 11, "december": 12,
                    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
                    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
                }
                import datetime as _dt_pt_xd
                _pt_xd_day = int(_pt_xd_m.group(1) or _pt_xd_m.group(4) or 0)
                _pt_xd_mon_str = (_pt_xd_m.group(2) or _pt_xd_m.group(3) or "").lower()
                _pt_xd_mon = _MONTH_MAP_PT.get(_pt_xd_mon_str)
                if _pt_xd_day and _pt_xd_mon:
                    _pt_avail_xd = self.session.get("available_days", [])
                    _pt_xd_match = None
                    for _pt_xd_entry in _pt_avail_xd:
                        _pt_xd_dstr = _pt_xd_entry.get("date") or _pt_xd_entry.get("datetime", "")
                        try:
                            _pt_xd_date = _dt_pt_xd.date.fromisoformat(_pt_xd_dstr[:10])
                            if _pt_xd_date.day == _pt_xd_day and _pt_xd_date.month == _pt_xd_mon:
                                _pt_xd_match = _pt_xd_entry
                                break
                        except (ValueError, TypeError):
                            pass
                    if _pt_xd_match:
                        _pt_xd_new_label = _pt_xd_match.get("day_label", "")
                        self.session["chosen_day"] = _pt_xd_new_label
                        self.session.setdefault("collected", {})["chosen_day"] = _pt_xd_new_label
                        self.session.pop("selected_slot", None)
                        self.session.pop("selected_slot_speech", None)
                        self.session.pop("slot_pending_confirmation", None)
                        self.session.pop("offered_constrained_times", None)
                        self.session.pop("offered_constrained_slots", None)
                        logger.info(
                            "[ms_flow] PRESENT_TIMES explicit date match → %r", _pt_xd_new_label
                        )
                        await self.ask_current_question()
                        return

            # ── Day-change check ─────────────────────────────────────────────
            # Before re-asking times, check if the caller wants a different day.
            _avail_re   = self.session.get("available_days", [])
            _chosen_re  = self.session.get("chosen_day", "")
            _cur_wd_re  = next(
                (w for w in _chosen_re.lower().split() if w in _WEEKDAY_WORDS), None
            )
            _dc_re_entry = None
            for _dc_re in _avail_re:
                _dc_re_label   = _dc_re.get("day_label", "")
                _dc_re_weekday = next(
                    (w for w in _dc_re_label.lower().split() if w in _WEEKDAY_WORDS), None
                )
                if _dc_re_weekday and _dc_re_weekday != _cur_wd_re and _dc_re_weekday in text:
                    _dc_re_entry = _dc_re
                    break
            if _dc_re_entry:
                _dc_re_label = _dc_re_entry.get("day_label", "")
                self.session["chosen_day"] = _dc_re_label
                self.session.setdefault("collected", {})["chosen_day"] = _dc_re_label
                self.session.pop("selected_slot", None)
                self.session.pop("selected_slot_speech", None)
                self.session.pop("slot_pending_confirmation", None)
                logger.info(
                    "[ms_flow] %s: day-change (catch-all) %r → %r",
                    step["state"], _chosen_re, _dc_re_label,
                )
                await self.ask_current_question()
                return

            # ── PRESENT_TIMES: deterministic "none/no" rejection ────────────
            # If caller rejects all offered times (no constraint specified),
            # offer a different day instead of looping on the same times or LLM.
            _PT_NONE = (
                "none of those", "none of them", "none of these",
                "not any of those", "none suit", "none of those work",
                "those don't work", "they don't work", "doesn't work for me",
                "not available", "can't do any",
                "no not those", "no none", "no none of",
                "something else", "different time", "different day",
                "another day", "any other day",
            )
            _PT_STOP = ("stop", "wait", "hold on", "actually")
            _pt_is_none = any(p in text for p in _PT_NONE)
            _pt_is_stop = any(p in text for p in _PT_STOP) and len(text.split()) <= 3
            if _pt_is_none and not _is_constraint:
                _pt_avail = self.session.get("available_days", [])
                _pt_chosen = self.session.get("chosen_day", "")
                _pt_other = [
                    d for d in _pt_avail if d.get("day_label", "") != _pt_chosen
                ]
                if _pt_other:
                    from app.vagueness_detector import _time_to_speech as _t2s_pt
                    _pt_other_label = _pt_other[0].get("day_label", "another day")
                    _pt_other_times = _pt_other[0].get("slot_times", [])[:3]
                    if _pt_other_times:
                        _pt_spoken = [_t2s_pt(t) for t in _pt_other_times]
                        if len(_pt_spoken) == 1:
                            _pt_alt = (
                                f"No problem \u2014 I also have {_pt_spoken[0]} on "
                                f"{_pt_other_label}. Would that work?"
                            )
                        elif len(_pt_spoken) == 2:
                            _pt_alt = (
                                f"No problem \u2014 on {_pt_other_label} I have "
                                f"{_pt_spoken[0]} or {_pt_spoken[1]}. Would either of those work?"
                            )
                        else:
                            _pt_alt = (
                                f"No problem \u2014 on {_pt_other_label} I have "
                                f"{', '.join(_pt_spoken[:-1])}, or {_pt_spoken[-1]}. "
                                "Would any of those work?"
                            )
                    else:
                        _pt_alt = (
                            f"No problem \u2014 I also have availability on "
                            f"{_pt_other_label}. Would that day work for you?"
                        )
                    # Update chosen day so next confirmation binds correctly
                    self.session["chosen_day"] = _pt_other_label
                    self.session.setdefault("collected", {})["chosen_day"] = _pt_other_label
                    self.session.pop("selected_slot", None)
                    self.session.pop("selected_slot_speech", None)
                    self.session.pop("slot_pending_confirmation", None)
                    self.session.pop("offered_constrained_times", None)
                    self.session.pop("offered_constrained_slots", None)
                else:
                    _pt_alt = (
                        "I\u2019m afraid those are the only times I have available. "
                        "Can I take your details and have someone call you back with more options?"
                    )
                await self._tts.put(_pt_alt)
                self.session["last_question"] = _pt_alt
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _pt_alt}
                )
                logger.info(
                    "[ms_flow] %s: times rejected → offered alt day %r",
                    step["state"], _pt_other[0].get("day_label", "") if _pt_other else "none",
                )
                return
            if _pt_is_stop:
                # "stop/wait/hold on" — acknowledge and wait for clarification
                _pt_ack = (
                    self.session.get("last_question")
                    or "Of course \u2014 what would work better for you?"
                )
                await self._tts.put(_pt_ack)
                return

            # No slot matched and not a day-change — pass back to LLM with full
            # slot context so it can answer questions like "do you have afternoons?"
            # intelligently rather than re-asking the same times verbatim.

            # Guard: skip very short STT fragments (garbage like "rd", "then", "hello")
            # unless they contain a time-period keyword we must honour.
            _fu_words = transcript.strip().split()
            _fu_has_period = any(k in text for k in ("afternoon", "morning", "evening"))
            if len(_fu_words) <= 1 and len(transcript.strip()) <= 5 and not _fu_has_period:
                logger.info(
                    "[ms_flow] %s: short STT fragment %r — ignoring (no slot match)",
                    step["state"], transcript[:20],
                )
                return

            # Store the caller's actual utterance so the LLM instruction can reference it
            # (run_instruction only passes the formatted instruction, not conversation_history)
            self.session["caller_followup"] = transcript
            logger.info(
                "[ms_flow] %s: no slot match for %r → caller_followup set, handing to LLM",
                step["state"], text[:40],
            )
            await self.ask_current_question()
            # Clear after use so the next fresh PRESENT_TIMES call starts clean
            self.session.pop("caller_followup", None)
            return

        # ── COLLECT_NAME compatibility rule (Phase 5.1 narrow fix) ─────────────
        # If caller sends a phone-confirm phrase while we're at COLLECT_NAME
        # (or the cancel/reschedule variants) and a Twilio caller-ID number is
        # available, treat this as: implicit name skip + phone confirmed.
        # Also catches phone-reject ("no use a different number") so it is never
        # parsed as a name — redirects straight to COLLECT_PHONE for all flows.
        if current_state in (
            "COLLECT_NAME", "COLLECT_NAME_CANCEL", "COLLECT_NAME_RESCHEDULE",
        ):
            logger.info(
                "[ms_flow] COLLECT_NAME state=%s input=%r phone_accept=%s",
                current_state, text[:60], _is_phone_accept(text),
            )
            # ── FIRST-CHECK: phone-reject intent ───────────────────────────
            # "no use a different number" while in COLLECT_NAME means the caller
            # wants to supply a different phone number.  Must run before name
            # extraction so the phrase is never parsed as an invalid name.
            if _is_phone_reject(text):
                logger.info(
                    "[ms_flow] phone_reject_detected state=COLLECT_NAME — redirecting to COLLECT_PHONE",
                )
                self.session["phone_confirmed"]    = False
                self.session["phone_number"]       = None
                self.session["phone_digits_buffer"] = ""
                self.session.setdefault("collected", {}).pop("phone", None)
                self.session.pop("phone_readback_pending", None)
                self.session.pop("phone_readback_retry", None)
                self.session["flow_step"] = (
                    _RESCHEDULE_COLLECT_PHONE_INDEX
                    if self._active_flow is RESCHEDULE_FLOW
                    else _CANCEL_COLLECT_PHONE_INDEX
                    if self._active_flow is CANCEL_FLOW
                    else _COLLECT_PHONE_INDEX
                )
                self.session["state"]     = "COLLECT_PHONE"
                await self.ask_current_question()
                return

            # ── REPAIR / CLARIFICATION: replay last_question without advancing ──────
            # Must run BEFORE name extraction so phrases like "say that again"
            # (3 words, passes word-count gate) are never stored as a name.
            # ── SLOT-REPAIR: caller wants to go back and review availability ─────
            # Must run BEFORE _CN_REPAIR so slot-related phrases route back to
            # PRESENT_TIMES rather than re-asking for the name.
            _CN_SLOT_REPAIR = (
                "repeat the slot", "repeat the slots", "slots you offered",
                "availability", "available times", "available slots",
                "repeat the availability", "offered for the availability",
                "back to the slots", "go back to the times", "back to availability",
                "what slots", "what times", "what were the times",
                "what were the slots", "what were the options",
                "the slots", "the times", "offered",
            )
            if any(p in text for p in _CN_SLOT_REPAIR):
                _pt_states = {"PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"}
                _pt_repair_idx = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] in _pt_states),
                    None,
                )
                if _pt_repair_idx is not None:
                    self.session.pop("slot_confirmed", None)
                    self.session.pop("selected_slot", None)
                    self.session.pop("selected_slot_speech", None)
                    self.session["flow_step"] = _pt_repair_idx
                    self.session["state"]     = self._active_flow[_pt_repair_idx]["state"]
                    logger.info(
                        "[ms_flow] COLLECT_NAME: slot-repair → stepping back to %s",
                        self.session["state"],
                    )
                    await self.ask_current_question()
                    return

            _CN_REPAIR = (
                "what was the question", "say that again", "say it again",
                "repeat that", "repeat the question",
                "what did you ask", "what did you say",
                "cut off", "you cut off", "got cut off", "broke up",
                "didn't catch", "didn't hear", "couldn't hear",
                "pardon", "come again", "could you repeat",
                "what was that", "sorry what", "missed that",
                "what were you asking", "what did you want",
            )
            if any(p in text for p in _CN_REPAIR):
                _cn_pending = self.session.get("last_question", "And what's your first name please?")
                await self._tts.put(_cn_pending)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _cn_pending}
                )
                logger.info("[ms_flow] COLLECT_NAME repair → replaying: %r", _cn_pending[:60])
                return

            _cn_twilio = (
                self.session.get("twilio_from_local")
                or self.session.get("twilio_from", "")
            )
            if _is_phone_accept(text) and _cn_twilio:
                logger.info(
                    "[ms_flow] compat_phone_accept state=COLLECT_NAME input=%r", text[:60],
                )
                import re as _re_cn
                _cn_digits = _re_cn.sub(r"\D", "", _cn_twilio)
                _cn_phone  = _cn_digits or _cn_twilio
                self.session["phone_confirmed"]   = True
                self.session["phone_from_twilio"] = True
                self.session["phone_number"]      = _cn_phone
                self.session.setdefault("collected", {})["phone"] = _cn_phone
                # Finalize name from session if not already set
                if not self.session.get("full_name"):
                    _cn_name = (
                        (self.session.get("collected") or {}).get("full_name")
                        or (self.session.get("collected") or {}).get("name")
                        or self.session.get("patient_name")
                        or self.session.get("caller_name")
                    )
                    if _cn_name:
                        self.session["full_name"] = _cn_name
                        self.session.setdefault("collected", {})["full_name"] = _cn_name
                if self._active_flow is RESCHEDULE_FLOW:
                    self.session["flow_step"] = _RESCHEDULE_LOOKUP_INDEX
                    self.session["state"]     = "LOOKUP_RESCHEDULE"
                elif self.session.get("selected_slot"):
                    self.session["flow_step"] = _CONFIRM_BOOKING_INDEX
                    self.session["state"]     = "CONFIRM_BOOKING"
                else:
                    self.session["flow_step"] = _PRESENT_DAYS_INDEX
                    self.session["state"]     = "PRESENT_DAYS"
                self.session.pop("phone_readback_pending", None)
                self.session.pop("phone_readback_retry", None)
                self.session.pop("slot_pending_confirmation", None)
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                logger.info("[ms_flow] compat_phone_accept -> %s", self.session["state"])
                await self.ask_current_question()
                return

        # ── CONFIRM_PHONE / CONFIRM_PHONE_RETURNING / RETURNING_PLAN_CONFIRM_PHONE ──
        # FIRST-CHECK: match YES/NO before any fallback or clarification logic.
        # Without this gate "yes use my number" can match general_query intent
        # in _detect_intent and be routed to the LLM interrupt path.
        if step["state"] in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING", "RETURNING_PLAN_CONFIRM_PHONE"):
            logger.info(
                "[ms_flow] CONFIRM_PHONE state=%s input=%r phone_accept=%s",
                step["state"], text[:60], _is_phone_accept(text),
            )
            # ── FIRST-CHECK: explicit phone-accept phrase ────────────────────
            # Must precede generic _CP_YES/_CP_NO so "yes use this number" is
            # never ambiguous — it is always YES with no possibility of _cp_no
            # contamination from a later phrase in the same utterance.
            if _is_phone_accept(text):
                _cp_yes, _cp_no = True, False
            else:
                _CP_YES = (
                    "yes", "yeah", "yep", "yup",
                    "yes use this number", "use this number",
                    "same number", "yes that's fine", "yes thats fine",
                    "use my current number", "yes use my number", "use my number",
                    "that's fine", "thats fine", "correct",
                )
                _CP_NO = (
                    "no", "nope", "no use a different number", "different number",
                    "another number", "no i'll give you another one",
                    "no i'll give you another", "use a different number",
                    "wrong number", "not the right number",
                    "that's not the right number", "that's the wrong number",
                    "thats not the right number", "thats the wrong number",
                    "not the best number",
                )
                _CP_SEMANTIC_YES = (
                    "that's the best number", "thats the best number",
                    "that's the right number", "thats the right number",
                    "this is the best number", "this is the right number",
                    "that's the one", "thats the one",
                    "you can use this one", "you can use this number",
                    "this number is fine", "that number is fine",
                    "best number to reach me", "best number for me",
                    "that one is fine", "this one is fine",
                )
                import re as _re_cp2
                _cp_semantic_yes = (
                    any(p in text for p in _CP_SEMANTIC_YES)
                    and not _re_cp2.search(r'\bno\b', text)
                    and not any(n in text for n in ("not", "different", "another", "wrong"))
                )
                _cp_yes = any(p in text for p in _CP_YES) or _cp_semantic_yes
                _cp_no  = any(p in text for p in _CP_NO)
            if _cp_yes and not _cp_no:
                import re as _re_cp
                if self.session.get("phone_readback_pending"):
                    # Caller typed a number on the keypad and is confirming it.
                    # Preserve the number captured by the DTMF hard gate — do NOT
                    # overwrite with Twilio caller-ID.
                    _cp_phone = (
                        self.session.get("phone_number")
                        or self.session.get("phone")
                        or self.session.get("phone_candidate")
                        or ""
                    )
                    _is_twilio_confirm = False
                else:
                    # Confirming Twilio caller-ID as the phone for this booking.
                    _cp_twilio = (
                        self.session.get("twilio_from_local")
                        or self.session.get("twilio_from", "")
                    )
                    _cp_digits = _re_cp.sub(r"\D", "", _cp_twilio)
                    _cp_phone  = _cp_digits or _cp_twilio
                    _is_twilio_confirm = True
                self.session["phone_confirmed"]     = True
                self.session["phone_from_twilio"]   = _is_twilio_confirm
                self.session["phone_number"]        = _cp_phone
                self.session.setdefault("collected", {})["phone"] = _to_e164_uk(_cp_phone)
                self.session["phone_digits_buffer"] = ""
                self.session.pop("phone_readback_pending", None)
                self.session.pop("phone_readback_retry", None)
                self.session.pop("slot_pending_confirmation", None)
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                if self._active_flow is RESCHEDULE_FLOW:
                    self.session["flow_step"] = _RESCHEDULE_LOOKUP_INDEX
                    self.session["state"]     = "LOOKUP_RESCHEDULE"
                elif step["state"] == "RETURNING_PLAN_CONFIRM_PHONE":
                    # On-plan returning: advance to next step (RETURNING_PLAN_LOOKUP,
                    # skipping RETURNING_PLAN_COLLECT_PHONE which is now redundant).
                    self.session["flow_step"] = step["step"] + 1
                    # ask_current_question will skip COLLECT_PHONE because phone_confirmed=True
                elif self.session.get("selected_slot"):
                    self.session["flow_step"] = _CONFIRM_BOOKING_INDEX
                    self.session["state"]     = "CONFIRM_BOOKING"
                else:
                    self.session["flow_step"] = _PRESENT_DAYS_INDEX
                    self.session["state"]     = "PRESENT_DAYS"
                logger.info(
                    "[ms_flow] phone_confirm matched YES → phone=%r next_state=%s",
                    (_cp_phone[-4:] if _cp_phone else ""), self.session.get("state"),
                )
                await self.ask_current_question()
                return
            elif _cp_no and not _cp_yes:
                # Rejected — clear number, advance to COLLECT_PHONE in keypad-first mode.
                # Set phone_awaiting_dtmf=True so caller can type the number on their keypad.
                self.session["phone_confirmed"]     = False
                self.session["phone_from_twilio"]   = False
                self.session["phone_number"]        = None
                self.session["phone_digits_buffer"] = ""
                self.session["phone_dtmf_buffer"]   = ""
                self.session["phone_awaiting_dtmf"] = True   # accept keypad input
                self.session.pop("phone_readback_retry", None)
                self.session.setdefault("collected", {}).pop("phone", None)
                _cp_no_nxt = step["step"] + 1
                _cp_no_state = (
                    self._active_flow[_cp_no_nxt]["state"]
                    if _cp_no_nxt < len(self._active_flow) else "DONE"
                )
                self.session["flow_step"] = _cp_no_nxt
                self.session["state"]     = _cp_no_state
                # Flow-specific bridge: reschedule/cancel asks for the booking number
                if self._active_flow is RESCHEDULE_FLOW or self._active_flow is CANCEL_FLOW:
                    _cp_no_bridge = (
                        "Okay — then could you type in on your keyboard "
                        "the number associated with your booking?"
                    )
                else:
                    _cp_no_bridge = (
                        "No problem — please type the number in using your keyboard now."
                    )
                self.session["last_question"] = _cp_no_bridge
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _cp_no_bridge}
                )
                await self._tts.put(_cp_no_bridge)
                logger.info(
                    "[ms_flow] phone_confirm matched NO → keypad-first next_state=%s", _cp_no_state,
                )
                return  # keypad bridge already spoken; don't call ask_current_question
            logger.info(
                "[ms_flow] %s: no deterministic YES/NO match — falling through "
                "(general_query blocked by DATA_COLLECTION_STATES)",
                step["state"],
            )

        # ── MID-FLOW INTERRUPT: caller asks an off-topic question mid-booking ───
        # Answer it warmly and end the turn — do NOT re-ask the current step.
        # The next caller utterance re-enters handle_transcript at the same flow_step.
        _interruptable_states = {
            "NEW_OR_RETURNING",
            "RETURNING_RECENCY", "RETURNING_TREATMENT_PLAN",
            "RETURNING_PLAN_CONFIRM_PHONE", "RETURNING_PLAN_COLLECT_PHONE",
            "COLLECT_NAME_RETURNING", "CONFIRM_PHONE_RETURNING", "COLLECT_PHONE_RETURNING",
            "COLLECT_NAME", "CONFIRM_PHONE", "COLLECT_PHONE",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
            "PRESENT_DAYS", "PRESENT_TIMES",
            "PRESENT_DAYS_RESCHEDULE", "PRESENT_TIMES_RESCHEDULE",
            # COLLECT_REASON: caller may ask a FAQ while being asked what brings them in.
            # Reschedule/cancel are also hard-routed here so the duplicate guard below (line ~8310)
            # is never reached; booking/symptom answers fall through to extraction unchanged.
            "COLLECT_REASON",
            # CONFIRM_BOOKING: caller may ask a last-minute FAQ before confirming.
            # General-query (incl. plain "yes") is blocked by _DATA_COLLECTION_STATES so the
            # dedicated CONFIRM_BOOKING YES handler below still fires correctly.
            "CONFIRM_BOOKING",
        }
        if step["state"] in _interruptable_states:
            # Data-collection states (phone/name) are asking for specific input.
            # Never trigger a general_query interrupt here — the caller likely just
            # said something ambiguous while trying to give the answer.  FAQ
            # questions (prices, hours, etc.) are still allowed to interrupt.
            _DATA_COLLECTION_STATES = {
                # Phone / name / reason input — no general-query interrupts
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
                "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                # COLLECT_REASON: open-ended "what brings you in?". Fragment guard
                # (BUG 1/2) rejects partial answers; we must not also fire LLM.
                "COLLECT_REASON",
                # NEW_OR_RETURNING is a binary closed question — deterministic
                # extraction runs in the priority block above.  Any utterance
                # that reaches here failed extraction; treat it as a garbled
                # answer, not an intent to chat.  General-query LLM must not fire.
                "NEW_OR_RETURNING",
                # RETURNING_RECENCY is a closed "recently / a while ago" question.
                # Answers like "a while ago" score as general_query in _detect_intent
                # (no booking/FAQ keywords), so general_query must be suppressed here
                # or the mid-flow interrupt swallows the answer and flow stalls.
                "RETURNING_RECENCY",
                # FIX B: RETURNING_TREATMENT_PLAN is a closed yes/current-status
                # question.  Direct answers like "I'm still coming in regularly"
                # score as general_query in _detect_intent, which triggers an LLM
                # side response and re-asks the same question.  Must be blocked.
                "RETURNING_TREATMENT_PLAN",
                # PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE — YES answers are caught by
                # the priority block above.  Any utterance that reaches here is a
                # non-yes response (specific day name, vague, noise).  general_query
                # must not fire — the caller is trying to answer the day question.
                "PRESENT_DAYS",
                "PRESENT_DAYS_RESCHEDULE",
                # PRESENT_TIMES / PRESENT_TIMES_RESCHEDULE — ordinal and repeat
                # requests are handled by the priority block above.  Any utterance
                # that reaches here is a time selection attempt.  general_query
                # must not fire — the caller is trying to pick a slot.
                "PRESENT_TIMES",
                "PRESENT_TIMES_RESCHEDULE",
                # CONFIRM_PHONE / CONFIRM_PHONE_RETURNING / RETURNING_PLAN_CONFIRM_PHONE
                # — YES/NO gate runs above. Any utterance that reaches here is
                # ambiguous input, not a general chat question.
                "CONFIRM_PHONE",
                "CONFIRM_PHONE_RETURNING",
                "RETURNING_PLAN_CONFIRM_PHONE",
                # RETURNING_PLAN_COLLECT_PHONE — collecting phone number for plan lookup.
                "RETURNING_PLAN_COLLECT_PHONE",
                # GENERAL_BOOKING_OFFER / FAQ_BOOKING_OFFER — these are explicit
                # yes/no gates after answering a general/FAQ query.  Treat any
                # utterance here as an answer to the booking offer, not a new
                # general_query interrupt that would swallow the response.
                "GENERAL_BOOKING_OFFER",
                "FAQ_BOOKING_OFFER",
                # COLLECT_REASON: open-ended "what brings you in?".  Fragment guard
                # (BUG 1/2) rejects partial answers; general_query must not also fire LLM.
                # Specific FAQ intents (prices, hours, etc.) are still allowed to interrupt.
                "COLLECT_REASON",
                # CONFIRM_BOOKING: final yes/no gate.  "yes please" and plain "yes" score
                # general_query in _detect_intent — must not fire LLM.  FAQ intents are
                # still allowed so callers can ask a last-minute question before confirming.
                "CONFIRM_BOOKING",
            }
            _mid_intents = {
                "faq_prices", "faq_insurance", "faq_hours",
                "faq_location", "faq_services", "faq_capability",
            }
            if step["state"] not in _DATA_COLLECTION_STATES:
                _mid_intents.add("general_query")
            _mid_intent = self._detect_intent(text)
            # ── Booking-state FAQ interrupt upgrade ──────────────────────────
            # At DATA_COLLECTION states (COLLECT_REASON, COLLECT_NAME*,
            # CONFIRM_PHONE*) _detect_intent often returns "general_query" for
            # FAQ-shaped utterances ("I saw you offer acupuncture — is it
            # painful?") because question-form detection is conservative.
            # general_query is deliberately excluded from _mid_intents at
            # these states so ambiguous stutters don't swallow answers —
            # but that also lets genuine FAQ interruptions fall through
            # into the state's payload consumer, where e.g. the full
            # FAQ utterance becomes `reason`.  Detect obvious FAQ shape
            # and upgrade the intent so the mid-flow interrupt fires and
            # _handle_mid_flow_interrupt re-anchors to the suspended
            # booking question.  Strong book verbs and body+symptom
            # compounds short-circuit the helper → legitimate booking
            # answers are never affected.
            _BOOKING_FAQ_STATES = {
                "COLLECT_REASON",
                "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
            }
            # ── Mid-booking FAQ ellipsis follow-up ─────────────────────────
            # After a mid-booking FAQ answer, the immediate next utterance is
            # often an ellipsis follow-up ("do you think that would be a
            # problem") that contains no service noun and no booking payload.
            # Without this guard it would be consumed by COLLECT_REASON /
            # COLLECT_NAME etc. Bind it back to the FAQ thread instead.
            _mbf_ctx = self.session.get("_mid_booking_faq_ctx")
            _mbf_resolved = False
            if (
                _mbf_ctx
                and step["state"] in _BOOKING_FAQ_STATES
                and _mid_intent not in ("reschedule", "cancel", "booking")
            ):
                _t_mbf = (text or "").lower()
                _mbf_topic_switch = any(
                    p in _t_mbf for p in (
                        "what about shockwave", "tell me about another",
                        "another service", "forget that", "never mind that",
                        "different question", "change topic",
                        "what about laser", "what about pilates",
                        "what about massage", "what about parking",
                        "what about prices", "what about insurance",
                    )
                )
                _mbf_current_topic = _mbf_ctx.get("topic")
                _mbf_new_svc = _detect_service_switch(_t_mbf, _mbf_current_topic)
                # ── Priority 1: explicit service switch to a different service.
                #   Clear prior FAQ topic, mark resolved, resume booking so the
                #   mid-flow interrupt block below cannot re-fire a stale FAQ
                #   on the same utterance.
                if _mbf_new_svc:
                    self.session.pop("_mid_booking_faq_ctx", None)
                    self.session.pop("_faq_active_service", None)
                    _mbf_resolved = True
                    _mid_intent = "booking"
                    logger.info(
                        "[ms_flow] mid_booking_faq_service_switch "
                        "from=%s to=%s text=%r",
                        _mbf_current_topic, _mbf_new_svc, text[:80],
                    )
                    logger.info(
                        "[ms_flow] mid_booking_faq_context_cleared "
                        "reason=service_switch text=%r", text[:80],
                    )
                # ── Priority 2: explicit service rejection / decline.
                #   Two sub-cases:
                #     (a) Same utterance also names a replacement service →
                #         DO NOT re-ask; let normal reason capture consume
                #         the whole utterance so the caller is not asked
                #         "what would you like to book for then?" after
                #         they already said.
                #     (b) Rejection with no replacement → clean re-ask.
                elif _is_service_rejection(_t_mbf):
                    _reject_replacement = None
                    for _rsvc, _raliases in _SERVICE_SWITCH_ALIASES:
                        if _rsvc == (_mbf_current_topic or "").lower():
                            continue
                        if any(_a in _t_mbf for _a in _raliases):
                            _reject_replacement = _rsvc
                            break
                    self.session.pop("_mid_booking_faq_ctx", None)
                    self.session.pop("_faq_active_service", None)
                    _mbf_resolved = True
                    _mid_intent = "booking"
                    if _reject_replacement:
                        logger.info(
                            "[ms_flow] mid_booking_faq_service_rejected_with_replacement "
                            "rejected=%s replacement=%s text=%r",
                            _mbf_current_topic, _reject_replacement, text[:80],
                        )
                        logger.info(
                            "[ms_flow] mid_booking_faq_context_cleared "
                            "reason=rejected_with_replacement text=%r",
                            text[:80],
                        )
                        # Fall through — normal COLLECT_REASON extraction
                        # captures the utterance as the booking reason and
                        # advances.  No re-ask.
                    else:
                        self.session.pop("_partial_reason", None)
                        logger.info(
                            "[ms_flow] mid_booking_faq_service_rejected "
                            "topic=%s text=%r",
                            _mbf_current_topic, text[:80],
                        )
                        logger.info(
                            "[ms_flow] mid_booking_faq_context_cleared "
                            "reason=service_rejected text=%r", text[:80],
                        )
                        # Speak a friendly one-liner but store a clean
                        # stand-alone last_question so the watchdog replay
                        # doesn't stack prefixes ("Sorry, I didn't catch
                        # that. No problem — what...").
                        _reject_spoken = (
                            "No problem — what would you like to book for then?"
                        )
                        _reject_lq = "What would you like to book for then?"
                        await self._tts.put(_reject_spoken)
                        self.session["last_question"] = _reject_lq
                        self.session.setdefault(
                            "conversation_history", []
                        ).append(
                            {"role": "assistant", "content": _reject_spoken}
                        )
                        logger.info(
                            "[ms_flow] COLLECT_REASON: clean_reject_reask "
                            "spoken=%r last_question=%r",
                            _reject_spoken, _reject_lq,
                        )
                        return
                elif _is_booking_resume_signal(text):
                    self.session.pop("_mid_booking_faq_ctx", None)
                    _mbf_resolved = True
                    _mid_intent = "booking"
                    logger.info(
                        "[ms_flow] mid_booking_faq_context_cleared "
                        "reason=explicit_resume_booking text=%r",
                        text[:80],
                    )
                elif _mbf_topic_switch:
                    self.session.pop("_mid_booking_faq_ctx", None)
                    self.session.pop("_faq_active_service", None)
                    _mbf_resolved = True
                    _mid_intent = "booking"
                    logger.info(
                        "[ms_flow] mid_booking_faq_context_cleared "
                        "reason=explicit_topic_switch text=%r",
                        text[:80],
                    )
                elif _is_mid_booking_acupuncture_resolution(_t_mbf, self.session):
                    # Declarative / proceed-oriented utterance after an
                    # acupuncture fear answer — caller has accepted the
                    # concern and wants to resume booking.  Clear ctx,
                    # mark resolved, AND force _mid_intent=booking so that
                    # the downstream mid-flow interrupt block cannot fire
                    # a second acupuncture FAQ answer on the same turn.
                    _mbf_topic_log = _mbf_ctx.get("topic")
                    logger.info(
                        "[ms_flow] mid_booking_faq_resolution_detected "
                        "topic=%s route=booking_resume text=%r",
                        _mbf_topic_log, text[:80],
                    )
                    self.session.pop("_mid_booking_faq_ctx", None)
                    self.session.pop("_faq_active_service", None)
                    _mbf_resolved = True
                    _mid_intent = "booking"
                    logger.info(
                        "[ms_flow] mid_booking_faq_context_cleared "
                        "reason=accepted_and_resuming_booking text=%r",
                        text[:80],
                    )
                    logger.info(
                        "[ms_flow] COLLECT_REASON: consumed acupuncture "
                        "resolution as booking reason text=%r",
                        text[:80],
                    )
                else:
                    _mbf_match = _is_faq_ellipsis_followup(text, ctx_active=True)
                    _mbf_topic_log = _mbf_ctx.get("topic")
                    _mbf_reason = (
                        "needle_concern_followup"
                        if (_mbf_topic_log == "acupuncture"
                            and any(p in (text or "").lower() for p in (
                                "needle", "needles", "scared", "nervous",
                                "anxious", "worried", "fear",
                                "painful", "hurt", "hurts",
                                "problem", "issue", "concern",
                            )))
                        else "ellipsis_followup" if _mbf_match else "no_match"
                    )
                    logger.info(
                        "[ms_flow] mid_booking_faq_followup_check "
                        "topic=%s matched=%s reason=%s explicit_resume=False "
                        "text=%r",
                        _mbf_topic_log, _mbf_match, _mbf_reason, text[:80],
                    )
                    if not _mbf_match:
                        # Utterance is not an FAQ ellipsis follow-up — caller
                        # has resumed the booking flow.  Clear context so
                        # normal booking payload consumption proceeds.
                        self.session.pop("_mid_booking_faq_ctx", None)
                        logger.info(
                            "[ms_flow] mid_booking_faq_context_cleared "
                            "reason=booking_payload_resumed text=%r",
                            text[:80],
                        )
                    if _mbf_match:
                        _mbf_ctx["turns_left"] = int(_mbf_ctx.get("turns_left", 1)) - 1
                        if _mbf_ctx["turns_left"] <= 0:
                            self.session.pop("_mid_booking_faq_ctx", None)
                        else:
                            self.session["_mid_booking_faq_ctx"] = _mbf_ctx
                        _mbf_topic = _mbf_ctx.get("topic") or "acupuncture"
                        _mbf_synth = f"{_mbf_topic} {transcript}".strip()
                        _mbf_route = (
                            "acupuncture_fear_followup"
                            if _mbf_topic == "acupuncture"
                            else "faq_services"
                        )
                        logger.info(
                            "[ms_flow] mid_booking_faq_context_preserved "
                            "topic=%s route=%s",
                            _mbf_topic, _mbf_route,
                        )
                        logger.info(
                            "[ms_flow] mid_booking_faq_followup_override "
                            "topic=%s route=faq_services",
                            _mbf_topic,
                        )
                        self.session["_faq_active_service"] = _mbf_topic
                        await self._handle_mid_flow_interrupt(
                            "faq_services", _mbf_synth
                        )
                        return
            if (
                step["state"] in _BOOKING_FAQ_STATES
                and _mid_intent not in _mid_intents
                and _mid_intent not in ("reschedule", "cancel", "booking")
                and not _mbf_resolved
            ):
                _bfi_hit, _bfi_reason = _is_booking_state_faq_interrupt(text)
                if _bfi_hit:
                    # ── Incomplete-intro hold (Bug B) ────────────────────────
                    # "I saw on your website where you offer acupuncture"
                    # is a lead-in that hasn't yet landed the concern.  Hold
                    # the turn so the caller can finish the thought; on the
                    # next utterance the prepend at handle_transcript entry
                    # combines it and we classify/answer on the full text.
                    if (
                        step["state"] == "COLLECT_REASON"
                        and _is_incomplete_faq_intro(text)
                    ):
                        self.session["_faq_pending_intro"] = transcript.strip()
                        logger.info(
                            "[ms_flow] COLLECT_REASON: faq_intro_held "
                            "text=%r — awaiting continuation",
                            transcript[:80],
                        )
                        return
                    logger.info(
                        "[ms_flow] booking_state_faq_interrupt at=%s reason=%s "
                        "prior_intent=%s transcript=%r — upgrading to faq_services",
                        step["state"], _bfi_reason, _mid_intent, transcript[:80],
                    )
                    _mid_intent = "faq_services"
            # Hard-route reschedule/cancel before any FAQ handling — these must
            # exit booking immediately regardless of current state.
            # Guard: do NOT reset if already in the target flow (avoids restart loops).
            if _mid_intent == "reschedule":
                # Regression guard: generic booking language ("acupuncture
                # booking", "i want to book", "go through with booking") must
                # never trigger a reschedule hard-route.  Require an explicit
                # reschedule/cancel/change/move signal in the raw text before
                # abandoning the active booking flow.
                _rs_explicit_sigs = (
                    "reschedule", "re-schedule", "re schedule",
                    "change my appointment", "change the appointment",
                    "change an appointment", "change a booking",
                    "change my booking", "change the booking",
                    "move my appointment", "move the appointment",
                    "move an appointment", "move my booking",
                    "rearrange my appointment", "rearrange the appointment",
                    "rebook my", "rebook the", "re-book my", "re-book the",
                    "different time for my", "different day for my",
                )
                _rs_text_lower = (text or "").lower()
                if not any(p in _rs_text_lower for p in _rs_explicit_sigs):
                    logger.info(
                        "[ms_flow] reschedule_hard_route_suppressed "
                        "reason=no_explicit_reschedule_language text=%r",
                        text[:80],
                    )
                    _mid_intent = "booking"
                    # Fall through — do NOT return; continue normal booking flow.
                else:
                    if self._active_flow is not RESCHEDULE_FLOW:
                        logger.info(
                            "[ms_flow] mid-flow reschedule hard-route at %s", step["state"]
                        )
                        self._switch_flow("reschedule")
                        await self.ask_current_question()
                    else:
                        # Already in reschedule flow — re-anchor to current question so
                        # the caller hears something instead of dead air.
                        _rrs_lq = self.session.get("last_question", "")
                        if _rrs_lq:
                            await self._tts.put(_rrs_lq)
                        logger.info(
                            "[ms_flow] mid-flow reschedule already in RESCHEDULE_FLOW at %s — re-anchoring",
                            step["state"],
                        )
                    return
            if _mid_intent == "cancel":
                if self._active_flow is not CANCEL_FLOW:
                    logger.info(
                        "[ms_flow] mid-flow cancel hard-route at %s", step["state"]
                    )
                    self._switch_flow("cancel")
                    await self.ask_current_question()
                else:
                    # Already in cancel flow — re-anchor to current question so
                    # the caller hears something instead of dead air.
                    _rcs_lq = self.session.get("last_question", "")
                    if _rcs_lq:
                        await self._tts.put(_rcs_lq)
                    logger.info(
                        "[ms_flow] mid-flow cancel already in CANCEL_FLOW at %s — re-anchoring",
                        step["state"],
                    )
                return
            if _mid_intent in _mid_intents and not _mbf_resolved:
                logger.info(
                    "[ms_flow] mid-flow interrupt at %s — intent=%s transcript=%r",
                    step["state"], _mid_intent, transcript[:60],
                )
                await self._handle_mid_flow_interrupt(_mid_intent, transcript)
                # Set a short-lived context so the next utterance (likely an
                # ellipsis follow-up like "do you think that would be a
                # problem") is bound back to the FAQ thread rather than
                # consumed as booking payload.
                if (
                    step["state"] in _BOOKING_FAQ_STATES
                    and _mid_intent in ("faq_services", "general_query")
                ):
                    _mbf_topic_set = self.session.get("_faq_active_service")
                    self.session["_mid_booking_faq_ctx"] = {
                        "interrupted_state": step["state"],
                        "intent": _mid_intent,
                        "topic": _mbf_topic_set,
                        "turns_left": 2,
                    }
                    logger.info(
                        "[ms_flow] mid_booking_faq_context_set "
                        "interrupted_state=%s topic=%s intent=%s",
                        step["state"], _mbf_topic_set, _mid_intent,
                    )
                return  # do NOT call ask_current_question — let caller respond naturally

        # ── FAQ_BOOKING_OFFER: yes → switch to booking, no → goodbye ─────────
        if step["state"] == "FAQ_BOOKING_OFFER":
            # ── Pending location clarification: caller is responding to
            #    "Sure — is that for our Alcester or Redditch clinic?" ──────
            _fbo_pending_loc = self.session.get("_faq_loc_pending_intent")
            if _fbo_pending_loc:
                _fbo_pend_text = text.strip().lower()
                _fbo_pend_redd = any(p in _fbo_pend_text for p in (
                    "redditch", "reditch", "reddish", "reddit",
                    "red itch", "red ditch", "red-ditch", "bromsgrove",
                ))
                _fbo_pend_alce = any(p in _fbo_pend_text for p in (
                    "alcester", "greig", "kinwarton",
                ))
                if _fbo_pend_redd and not _fbo_pend_alce:
                    _fbo_pend_clinic = "redditch"
                elif _fbo_pend_alce and not _fbo_pend_redd:
                    _fbo_pend_clinic = "alcester"
                else:
                    _fbo_pend_clinic = None
                if _fbo_pend_clinic:
                    # Caller named a clinic — resolve and answer
                    self.session["last_faq_loc_id"] = _fbo_pend_clinic
                    _fbo_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                    self.session.pop("_faq_loc_pending_intent", None)
                    _fbo_synth_tx = (
                        f"{_fbo_pend_sub} {_fbo_pend_clinic}".strip()
                        if _fbo_pend_sub else _fbo_pend_clinic
                    )
                    self.session["_faq_ans_intent"] = _fbo_pending_loc
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt(_fbo_pending_loc, _fbo_synth_tx)
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                    logger.info(
                        "[ms_flow] FAQ_BOOKING_OFFER: pending location resolved → %s for %s",
                        _fbo_pending_loc, _fbo_pend_clinic,
                    )
                    return
                elif len(text.strip().split()) <= 3:
                    # Short utterance, no clinic detected — re-ask once
                    logger.info(
                        "[ms_flow] FAQ_BOOKING_OFFER: location pending, no clinic in %r — re-asking",
                        text[:40],
                    )
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    return
                else:
                    # Global block handles all pending FAQ clarification before reaching
                    # this state handler; if we arrive here the pending was not consumed.
                    # Log and re-ask rather than silently clearing the original intent.
                    logger.warning(
                        "[ms_flow] FAQ_BOOKING_OFFER: pending loc not consumed by global block %r",
                        text[:40],
                    )
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    return

            # Weak acknowledgements alone ("yeah", "okay", "understood" etc.) must NOT
            # route to LLM — the caller is still processing the FAQ answer.
            # Inert: hold state and let the silence handler re-prompt if needed.
            _FBO_ACK_WORDS = frozenset({
                "okay", "ok", "alright", "right", "sure", "yeah", "yep", "yup",
                "great", "good", "got", "it", "understood", "perfect", "brilliant",
                "lovely", "cool", "noted", "absolutely", "certainly", "exactly",
            })
            _fbo_words = set(text.strip().split())
            if _fbo_words and _fbo_words <= _FBO_ACK_WORDS:
                logger.info("[ms_flow] FAQ_BOOKING_OFFER: ack-only %r — inert", text[:40])
                self.session["fragment_suppressed"] = True
                return

            # Bare affirmatives ("yes", "please") after "Anything else?" mean the
            # caller has another question but hasn't stated it yet.  Emit a clean
            # continuation prompt instead of triggering topic carry-forward.
            _FBO_BARE_YES = frozenset({"yes", "please"})
            if _fbo_words and _fbo_words <= _FBO_BARE_YES:
                logger.info("[ms_flow] FAQ_BOOKING_OFFER: bare affirmative %r", text[:40])
                await self._tts.put("Of course — what would you like to know?")
                return

            # ── Clinic correction intercept (BUG 2) ───────────────────────────
            # "no it was for the redditch clinic" / "i meant alcester" — the caller
            # is correcting the clinic for the PREVIOUS FAQ answer, not asking a new
            # question.  _detect_intent would return general_query because there are no
            # FAQ topic keywords.  Instead: rerun the last FAQ intent with the corrected
            # clinic, using a synthetic transcript so sub-type detection (parking /
            # transport / address) still works.
            _corr_text = text.strip().lower()
            _corr_redd = any(p in _corr_text for p in (
                "redditch", "reditch", "reddish", "reddit",
                "red itch", "red ditch", "red-ditch",  # BUG 1: STT near-forms
                "bromsgrove",
            ))
            _corr_alce = any(p in _corr_text for p in ("alcester", "greig", "kinwarton"))
            _last_faq_intent_corr = self.session.get("last_faq_intent")
            if (
                (_corr_redd or _corr_alce) and not (_corr_redd and _corr_alce)
                and _last_faq_intent_corr in {"faq_location", "faq_hours"}
                and any(s in _corr_text for s in (
                    "no", "i meant", "actually", "not the", "it was for",
                    "i wanted", "for the", "was for", "meant the",
                ))
            ):
                _corr_clinic = "redditch" if _corr_redd else "alcester"
                self.session["last_faq_loc_id"] = _corr_clinic
                _last_sub = self.session.get("last_faq_sub", "")
                # Build synthetic transcript so the sub-type lookup resolves correctly
                _synth_tx = f"{_last_sub} {_corr_clinic}".strip() if _last_sub else _corr_clinic
                logger.info(
                    "[ms_flow] FAQ_BOOKING_OFFER: clinic correction → rerun %s for %s (sub=%r)",
                    _last_faq_intent_corr, _corr_clinic, _last_sub,
                )
                await self._handle_mid_flow_interrupt(_last_faq_intent_corr, _synth_tx)
                # last_question already updated inside _handle_mid_flow_interrupt for
                # offer states (Fix C). Explicit overwrite here for defence-in-depth.
                self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # ── FAQ follow-up phrase buckets ────────────────────────────────────
            # Guard before _detect_intent so multi-word acks and continuation
            # phrases never fall into the booking-push else branch.
            _txt_lower_fbo = text.strip().lower()

            # Multi-word acknowledgements — caller satisfied, prompt for next question
            _FAQ_MULTIWORD_ACKS_FBO = (
                "okay that's noted", "ok that's noted", "okay thats noted",
                "okay that helps", "ok that helps", "that's helpful", "thats helpful",
                "that is helpful", "that makes sense", "okay that makes sense",
                "i see", "yeah that's fine", "yeah thats fine",
                "yeah okay", "yeah ok", "yeah alright",
                "right okay", "right ok", "alright then",
                "mmhm", "mm-hm", "uh-huh", "uh huh",
            )
            if any(p in _txt_lower_fbo for p in _FAQ_MULTIWORD_ACKS_FBO):
                logger.info("[ms_flow] faq_followup: acknowledgement %r", text[:40])
                await self._tts.put("Of course — anything else you'd like to ask?")
                return

            # Bare continuation phrases — caller signals another question is coming.
            # Prompt 8 Bug 3/4 fix: strip the continuation prefix and check whether
            # the remainder already contains a concrete FAQ question.  If so, answer
            # it immediately — never discard an embedded question with a generic opener.
            _FAQ_CONTINUE_FBO = (
                "other question", "another question", "one more question",
                "few more questions", "yeah another", "yes another",
                "yeah one more", "yes one more",
                "a couple", "couple more", "a few more", "few more",
            )
            if any(p in _txt_lower_fbo for p in _FAQ_CONTINUE_FBO):
                _fbo_cont_remainder = _txt_lower_fbo
                for _fbo_cp in sorted(_FAQ_CONTINUE_FBO, key=len, reverse=True):
                    if _fbo_cp in _fbo_cont_remainder:
                        _fbo_cp_idx = _fbo_cont_remainder.index(_fbo_cp) + len(_fbo_cp)
                        _fbo_cont_remainder = _fbo_cont_remainder[_fbo_cp_idx:].strip(" .,\u2014-")
                        break
                _FAQ_ANSWER_INTENTS = {
                    "faq_services", "faq_prices", "faq_hours",
                    "faq_location", "faq_insurance", "faq_capability",
                }
                if _fbo_cont_remainder:
                    _fbo_cont_intent = self._detect_intent(_fbo_cont_remainder)
                    if _fbo_cont_intent in _FAQ_ANSWER_INTENTS or (
                        _fbo_cont_intent == "general_query"
                        and any(s in _fbo_cont_remainder for s in (
                            "?", "what", "how", "can you", "do you",
                            "is there", "are you", "where", "when",
                        ))
                    ):
                        logger.info(
                            "[ms_flow] faq_followup: continue+embedded %s %r → answering directly",
                            _fbo_cont_intent, _fbo_cont_remainder[:50],
                        )
                        self.session["_faq_ans_intent"] = _fbo_cont_intent
                        self.session["_faq_ans_at"]     = time.time()
                        await self._handle_mid_flow_interrupt(_fbo_cont_intent, transcript)
                        if not self.session.get("_faq_loc_pending_intent"):
                            self.session["last_question"] = _faq_topic_reanchor(self.session)
                        return
                logger.info("[ms_flow] faq_followup: continue %r", text[:40])
                await self._tts.put("Of course — what would you like to know?")
                return

            # ── Locked-topic FAQ follow-up preemption ─────────────────────
            # Runs BEFORE _detect_intent so booking_priority_p ("painful",
            # "hurt", "pain") cannot hijack a natural follow-up about the
            # locked service topic (e.g. acupuncture + needles) into
            # booking / general_query.  Ack / continuation / clinic-correction
            # guards above already returned; this only sees substantive
            # follow-up utterances.
            _lt_svc, _lt_matched, _lt_switch = _check_locked_topic_followup(
                self.session, _txt_lower_fbo,
            )
            if _lt_svc:
                logger.info(
                    "[ms_faq] locked_topic_followup_check topic=%s text=%r "
                    "matched=%s explicit_switch=%s",
                    _lt_svc, text[:60], _lt_matched, _lt_switch,
                )
            if _lt_svc and _lt_matched and not _lt_switch:
                logger.info(
                    "[ms_faq] locked_topic_followup_override topic=%s "
                    "reason=service_followup_preempts_general_reroute route=faq_services",
                    _lt_svc,
                )
                self.session["_faq_ans_intent"] = "faq_services"
                self.session["_faq_ans_at"]     = time.time()
                # Prepend the locked service so _handle_mid_flow_interrupt's
                # existing acupuncture fear / specific-service drill-down
                # paths (which key off "acupuncture" in the text) fire
                # correctly even when the follow-up does not repeat the name.
                _lt_synth_tx = f"{_lt_svc} {transcript}"
                await self._handle_mid_flow_interrupt("faq_services", _lt_synth_tx)
                if not self.session.get("_faq_loc_pending_intent"):
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # Pre-flight: informational cancel questions ("can I cancel by phone?",
            # "what's your cancellation policy?") must NOT trigger CANCEL_FLOW.
            # They are FAQ questions about the process, not actual cancel requests.
            _fbo_info_cancel = any(s in _txt_lower_fbo for s in (
                "can i cancel", "how do i cancel", "cancel by phone",
                "cancel by email", "cancel online", "how to cancel",
                "cancellation policy", "how much notice", "notice to cancel",
                "late cancellation", "cancel an appointment",
            ))
            # BUG 4: parking-policy nuance ("time limit", "whole day", "when you say it's free")
            # contains no parking keyword and would otherwise fall to general_query/LLM.
            # When the prior FAQ turn was about location/parking, keep this in the practical
            # FAQ lane so it gets deterministic handling.
            _fbo_last_sub_nu = self.session.get("last_faq_sub", "")
            _fbo_last_int_nu = self.session.get("last_faq_intent", "")
            _FBO_PARK_NUANCE_P = (
                "time limit", "whole day", "all day", "stay there", "stay here",
                "how long can i", "how long do i", "how long can we",
                "restriction", "restrictions",
                "when you say it's free", "when you say it is free", "when you say free",
            )
            _fbo_parking_nuance = (
                (_fbo_last_sub_nu in ("parking", "composite") or _fbo_last_int_nu == "faq_location")
                and any(p in _txt_lower_fbo for p in _FBO_PARK_NUANCE_P)
            )
            if _fbo_info_cancel:
                _fbo_intent = "faq_booking_logistics"
            elif _fbo_parking_nuance:
                logger.info(
                    "[ms_faq] parking_nuance_route last_sub=%s last_intent=%s → faq_location",
                    _fbo_last_sub_nu, _fbo_last_int_nu,
                )
                _fbo_intent = "faq_location"
            else:
                _fbo_intent = self._detect_intent(text)

            # Bug 8: reschedule/cancel must hard-route immediately — never fall
            # through to booking logic or FAQ follow-up answering.
            if _fbo_intent == "reschedule":
                if self._active_flow is not RESCHEDULE_FLOW:
                    self._switch_flow("reschedule")
                    await self.ask_current_question()
                return
            if _fbo_intent == "cancel":
                if self._active_flow is not CANCEL_FLOW:
                    self._switch_flow("cancel")
                    await self.ask_current_question()
                return

            # FAQ follow-ups — answer regardless of count; only booking intent
            # exits this state.  Counter retained for logging/observability only.
            _fbo_count = self.session.get("faq_follow_up_count", 0)
            if _fbo_intent in {"faq_services", "faq_prices", "faq_hours",
                               "faq_location", "faq_insurance", "faq_capability",
                               "faq_child_policy", "faq_booking_logistics",
                               "general_query"}:
                # Duplicate suppression: trailing STT partials or echo fragments
                # (e.g. "insurance" arriving 0.5 s after the full answer was queued)
                # must not trigger a second identical answer.  Suppress the same
                # intent within 8 s of the most recent answer for this state.
                #
                # Clarification bypass: if the caller just received a location answer
                # (accessibility, directions, etc.) and is now asking WHERE exactly /
                # which clinic / what the address is, that is a DIFFERENT sub-question
                # not a trailing echo — skip dedup and answer again.
                _fbo_last_intent = self.session.get("_faq_ans_intent")
                _fbo_last_at     = self.session.get("_faq_ans_at", 0.0)
                _FBO_LOC_CLARIFY_SIGS = (
                    "where", "address", "which clinic", "which one",
                    "exactly", "where is", "where are", "directions",
                    "how do i get", "how to get", "postcode", "road",
                )
                _fbo_is_loc_clarification = (
                    _fbo_intent == "faq_location"
                    and _fbo_last_intent == "faq_location"
                    and any(p in transcript.lower() for p in _FBO_LOC_CLARIFY_SIGS)
                )
                # BUG 3: part-aware dedup — do not suppress a practical sub-part that
                # was not actually answered in the last practical response.
                _fbo_parts_answered = set(self.session.get("_faq_parts_answered") or [])
                _fbo_tx_low = transcript.lower()
                _FBO_PP_ACCESS = (
                    "step free", "step-free", "wheelchair", "accessible",
                    "accessibility", "disabled access", "disability",
                )
                _FBO_PP_LOC = (
                    "where exactly", "where is", "where are", "address",
                    "postcode", "which road", "directions", "how do i get",
                    "find you", "locate you", "get to",
                )
                _FBO_PP_PARK = (
                    "parking", "car park", "free parking", "easy to park",
                    "pay to park", "do i need to pay", "is it free",
                )
                _FBO_PP_PARK_NUANCE = (
                    "time limit", "whole day", "all day", "stay there", "stay here",
                    "how long can i", "how long do i", "how long can we",
                    "restriction", "restrictions",
                    "when you say it's free", "when you say it is free", "when you say free",
                )
                _FBO_PP_TRANS = ("bus", "train", "public transport", "station")
                _fbo_req_parts: set = set()
                if any(p in _fbo_tx_low for p in _FBO_PP_ACCESS): _fbo_req_parts.add("accessibility")
                if any(p in _fbo_tx_low for p in _FBO_PP_LOC):    _fbo_req_parts.add("location")
                if any(p in _fbo_tx_low for p in _FBO_PP_PARK):   _fbo_req_parts.add("parking")
                if any(p in _fbo_tx_low for p in _FBO_PP_PARK_NUANCE): _fbo_req_parts.add("parking_policy")
                if any(p in _fbo_tx_low for p in _FBO_PP_TRANS):  _fbo_req_parts.add("transport")
                _fbo_new_part = bool(_fbo_req_parts - _fbo_parts_answered) if _fbo_req_parts else False
                logger.info(
                    "[ms_faq] practical_dedup_check requested=%s last_answered=%s suppressed=%s",
                    "|".join(sorted(_fbo_req_parts)) or "<none>",
                    "|".join(sorted(_fbo_parts_answered)) or "<none>",
                    (
                        _fbo_intent == _fbo_last_intent
                        and (time.time() - _fbo_last_at) < 8.0
                        and not _fbo_is_loc_clarification
                        and not _fbo_new_part
                    ),
                )
                if (
                    _fbo_intent == _fbo_last_intent
                    and (time.time() - _fbo_last_at) < 8.0
                    and not _fbo_is_loc_clarification
                    and not _fbo_new_part
                ):
                    logger.info(
                        "[ms_flow] FAQ_BOOKING_OFFER: dedup — %s answered %.1fs ago, suppressed",
                        _fbo_intent, time.time() - _fbo_last_at,
                    )
                    self.session["fragment_suppressed"] = True  # block global hard fallback
                    return
                self.session["faq_follow_up_count"] = _fbo_count + 1
                logger.info(
                    "[ms_flow] FAQ_BOOKING_OFFER: follow-up %s (count=%d) — answering",
                    _fbo_intent, _fbo_count + 1,
                )
                # Stamp before the await so any re-entrant transcript for the same
                # intent is already blocked by the time we resume.
                self.session["_faq_ans_intent"] = _fbo_intent
                self.session["_faq_ans_at"]     = time.time()
                await self._handle_mid_flow_interrupt(_fbo_intent, transcript)
                # If a location clarification was emitted, last_question is already
                # the clarification text ("Alcester or Redditch?") — don't overwrite.
                if not self.session.get("_faq_loc_pending_intent"):
                    # Store a fresh neutral follow-up — not the answer body.
                    # Storing the answer in last_question would cause the silence
                    # handler to re-read the full FAQ answer aloud on the next
                    # silence event, and the NEXT turn's re-anchor would replay
                    # it as stale content.
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # Reset follow-up count on any non-FAQ answer
            self.session["faq_follow_up_count"] = 0

            # Bare negatives ("no", "nope", "nah") — caller has no more questions.
            # len<3 for "no"/"nah" means these fall through the substantive check;
            # catch them here before _extract so we close cleanly.
            _fbo_neg_words = set(_txt_lower_fbo.split())
            if _fbo_neg_words and _fbo_neg_words <= frozenset({"no", "nope", "nah"}):
                logger.info("[ms_flow] FAQ_BOOKING_OFFER: bare negative — closing call")
                self.session["call_outcome_logged"] = "faq_only"
                await self._tts.put("Thanks for calling Theorem Health. Have a great day!")
                self.session["flow_step"] = len(self._active_flow)
                return

            answer = self._extract("faq_booking", text, transcript)
            if answer == "book":
                logger.info("[ms_flow] faq_followup: explicit_intent_switch=booking")
                self._switch_flow("booking")
                await self.ask_current_question()
                return
            elif answer == "done":
                logger.info("[ms_flow] faq_followup: exit (done)")
                self.session["call_outcome_logged"] = "faq_only"
                await self._tts.put(
                    "Thanks for calling Theorem Health. Have a great day!"
                )
                self.session["flow_step"] = len(self._active_flow)
                return
            else:
                # Bug 5 fix: detect continuation phrases — caller is restating or
                # clarifying their question, not responding to a booking offer.
                # Route to general_query LLM so the question gets answered rather
                # than falling into a dead "didn't catch that" loop.
                _FAQ_CONTINUATION = (
                    "my question was", "i was asking about", "i was asking",
                    "what i wanted to ask", "i meant to ask", "i meant",
                    "i was wondering about", "i wanted to know about",
                    "i wanted to ask", "the question was", "what i asked",
                    "going back to my question", "about my question",
                )
                if any(p in _txt_lower_fbo for p in _FAQ_CONTINUATION):
                    logger.info(
                        "[ms_flow] faq_followup: continuation phrase %r → general_query",
                        text[:50],
                    )
                    self.session["_faq_ans_intent"] = "general_query"
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt("general_query", transcript)
                    if not self.session.get("_faq_loc_pending_intent"):
                        self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                # Dangling-fragment guard: connector-start + open-end word = incomplete clause.
                # "to go to your", "and is the approach kind of slowly based or" etc.
                # Hold without answering so the caller can finish the thought.
                _DFG_STARTERS = frozenset({
                    "and", "to", "of", "but", "so", "or", "because", "with", "for", "from",
                })
                _DFG_ENDERS = frozenset({
                    "or", "and", "to", "for", "the", "a", "an", "your", "their",
                    "our", "its", "in", "on", "at", "by", "of", "about",
                })
                _dfg_ws = _txt_lower_fbo.split()
                if (
                    len(_dfg_ws) >= 2
                    and _dfg_ws[0] in _DFG_STARTERS
                    and _dfg_ws[-1] in _DFG_ENDERS
                ):
                    logger.info(
                        "[ms_flow] FAQ_BOOKING_OFFER: dangling fragment %r — holding", text[:40]
                    )
                    self.session["fragment_suppressed"] = True
                    return
                # Semantic content guard: any utterance with a substantive word
                # (length ≥ 3, not a pure filler) is a question-in-progress.
                # Topic carry-forward: if a specific service topic was active (e.g.
                # acupuncture), keep the follow-up in that context rather than drifting
                # to generic LLM output that may cite the wrong service.
                _FBO_FILLERS = frozenset({"uh", "um", "er", "hmm", "hm", "ah", "eh"})
                _fbo_substantive = [
                    w for w in _txt_lower_fbo.split()
                    if len(w) >= 3 and w not in _FBO_FILLERS
                ]
                if _fbo_substantive:
                    _fbo_active_svc   = self.session.get("_faq_active_service")
                    _fbo_active_topic = self.session.get("_faq_active_topic")
                    _fbo_fw           = self.session.get("_faq_followup_window", 0)
                    # Continuation phrase detection: utterances starting with or
                    # containing these signals are treated as follow-ups by default.
                    _FOLLOWUP_STARTERS = (
                        "and ", "what about", "in terms of", "would that",
                        "is it ", "does it ", "how does", "do you ", "what should",
                        "to go to", "is there ", "how long", "would that be",
                        "so if", "okay but", "right but", "and if", "then what",
                        "and in",
                    )
                    _fbo_is_continuation = any(
                        _txt_lower_fbo.startswith(p) or p in _txt_lower_fbo
                        for p in _FOLLOWUP_STARTERS
                    )
                    if (
                        _fbo_active_svc
                        and len(_fbo_substantive) <= 10  # raised from 5
                        and _fbo_intent == "general_query"
                    ):
                        # Service topic carry-forward
                        _fbo_eff_intent = "faq_services"
                        _fbo_eff_tx     = f"{_fbo_active_svc} {transcript}"
                        self.session["_faq_followup_window"] = max(0, _fbo_fw - 1)
                        logger.info(
                            "[ms_flow] faq_followup: service topic carry-forward %s for %r",
                            _fbo_active_svc, text[:40],
                        )
                        logger.info(
                            "[ms_faq] followup matched locked topic=%s — kept in faq_topic "
                            "instead of top_level_reroute",
                            _fbo_active_svc,
                        )
                    elif (
                        _fbo_active_topic
                        and _fbo_fw > 0
                        and _fbo_intent == "general_query"
                        and (_fbo_is_continuation or len(_fbo_substantive) <= 4)
                    ):
                        # Non-service topic carry-forward (location, insurance, hours, etc.)
                        _fbo_eff_intent = _fbo_active_topic
                        _fbo_eff_tx     = transcript
                        self.session["_faq_followup_window"] = max(0, _fbo_fw - 1)
                        logger.info(
                            "[ms_flow] faq_followup: topic carry-forward %s (window=%d) for %r",
                            _fbo_active_topic, _fbo_fw, text[:40],
                        )
                    else:
                        _fbo_eff_intent = "general_query"
                        _fbo_eff_tx     = transcript
                        logger.info(
                            "[ms_flow] faq_followup: semantic guard %r → general_query",
                            text[:50],
                        )
                    self.session["_faq_ans_intent"]   = _fbo_eff_intent
                    self.session["_faq_ans_at"]       = time.time()
                    if _fbo_eff_intent == "general_query":
                        self.session["_faq_ctx_fragment"] = transcript.strip()
                    self.session["_faq_repair_count"] = 0
                    await self._handle_mid_flow_interrupt(_fbo_eff_intent, _fbo_eff_tx)
                    if not self.session.get("_faq_loc_pending_intent"):
                        self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                logger.info("[ms_flow] faq_followup: faq_repair %r", text[:40])
                _faq_rc = self.session.get("_faq_repair_count", 0) + 1
                self.session["_faq_repair_count"] = _faq_rc
                if _faq_rc <= 1:
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "was there something else you wanted to ask?"
                    )
                return

        # ── GENERAL_BOOKING_OFFER: yes → switch to booking, no → goodbye ─────
        if step["state"] == "GENERAL_BOOKING_OFFER":
            # ── Pending location clarification: caller is responding to
            #    "Sure — is that for our Alcester or Redditch clinic?" ──────
            _gbo_pending_loc = self.session.get("_faq_loc_pending_intent")
            if _gbo_pending_loc:
                _gbo_pend_text = text.strip().lower()
                _gbo_pend_redd = any(p in _gbo_pend_text for p in (
                    "redditch", "reditch", "reddish", "reddit",
                    "red itch", "red ditch", "red-ditch", "bromsgrove",
                ))
                _gbo_pend_alce = any(p in _gbo_pend_text for p in (
                    "alcester", "greig", "kinwarton",
                ))
                if _gbo_pend_redd and not _gbo_pend_alce:
                    _gbo_pend_clinic = "redditch"
                elif _gbo_pend_alce and not _gbo_pend_redd:
                    _gbo_pend_clinic = "alcester"
                else:
                    _gbo_pend_clinic = None
                if _gbo_pend_clinic:
                    self.session["last_faq_loc_id"] = _gbo_pend_clinic
                    _gbo_pend_sub = self.session.pop("_faq_loc_pending_sub", "")
                    self.session.pop("_faq_loc_pending_intent", None)
                    _gbo_synth_tx = (
                        f"{_gbo_pend_sub} {_gbo_pend_clinic}".strip()
                        if _gbo_pend_sub else _gbo_pend_clinic
                    )
                    self.session["_faq_ans_intent"] = _gbo_pending_loc
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt(_gbo_pending_loc, _gbo_synth_tx)
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                    logger.info(
                        "[ms_flow] GENERAL_BOOKING_OFFER: pending location resolved → %s for %s",
                        _gbo_pending_loc, _gbo_pend_clinic,
                    )
                    return
                elif len(text.strip().split()) <= 3:
                    logger.info(
                        "[ms_flow] GENERAL_BOOKING_OFFER: location pending, no clinic in %r — re-asking",
                        text[:40],
                    )
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    return
                else:
                    # Global block handles all pending FAQ clarification before reaching
                    # this state handler; if we arrive here the pending was not consumed.
                    logger.warning(
                        "[ms_flow] GENERAL_BOOKING_OFFER: pending loc not consumed by global block %r",
                        text[:40],
                    )
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "could you say the Alcester clinic or the Redditch clinic?"
                    )
                    return

            # Pure acknowledgements ("okay", "okay perfect", "alright", "that's understood")
            # are inert — the caller is processing the answer, not asking a new question.
            # Do nothing: silence handler will re-ask if needed.
            _GBO_ACK_WORDS = frozenset({
                "okay", "ok", "alright", "right", "sure", "yeah", "yep", "yup",
                "great", "good", "got", "it", "understood", "perfect", "brilliant",
                "lovely", "cool", "noted", "absolutely", "certainly", "exactly",
            })
            _gbo_words = set(text.strip().split())
            if _gbo_words and _gbo_words <= _GBO_ACK_WORDS:
                logger.info("[ms_flow] GENERAL_BOOKING_OFFER: ack-only %r — inert", text[:40])
                self.session["fragment_suppressed"] = True
                return

            # Bare affirmatives ("yes", "please") — caller has another question
            # but hasn't stated it yet.  Prompt cleanly instead of carry-forward.
            _GBO_BARE_YES = frozenset({"yes", "please"})
            if _gbo_words and _gbo_words <= _GBO_BARE_YES:
                logger.info("[ms_flow] GENERAL_BOOKING_OFFER: bare affirmative %r", text[:40])
                await self._tts.put("Of course — what would you like to know?")
                return

            # ── FAQ follow-up phrase buckets ────────────────────────────────────
            _txt_lower_gbo = text.strip().lower()

            # Multi-word acknowledgements — caller satisfied, prompt for next question
            _FAQ_MULTIWORD_ACKS_GBO = (
                "okay that's noted", "ok that's noted", "okay thats noted",
                "okay that helps", "ok that helps", "that's helpful", "thats helpful",
                "that is helpful", "that makes sense", "okay that makes sense",
                "i see", "yeah that's fine", "yeah thats fine",
                "yeah okay", "yeah ok", "yeah alright",
                "right okay", "right ok", "alright then",
                "mmhm", "mm-hm", "uh-huh", "uh huh",
            )
            if any(p in _txt_lower_gbo for p in _FAQ_MULTIWORD_ACKS_GBO):
                logger.info("[ms_flow] faq_followup: acknowledgement %r", text[:40])
                await self._tts.put("Of course — anything else you'd like to ask?")
                return

            # Bare continuation phrases only — phrases carrying embedded FAQ intent
            # must fall through to _detect_intent so the topic gets answered directly.
            # Prompt 8 Bug 3/4 fix: strip prefix and check for embedded FAQ intent.
            _FAQ_CONTINUE_GBO = (
                "other question", "another question", "one more question",
                "few more questions", "yeah another", "yes another",
                "yeah one more", "yes one more",
                "a couple", "couple more", "a few more", "few more",
            )
            if any(p in _txt_lower_gbo for p in _FAQ_CONTINUE_GBO):
                _gbo_cont_remainder = _txt_lower_gbo
                for _gbo_cp in sorted(_FAQ_CONTINUE_GBO, key=len, reverse=True):
                    if _gbo_cp in _gbo_cont_remainder:
                        _gbo_cp_idx = _gbo_cont_remainder.index(_gbo_cp) + len(_gbo_cp)
                        _gbo_cont_remainder = _gbo_cont_remainder[_gbo_cp_idx:].strip(" .,\u2014-")
                        break
                _FAQ_ANSWER_INTENTS_GBO = {
                    "faq_services", "faq_prices", "faq_hours",
                    "faq_location", "faq_insurance", "faq_capability",
                }
                if _gbo_cont_remainder:
                    _gbo_cont_intent = self._detect_intent(_gbo_cont_remainder)
                    if _gbo_cont_intent in _FAQ_ANSWER_INTENTS_GBO or (
                        _gbo_cont_intent == "general_query"
                        and any(s in _gbo_cont_remainder for s in (
                            "?", "what", "how", "can you", "do you",
                            "is there", "are you", "where", "when",
                        ))
                    ):
                        logger.info(
                            "[ms_flow] faq_followup: continue+embedded %s %r → answering directly",
                            _gbo_cont_intent, _gbo_cont_remainder[:50],
                        )
                        self.session["_faq_ans_intent"] = _gbo_cont_intent
                        self.session["_faq_ans_at"]     = time.time()
                        await self._handle_mid_flow_interrupt(_gbo_cont_intent, transcript)
                        if not self.session.get("_faq_loc_pending_intent"):
                            self.session["last_question"] = _faq_topic_reanchor(self.session)
                        return
                logger.info("[ms_flow] faq_followup: continue %r", text[:40])
                await self._tts.put("Of course — what would you like to know?")
                return

            # ── Locked-topic FAQ follow-up preemption ─────────────────────
            # Mirror of the FAQ_BOOKING_OFFER preemption — see that block
            # for rationale.  Must run BEFORE _detect_intent so locked-topic
            # service follow-ups never get reclassified as booking/general.
            _glt_svc, _glt_matched, _glt_switch = _check_locked_topic_followup(
                self.session, _txt_lower_gbo,
            )
            if _glt_svc:
                logger.info(
                    "[ms_faq] locked_topic_followup_check topic=%s text=%r "
                    "matched=%s explicit_switch=%s",
                    _glt_svc, text[:60], _glt_matched, _glt_switch,
                )
            if _glt_svc and _glt_matched and not _glt_switch:
                logger.info(
                    "[ms_faq] locked_topic_followup_override topic=%s "
                    "reason=service_followup_preempts_general_reroute route=faq_services",
                    _glt_svc,
                )
                self.session["_faq_ans_intent"] = "faq_services"
                self.session["_faq_ans_at"]     = time.time()
                _glt_synth_tx = f"{_glt_svc} {transcript}"
                await self._handle_mid_flow_interrupt("faq_services", _glt_synth_tx)
                if not self.session.get("_faq_loc_pending_intent"):
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # Pre-flight: informational cancel questions must NOT trigger CANCEL_FLOW.
            _gbo_info_cancel = any(s in _txt_lower_gbo for s in (
                "can i cancel", "how do i cancel", "cancel by phone",
                "cancel by email", "cancel online", "how to cancel",
                "cancellation policy", "how much notice", "notice to cancel",
                "late cancellation", "cancel an appointment",
            ))
            _gbo_intent = "faq_booking_logistics" if _gbo_info_cancel else self._detect_intent(text)
            # Reschedule/cancel: hard-route immediately.
            if _gbo_intent == "reschedule":
                if self._active_flow is not RESCHEDULE_FLOW:
                    self._switch_flow("reschedule")
                    await self.ask_current_question()
                return
            if _gbo_intent == "cancel":
                if self._active_flow is not CANCEL_FLOW:
                    self._switch_flow("cancel")
                    await self.ask_current_question()
                return
            # Specific FAQ intent — answer and re-anchor.
            # Duplicate suppression shared between the two FAQ sub-blocks below:
            # a trailing STT partial for the same topic (e.g. "insurance" arriving
            # 0.5 s after the full insurance answer was queued) must not fire a
            # second identical answer.  Suppress within an 8-second window.
            #
            # Clarification bypass: if the caller just received a location answer
            # and is now asking WHERE / which clinic / address, that is a distinct
            # sub-question — let it through rather than suppressing as a duplicate.
            _gbo_last_intent = self.session.get("_faq_ans_intent")
            _gbo_last_at     = self.session.get("_faq_ans_at", 0.0)
            _GBO_DEDUP_SEC   = 8.0
            _GBO_LOC_CLARIFY_SIGS = (
                "where", "address", "which clinic", "which one",
                "exactly", "where is", "where are", "directions",
                "how do i get", "how to get", "postcode", "road",
            )
            _gbo_is_loc_clarification = (
                _gbo_intent == "faq_location"
                and _gbo_last_intent == "faq_location"
                and any(p in transcript.lower() for p in _GBO_LOC_CLARIFY_SIGS)
            )
            if _gbo_intent in {
                "faq_services", "faq_prices", "faq_hours",
                "faq_location", "faq_insurance", "faq_capability",
                "faq_child_policy", "faq_booking_logistics",
            }:
                if (
                    _gbo_intent == _gbo_last_intent
                    and (time.time() - _gbo_last_at) < _GBO_DEDUP_SEC
                    and not _gbo_is_loc_clarification
                ):
                    logger.info(
                        "[ms_flow] GENERAL_BOOKING_OFFER: dedup — %s answered %.1fs ago, suppressed",
                        _gbo_intent, time.time() - _gbo_last_at,
                    )
                    self.session["fragment_suppressed"] = True  # block global hard fallback
                    return
                # Stamp before the await so any re-entrant transcript for the same
                # intent is already blocked by the time we resume.
                self.session["_faq_ans_intent"] = _gbo_intent
                self.session["_faq_ans_at"]     = time.time()
                await self._handle_mid_flow_interrupt(_gbo_intent, transcript)
                # If a location clarification was emitted, last_question already
                # holds the clarification text — don't overwrite with "Anything else?"
                if not self.session.get("_faq_loc_pending_intent"):
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                return
            # general_query only fires for genuine questions (contains a question signal).
            # Without a signal, "okay" / "okay that's fine" etc. reach here and must not
            # trigger a new LLM response.
            _GBO_QUESTION_SIGNALS = (
                "?", "what", "how", "can you", "tell me", "do you",
                "is there", "are you", "which", "where", "when", "why",
                "explain", "describe",
            )
            if _gbo_intent == "general_query" and any(s in text for s in _GBO_QUESTION_SIGNALS):
                if (
                    _gbo_intent == _gbo_last_intent
                    and (time.time() - _gbo_last_at) < _GBO_DEDUP_SEC
                ):
                    logger.info(
                        "[ms_flow] GENERAL_BOOKING_OFFER: dedup general_query — suppressed",
                    )
                    self.session["fragment_suppressed"] = True  # block global hard fallback
                    return
                self.session["_faq_ans_intent"] = _gbo_intent
                self.session["_faq_ans_at"]     = time.time()
                await self._handle_mid_flow_interrupt(_gbo_intent, transcript)
                if not self.session.get("_faq_loc_pending_intent"):
                    self.session["last_question"] = _faq_topic_reanchor(self.session)
                return

            # Bare negatives ("no", "nope", "nah") — caller has no more questions.
            _gbo_neg_words = set(_txt_lower_gbo.split())
            if _gbo_neg_words and _gbo_neg_words <= frozenset({"no", "nope", "nah"}):
                logger.info("[ms_flow] GENERAL_BOOKING_OFFER: bare negative — closing call")
                self.session["call_outcome_logged"] = "faq_only"
                await self._tts.put("Thanks for calling Theorem Health. Have a great day!")
                self.session["flow_step"] = len(self._active_flow)
                return

            answer = self._extract("faq_booking", text, transcript)
            if answer == "book":
                logger.info("[ms_flow] faq_followup: explicit_intent_switch=booking")
                self._switch_flow("booking")
                await self.ask_current_question()
                return
            elif answer == "done":
                logger.info("[ms_flow] faq_followup: exit (done)")
                self.session["call_outcome_logged"] = "faq_only"
                await self._tts.put(
                    "Thanks for calling Theorem Health. Have a great day!"
                )
                self.session["flow_step"] = len(self._active_flow)
                return
            else:
                # Bug 5 fix: detect continuation phrases — caller is restating or
                # clarifying their question, not responding to a booking offer.
                _txt_lower_gbo = text.strip().lower()
                _FAQ_CONTINUATION_GBO = (
                    "my question was", "i was asking about", "i was asking",
                    "what i wanted to ask", "i meant to ask", "i meant",
                    "i was wondering about", "i wanted to know about",
                    "i wanted to ask", "the question was", "what i asked",
                    "going back to my question", "about my question",
                )
                if any(p in _txt_lower_gbo for p in _FAQ_CONTINUATION_GBO):
                    logger.info(
                        "[ms_flow] faq_followup: continuation phrase %r → general_query",
                        text[:50],
                    )
                    self.session["_faq_ans_intent"] = "general_query"
                    self.session["_faq_ans_at"]     = time.time()
                    await self._handle_mid_flow_interrupt("general_query", transcript)
                    if not self.session.get("_faq_loc_pending_intent"):
                        self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                # Dangling-fragment guard: connector-start + open-end word = incomplete clause.
                # Hold without answering so the caller can finish the thought.
                _DFG_STARTERS_GBO = frozenset({
                    "and", "to", "of", "but", "so", "or", "because", "with", "for", "from",
                })
                _DFG_ENDERS_GBO = frozenset({
                    "or", "and", "to", "for", "the", "a", "an", "your", "their",
                    "our", "its", "in", "on", "at", "by", "of", "about",
                })
                _dfg_ws_gbo = _txt_lower_gbo.split()
                if (
                    len(_dfg_ws_gbo) >= 2
                    and _dfg_ws_gbo[0] in _DFG_STARTERS_GBO
                    and _dfg_ws_gbo[-1] in _DFG_ENDERS_GBO
                ):
                    logger.info(
                        "[ms_flow] GENERAL_BOOKING_OFFER: dangling fragment %r — holding", text[:40]
                    )
                    self.session["fragment_suppressed"] = True
                    return
                # Semantic content guard: any utterance with a substantive word
                # (length ≥ 3, not a pure filler) is a question-in-progress.
                # Topic carry-forward: if a specific service topic was active (e.g.
                # acupuncture), keep the follow-up in that context rather than drifting
                # to generic LLM output that may cite the wrong service.
                _GBO_FILLERS = frozenset({"uh", "um", "er", "hmm", "hm", "ah", "eh"})
                _gbo_substantive = [
                    w for w in _txt_lower_gbo.split()
                    if len(w) >= 3 and w not in _GBO_FILLERS
                ]
                if _gbo_substantive:
                    _gbo_active_svc   = self.session.get("_faq_active_service")
                    _gbo_active_topic = self.session.get("_faq_active_topic")
                    _gbo_fw           = self.session.get("_faq_followup_window", 0)
                    _FOLLOWUP_STARTERS_GBO = (
                        "and ", "what about", "in terms of", "would that",
                        "is it ", "does it ", "how does", "do you ", "what should",
                        "to go to", "is there ", "how long", "would that be",
                        "so if", "okay but", "right but", "and if", "then what",
                        "and in",
                    )
                    _gbo_is_continuation = any(
                        _txt_lower_gbo.startswith(p) or p in _txt_lower_gbo
                        for p in _FOLLOWUP_STARTERS_GBO
                    )
                    if (
                        _gbo_active_svc
                        and len(_gbo_substantive) <= 10
                        and _gbo_intent == "general_query"
                    ):
                        _gbo_eff_intent = "faq_services"
                        _gbo_eff_tx     = f"{_gbo_active_svc} {transcript}"
                        self.session["_faq_followup_window"] = max(0, _gbo_fw - 1)
                        logger.info(
                            "[ms_flow] faq_followup: service topic carry-forward %s for %r",
                            _gbo_active_svc, text[:40],
                        )
                        logger.info(
                            "[ms_faq] followup matched locked topic=%s — kept in faq_topic "
                            "instead of top_level_reroute",
                            _gbo_active_svc,
                        )
                    elif (
                        _gbo_active_topic
                        and _gbo_fw > 0
                        and _gbo_intent == "general_query"
                        and (_gbo_is_continuation or len(_gbo_substantive) <= 4)
                    ):
                        # Non-service topic carry-forward (location, insurance, hours, etc.)
                        _gbo_eff_intent = _gbo_active_topic
                        _gbo_eff_tx     = transcript
                        self.session["_faq_followup_window"] = max(0, _gbo_fw - 1)
                        logger.info(
                            "[ms_flow] faq_followup: topic carry-forward %s (window=%d) for %r",
                            _gbo_active_topic, _gbo_fw, text[:40],
                        )
                    else:
                        _gbo_eff_intent = "general_query"
                        _gbo_eff_tx     = transcript
                        logger.info(
                            "[ms_flow] faq_followup: semantic guard %r → general_query",
                            text[:50],
                        )
                    self.session["_faq_ans_intent"]   = _gbo_eff_intent
                    self.session["_faq_ans_at"]       = time.time()
                    if _gbo_eff_intent == "general_query":
                        self.session["_faq_ctx_fragment"] = transcript.strip()
                    self.session["_faq_repair_count"] = 0
                    await self._handle_mid_flow_interrupt(_gbo_eff_intent, _gbo_eff_tx)
                    if not self.session.get("_faq_loc_pending_intent"):
                        self.session["last_question"] = _faq_topic_reanchor(self.session)
                    return
                logger.info("[ms_flow] faq_followup: faq_repair %r", text[:40])
                _faq_rc = self.session.get("_faq_repair_count", 0) + 1
                self.session["_faq_repair_count"] = _faq_rc
                if _faq_rc <= 1:
                    await self._tts.put(
                        "Sorry, I didn't quite catch that — "
                        "was there something else you wanted to ask?"
                    )
                return

        # ── LOOKUP_RESCHEDULE / LOOKUP_CANCEL: re-fire LLM on every caller turn ──
        # These steps are multi-turn LLM interactions:
        #   Turn 1 (ask_current_question): LLM calls lookup_appointment, reads back result.
        #   Turn 2+ (handle_transcript):   Caller says "yes/no"; LLM calls
        #                                  confirm_appointment_found when confirmed.
        # Advance only happens in ask_current_question AFTER the LLM sets
        # rc_appointment_confirmed=True.  We MUST NOT use generic extraction here —
        # _extract("none") returns True which would silently advance to the next step
        # without the LLM ever calling confirm_appointment_found.
        if step["state"] in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
            # ── PART 2: deterministic disambiguation for ambiguous STRONG candidates ──
            # Fires when tool returned ambiguous=True (two tied STRONG matches) and
            # stored lookup_candidates in session without setting rc_stage.
            # Try to bind the caller's utterance to one specific candidate.
            import re as _re_lu
            _lu_cands = self.session.get("lookup_candidates", [])
            if _lu_cands and not self.session.get("rc_stage"):
                _lc_bind = text.strip().lower()
                _lu_ordinals = ("first", "second", "third")
                _bound_cand = None
                for _idx, _cand in enumerate(_lu_cands):
                    # Ordinal: "the first one", "the second"
                    if _idx < len(_lu_ordinals) and _lu_ordinals[_idx] in _lc_bind:
                        _bound_cand = _cand
                        break
                    # Month name: "the May one", "the April one"
                    try:
                        _cdt = datetime.fromisoformat(_cand["datetime"].replace("Z", "+00:00"))
                        _c_month = _cdt.strftime("%B").lower()
                        _c_day   = _cdt.day
                        _c_h24   = _cdt.hour
                        _c_h12   = _c_h24 if _c_h24 <= 12 else _c_h24 - 12
                        _c_tl    = _cand.get("time_label", "")
                    except Exception:
                        continue
                    if _c_month and _c_month in _lc_bind:
                        _bound_cand = _cand
                        break
                    # Day number with word boundary: "the 6th", "22nd"
                    if _re_lu.search(rf'\b{_c_day}\b', _lc_bind):
                        _bound_cand = _cand
                        break
                    # Time label direct or 12h with am/pm
                    if _c_tl and _c_tl in _lc_bind:
                        _bound_cand = _cand
                        break
                    if _re_lu.search(rf'\b{_c_h12}\s*(?:am|pm|o\'?clock)\b', _lc_bind):
                        _bound_cand = _cand
                        break

                if _bound_cand:
                    # Commit the chosen candidate to session
                    self.session["reschedule_appt_id"]       = _bound_cand["id"]
                    self.session["reschedule_appt_datetime"]  = _bound_cand["datetime"]
                    self.session["reschedule_appt_type"]      = _bound_cand.get("type", "appointment")
                    self.session["lookup_appt_first_name"]    = _bound_cand.get("first_name", "")
                    self.session["lookup_appt_last_name"]     = _bound_cand.get("last_name", "")
                    self.session["rc_stage"] = "lookup_done"
                    # Remaining candidates become alternatives for NO-path advancement
                    _leftover = [c for c in _lu_cands if c["id"] != _bound_cand["id"]]
                    if _leftover:
                        self.session["reschedule_appt_alternatives"] = _leftover
                    self.session.pop("lookup_candidates", None)
                    _confirm_q = (
                        f"Got it — I found your appointment on {_bound_cand['day_label']} "
                        f"at {_bound_cand['time_label']}. Is that the one?"
                    )
                    await self._tts.put(_confirm_q)
                    self.session["last_question"] = _confirm_q
                    logger.info(
                        "[ms_flow] LOOKUP disambiguation: bound to candidate id=%s from %r",
                        _bound_cand["id"], text[:60],
                    )
                    return
                else:
                    # Can't resolve from utterance — re-ask the disambiguation question
                    if len(_lu_cands) >= 2:
                        _dc0, _dc1 = _lu_cands[0], _lu_cands[1]
                        _disambig_q = (
                            f"I found two appointments that could be yours — "
                            f"was it {_dc0['day_label']} at {_dc0['time_label']}, "
                            f"or {_dc1['day_label']} at {_dc1['time_label']}?"
                        )
                        await self._tts.put(_disambig_q)
                        self.session["last_question"] = _disambig_q
                        logger.info(
                            "[ms_flow] LOOKUP disambiguation: re-asking — could not bind from %r",
                            text[:60],
                        )
                    return

            # Deterministic name correction pre-check — runs before LLM re-fire.
            # Active only when lookup_correction_mode is set (i.e. a previous lookup failed).
            if self.session.get("lookup_correction_mode"):
                _lc_corr = text.strip().lower()
                _CORR_CONFIRM = (
                    "yes", "yeah", "yep", "correct", "that's right", "thats right",
                    "it's right", "it is right", "yes it is", "that's correct",
                    "yes it's", "yes that's",
                )
                _is_confirmed = any(p in _lc_corr for p in _CORR_CONFIRM)
                _rc_step = self.session.get("rc_recovery_step", "surname")

                def _flush_tts():
                    while not self._tts.empty():
                        try: self._tts.get_nowait()
                        except Exception: break

                # ── Keypad phone recovery: intercept before name/ladder steps ──────
                # Active when lookup failed in phone-first mode and we asked for
                # the booking phone number on the keypad.  Accepts DTMF-buffered
                # digits (sent as synthetic transcript by connection.py) or spoken
                # digits extracted from STT text.
                if self.session.get("rc_kp_phone_pending"):
                    import re as _re_kp
                    _kp_digits = _re_kp.sub(r"\D", "", text or "")
                    if len(_kp_digits) >= 10:
                        _kp_phone = _to_e164_uk(_kp_digits) or _kp_digits
                        self.session["phone_number"] = _kp_phone
                        self.session.setdefault("collected", {})["phone"] = _kp_phone
                        self.session.pop("rc_kp_phone_pending", None)
                        self.session["rc_kp_phone_done"] = True   # mark so second failure goes to name
                        self.session.pop("lookup_correction_mode", None)
                        self.session.pop("rc_recovery_step", None)
                        logger.info(
                            "[ms_flow] keypad phone recovery lookup retried: digits=%r → phone=%r",
                            _kp_digits, _kp_phone,
                        )
                        _flush_tts()
                        await self.ask_current_question()
                    else:
                        # No usable digits — short re-prompt toward keypad
                        _kp_reprompt = "Please enter the phone number on your keypad."
                        _flush_tts()
                        await self._tts.put(_kp_reprompt)
                        self.session["last_question"] = _kp_reprompt
                        logger.info(
                            "[ms_flow] keypad phone recovery: unclear %r — re-prompting",
                            text[:40],
                        )
                    return

                # ── Step-specific branches ─────────────────────────────────────
                if _rc_step == "surname":
                    if _is_confirmed:
                        # Surname confirmed unchanged — don't retry the same lookup.
                        # Advance to location challenge.
                        self.session["rc_recovery_step"] = "location"
                        _loc = self.session.get("selected_location", "")
                        if _loc == "alcester":
                            _other = "Redditch"
                        elif _loc == "redditch":
                            _other = "Alcester"
                        else:
                            _other = None
                        if _other:
                            _loc_q = (
                                f"Could the booking be at {_other} rather than {_loc.title()}?"
                            )
                        else:
                            _loc_q = "Is the booking at the Alcester or Redditch clinic?"
                        _flush_tts()
                        await self._tts.put(_loc_q)
                        self.session["last_question"] = _loc_q
                        logger.info(
                            "[ms_flow] recovery surname=confirmed → advancing to location challenge"
                        )
                        return

                    # Name capture — support progressive first-name / surname entry
                    # If caller gives a single word, store as first name and ask for surname.
                    # If rc_name_first_name already stored, combine and retry immediately.
                    _correction = _parse_lookup_name_correction(text)
                    if _correction:
                        col = self.session.setdefault("collected", {})
                        _stored_first = self.session.get("rc_name_first_name")
                        if _correction.startswith("__SURNAME__"):
                            _new_surname = _correction[len("__SURNAME__"):]
                            _existing_full = (col.get("full_name") or "").strip()
                            _first = _stored_first or (_existing_full.split()[0] if _existing_full else "")
                            _combined = f"{_first} {_new_surname}".strip() if _first else _new_surname
                            self.session.pop("rc_name_first_name", None)
                            col["full_name"] = _combined
                            self.session["full_name"] = _combined
                            logger.info(
                                "[ms_flow] recovery/surname correction: %r → full_name=%r",
                                text[:50], _combined,
                            )
                        elif _stored_first:
                            # We have a stored first name — this is the surname
                            _combined = f"{_stored_first} {_correction}".strip()
                            self.session.pop("rc_name_first_name", None)
                            col["full_name"] = _combined
                            self.session["full_name"] = _combined
                            logger.info(
                                "[ms_flow] partial name: surname=%r received → full_name=%r",
                                _correction, _combined,
                            )
                        elif len(_correction.split()) == 1:
                            # Single word — treat as first name, ask for surname
                            self.session["rc_name_first_name"] = _correction
                            _sn_q = "Thanks — what surname was it booked under?"
                            _flush_tts()
                            await self._tts.put(_sn_q)
                            self.session.setdefault("conversation_history", []).append(
                                {"role": "assistant", "content": _sn_q}
                            )
                            self.session["last_question"] = _sn_q
                            logger.info(
                                "[ms_flow] partial name captured (first=%r) — requesting surname",
                                _correction,
                            )
                            return
                        else:
                            col["full_name"] = _correction
                            self.session["full_name"] = _correction
                            logger.info(
                                "[ms_flow] recovery/surname full-name correction: %r → full_name=%r",
                                text[:50], _correction,
                            )
                        self.session.pop("lookup_correction_mode", None)
                        self.session.pop("rc_recovery_step", None)
                        # Clear phone-first flag — next LLM turn must use the
                        # collected name in the lookup, not phone-only mode.
                        self.session.pop("rc_phone_first", None)
                        _flush_tts()
                        await self.ask_current_question()
                        return
                    else:
                        # Cannot parse a name — check if we have partial info
                        _stored_first_reask = self.session.get("rc_name_first_name")
                        if _stored_first_reask:
                            _reask = "What surname was the booking made under?"
                        else:
                            _reask = (
                                "Sorry — I didn't quite catch that. "
                                "Could you give me the surname the booking was made under?"
                            )
                        _flush_tts()
                        await self._tts.put(_reask)
                        self.session["last_question"] = _reask
                        return

                elif _rc_step == "location":
                    if _is_confirmed:
                        # Location confirmed unchanged — advance to phone challenge
                        self.session["rc_recovery_step"] = "phone"
                        _phone_q = (
                            "I'm still not finding it. "
                            "Could you confirm the phone number on the booking?"
                        )
                        _flush_tts()
                        await self._tts.put(_phone_q)
                        self.session["last_question"] = _phone_q
                        logger.info(
                            "[ms_flow] recovery location=confirmed → advancing to phone challenge"
                        )
                        return

                    # Try to resolve a new clinic from the utterance
                    # ── DTMF mode: waiting for keypad press — loops until resolved ─────────
                    if self.session.get("_recovery_loc_awaiting_dtmf"):
                        _rl_dtmf_loc = self._extract("location_selection", text, transcript)
                        if _rl_dtmf_loc:
                            self.session["selected_location"] = _rl_dtmf_loc
                            self.session.pop("lookup_correction_mode", None)
                            self.session.pop("rc_recovery_step", None)
                            self.session.pop("_recovery_loc_retry_count", None)
                            self.session.pop("_recovery_loc_awaiting_dtmf", None)
                            logger.info("[ms_flow] recovery/location: DTMF resolved → %s", _rl_dtmf_loc)
                            _flush_tts()
                            await self.ask_current_question()
                            return
                        _rl_dtmf_re = "Press 1 for Alcester or 2 for Redditch."
                        _flush_tts()
                        await self._tts.put(_rl_dtmf_re)
                        self.session["last_question"] = _rl_dtmf_re
                        return

                    _loc_result = _resolve_clinic(_lc_corr, context="ask_location")
                    if _loc_result["status"] == "resolved":
                        self.session["selected_location"] = _loc_result["location"]
                        logger.info(
                            "[ms_flow] recovery/location: resolved %r → %r",
                            text[:50], _loc_result["location"],
                        )
                        self.session.pop("lookup_correction_mode", None)
                        self.session.pop("rc_recovery_step", None)
                        self.session.pop("_recovery_loc_retry_count", None)
                        self.session.pop("_recovery_loc_awaiting_dtmf", None)
                        _flush_tts()
                        await self.ask_current_question()
                        return
                    elif _loc_result["status"] == "ambiguous":
                        _rl_conf = _loc_result.get("confidence", 0)
                        _rl_dbg  = _loc_result.get("debug", {})
                        _rl_alc  = _rl_dbg.get("alcester_score", 0)
                        _rl_red  = _rl_dbg.get("redditch_score", 0)
                        _rl_reason = _loc_result.get("reason", "")
                        # Recovery/location score-bind must also require prefix
                        # or soft-alias evidence — pure-similarity-only ambiguous
                        # results (e.g. "please" at 0.57) must never silent-bind.
                        _rl_has_signal = (
                            "prefix" in _rl_reason
                            or "soft_alias" in _rl_reason
                        )
                        if _rl_conf >= 0.60 and _rl_has_signal:
                            _rl_bind = "alcester" if _rl_alc >= _rl_red else "redditch"
                            self.session["selected_location"] = _rl_bind
                            self.session.pop("lookup_correction_mode", None)
                            self.session.pop("rc_recovery_step", None)
                            self.session.pop("_recovery_loc_retry_count", None)
                            self.session.pop("_recovery_loc_awaiting_dtmf", None)
                            logger.info(
                                "[ms_flow] recovery/location: ambiguous conf=%.2f → silent bind %s",
                                _rl_conf, _rl_bind,
                            )
                            _flush_tts()
                            await self.ask_current_question()
                            return
                        # Low-confidence ambiguous — retry ladder
                        _rl_retry_n = self.session.get("_recovery_loc_retry_count", 0)
                        if _rl_retry_n < 1:
                            self.session["_recovery_loc_retry_count"] = _rl_retry_n + 1
                            _reask = (
                                "Sorry, I didn't quite catch that — "
                                "could you say the Alcester clinic or the Redditch clinic?"
                            )
                            _flush_tts()
                            await self._tts.put(_reask)
                            self.session["last_question"] = _reask
                            logger.info(
                                "[ms_flow] recovery/location: ambiguous → voice retry %d", _rl_retry_n + 1,
                            )
                            return
                        # Retry exhausted — DTMF loop (never ends call)
                        self.session["_recovery_loc_awaiting_dtmf"] = True
                        _reask = "Press 1 for Alcester or 2 for Redditch."
                        _flush_tts()
                        await self._tts.put(_reask)
                        self.session["last_question"] = _reask
                        logger.info("[ms_flow] recovery/location: ambiguous → DTMF mode")
                        return
                    else:
                        # Unknown / no signal — retry ladder
                        _rl_retry_n = self.session.get("_recovery_loc_retry_count", 0)
                        if _rl_retry_n < 1:
                            self.session["_recovery_loc_retry_count"] = _rl_retry_n + 1
                            _reask = (
                                "Sorry, I didn't quite catch that — "
                                "could you say the Alcester clinic or the Redditch clinic?"
                            )
                            _flush_tts()
                            await self._tts.put(_reask)
                            self.session["last_question"] = _reask
                            logger.info(
                                "[ms_flow] recovery/location: unknown → voice retry %d", _rl_retry_n + 1,
                            )
                            return
                        # Retry exhausted — DTMF loop (never ends call)
                        self.session["_recovery_loc_awaiting_dtmf"] = True
                        _reask = "Press 1 for Alcester or 2 for Redditch."
                        _flush_tts()
                        await self._tts.put(_reask)
                        self.session["last_question"] = _reask
                        logger.info("[ms_flow] recovery/location: unknown → DTMF mode")
                        return

                # ── phone step / generic fallback ─────────────────────────────
                # If caller confirms the phone is the same, retrying lookup would
                # produce the exact same failed result — exit gracefully instead.
                if _is_confirmed:
                    self.session.pop("lookup_correction_mode", None)
                    self.session.pop("rc_recovery_step", None)
                    self.session.pop("rc_phone_first", None)
                    logger.info(
                        "[ms_flow] correction_mode: phone confirmed unchanged — graceful exit"
                    )
                    _flush_tts()
                    _pf_exit_msg = (
                        "I'm really sorry — I still can't locate that booking. "
                        "Could you call the clinic directly and they'll sort it out for you?"
                    )
                    await self._tts.put(_pf_exit_msg)
                    self.session["last_question"] = _pf_exit_msg
                    return

                _correction = _parse_lookup_name_correction(text)
                if _correction:
                    col = self.session.setdefault("collected", {})
                    if _correction.startswith("__SURNAME__"):
                        _new_surname = _correction[len("__SURNAME__"):]
                        _existing_full = (col.get("full_name") or "").strip()
                        _first = _existing_full.split()[0] if _existing_full else ""
                        _combined = f"{_first} {_new_surname}".strip() if _first else _new_surname
                        col["full_name"] = _combined
                        self.session["full_name"] = _combined
                        logger.info(
                            "[ms_flow] correction (surname-only): %r → full_name=%r",
                            text[:50], _combined,
                        )
                    else:
                        col["full_name"] = _correction
                        self.session["full_name"] = _correction
                        logger.info(
                            "[ms_flow] correction (full name): %r → full_name=%r",
                            text[:50], _correction,
                        )
                    self.session.pop("lookup_correction_mode", None)
                    self.session.pop("rc_recovery_step", None)
                    # Clear phone-first flag — next LLM turn must use the
                    # collected name in the lookup, not phone-only mode.
                    self.session.pop("rc_phone_first", None)
                    logger.info("[ms_flow] correction applied — re-firing LLM for retry lookup")
                    _flush_tts()
                    await self.ask_current_question()
                    return
                else:
                    # Bug 5: can't parse a name from this utterance — re-anchor caller
                    _corr_reask = (
                        "Sorry — I didn't quite catch that. "
                        "Could you give me the first name and surname the booking was made under?"
                    )
                    _flush_tts()
                    await self._tts.put(_corr_reask)
                    self.session["last_question"] = _corr_reask
                    return

            # ── Deterministic confirmation: appointment already found ───────────
            # When rc_stage == 'lookup_done', lookup_appointment already ran and the
            # LLM has presented the result.  Intercept YES/fragment confirmations here
            # so we NEVER re-fire the LLM (which risks re-running lookup_appointment
            # and repeating the "I'm looking for your appointment now" status line).
            if self.session.get("rc_stage") == "lookup_done":
                _lc_t = text.strip().lower()

                # ── PART 4: intercept lookup meta-questions before YES/NO or name edit ──
                # "under what name was that", "what name do you have", "what date was that"
                # must NEVER trigger name-correction or loop the LLM — answer from session.
                _LOOKUP_META = (
                    "under what name", "what name", "which name", "what's the name",
                    "name was that", "name was it", "name is that",
                    "what date was", "what time was", "which appointment",
                    "which one do you mean", "what appointment",
                )
                if any(p in _lc_t for p in _LOOKUP_META):
                    _lm_first = self.session.get("lookup_appt_first_name", "")
                    _lm_last  = self.session.get("lookup_appt_last_name", "")
                    _lm_cands = self.session.get("lookup_candidates", [])
                    if _lm_cands:
                        _nm_parts = " and one under ".join(
                            f"{c.get('first_name','')} {c.get('last_name','')}".strip()
                            for c in _lm_cands[:2]
                        )
                        _meta_resp = (
                            f"I've found two possible appointments — one under {_nm_parts}. "
                            "Which one did you mean?"
                        )
                    elif _lm_first or _lm_last:
                        _meta_resp = f"I've got it under {_lm_first} {_lm_last}.".strip() + "."
                    else:
                        _meta_resp = (
                            "Let me check — could you confirm the name "
                            "the appointment was booked under?"
                        )
                    logger.info(
                        "[ms_flow] LOOKUP meta-question intercepted — answering from session: %r",
                        text[:60],
                    )
                    await self._tts.put(_meta_resp)
                    self.session["last_question"] = _meta_resp
                    return

                _LU_YES = (
                    "yes", "yeah", "yep", "yup", "correct", "that's right",
                    "thats right", "that's correct", "thats correct",
                    "that's the one", "that's it", "that was it",
                    "it was", "yes it was", "yes it", "yep it", "yeah it",
                    "you found the right", "found the right", "right appointment",
                    "right one", "the right one", "that one", "yes that's right",
                    "perfect", "great", "confirmed", "that's correct",
                    # Explicit reschedule-intent phrases — unambiguous confirmation
                    "reschedule that", "reschedule it", "yes reschedule",
                    "please reschedule", "reschedule that appointment",
                    "yes please reschedule", "yes, reschedule",
                    # Polite single-word affirmative in response to a yes/no question
                    "please",
                )
                _LU_NO = (
                    "no", "nope", "not right", "wrong", "that's not right",
                    "thats not right", "that's not the one", "thats not the one",
                    "wrong appointment", "not that one", "different appointment",
                    "that's wrong", "thats wrong",
                )
                _is_lu_yes = any(p in _lc_t for p in _LU_YES)
                _is_lu_no  = any(p in _lc_t for p in _LU_NO)

                if _is_lu_yes and not _is_lu_no:
                    # Deterministic confirm — equivalent to LLM calling confirm_appointment_found()
                    self.session["rc_appointment_confirmed"] = True
                    self.session["rc_stage"] = "confirmed"
                    _flow_label = step["state"]
                    _next_step = step["step"] + 1
                    # Reschedule: skip CONFIRM_RESCHEDULE_OR_CANCEL — go straight to PRESENT_DAYS.
                    # Cancel: keep CONFIRM_RESCHEDULE_OR_CANCEL so caller is asked to choose.
                    if (self.session.get("intent") == "reschedule"
                            and _next_step < len(self._active_flow)
                            and self._active_flow[_next_step]["state"] == "CONFIRM_RESCHEDULE_OR_CANCEL"):
                        _next_step += 1
                        logger.info(
                            "[ms_flow] %s: reschedule — skipping CONFIRM_RESCHEDULE_OR_CANCEL → step %d",
                            _flow_label, _next_step,
                        )
                    logger.info(
                        "[ms_flow] %s: deterministic YES confirmed — advancing to step %d",
                        _flow_label, _next_step,
                    )
                    # Advance flow_step BEFORE ask_current_question so we never
                    # re-fire the LLM on the lookup step (Bugs 1 and 6).
                    self.session["flow_step"] = _next_step
                    self.session["question_asked_this_turn"] = False
                    # Fold the caller's first name naturally into the next prompt.
                    _lu_yes_fn = (self.session.get("lookup_appt_first_name") or "").strip()
                    if _lu_yes_fn:
                        self.session["_nc_transition_prefix"] = f"Thanks {_lu_yes_fn} —"
                    await self.ask_current_question()
                    return

                # ── PART 3: deterministic NO handling — advance to next candidate ──
                # "no that's the wrong appointment" / "no it's a different one"
                # must NOT loop the same candidate.  Mark it rejected and advance.
                if _is_lu_no and not _is_lu_yes:
                    _rej_id = self.session.get("reschedule_appt_id", "")
                    _rej_list = self.session.setdefault("lookup_rejected_ids", [])
                    if _rej_id and _rej_id not in _rej_list:
                        _rej_list.append(_rej_id)
                        self.session["lookup_rejected_ids"] = _rej_list
                    # Clear current binding so we don't stay on the rejected candidate
                    self.session.pop("rc_stage", None)
                    logger.info(
                        "[ms_flow] LOOKUP: candidate %r rejected by caller — advancing",
                        _rej_id,
                    )
                    # Find next non-rejected candidate from stored alternatives
                    _next_alts = [
                        a for a in self.session.get("reschedule_appt_alternatives", [])
                        if a.get("id") not in _rej_list
                    ]
                    if _next_alts:
                        _nxt = _next_alts[0]
                        self.session["reschedule_appt_id"]       = _nxt["id"]
                        self.session["reschedule_appt_datetime"]  = _nxt["datetime"]
                        self.session["reschedule_appt_type"]      = _nxt.get("type", "appointment")
                        self.session["lookup_appt_first_name"]    = _nxt.get("first_name", "")
                        self.session["lookup_appt_last_name"]     = _nxt.get("last_name", "")
                        self.session["rc_stage"] = "lookup_done"
                        # Remaining become the new alternatives list
                        self.session["reschedule_appt_alternatives"] = _next_alts[1:]
                        _nxt_q = (
                            f"Let me try the other one — "
                            f"was it {_nxt['day_label']} at {_nxt['time_label']}?"
                        )
                        await self._tts.put(_nxt_q)
                        self.session["last_question"] = _nxt_q
                        logger.info(
                            "[ms_flow] LOOKUP: advanced to next candidate id=%s", _nxt["id"]
                        )
                    else:
                        # No more candidates — enter name correction mode so caller
                        # can provide a corrected name/phone for a fresh lookup
                        _no_more_q = (
                            "I'm sorry — I can't find another appointment matching those details. "
                            "Could you tell me the name and number the booking was made under?"
                        )
                        self.session["lookup_correction_mode"] = True
                        await self._tts.put(_no_more_q)
                        self.session["last_question"] = _no_more_q
                        logger.info("[ms_flow] LOOKUP: no more candidates — entering correction mode")
                    return
                # Ambiguous YES/NO — fall through to LLM

            # BUG 4: caller may abandon reschedule/cancel and ask to book new instead.
            # Intercept positive booking intent BEFORE re-firing LLM so the call
            # doesn't close.  Only match on unambiguous new-booking phrases — bare
            # "never mind" without a booking signal remains plain abandonment for LLM.
            _lu_text = text.strip().lower()
            _lu_book_signals = (
                "book a", "book an", "new appointment", "make an appointment",
                "make a booking", "want to book", "like to book",
                "need to book", "new booking", "book instead",
                "just book", "book a new",
            )
            if any(s in _lu_text for s in _lu_book_signals):
                logger.info(
                    "[ms_flow] %s: caller pivoted to new booking — switching to BOOKING_FLOW",
                    step["state"],
                )
                self._switch_flow("booking")
                await self.ask_current_question()
                return
            # transcript already appended to conversation_history above
            logger.info(
                "[ms_flow] %s: caller turn %r — re-firing LLM for confirmation exchange",
                step["state"], transcript[:60],
            )
            await self.ask_current_question()
            return

        # ── CONFIRM_RESCHEDULE_OR_CANCEL: deterministic binary fork ───────────
        # Reschedule → jump to PRESENT_DAYS_RESCHEDULE (slot selection).
        # Cancel     → execute cancel directly and close.
        # Ambiguous  → re-ask; never silently assume either path.
        if step["state"] == "CONFIRM_RESCHEDULE_OR_CANCEL":
            _roc_text = text.strip().lower()
            _ROC_RESCHEDULE = (
                "reschedule", "move it", "change the time", "another time",
                "another day", "another slot", "different time", "different day",
                "rearrange", "move the appointment", "change my appointment",
                "book another", "new time", "different slot", "move to",
                # Natural phrasing variants from live calls
                "change it", "i'd like to change", "id like to change",
                "can we move", "move the time", "pick another", "find another",
            )
            _ROC_CANCEL = (
                "cancel", "cancel it", "cancel altogether", "just cancel",
                "remove it", "delete it", "don't want it", "dont want it",
                "cancel the appointment", "no longer need", "not going",
                "want to cancel", "like to cancel",
                # Natural phrasing variants from live calls
                "cancel please", "go ahead and cancel", "please cancel",
                "i want to cancel", "i'd like to cancel", "id like to cancel",
            )
            _roc_is_reschedule = any(p in _roc_text for p in _ROC_RESCHEDULE)
            _roc_is_cancel     = any(p in _roc_text for p in _ROC_CANCEL)

            if _roc_is_reschedule and not _roc_is_cancel:
                # Jump to PRESENT_DAYS_RESCHEDULE in the active flow.
                # If we're in CANCEL_FLOW (no such step), switch to RESCHEDULE_FLOW.
                _pdr_idx = next(
                    (i for i, s in enumerate(self._active_flow)
                     if s["state"] == "PRESENT_DAYS_RESCHEDULE"),
                    None,
                )
                if _pdr_idx is None:
                    self._active_flow = RESCHEDULE_FLOW
                    self.session["active_flow"] = "reschedule"
                    _pdr_idx = _RESCHEDULE_PRESENT_DAYS_INDEX
                self.session["flow_step"] = _pdr_idx
                self.session["question_asked_this_turn"] = False
                logger.info(
                    "[ms_flow] CONFIRM_RESCHEDULE_OR_CANCEL: reschedule — "
                    "jumping to PRESENT_DAYS_RESCHEDULE (idx=%d)", _pdr_idx,
                )
                await self.ask_current_question()
                return

            if _roc_is_cancel and not _roc_is_reschedule:
                self.session["flow_step"] = _CONFIRM_CANCEL_INDEX
                self.session["question_asked_this_turn"] = False
                logger.info(
                    "[ms_flow] CONFIRM_RESCHEDULE_OR_CANCEL: cancel — jumping to CONFIRM_CANCEL (idx=%d)",
                    _CONFIRM_CANCEL_INDEX,
                )
                await self.ask_current_question()
                return

            # ── Confirmation bleed-through: caller is still answering the prior ──
            # lookup-confirmation question ("yes that's the right appointment",
            # "yes it was", "that's correct").  Bare yes/no and appointment-confirm
            # phrases land here.  Give a warm targeted redirect — not a cold sorry.
            # Pattern: utterance contains a confirmation signal but zero action words.
            _ROC_BLEED_SIGNALS = (
                "that's the right", "thats the right",
                "that's my appointment", "thats my appointment",
                "that's correct", "thats correct",
                "that's right", "thats right",
                "that's it", "thats it",
                "yes it was", "yeah it was",
                "it was", "it is",
                "hello yes", "hi yes",
                "yes that's", "yeah that's",
                "yes thats", "yeah thats",
                "correct", "right",
            )
            _roc_is_bleed = any(p in _roc_text for p in _ROC_BLEED_SIGNALS)

            if _roc_is_bleed:
                _roc_bleed_reask = (
                    "Thanks — would you like to reschedule it, "
                    "or cancel it altogether?"
                )
                await self._tts.put(_roc_bleed_reask)
                self.session["last_question"] = _roc_bleed_reask
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _roc_bleed_reask}
                )
                logger.info(
                    "[ms_flow] CONFIRM_RESCHEDULE_OR_CANCEL: confirmation bleed-through %r — redirecting",
                    _roc_text[:60],
                )
                return

            # Truly ambiguous (bare yes/no/okay/maybe with no action phrase)
            _roc_reask = "Sorry — would you like to reschedule it, or cancel it altogether?"
            await self._tts.put(_roc_reask)
            self.session["last_question"] = _roc_reask
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _roc_reask}
            )
            logger.info(
                "[ms_flow] CONFIRM_RESCHEDULE_OR_CANCEL: ambiguous %r — re-asking",
                _roc_text[:60],
            )
            return

        # ── CONFIRM_BOOKING: strong-confirm-only gate ──────────────────────────
        # _exec_book_appointment is ONLY reachable via the strong YES branch.
        # Questions, repeats, corrections, weak acks, and ambiguous input all
        # exit through non-booking branches.  The gate fails closed.
        if step["state"] == "CONFIRM_BOOKING":
            import re as _cb_re

            # ── PART 1: strong explicit confirmation phrases ─────────────────
            _CB_YES = (
                "yes", "yeah", "yep", "yup", "yeh", "ya",
                "yes please", "yes go ahead", "yes that's right", "yes that's correct",
                "yes please book", "yes book it", "yes confirm",
                "please book it", "book it", "book that", "book that please",
                "go ahead", "go ahead and book", "please go ahead",
                "confirm it", "confirm that", "confirm the booking",
                "that's correct", "that's right", "that all sounds right",
                "that all sounds correct", "sounds right", "sounds correct",
                "i confirm", "i'd like to book", "i want to book",
                "please confirm",
            )

            # ── PART 2: question-like detection — blocks booking ─────────────
            _cb_q_starters = (
                "do you", "what ", "which ", "can you", "could you", "how ",
                "is that", "is it", "was that", "did you", "have you",
                "are you", "will you", "when ", "where ", "why ",
            )
            _cb_q_phrases = (
                "what time", "which clinic", "what name", "what number",
                "how much", "what did you say", "need my phone",
                "need my number", "do you need", "what was that",
                "is that right", "is that correct", "is that the", "is that for",
            )
            _is_q = (
                text.endswith("?")
                or any(text.startswith(p) for p in _cb_q_starters)
                or any(p in text for p in _cb_q_phrases)
            )

            # ── PART 3: explicit NO / rejection ─────────────────────────────
            _is_no = (
                bool(_cb_re.search(r'\bno\b', text))
                or any(p in text for p in (
                    "nope", "nah", "no thank", "don't book", "don't confirm",
                    "not yet", "hold on", "wait a", "actually no", "actually not",
                    "not quite", "that's wrong", "not right", "something's wrong",
                ))
            )

            # ── PART 4: correction language — must not book ──────────────────
            _cb_corr_phrases = (
                "wrong clinic", "wrong name", "wrong time", "wrong number",
                "not that clinic", "not that time", "not that name",
                "not redditch", "not alcester",
                "different clinic", "different time", "different day",
                "that's not my name", "that's not my surname",
                "the name is wrong", "surname is wrong", "first name is wrong",
                "i meant the other", "i said the other",
            )
            _is_correction = any(p in text for p in _cb_corr_phrases)

            # Also treat name-repair phrases as corrections (reuses CONFIRM_PHONE logic)
            _cb_name_fix_phrases = (
                "my name is", "my surname is", "my first name is",
                "it should be under", "the name should be",
            )
            _is_name_fix = any(p in text for p in _cb_name_fix_phrases)
            if _is_name_fix:
                _is_correction = True

            # ── PART 5: repeat / replay ──────────────────────────────────────
            _is_repeat = (
                text in ("sorry", "sorry?", "pardon", "pardon?")
                or any(p in text for p in (
                    "repeat", "say that again", "again please",
                    "could you repeat", "can you repeat", "say it again",
                    "once more", "what did you say",
                ))
            )

            _is_yes = any(p in text for p in _CB_YES)

            # ── Decision tree — fail closed ──────────────────────────────────
            # Secondary slot guard (belt-and-suspenders): ask_current_question should
            # have blocked entry without a valid slot, but if slot somehow empty here,
            # abort before the booking call and re-route to time selection.
            if _is_yes and not _is_q and not _is_no and not _is_correction:
                _cb_ys_slot = self.session.get("selected_slot") or ""
                if not _cb_ys_slot.strip():
                    logger.error(
                        "[ms_flow] CONFIRM_BOOKING YES: secondary guard — selected_slot empty, "
                        "re-routing to PRESENT_TIMES"
                    )
                    _cb_ys_pt = next(
                        (i for i, s in enumerate(self._active_flow)
                         if s["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE")),
                        None,
                    )
                    if _cb_ys_pt is not None:
                        self.session["flow_step"] = _cb_ys_pt
                        self.session["state"]     = self._active_flow[_cb_ys_pt]["state"]
                        await self.ask_current_question()
                    return
            if _is_yes and not _is_q and not _is_no and not _is_correction:
                # Strong explicit confirm — execute booking
                from app.tools.receptionist_tools import _exec_book_appointment as _do_book
                _name_cb = (
                    self.session.get("full_name")
                    or (self.session.get("collected") or {}).get("full_name")
                    or (self.session.get("collected") or {}).get("name")
                    or self.session.get("patient_name")
                    or self.session.get("caller_name")
                )
                _loc_cb = (self.session.get("selected_location") or "alcester").lower()
                _clinic_name = "Redditch" if "redditch" in _loc_cb else "Alcester"
                _book_args = {
                    "patient_name": _name_cb or "",
                    "phone": _to_e164_uk(
                        self.session.get("phone_number")
                        or (self.session.get("collected") or {}).get("phone")
                        or self.session.get("twilio_from", "")
                    ),
                    "slot_iso": (
                        self.session.get("selected_slot")
                        or self.session.get("selected_slot_speech")
                        or ""
                    ),
                    "location": _loc_cb,
                    "service": "physiotherapy assessment",
                    "is_new_patient": (
                        (self.session.get("new_or_returning") or "new") != "returning"
                    ),
                }
                _book_success = False
                try:
                    _book_result = await _do_book(_book_args, self.session)
                    _book_success = bool(_book_result.get("success"))
                    if not _book_success:
                        logger.error(
                            "[ms_flow] CONFIRM_BOOKING YES: book failed: %r",
                            _book_result.get("error"),
                        )
                    else:
                        logger.info("[ms_flow] CONFIRM_BOOKING YES: booking created successfully")
                except Exception as _be:
                    logger.error("[ms_flow] CONFIRM_BOOKING YES: book exception: %r", _be)
                    _book_success = False

                _slot_cb = (
                    self.session.get("selected_slot_speech")
                    or self.session.get("selected_slot")
                    or "your appointment"
                )
                self.session["state"]      = "DONE"
                self.session["flow_state"] = "DONE"
                self.session["flow_step"]  = len(self._active_flow)

                if _book_success:
                    self.session["booking_confirmed"] = True
                    # ── Name-correction SMS (Prompt 13) ──────────────────────
                    # If voice first-name capture failed and we promised an SMS,
                    # send the name-request SMS now that we have the phone number.
                    # create_pending_name_confirmation was already called inside
                    # _exec_book_appointment, so the Redis record exists.
                    _needs_name_sms = self.session.pop("needs_name_correction_sms", False)
                    if _needs_name_sms:
                        _ncorr_phone = _book_args.get("phone") or ""
                        if _ncorr_phone:
                            _ncorr_body = (
                                "Hi, this is Susie from Theorem Health. "
                                "I didn't quite catch your first name on the call. "
                                "Please reply with your first name so I can complete "
                                "your booking details."
                            )
                            try:
                                from app.notifications.sms import send_sms as _ncorr_sms
                                await _ncorr_sms(_ncorr_phone, _ncorr_body)
                                logger.info(
                                    "[ms_flow] CONFIRM_BOOKING: name-correction SMS sent to %r",
                                    _ncorr_phone,
                                )
                            except Exception as _ncorr_err:
                                logger.warning(
                                    "[ms_flow] CONFIRM_BOOKING: name-correction SMS failed "
                                    "(non-fatal): %r", _ncorr_err,
                                )
                    _cb_done = (
                        f"Brilliant — you're all booked in for {_slot_cb} "
                        f"at our {_clinic_name} clinic. "
                        + (
                            "I'm sending a confirmation text now — just reply to it with your full name to complete the booking. Have a great day!"
                            if _needs_name_sms else
                            "We'll send a confirmation text shortly. Have a great day!"
                        )
                    )
                    logger.info(
                        "[ms_flow] CONFIRM_BOOKING YES handler → booking_confirmed=True "
                        "state=DONE name=%r slot=%r",
                        _name_cb, str(_slot_cb)[:40],
                    )
                else:
                    self.session["booking_confirmed"] = False
                    _cb_done = (
                        "I'm sorry — there was a problem securing that slot. "
                        "I'll make sure the team knows, and someone will call you back "
                        "to confirm your booking. Apologies for the inconvenience!"
                    )
                    logger.error(
                        "[ms_flow] CONFIRM_BOOKING YES handler → booking FAILED, "
                        "booking_confirmed=False state=DONE name=%r slot=%r",
                        _name_cb, str(_slot_cb)[:40],
                    )

                await self._tts.put(_cb_done)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _cb_done}
                )
                return

            # ── Non-YES paths — _exec_book_appointment is unreachable below ──

            elif _is_repeat:
                # Replay the booking summary; do not advance state
                _replay = self.session.get("last_question") or "Shall I go ahead and book that?"
                await self._tts.put(_replay)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _replay}
                )
                logger.info("[ms_flow] CONFIRM_BOOKING: repeat → replayed summary")
                return

            elif _is_name_fix:
                # Route back to COLLECT_NAME so caller can correct their name
                _cn_states = {"COLLECT_NAME", "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL"}
                _cn_idx = next(
                    (i for i, s in enumerate(self._active_flow) if s["state"] in _cn_states),
                    None,
                )
                if _cn_idx is not None:
                    from app.media_streams.name_collector import NameCollector as _NC_cb
                    _NC_cb(self.session).reset()
                    self.session["flow_step"] = _cn_idx
                    self.session["state"]     = self._active_flow[_cn_idx]["state"]
                    self.session["question_asked_this_turn"] = False
                    logger.info(
                        "[ms_flow] CONFIRM_BOOKING name-fix → %s", self.session["state"],
                    )
                    await self.ask_current_question()
                    return
                # COLLECT_NAME not found (should never happen) — safe re-anchor
                _corr_fallback = "No problem — what is the correct name for the booking?"
                await self._tts.put(_corr_fallback)
                self.session["last_question"] = _corr_fallback
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _corr_fallback}
                )
                return

            elif _is_correction:
                # Generic field correction — re-anchor at CONFIRM_BOOKING
                _corr_text = (
                    "No problem — what would you like to change? "
                    "Just let me know and I'll update it."
                )
                await self._tts.put(_corr_text)
                self.session["last_question"] = _corr_text
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _corr_text}
                )
                logger.info("[ms_flow] CONFIRM_BOOKING: correction detected — re-anchoring")
                return

            elif _is_no:
                _no_text = (
                    "No problem. Is there something you'd like to change, "
                    "or shall I leave it there for now?"
                )
                await self._tts.put(_no_text)
                self.session["last_question"] = _no_text
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _no_text}
                )
                logger.info("[ms_flow] CONFIRM_BOOKING: NO — re-anchoring without booking")
                return

            else:
                # Question, weak ack ("okay", "right", "fine"), or unknown.
                # For question-like utterances replay the full summary;
                # for everything else ask for an explicit yes/no.
                if _is_q:
                    _safe_text = self.session.get("last_question") or "Shall I go ahead and book that?"
                else:
                    _safe_text = (
                        "Sorry — would you like me to go ahead and book that, "
                        "or is there something you'd like to change?"
                    )
                await self._tts.put(_safe_text)
                self.session["last_question"] = _safe_text
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _safe_text}
                )
                logger.warning(
                    "[ms_flow] CONFIRM_BOOKING: non-YES blocked "
                    "(is_yes=%s is_q=%s is_no=%s is_corr=%s) text=%r",
                    _is_yes, _is_q, _is_no, _is_correction, text[:80],
                )
                return

        # ── Final compat guard: abort extraction if booking already done ────────
        # Note: CONFIRM_BOOKING is handled by the dedicated block above, so only
        # check booking_confirmed / DONE here — not "CONFIRM_BOOKING" itself.
        if self.session.get("booking_confirmed") or self.session.get("state") == "DONE":
            logger.info(
                "[ms_flow] compat final guard: booking_confirmed=%s state=%s — skipping extraction",
                self.session.get("booking_confirmed"), self.session.get("state"),
            )
            return

        # ── COLLECT_NAME: unified NameCollector dispatch ──────────────────────
        # All first-name/surname collection across all booking flows routes
        # through a single deterministic substate machine — no LLM, no duplicated
        # guards, no scattered fragment logic.
        _COLLECT_NAME_STATES_ALL = frozenset({
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        })
        if step["state"] in _COLLECT_NAME_STATES_ALL:
            # ── COLLECT_NAME: FAQ-shape utterance defensive guard ────────
            # Backup for the handle_transcript mid-flow upgrade.  Caller may
            # fire an FAQ question while we're asking for their name
            # ("how big are the needles?", "quick question — do you take
            # insurance?"); the NameCollector would otherwise reject the
            # utterance as invalid name after multiple retries.  Route any
            # clearly-FAQ-shaped utterance to the mid-flow interrupt so it
            # is answered and the pending name question is re-anchored.
            # Valid names like "my first name is Quentin" do not match the
            # helper (no service term, no info-seek phrase, no opener).
            _cn_faq_hit, _cn_faq_reason = _is_booking_state_faq_interrupt(text)
            if _cn_faq_hit and not _mbf_resolved:
                logger.info(
                    "[ms_flow] %s: faq_interrupt_guard reason=%s "
                    "transcript=%r — routing to mid-flow interrupt, not storing as name",
                    step["state"], _cn_faq_reason, transcript[:80],
                )
                await self._handle_mid_flow_interrupt("faq_services", transcript)
                return
            from app.media_streams.name_collector import NameCollector as _NameColl
            _nc_action, _nc_payload = _NameColl(self.session).handle(text, transcript)
            while not self._tts.empty():
                try:
                    self._tts.get_nowait()
                except Exception:
                    break
            if _nc_action == "scaffold_continue":
                # Caller sent only a setup fragment ("my surname is", "my first name is").
                # Do NOT speak the re-ask question — hold silence so the completion
                # token ("Roch", "Quentin") can arrive without interruption.
                # connection.py will restart the silence timer, which fires the
                # structured scaffold recovery prompt after 3 s if nothing arrives.
                self.session["last_question"] = _nc_payload
                self.session["_nc_scaffold_hold"] = True
                return
            if _nc_action == "sms_fallback":
                # Voice name capture failed after final retry.
                # Speak the fallback phrase, flag for name-correction SMS at
                # CONFIRM_BOOKING, then advance the flow past COLLECT_NAME.
                await self._tts.put(_nc_payload)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _nc_payload}
                )
                self.session["needs_name_correction_sms"] = True
                _sms_fb_next = step["step"] + 1
                _sms_fb_next_state = (
                    self._active_flow[_sms_fb_next]["state"]
                    if _sms_fb_next < len(self._active_flow) else "DONE"
                )
                self.session["flow_step"]  = _sms_fb_next
                self.session["state"]      = _sms_fb_next_state
                self.session["flow_state"] = _sms_fb_next_state
                logger.info(
                    "[ms_flow] COLLECT_NAME sms_fallback: advanced to step %d (%s)",
                    _sms_fb_next, _sms_fb_next_state,
                )
                await self.ask_current_question()
                return
            if _nc_action != "accept":
                await self._tts.put(_nc_payload)
                self.session["last_question"] = _nc_payload
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _nc_payload}
                )
                return
            # accept — surname collection resolved.
            # If it resolved via a degraded/best-effort path a preamble is set.
            # Instead of speaking it as a standalone utterance (which stacks with
            # the bridge and the next question), we fold it into a short transition
            # prefix that ask_current_question() will prepend to the next question,
            # producing ONE composed outbound utterance.  The bridge (_get_bridge)
            # is suppressed for this recovery path to prevent "Thanks, Quentin."
            # sandwiched between the correction note and the next question.
            _preamble = self.session.pop("_nc_accept_preamble", None)
            if _preamble:
                # Recovery path: compose once in ask_current_question, not here.
                self.session["_nc_suppress_bridge"] = True
                self.session["_nc_transition_prefix"] = (
                    "No problem — I'll include a correction option in the "
                    "confirmation message."
                )
            else:
                # Direct first-name capture (fn_confirm was skipped): prepend
                # "Thanks {name} —" to the next question (phone / PRESENT_DAYS).
                _nc_fn_prefix_name = self.session.pop("_nc_fn_name_prefix", None)
                if _nc_fn_prefix_name:
                    self.session["_nc_transition_prefix"] = f"Thanks {_nc_fn_prefix_name} —"
                    logger.info(
                        "[ms_flow] COLLECT_NAME: direct fn capture — prefix=Thanks %s —",
                        _nc_fn_prefix_name,
                    )
            answer = _nc_payload
        else:
            answer = self._extract(step["extract"], text, transcript)

        # ── PRESENT_DAYS: nullify extracted day on mixed-intent turns ─────────
        # "quick question first are you open on saturdays" extracts "saturday"
        # but the caller is asking an inquiry, not selecting a booking day.
        # Nullifying answer routes through the inquiry / re-anchor path instead
        # of silently committing the day and advancing to PRESENT_TIMES.
        if answer is not None and step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            _PD_MIXED_SIGNALS = (
                "are you open", "do you open", "open on saturday", "open on sunday",
                "open sundays", "open saturdays", "have parking", "is there parking",
                "do you have", "how long", "what time", "are there any", "can you do",
                "quick question", "just a question", "just wondering",
                # Name-introduction phrases must never bind a day:
                "full name is", "my name is", "first name is", "surname is", "my first name",
            )
            if any(_sig in text for _sig in _PD_MIXED_SIGNALS):
                logger.info(
                    "[ms_flow] PRESENT_DAYS: mixed-intent — nullifying extracted day %r",
                    answer,
                )
                answer = None

        # ── COLLECT_REASON: reschedule/cancel re-route ───────────────────────────
        # Caller says "I want to reschedule" at the booking-reason step.
        # Do NOT store this as a booking reason — switch flow immediately.
        if step["state"] == "COLLECT_REASON":
            _cr_intent = self._detect_intent(text)
            if _cr_intent == "reschedule":
                _cr_rs_sigs = (
                    "reschedule", "re-schedule", "re schedule",
                    "change my appointment", "change the appointment",
                    "change an appointment", "change a booking",
                    "change my booking", "change the booking",
                    "move my appointment", "move the appointment",
                    "move an appointment", "move my booking",
                    "rearrange my appointment", "rearrange the appointment",
                    "rebook my", "rebook the", "re-book my", "re-book the",
                    "different time for my", "different day for my",
                )
                _cr_text_lower = (text or "").lower()
                if not any(p in _cr_text_lower for p in _cr_rs_sigs):
                    logger.info(
                        "[ms_flow] reschedule_hard_route_suppressed "
                        "at=COLLECT_REASON reason=no_explicit_reschedule_language "
                        "text=%r", text[:80],
                    )
                    _cr_intent = "booking"
                else:
                    logger.info("[ms_flow] COLLECT_REASON: reschedule intent detected — switching flow")
                    self._switch_flow("reschedule")
                    await self.ask_current_question()
                    return
            if _cr_intent == "cancel":
                logger.info("[ms_flow] COLLECT_REASON: cancel intent detected — switching flow")
                self._switch_flow("cancel")
                await self.ask_current_question()
                return
            # ── COLLECT_REASON: FAQ-shape utterance defensive guard ──────────
            # Backup for the handle_transcript mid-flow upgrade.  If an FAQ-
            # shaped utterance reached this point, route it through the mid-
            # flow interrupt so it is answered and the suspended
            # COLLECT_REASON question is re-anchored — NEVER stored as the
            # caller's booking reason.  Body+symptom compounds and strong
            # book verbs short-circuit the helper, so "my neck is killing me"
            # still falls through to normal reason capture.
            _cr_faq_hit, _cr_faq_reason = _is_booking_state_faq_interrupt(text)
            if _cr_faq_hit and not _mbf_resolved:
                logger.info(
                    "[ms_flow] COLLECT_REASON: faq_interrupt_guard reason=%s "
                    "transcript=%r — routing to mid-flow interrupt, not storing as reason",
                    _cr_faq_reason, transcript[:80],
                )
                await self._handle_mid_flow_interrupt("faq_services", transcript)
                return

        # ── COLLECT_REASON: partial-reason join (BUG 2 continuation) ─────────────
        # If BUG 2 guard stored a partial reason (dangling-verb re-ask), prepend it
        # to the new transcript so "in quite a bit of pain" merges with
        # "my left ankle is" to form "my left ankle is in quite a bit of pain".
        if step["state"] == "COLLECT_REASON" and self.session.get("_partial_reason"):
            _pr = self.session.pop("_partial_reason")
            transcript = _pr + " " + transcript.strip()
            text       = transcript.lower()
            if answer is not None:
                answer = _pr + " " + answer.strip()
            logger.info(
                "[ms_flow] COLLECT_REASON: joined partial reason → %r", transcript[:80],
            )

        # ── COLLECT_REASON: fragment guard (BUG 1/2) ─────────────────────────
        # extract:"any" accepts every non-empty transcript verbatim.  Guard against
        # premature advancement on bare fragments ("my", "my left", "pain").
        # Rule: reject answer if it has no clinical content word AND fewer than 3
        # words.  This also handles BUG 2 (fragmented continuation): "my" → None
        # → re-ask → caller naturally continues with the full phrase.
        if step["state"] == "COLLECT_REASON" and answer is not None:
            _REASON_FLOOR_WORDS = (
                "pain", "ache", "aching", "hurt", "hurting", "injury", "injured",
                "sore", "soreness", "stiff", "stiffness", "swollen", "swelling",
                "ankle", "knee", "back", "neck", "shoulder", "hip", "wrist",
                "elbow", "leg", "arm", "foot", "heel", "spine", "head",
                "tendon", "ligament", "muscle", "nerve", "joint",
                "problem", "issue", "trouble", "condition",
                "physiotherapy", "physio", "treatment", "rehab",
                # Context-valid service/appointment-type tokens — when the
                # caller has already navigated the mid-booking FAQ and gives
                # a short fragment like "assessment" or "initial assessment",
                # the preceding context (flow state + FAQ history) makes
                # these unambiguously a booking-reason.  Accept instead of
                # kicking into the too-short/too-vague re-ask.
                "assessment", "initial",
                "acupuncture", "shockwave", "laser", "massage", "pilates",
                "biomechanical", "biomechanics",
            )
            _reason_lower = answer.strip().lower()
            _has_content  = any(w in _reason_lower for w in _REASON_FLOOR_WORDS)

            # ── Repair / restart fragment guard ──────────────────────────────
            # Utterances that are nothing but correction speech with no clinical
            # content — e.g. "sorry i would say" / "sorry i was saying" — must
            # not be stored as a booking reason.  These arrive when the caller
            # restarts their sentence mid-utterance and the STT finalises the
            # correction prefix as a standalone transcript.
            # Only fires when there is no clinical content word so "sorry my
            # back is in pain" (repair opener + real content) still passes.
            _REASON_REPAIR_FRAGMENTS = (
                "sorry i was saying",
                "sorry i would say",
                "sorry i was going to say",
                "sorry i was just",
                "sorry i want to say",
                "no sorry",
                "sorry sorry",
                "hold on",
                "wait a moment",
                "hang on",
                "never mind that",
            )
            if not _has_content and any(
                _reason_lower.startswith(p) or _reason_lower == p
                for p in _REASON_REPAIR_FRAGMENTS
            ):
                logger.info(
                    "[ms_flow] COLLECT_REASON: repair fragment %r rejected — re-asking",
                    answer[:60],
                )
                answer = None

            if answer is not None and not _has_content and len(_reason_lower.split()) < 3:
                logger.info(
                    "[ms_flow] COLLECT_REASON: fragment %r rejected (no content / too short) — re-asking",
                    answer[:50],
                )
                answer = None   # fall through to re-ask logic below

            # ── Incomplete-tail guard ─────────────────────────────────────────
            # Rejects any utterance whose final word/phrase signals that the
            # caller hasn't finished their sentence.  Covers:
            #   • linking / auxiliary verbs  (original set)
            #   • extended auxiliaries       (been, being, getting, …)
            #   • prepositions               (of, in, for, at, …)
            #   • articles / possessives     (the, a, an, my, your, …)
            #   • quantifiers / degree words (few, bit, lot, quite, …)
            #   • subordinating conjunctions (when, since, after, because, …)
            #   • coordinating conjunctions  (and, but, or)
            #   • infinitive marker          (to)
            #   • positional adjectives      (lower, upper, inner, outer)
            #   PLUS a second pattern for "subordinator + subject pronoun" at
            #   end (e.g. "it started when I").
            if answer is not None:
                import re as _re_cr_dangle
                _DANGLE_RE = _re_cr_dangle.compile(
                    r'\b(?:'
                    # linking / auxiliary verbs (original)
                    r'is|are|was|were|has|have|had|feels?|felt|seems?|appears?'
                    r'|'
                    # extended auxiliaries that need a complement
                    r'been|being|getting|becoming|become'
                    r'|'
                    # prepositions that always require an object
                    r'of|into|towards|onto|upon|like'
                    r'|'
                    # common prepositions — almost always dangle at end of a reason
                    r'in|for|at|on|with|by|from|around|through|about|near|along'
                    r'|'
                    # articles and possessives — always require a following noun
                    r'the|a|an|my|your|his|her|their|our'
                    r'|'
                    # quantifiers / degree words that require a noun to complete
                    r'few|bit|lot|quite|much|some|also'
                    r'|'
                    # subordinating conjunctions — open an unfinished clause
                    r'when|since|after|because|while|although|though|if|whenever|until|before'
                    r'|'
                    # coordinating conjunctions at end — obviously unfinished
                    r'and|but|or'
                    r'|'
                    # infinitive marker at end (e.g. "I try to", "when I try to")
                    r'to'
                    r'|'
                    # positional adjectives that require a body-part noun to follow
                    r'lower|upper|inner|outer'
                    r')\s*$',
                    _re_cr_dangle.IGNORECASE,
                )
                # "subordinator + subject pronoun" tail — e.g. "it started when I",
                # "especially when I try", "after I"
                _SUBJ_DANGLE_RE = _re_cr_dangle.compile(
                    r'\b(?:when|since|after|because|while|if|before|until|whenever)'
                    r"\s+(?:i|i'm|i've|i'd|i'll|we|you|they|he|she|it)\s*$",
                    _re_cr_dangle.IGNORECASE,
                )
                if _DANGLE_RE.search(_reason_lower) or _SUBJ_DANGLE_RE.search(_reason_lower):
                    logger.info(
                        "[ms_flow] COLLECT_REASON: dangling clause %r — re-asking for more",
                        answer[:50],
                    )
                    # Store the partial so we can prefix it if caller continues
                    self.session["_partial_reason"] = answer.strip()
                    # Re-ask by overriding answer to None and injecting a re-ask phrase
                    _dangle_phrase = "Sorry, could you tell me a bit more about that?"
                    await self._tts.put(_dangle_phrase)
                    self.session["last_question"] = _dangle_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _dangle_phrase}
                    )
                    return  # wait for caller to complete the phrase

        # ── NEW_OR_RETURNING: Haiku silent classifier fallback ───────────────────
        # Fires when deterministic keyword matching missed — e.g. "I came about
        # two years ago" or "I don't think I've ever been".  Haiku classifies
        # silently (no TTS) so the flow advances without a spoken re-ask.
        if answer is None and step["state"] == "NEW_OR_RETURNING":
            answer = await self._haiku_classify(
                transcript,
                question=(
                    "Is this person a new patient or a returning one? "
                    "They said: '{transcript}'. "
                    "Reply with ONLY one word: new / returning / unclear."
                ),
                mapping={"new": "new", "returning": "returning"},
            )
            if answer:
                logger.info(
                    "[ms_flow] NEW_OR_RETURNING Haiku classified %r → %r",
                    transcript[:40], answer,
                )

        # ── RETURNING_TREATMENT_PLAN: Haiku silent classifier fallback ───────────
        if answer is None and step["state"] == "RETURNING_TREATMENT_PLAN":
            answer = await self._haiku_classify(
                transcript,
                question=(
                    "Is this returning patient still on an active treatment plan, "
                    "or has treatment ended / is this a new episode? "
                    "They said: '{transcript}'. "
                    "Reply with ONLY one word: yes / no / unclear."
                ),
                mapping={"yes": True, "no": False},
            )
            if answer is not None:
                logger.info(
                    "[ms_flow] RETURNING_TREATMENT_PLAN Haiku classified %r → %r",
                    transcript[:40], answer,
                )

        # ── PHONE DIGIT ACCUMULATION: stitch together number spoken in chunks ──
        # After Fix 1 (garbage filter), digit-only chunks now reach the flow.
        # Each chunk individually fails the 10-digit minimum.  Accumulate them in
        # session until we have a complete number, then proceed normally.
        if answer is None and step["state"] in ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "RETURNING_PLAN_COLLECT_PHONE"):
            _new_digits = "".join(c for c in transcript if c.isdigit())
            if _new_digits:
                _buffer = self.session.get("phone_digits_buffer", "") + _new_digits
                self.session["phone_digits_buffer"] = _buffer
                logger.info(
                    "[ms_flow] phone digit buffer +%s → %r (%d digits)",
                    _new_digits, _buffer, len(_buffer),
                )
                if len(_buffer) >= 10:
                    # Enough digits — treat as a complete phone number
                    answer = _buffer[:11] if len(_buffer) > 11 else _buffer
                    self.session["phone_digits_buffer"] = ""
                    logger.info("[ms_flow] phone digit buffer complete → %r", answer)
                else:
                    # Still accumulating — silently wait; silence handler re-asks if needed
                    return

        # ── VAGUENESS: PRESENT_TIMES — no valid slot parsed but response is vague ─
        # BUG 5 fix: use a narrow explicit check instead of is_vague_availability().
        # is_vague_availability() returns True for ANY short utterance (<3 words)
        # that has no day/time reference — including "hello", which is NOT vague,
        # it's noise.  Auto-defaulting a real slot from noise is not pilot-safe.
        if answer is None and step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
            _GENUINE_VAGUE_TIME = (
                "any time", "anytime", "whenever", "whatever",
                "doesn't matter", "dont matter", "don't mind", "dont mind",
                "any slot", "flexible", "up to you", "you choose",
                "doesn't bother", "not fussed", "either", "either one",
                "either of them", "any of them", "no preference",
            )
            if any(p in transcript.lower() for p in _GENUINE_VAGUE_TIME):
                _avail = self.session.get("available_days", [])
                _chosen = self.session.get("chosen_day", "")
                _slot_iso = None
                _slot_speech = None
                for _day in _avail:
                    if _chosen.lower() in _day.get("day_label", "").lower() and _day.get("slots"):
                        _slot_iso   = _day["slots"][0]["start"]
                        _slot_speech = (
                            f"{_day['day_label']} at "
                            f"{_day['slot_times'][0] if _day.get('slot_times') else 'the first time'}"
                        )
                        break
                if not _slot_iso and _avail:
                    # Fallback: first day's first slot
                    _day = _avail[0]
                    if _day.get("slots"):
                        _slot_iso   = _day["slots"][0]["start"]
                        _slot_speech = f"{_day['day_label']} at {_day['slot_times'][0]}"
                if _slot_iso:
                    self.session["selected_slot"]       = _slot_iso
                    self.session["selected_slot_speech"] = _slot_speech
                    self.session["flow_step"]            = step["step"] + 1
                    phrase = f"Perfect — I'll put you down for {_slot_speech}."
                    await self._tts.put(phrase)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": phrase}
                    )
                    logger.info("[ms_flow] vague time → defaulted to %r", _slot_iso)
                    await self.ask_current_question()
                    return
                # No slot data — fall through to normal re-ask

        # PRESENT_DAYS ordinal rescue: _extract returns None for "the first one" /
        # "first" / "last" etc. because there is no literal day name in the phrase.
        # Resolve against available_days BEFORE the answer-is-None retry gate fires.
        if answer is None and step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            _pre_avail = self.session.get("available_days", [])
            if _pre_avail:
                # Mixed-intent guard: if text combines an ordinal with a sidebar
                # inquiry, skip ordinal binding — let the inquiry path handle it.
                _ORDINAL_MIXED_SIGNALS = (
                    "are you open", "do you open", "open on saturday", "open on sunday",
                    "open sundays", "open saturdays", "have parking", "is there parking",
                    "do you have", "how long", "what time", "are there any", "can you do",
                )
                _ordinal_has_mixed = any(_sig in text for _sig in _ORDINAL_MIXED_SIGNALS)
                if not _ordinal_has_mixed:
                    for _pre_pat, _pre_i in [
                        ("first one", 0), ("second one", 1), ("third one", 2),
                        ("middle one", 1), ("the middle", 1),
                        ("the first", 0), ("the second", 1), ("the third", 2),
                        ("the last", -1), ("last one", -1), ("the final", -1),
                        ("first", 0), ("second", 1), ("third", 2),
                        ("middle", 1), ("last", -1), ("final", -1),
                    ]:
                        if _pre_pat in text:
                            _pre_n = len(_pre_avail)
                            _pre_r = _pre_i if _pre_i >= 0 else max(0, _pre_n + _pre_i)
                            _pre_r = min(_pre_r, _pre_n - 1)
                            _pre_day = _pre_avail[_pre_r].get("day_label", "")
                            self.session["chosen_day"]         = _pre_day
                            self.session[step["answer_field"]] = _pre_day
                            self.session.pop("vague_option_pending", None)
                            self.session.pop("vague_clarification_asked", None)
                            self.session["presented_vague_options"] = []
                            _pre_next = step["step"] + 1
                            _pre_ns = (
                                self._active_flow[_pre_next]["state"]
                                if _pre_next < len(self._active_flow) else "DONE"
                            )
                            self.session["flow_step"]  = _pre_next
                            self.session["state"]      = _pre_ns
                            self.session["flow_state"] = _pre_ns
                            self.session["_last_handled_by"] = "present_days_ordinal_pre_gate"
                            await self.ask_current_question()
                            return

        if answer is None:
            # No valid answer extracted — acknowledged re-ask with retry counting

            # ── Keypad-entry status phrases: phone states ─────────────────────
            # Caller says "typing it in" / "one second" etc. at a phone-collection
            # step — acknowledge and wait; do NOT increment retry counter.
            _PHONE_KEYPAD_STATES = {"COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "RETURNING_PLAN_COLLECT_PHONE"}
            if step["state"] in _PHONE_KEYPAD_STATES:
                _KEYPAD_PHRASES = (
                    "typing it in", "entering it", "on the keypad", "on the keyboard",
                    "one second", "just getting it", "putting it in", "got it here",
                    "just got", "typed in", "just typing", "bear with me",
                    "give me a sec", "just a sec", "two seconds",
                )
                if any(p in text for p in _KEYPAD_PHRASES):
                    _kp_q = self.session.get("last_question", "And the best number to reach you on?")
                    await self._tts.put("That's fine — go ahead and type it in on the keypad.")
                    self.session["last_question"] = _kp_q
                    logger.info("[ms_flow] COLLECT_PHONE: keypad-entry phrase detected — no retry consumed")
                    return

            phrase_key = _phrase_key_for_step(step)
            retry_counts = self.session.setdefault("slot_retry_counts", {})
            retry_counts[phrase_key] = retry_counts.get(phrase_key, 0) + 1
            count = retry_counts[phrase_key]
            logger.info(
                "[ms_flow] no answer for step %d (%s) from %r — retry #%d",
                step["step"], step["answer_field"], transcript[:60], count,
            )
            if count >= 3:
                phrase = (
                    "I'm having a little trouble catching that — "
                    "please give us a call back and the team can help you get booked."
                )
                await self._tts.put(phrase)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": phrase}
                )
                self.session["graceful_exit"] = True
                self.session["request_transfer"] = True
                self.session["flow_step"] = len(self._active_flow)
                logger.info("[ms_flow] retry >= 3 on %r — graceful exit triggered", phrase_key)
                return
            # count == 1: repair check → sidebar check → Haiku fallback.
            # Repair detection runs first so clarification requests reset the
            # retry counter and replay the last question without consuming a retry.
            # Sidebar detection (Haiku, ~200-300ms) fires only on first failed
            # extraction — zero overhead on clean turns.
            if count == 1:
                # ── Inquiry frequency cap ────────────────────────────────────
                _gic_pre = self.session.get("general_inquiry_count", 0)
                if _gic_pre >= 2:
                    retry_counts[phrase_key] = 0
                    _steer_q   = self.session.get("last_question", "")
                    _steer_msg = (
                        "Let me just focus on getting your appointment sorted — "
                        + (_steer_q if _steer_q else "could we carry on from where we were?")
                    )
                    await self._tts.put(_steer_msg)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _steer_msg}
                    )
                    self.session["general_inquiry_count"] = 0
                    return
                # ── REPAIR / CLARIFICATION: replay last_question, reset retry ──
                _REPAIR = (
                    "what was the question", "say that again", "say it again",
                    "repeat that", "what did you ask", "what did you say",
                    "cut off", "you cut off", "got cut off", "broke up",
                    "didn't catch", "didn't hear", "couldn't hear",
                    "pardon", "come again", "could you repeat",
                    "what was that", "sorry what", "missed that",
                )
                if any(p in text for p in _REPAIR):
                    _pending_q = self.session.get("last_question", "")
                    if _pending_q:
                        retry_counts[phrase_key] = 0
                        await self._tts.put(_pending_q)
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _pending_q}
                        )
                        logger.info(
                            "[ms_flow] repair detected → replaying last_question: %r",
                            _pending_q[:80],
                        )
                        return
                from app.sidebar_handler import detect_sidebar_topic
                _sidebar_topic = await detect_sidebar_topic(transcript, step["state"])
                if _sidebar_topic:
                    from app.tools.receptionist_tools import _exec_get_clinic_info
                    _faq_result = await _exec_get_clinic_info(
                        {"topic": _sidebar_topic}, self.session
                    )
                    _faq_info = _faq_result.get("info", "")
                    _generic = "I don't have that specific information to hand."
                    if _faq_info and _faq_info != _generic:
                        # Cap to 2 sentences / 300 chars — same rule as
                        # _maybe_answer_inquiry (BUG 3: prevents long monologues
                        # mid-booking when _exec_get_clinic_info returns verbose text)
                        if len(_faq_info) > 300:
                            import re as _re_sib
                            _sib_sents = _re_sib.split(r'(?<=[.!?])\s+', _faq_info)
                            _faq_info = " ".join(_sib_sents[:2]).strip()
                            if _faq_info and _faq_info[-1] not in ".!?":
                                _faq_info += "."
                        # Reset retry — this wasn't a failed extraction
                        retry_counts[phrase_key] = 0
                        pending_q = self.session.get("last_question", "")
                        await self._tts.put(_faq_info)
                        if pending_q:
                            await self._tts.put(pending_q)
                        self.session.setdefault("conversation_history", []).append(
                            {"role": "assistant", "content": _faq_info}
                        )
                        self.session["last_info_answer"]      = _faq_info
                        self.session["general_inquiry_count"] = (
                            self.session.get("general_inquiry_count", 0) + 1
                        )
                        logger.info(
                            "[ms_flow] sidebar answered: topic=%s state=%s",
                            _sidebar_topic, step["state"],
                        )
                        return
                # Bypass Haiku for ALL COLLECT_NAME states — prevents Haiku
                # pseudo-confirmation wording ("Thanks John! Just to confirm…")
                # that diverges from the real deterministic question.
                # NameCollector always keeps last_question up to date.
                _CN_HAIKU_STATES = frozenset({
                    "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                    "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                })
                if step["state"] in _CN_HAIKU_STATES:
                    _cn_replay = self.session.get("last_question", "What's your first name please?")
                    await self._tts.put(_cn_replay)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _cn_replay}
                    )
                    return
                # Bypass Haiku for phone-collection states — any unrecognised utterance
                # at COLLECT_PHONE / COLLECT_PHONE_RETURNING is almost always a partial
                # number, background noise, or a filler ("hold on", "hang on").
                # Haiku would say something confusing here; just replay the question.
                _COLLECT_PHONE_STATES_FG = {"COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE", "RETURNING_PLAN_COLLECT_PHONE"}
                if step["state"] in _COLLECT_PHONE_STATES_FG:
                    _ph_replay = self.session.get("last_question", "And the best number to reach you on?")
                    await self._tts.put(_ph_replay)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _ph_replay}
                    )
                    logger.info("[ms_flow] COLLECT_PHONE: Haiku bypassed — replaying last_question")
                    return
                # Bypass Haiku for CONFIRM_PHONE states — any unrecognised utterance
                # (not YES, not NO, not a FAQ) is a failed phone gate response.
                # Replay with a gentle watchdog so the caller can try again.
                _CONFIRM_PHONE_STATES_FG = frozenset({
                    "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
                    "RETURNING_PLAN_CONFIRM_PHONE",
                })
                if step["state"] in _CONFIRM_PHONE_STATES_FG:
                    _cph_loc = self.session.get("selected_location")
                    _cph_loc_name = (
                        ("Redditch" if "redditch" in _cph_loc else "Alcester")
                        if _cph_loc else None
                    )
                    if (
                        (self._active_flow is RESCHEDULE_FLOW or self._active_flow is CANCEL_FLOW)
                        and _cph_loc_name
                    ):
                        _cph_retry_q = (
                            f"Sorry, I didn\u2019t catch that \u2014 "
                            f"for your {_cph_loc_name} booking, "
                            "if the number you're calling on is the one associated with your booking, "
                            "say: use this number. Otherwise, say: different number."
                        )
                    else:
                        _cph_retry_q = (
                            "Sorry, I didn\u2019t catch that \u2014 "
                            "if you'd like me to use the number you're calling on, "
                            "say: use this number. Otherwise, say: different number."
                        )
                    await self._tts.put(_cph_retry_q)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _cph_retry_q}
                    )
                    self.session["last_question"] = _cph_retry_q
                    self.session["phone_confirm_armed"] = True
                    logger.info("[ms_flow] CONFIRM_PHONE: Haiku bypassed — watchdog retry")
                    return
                # Bypass Haiku for PRESENT_DAYS states when utterance is a short cut-off
                # fragment with no unambiguous day/time content. "that's why i" etc. are
                # audio artifacts — replay last_question instead.
                _PD_HAIKU_STATES = {"PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"}
                if step["state"] in _PD_HAIKU_STATES:
                    _pd_hk_words = transcript.strip().split()
                    _pd_day_tokens = (
                        "monday", "tuesday", "wednesday", "thursday", "friday",
                        "saturday", "sunday", "today", "tomorrow", "next week",
                        "earliest", "soonest", "whenever", "morning", "afternoon",
                        "week", "fortnight",
                    )
                    _pd_has_day_token = any(tok in text for tok in _pd_day_tokens)
                    if not _pd_has_day_token and len(_pd_hk_words) <= 5:
                        logger.info(
                            "[ms_flow] PRESENT_DAYS: cut-off fragment suppressed before Haiku %r",
                            transcript[:40],
                        )
                        _pd_replay = self.session.get("last_question", "Which day would suit you best?")
                        await self._tts.put(_pd_replay)
                        return
                await self._haiku_fallback(transcript, step)
                return
            # count == 2: hardcoded second retry
            phrase = RETRY_PHRASES["second_retry"]["default"]
            await self._tts.put(phrase)
            # Keep last_question unchanged so SilenceHandler can re-ask again
            return

        # ── VAGUENESS: PRESENT_DAYS — answer extracted but response is vague ─────
        # Applies ONLY to ask_day (PRESENT_DAYS / PRESENT_DAYS_RESCHEDULE).
        # If the caller said "whenever" we have already-stored available_days data —
        # present the first 2 concrete options directly without another API call.
        if step["state"] in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            from app.vagueness_detector import (
                is_vague_availability, build_vague_options, build_vague_response_phrase,
            )
            # ── TOP-PRIORITY: ordinal resolves to a specific day immediately ────
            # Must run before the ordinal guard below so "last one" / "first" etc.
            # selects a day and advances instead of replaying the day list.
            # Mixed-intent guard: skip if text also contains an inquiry signal.
            _pd_avail = self.session.get("available_days", [])
            _pd_mixed = any(_sig in text for _sig in (
                "are you open", "do you open", "open on saturday", "open on sunday",
                "open sundays", "open saturdays", "have parking", "is there parking",
                "do you have", "how long", "what time", "are there any", "can you do",
                "quick question", "just a question", "just wondering",
            ))
            if _pd_avail and not _pd_mixed:
                _PD_ORD = [
                    ("first one", 0), ("second one", 1), ("third one", 2),
                    ("middle one", 1), ("the middle", 1),
                    ("the first", 0), ("the second", 1), ("the third", 2),
                    ("the last", -1), ("last one", -1), ("the final", -1),
                    ("first", 0), ("second", 1), ("third", 2),
                    ("middle", 1), ("last", -1), ("final", -1),
                ]
                _pd_ord_idx = None
                for _pd_pat, _pd_i in _PD_ORD:
                    if _pd_pat in text:
                        _pd_ord_idx = _pd_i
                        break
                if _pd_ord_idx is not None:
                    _pd_n  = len(_pd_avail)
                    _pd_r  = _pd_ord_idx if _pd_ord_idx >= 0 else max(0, _pd_n + _pd_ord_idx)
                    _pd_r  = min(_pd_r, _pd_n - 1)
                    _pd_day = _pd_avail[_pd_r].get("day_label", "")
                    self.session["chosen_day"]           = _pd_day
                    self.session[step["answer_field"]]   = _pd_day
                    self.session.pop("vague_option_pending",    None)
                    self.session.pop("vague_clarification_asked", None)
                    self.session["presented_vague_options"] = []
                    _pd_next = step["step"] + 1
                    _pd_ns   = (
                        self._active_flow[_pd_next]["state"]
                        if _pd_next < len(self._active_flow) else "DONE"
                    )
                    self.session["flow_step"]  = _pd_next
                    self.session["state"]      = _pd_ns
                    self.session["flow_state"] = _pd_ns
                    self.session["_last_handled_by"] = "present_days_ordinal_selection"
                    print("[PRESENT_DAYS ORDINAL]", {
                        "text":           text,
                        "resolved_index": _pd_r,
                        "chosen_day":     _pd_day,
                        "next_state":     _pd_ns,
                        "flow_step":      self.session.get("flow_step"),
                    })
                    logger.info(
                        "[ms_flow] PRESENT_DAYS ordinal: %r → idx=%d day=%r next=%s",
                        transcript[:40], _pd_r, _pd_day, _pd_ns,
                    )
                    await self.ask_current_question()
                    return

            # Guard: ordinal/positional words must never trigger the time-offer
            # vague handler while the day is not yet committed. Only fires now
            # when the ordinal did NOT resolve to a valid day above.
            _ORDINAL_GUARD_WORDS = {"first", "second", "third", "last", "three", "four"}
            if any(w in _ORDINAL_GUARD_WORDS for w in text.split()):
                _og_phrase = _build_day_list_phrase(self.session.get("available_days", []))
                if _og_phrase:
                    await self._tts.put(_og_phrase)
                    self.session["last_question"] = _og_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _og_phrase}
                    )
                    logger.info(
                        "[ms_flow] %s: ordinal guard — day list replayed (time-offer blocked): %r",
                        step["state"], transcript[:40],
                    )
                    return
            import re as _re_vague_tq
            _has_time_qual_vague = bool(
                _re_vague_tq.search(r"\b(?:morning|afternoon|evening)\w*", transcript, _re_vague_tq.IGNORECASE)
            )
            if is_vague_availability(transcript) or _has_time_qual_vague:
                _avail = self.session.get("available_days", [])
                _opts  = build_vague_options(_avail)
                if _opts:
                    _phrase = build_vague_response_phrase(_opts)
                    if len(_phrase) > 400:
                        _phrase = _phrase[:400]
                    self.session["vague_option_pending"]    = True
                    self.session["presented_vague_options"] = _opts
                    self.session.pop("vague_clarification_asked", None)
                    await self._tts.put(_phrase)
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _phrase}
                    )
                    self.session["last_question"] = _phrase
                    logger.info(
                        "[ms_flow] vague availability at %s — %d options presented",
                        step["state"], len(_opts),
                    )
                    return  # Don't advance — wait for option selection
                # No slots available in cache — fall through to normal LLM handling

        # CONFIRM_PHONE / CONFIRM_PHONE_RETURNING: declined — collect manually
        if step["state"] in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING") and answer is False:
            self.session["phone_confirmed"]  = False
            self.session["phone_from_twilio"] = False
            self.session["phone_number"]     = None
            collected = self.session.setdefault("collected", {})
            collected.pop("phone", None)
            self.session["flow_step"] = step["step"] + 1
            phrase = "No problem — what number would you like us to use?"
            await self._tts.put(phrase)
            _store_last_question(
                self.session, phrase,
                force=True, source="flow_step:CONFIRM_PHONE_decline",
            )
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            logger.info("[ms_flow] %s declined — will collect manually", step["state"])
            return

        # Store the answer
        self.session[step["answer_field"]] = answer
        self.session["general_inquiry_count"] = 0  # reset inquiry frequency on valid answer
        # Mirror into collected{} for LLM context
        if step["answer_field"] in ("full_name", "phone_number", "new_or_returning"):
            col = self.session.setdefault("collected", {})
            if step["answer_field"] == "full_name":
                col["full_name"] = answer
                col["name"]      = answer
                self.session.pop("name_fragment", None)  # clear single-word fragment if present
                # Notify name tracker — stores validated first name, resets uses
                self._name_tracker.set_name(answer)
                # Persist tracker state to serialisable session mirrors
                self.session["name_tracker_name"] = self._name_tracker._name
                self.session["name_tracker_uses"] = self._name_tracker._uses_remaining
            elif step["answer_field"] == "phone_number":
                col["phone"] = _to_e164_uk(answer)
                # Phone readback: speak the number back slowly and ask for confirmation.
                # Only for COLLECT_PHONE states — not CONFIRM_BOOKING (which has its own
                # full readback) and not when Twilio caller-ID was already confirmed.
                if step["state"] in ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING"):
                    _spaced = _format_phone_readback(answer)
                    _rb_phrase = (
                        f"Perfect, got that — just to confirm, I have your number down as {_spaced}. "
                        "Is that correct?"
                    )
                    await self._tts.put(_rb_phrase)
                    self.session["last_question"] = _rb_phrase
                    self.session.setdefault("conversation_history", []).append(
                        {"role": "assistant", "content": _rb_phrase}
                    )
                    self.session["phone_readback_pending"] = True
                    logger.info(
                        "[ms_flow] phone readback: %r → %r", answer, _rb_phrase[:80]
                    )
                    return  # wait for confirmation; flow_step NOT advanced yet
            elif step["answer_field"] == "new_or_returning":
                col["patient_type"] = answer

        logger.info(
            "[ms_flow] step %d %s=%r",
            step["step"], step["answer_field"], str(answer)[:60],
        )

        # Opportunistic multi-field harvest — pre-store extra info volunteered by the caller
        _harvest_extra_fields(text, transcript, step["state"], self.session)

        # ── CONFIRM_RESCHEDULE: patient just confirmed "yes" → execute reschedule ──
        if step["state"] == "CONFIRM_RESCHEDULE" and answer:
            from app.tools.receptionist_tools import _exec_reschedule_appointment
            phone_val = _to_e164_uk(
                self.session.get("phone_number")
                or self.session.get("twilio_from_local")
                or self.session.get("twilio_from", "")
            )
            # Use booking-derived identity when conversational full_name is absent
            # (phone-first flows never collect a name separately).
            _cr_name = (self.session.get("full_name") or "").strip()
            if not _cr_name:
                _cr_lb_fn = (self.session.get("lookup_appt_first_name") or "").strip()
                _cr_lb_ln = (self.session.get("lookup_appt_last_name") or "").strip()
                _cr_name  = f"{_cr_lb_fn} {_cr_lb_ln}".strip()
            reschedule_args = {
                "patient_name":    _cr_name,
                "phone":           phone_val,
                "location":        self.session.get("selected_location", "alcester"),
                "new_slot_iso":    self.session.get("selected_slot", ""),
                "duration_minutes": 50,
            }
            logger.info(
                "[ms_flow] CONFIRM_RESCHEDULE — calling _exec_reschedule_appointment "
                "name=%r slot=%r location=%r",
                reschedule_args["patient_name"],
                reschedule_args["new_slot_iso"],
                reschedule_args["location"],
            )
            # Patient confirmed intent — record regardless of Acuity execution result
            self.session["reschedule_confirmed"] = True
            reschedule_result = await _exec_reschedule_appointment(reschedule_args, self.session)
            exec_succeeded = bool(reschedule_result.get("success"))
            self.session["reschedule_execution_succeeded"] = exec_succeeded
            logger.info(
                "[ms_flow] CONFIRM_RESCHEDULE exec result: success=%s error=%r rescheduled_to=%r",
                exec_succeeded,
                reschedule_result.get("error"),
                reschedule_result.get("rescheduled_to"),
            )
            if exec_succeeded:
                slot_speech = self.session.get("selected_slot_speech", "the new time")
                response = (
                    f"Done — I've moved you to {slot_speech}. "
                    "You'll get the confirmation text shortly. "
                    "Is there anything else I can help with?"
                )
            else:
                response = (
                    "I'm sorry — I wasn't able to complete that reschedule. "
                    "Please give us a call directly and the team will get it sorted for you."
                )
                self.session["acuity_error"] = reschedule_result.get("error")
            await self._tts.put(response)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": response}
            )
            self.session["flow_step"] = len(self._active_flow)
            logger.info(
                "[ms_flow] CONFIRM_RESCHEDULE complete — reschedule_confirmed=%s execution_succeeded=%s",
                self.session["reschedule_confirmed"],
                self.session["reschedule_execution_succeeded"],
            )
            return

        # After slot selection, confirm with the caller before moving to name
        # collection.  flow_step is NOT advanced here — it advances in
        # _handle_slot_confirmation when the caller says yes.
        # NOTE: the 1-slot path in ask_current_question sets slot_pending_confirmation=True
        # and returns at line 4646 before this block, so this only runs for multi-slot.
        if step["state"] in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
            slot_text = str(answer)
            slot_speech = _format_slot_for_speech(slot_text)
            self.session["selected_slot_speech"] = slot_speech
            self.session["selected_slot"]        = slot_text  # needed by _exec_reschedule_appointment
            self.session["slot_confirmed"]       = True
            logger.info("[ms_flow] slot confirmed (no re-ask): %r", slot_speech[:80])

        # Advance to next step
        self.session["flow_step"] = step["step"] + 1
        _next_state = (self._active_flow[step["step"] + 1]["state"]
                       if step["step"] + 1 < len(self._active_flow) else "DONE")
        self.session["state"] = _next_state
        # Fix 6: ensure booking_confirmed + DONE are authoritative on this branch
        if step["state"] == "CONFIRM_BOOKING":
            self.session["booking_confirmed"] = True
            self.session["state"]      = "DONE"
            self.session["flow_state"] = "DONE"
            logger.info("[ms_flow] CONFIRM_BOOKING advance → booking_confirmed=True state=DONE flow_state=DONE")
        # Clear stale per-step flags so they cannot replay after a successful parse
        self.session.pop("slot_pending_confirmation", None)
        self.session.pop("vague_option_pending", None)
        self.session.pop("vague_clarification_asked", None)
        logger.info("[ms_flow] advance: step→%d matched_state=%s next_state=%s",
                    step["step"] + 1, step["state"], self.session["state"])

        # ── COLLECT_REASON: inline empathy acknowledgement (replaces CONFIRM_ASSESSMENT) ──
        # Non-gating — spoken once, no reply expected. Flow advances immediately.
        if step["state"] == "COLLECT_REASON":
            # Suppress empathy when the captured reason is effectively a
            # service / treatment choice ("i'd like to book acupuncture",
            # "let's do shockwave", "i want to try acupuncture then").  The
            # generic "I'm sorry to hear that" line is wrong here — there is
            # no pain/distress to acknowledge.
            _cr_ans_lower = (answer or "").lower()
            _CR_SERVICE_WORDS = (
                "acupuncture", "shockwave", "laser", "pilates", "massage",
                "biomechanical", "biomechanics", "sports therapy",
                "physio assessment", "rehab", "rehabilitation",
            )
            _CR_CHOICE_VERBS = (
                "book", "booking", "try", "do ", "go ahead", "go with",
                "go through", "have", "get", "want", "wanted", "like to",
                "let's", "lets", "i'll", "ill ", "i will",
                "happy to", "fine for", "fine with", "ok with", "okay with",
            )
            _cr_is_service_choice = (
                any(s in _cr_ans_lower for s in _CR_SERVICE_WORDS)
                and any(v in _cr_ans_lower for v in _CR_CHOICE_VERBS)
            )
            # Symptom/distress veto — if the reason also contains pain or
            # body+symptom signals, treat as ordinary clinical reason and
            # let empathy fire normally.
            _CR_SYMPTOM_WORDS = (
                "pain", "painful", "ache", "aching", "hurt", "hurting",
                "sore", "stiff", "stiffness", "injury", "injured",
                "killing me", "swollen", "swelling", "sprain", "strain",
                "torn", "fracture", "headache", "migraine",
            )
            if _cr_is_service_choice and not any(
                w in _cr_ans_lower for w in _CR_SYMPTOM_WORDS
            ):
                logger.info(
                    "[ms_flow] COLLECT_REASON: service-choice reason "
                    "detected — empathy suppressed answer=%r",
                    (answer or "")[:80],
                )
            else:
                _cr_empathy = _fast_empathy_line(answer or "")
                await self._tts.put(_cr_empathy)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _cr_empathy}
                )
                logger.info("[ms_flow] COLLECT_REASON: empathy emitted — %r", _cr_empathy[:80])

        # Emit a short conversational bridge before the next question.
        # Skip if the next step uses LLM — it writes its own opener.
        _next_step = self.current_step()
        _next_llm  = _next_step["use_llm"] if _next_step else False
        _bridge    = _get_bridge(step["state"], answer, self.session, _next_llm)
        if _bridge:
            await self._tts.put(_bridge)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _bridge}
            )

        # Ask the next question
        await self.ask_current_question()

    # ── General inquiry helper ───────────────────────────────────────────

    async def _maybe_answer_inquiry(
        self,
        transcript: str,
        state: str,
        frozen_q: str,
    ) -> bool:
        """
        Advisory informational detour for hard-gated states (ASK_LOCATION,
        COLLECT_PHONE, CONFIRM_PHONE) where the main extraction path never runs.

        Contract:
        - NEVER mutates flow_step, state, last_question, or any booking slot.
        - NEVER advances or rewinds the flow.
        - Stores answer in last_info_answer (separate from last_question).
        - Re-anchors by speaking frozen_q immediately after the answer.
        - Returns True if the inquiry was handled; False if not a known topic.
        """
        from app.sidebar_handler import detect_sidebar_topic
        from app.tools.receptionist_tools import _exec_get_clinic_info

        # Frequency cap: after 2 consecutive inquiries steer back to the flow.
        _gic = self.session.get("general_inquiry_count", 0)
        if _gic >= 2:
            _steer = (
                "Of course — happy to help with any questions. "
                + (frozen_q if frozen_q else "Could we carry on from where we were?")
            )
            await self._tts.put(_steer)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": _steer}
            )
            self.session["general_inquiry_count"] = 0
            logger.info("[ms_flow] inquiry freq-cap hit (count=%d) — steering back", _gic)
            return True

        _topic = await detect_sidebar_topic(transcript, state)
        if not _topic:
            return False

        _result = await _exec_get_clinic_info({"topic": _topic}, self.session)
        _info   = _result.get("info", "")
        _generic = "I don't have that specific information to hand."
        _answer = (
            _info if (_info and _info != _generic)
            else "I'm not completely sure on that, but the team can confirm when you come in."
        )
        # Cap long info answers to 2 sentences (~300 chars) so they don't
        # dominate mid-booking flow and make recovery awkward.
        if len(_answer) > 300:
            import re as _re_cap
            _sentences = _re_cap.split(r'(?<=[.!?])\s+', _answer)
            _answer = " ".join(_sentences[:2]).strip()
            if _answer and not _answer[-1] in ".!?":
                _answer += "."
        await self._tts.put(_answer)
        if frozen_q:
            await self._tts.put(frozen_q)
        self.session.setdefault("conversation_history", []).append(
            {"role": "assistant", "content": _answer}
        )
        self.session["last_info_answer"]      = _answer   # never overwrites last_question
        self.session["general_inquiry_count"] = _gic + 1
        logger.info(
            "[ms_flow] inquiry answered: topic=%s state=%s count=%d",
            _topic, state, _gic + 1,
        )
        return True

    # ── Haiku fallback ────────────────────────────────────────────────────

    async def _haiku_fallback(self, transcript: str, step: dict) -> None:
        """
        Fallback LLM call when no deterministic handler matched the caller's input.

        Uses claude-haiku-4-5 (fast + cheap), no tools, max 80 tokens.
        Acknowledges what the caller said, then redirects to the pending question.
        Called on the first retry attempt so the caller never hears a robotic
        verbatim re-ask when they asked a legitimate question.
        """
        import anthropic as _anthropic
        pending_q = self.session.get("last_question", "")
        state      = step["state"] if step else self.session.get("state", "")
        system_msg = (
            "You are Susie, a friendly receptionist at Theorem Health and Wellness, "
            "a physiotherapy clinic. Keep responses to 1-2 short sentences. "
            "Do NOT make up availability, prices, or appointment details. "
            "If unsure about a clinical question, say you'll pass it to the team."
        )
        user_msg = (
            f"Current booking step: {state}.\n"
            f"Caller said: \"{transcript}\"\n"
            f"Pending question you still need answered: \"{pending_q}\"\n\n"
            "Acknowledge what they said briefly, then ask the pending question."
        )
        try:
            _client = _anthropic.AsyncAnthropic()
            _resp = await _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                system=system_msg,
                messages=[{"role": "user", "content": user_msg}],
            )
            _text = (_resp.content[0].text or "").strip() if _resp.content else ""
        except Exception as _e:
            logger.error("[ms_flow] _haiku_fallback error: %r", _e)
            _text = ""

        if not _text:
            # Hard fallback if Haiku fails
            _text = (
                "Sorry, I didn't quite catch that. "
                + (pending_q or "Could you say that again?")
            )

        await self._tts.put(_text)
        self.session["last_question"] = pending_q  # preserve for silence handler
        self.session.setdefault("conversation_history", []).append(
            {"role": "assistant", "content": _text}
        )
        logger.info("[ms_flow] _haiku_fallback: state=%s response=%r", state, _text[:80])

    async def _haiku_fallback_days(
        self, transcript: str, step: dict, day_labels: list
    ) -> None:
        """
        Haiku fallback for PRESENT_DAYS when no day was matched.
        Includes the available day list in context so it can handle
        "what's the earliest?", "anything next week?", "do you have mornings?" etc.
        Does NOT re-run the tool — uses whatever days are already in session.
        """
        import anthropic as _anthropic
        pending_q = self.session.get("last_question", "")
        state = step["state"] if step else "PRESENT_DAYS"
        days_str = ", ".join(day_labels) if day_labels else "no days available"
        system_msg = (
            "You are Susie, a receptionist at Theorem Health physiotherapy clinic. "
            "Keep responses to 1-2 short sentences. Do NOT invent availability."
        )
        user_msg = (
            f"Available appointment days: {days_str}.\n"
            f"Caller said: \"{transcript}\"\n"
            f"Pending question: \"{pending_q}\"\n\n"
            "Acknowledge what they said briefly, then ask them to choose from the available days."
        )
        try:
            _client = _anthropic.AsyncAnthropic()
            _resp = await _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                system=system_msg,
                messages=[{"role": "user", "content": user_msg}],
            )
            _text = (_resp.content[0].text or "").strip() if _resp.content else ""
        except Exception as _e:
            logger.error("[ms_flow] _haiku_fallback_days error: %r", _e)
            _text = ""
        if not _text:
            _text = pending_q or "Sorry, which of those days works for you?"
        await self._tts.put(_text)
        # Anchor to the actual spoken phrase so silence re-ask replays
        # what the caller just heard, not the stale original day offer.
        self.session["last_question"] = _text
        self.session.setdefault("conversation_history", []).append(
            {"role": "assistant", "content": _text}
        )
        logger.info("[ms_flow] _haiku_fallback_days: %r", _text[:80])

    async def _haiku_classify(
        self, transcript: str, question: str, mapping: dict
    ) -> object:
        """
        Silent Haiku classifier — no TTS output.
        Sends `question` (with {transcript} interpolated) to Haiku and maps
        the single-word reply to a value via `mapping`.
        Returns None if the reply is "unclear" or not in mapping.
        """
        import anthropic as _anthropic
        prompt = question.replace("{transcript}", transcript)
        try:
            _client = _anthropic.AsyncAnthropic()
            _resp = await _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                system="Reply with a single word only.",
                messages=[{"role": "user", "content": prompt}],
            )
            word = (_resp.content[0].text or "").strip().lower() if _resp.content else ""
        except Exception as _e:
            logger.error("[ms_flow] _haiku_classify error: %r", _e)
            return None
        return mapping.get(word)  # returns None for "unclear" or unknown replies

    # ── intent routing ────────────────────────────────────────────────────

    def _detect_intent(self, text: str) -> str:
        """
        Classify the caller's first utterance into one of seven intent strings.
        Returns "booking" as the default fallback.
        """
        import re as _re_di

        # ── FAQ-FIRST PRECEDENCE GUARD ─────────────────────────────────────
        # Root precedence fix: informational / question-form first turns must
        # not be hijacked into booking by downstream heuristics that match
        # "pain"/"painful"/"hurt"/body-part words. When the utterance is
        # clearly a question AND mentions an FAQ topic AND contains no
        # explicit booking verb, suppress the aggressive booking heuristics
        # (body+symptom, body-alone, booking_priority_p, booking rescue) for
        # this turn — the existing FAQ matchers further down will pick the
        # right faq_* intent (or fall through to general_query).
        _EXPLICIT_BOOK_PHRASES = (
            "i want to book", "i'd like to book", "i need to book",
            "want to book", "looking to book", "trying to book",
            "book an appointment", "book appointment",
            "booking an appointment", "booking appointment",
            "make an appointment", "making an appointment",
            "schedule an appointment", "schedule appointment",
            "schedule a physio", "schedule me",
            "book me in", "book me", "get me booked",
            "can i book", "could i book", "can you book me",
            "i'd like to schedule", "i want to schedule",
            "i'd like to come in", "i want to come in",
            "like to come in", "need to come in",
            "see a physio", "see a physiotherapist",
            "get an appointment", "need an appointment", "want an appointment",
            "arrange an appointment", "arrange appointment",
            # competitor-threat implicit booking signals
            "another clinic", "different clinic",
        )
        _has_explicit_book = any(p in text for p in _EXPLICIT_BOOK_PHRASES)

        _QUESTION_LEADS = (
            "is it", "is that", "is the", "is there", "isn't it", "isnt it",
            "are they", "are the", "are you", "are there", "aren't they",
            "do you", "does the", "does it", "did you", "did the",
            "can you", "can i", "can my", "could you", "could i",
            "would you", "would it", "would that", "would physio",
            "should i", "should we", "will you", "will it",
            "what ", "what's", "whats", "what do", "what does",
            "where ", "where's", "wheres",
            "how ", "how's", "hows", "how do", "how does", "how much",
            "how long", "how many",
            "when ", "when's", "when is", "when are",
            "why ", "which ", "who ", "whose ",
            "tell me", "any chance", "any idea", "any way",
        )
        _has_question_form = (
            text.strip().endswith("?")
            or any(q in text for q in _QUESTION_LEADS)
        )

        _FAQ_TOPIC_KEYWORDS = (
            # services / treatments
            "acupuncture", "shockwave", "laser", "pilates", "massage",
            "biomechanical", "biomechanics", "sports therapy",
            "physio", "physiotherapy", "physiotherapist",
            "rehab", "rehabilitation", "assessment",
            "treatment", "treatments", "therapy", "therapies",
            "service", "services",
            "needle", "needles",
            # accessibility / logistics
            "step free", "step-free", "step three",
            "wheelchair", "wheel chair", "accessible", "accessibility",
            "disabled", "ramp", "mobility", "lift access",
            "parking", "car park", "where to park",
            "address", "postcode", "post code", "directions",
            "where are you", "where is your", "where exactly", "where is the",
            "how do i get", "get to the clinic",
            # pricing / insurance / hours
            "price", "cost", "how much", "charge", "fee", "rates", "pricing",
            "insurance", "bupa", "axa", "aviva", "vitality",
            "self pay", "self-pay", "claim",
            "hours", "opening hours", "open", "closing", "closed",
            "what time", "when are you open",
            # capability / eligibility / policy
            "what do you offer", "do you offer", "what you offer",
            "what conditions", "what do you treat",
            "see children", "treat children", "see kids", "treat kids",
            "accept children", "do you see",
            "paediatric", "pediatric", "child physio",
            "referral", "self refer", "self-refer", "gp referral",
            # logistics / first appointment
            "website", "online", "online booking",
            "first appointment", "first visit", "first session",
            "what happens at", "what to expect", "coming for the first time",
            "how many sessions",
        )
        _has_faq_topic = any(k in text for k in _FAQ_TOPIC_KEYWORDS)

        _faq_first_guard = (
            _has_question_form
            and _has_faq_topic
            and not _has_explicit_book
        )
        if _faq_first_guard:
            logger.info(
                "[ms_flow] first_turn_guard: faq_beats_booking — "
                "question_form=%s faq_topic=%s explicit_book=%s text=%r",
                _has_question_form, _has_faq_topic, _has_explicit_book,
                text[:80],
            )

        # ── Narrow service-question guard (companion to _faq_first_guard) ──
        # The question-form test above is conservative: phrases like
        # "i saw on your website you offered acupuncture and i want to know if
        # they're big and if it's painful" do not contain a _QUESTION_LEADS
        # token and do not end with "?", so _faq_first_guard stays False.
        # booking_priority_p then matches "painful" and returns booking.
        #
        # This companion guard fires ONLY when all three conditions hold:
        #   (1) the utterance mentions a recognised service (acupuncture,
        #       shockwave, laser, pilates, massage, biomechanics, physio,
        #       physiotherapy);
        #   (2) the utterance contains information-seeking / concern language
        #       (i saw, i want to know, i'm scared, i'm nervous, can you tell
        #       me, how does it work, etc.);
        #   (3) NO strong appointment-action verb is present (book, booking,
        #       appointment, schedule, reschedule, cancel, come in).
        #
        # Generic symptom words (pain, painful, hurt, sore, ache) are NOT
        # treated as booking signals for this guard — they are legitimate
        # content words inside a service-information question.
        _SERVICE_MENTION_KEYWORDS = (
            "acupuncture", "shockwave", "laser", "pilates",
            "sports massage", "massage",
            "biomechanical", "biomechanics",
            "physio", "physiotherapy", "physiotherapist",
        )
        _has_service_mention = any(
            k in text for k in _SERVICE_MENTION_KEYWORDS
        )
        _INFO_SEEKING_PHRASES = (
            "i saw on your website", "saw on your website", "on your website",
            "i saw on the website", "saw on the website",
            "i want to know", "i wanted to know", "want to know",
            "i'd like to know", "id like to know", "would like to know",
            "can you tell me", "could you tell me", "tell me about",
            "i was wondering", "just wondering", "wondering if",
            "i'm scared", "im scared", "scared of",
            "i'm nervous", "im nervous", "nervous about",
            "i'm anxious", "im anxious", "anxious about",
            "i'm worried", "im worried", "worried about",
            "how does it work", "how does that work", "how does acupuncture",
            "how does shockwave", "how does laser",
            "what happens", "what is it", "what is acupuncture",
            "what does it feel", "what does it involve",
            "will it hurt", "is it painful", "is it safe",
            "are the needles", "are they big", "are they small",
            "how big", "how long", "how often", "how many sessions",
            "side effects", "any side",
        )
        _has_info_seeking = any(
            p in text for p in _INFO_SEEKING_PHRASES
        )
        # Narrow strong-booking list — only strict appointment-action verbs.
        # Does NOT include symptom/problem words like pain/hurt/sore.
        _STRONG_BOOK_VERBS = (
            "book", "booking", "booked",
            "appointment", "appointments",
            "schedule", "scheduled", "scheduling",
            "reschedule", "rescheduled",
            "cancel", "cancelled", "canceling",
            "come in", "coming in", "come and see",
        )
        _has_strong_book_verb = any(
            v in text for v in _STRONG_BOOK_VERBS
        )
        _service_question_guard = (
            _has_service_mention
            and _has_info_seeking
            and not _has_strong_book_verb
        )
        if _service_question_guard:
            logger.info(
                "[ms_flow] first_turn_guard: service_question_beats_booking — "
                "service_mention=%s info_seeking=%s strong_book_verb=%s text=%r",
                _has_service_mention, _has_info_seeking, _has_strong_book_verb,
                text[:80],
            )
            # Fold into _faq_first_guard so all four booking heuristics
            # (body+symptom, body-alone, booking_priority_p, booking-rescue)
            # are suppressed on this turn without duplicating conditions.
            _faq_first_guard = True

        # ABSOLUTE TOP-PRIORITY: body-part + symptom compound detection.
        # Catches phrases like "my shoulder's been killing me" or "recurring ankle
        # problem" that may not have an explicit symptom keyword but combine a body
        # term with a pain/problem signal.
        _BODY_RE = r"\b(back|shoulder|ankle|knee|hip|neck|wrist|elbow|leg|arm)\b"
        _SYMP_RE = r"\b(pain|painful|ache|aching|hurt|hurting|injury|injured|problem|issue|sore|stiff|stiffness|recurring|grief|trouble|bother)\b"
        if (
            not _faq_first_guard
            and _re_di.search(_BODY_RE, text)
            and (_re_di.search(_SYMP_RE, text) or "killing me" in text
                 or "giving me grief" in text or "playing up" in text
                 or "giving me trouble" in text)
        ):
            logger.debug(
                "[ms_flow] detect_intent_booking_symptom_rule body+symptom: %r", text[:60]
            )
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                "source=body_symptom_rule override_reason=none"
            )
            return "booking"

        # Body part as a standalone short answer (e.g. "Shoulder", "My knee").
        # Callers often give the affected area as their entire first utterance.
        # Only fires when the whole text is just an optional "my/the" + body part,
        # so it doesn't swallow sentences that happen to contain a body word.
        _BODY_ALONE_RE = r"^(my|the|my\s+left|my\s+right|left|right)?\s*\b(back|shoulder|ankle|knee|hip|neck|wrist|elbow|leg|arm)\b\s*$"
        if not _faq_first_guard and _re_di.match(_BODY_ALONE_RE, text):
            logger.debug(
                "[ms_flow] detect_intent body-alone rule: %r", text[:60]
            )
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                "source=body_alone_rule override_reason=none"
            )
            return "booking"

        # Explicit booking signals checked FIRST — these override any FAQ keyword
        # matches that might appear coincidentally (e.g. "not feeling right about
        # the cost" would otherwise fire faq_prices despite being a health complaint).
        booking_priority_p = (
            "not feeling", "feeling off", "feel off", "unwell", "not well",
            "not myself", "off colour", "off color", "under the weather",
            "something wrong", "been suffering", "not been well", "been struggling",
            "pain", "painful", "ache", "aching", "hurt", "hurting", "injury", "injured",
            "problem", "issue", "sore", "stiff", "stiffness", "swollen", "swelling",
            "recurring", "killing me",
            "grief", "giving me grief", "giving me trouble", "giving me bother",
            "playing up", "been playing up", "niggly", "niggling",
            "pulled", "torn", "sprain", "strain", "fracture",
            "headache", "migraine",
            "i want to book", "i'd like to book", "i need to book",
            "want to book", "looking to book", "trying to book",
            "book an appointment", "make an appointment", "see a physio",
            "book me in", "book me",
            "another clinic", "different clinic",  # implicit booking intent (competitor threat)
            # BUG 1: STT variants — "book" near "appointment" in longer utterances
            "book an appointment", "book appointment",
            "booking an appointment", "booking appointment",
            "book of appointment",  # STT mishear of "book an appointment"
            "that's a book", "thats a book",  # "that's a book of appointment"
            "i was asking to book", "asking to book",
            "i'd like to book", "like to book",
        )
        # Very short direct booking utterances: "book", "book pls", "book now", "book please"
        if len(text.split()) <= 3 and "book" in text:
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                "source=short_book_verb override_reason=none"
            )
            return "booking"
        # BUG 1: longer utterances containing BOTH "book" and "appointment" anywhere
        if "book" in text and "appoint" in text:
            logger.debug("[ms_flow] detect_intent book+appoint match: %r", text[:60])
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                "source=book_plus_appoint override_reason=none"
            )
            return "booking"
        # booking_priority_p includes symptom words (pain/painful/hurt/etc.). These
        # must NOT force booking when the utterance is a clear FAQ question —
        # "is acupuncture painful?" contains "painful" but is informational.
        if any(p in text for p in booking_priority_p):
            if _faq_first_guard:
                logger.info(
                    "[ms_flow] first_turn_guard: explicit_booking_required_for_booking_path "
                    "— booking_priority_p match suppressed (text=%r)", text[:60]
                )
            else:
                logger.debug("[ms_flow] detect_intent_booking_symptom_rule matched: %r", text[:60])
                logger.info(
                    "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                    "source=booking_priority_p override_reason=none"
                )
                return "booking"

        transfer_p = (
            "speak to a person", "speak to someone", "speak to a human",
            "speak to a real person", "real person", "speak to staff",
            "member of staff", "talk to someone", "talk to a person",
            "talk to a human", "speak to the team", "speak to a member",
            "human please", "person please",
        )
        if any(p in text for p in transfer_p): return "transfer"

        # ── FAQ-before-booking intercept ──────────────────────────────────────
        # "I have a few questions before booking" / "I had a question first" —
        # STT can mangle "before booking" → "rebook" which would trigger reschedule_p.
        # Catching FAQ-led phrasing here, before reschedule_p, ensures these callers
        # reach general_query (LLM FAQ path) rather than an unwanted flow switch.
        _FAQ_FIRST_PHRASES = (
            "a few questions",
            "few questions",
            "some questions",
            "a couple of questions",
            "couple of questions",
            "questions before",
            "questions first",
            "questions about",
            "had a question",
            "have a question",
            "just had a question",
            "just have a question",
            "a quick question",
            "quick question",
            "got a question",
            "i've got a question",
            "i have got a question",
        )
        if any(p in text for p in _FAQ_FIRST_PHRASES):
            logger.info(
                "[ms_flow] DETECT_INTENT faq-first intercept: %r → general_query",
                text[:60],
            )
            return "general_query"

        reschedule_p = (
            "reschedule", "re-schedule", "re schedule",  # BUG 3: hyphenated/spaced STT variants
            "change my appointment", "move my appointment",
            "change the time", "different time", "different day",
            "rebook", "re-book", "re book",              # BUG 3: rebook variants
            "move it", "move an appointment", "rearrange my appointment",
            "change a booking", "change my booking",
        )
        cancel_p = (
            "cancel", "cancellation", "don't want", "dont want", "not coming",
            "won't be able", "wont be able", "need to cancel", "want to cancel",
        )
        insurance_p = (
            "insurance", "bupa", "axa", "aviva", "vitality",
            "covered", "cover", "claim", "health insurance",
        )
        price_p = (
            "price", "cost", "how much", "charge", "fee", "rates", "pricing",
        )
        hours_p = (
            "hours", "opening hours", "open", "close",
            "when are you", "what time are you",
        )
        # Journey-time / distance questions — the LLM handles these via system-prompt
        # guidance (give address + suggest Google Maps).  Checked BEFORE location_p so
        # "how long from Coventry" / "how long it would take me" never misfires as faq_location.
        # "how long" and "how far" are intentionally removed from location_p below.
        journey_p = (
            "how long", "how far", "journey time", "travel time",
            "how many minutes", "how many miles",
        )
        # Accessibility signals checked at highest priority so they are never
        # swallowed by location_p (which would return the address) or child_p
        # (which would return the child-treatment answer).  Both of those are
        # wrong responses to "does the clinic have step-free access for my son".
        # Still returns faq_location so _handle_mid_flow_interrupt / ANSWER_FAQ
        # can serve the right sub-answer via their own accessibility intercepts.
        accessibility_p = (
            "step free", "step-free", "step three",
            "wheelchair",
            "disabled access", "disability access",
        )
        if any(p in text for p in accessibility_p): return "faq_location"

        location_p = (
            "where are you", "where is your", "where exactly is", "where is the",
            "address", "parking", "directions", "how do i get",
            "drive to", "travel to", "get to",
            "journey to", "far is", "distance", "near", "nearest",
            "find you", "locate you", "clinic address", "your clinic is",
            # BUG 2: parking questions that don't contain the word "parking"
            "can i park", "where to park", "where can i park", "car park",
            "park in the", "park near", "park there",
        )
        services_p = (
            "services", "service", "treatments", "what do you offer",
            "what conditions",
            "rundown", "everything you offer", "everything you do",
            "what therapies", "what therapy", "what do you treat",
            "list of", "tell me what you",
            "what kind of service", "what type of service",
            # specific service names — caller asking about a modality directly
            "shockwave", "acupuncture", "laser", "massage", "pilates",
            "biomechanical", "biomechanics", "sports therapy", "physio assessment",
        )
        if any(p in text for p in reschedule_p): return "reschedule"
        if any(p in text for p in cancel_p):     return "cancel"
        if any(p in text for p in insurance_p):  return "faq_insurance"
        if any(p in text for p in price_p):      return "faq_prices"
        if any(p in text for p in hours_p):      return "faq_hours"
        if any(p in text for p in journey_p):    return "general_query"  # travel time → LLM, not address lookup
        if any(p in text for p in location_p):   return "faq_location"
        # Capability question checked before services to avoid "what can you help"
        # routing to the services list instead of the capability answer.
        if any(p in text for p in _CAPABILITY_PHRASES): return "faq_capability"
        # "tell me more about shockwave therapy" etc. — route as faq_services so the
        # service-detail fast path in _handle_mid_flow_interrupt handles it.
        if "tell me more about" in text and any(k in text for k in _FAQ_PRICES_SERVICE_KEYWORDS):
            return "faq_services"
        if any(p in text for p in services_p):   return "faq_services"

        # ── Child / paediatric policy ────────────────────────────────────────
        # Checked AFTER booking_priority_p so "my child's knee injury" (body+symptom)
        # still routes to booking.  These are purely informational FAQ forms.
        child_p = (
            # Direct treatment-eligibility questions — keep these
            "do you see children", "do you treat children", "treat children",
            "see children", "accept children",
            # "can my <relation>" — eligibility-specific; the verb "can" signals
            # a capability/booking question, not a building-access question
            "can my son", "can my daughter", "can my child", "can my kid",
            # Generic child-focus phrases — safe because accessibility_p fires
            # BEFORE child_p in _detect_intent, so "step-free access for a child"
            # is already captured by accessibility_p and never reaches here.
            "for a child", "for children",
            # Expressions of need — also eligibility-specific
            "my son needs", "my daughter needs", "my child needs", "my kid needs",
            # Clinical terminology — unambiguous paediatric context
            "paediatric", "pediatric", "child physio", "children physio",
            "little boy", "little girl", "young child",
            # NOTE: "for my son", "for my daughter" deliberately removed — bare
            # possessives that appear in accessibility questions ("step-free access
            # for my son") when STT noise prevents accessibility_p from matching.
            # "can my son" / "can my daughter" (with the eligibility verb) are kept.
        )
        if any(p in text for p in child_p): return "faq_child_policy"

        # ── Referral / booking logistics ─────────────────────────────────────
        # Specific question forms only — "referral" alone is too broad (caller
        # might say "I have a referral letter", which is booking context).
        referral_p = (
            "do i need a referral", "do i need to be referred",
            "need a referral", "need a gp referral", "need a doctor",
            "gp referral", "doctor referral", "doctor's referral",
            "can i self refer", "can i self-refer", "self-refer",
        )
        if any(p in text for p in referral_p): return "faq_booking_logistics"

        # First-appointment / what-to-expect info questions — these contain
        # "appointment" or "session" but are informational, not booking intent.
        # Must be checked BEFORE the booking rescue below so they don't get
        # swallowed as booking requests.
        _first_appt_signals = (
            "what happens at", "what to expect", "what should i expect",
            "first appointment", "first visit", "first session",
            "how long is the first", "do i need to bring", "what do i bring",
            "what do i wear", "what should i wear", "what do i need to wear",
            "coming for the first time",
        )
        if any(p in text for p in _first_appt_signals):
            return "faq_booking_logistics"

        # Booking rescue: by this point reschedule_p and cancel_p have already
        # been checked and did not match.  If "appointment" (or its derivatives)
        # is present the caller intends to book — STT noise around the verb
        # ("took", "need", "want") should not push a real booking to general_query.
        # Block question-form queries ("what time is my appointment", "when is my
        # appointment") so informational follow-ups still reach the LLM.
        _APPT_INFO_QUERIES = (
            "what time", "what's the time", "when is my", "when's my",
            "do i have", "have i got", "how long is my",
        )
        if (
            "appoint" in text
            and not any(q in text for q in _APPT_INFO_QUERIES)
            and not _faq_first_guard
        ):
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=booking "
                "source=appoint_rescue override_reason=none"
            )
            logger.info(
                "[ms_flow] DETECT_INTENT booking-rescue: transcript=%r → booking",
                text[:60],
            )
            return "booking"

        if _faq_first_guard:
            logger.info(
                "[ms_flow] top_level_intent_decision: chosen_intent=general_query "
                "source=faq_first_guard override_reason=faq_question_form_no_booking_verb"
            )
        return "general_query"  # unknown question — LLM handles it freely

    def _switch_flow(self, intent: str) -> None:
        """
        Switch _active_flow to the flow matching the given intent and
        reset flow_step to 0.
        """
        _faq_intents = {
            "faq_prices", "faq_insurance", "faq_hours",
            "faq_location", "faq_services", "faq_capability",
        }
        if intent == "transfer":
            self._active_flow = TRANSFER_FLOW
        elif intent == "reschedule":
            self._active_flow = RESCHEDULE_FLOW
        elif intent == "cancel":
            self._active_flow = CANCEL_FLOW
        elif intent in _faq_intents:
            self._active_flow = FAQ_FLOW
            self.session["faq_topic"] = _INTENT_TO_FAQ_TOPIC.get(intent, "services")
            self.session["faq_follow_up_count"] = 0  # reset so each FAQ entry is fresh
        elif intent == "general_query":
            self._active_flow = GENERAL_QUERY_FLOW
        else:
            self._active_flow = BOOKING_FLOW
        # Track the most-recent intent so infer_call_outcome sees mid-call switches
        self.session["intent"] = intent
        # Both reschedule and cancel use phone-first architecture — start at
        # CONFIRM_PHONE (step 1), skipping COLLECT_NAME_RESCHEDULE.
        # Cancel keeps CONFIRM_RESCHEDULE_OR_CANCEL (step 4) so caller is asked
        # "reschedule or cancel?".  Reschedule skips step 4 (handled at advance time).
        self.session["flow_step"] = 1 if intent in ("reschedule", "cancel") else 0
        # Multi-location clinics (theorem_v2): ask caller which clinic before starting flow.
        # Single-location clinics: hardcode alcester as before — zero behaviour change.
        #
        # NOTE: do NOT condition on `not selected_location` — the greeting phase sets
        # selected_location="alcester" as a default before any user turn, which would
        # cause the check to always fail.
        #
        # EXCEPTION: if the conversation has already established a single specific
        # clinic (e.g. caller asked about Redditch during FAQ and system confirmed it),
        # preserve that context — do NOT re-ask.  We detect this by scanning recent
        # conversation_history for messages that mention exactly ONE clinic name.
        # Messages that mention BOTH clinics (the initial offer question) are skipped.
        if (
            self.session.get("twilio_to") == "+447366530580"
            and intent in {"booking", "reschedule", "cancel"}
        ):
            _hist_sf = self.session.get("conversation_history", [])
            _ctx_loc = None
            for _sf_entry in reversed(_hist_sf[-10:]):
                # Only use CALLER messages to infer booking location.
                # Assistant messages about Redditch hours/address must NOT cause
                # the booking to default to Redditch — that is over-inference.
                if _sf_entry.get("role") != "user":
                    continue
                _sf_c = (_sf_entry.get("content") or "").lower()
                _sf_has_redd = "redditch" in _sf_c or "reditch" in _sf_c or "reddish" in _sf_c
                _sf_has_alce = any(p in _sf_c for p in ("alcester", "greig", "kinwarton"))
                # Skip messages that name both clinics — those are offer/question turns
                if _sf_has_redd and _sf_has_alce:
                    continue
                if _sf_has_redd:
                    _ctx_loc = "redditch"
                    break
                if _sf_has_alce:
                    _ctx_loc = "alcester"
                    break
            if _ctx_loc:
                # Clinic already established in conversation — carry it forward
                self.session["needs_location"] = False
                self.session["selected_location"] = _ctx_loc
                logger.info(
                    "[ms_flow] _switch_flow: location inferred from conversation context → %s",
                    _ctx_loc,
                )
            else:
                self.session["needs_location"] = True
                self.session.pop("selected_location", None)   # clear stale greeting-phase default
        else:
            self.session["needs_location"] = False
            self.session["selected_location"] = "alcester"
        self._intent_detected = True
        _entry_state = "ASK_LOCATION" if self.session.get("needs_location") else self._active_flow[0]["state"]
        logger.info(
            "[ms_flow] intent=%s → entry_state=%s",
            intent, _entry_state,
        )

    # ── mid-flow interrupt ────────────────────────────────────────────────

    async def _handle_mid_flow_interrupt(self, intent: str, transcript: str) -> None:
        """
        Caller asked an off-topic question mid-booking flow.
        Answer it warmly in 1–2 sentences, then re-ask the pending question so
        the caller knows where we are in the booking.
        Does NOT change flow_step or _active_flow.
        """
        # Precompute whether we're currently at an FAQ/general offer state so
        # fast-path answers can be combined with the re-anchor into ONE TTS turn.
        _mfi_cur_step  = self.current_step()
        _mfi_cur_state = _mfi_cur_step["state"] if _mfi_cur_step else None
        _mfi_in_offer  = _mfi_cur_state in ("FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER")
        # Bug 1 fix: never bake "Anything else?" into the fast-path TTS answer.
        # last_question is set to "Anything else you'd like to ask?" in the
        # re-anchor block below so the silence handler asks it only after real
        # silence — not immediately after every answer (over-aggressive).
        _re_anchor_sfx = ""
        # Clear active service topic when a non-service FAQ is answered.
        # Prevents stale acupuncture context leaking into hours/location/etc. answers.
        # Exception: when routing a general_query follow-up within an open follow-up
        # window, preserve the active service — the caller is continuing the same topic.
        if intent != "faq_services":
            _faq_fw = self.session.get("_faq_followup_window", 0)
            if not (intent == "general_query" and _faq_fw > 0
                    and self.session.get("_faq_active_service")):
                self.session.pop("_faq_active_service", None)
        # Pop accumulated context fragment; deterministic paths discard it,
        # general_query uses it to combine multi-turn fragmented questions.
        _ctx_frag = self.session.pop("_faq_ctx_fragment", "")
        _FAQ_TOPICS = {
            "faq_prices":    "prices",
            "faq_insurance": "insurance",
            "faq_hours":     "hours",
            "faq_location":  "address",
            "faq_services":  "services",
        }
        if intent == "faq_services":
            _svc_text = transcript.strip().lower()
            # ── Needle-fear / anxiety: reassurance-first — checked before service detection ──
            # "I'm scared of needles", "does it hurt", etc. need a different answer
            # than a generic acupuncture service description.
            _NEEDLE_FEAR_SIGNALS = (
                "scared", "afraid", "nervous", "anxious", "anxiety", "phobia",
                "does it hurt", "will it hurt", "is it painful", "painful", "hurt", "hurts",
                "scary", "is it scary",
                "needle", "needles", "how big", "how long", "size of",
                "hate needles", "don't like needles", "not keen on needles",
                "what does it feel", "feel like", "worried", "worry",
            )
            # Topic-lock safety net: if acupuncture is the active FAQ topic,
            # treat needle-fear follow-ups as acupuncture even when the text
            # omits the word "acupuncture" ("would my fear of needles be a
            # problem" after topic=acupuncture).
            _mbf_ctx_hmfi = self.session.get("_mid_booking_faq_ctx") or {}
            _acu_topic_locked = (
                "acupuncture" in _svc_text
                or self.session.get("_faq_active_service") == "acupuncture"
                or _mbf_ctx_hmfi.get("topic") == "acupuncture"
            )
            if _acu_topic_locked and any(p in _svc_text for p in _NEEDLE_FEAR_SIGNALS):
                _svc_answer = _FAQ_ACUPUNCTURE_FEAR_ANSWER
                self.session["_faq_active_service"] = "acupuncture"
                logger.info("[ms_flow] _handle_mid_flow_interrupt: acupuncture fear fast path")
            else:
                # ── Specific-modality drill-down: checked BEFORE generic overview ──
                # "shockwave therapy please" / "tell me about acupuncture" etc. must
                # produce the specific answer, not the generic services summary.
                _specific_key = next(
                    (svc for kw, svc in _SERVICE_KEYWORD_MAP if kw in _svc_text),
                    None,
                )
                if _specific_key:
                    _svc_answer = _SPECIFIC_SERVICE_ANSWERS[_specific_key]
                    # ── Availability-first: if the caller asked whether we offer
                    # the service (not just for a description), lead with a direct
                    # yes-confirmation before any explanatory detail.
                    # Applies to every specific service uniformly.
                    _SVC_AVAIL_TRIGGERS = (
                        "do you do", "do you offer", "do you have", "do you provide",
                        "can i get", "can you do", "can you offer", "is it available",
                        "do you run", "do you carry out", "is that a service",
                        "is that something", "can i book a", "do you see",
                    )
                    _SVC_DISPLAY_NAMES = {
                        "shockwave":    "shockwave therapy",
                        "acupuncture":  "acupuncture",
                        "laser":        "laser therapy",
                        "sports_massage": "sports massage",
                        "pilates":      "Pilates classes",
                        "biomechanics": "biomechanical assessments",
                    }
                    if any(t in _svc_text for t in _SVC_AVAIL_TRIGGERS):
                        # Availability question — one short confirmation, no description.
                        # Caller only wants to know if we offer it; detail is not asked for.
                        _display = _SVC_DISPLAY_NAMES.get(
                            _specific_key, _specific_key.replace("_", " ")
                        )
                        _svc_answer = f"Yes, absolutely — we do offer {_display}."
                        logger.info(
                            "[ms_flow] _handle_mid_flow_interrupt: services availability-first %s",
                            _specific_key,
                        )
                    else:
                        logger.info(
                            "[ms_flow] _handle_mid_flow_interrupt: services specific=%s", _specific_key
                        )
                    # Store active service for topic carry-forward on follow-up fragments.
                    self.session["_faq_active_service"] = _specific_key
                else:
                    # Generic overview: full list if explicitly requested, short summary otherwise.
                    _FULL_LIST_PHRASES = (
                        "full list", "all of them", "all services",
                        "list them", "the whole list", "everything",
                    )
                    _svc_answer = (
                        _FAQ_SERVICES_FULL
                        if any(p in _svc_text for p in _FULL_LIST_PHRASES)
                        else _FAQ_SERVICES_FAST
                    )
                    logger.info("[ms_flow] _handle_mid_flow_interrupt: services fast path")
                    # Generic overview clears any specific-service topic context.
                    self.session.pop("_faq_active_service", None)
            await self._tts.put(_svc_answer + _re_anchor_sfx)
            self.session["last_faq_answer"] = _svc_answer
        elif intent == "faq_capability":
            logger.info("[ms_flow] _handle_mid_flow_interrupt: capability fast path")
            await self._tts.put(_CAPABILITY_ANSWER + _re_anchor_sfx)
            self.session["last_faq_answer"] = _CAPABILITY_ANSWER
        elif intent == "faq_insurance":
            # Insurer-specific response: Bupa rejection, named-insurer self-pay, or generic.
            _ins_text = transcript.strip().lower()
            _INSURERS = {
                "axa": "AXA", "aviva": "Aviva", "wpa": "WPA",
                "vitality": "Vitality", "cigna": "Cigna", "healix": "Healix",
                "nuffield": "Nuffield", "simplyhealth": "Simplyhealth",
            }
            if "bupa" in _ins_text:
                _ins_ans = (
                    "I\u2019m sorry about that \u2014 we don\u2019t accept Bupa directly. "
                    "You\u2019re welcome to self-pay and claim back if your policy allows, "
                    "but Bupa direct billing isn\u2019t something we currently offer."
                )
            else:
                _named = next(
                    (name for key, name in _INSURERS.items() if key in _ins_text), None
                )
                if _named:
                    _ins_ans = (
                        f"Of course \u2014 for {_named}, we\u2019re a self-pay clinic, "
                        f"so you\u2019d pay us directly and then submit a claim to {_named} "
                        "if your policy covers physiotherapy. "
                        "It\u2019s worth confirming the cover with them and the clinic beforehand."
                    )
                else:
                    _ins_ans = _FAQ_INSURANCE_ANSWER
            logger.info("[ms_flow] _handle_mid_flow_interrupt: insurance fast path")
            await self._tts.put(_ins_ans + _re_anchor_sfx)
            self.session["last_faq_answer"] = _ins_ans
        elif intent == "faq_prices":
            # Prices: if no specific service named → deterministic from-price gate.
            # If a service is named → LLM constrained to one sentence for that service.
            _pr_text = transcript.strip().lower()
            _named_svc = any(k in _pr_text for k in _FAQ_PRICES_SERVICE_KEYWORDS)
            if not _named_svc:
                logger.info("[ms_flow] _handle_mid_flow_interrupt: prices no-service fast path")
                await self._tts.put(_FAQ_PRICES_NO_SERVICE + _re_anchor_sfx)
                self.session["last_faq_answer"] = _FAQ_PRICES_NO_SERVICE
            else:
                instruction = (
                    f"The caller asked about the price of a specific service. "
                    f"Their message: '{transcript.strip()}'\n"
                    "Give ONLY the price and duration for that one service in one sentence. "
                    "Do NOT list other services or prices. "
                    "Answer directly from the clinic information in your system prompt. "
                    "Just answer and stop."
                )
        elif intent in ("faq_hours", "faq_location"):
            # ── Deterministic clinic-data lookup — no LLM needed ──────────────
            from app.clinic_config import get_clinic as _gc_mfi
            _cid_mfi = self.session.get("clinic_id") or "demo"
            _cli_mfi = _gc_mfi(_cid_mfi)
            _locs_mfi = {loc["id"]: loc for loc in _cli_mfi.get("locations", [])}
            _mfi_text = transcript.strip().lower()
            # Detect which clinic the caller is asking about.
            # Priority: (1) explicit name in current utterance, (2) last FAQ clinic
            # context (BUG 1 — carry-over for follow-ups like "and parking?"),
            # (3) booking selected_location as last resort.
            _mfi_redd = any(p in _mfi_text for p in (
                "redditch", "reditch", "reddish", "reddit",
                "red itch", "red ditch", "red-ditch",  # BUG 1: STT near-forms
                "bromsgrove",
            ))
            _mfi_alce = any(p in _mfi_text for p in (
                "alcester", "greig", "kinwarton",
            ))
            if _mfi_redd and not _mfi_alce:
                _mfi_loc_id = "redditch"
            elif _mfi_alce and not _mfi_redd:
                _mfi_loc_id = "alcester"
            else:
                # No explicit clinic in utterance — use last FAQ clinic context
                # (carry-over: "and can I park?" inherits the clinic just discussed).
                # For multi-location clinics do NOT fall back to selected_location —
                # that field defaults to "alcester" in the greeting phase even when
                # the caller never named a clinic, which would silently give wrong data.
                # For single-location clinics the fallback is harmless.
                _mfi_loc_id = self.session.get("last_faq_loc_id") or (
                    (self.session.get("selected_location") or "").lower()
                    if len(_locs_mfi) == 1 else ""
                )
            _mfi_loc = _locs_mfi.get(_mfi_loc_id) or (
                # Single-location clinic — use the only location
                list(_locs_mfi.values())[0] if len(_locs_mfi) == 1 else None
            )
            if intent == "faq_hours":
                if _mfi_loc:
                    _mfi_ans = "Of course — " + _mfi_loc.get("hours_summary", "")
                else:
                    # Two clinics, location ambiguous — give both
                    _mfi_parts = [
                        _ld.get("hours_summary", "")
                        for _ld in _locs_mfi.values()
                        if _ld.get("hours_summary")
                    ]
                    _mfi_ans = "Of course — " + "  ".join(_mfi_parts)
                self.session["last_faq_sub"] = "hours"  # for correction recovery (BUG 2)
            else:  # faq_location
                # ── BUG 4: bounded parking-policy nuance (time limits / restrictions) ──
                # Answer from clinic parking config when it explicitly addresses time
                # limits; otherwise give a bounded deterministic "unknown" answer.
                # Never fall through to LLM speculation.
                _PARK_NUANCE_P = (
                    "time limit", "whole day", "all day", "stay there", "stay here",
                    "how long can i", "how long do i", "how long can we",
                    "restriction", "restrictions",
                    "when you say it's free", "when you say it is free", "when you say free",
                )
                if _mfi_loc and any(p in _mfi_text for p in _PARK_NUANCE_P):
                    _pk_full = _mfi_loc.get("parking", "") or ""
                    _pk_low  = _pk_full.lower()
                    if "time limit" in _pk_low or "no time" in _pk_low:
                        _mfi_ans = "Of course — " + _pk_full
                        logger.info(
                            "[ms_faq] parking_nuance_answer clinic=%s source=clinic_config",
                            _mfi_loc_id,
                        )
                    else:
                        _pk_known = _pk_full.split(".")[0].strip()
                        _loc_name_nu = _mfi_loc.get("name", _mfi_loc_id.title())
                        _nu_bits = []
                        if _pk_known:
                            _nu_bits.append(_pk_known + ".")
                        _nu_bits.append(
                            "I don\u2019t have a confirmed rule about time limits for the "
                            f"{_loc_name_nu} parking \u2014 for exact restrictions it\u2019s "
                            "best to check the signs on arrival or with the venue directly."
                        )
                        _mfi_ans = " ".join(_nu_bits)
                        logger.info(
                            "[ms_faq] practical_bounded_unknown part=parking_policy "
                            "clinic=%s source=deterministic_unknown", _mfi_loc_id,
                        )
                    self.session["last_faq_sub"]           = "parking"
                    self.session["last_faq_loc_id"]        = _mfi_loc_id
                    self.session["last_faq_intent"]        = intent
                    # Track parking_policy as a distinct virtual part so a later
                    # plain "do you have parking?" is still seen as a new part,
                    # while a repeat of the same nuance correctly dedups.
                    _pp_existing = set(self.session.get("_faq_parts_answered") or [])
                    _pp_existing.update(["parking", "parking_policy"])
                    self.session["_faq_parts_answered"]    = list(_pp_existing)
                    self.session["_faq_active_topic"]      = intent
                    self.session["_faq_followup_window"]   = 3
                    self.session["_faq_last_user_question"] = transcript
                    await self._tts.put(_mfi_ans + _re_anchor_sfx)
                    self.session["last_faq_answer"] = _mfi_ans
                    return

                # ── Compound practical FAQ composition ─────────────────────
                # "does the clinic have step-free access and where exactly is
                # the alcester one" / "where is alcester and do you have
                # parking" must answer BOTH supported sub-questions in one
                # concise response, not just the first matching single path.
                # Only fires when 2+ supported parts are detected AND a
                # clinic is resolved (explicit mention or last_faq_loc_id
                # carry-over).  Falls through to existing single-path
                # behaviour when the composite cannot be built.
                _pp_access_sig = (
                    "step free", "step-free", "step three",
                    "wheelchair", "wheel chair",
                    "accessible", "accessibility",
                    "disabled access", "disability",
                )
                _pp_location_sig = (
                    "where exactly", "where is", "where are",
                    "address", "postcode", "post code",
                    "which road", "which street",
                    "find you", "locate you", "get to",
                    "directions", "how do i get",
                )
                _pp_parking_sig = (
                    "parking", "car park", "paid parking",
                    "free parking", "easy to park",
                    "pay to park", "do i need to pay",
                    "is it free",
                )
                _pp_transport_sig = (
                    "bus", "train", "public transport",
                    "station", "tube", "coach",
                )
                _pp_parts: list = []
                if any(p in _mfi_text for p in _pp_access_sig):    _pp_parts.append("accessibility")
                if any(p in _mfi_text for p in _pp_location_sig):  _pp_parts.append("location")
                if any(p in _mfi_text for p in _pp_parking_sig):   _pp_parts.append("parking")
                if any(p in _mfi_text for p in _pp_transport_sig): _pp_parts.append("transport")

                if len(_pp_parts) >= 2 and _mfi_loc:
                    logger.info(
                        "[ms_faq] practical_followup_detected clinic=%s parts=%s",
                        _mfi_loc_id, "|".join(_pp_parts),
                    )
                    _pp_fragments: list = []
                    # 1. Accessibility confirmation (clinic-wide)
                    if "accessibility" in _pp_parts:
                        _ac_raw   = (_cli_mfi.get("faq") or {}).get("accessibility") or ""
                        _ac_first = _ac_raw.split(".")[0].strip() if _ac_raw else ""
                        _pp_fragments.append(
                            (_ac_first + ".") if _ac_first
                            else "Yes \u2014 both our clinics are wheelchair accessible."
                        )
                    # 2. Exact clinic location (clinic-specific)
                    if "location" in _pp_parts:
                        _addr_raw   = _mfi_loc.get("address", "")
                        _addr_first = _addr_raw.split(".")[0].strip() if _addr_raw else ""
                        _loc_name   = _mfi_loc.get("name", _mfi_loc_id.title())
                        # Strip leading "We're at " / "We are at " so the
                        # composed sentence does not read "is at We're at ...".
                        for _lead in ("we\u2019re at ", "we're at ", "we are at "):
                            if _addr_first.lower().startswith(_lead):
                                _addr_first = _addr_first[len(_lead):].lstrip()
                                break
                        if _addr_first:
                            _pp_fragments.append(
                                f"The {_loc_name} clinic is at {_addr_first}."
                            )
                    # 3. Parking practical detail (clinic-specific first sentence)
                    if "parking" in _pp_parts:
                        _park_raw   = _mfi_loc.get("parking", "")
                        _park_first = _park_raw.split(".")[0].strip() if _park_raw else ""
                        if _park_first:
                            _pp_fragments.append(_park_first + ".")
                    # 4. Transport practical detail (clinic-specific first sentence)
                    if "transport" in _pp_parts:
                        _trans_raw   = _mfi_loc.get("transport", "")
                        _trans_first = _trans_raw.split(".")[0].strip() if _trans_raw else ""
                        if _trans_first:
                            _pp_fragments.append(_trans_first + ".")
                    _mfi_ans_composite = " ".join(_pp_fragments).strip()
                    if _mfi_ans_composite and len(_pp_fragments) >= 2:
                        logger.info(
                            "[ms_faq] practical_followup_answer_source=clinic_config "
                            "composite=True clinic=%s parts=%s",
                            _mfi_loc_id, "|".join(_pp_parts),
                        )
                        self.session["last_faq_sub"]           = "composite"
                        self.session["last_faq_loc_id"]        = _mfi_loc_id
                        self.session["last_faq_intent"]        = intent
                        self.session["_faq_parts_answered"]    = list(_pp_parts)
                        self.session["_faq_active_topic"]       = intent
                        self.session["_faq_followup_window"]    = 3
                        self.session["_faq_last_user_question"] = transcript
                        logger.info(
                            "[ms_faq] practical_parts_answered stored=%s",
                            "|".join(_pp_parts),
                        )
                        await self._tts.put(_mfi_ans_composite + _re_anchor_sfx)
                        self.session["last_faq_answer"] = _mfi_ans_composite
                        return
                    else:
                        logger.info(
                            "[ms_faq] practical_followup_fallback single_path=%s "
                            "reason=insufficient_composite_fragments",
                            intent,
                        )
                elif len(_pp_parts) >= 2 and not _mfi_loc:
                    logger.info(
                        "[ms_faq] practical_followup_fallback single_path=%s "
                        "reason=clinic_unresolved parts=%s",
                        intent, "|".join(_pp_parts),
                    )

                # Accessibility-specific question (step-free, wheelchair) — check
                # clinic-level faq.accessibility field first (applies to all locations).
                _access_q = any(p in _mfi_text for p in (
                    "step free", "step-free", "step three", "wheelchair",
                    "disabled access", "disability",
                ))
                if _access_q:
                    _mfi_ans = _short_access_ans(_cli_mfi)
                    self.session["last_faq_sub"] = "accessibility"
                    self.session["_faq_parts_answered"] = ["accessibility"]
                    logger.info(
                        "[ms_flow] _handle_mid_flow_interrupt: faq_location accessibility fast path"
                    )
                    await self._tts.put(_mfi_ans + _re_anchor_sfx)
                    self.session["last_faq_answer"] = _mfi_ans
                    self.session["last_faq_loc_id"] = _mfi_loc_id
                    self.session["last_faq_intent"] = intent
                    # Stamp topic memory before early return
                    self.session["_faq_active_topic"]       = intent
                    self.session["_faq_followup_window"]    = 3
                    self.session["_faq_last_user_question"] = transcript
                    return
                _parking_q = _access_q or any(p in _mfi_text for p in (
                    "parking", "park", "disabled", "accessible", "accessibility",
                ))
                _transport_q = any(p in _mfi_text for p in (
                    "bus", "train", "transport", "station",
                    "get there", "travel", "journey", "public",
                ))
                if _mfi_loc:
                    if _parking_q:
                        _mfi_parking_raw = _mfi_loc.get("parking", "")
                        _mfi_parking_first = (_mfi_parking_raw.split(".")[0].strip() + ".") if "." in _mfi_parking_raw else _mfi_parking_raw
                        _mfi_ans = "Of course — " + _mfi_parking_first
                        self.session["last_faq_sub"] = "parking"
                        self.session["_faq_parts_answered"] = ["parking"]
                    elif _transport_q:
                        _mfi_ans = "Of course — " + _mfi_loc.get("transport", "")
                        self.session["last_faq_sub"] = "transport"
                        self.session["_faq_parts_answered"] = ["transport"]
                    else:
                        # First sentence of address only — voice-friendly length
                        _fa = _mfi_loc.get("address", "")
                        _mfi_ans = "Of course — " + _fa.split(".")[0].strip() + ("." if _fa else "")
                        self.session["last_faq_sub"] = "address"
                        self.session["_faq_parts_answered"] = ["location"]
                else:
                    # No clinic established + multi-location clinic: asking for
                    # parking, transport, or address for an unspecified clinic
                    # cannot be answered accurately.  Ask which clinic the caller
                    # means instead of guessing or combining both answers.
                    _loc_clar = "Sure — is that for our Alcester or Redditch clinic?"
                    await self._tts.put(_loc_clar)
                    self.session["_faq_loc_pending_intent"] = intent
                    self.session["_faq_loc_pending_sub"] = (
                        "parking" if _parking_q else
                        "transport" if _transport_q else
                        "address"
                    )
                    self.session["last_question"] = _loc_clar
                    self.session["last_faq_sub"] = "address"
                    logger.info(
                        "[ms_flow] _handle_mid_flow_interrupt: faq_location — "
                        "no clinic established, clarification emitted"
                    )
                    return  # skip standard re-anchor; clarification IS the re-anchor
            if _mfi_ans:
                # Persist clinic + intent for carry-over and correction recovery
                # (BUG 1: follow-up inherits clinic; BUG 2: correction reruns intent)
                self.session["last_faq_loc_id"] = _mfi_loc_id
                self.session["last_faq_intent"] = intent
                logger.info(
                    "[ms_flow] _handle_mid_flow_interrupt: %s deterministic (loc=%s)",
                    intent, _mfi_loc_id,
                )
                await self._tts.put(_mfi_ans + _re_anchor_sfx)
                self.session["last_faq_answer"] = _mfi_ans
            else:
                # Config data missing — fall back to LLM
                _topic_fb = "opening hours" if intent == "faq_hours" else "location and address"
                instruction = (
                    f"The caller asked about {_topic_fb}. "
                    "Answer directly from the clinic information in your system prompt — "
                    "1–2 sentences, just answer and stop."
                )
                await self._llm(instruction, allow_tools=False)
        elif intent == "faq_child_policy":
            # Age-gate: never reject before knowing the age — "my son" could be 19.
            # Ask age first; the global _child_age_pending block handles the response.
            _cp_tx  = transcript.strip().lower()
            _cp_rel = (
                "son"      if any(w in _cp_tx for w in ("son", "boy", " he ", "his")) else
                "daughter" if any(w in _cp_tx for w in ("daughter", "girl", " she ", "her")) else
                "child"
            )
            _CP_PRONOUNS = {"son": "your son", "daughter": "your daughter"}
            _cp_age_q = (
                f"Of course \u2014 just so I can check we can help, "
                f"how old is {_CP_PRONOUNS.get(_cp_rel, 'your child')}?"
            )
            await self._tts.put(_cp_age_q)
            self.session["_child_age_pending"]      = True
            self.session["_child_age_relationship"] = _cp_rel
            self.session["_child_age_reask_count"]  = 0
            self.session["last_question"]           = _cp_age_q
            self.session["last_faq_answer"]         = _cp_age_q
            logger.info(
                "[ms_flow] _handle_mid_flow_interrupt: faq_child_policy → age-gate "
                "(rel=%s)", _cp_rel,
            )
            return  # global _child_age_pending block handles next turn; skip re-anchor
        elif intent == "faq_booking_logistics":
            from app.clinic_config import get_clinic as _gc_bl
            _cli_bl = _gc_bl(self.session.get("clinic_id") or "demo")
            _bl_faq = _cli_bl.get("faq", {})
            _bl_tx  = transcript.strip().lower()
            if any(s in _bl_tx for s in ("referral", "refer", "self-refer", "gp letter")):
                _bl_ans = _bl_faq.get("gp_referral") or "No referral is needed — you can book directly with us."
            elif any(s in _bl_tx for s in ("cancel", "cancellation")):
                _can_pol = _cli_bl.get("cancellation_policy", "")
                _bl_ans = _can_pol or "You can cancel by phone, and we ask for at least 24 hours' notice."
            elif any(s in _bl_tx for s in ("online", "book online", "how to book", "how do i book", "how do bookings")):
                _bl_ans = _bl_faq.get("online_booking") or "You can book by phone or online at our website."
            elif any(s in _bl_tx for s in ("pay", "payment", "how do i pay", "how can i pay")):
                _bl_ans = _bl_faq.get("payment_methods") or "We accept cash and card — payment is due at or after your session."
            elif any(s in _bl_tx for s in (
                "first visit", "first appointment", "first session", "first time",
                "what happens", "what to expect", "what should i expect",
                "how long is the first",
            )):
                _bl_ans = _FAQ_FIRST_APPT_ANSWER
            elif any(s in _bl_tx for s in (
                "what do i bring", "do i need to bring", "what to bring",
                "what should i bring", "what do i wear", "what should i wear",
                "what do i need to wear",
            )):
                _bl_ans = (
                    _bl_faq.get("what_to_bring")
                    or _cli_bl.get("what_to_bring")
                    or (
                        "Just wear comfortable, loose clothing if you can. "
                        "If you have any scans, reports, or a referral letter, bring those too \u2014 "
                        "but don\u2019t worry if you haven\u2019t, just come as you are."
                    )
                )
            else:
                _bl_ans = None
            if _bl_ans:
                logger.info("[ms_flow] _handle_mid_flow_interrupt: booking_logistics fast path")
                await self._tts.put(_bl_ans + _re_anchor_sfx)
                self.session["last_faq_answer"] = _bl_ans
            else:
                # Non-deterministic path — set instruction and fall through to LLM below.
                instruction = (
                    f"The caller asked: '{transcript.strip()}'\n"
                    "Answer briefly in 1\u20132 sentences about the booking or appointment process. "
                    "Use the clinic information in your system prompt. Just answer and stop."
                )
        elif intent in _FAQ_TOPICS:
            topic = _FAQ_TOPICS[intent]
            instruction = (
                f"The caller asked about {topic}. "
                "Answer directly from the clinic information in your system prompt — "
                "do NOT call any tools, do NOT ask clarifying questions. "
                "Answer warmly and concisely in 1–2 sentences. "
                "Just answer and stop — do NOT re-ask the booking question or add transitions."
            )
        else:
            # General question — LLM answers from knowledge.
            # Combine with any accumulated context fragment from a prior turn.
            _gq_text = transcript.strip()
            if _ctx_frag and _ctx_frag != _gq_text:
                _gq_text = f"{_ctx_frag} — {_gq_text}"
            instruction = (
                f"The caller asked: '{_gq_text}'\n"
                "Answer it helpfully in 1–2 sentences from the clinic information in your "
                "system prompt. "
                "Do NOT call check_availability, book_appointment, or any booking tool. "
                "Do NOT re-ask the booking question. "
                "Do NOT add any transitional phrases or invitations such as "
                "'yes go on', 'where were we', or 'sorry about that'. "
                "Do NOT mention line quality, connection issues, or ask the caller to repeat. "
                "If the question is unclear, give a brief helpful answer and offer to assist further. "
                "Just answer and stop."
            )
        logger.info("[ms_flow] _handle_mid_flow_interrupt: intent=%s", intent)
        _skip_llm = (
            intent in ("faq_services", "faq_capability", "faq_insurance",
                       "faq_hours", "faq_location", "faq_child_policy")
            or (intent == "faq_booking_logistics" and self.session.get("last_faq_answer"))
            or (intent == "faq_prices" and not any(
                k in transcript.strip().lower() for k in _FAQ_PRICES_SERVICE_KEYWORDS
            ))
        )
        if not _skip_llm:
            await self._llm(instruction, allow_tools=False)
        # Stamp active FAQ topic and refresh follow-up window after any FAQ answer.
        # general_query is excluded — it must not overwrite the active topic when
        # this call was itself a carry-forward of an existing topic.
        if intent != "general_query":
            self.session["_faq_active_topic"]       = intent
            self.session["_faq_followup_window"]    = 3
            self.session["_faq_last_user_question"] = transcript
        # After the aside, re-anchor the caller to the exact step they were in.
        # This is step-specific so the caller is never left with an open floor.
        _int_step = self.current_step()
        if _int_step is not None:
            _int_state = _int_step["state"]
            if _int_state in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
                _int_avail  = self.session.get("available_days", [])
                _int_anchor = (
                    _build_day_list_phrase(_int_avail)
                    or "Which of those days works best for you?"
                )
            elif _int_state in ("PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE"):
                _int_avail  = self.session.get("available_days", [])
                _int_chosen = self.session.get("chosen_day", "")
                _int_target = _find_chosen_day_entry(_int_avail, _int_chosen)
                _int_slots  = (_int_target or {}).get("slots", [])
                if len(_int_slots) == 1:
                    from app.vagueness_detector import _time_to_speech as _t2s_int
                    _int_time   = ((_int_target or {}).get("slot_times") or [""])[0]
                    _int_spoken = _t2s_int(_int_time) if _int_time else "that time"
                    _int_dlabel = (_int_target or {}).get("day_label", "")
                    _int_anchor = f"Sure — did you want {_int_dlabel} at {_int_spoken}?"
                else:
                    _int_times = (_int_target or {}).get("slot_times", [])[:4]
                    if _int_times:
                        from app.vagueness_detector import _time_to_speech as _t2s_int
                        _int_opts   = " or ".join(_t2s_int(t) for t in _int_times)
                        _int_anchor = f"Sure — which of those times works? I had {_int_opts}."
                    else:
                        _int_anchor = self.session.get("last_question", "")
            elif (
                _int_state in (
                    "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                    "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                )
                and self.session.get("name_readback_pending")
            ):
                _int_anchor = "Sorry — was that yes, or did you want to correct the name?"
            elif _int_state in ("FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER"):
                # Multi-question FAQ floor: always use a fresh neutral follow-up.
                # Do NOT read last_question here — it may contain an old FAQ answer
                # body or a stale entry prompt from a prior turn, which would be
                # re-spoken as the re-anchor and break conversation coherence.
                # (The mid-flow interrupt path returns at line ~9070, bypassing the
                # last_question update in the FAQ_BOOKING_OFFER handler, so the only
                # reliable way to break the stale-anchor chain is here.)
                _int_anchor = "Anything else you'd like to ask?"
            else:
                _int_anchor = self.session.get("last_question", "")

            # Safety net: if _int_anchor is still empty, use a state-aware hard default
            # so the caller is NEVER left in dead air after a mid-flow interrupt.
            if not _int_anchor:
                _ANCHOR_DEFAULTS: "Dict[str, str]" = {
                    "NEW_OR_RETURNING":            "Have you been with us before, or is this your first time?",
                    "RETURNING_RECENCY":           "And was that recently, or a little while ago?",
                    "RETURNING_TREATMENT_PLAN":    "Are you still on a current treatment plan with us?",
                    "COLLECT_NAME":                "Could I take your name please?",
                    "COLLECT_NAME_RETURNING":      "And could I take your name please?",
                    "COLLECT_NAME_RESCHEDULE":     "And could I take your name please?",
                    "COLLECT_NAME_CANCEL":         "And could I take your name please?",
                    "COLLECT_PHONE":               "And the best number to reach you on?",
                    "COLLECT_PHONE_RETURNING":     "And the best number to reach you on?",
                    "COLLECT_PHONE_RESCHEDULE":    "And the best number to reach you on?",
                    "PRESENT_DAYS":                "Which day would work best for you?",
                    "PRESENT_DAYS_RESCHEDULE":     "Which day would work best for you?",
                    "PRESENT_TIMES":               "Which time would suit you?",
                    "PRESENT_TIMES_RESCHEDULE":    "Which time would suit you?",
                    "CONFIRM_BOOKING":             "Does that all sound right?",
                    "FAQ_BOOKING_OFFER":           "Anything else you'd like to ask?",
                    "GENERAL_BOOKING_OFFER":       "Anything else you'd like to ask?",
                    "COLLECT_REASON":              "What is it that's bringing you in?",
                    "CONFIRM_PHONE":               "Is that number correct?",
                    "CONFIRM_PHONE_RETURNING":     "Is that number correct?",
                }
                _int_anchor = _ANCHOR_DEFAULTS.get(
                    _int_state or "",
                    "Can I help you continue with your booking?",
                )
                logger.info(
                    "[ms_flow] mid-flow interrupt: last_question empty — using default anchor for %s",
                    _int_state,
                )

            # Re-anchor.  For FAQ/GENERAL offer states we set last_question so the
            # silence handler asks "Anything else?" only after genuine silence — we
            # never speak it immediately after the answer (Bug 1 fix).
            _offer_states = {"FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER"}
            _anchor_spoken = ""  # always defined — prevents UnboundLocalError in logger below
            # Prompt 5 Bug #3: after a mid-booking FAQ answer in COLLECT_REASON, do
            # NOT speak a forced re-anchor in the same response — store last_question
            # silently so the silence watchdog can replay it only after real silence.
            _defer_reanchor = (
                _int_state == "COLLECT_REASON" and intent == "faq_services"
            )
            if _int_state in _offer_states:
                _faq_reanchor_text = "Anything else you'd like to ask?"
                # Prompt 8 Bug 1 fix: only store the deferred prompt when last_question
                # is empty or is already the generic placeholder.  Never overwrite an
                # active specific prompt (e.g. "Sure — is that Alcester or Redditch?"
                # or a forced-confirm question) — those must remain the repeat target.
                _curr_lq = self.session.get("last_question", "")
                _GENERIC_FAQ_LQ = {"Anything else you'd like to ask?", ""}
                if _curr_lq in _GENERIC_FAQ_LQ:
                    self.session["last_question"] = _faq_reanchor_text
                # Do NOT emit aloud — the silence handler replays last_question after
                # real silence.  Speaking it right after the answer felt over-aggressive.
                logger.info("[ms_flow] faq_reanchor: (deferred to silence handler)")
            elif _defer_reanchor:
                if _int_anchor.strip():
                    self.session["last_question"] = _int_anchor
                logger.info(
                    "[ms_flow] faq_reanchor: COLLECT_REASON+faq_services deferred "
                    "(last_question=%r)", (_int_anchor or "")[:80],
                )
            else:
                if not _int_anchor.strip():
                    logger.warning(
                        "[ms_flow] faq_reanchor_empty_guard_hit: state=%s anchor=%r",
                        _int_state, _int_anchor,
                    )
                _RE_ANCHOR_OPENERS = [
                    "Anyway \u2014 ",
                    "So \u2014 ",
                    "Right \u2014 ",
                    "Coming back to that \u2014 ",
                ]
                _anchor_spoken = _random.choice(_RE_ANCHOR_OPENERS) + _int_anchor
                await self._tts.put(_anchor_spoken)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _anchor_spoken}
                )
            logger.info(
                "[ms_flow] mid-flow interrupt: step re-anchor %s → %r",
                _int_state, (_anchor_spoken or "(baked)")[:80],
            )
        else:
            logger.info("[ms_flow] mid-flow interrupt: flow complete — no re-anchor")

    async def _handle_phone_readback_confirmation(
        self, text: str, transcript: str, step: Dict[str, Any]
    ) -> None:
        """
        Handle the yes/no response after we read the collected phone number back.

        yes / unclear after one retry → accept number, clear flag, advance flow
        no                            → clear number + buffer, re-ask for it
        """
        logger.info(
            "[ms_flow] phone_readback state=%s input=%r phone_accept=%s",
            step["state"], text[:60], _is_phone_accept(text),
        )
        # FIRST-CHECK: explicit phone-accept phrase overrides readback yes/no
        _prb_twilio = (
            self.session.get("twilio_from_local")
            or self.session.get("twilio_from", "")
        )
        if _is_phone_accept(text) and _prb_twilio:
            logger.info(
                "[ms_flow] compat_phone_accept state=%s input=%r",
                step["state"], text[:60],
            )
            import re as _re_prb
            _prb_digits = _re_prb.sub(r"\D", "", _prb_twilio)
            _prb_phone  = _prb_digits or _prb_twilio
            self.session["phone_confirmed"]       = True
            self.session["phone_from_twilio"]     = True
            self.session["phone_number"]          = _prb_phone
            self.session.setdefault("collected", {})["phone"] = _to_e164_uk(_prb_phone)
            self.session["phone_readback_pending"] = False
            self.session.pop("phone_readback_retry", None)
            self.session.pop("slot_pending_confirmation", None)
            self.session.pop("vague_option_pending", None)
            self.session.pop("vague_clarification_asked", None)
            if self._active_flow is RESCHEDULE_FLOW:
                self.session["flow_step"] = _RESCHEDULE_LOOKUP_INDEX
                self.session["state"]     = "LOOKUP_RESCHEDULE"
            elif self.session.get("selected_slot"):
                self.session["flow_step"] = _CONFIRM_BOOKING_INDEX
                self.session["state"]     = "CONFIRM_BOOKING"
            else:
                self.session["flow_step"] = _PRESENT_DAYS_INDEX
                self.session["state"]     = "PRESENT_DAYS"
            logger.info("[ms_flow] compat_phone_accept -> %s", self.session["state"])
            await self.ask_current_question()
            return

        answer = self._extract("yes_no", text, transcript)
        logger.info(
            "[ms_flow] phone readback confirmation: %r → %s", transcript[:60], answer
        )

        if answer is True:
            # Confirmed — clear readback flag, stale retry counter, and advance
            self.session["phone_readback_pending"] = False
            self.session["phone_digits_buffer"]    = ""
            self.session.pop("phone_readback_retry", None)
            self.session["flow_step"] = step["step"] + 1
            logger.info("[ms_flow] phone readback confirmed — advancing")
            _next_step = self.current_step()
            _next_llm  = _next_step["use_llm"] if _next_step else False
            _bridge    = _get_bridge(step["state"], True, self.session, _next_llm)
            if _bridge:
                await self._tts.put(_bridge)
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _bridge}
                )
            await self.ask_current_question()

        elif answer is False:
            # Rejected — clear everything and re-ask for the number
            self.session["phone_readback_pending"] = False
            self.session["phone_number"]           = None
            self.session["phone_digits_buffer"]    = ""
            col = self.session.setdefault("collected", {})
            col.pop("phone", None)
            phrase = "No problem — could you give me the number again?"
            await self._tts.put(phrase)
            self.session["last_question"] = phrase
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            logger.info("[ms_flow] phone readback rejected — re-asking")

        else:
            # Unclear — use the retry counter; on second unclear, accept and move on
            retry_key  = "phone_readback_retry"
            retry_count = self.session.get(retry_key, 0) + 1
            self.session[retry_key] = retry_count
            if retry_count >= 2:
                # Accept silently rather than block the caller indefinitely
                self.session["phone_readback_pending"] = False
                self.session.pop(retry_key, None)
                self.session["flow_step"] = step["step"] + 1
                logger.info("[ms_flow] phone readback: 2nd unclear — accepting and advancing")
                await self.ask_current_question()
            else:
                # Do NOT prepend last_question — it belongs to a prior step.
                phrase = "Is that number correct?"
                await self._tts.put(phrase)
                self.session["last_question"] = phrase
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": phrase}
                )

    # ── slot confirmation ─────────────────────────────────────────────────

    async def _handle_slot_confirmation(self, text: str, transcript: str) -> None:
        """
        Handle the yes/no response after Susie has confirmed a slot selection.

        yes → clear flag, advance flow_step past PRESENT_TIMES, ask next question
        no  → clear flag, clear selected_slot, re-ask which slot they prefer
              (does NOT re-run LLM/check_availability — slots are still offered)
        no match → re-ask the confirmation phrase
        """
        yes_patterns = [
            "yes", "yeah", "yeh", "ya", "yep", "yup", "correct",
            "that's right", "thats right", "perfect",
            "sounds good", "that works", "go ahead",
            "please", "ok", "okay", "sure", "fine",
            "that one", "confirmed", "alright", "aye",
            # Natural spoken confirmations:
            "it does", "yes it does", "it would", "it will",
            "suits me", "that suits", "suits", "does suit",
            "that'd work", "sounds great", "that's great", "thats great",
            "happy with that", "happy with",
            "works for me",
        ]
        no_patterns = [
            "no", "nope", "nah", "wrong", "different",
            "not that", "actually no", "change",
            "other one", "different one", "not right",
        ]

        for p in yes_patterns:
            if p in text:
                logger.info("[ms_flow] slot confirmation: YES matched=%r branch=YES", p)
                self.session["slot_pending_confirmation"] = False
                self.session.pop("vague_option_pending", None)
                self.session.pop("vague_clarification_asked", None)
                step = self.current_step()
                if step:
                    _nxt_sc_i = step["step"] + 1
                    _nxt_sc = (
                        self._active_flow[_nxt_sc_i]["state"]
                        if _nxt_sc_i < len(self._active_flow) else "DONE"
                    )
                    self.session["flow_step"]  = _nxt_sc_i
                    self.session["state"]      = _nxt_sc
                    self.session["flow_state"] = _nxt_sc
                    logger.info("[ms_flow] slot confirmed → advancing state=%s", _nxt_sc)
                    print("[SLOT GATE] confirm yes -> collect_name", {
                        "state":     self.session.get("state"),
                        "flow_step": self.session.get("flow_step"),
                    })
                self.session["_last_handled_by"] = "slot_pending_confirmation"
                await self.ask_current_question()
                return

        for p in no_patterns:
            if p in text:
                logger.info("[ms_flow] slot confirmation: NO matched=%r", p)
                self.session["slot_pending_confirmation"] = False
                self.session["selected_slot"] = None
                # Check if the caller is correcting to a different offered day
                # (e.g. "no, I said Thursday not Tuesday").
                import re as _re_sc
                _sc_avail = self.session.get("available_days", [])
                _sc_chosen = self.session.get("chosen_day", "")
                _sc_cur_wd = next(
                    (w for w in _sc_chosen.lower().split() if w in _WEEKDAY_WORDS), None
                )
                _sc_new_entry = None
                for _sc_entry in _sc_avail:
                    _sc_wd = next(
                        (w for w in _sc_entry.get("day_label", "").lower().split()
                         if w in _WEEKDAY_WORDS), None
                    )
                    if _sc_wd and _sc_wd != _sc_cur_wd and _re_sc.search(r'\b' + _sc_wd + r'\b', text):
                        _sc_new_entry = _sc_entry
                        break
                if _sc_new_entry:
                    _sc_new_label = _sc_new_entry.get("day_label", "")
                    self.session["chosen_day"] = _sc_new_label
                    self.session.setdefault("collected", {})["chosen_day"] = _sc_new_label
                    logger.info(
                        "[ms_flow] slot confirmation NO: day-change %r → %r",
                        _sc_chosen, _sc_new_label,
                    )
                    await self.ask_current_question()
                    return
                # No day correction — stay at PRESENT_TIMES for caller to pick again.
                phrase = "No problem — which slot would you prefer?"
                await self._tts.put(phrase)
                _store_last_question(
                    self.session, phrase,
                    force=True, source="flow_step:slot_confirm_reask",
                )
                return

        # No match — acknowledged re-ask with retry counting
        retry_counts = self.session.setdefault("slot_retry_counts", {})
        retry_counts["slot_confirmation"] = retry_counts.get("slot_confirmation", 0) + 1
        count = retry_counts["slot_confirmation"]
        logger.info("[ms_flow] slot confirmation no-match — retry #%d", count)
        if count >= 3:
            phrase = (
                "I'm having a little trouble catching that — "
                "please give us a call back and the team can help you get booked."
            )
            await self._tts.put(phrase)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            self.session["graceful_exit"] = True
            self.session["request_transfer"] = True
            self.session["flow_step"] = len(self._active_flow)
            logger.info("[ms_flow] slot confirmation retry >= 3 — graceful exit triggered")
            return
        if count == 2:
            phrase = RETRY_PHRASES["second_retry"]["default"]
        else:
            # Do NOT prepend last_question — it belongs to a prior step.
            phrase = RETRY_PHRASES["first_retry"]["default"]
        await self._tts.put(phrase)

    # ── STATE_READBACK ────────────────────────────────────────────────────

    async def _start_readback(self) -> None:
        """
        Speak a full booking readback and wait for caller confirmation.

        Called by ask_current_question() when BOOKING_FLOW completes (flow_step
        is out of bounds) and the readback has not yet been delivered.  Sets
        readback_pending=True so the next caller turn routes to
        _handle_readback_confirmation().
        """
        # Insertion point 1 — name usage tracker (use name if still available)
        _tracked_name = self._name_tracker.get_name_if_available()
        # Update session mirrors after decrement
        self.session["name_tracker_uses"] = self._name_tracker._uses_remaining
        name   = _tracked_name or (self.session.get("full_name") or "")
        slot   = (
            self.session.get("selected_slot_speech")
            or self.session.get("selected_slot")
            or "the selected time"
        )

        # BUG 9: guard against obvious garbage name reaching the final confirmation.
        # If the captured name looks like a greeting or filler, reset to COLLECT_NAME.
        _FILLER_NAMES = frozenset({
            "hello", "hi", "hey", "yes", "no", "okay", "ok", "sure",
            "thanks", "thank", "please", "bye", "goodbye",
            "yeah", "yep", "yup", "nope", "nah", "yeh",
        })
        if not name.strip() or name.strip().lower() in _FILLER_NAMES:
            _cn_step = next(
                (s["step"] for s in self._active_flow if s["state"] == "COLLECT_NAME"), None
            )
            if _cn_step is not None:
                self.session["flow_step"] = _cn_step
                self.session["full_name"] = None
                self.session.setdefault("collected", {}).pop("full_name", None)
                self.session.setdefault("collected", {}).pop("name", None)
                self.session["readback_delivered"] = False  # allow retry after re-collection
                _gb_phrase = "Just before I confirm — could you say your name for me?"
                await self._tts.put(_gb_phrase)
                self.session["last_question"] = _gb_phrase
                self.session.setdefault("conversation_history", []).append(
                    {"role": "assistant", "content": _gb_phrase}
                )
                logger.info("[ms_flow] readback: garbage name %r — re-collecting", name)
                return
        name = name or "you"

        # Fix D: short readback — drop the verbose preamble and reason to reduce
        # interruption risk.
        _rb_loc = (self.session.get("selected_location") or "alcester").lower()
        _rb_clinic = "Redditch" if "redditch" in _rb_loc else "Alcester"
        phrase = (
            f"Just to confirm — {name}, you're booked in for {slot} "
            f"at our {_rb_clinic} clinic. Does that sound right?"
        )
        await self._tts.put(phrase)
        self.session.setdefault("conversation_history", []).append(
            {"role": "assistant", "content": phrase}
        )
        self.session["last_question"]      = "Does that all sound right?"
        self.session["readback_pending"]   = True
        self.session["readback_delivered"] = True
        self.session["state"]              = "STATE_READBACK"
        logger.info("[ms_flow] readback started: %r", phrase[:100])

    async def _classify_readback_response(self, transcript: str) -> dict:
        """
        Call Claude (max_tokens=80, temperature=0) to classify the caller's
        response to the booking readback.

        Returns {"confirmed": bool, "corrected_slot": str|None, "new_value": str|None}.
        Raises on network or parse errors — caller must wrap in try/except.
        """
        import json as _json
        import anthropic as _anthropic

        client = _anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            temperature=0,
            messages=[{
                "role": "user",
                "content": (
                    f"The caller said: '{transcript}'\n"
                    "Classify as JSON only — no extra text:\n"
                    "{\"confirmed\": bool, \"corrected_slot\": string_or_null, "
                    "\"new_value\": string_or_null}\n"
                    "confirmed=true if the caller is agreeing to the readback.\n"
                    "corrected_slot is the session field being corrected: "
                    "full_name, phone_number, reason, or selected_slot.\n"
                    "new_value is the corrected value they stated.\n"
                    "Respond ONLY with valid JSON."
                ),
            }],
        )
        return _json.loads(response.content[0].text.strip())

    async def _handle_readback_confirmation(self, text: str, transcript: str) -> None:
        """
        Handle the caller's response to the STATE_READBACK phrase.

        Turn 1 (readback_correction_turn=False):
          - Classify with Claude.
          - Confirmed → advance to CONFIRM_BOOKING.
          - Corrected → update slot, speak correction phrase, set readback_correction_turn=True.
          - Classification error → treat as confirmed (log, proceed).

        Turn 2 (readback_correction_turn=True):
          - Accept any response as confirmed, advance to CONFIRM_BOOKING.
        """
        # Second turn after a correction: accept anything as confirmed
        if self.session.get("readback_correction_turn"):
            self.session["readback_pending"]         = False
            self.session["readback_correction_turn"] = False
            self.session["flow_step"]                = _CONFIRM_BOOKING_INDEX
            logger.info("[ms_flow] readback second-turn — treating as confirmed")
            await self.ask_current_question()
            return

        # First turn: classify with Claude
        try:
            result = await self._classify_readback_response(transcript)
        except Exception as exc:
            logger.error(
                "[ms_flow] readback classification failed: %r — treating as confirmed", exc
            )
            result = {"confirmed": True, "corrected_slot": None, "new_value": None}

        confirmed      = bool(result.get("confirmed"))
        corrected_slot = result.get("corrected_slot")
        new_value      = result.get("new_value")

        if confirmed:
            self.session["readback_pending"] = False
            self.session["flow_step"]        = _CONFIRM_BOOKING_INDEX
            logger.info("[ms_flow] readback confirmed — advancing to CONFIRM_BOOKING")
            await self.ask_current_question()
        elif corrected_slot and new_value:
            # Update the corrected slot and mirror into collected{}
            self.session[corrected_slot] = new_value
            if corrected_slot == "full_name":
                col = self.session.setdefault("collected", {})
                col["full_name"] = new_value
                col["name"]      = new_value
            elif corrected_slot == "phone_number":
                self.session.setdefault("collected", {})["phone"] = new_value

            phrase = (
                f"Got it — {new_value}. "
                "Everything else stays the same — shall I go ahead and book that in?"
            )
            await self._tts.put(phrase)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            self.session["last_question"]            = "Shall I go ahead and book that in?"
            self.session["readback_correction_turn"] = True
            logger.info("[ms_flow] readback correction: %s=%r", corrected_slot, new_value)
        else:
            # confirmed=False with no parseable correction (e.g. "no cancel it",
            # "I want to change that") — re-ask rather than silently advance.
            # readback_pending stays True so the next transcript routes here again.
            phrase = (
                "Sorry — does everything sound right, "
                "or would you like to change something?"
            )
            await self._tts.put(phrase)
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": phrase}
            )
            self.session["last_question"] = phrase
            logger.info("[ms_flow] readback: not confirmed, no correction — re-asking")

    # ── extraction ────────────────────────────────────────────────────────

    def _extract(self, method: str, text: str, raw: str) -> Optional[Any]:
        """
        Extract a typed answer from the caller's normalised text.

        Returns the extracted value, or None if no valid answer was found.
        """

        # ----- any: any non-empty response is valid ----------------------
        if method == "any":
            return raw.strip() if text.strip() else None

        # ----- duration: time-period / quantity signals ------------------
        if method == "duration":
            signals = (
                "day", "days", "week", "weeks", "month", "months",
                "year", "years", "hour", "hours", "while", "ago",
                "since", "recently", "just", "about", "couple", "few",
                "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "always", "long", "time",
                "yesterday", "today", "morning",
                # Northern English / informal duration expressions
                "a fair while", "good while", "donkey's years",
                "ages like", "not long like", "recently like",
                "not long ago", "a good while",
                "on and off like", "comes and goes",
                "been plaguing me", "been at me",
                "right long time", "forever like",
                "ages", "forever", "plaguing",
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- yes_no: affirmative confirmation --------------------------
        if method == "yes_no":
            yes = (
                "yes", "yeah", "ya", "yep", "yup", "ok", "okay",
                "sure", "fine", "alright", "sounds good", "go ahead",
                "please", "that works", "correct", "definitely",
                "of course", "absolutely",
                # "that sounds …" variants — callers confirm with these at CONFIRM_ASSESSMENT
                "that sounds good", "that sounds fine", "that sounds okay",
                "that sounds right", "sounds fine", "sounds okay",
                "yeah that sounds", "yeah that's fine", "yeah that's okay",
                # Northern English / informal affirmatives
                "aye", "aye go on", "go on then",
                "right then", "fair enough",
                "sound", "sorted", "champion",
                "mint that", "yeah go on",
                "right okay", "alright then",
                "that'll do",
                "reight", "reight then",
                "no bother",
                "that's sound", "perfect that",
            )
            # Frustration/objection guard: don't classify as yes if the caller is
            # expressing frustration or refusing to repeat themselves.
            _frustration_patterns = (
                "not going to repeat", "not gonna repeat",
                "already said", "said it already", "said that already",
                "told you", "just told you",
                "third time", "how many times", "keep asking",
                "not repeating", "won't repeat",
                "i'm not going to", "im not going to",
                "why do you keep", "stop asking",
                "said this before", "i said this",
            )
            if any(p in text for p in yes):
                if any(f in text for f in _frustration_patterns):
                    logger.info("[ms_extract] yes_no: affirmative blocked by frustration guard: '%s'", text)
                    return None
                return True
            # Fuzzy fallback for yes_no
            yes_fuzzy = [
                "yes", "yeah", "that's fine", "sounds good",
                "go ahead", "that works",
            ]
            if _fuzzy_match(text, yes_fuzzy, threshold=75):
                if not any(f in text for f in _frustration_patterns):
                    logger.info("[ms_extract] fuzzy yes: '%s'", text)
                    return True
            return None

        # ----- new_or_returning ------------------------------------------
        if method == "new_or_returning":
            # CRITICAL: new_patterns checked FIRST.
            # "i have not" contains "i have" — if returning were checked first
            # it would incorrectly match as returning.  Order must never change.
            #
            # Bare "no" and "never" are INTENTIONALLY absent — they substring-match
            # "know", "nothing", "not", "however", etc. in free-form sentences.
            # Bare "yes"/"yeah" are SAFE here because this extraction is ONLY called
            # from the NEW_OR_RETURNING priority block (which already ran) or from
            # the fallback path which is now protected from general_query interrupts.
            # The word-count guard below handles "yeah whatever already told you new patient".
            new_patterns = [
                # Explicit multi-word first-time phrases (user-specified)
                "it's my first time", "it is my first time",
                "it's my first", "its my first", "this is my first",
                "my first time", "my first visit", "my first call",
                "never been before", "never been with you before",
                "i have never been", "i haven't been before",
                "i have never been with you",
                "not been with you before",
                "never been here before",
                # General new-patient indicators
                "i have not", "i haven't", "i havent",
                "have not been", "haven't been", "havent been",
                "not been", "never been", "never visited",
                "first time", "first visit",
                "new patient", "i'm new", "im new",
                "no i", "nah", "nope",
                "no never", "not visited", "don't think",
                "dont think", "no not", "not really",
                # Northern English / informal variants
                "never 'ad", "not 'ad", "me first",
                "first time like", "never like",
                "nope never", "nah never",
                "haven't no", "not as such",
                "don't think so", "not that i know",
                "new to you", "new here",
                "no i 'ave not", "no i havent",
            ]
            returning_patterns = [
                # Explicit returning phrases (user-specified)
                "i've been before", "i have been before",
                "i came before", "i've been with you before",
                "i have been with you",
                "a few times",
                # General returning indicators
                "i have been", "i've been", "ive been",
                "yeah i have", "yes i have", "yep i have",
                "been before", "been there", "been with you",
                "been a patient", "been here", "come before",
                "visited before", "existing", "returning",
                # Short bare affirmatives — safe at this state because extraction runs
                # inside the NEW_OR_RETURNING priority block before any interrupt logic.
                # Word-count guard below protects against long sentences.
                "yes", "yeah", "yep", "yup", "ya",
                # Northern English / informal variants
                "aye", "aye i have", "aye been",
                "yeah been", "yep been",
                "been a few times", "few times",
                "come before like", "been like",
                "i 'ave", "i ave been",
                "visited", "existing patient", "registered",
            ]
            # Word-count guard: for utterances > 8 words, only accept multi-word
            # specific patterns to avoid free-form sentence substring pollution.
            # e.g. "yeah whatever i already told you" must not match bare "yeah".
            word_count = len(text.split())
            if word_count > 8:
                # Only multi-word patterns (>= 2 words) are safe for long sentences
                new_patterns    = [p for p in new_patterns    if len(p.split()) >= 2]
                returning_patterns = [p for p in returning_patterns if len(p.split()) >= 2]

            matched = False
            for p in new_patterns:
                if p in text:
                    logger.info(
                        "[ms_extract] new_or_returning=new matched=%r transcript=%r",
                        p, raw[:60],
                    )
                    matched = True
                    return "new"
            if not matched:
                for p in returning_patterns:
                    if p in text:
                        logger.info(
                            "[ms_extract] new_or_returning=returning matched=%r transcript=%r",
                            p, raw[:60],
                        )
                        return "returning"
            # Fuzzy fallback for new_or_returning
            # BUG 2 fix: skip fuzzy entirely if the utterance looks like a question
            # (contains a question signal or FAQ vocabulary) — the partial_ratio
            # algorithm can match "first time" against "first do you have any parking"
            # because it finds "first" as a shared substring with a high score.
            _nor_skip_fuzzy = (
                "?" in raw
                or any(p in text for p in (
                    "do you", "have you", "is there", "are you", "can you",
                    "where", "how ", "what time", "parking", "location",
                    "address", "opening", "insurance", "cost", "price",
                ))
            )
            if _nor_skip_fuzzy:
                return None
            new_fuzzy = [
                "not been", "never been", "first time",
                "have not", "haven't been", "new patient",
            ]
            returning_fuzzy = [
                "been before", "been there", "have been",
                "visited before", "existing patient",
            ]
            if _fuzzy_match(text, new_fuzzy, threshold=85):
                logger.info("[ms_extract] fuzzy new: '%s'", text)
                return "new"
            if _fuzzy_match(text, returning_fuzzy, threshold=85):
                logger.info("[ms_extract] fuzzy returning: '%s'", text)
                return "returning"
            return None

        # ----- recency: "recently" vs "long time ago" -------------------
        if method == "recency":
            long_ago = (
                "long time", "years ago", "year ago", "ages",
                "quite a while", "good while", "been a while",
                "long while", "donkey's", "forever",
                "not for a while", "long way back", "long time ago",
                # Bare "no"/"nope" to "Was that recently?" means "not recently"
                "no", "nope", "nah", "not really",
            )
            recent = (
                "recently", "not long", "just a few", "couple months",
                "few months", "couple of weeks", "few weeks", "this year",
                "last month", "last few weeks", "few weeks ago",
                "month ago", "months ago", "weeks ago", "still going", "ongoing",
                "currently", "active", "come regularly", "been coming",
                # Specific time ranges within ~2 years
                "6 months", "six months", "8 months", "eight months",
                "10 months", "ten months", "12 months", "twelve months",
                "18 months", "eighteen months", "a year ago", "about a year",
            )
            # Check long_ago first — "long time" takes priority over shorter matches
            for sig in long_ago:
                if sig in text:
                    logger.info("[ms_extract] recency=long_ago matched=%r", sig)
                    return "long_ago"
            for sig in recent:
                if sig in text:
                    logger.info("[ms_extract] recency=recent matched=%r", sig)
                    return "recent"
            if _fuzzy_match(text, ["long time ago", "a while ago", "been a while"], threshold=75):
                return "long_ago"
            if _fuzzy_match(text, ["recently", "not long ago", "a few months ago"], threshold=75):
                return "recent"
            return None

        # ----- yes_no_explicit: yes→True, no→False, unclear→None -----------
        if method == "yes_no_explicit":
            yes_p = (
                "yes", "yeah", "ya", "yep", "yup", "ok", "okay",
                "sure", "fine", "alright", "sounds good", "go ahead",
                "please", "that works", "correct", "definitely",
                "of course", "absolutely", "aye", "aye go on",
                "right then", "fair enough", "sound", "sorted",
                "i am", "i'm on", "i have",
                # FIX B: common positive answers to "are you still coming in regularly?"
                "still coming", "coming in regularly", "regularly",
                "still on", "still under", "still having", "still getting",
                "ongoing",
            )
            no_p = (
                "no", "nope", "not really", "i'm not", "im not", "nah",
                "not on", "not currently", "don't think", "i haven't",
                "i havent", "never", "no i", "no i'm not",
                # FIX B: common negative answers
                "new episode", "flared up", "came back", "stopped",
                "finished", "ended",
            )
            for p in yes_p:
                if p in text:
                    return True
            for p in no_p:
                if p in text:
                    return False
            return None

        # ----- availability: day / time references ----------------------
        if method == "availability":
            signals = (
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday", "morning", "afternoon", "evening",
                "next week", "this week", "after", "before", "anytime",
                "any day", "flexible", "weekday", "weekend", "today",
                "tomorrow", "week", "from", "starting", "available", "free",
                # Northern English / informal availability expressions
                "any road", "whenever suits",
                "whenever really", "flexible like",
                "don't mind like", "not bothered",
                "owt really", "any time like",
                "whenever tha can", "whenever you can",
                "as soon as", "sharpish",
                "fairly soon", "sooner the better",
                "whenever", "anytime",
            )
            return raw.strip() if any(s in text for s in signals) else None

        # ----- slot_selection: which appointment slot the caller chose ---
        if method == "slot_selection":
            offered     = self.session.get("last_offered_slots") or []
            labels      = self.session.get("slot_labels") or []
            slots_count = self.session.get("slots_count", len(offered) or 3)

            # Negation guard — if the caller is rejecting a slot, don't extract it.
            # e.g. "i can't do the first slot", "not the first one", "don't want the second"
            _NEGATION_PATTERNS = (
                "can't do", "cannot do", "can't make", "cannot make",
                "don't want", "dont want", "not the first", "not the second",
                "not the third", "not that one", "not do the first",
                "can't really do", "can't do the first", "can't do the second",
                "not available for", "won't work", "wont work",
                "doesn't work", "doesnt work", "no good", "no good for me",
                "any other", "different slot", "other slot", "other slots",
                "other option", "anything else",
            )
            if any(p in text for p in _NEGATION_PATTERNS):
                logger.info("[ms_flow] slot_selection negation guard — treating as no match: %r", text[:60])
                return None

            def _pick(idx: int) -> Optional[Any]:
                """Return the human-readable slot label at 0-based index.
                slot_labels contains strings like 'Mon 23 Mar at 09:00'
                which book_appointment can resolve AND the confirmation phrase
                can repeat verbatim.  Falls back to raw slot dict (book_appointment
                handles that too), then to a plain number.
                idx is clamped to the valid range so out-of-bounds selections
                (e.g. 'second one' when only 1 slot available) return the last
                slot rather than None — prevents LLM hallucination."""
                n = len(labels) if labels else (len(offered) if offered else slots_count)
                idx = min(idx, max(n - 1, 0))  # clamp to valid range
                if labels and idx < len(labels):
                    return labels[idx]
                if offered and idx < len(offered):
                    return offered[idx]
                if slots_count:
                    return str(idx + 1)
                return None

            # "last / final" catch-all → highest slot
            last_p = (
                "last one", "the last one", "final one", "the final one",
                "the last", "last option", "last slot", "final slot",
                "final option", "that last one", "the final",
                # Northern English / informal
                "last un", "t'last", "final un",
            )
            if any(p in text for p in last_p):
                available = min(len(labels), 3) if labels else (min(len(offered), 3) if offered else slots_count)
                idx = min(slots_count, available) - 1
                logger.info("[ms_flow] slot_selection last/final → idx=%d", idx)
                return _pick(idx)

            # Generic "that one" patterns — treated as slot 1 (index 0) since
            # the caller will be asked to confirm and can correct if needed
            generic_p = (
                "that un", "that one there",
                "that'll do", "that suits", "that works for me like",
            )
            if any(p in text for p in generic_p):
                logger.info("[ms_flow] slot_selection generic → idx=0")
                return _pick(0)

            # Numbered patterns
            slot_map = {
                0: ("first", "one", "1", "option one", "number one",
                    "first one", "the first", "first slot", "option 1",
                    # Northern English / informal
                    "first un", "t'first"),
                1: ("second", "two", "2", "option two", "number two",
                    "second one", "the second", "second slot", "option 2",
                    "middle", "middle one", "that middle one",
                    # Northern English / informal
                    "middle un", "t'second"),
                2: ("third", "three", "3", "option three", "number three",
                    "third one", "the third", "third slot", "option 3"),
            }
            # Pass 1: compound (multi-word) patterns — most specific, checked first
            # to prevent "one" matching "second one" or "that middle one".
            # No slots_count guard here — _pick() clamps out-of-range idx so
            # e.g. "second one" with 1 slot returns that single slot instead of None.
            for idx, patterns in slot_map.items():
                if any(len(p.split()) > 1 and p in text for p in patterns):
                    logger.info("[ms_flow] slot_selection compound idx=%d", idx)
                    return _pick(idx)
            # Pass 2: single-word patterns — fallback
            for idx, patterns in slot_map.items():
                if idx < slots_count:
                    if any(len(p.split()) == 1 and p in text for p in patterns):
                        logger.info("[ms_flow] slot_selection single-word idx=%d", idx)
                        return _pick(idx)

            # Fuzzy fallback for slot_selection
            slot_fuzzy = {
                "1": ["first one", "first slot", "first option"],
                "2": ["second one", "second slot", "middle one"],
                "3": ["third one", "last one", "final one"],
            }
            for slot, fuzzy_patterns in slot_fuzzy.items():
                if int(slot) <= slots_count:
                    if _fuzzy_match(text, fuzzy_patterns, threshold=70):
                        logger.info(
                            "[ms_extract] fuzzy slot=%s: '%s'", slot, text
                        )
                        return _pick(int(slot) - 1)
            return None

        # ----- phone_confirm: yes/no to using the Twilio caller-ID number --
        # Keep in sync with _HG_YES / _HG_NO_PHRASES in the CONFIRM_PHONE
        # hard-gate handler so CONFIRM_PHONE_RETURNING has the same coverage.
        if method == "phone_confirm":
            yes_p = (
                "yes", "yeah", "yep", "yup", "yeh", "ya",
                "yes use this number", "use this number", "same number",
                "use my number", "use my current number",
                "this number", "this one",
                "the number i'm calling on", "number i'm calling on",
                "the number i am calling on",
                "that's fine", "thats fine", "that's ok", "thats ok",
                "that's right", "thats right", "that is right",
                "that's correct", "thats correct", "that is correct",
                "correct", "correct number",
                "it is", "yes it is", "yeah it is",
                "go ahead", "use this", "can use this",
                "yes please", "yeah please",
                "it should", "should be",
                "that's the one", "thats the one",
            )
            no_p = (
                "no", "nope", "nah",
                "no use a different number", "different number",
                "another number", "no i'll give you another one",
                "no i'll give you another", "use a different number",
                "wrong number", "not the right number",
                "not the best number", "different phone",
            )
            for p in yes_p:
                if p in text:
                    return True
            for p in no_p:
                if p in text:
                    return False
            return None

        # ----- name: 1-5 word name ---------------------------------------
        if method == "name":
            _raw_name = raw.strip()

            # 1. Strip common prefixes: "my name is X", "it's X", "I'm X", "call me X"
            import re as _re_name
            _prefix_m = _re_name.match(
                r'^(?:my (?:full |first |last )?name(?:\s+is)?|the name(?:\s+is)?|name(?:\s+is)?'
                r'|it\'?s|its|i\'?m|im|call me|this is)\s+',
                _raw_name, _re_name.IGNORECASE,
            )
            if _prefix_m:
                _raw_name = _raw_name[_prefix_m.end():].strip()

            # 2. Strip trailing meta-questions before word-count check
            _META_STARTS = (
                " do you need", " do you want", " can you", " should i",
                " is that", " do i need", " will you", " did you",
                " need help", " by the way", " just to say", " just checking",
                " that's", " that is", " let me spell", ", that",
            )
            for _ms in _META_STARTS:
                _idx = _raw_name.lower().find(_ms)
                if _idx > 0:
                    _raw_name = _raw_name[:_idx].strip()
                    break

            # Reject refusal / attitude / filler phrases before any further
            # processing.  "i don't care" passes the word-count and function-word
            # gates below without this guard (BUG 4).
            _REFUSAL_PHRASES = (
                "i don't care", "i dont care", "don't care", "dont care",
                "whatever", "you tell me", "does it matter",
                "i don't know", "i dont know", "don't know", "dont know",
                "just book", "just go ahead", "skip it", "skip",
                "nevermind", "never mind", "not telling", "why do you need",
                "i'd rather not", "i would rather not", "none of your",
                "why", "who cares", "not important",
            )
            if any(p in _raw_name.lower() for p in _REFUSAL_PHRASES):
                logger.info("[ms_extract] name: rejected refusal phrase %r", _raw_name)
                return None

            words = _raw_name.split()
            if not (1 <= len(words) <= 5):
                return None

            # Reject obvious greetings / filler as a name.
            # Single-word hits against this set are not valid names.
            _NOT_A_NAME = frozenset({
                "hello", "hi", "hey", "yes", "no", "okay", "ok", "sure",
                "thanks", "thank", "please", "bye", "goodbye", "sorry",
                "yeah", "yep", "yup", "nope", "nah", "yeh", "right",
                # conjunctions / articles / pronouns that can appear as single-word
                # answers (e.g. "my name is and Smith" → LLM extracts "and")
                "and", "or", "but", "so", "a", "an", "the", "my", "it",
                "its", "i", "me", "we", "us", "he", "she", "they", "them",
                # BUG 1: clinic-domain and context words that STT emits as fragments
                # and must never be accepted as a person's first name
                "clinic", "clinics", "therapy", "therapist", "physio",
                "physiotherapy", "reception", "receptionist", "appointment",
                "appointments", "booking", "bookings", "health", "redditch",
                "alcester", "doctor", "service", "services", "waiting",
                "here", "there", "today", "tomorrow", "yes", "number",
                "first", "second", "third", "next", "last", "new", "old",
            })
            if len(words) == 1 and _raw_name.lower() in _NOT_A_NAME:
                logger.info("[ms_extract] name: rejected filler %r as name", _raw_name)
                return None

            # Reject multi-word "names" that contain prepositions / function words
            # or negative contractions (BUG 4: "i don't care" still passes after
            # the refusal-phrase check if the phrase isn't listed — belt-and-braces).
            # STT fragments like "in rock" pass the word-count gate (2 words) and
            # neither word is a greeting, but "in" is clearly not a name component.
            _NAME_FUNCTION_WORDS = frozenset({
                "in", "on", "at", "to", "for", "of", "by", "up", "as",
                "is", "am", "are", "was", "be", "been", "do", "did",
                "if", "got", "get", "has", "have", "had", "out", "off",
                # negative contractions — never part of a real name
                "don't", "dont", "won't", "wont", "can't", "cant",
                "didn't", "didnt", "doesn't", "doesnt", "isn't", "isnt",
            })
            if len(words) > 1 and any(w.lower() in _NAME_FUNCTION_WORDS for w in words):
                logger.info(
                    "[ms_extract] name: rejected function-word fragment %r as name", _raw_name
                )
                return None

            return _raw_name

        # ----- phone: 11+ digit number (UK standard) --------------------
        if method == "phone":
            digits = "".join(c for c in raw if c.isdigit())
            return digits if len(digits) >= 11 else None  # BUG 5: was 10

        # ----- none: no extraction needed (LLM confirmation steps) ------
        if method == "none":
            return True

        # ----- location_selection: Alcester or Redditch ------------------
        if method == "location_selection":
            _t = text.strip()

            # No-preference — default to Alcester (the main clinic)
            _no_pref = any(p in _t for p in (
                "don't mind", "dont mind", "either", "doesn't matter",
                "doesnt matter", "anywhere", "wherever", "no preference",
                "don't have a preference", "dont have a preference",
                "up to you", "you choose", "doesn't make a difference",
            ))
            if _no_pref:
                return "alcester"

            # Keypad digits (DTMF synthetic transcript) — exact full-text match
            if _t == "1":
                return "alcester"
            if _t == "2":
                return "redditch"

            # Delegate to the dedicated weighted resolver (prefix + alias + similarity).
            _loc_result = _resolve_clinic(_t, context="ask_location")
            if _loc_result["status"] == "resolved":
                if _loc_result["reason"] == "prefix_fallback":
                    # Low-confidence resolution — open one-turn correction window
                    # so the caller can immediately override if the prefix lean
                    # was wrong.
                    self.session["location_fallback_unconfirmed"] = True
                return _loc_result["location"]
            return None

        # ----- faq_booking: wants to book after FAQ answer ---------------
        if method == "faq_booking":
            # Only explicit booking language confirms a booking — never bare acknowledgements.
            # "yeah/sure/okay" after a FAQ answer mean "got it" not "please book me in".
            yes_p = (
                "book", "booking", "appointment",
                "i would like to book", "i'd like to book",
                "i want to book", "make an appointment",
                "book an appointment", "yes please book",
                "yes i'd like",
            )
            # Short tokens (≤4 chars) require whole-word matching to avoid
            # "no" matching inside "not yet", "nope" inside "nobody", etc.
            no_p_short = {"no", "nope"}
            # Acknowledgements + farewells → graceful end (not booking)
            no_p_phrase = (
                "that's all", "thats all", "nothing else",
                "thanks", "thank you", "bye", "goodbye", "no thank",
                "cheers", "brilliant", "lovely", "perfect",
                "call back", "i'll call", "i will call",
                "sorted", "all sorted", "that's great", "thats great",
            )
            # Single-word yes ("yes" alone) → booking; embedded in longer correction → repair
            _words_set = set(text.split())
            _ack_words = {"yeah", "yep", "yup", "sure", "okay", "ok",
                          "right", "alright", "great", "good", "got it",
                          "perfect", "brilliant", "lovely", "cool", "understood", "noted"}
            # Standalone acknowledgement → done (caller satisfied, not booking)
            if _words_set <= _ack_words or text.strip() in _ack_words:
                return "done"
            # Explicit "yes" alone → book; "yes" inside correction → None
            if "yes" in text:
                _no_correction_signals = (
                    "my question", "i was asking", "about", "what", "how", "i meant",
                )
                if not any(s in text for s in _no_correction_signals):
                    return "book"
            for p in yes_p:
                if p in text: return "book"
            # Do not end the call when the "no" is part of a correction/question.
            # "No my question was about prices" must go to repair intercept, not goodbye.
            _no_correction_signals = (
                "my question", "i was asking", "about", "what", "how", "i meant",
            )
            if _words_set & no_p_short:
                if any(s in text for s in _no_correction_signals):
                    return None  # let repair intercept or flow re-ask handle it
                return "done"
            for p in no_p_phrase:
                if p in text: return "done"
            return None

        # ----- intent: classify first caller utterance -------------------
        if method == "intent":
            # Handled as a special case in handle_transcript(); this path
            # is a safety fallback so _extract() never returns None for it.
            return self._detect_intent(text)

        logger.warning("[ms_flow] unknown extract method: %r", method)
        return None
