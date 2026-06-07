# app/knowledge_base.py
"""
Clinic knowledge base for answering sidebar questions mid-booking.
Every value line is marked # MARK_REVIEW — replace with real clinic data.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CLINIC_KB: dict = {
    "pricing": (  # MARK_REVIEW
        "Initial assessment: £60 (45 minutes). "
        "Follow-up session: £45 (30 minutes). "
        "Sports massage: £45 (45 minutes). "
        "Shockwave therapy: £60 (30 minutes). "
        "Rehabilitation session: £45 (45 minutes)."
    ),
    "appointment_duration": (  # MARK_REVIEW
        "Initial assessments are 45 minutes. "
        "Follow-up appointments are 30 minutes. "
        "Sports massage and shockwave sessions are 45 minutes."
    ),
    "evening_availability": (  # MARK_REVIEW
        "Evening appointments (after 5 pm) are available on Tuesdays and Thursdays. "
        "Last slot is at 7 pm."
    ),
    "weekend_availability": (  # MARK_REVIEW
        "Saturday morning appointments are available from 9 am to 1 pm. "
        "No Sunday appointments."
    ),
    "parking": (  # MARK_REVIEW
        "Free on-site parking is available directly outside both clinic locations."
    ),
    "what_to_bring_first_visit": (  # MARK_REVIEW
        "For your first visit please bring any relevant X-rays, MRI scans, or letters "
        "from your GP or consultant. Wear comfortable, loose-fitting clothing so the "
        "physiotherapist can assess the affected area."
    ),
    "cancellation_policy": (  # MARK_REVIEW
        "We ask for at least 24 hours' notice to cancel or reschedule. "
        "Cancellations with less than 24 hours' notice may incur the full session fee."
    ),
    "clinic_address": (  # MARK_REVIEW
        "Alcester clinic: 1 Example Street, Alcester, B49 5AA. "
        "Redditch clinic: 2 Sample Road, Redditch, B98 7XY."
    ),
}

_KB_PROMPT = """\
You are Susie, a phone receptionist for a physiotherapy clinic.
Answer the caller's question using ONLY the information in the knowledge base below.
If the question is not covered by the knowledge base, respond with exactly:
"I'm not sure about that, but I can get someone to call you back."
Do NOT invent facts. Keep the answer brief (one or two sentences).

Knowledge base:
{kb_text}

Caller's question: "{question}"

Answer:\
"""

_client = None


def _get_client():
    global _client
    if _client is None:
        import os
        from anthropic import AsyncAnthropic
        _client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


async def answer_sidebar(question: str, kb: dict) -> str:
    """
    Answer an off-flow question using the knowledge base.
    Returns a plain string suitable for TTS.
    Falls back to a safe "not sure" message on any exception.
    """
    try:
        kb_text = "\n".join(f"- {k}: {v}" for k, v in kb.items())
        prompt  = _KB_PROMPT.format(kb_text=kb_text, question=question)
        client  = _get_client()
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("knowledge_base.answer_sidebar failed: %r", exc)
        return "I'm not sure about that, but I can get someone to call you back."
