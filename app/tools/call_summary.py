 
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

    # Prefer "ended_at_utc" already set by your /twilio/status handler if you add it,
    # otherwise compute now.
    ended_at_utc = session.get("ended_at_utc") or (datetime.utcnow().isoformat() + "Z")

    # Intent best-effort (because you have both deterministic + LLM routing)
    intent = session.get("last_intent") or session.get("intent") or session.get("llm_intent")

    # Selected slot best-effort (your triage.py uses these keys)
    selected_slot = (
        session.get("selected_slot_label")
        or session.get("selected_slot")
        or session.get("SELECTED_SLOT_LABEL")  # legacy/optional
        or collected.get("selected_slot")
    )

    # Calendar info best-effort (booking + reschedule)
    calendar_event_id = (
        session.get("calendar_event_id")
        or session.get("event_id")
        or session.get("resch_event_id")
        or session.get("created_event_id")
    )

    calendar_status = (
        session.get("calendar_status")
        or session.get("calendar_result")  # optional legacy
        or ("manual_needed" if (session.get("manual_booking") or session.get("manual_reschedule")) else None)
    )

    calendar_error = session.get("calendar_error") or session.get("calendar_last_error") or ""

    # Service vs reason: keep both
    service = collected.get("service") or collected.get("reason")
    reason = collected.get("reason")

    summary = {
        "meta": {
            "call_sid": session.get("call_sid"),
            "session_id": session.get("session_id"),
            "ended_at_utc": ended_at_utc,
            "flow_state_final": session.get("state"),
            "call_status": session.get("call_status"),  # optional: set in /twilio/status
        },
        "patient": {
            "name": collected.get("name"),
            "phone": collected.get("phone") or session.get("caller_phone"),
            "new_or_returning": collected.get("patient_type"),
        },
        "appointment": {
            "intent": intent,
            "service": service,
            "reason": reason,
            "notes": collected.get("notes"),
            "time_preference": collected.get("time_pref"),
            "selected_slot": selected_slot,
            "calendar": {
                "event_id": calendar_event_id,
                "status": calendar_status,  # created/patched/failed/manual_needed
                "error": calendar_error,
            },
        },
        "insurance": {
            "insurer_name": session.get("insurer_name") or collected.get("insurer"),
            "acceptance": session.get("insurance_acceptance"),  # accepted/not_accepted/unknown
        },
        "faq": faq_turns,
        "handoff": {
            "manual_followup_needed": bool(
                session.get("manual_followup_needed")
                or session.get("manual_followup")
                or session.get("manual_reschedule")
                or session.get("manual_booking")
            ),
            "reason": (
                session.get("manual_followup_reason")
                or session.get("manual_reason")
                or session.get("manual_booking_reason")
                or session.get("manual_reschedule_reason")
            ),
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

    # Keep FAQ JSON but prevent mega-cells
    faq_json = json.dumps(faq_turns, ensure_ascii=False)
    if len(faq_json) > 45000:
        faq_json = faq_json[:45000] + "…"

    slot_label = _slot_label_from_selected(appt.get("selected_slot"))

   twilio_payload_json = json.dumps(meta.get("twilio_status_payload", {}), ensure_ascii=False)
   if len(twilio_payload_json) > 45000:
       twilio_payload_json = twilio_payload_json[:45000] + "…"
   
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

    # ✅ NEW — Twilio metadata (append-only)
    meta.get("from"),
    meta.get("to"),
    meta.get("duration_sec"),
    meta.get("direction"),
    twilio_payload_json,
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
