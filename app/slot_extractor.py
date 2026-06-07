# app/slot_extractor.py
"""
Slot pre-filler: extracts booking slots from a caller's utterance before the
state machine runs, so Susie skips asking for information already provided.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton — same pattern used by conversation.py / llm_stream.py
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is None:
        import os
        from anthropic import AsyncAnthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        _client = AsyncAnthropic(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------
_EXTRACTION_PROMPT = """\
Extract booking information from this phone call utterance.

Only extract information that is EXPLICITLY stated. Do NOT infer or imply values.

Utterance: "{utterance}"

Return a JSON object with ONLY these fields. Use null for any field not clearly present:
{{
  "reason_for_visit": null,
  "preferred_day": null,
  "preferred_time": null,
  "patient_name": null,
  "phone_number": null
}}

Rules:
- Only extract if the value is explicitly and unambiguously stated — not implied
- reason_for_visit: a medical complaint, symptom, or condition (e.g. "shoulder pain")
- preferred_day: a specific day or relative reference (e.g. "Monday", "this week", "tomorrow")
- preferred_time: a time-of-day preference (e.g. "morning", "2pm", "afternoon")
- patient_name: a full name only if the caller clearly states it as their own name
- phone_number: a phone number only if explicitly stated
- Return null for any slot not clearly present — do NOT guess
- No markdown, no commentary — only the raw JSON object\
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_slots_from_utterance(utterance: str, current_slots: dict) -> dict:
    """
    Extract booking slots from a caller's utterance.

    Returns {slot_name: extracted_value} for slots confidently found.
    Slot names use extractor convention:
        reason_for_visit, preferred_day, preferred_time, patient_name, phone_number

    Returns empty dict if nothing confident is found or on ANY exception —
    the state machine must continue unaffected.

    Targets <800 ms: max_tokens=150, temperature=0.
    """
    try:
        client = _get_client()
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            temperature=0,
            messages=[{
                "role": "user",
                "content": _EXTRACTION_PROMPT.format(utterance=utterance),
            }],
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)

        result: dict = {}
        for key, value in data.items():
            if value is not None and str(value).strip():
                result[key] = str(value).strip()
        return result

    except Exception as exc:
        logger.error("slot_extractor.extract_slots_from_utterance failed: %r", exc)
        return {}


async def generate_prefill_ack(slot_display_name: str, slot_value: str) -> str:
    """
    Generate a warm 1-sentence acknowledgement for a pre-filled slot.

    max_tokens=40, temperature=0.3 as specified.
    Returns empty string on failure — caller must handle gracefully.
    """
    try:
        client = _get_client()
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=40,
            temperature=0.3,
            messages=[{
                "role": "user",
                "content": (
                    f"Write a single warm sentence (no quotes, no markdown) "
                    f"acknowledging that the caller mentioned '{slot_value}' "
                    f"as their {slot_display_name.replace('_', ' ')}. "
                    f"Keep it under 15 words."
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("slot_extractor.generate_prefill_ack failed: %r", exc)
        return ""
