# app/tools/llm_router.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, TypedDict

from openai import OpenAI


class LLMEntities(TypedDict, total=False):
    name: str
    phone: str
    patient_type: str           # NEW / RETURNING
    reason: str
    time_pref: str
    original_appt: str
    new_appt: str
    insurer: str


class LLMResult(TypedDict, total=False):
    intent: str                 # BOOK / RESCHEDULE / FAQ / HUMAN / MESSAGE / OTHER
    faq_topic: str              # prices / hours / location / insurance / services / policies / first_visit / other
    confidence: float           # 0..1
    entities: LLMEntities
    reply: str                  # what to say to user (1-2 sentences max)
    follow_up_question: str     # if missing info, ask ONE question


def _clinic_context(clinic: Dict[str, Any]) -> str:
    """
    Keep this short. The model should only answer using this info.
    """
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
    """
    OpenAI Structured Outputs JSON Schema.
    """
    return {
        "name": "receptionist_router",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["BOOK", "RESCHEDULE", "FAQ", "HUMAN", "MESSAGE", "OTHER"],
                },
                "faq_topic": {
                    "type": "string",
                    "enum": ["prices", "hours", "location", "insurance", "services", "policies", "first_visit", "other"],
                },
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


def route_and_answer(
    user_text: str,
    clinic: Dict[str, Any],
    current_state: str,
    last_bot_prompt: str = "",
) -> LLMResult:
    """
    One call that (a) routes intent, (b) extracts entities, (c) gives a short reply.
    Uses Structured Outputs so parsing is reliable. :contentReference[oaicite:4]{index=4}
    """
    ctx = _clinic_context(clinic)

    system = (
        "You are a helpful UK physiotherapy clinic receptionist.\n"
        "Rules:\n"
        "1) Be concise: 1-2 sentences max.\n"
        "2) Only answer using the clinic context provided. If unknown, say you can confirm with the clinic.\n"
        "3) If the user is trying to book/reschedule, set intent accordingly and extract any useful details.\n"
        "4) If info is missing, ask ONE follow-up question only.\n"
        "5) Never invent addresses, prices, or hours not in context.\n"
    )

    user = (
        f"CLINIC CONTEXT:\n{ctx}\n\n"
        f"CURRENT STATE: {current_state}\n"
        f"LAST BOT PROMPT: {last_bot_prompt}\n\n"
        f"USER SAID: {user_text}\n\n"
        "Return JSON that matches the schema."
    )

    client = _client()
    model = _model_name()

    # Responses API (recommended modern endpoint) :contentReference[oaicite:5]{index=5}
    resp = client.responses.create(
        model=model,
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

    # The SDK returns the parsed JSON as text in output; we read it safely.
    # Different SDK versions surface it slightly differently, so we extract robustly.
    out_text = ""
    for item in resp.output:
        if item.type == "message":
            for c in item.content:
                if c.type == "output_text":
                    out_text += c.text

    # With strict json_schema, out_text is guaranteed valid JSON matching schema.
    import json
    data = json.loads(out_text)

    # Defensive cleanup
    data["confidence"] = float(data.get("confidence") or 0.0)
    if not isinstance(data.get("entities"), dict):
        data["entities"] = {}
    return data  # type: ignore[return-value]

