# app/utils/intent_classifier.py
"""
Intent classification layer for Susie the AI receptionist.

Runs BEFORE the triage state machine on every caller turn.
Uses GPT-4o-mini to classify intent, extract entities, and detect
emotional tone from raw speech.  On failure it returns None so the
caller can fall through to the existing keyword-based logic.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OUTPUT SCHEMA (returned as a plain dict — never raises, always returns)
# ---------------------------------------------------------------------------
# {
#   "primary_intent": str,          one of the INTENTS list
#   "confidence":     float,        0.0 – 1.0
#   "extracted_entities": {         any subset of:
#     "patient_name":       str,
#     "phone_number":       str,
#     "condition":          str,
#     "location_preference": str,
#     "time_preference":    str,
#     "insurer_name":       str,
#     "appointment_date":   str,
#   },
#   "emotional_tone": str,          one of TONES
#   "suggested_response_if_unclear": str,   (populated when confidence < 0.6)
# }

INTENTS = [
    "book_appointment",
    "reschedule",
    "cancel",
    "price_enquiry",
    "insurance_enquiry",
    "hours_enquiry",
    "location_enquiry",
    "condition_question",
    "confirm_yes",
    "confirm_no",
    "request_human",
    "general_question",
    "unclear",
]

TONES = ["neutral", "anxious", "frustrated", "urgent", "confused"]

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an intent classifier for Susie, the AI receptionist at Theorem Health & Wellness — a UK physiotherapy clinic with two locations: Alcester and Redditch.

Your job is to analyse the caller's speech and return a JSON object with exactly these fields:

{
  "primary_intent": "<one of the allowed intents>",
  "confidence": <float 0.0–1.0>,
  "extracted_entities": {
    "patient_name": "<full name if mentioned, else null>",
    "phone_number": "<phone number if mentioned, else null>",
    "condition": "<their medical complaint in plain English if described, else null>",
    "location_preference": "<alcester or redditch if mentioned, else null>",
    "time_preference": "<day/time preference if mentioned, else null>",
    "insurer_name": "<insurance company name if mentioned, else null>",
    "appointment_date": "<specific date or relative date like 'next Tuesday' if mentioned, else null>"
  },
  "emotional_tone": "<one of the allowed tones>",
  "suggested_response_if_unclear": "<one natural sentence Susie should say if confidence is below 0.6, else null>"
}

Allowed primary_intent values:
- book_appointment  — caller wants to make a new appointment
- reschedule        — caller wants to move an existing appointment
- cancel            — caller wants to cancel an existing appointment
- price_enquiry     — asking about costs, fees, prices
- insurance_enquiry — asking about insurance, cover, claims, Bupa, AXA etc.
- hours_enquiry     — asking about opening hours, when the clinic is open
- location_enquiry  — asking about the clinic address, directions, parking
- condition_question — describing a physical symptom or asking about a treatment
- confirm_yes       — caller is confirming, agreeing, saying yes
- confirm_no        — caller is declining, disagreeing, saying no
- request_human     — caller wants to speak to a real person
- general_question  — any other question not covered above
- unclear           — cannot determine intent with confidence

Allowed emotional_tone values: neutral, anxious, frustrated, urgent, confused

IMPORTANT CONTEXT — understand these UK patterns:

Booking phrases (→ book_appointment):
"I'd like to come in", "can I get seen", "I need an appointment", "I want to book",
"can I make an appointment", "I'd like to see someone", "can I get booked in",
"I was wondering if I could book", "looking to get an appointment"

Condition descriptions (→ condition_question, extract "condition"):
"bad back", "bad knee", "dodgy shoulder", "shooting pain", "my back's been playing up",
"I've done something to my...", "I've been having trouble with my...",
"stiff neck", "it's been really sore", "can't really move it properly"

Hesitant speech (do not penalise confidence for these — classify intent normally):
"um", "well", "I was wondering if", "I'm not sure if you can help but",
"sorry to bother you", "I don't know if this is the right number"

Yes patterns (→ confirm_yes):
"yeah", "yep", "yes please", "that sounds good", "go ahead", "sounds great",
"perfect", "that'll do", "I'll take that", "that works"

No patterns (→ confirm_no):
"no thanks", "not right now", "I'll leave it", "maybe another time",
"actually no", "on second thoughts no"

For suggested_response_if_unclear: write one natural sentence as Susie would say it — warm, British, conversational. Do not say "I didn't understand". Instead, gently ask the caller what they need.

Return ONLY valid JSON. No explanation, no markdown, no extra text.
"""

# ---------------------------------------------------------------------------
# MAIN FUNCTION
# ---------------------------------------------------------------------------

async def classify_intent(
    text: str,
    conversation_history: List[Dict[str, str]],
    current_state: str,
    session: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Classify caller intent using GPT-4o-mini.

    Returns a structured dict (see schema above), or None if the API call
    fails for any reason — callers must handle None gracefully.
    """
    if not text or not text.strip():
        return None

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("intent_classifier: OPENAI_API_KEY not set")
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("intent_classifier: openai package not available")
        return None

    client = AsyncOpenAI(api_key=api_key)

    # Build the user message — include state context + last 4 history turns
    state_hint = f"[Current conversation state: {current_state}]\n"

    history_lines: List[str] = []
    for turn in (conversation_history or [])[-4:]:
        role = "Caller" if turn.get("role") == "user" else "Susie"
        content = (turn.get("content") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")
    history_block = "\n".join(history_lines)

    user_message = (
        f"{state_hint}"
        + (f"Recent conversation:\n{history_block}\n\n" if history_block else "")
        + f"Caller just said: \"{text.strip()}\""
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.1,   # Low temp for consistent classification
        )

        raw = (response.choices[0].message.content or "").strip()
        result: Dict[str, Any] = json.loads(raw)

        # Validate and normalise
        result["primary_intent"] = result.get("primary_intent", "unclear")
        if result["primary_intent"] not in INTENTS:
            result["primary_intent"] = "unclear"

        result["confidence"] = float(result.get("confidence", 0.5))
        result["confidence"] = max(0.0, min(1.0, result["confidence"]))

        result["emotional_tone"] = result.get("emotional_tone", "neutral")
        if result["emotional_tone"] not in TONES:
            result["emotional_tone"] = "neutral"

        # Ensure extracted_entities is always a dict
        entities = result.get("extracted_entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        # Strip null values so callers can do a simple truthiness check
        result["extracted_entities"] = {
            k: v for k, v in entities.items() if v not in (None, "", "null")
        }

        if "suggested_response_if_unclear" not in result:
            result["suggested_response_if_unclear"] = None

        return result

    except json.JSONDecodeError as e:
        logger.error("intent_classifier: JSON parse error: %r", e)
        return None
    except Exception as e:
        logger.error("intent_classifier: API error: %r", e)
        return None
