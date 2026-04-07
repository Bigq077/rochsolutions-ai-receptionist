# app/sidebar_handler.py
"""
Sidebar detection: classifies whether a caller's utterance is an off-flow
question (sidebar) rather than an attempt to answer the current booking slot.

detect_sidebar()       — legacy YES/NO classifier (Brain pipeline)
detect_sidebar_topic() — topic-aware classifier (Media Streams pipeline)
                         Returns the FAQ topic key to look up, or None.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# What each state is currently trying to collect — shown to the classifier
# so it understands what "on-flow" means for that state.
_STATE_SLOT_DESCRIPTIONS: dict[str, str] = {
    "TRIAGE":                "the caller's general intent (book, reschedule, cancel, or ask a question)",
    "ASK_LOCATION":          "which clinic location the caller wants (Alcester or Redditch)",
    "DETECT_INTENT":         "what the caller wants to do today",
    "NEW_OR_RETURNING":      "whether the caller is a new or returning patient",
    "RETURNING_RECENCY":     "whether their last visit was recent or a while ago",
    "RETURNING_TREATMENT_PLAN": "whether they are still on an active treatment plan",
    "COLLECT_REASON":        "the reason for the appointment / medical complaint",
    "CONFIRM_ASSESSMENT":    "confirmation that the caller accepts the physiotherapy assessment",
    "PRESENT_DAYS":          "which of the offered appointment days the caller prefers",
    "PRESENT_TIMES":         "which of the offered appointment times the caller prefers",
    "COLLECT_NAME":          "the caller's full name for the booking",
    "COLLECT_PHONE":         "the caller's mobile phone number",
    "CONFIRM_PHONE":         "whether to use the caller's existing phone number",
    "CONFIRM_BOOKING":       "a yes/no confirmation to finalise the booking",
    "COLLECT_NAME_RETURNING": "the caller's full name to look up their existing booking",
    "COLLECT_PHONE_RETURNING": "the caller's phone number to find their existing account",
    "COLLECT_NAME_RESCHEDULE": "the caller's full name to look up their existing booking",
    "COLLECT_NAME_CANCEL":   "the caller's full name to look up the booking to cancel",
    "PRESENT_DAYS_RESCHEDULE": "which of the offered days the caller wants for rescheduling",
    "PRESENT_TIMES_RESCHEDULE": "which of the offered times the caller wants for rescheduling",
    "CONFIRM_RESCHEDULE":    "a yes/no confirmation to finalise the reschedule",
    "CONFIRM_CANCEL":        "a yes/no confirmation to cancel the appointment",
    "BOOK_PATIENT_TYPE":     "whether the caller is a new or returning patient",
    "BOOK_REASON":           "the reason for the appointment / medical complaint",
    "BOOK_INTAKE":           "a follow-up clinical detail about the caller's condition",
    "BOOK_RECOMMEND":        "confirmation that the caller accepts the recommended service",
    "BOOK_TIME_PREF":        "the caller's preferred day or time for the appointment",
    "BOOK_PICK_SLOT":        "which of the three offered time slots the caller prefers (1, 2, or 3)",
    "BOOK_NAME":             "the caller's full name for the booking",
    "BOOK_PHONE":            "the caller's mobile phone number",
    "BOOK_CONFIRM":          "a yes/no confirmation to finalise the booking",
    "RESCH_NAME":            "the caller's full name to look up their existing booking",
    "RESCH_PHONE":           "the caller's phone number to find their existing booking",
    "RESCH_BOOK_BACK":       "whether the caller wants to book a new appointment after cancelling",
    "RESCH_SAME_PROBLEM":    "whether the reschedule is for the same problem or a new one",
    "CANCEL_OR_REBOOK":      "whether the caller wants to cancel or rebook their appointment",
    "RESCH_ORIGINAL":        "the caller's original appointment details",
    "RESCH_NEW_PREF":        "the caller's preferred new time for rescheduling",
    "RESCH_PICK_SLOT":       "which of the offered reschedule slots the caller prefers",
    "RESCH_CONFIRM":         "a yes/no confirmation for the reschedule",
    "RESCH_PHONE_FALLBACK":  "the caller's phone number (fallback attempt)",
    "INS_EXPLAIN":           "acknowledgement of the insurance explanation",
    "INS_COLLECT_INSURER":   "the name of the caller's insurance provider",
    "INS_BUPA_RESPONSE":     "the caller's response to the Bupa insurance information",
    "INS_COLLECT_POLICY":    "the caller's insurance policy number",
    "INSURANCE_PROVIDER":    "the caller's insurance provider details",
    "FAQ_DETOUR":            "the caller's question or instruction to continue booking",
    "MANUAL_CAPTURE":        "a yes/no to pass collected info to the clinic team manually",
}

# All topic keys the get_clinic_info tool knows about (must stay in sync with
# the enum in receptionist_tools.py TOOL_GET_CLINIC_INFO).
_FAQ_TOPICS = [
    # Core operational topics
    "hours", "address", "transport", "parking", "prices", "insurance",
    "services", "cancellation_policy", "what_to_bring",
    # FAQ topics
    "what_is_assessment", "gp_referral", "how_many_sessions", "conditions_treated",
    "practitioners", "location_comparison", "shockwave_description", "laser_description",
    "acupuncture_description", "psychotherapy_description", "home_visits", "online_booking",
    "online_consultations", "children_policy", "first_visit", "surcharge_explained",
    "waitlist", "website", "prescribing_service", "rehabilitation",
    "between_sessions_support", "reports_letters", "insurance_claim", "accessibility",
    "contact_after_call", "payment_methods", "same_day_booking",
    "physio_vs_rehab_difference", "new_vs_returning", "running_late",
    "what_to_expect_after", "qualifications", "returning_after_discharge",
    "work_injury", "can_bring_someone", "location_inside_greig", "packages_discounts",
]

_TOPIC_CLASSIFICATION_PROMPT = """\
You are classifying a phone call utterance for a physiotherapy clinic receptionist AI.

