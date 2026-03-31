# app/sidebar_handler.py
"""
Sidebar detection: classifies whether a caller's utterance is an off-flow
question (sidebar) rather than an attempt to answer the current booking slot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# What each state is currently trying to collect — shown to the classifier
# so it understands what "on-flow" means for that state.
_STATE_SLOT_DESCRIPTIONS: dict[str, str] = {
    "TRIAGE":            "the caller's general intent (book, reschedule, cancel, or ask a question)",
    "ASK_LOCATION":      "which clinic location the caller wants (Alcester or Redditch)",
    "BOOK_PATIENT_TYPE": "whether the caller is a new or returning patient",
    "BOOK_REASON":       "the reason for the appointment / medical complaint",
    "BOOK_INTAKE":       "a follow-up clinical detail about the caller's condition",
    "BOOK_RECOMMEND":    "confirmation that the caller accepts the recommended service",
    "BOOK_TIME_PREF":    "the caller's preferred day or time for the appointment",
    "BOOK_PICK_SLOT":    "which of the three offered time slots the caller prefers (1, 2, or 3)",
    "BOOK_NAME":         "the caller's full name for the booking",
    "BOOK_PHONE":        "the caller's mobile phone number",
    "BOOK_CONFIRM":      "a yes/no confirmation to finalise the booking",
    "RESCH_NAME":        "the caller's full name to look up their existing booking",
    "RESCH_PHONE":       "the caller's phone number to find their existing booking",
    "RESCH_BOOK_BACK":   "whether the caller wants to book a new appointment after cancelling",
    "RESCH_SAME_PROBLEM": "whether the reschedule is for the same problem or a new one",
    "CANCEL_OR_REBOOK":  "whether the caller wants to cancel or rebook their appointment",
    "RESCH_ORIGINAL":    "the caller's original appointment details",
    "RESCH_NEW_PREF":    "the caller's preferred new time for rescheduling",
    "RESCH_PICK_SLOT":   "which of the offered reschedule slots the caller prefers",
    "RESCH_CONFIRM":     "a yes/no confirmation for the reschedule",
    "RESCH_PHONE_FALLBACK": "the caller's phone number (fallback attempt)",
    "INS_EXPLAIN":       "acknowledgement of the insurance explanation",
    "INS_COLLECT_INSURER": "the name of the caller's insurance provider",
    "INS_BUPA_RESPONSE": "the caller's response to the Bupa insurance information",
    "INS_COLLECT_POLICY": "the caller's insurance policy number",
    "INSURANCE_PROVIDER": "the caller's insurance provider details",
    "FAQ_DETOUR":        "the caller's question or instruction to continue booking",
    "MANUAL_CAPTURE":    "a yes/no to pass collected info to the clinic team manually",
}

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


async def detect_sidebar(utterance: str, current_state: str) -> bool:
    """
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
            model="claude-sonnet-4-20250514",
            max_tokens=10,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip().upper()
        return answer.startswith("YES")
    except Exception as exc:
        logger.error("sidebar_handler.detect_sidebar failed: %r", exc)
        return False
