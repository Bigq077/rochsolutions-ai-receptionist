# app/call_summary.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List


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
            # Best-effort: many flows store selected slot in session keys; callers can also store it in collected.
            "selected_slot": (
                collected.get("selected_slot")
                or session.get("selected_slot_label")
                or session.get("selected_slot")
                or session.get("SELECTED_SLOT_LABEL_KEY")
                or session.get("SELECTED_SLOT_KEY")
            ),
            "calendar": {
                "event_id": session.get("calendar_event_id"),
                "status": session.get("calendar_status"),  # created/patched/failed/manual_needed
                "error": session.get("calendar_error"),
            },
        },
        "insurance": {
            "insurer_name": session.get("insurer_name") or collected.get("insurer"),
            "acceptance": session.get("insurance_acceptance"),  # accepted/not_accepted/unknown
        },
        "faq": faq_turns,
        "handoff": {
            # Some flows use manual_reschedule/manual_reason; we unify into this handoff block.
            "manual_followup_needed": bool(
                session.get("manual_followup_needed")
                or session.get("manual_reschedule")
                or session.get("manual_booking")
            ),
            "reason": session.get("manual_followup_reason")
            or session.get("manual_reason")
            or session.get("manual_booking_reason"),
        },
    }
    return summary


def summary_to_sheet_row(summary: dict[str, Any]) -> List[Any]:
    """
    Convert structured summary -> flat row for Google Sheets.
    Keep the order in sync with your Sheet header row.
    Recommended header order (20 cols):
      1 ended_at_utc
      2 call_sid
      3 session_id
      4 intent
      5 flow_state_final
      6 patient_name
      7 patient_phone
      8 new_or_returning
      9 service
      10 reason
      11 time_preference
      12 selected_slot_label_or_time
      13 calendar_status
      14 calendar_event_id
      15 insurer_name
      16 insurance_acceptance
      17 manual_followup_needed
      18 manual_followup_reason
      19 faq_json
      20 calendar_error
    """
    meta = summary.get("meta", {}) or {}
    patient = summary.get("patient", {}) or {}
    appt = summary.get("appointment", {}) or {}
    cal = appt.get("calendar", {}) or {}
    ins = summary.get("insurance", {}) or {}
    handoff = summary.get("handoff", {}) or {}
    faq_turns = summary.get("faq", []) or []

    faq_json = json.dumps(faq_turns, ensure_ascii=False)
    slot_label = _slot_label_from_selected(appt.get("selected_slot"))

    return [
        meta.get("ended_at_utc"),
        meta.get("call_sid"),
        meta.get("session_id"),
        appt.get("intent"),
        meta.get("flow_state_final"),
        patient.get("name"),
        patient.get("phone"),
        patient.get("new_or_returning"),
        appt.get("service"),
        appt.get("reason"),
        appt.get("time_preference"),
        slot_label,
        cal.get("status"),
        cal.get("event_id"),
        ins.get("insurer_name"),
        ins.get("acceptance"),
        bool(handoff.get("manual_followup_needed")),
        handoff.get("reason"),
        faq_json,
        (cal.get("error") or "")[:250],
    ]


def _slot_label_from_selected(selected: Any) -> str:
    """
    Make selected slot human-readable for Sheets.
    Accepts:
      - string labels
      - dict with start/end keys
      - anything else -> ""
    """
    if selected is None:
        return ""

    if isinstance(selected, str):
        return selected.strip()

    if isinstance(selected, dict):
        start = (selected.get("start") or "").strip()
        end = (selected.get("end") or "").strip()
        label = (selected.get("label") or "").strip()
        if label:
            return label
        if start and end:
            return f"{start} to {end}"
        if start:
            return start
        return ""

    return ""