Current booking step: {state}
What the system is trying to collect right now: {slot_description}

Caller said: "{utterance}"

If the caller is asking a question about the clinic (NOT answering the pending question),
return the single most relevant topic key from this list:

{topics}

If the caller is answering the booking question (giving a name, phone number, yes/no,
day preference, medical reason, etc.), return: none

Reply with ONLY a single topic key or "none". No explanation, no punctuation.\
"""

# Legacy YES/NO prompt — kept for the Brain pipeline
_CLASSIFICATION_PROMPT = """\
You are classifying a single phone call utterance.

Current booking state: {state}
What the system is trying to collect right now: {slot_description}

Caller said: "{utterance}"

Is this utterance a sidebar — an off-flow question or comment that is NOT \
an attempt to answer what the system is collecting?

Examples of sidebars: asking about price, parking, opening hours, what to bring, \
cancellation policy, evening/weekend availability, whether they can bring an MRI scan.

Examples of on-flow answers: giving a name, giving a phone number, saying yes/no, \
stating a day preference, stating a medical condition.

Reply with exactly one word: YES if sidebar, NO if on-flow.\
"""

_client = None


def _get_client():
    global _client
    if _client is None:
        import os
        from anthropic import AsyncAnthropic
        _client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


async def detect_sidebar_topic(utterance: str, current_state: str) -> Optional[str]:
    """
    Return a FAQ topic key if the utterance is a sidebar (off-flow question),
    or None if it's an on-flow answer or on any error.

    Uses Haiku for speed (~200-300ms). Called only on first failed extraction
    so there is zero overhead on clean turns.

    Returns None on any exception — never breaks the state machine.
    """
    try:
        slot_desc = _STATE_SLOT_DESCRIPTIONS.get(
            current_state,
            "the caller's response to the current question",
        )
        topics_str = "\n".join(f"  {t}" for t in _FAQ_TOPICS)
        prompt = _TOPIC_CLASSIFICATION_PROMPT.format(
            state=current_state,
            slot_description=slot_desc,
            utterance=utterance,
            topics=topics_str,
        )
        client = _get_client()
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip().lower().rstrip(".")
        if not answer or answer == "none":
            return None
        if answer in _FAQ_TOPICS:
            logger.info("[sidebar] topic detected: %r (state=%s)", answer, current_state)
            return answer
        logger.warning("[sidebar] unexpected topic returned: %r — ignoring", answer)
        return None
    except Exception as exc:
        logger.error("[sidebar] detect_sidebar_topic failed: %r", exc)
        return None


async def detect_sidebar(utterance: str, current_state: str) -> bool:
    """
    Legacy YES/NO classifier used by the Brain pipeline.
    Return True if the utterance is a sidebar (off-flow question).
    Return False on any exception — never break the state machine.
    """
    try:
        slot_desc = _STATE_SLOT_DESCRIPTIONS.get(
            current_state,
            "the caller's response to the current question",
        )
        prompt = _CLASSIFICATION_PROMPT.format(
            state=current_state,
            slot_description=slot_desc,
            utterance=utterance,
        )
        client = _get_client()
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip().upper()
        return answer.startswith("YES")
    except Exception as exc:
        logger.error("[sidebar] detect_sidebar failed: %r", exc)
        return False
