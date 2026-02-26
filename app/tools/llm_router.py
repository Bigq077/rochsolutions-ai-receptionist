# app/tools/llm_router.py
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, TypedDict, List

from openai import OpenAI


class LLMEntities(TypedDict, total=False):
    name: str
    phone: str
    patient_type: str
    reason: str
    time_pref: str
    original_appt: str
    new_appt: str
    insurer: str


class LLMResult(TypedDict, total=False):
    intent: str
    faq_topic: str
    confidence: float
    entities: LLMEntities
    reply: str
    follow_up_question: str


def _clinic_context(clinic: Dict[str, Any]) -> str:
    services = clinic.get("services", [])
    insurers = clinic.get("common_insurers", [])
    return "\n".join([
        f"Clinic name: {clinic.get('display_name', 'Clinic')}",
        f"Address: {clinic.get('address', '')}",
        f"Parking: {clinic.get('parking', '')}",
        f"Hours: {clinic.get('hours_summary', '')}",
        f"Pricing: {clinic.get('pricing_summary', '')}",
        f"Insurance note: {clinic.get('insurance_note', '')}",
        f"Common insurers: {', '.join(insurers) if insurers else ''}",
        f"Services: {', '.join(services) if services else ''}",
        f"Cancellation policy: {clinic.get('cancellation_policy', '')}",
        f"What to bring: {clinic.get('what_to_bring', '')}",
    ])


def _schema() -> Dict[str, Any]:
    return {
        "name": "receptionist_router",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {"type": "string", "enum": ["BOOK", "RESCHEDULE", "FAQ", "HUMAN", "MESSAGE", "OTHER"]},
                "faq_topic": {"type": "string", "enum": ["prices", "hours", "location", "insurance", "services", "policies", "first_visit", "other"]},
                "confidence": {"type": "number"},
                "entities": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "phone": {"type": "string"},
                        "patient_type": {"type": "string"},
                        "reason": {"type": "string"},
                        "time_pref": {"type": "string"},
                        "original_appt": {"type": "string"},
                        "new_appt": {"type": "string"},
                        "insurer": {"type": "string"},
                    },
                },
                "reply": {"type": "string"},
                "follow_up_question": {"type": "string"},
            },
            "required": ["intent", "faq_topic", "confidence", "entities", "reply", "follow_up_question"],
        },
    }


def _model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _looks_like_facty_detail(text: str) -> bool:
    """
    Cheap heuristics: block replies containing specific amounts/times/addresses
    unless they appear in the provided context.
    """
    if not text:
        return False
    # money, times, postcode-ish tokens
    if re.search(r"£\s*\d", text):
        return True
    if re.search(r"\b\d{1,2}(:\d{2})\b", text):  # 14:30
        return True
    if re.search(r"\b\d{1,2}\s*(am|pm)\b", text.lower()):
        return True
    return False


def _guardrail_reply(reply: str, allowed_context: str) -> str:
    """
    If model tries to include precise clinic-specific facts that aren't in context,
    replace with a safe receptionist-style response.
    """
    if not reply:
        return "I can help with that — what would you like to know?"
    if not _looks_like_facty_detail(reply):
        return reply

    # If the exact token exists in context, allow it.
    # (Simple check: if reply contains £ or time, ensure that substring appears somewhere in context)
    if "£" in reply and "£" not in allowed_context:
        return "I can explain how it works, and I can confirm exact pricing with the clinic."
    if re.search(r"\b\d{1,2}(:\d{2})\b", reply) and not re.search(r"\b\d{1,2}(:\d{2})\b", allowed_context):
        return "I can help with that — what day and time are you aiming for?"
    return reply


def route_and_answer(
    user_text: str,
    clinic: Dict[str, Any],
    current_state: str,
    last_bot_prompt: str = "",
    knowledge_snippets: Optional[List[str]] = None,
) -> LLMResult:
    ctx = _clinic_context(clinic)
    kb = "\n\n".join(knowledge_snippets or [])

    system = (
        "You are a helpful UK physiotherapy clinic receptionist.\n"
        "Hard rules:\n"
        "1) Be concise: 1–2 sentences max.\n"
        "2) You MUST only use the provided context (clinic context + knowledge snippets).\n"
        "3) If a detail is not explicitly in the provided context, say you can confirm with the clinic.\n"
        "4) No medical diagnosis. No guaranteed outcomes. Encourage booking for clinical assessment.\n"
        "5) Ask ONE follow-up question if needed.\n"
    )

    user = (
        f"CLINIC CONTEXT:\n{ctx}\n\n"
        f"KNOWLEDGE SNIPPETS:\n{kb}\n\n"
        f"CURRENT STATE: {current_state}\n"
        f"LAST BOT PROMPT: {last_bot_prompt}\n\n"
        f"USER SAID: {user_text}\n\n"
        "Return JSON that matches the schema."
    )

    client = _client()
    resp = client.responses.create(
        model=_model_name(),
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text={
            "format": {
                "type": "json_schema",
                "json_schema": _schema(),
                "strict": True,
            }
        },
    )

    out_text = ""
    for item in resp.output:
        if item.type == "message":
            for c in item.content:
                if c.type == "output_text":
                    out_text += c.text

    import json

    # Guard: malformed or empty LLM output must never crash the call
    try:
        data = json.loads(out_text) if out_text.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return {
            "intent": "OTHER",
            "faq_topic": "other",
            "confidence": 0.0,
            "entities": {},
            "reply": "",
            "follow_up_question": "",
        }

    allowed = f"{ctx}\n\n{kb}"
    data["reply"] = _guardrail_reply(data.get("reply", ""), allowed)
    data["confidence"] = float(data.get("confidence") or 0.0)
    if not isinstance(data.get("entities"), dict):
        data["entities"] = {}
    return data  # type: ignore[return-value]
