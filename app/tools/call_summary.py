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
    turns = session.get("turns", []) or []

    # Prefer "ended_at_utc" already set by your /twilio/status handler,
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

    # Conversation stats
    caller_turns = [t.get("text", "") for t in turns if t.get("role") == "caller"]
    conversation_stats = {
        "turn_count": len(turns),
        "miss_count": int(session.get("miss_count", 0) or 0),
        "last_user_message": (caller_turns[-1] if caller_turns else ""),
    }

    # Calendar success (simple boolean)
    # "rescheduled" = Acuity reschedule; "patched" = Google Calendar reschedule
    calendar_success = calendar_status in ("created", "patched", "rescheduled")

    # Build base summary
    summary: dict[str, Any] = {
        "meta": {
            "call_sid": session.get("call_sid"),
            "session_id": session.get("session_id"),
            "ended_at_utc": ended_at_utc,
            "flow_state_final": session.get("state"),
            "call_status": session.get("call_status"),  # set in /twilio/status
            # Twilio metadata (filled by /twilio/status if you store it)
            "from": session.get("from") or session.get("caller_phone") or collected.get("phone"),
            "to": session.get("to"),
            "duration_sec": session.get("duration_sec") or session.get("call_duration_sec"),
            "direction": session.get("direction"),
            "twilio_status_payload": session.get("twilio_status_payload") or {},
        },
        "patient": {
            "name": collected.get("name"),
            # Only include a phone here if it was explicitly collected/confirmed
            # in this call.  The Twilio caller-ID (session.caller_phone) is
            # available via meta.from and must not masquerade as collected phone —
            # doing so makes FAQ-only summary rows appear as if phone collection
            # occurred when it did not.
            "phone": collected.get("phone") or None,
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
        "conversation": conversation_stats,
        "calendar_success": bool(calendar_success),
    }

    # Outcome + confidence score (computed after summary exists)
    summary["outcome"] = infer_call_outcome(session, summary)
    summary["confidence_score"] = compute_confidence_score(session, summary)

    return summary


def summary_to_sheet_row(summary: dict[str, Any]) -> List[Any]:
    """
    Convert structured summary -> flat row for Google Sheets.
    Keep the order in sync with your Sheet header row.

    Base 20 cols + append-only extras at the end.
    """
    meta = summary.get("meta", {}) or {}
    patient = summary.get("patient", {}) or {}
    appt = summary.get("appointment", {}) or {}
    cal = appt.get("calendar", {}) or {}
    ins = summary.get("insurance", {}) or {}
    handoff = summary.get("handoff", {}) or {}
    convo = summary.get("conversation", {}) or {}
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
        # --- Original 20 ---
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

        # --- NEW: Twilio metadata (append-only) ---
        meta.get("from"),
        meta.get("to"),
        meta.get("duration_sec"),
        meta.get("direction"),
        twilio_payload_json,

        # --- NEW: Outcome + quality (append-only) ---
        summary.get("outcome"),
        summary.get("calendar_success"),
        convo.get("turn_count"),
        convo.get("miss_count"),
        convo.get("last_user_message"),
        summary.get("confidence_score"),
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


def infer_call_outcome(session: dict[str, Any], summary: dict[str, Any]) -> str:
    """
    High-level outcome label for dashboards and SMS routing.

    Possible values:
      booked            — calendar event created (new booking)
      rescheduled       — calendar event updated
      human_requested   — caller explicitly asked for a human / live transfer
      out_of_hours      — call received outside clinic opening hours
      manual_followup   — booking attempted but needs manual intervention
      faq_only          — caller only asked questions, no booking attempt
      abandoned         — caller showed interest but didn't complete booking
      failed            — technical failure
      cancelled         — successful cancellation
      reschedule_failed — reschedule reached transaction stage but backend failed
    """
    # Deterministic guard: reschedule reached transaction stage but backend explicitly failed.
    # Must precede LLM-logged check — prevents LLM "abandoned" from overriding a genuine
    # system-side failure that the caller was already informed of verbally.
    if session.get("reschedule_confirmed") and session.get("reschedule_execution_succeeded") == False:  # noqa: E712
        return "reschedule_failed"

    # Phase 3: the LLM explicitly logs the outcome via log_call_outcome tool.
    # Trust that first — it's more accurate than our heuristics.
    _logged = str(session.get("call_outcome_logged") or "").lower().strip()
    if _logged in ("booked", "cancelled", "rescheduled", "faq_only", "abandoned", "transferred", "reschedule_failed"):
        return _logged

    cal_status = (summary.get("appointment", {}) or {}).get("calendar", {}).get("status")

    # Use cal_status directly to distinguish booking vs reschedule —
    # "created" = new event (booking), "patched" = updated event (reschedule).
    # Do NOT use intent here: Phase 3 sets intent to "BOOKED" (not "BOOK")
    # which previously caused all successful bookings to be labelled "rescheduled".
    if cal_status == "created":
        return "booked"
    if cal_status in ("patched", "rescheduled"):
        # "patched" = Google Calendar reschedule; "rescheduled" = Acuity reschedule
        return "rescheduled"

    # Human / live-transfer explicitly requested
    if session.get("request_transfer") or session.get("human_requested"):
        return "human_requested"

    # Out-of-hours flag (set in voice route when call arrives outside business hours)
    if session.get("out_of_hours"):
        return "out_of_hours"

    handoff_needed = bool((summary.get("handoff", {}) or {}).get("manual_followup_needed"))
    if handoff_needed:
        return "manual_followup"

    # FAQ-only heuristics: both the narrow "faq_*" intent path AND the
    # "general_query" path (GENERAL_QUERY_FLOW → GENERAL_BOOKING_OFFER).
    # Any of these signals indicates a real informational interaction that
    # must not be labelled "abandoned":
    #   • intent starts with "faq" — explicit FAQ_FLOW caller
    #   • intent == "general_query" — caller routed through GENERAL_QUERY_FLOW
    #   • faq_follow_up_count ≥ 1 — follow-up questions answered in FAQ_BOOKING_OFFER
    #   • faq_turns non-empty — FAQ turns logged
    #   • flow ended inside an FAQ-anchor state — direct evidence of engagement
    #   • _faq_ans_at set — at least one FAQ answer was emitted this call
    _intent_lower  = str(session.get("intent") or "").lower()
    _final_state   = str((summary.get("meta", {}) or {}).get("flow_state_final") or "")
    _faq_engaged   = (
        _intent_lower.startswith("faq")
        or _intent_lower == "general_query"
        or int(session.get("faq_follow_up_count") or 0) >= 1
        or bool(session.get("faq_turns") or [])
        or _final_state in {
            "FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER",
            "ANSWER_FAQ", "ANSWER_GENERAL",
        }
        or bool(session.get("_faq_ans_at"))
    )
    if _faq_engaged:
        return "faq_only"

    # Successful cancellation — must take precedence over abandoned fallthrough
    if session.get("cancel_confirmed"):
        return "cancelled"

    # Deep-progress incomplete: caller reached a meaningful operational stage
    # (availability offered, slot pending, slot confirmed, reschedule/cancel
    # appointment located, etc.) but hung up before final confirmation.
    # Classified as "abandoned" — the caller engaged genuinely with the flow
    # but didn't complete.  NOT "manual_followup": no rule-based reason staff
    # intervention is required.  Genuine manual cases are already captured
    # above by the handoff_needed check (line ~277), which sets the flag
    # from deterministic rules (safeguarding, ambiguous identity, etc.).
    if _has_deep_progression(session):
        return "abandoned"

    # Call completed but no booking/reschedule → abandoned
    if (summary.get("meta", {}) or {}).get("call_status") == "completed":
        return "abandoned"

    return "failed"


def _has_deep_progression(session: dict[str, Any]) -> bool:
    """
    True if the call reached a meaningful operational stage in any flow.

    Booking  : slots were offered to the caller, or a specific slot was pending /
               already confirmed by the caller.
    Reschedule: the caller's existing appointment was found (rc_appointment_confirmed),
                or the caller verbally confirmed the new time (reschedule_confirmed).
    Cancel   : the caller's existing appointment was found (same rc_appointment_confirmed
               flag shared with the reschedule flow).
    """
    return bool(
        session.get("slots_offered")             # booking: availability fetched + offered
        or session.get("slot_pending_confirmation")  # booking: specific slot awaiting confirm
        or session.get("slot_confirmed")         # booking: slot confirmed, not yet saved
        or session.get("rc_appointment_confirmed")   # reschedule/cancel: appt found + confirmed
        or session.get("reschedule_confirmed")   # reschedule: caller confirmed new time
    )


def compute_confidence_score(session: dict[str, Any], summary: dict[str, Any]) -> int:
    """
    Rough 0–100 quality score: higher is better.
    Useful for tracking improvements + spotting bad calls.
    """
    score = 100

    miss = int((summary.get("conversation", {}) or {}).get("miss_count") or 0)
    score -= min(miss * 15, 45)

    if bool((summary.get("handoff", {}) or {}).get("manual_followup_needed")):
        score -= 25

    # Penalize very short calls if duration available
    dur = (summary.get("meta", {}) or {}).get("duration_sec") or session.get("call_duration_sec")
    try:
        if dur is not None and int(dur) < 20:
            score -= 20
    except Exception:
        pass

    return max(score, 0)
