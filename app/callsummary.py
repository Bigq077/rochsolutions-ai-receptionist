# app/call_summary.py
from __future__ import annotations

from datetime import datetime
from typing import Any

def build_call_summary(session: dict[str, Any]) -> dict[str, Any]:
    """
    Pure function: returns structured JSON summary.
    No I/O here.
    """
    collected = session.get("collected", {}) or {}
    faq_turns = session.get("faq_turns", []) or []

    summary = {
        "meta": {
            "call_sid": session.get("call_sid"),
            "session_id": session.get("session_id"),
            "ended_at_utc": datetime.utcnow().isoformat() + "Z",
            "flow_state_final": session.get("state"),
        },
        "patient": {
            "name": collected.get("name"),
            "phone": collected.get("phone") or session.get("caller_phone"),
            "new_or_returning": collected.get("patient_type"),
        },
        "appointment": {
            "intent": session.get("last_intent"),
            "service": collected.get("service") or collected.get("reason"),
            "reason": collected.get("reason"),
            "notes": collected.get("notes"),
            "time_preference": collected.get("time_pref"),
            "selected_slot": collected.get("selected_slot"),
            "calendar": {
                "event_id": session.get("calendar_event_id"),
                "status": session.get("calendar_status"),  # e.g. created/patched/failed/manual_needed
                "error": session.get("calendar_error"),
            },
        },
        "insurance": {
            "insurer_name": session.get("insurer_name") or collected.get("insurer"),
            "acceptance": session.get("insurance_acceptance"),  # accepted/not_accepted/unknown
        },
        "faq": faq_turns,
        "handoff": {
            "manual_followup_needed": bool(session.get("manual_followup_needed")),
            "reason": session.get("manual_followup_reason"),
        },
    }
    return summary
