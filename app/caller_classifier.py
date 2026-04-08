"""
app/caller_classifier.py
------------------------
Caller type detection and professional caller flow.

classify_caller() — synchronous, never raises, always returns a dict.
run_professional_flow() — async, handles one turn of the professional flow.

Professional flow session keys are all prefixed `prof_` to avoid
collision with the booking flow:
  prof_name, prof_callback, prof_message, prof_flow_step
"""
from __future__ import annotations

import anthropic
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_log = logging.getLogger(__name__)

_LOG_DIR = Path("logs")


# ---------------------------------------------------------------------------
# Caller classifier
# ---------------------------------------------------------------------------

def classify_caller(utterance: str) -> dict:
    """
    Returns {"type": "patient"|"professional"|"unknown",
             "confidence": "high"|"low", "intent": str}
    Always returns a dict — never raises.
    """
    default = {"type": "unknown", "confidence": "low", "intent": ""}
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            temperature=0,
            messages=[{"role": "user", "content": f"""Classify this caller utterance.

Utterance: "{utterance}"

Professional signals: role introduction ("I'm calling from Dr X's surgery"),
words like "referral", "regarding a patient", "insurance", company/org mention.

Patient signals: symptom description, personal appointment intent, first-person name.

Return ONLY valid JSON, no commentary:
{{"type": "patient"|"professional"|"unknown", "confidence": "high"|"low", "intent": "<short description>"}}

If unclear, use "unknown" with "low" confidence."""}]
        )
        text = response.content[0].text.strip()
        if not text:
            # Empty model response — treat as unknown, no error log
            return default
        result = json.loads(text)
        for key in ["type", "confidence", "intent"]:
            if key not in result:
                return default
        if result["type"] not in ("patient", "professional", "unknown"):
            return default
        return result
    except Exception as e:
        logging.getLogger(__name__).error("classify_caller failed: %s", e)
        return default


# ---------------------------------------------------------------------------
# Professional caller flow
# ---------------------------------------------------------------------------

_PROF_QUESTIONS = [
    "Of course — could I take your name please?",
    "Thanks — and what's the best number to reach you on?",
    "Great — and what's the message you'd like to leave?",
]


async def run_professional_flow(
    session: Dict[str, Any],
    tts_fn,
    utterance: str,
    stt_fn=None,  # not used — STT is always running in this pipeline
) -> None:
    """
    Handle one turn of the professional caller slot-collection flow.

    States (prof_flow_step):
      0 — collecting prof_name       (question already spoken on entry)
      1 — collecting prof_callback   (asks for callback number)
      2 — collecting prof_message    (asks for message)
      3 — complete                   (closing phrase already spoken)

    Uses the same slot-collect-then-ask pattern as BOOKING_FLOW:
    receive utterance → store slot → ask next question.
    """
    step = session.get("prof_flow_step", 0)

    if step == 0:
        # Collect name
        session["prof_name"] = utterance.strip()
        session["prof_flow_step"] = 1
        await tts_fn("Thanks — and what's the best number to reach you on?")

    elif step == 1:
        # Collect callback number
        session["prof_callback"] = utterance.strip()
        session["prof_flow_step"] = 2
        await tts_fn("Great — and what's the message you'd like to leave?")

    elif step == 2:
        # Collect message → close
        session["prof_message"] = utterance.strip()
        session["prof_flow_step"] = 3
        await tts_fn(
            "Perfect, I'll pass that on. Someone will be in touch as soon as possible."
        )
        # Log and mark complete — order matters: log before clearing active flag
        _log_professional_call(session)
        session["professional_flow_active"] = False
        session["professional_flow_complete"] = True

    # step >= 3: flow already complete — ignore further utterances


# ---------------------------------------------------------------------------
# Professional call logging
# ---------------------------------------------------------------------------

def _log_professional_call(session: Dict[str, Any]) -> None:
    """
    Append one JSONL record to logs/professional_calls.jsonl.

    NOTE: This file intentionally stores PII (caller name and phone number)
    for callback purposes. Ensure it is excluded from version control and
    access-controlled in production.
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "call_sid":        session.get("call_sid"),
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "caller_name":     session.get("prof_name"),      # PII — stored for callback
            "callback_number": session.get("prof_callback"),  # PII — stored for callback
            "message":         session.get("prof_message"),
        }
        log_path = _LOG_DIR / "professional_calls.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        _log.info("[caller_classifier] professional call logged call_sid=%s", session.get("call_sid"))
    except Exception as exc:
        _log.error("[caller_classifier] _log_professional_call failed: %r", exc)
