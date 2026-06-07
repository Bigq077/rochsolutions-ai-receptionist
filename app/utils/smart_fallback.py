# app/utils/smart_fallback.py
"""
Smart fallback handler — called when a caller's input doesn't match any
happy-path branch in the current triage state.

Uses GPT-4o to generate a natural, in-character response as Susie, and
steers the caller back toward what the current state needs from them.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ============================================================================
# WHAT EACH STATE NEEDS FROM THE CALLER
# Injected into the system prompt so GPT-4o knows what to steer toward.
# ============================================================================

_STATE_NEEDS: Dict[str, str] = {
    "TRIAGE": (
        "Understand what the caller wants: book an appointment, "
        "reschedule or cancel, ask about services/prices/hours/location/insurance, "
        "or speak to a person."
    ),
    "ASK_LOCATION": (
        "Find out which clinic location they're calling about. "
        "The two options are Alcester or Redditch. "
        "Gently steer them to name one of those two."
    ),
    "BOOK_PATIENT_TYPE": (
        "Find out whether they are a new patient or a returning patient. "
        "Rephrase the question — don't repeat it word-for-word."
    ),
    "BOOK_REASON": (
        "Find out what they want to be seen for — their medical complaint "
        "or the type of appointment they want."
    ),
    "BOOK_INTAKE": (
        "You've asked a clinical follow-up question. Accept what they say naturally "
        "and move forward — there's no wrong answer here."
    ),
    "BOOK_RECOMMEND": (
        "You've recommended a service and quoted the price. "
        "You need to know whether they'd like to go ahead and book it. "
        "Re-ask in a different way — don't repeat 'say yes or no' verbatim."
    ),
    "BOOK_TIME_PREF": (
        "Find out when they'd like to come in — a specific day, time of day, "
        "or a general preference like 'mornings' or 'next week'."
    ),
    "BOOK_PICK_SLOT": (
        "You've offered three appointment times. "
        "They need to choose one by saying 1, 2, or 3. "
        "Remind them of the slots briefly and ask again."
    ),
    "BOOK_NAME": (
        "Get their full name for the booking. "
        "Ask clearly and naturally."
    ),
    "BOOK_PHONE": (
        "Get their mobile phone number so the clinic can reach them. "
        "Ask them to say it clearly."
    ),
    "BOOK_CONFIRM": (
        "You've given all the booking details. "
        "You need a yes to confirm, or a no to cancel. "
        "Rephrase the confirmation prompt — don't repeat it word-for-word."
    ),
    "RESCH_NAME": (
        "Get their full name so you can look up their existing booking."
    ),
    "RESCH_PHONE": (
        "Get the phone number the booking was originally made with."
    ),
    "RESCH_BOOK_BACK": (
        "Ask whether they'd like to book a new appointment. "
        "They need to say yes or no — rephrase naturally."
    ),
    "RESCH_SAME_PROBLEM": (
        "Ask whether they're coming back for the same problem as before, "
        "or something different. Rephrase — don't repeat verbatim."
    ),
    "CANCEL_OR_REBOOK": (
        "Ask whether they want to cancel the appointment outright, "
        "or book a new slot instead. Rephrase clearly."
    ),
    "INS_EXPLAIN": (
        "You're about to explain the insurance process. "
        "Ask which insurer they're with."
    ),
    "INS_COLLECT_INSURER": (
        "Find out which insurance company they're with. "
        "Give examples: AXA, Vitality, Aviva, WPA."
    ),
    "INS_BUPA_RESPONSE": (
        "You've explained that Bupa isn't accepted. "
        "Ask whether they'd still like to book as a private patient, "
        "or whether they'd prefer time to think it over. Rephrase warmly."
    ),
    "INS_COLLECT_POLICY": (
        "Ask for their insurance policy number. Reassure them it's optional — "
        "they can skip if they don't have it to hand."
    ),
    "FAQ_DETOUR": (
        "The caller asked a question mid-booking. "
        "Answer briefly if you can, then offer to continue the booking "
        "or ask if they have another question."
    ),
    "MANUAL_CAPTURE": (
        "You need to confirm their details so you can pass them to the clinic team. "
        "Ask them to say yes to confirm, or no if they'd like to do something else."
    ),
}

_DEFAULT_STATE_NEED = (
    "Help the caller and guide them naturally toward what they need."
)


# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def smart_fallback(
    history: List[Dict[str, str]],
    current_state: str,
    context: Dict[str, Any],
) -> str:
    """
    Generate a natural, in-character recovery response using GPT-4o when
    the caller's input doesn't match any happy-path branch in the current state.

    Args:
        history:       session["conversation_history"] — list of {role, content}
        current_state: the current triage state string (e.g. "BOOK_CONFIRM")
        context:       dict with at least "clinic_name" and optionally "location"

    Returns:
        A short spoken response (1–3 sentences) to play to the caller.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("smart_fallback: openai package not available")
        return "Sorry — could you say that again?"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("smart_fallback: OPENAI_API_KEY not set, using generic response")
        return "Sorry — I didn't quite catch that. Could you say that again?"

    client = AsyncOpenAI(api_key=api_key)

    clinic_name = context.get("clinic_name", "the clinic")
    location    = context.get("location", "")
    location_clause = f" ({location.title()} clinic)" if location else ""
    state_need  = _STATE_NEEDS.get(current_state, _DEFAULT_STATE_NEED)

    system_prompt = (
        f"You are Susie, the AI receptionist for {clinic_name}{location_clause}.\n\n"
        "Personality: warm, calm, professional, empathetic. "
        "Speak naturally — like a real human receptionist, not a robot or a menu system.\n\n"
        "Rules:\n"
        "- Never use lists or bullet points.\n"
        "- Keep responses short: 1 to 3 sentences maximum.\n"
        "- Never say 'I didn't understand you' — just naturally re-ask.\n"
        "- Never repeat the exact same wording you used before.\n"
        "- Never diagnose or give medical advice.\n"
        "- If there is any clinical emergency, tell the caller to call 999 or go to A&E.\n\n"
        f"Current conversation state: {current_state}\n"
        f"What you need from the caller next: {state_need}\n\n"
        "The caller said something you couldn't match to an expected response. "
        "Acknowledge what they said naturally if possible, then gently steer them "
        "back toward what you need. Do not be robotic. Do not repeat the last prompt verbatim."
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Include the last 6 turns of conversation history for context
    if history:
        for turn in history[-6:]:
            role = turn.get("role", "user")
            oai_role = "assistant" if role == "assistant" else "user"
            content = (turn.get("content") or "").strip()
            if content:
                messages.append({"role": oai_role, "content": content})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=100,
            temperature=0.65,
        )
        reply = (response.choices[0].message.content or "").strip()
        if reply:
            logger.info(f"smart_fallback [{current_state}]: {reply[:80]}...")
            return reply
    except Exception as e:
        logger.error(f"smart_fallback GPT-4o error [{current_state}]: {e!r}")

    # Hard fallback if GPT fails
    return "Sorry — I didn't quite catch that. Could you say that again?"
