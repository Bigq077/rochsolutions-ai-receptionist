# app/intent_analyzer.py
"""
Opening-intent analyzer for the Susie AI receptionist.

Detects returning-patient intent and strong rebook signals so the booking
flow can fast-track appropriately, skipping slots that are redundant for
patients who've been before.

KNOWN_PRACTITIONERS can be overridden at deploy time via the env var
KNOWN_PRACTITIONERS (comma-separated, case-insensitive).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Practitioners list — loadable from env var at module import time
# ---------------------------------------------------------------------------

def _load_practitioners() -> list:
    """
    Load KNOWN_PRACTITIONERS from env var (comma-separated) or fall back
    to the hardcoded default.  Parsed once at import time.
    """
    env_val = os.getenv("KNOWN_PRACTITIONERS", "")
    if env_val.strip():
        return [p.strip() for p in env_val.split(",") if p.strip()]
    return ["Mark", "Dr Smith"]  # MARK_REVIEW: update with actual practitioners


KNOWN_PRACTITIONERS: list = _load_practitioners()

# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def analyze_opening_intent(utterance: str) -> dict:
    """
    Classify the caller's opening utterance for returning-patient signals.

    Returns a dict:
        {
            "is_returning_patient": bool,
            "requested_practitioner": str | None,
            "has_strong_rebook_intent": bool,
            "confidence": "high" | "low",
        }

    Always returns a dict — never raises.
    Uses claude-sonnet-4-20250514 (sync Anthropic client) — must be called
    via asyncio.to_thread() from an async context.
    """
    import anthropic

    default = {
        "is_returning_patient":    False,
        "requested_practitioner":  None,
        "has_strong_rebook_intent": False,
        "confidence":              "low",
    }
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            temperature=0,
            messages=[{
                "role": "user",
                "content": (
                    f'Analyze this caller\'s opening utterance.\n\n'
                    f'Utterance: "{utterance}"\n\n'
                    f'Rules:\n'
                    f'- is_returning_patient: True ONLY if they explicitly say they are returning\n'
                    f'  ("I\'m a returning patient", "I\'ve been before", "I come regularly",\n'
                    f'   "I was there last month"). Do NOT infer from symptoms or tone.\n'
                    f'- requested_practitioner: exact name mentioned, or null\n'
                    f'- has_strong_rebook_intent: True if "rebook", "same again",\n'
                    f'  "regular appointment", "book again", "come back" are present\n'
                    f'- confidence: "high" if clear signals, "low" if uncertain\n\n'
                    f'Return ONLY valid JSON, no commentary:\n'
                    f'{{"is_returning_patient": bool, "requested_practitioner": str|null,\n'
                    f'  "has_strong_rebook_intent": bool, "confidence": "high"|"low"}}'
                ),
            }],
        )
        text = response.content[0].text.strip()
        result = json.loads(text)
        for key in default:
            if key not in result:
                return default
        return result
    except Exception as exc:
        logger.error("analyze_opening_intent failed: %r", exc)
        return default


def is_known_practitioner(name: str | None) -> bool:
    """
    Return True if name matches (case-insensitively) a KNOWN_PRACTITIONER.
    """
    if not name:
        return False
    name_lower = name.strip().lower()
    return any(p.lower() == name_lower for p in KNOWN_PRACTITIONERS)
