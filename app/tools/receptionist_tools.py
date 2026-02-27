# app/tools/receptionist_tools.py
"""
Tool definitions (Anthropic format) and async executor functions for the
Phase 3 tool-calling LLM receptionist.

Each executor has the signature:
    async def _exec_<name>(args: dict, session: dict) -> dict

Tools mutate `session` in-place where needed.
All blocking I/O (Google APIs, Sheets) is wrapped in asyncio.to_thread().
Every executor catches its own exceptions and returns {"error": "..."} rather
than raising, so a single tool failure never crashes the conversation loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytz

logger = logging.getLogger(__name__)

LONDON_TZ = pytz.timezone("Europe/London")

# Google tokens are stored globally in Redis under this key (same as legacy)
_TOKENS_KEY = "google_tokens"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _get_tokens() -> Optional[Dict[str, Any]]:
    """Fetch Google Calendar OAuth tokens from Redis."""
    from app.storage.redis_store import redis_get_json
    try:
        return await redis_get_json(_TOKENS_KEY)
    except Exception:
        return None


def _resolve_calendar_id(clinic: Dict[str, Any], location: str) -> str:
    """
    Return the Google Calendar ID to use for a given clinic + location.
    Falls back to DEFAULT_CALENDAR_ID env var, then 'primary'.
    """
    import os
    # Theorem: per-location calendar IDs come from env vars via THEOREM_LOCATIONS
    if clinic.get("clinic_id") == "theorem" and location:
        from app.clinic_config import THEOREM_LOCATIONS
        loc_cfg = THEOREM_LOCATIONS.get(location.lower(), {})
        cal_id = loc_cfg.get("acuity_calendar_id")
        if cal_id:
            return cal_id
    # Fallback: clinic-level calendar_id, then env, then 'primary'
    return (
        clinic.get("calendar_id")
        or os.getenv("DEFAULT_CALENDAR_ID", "primary")
        or "primary"
    )


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format: input_schema, not OpenAI parameters)
# ---------------------------------------------------------------------------

TOOL_CHECK_AVAILABILITY = {
    "name": "check_availability",
    "description": (
        "Check available appointment slots at a clinic location. "
        "Call this BEFORE asking the patient to pick a time. "
        "Returns up to 3 formatted slot strings. Always call this first, "
        "then present the slots to the patient."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "enum": ["alcester", "redditch"],
                "description": "Which clinic location to check availability for.",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "Appointment length in minutes (50 for Theorem physio, 30 for demo clinic).",
            },
            "preference": {
                "type": "string",
                "description": "Optional time preference from the patient e.g. 'morning', 'Thursday afternoon', 'next week'.",
            },
            "day_window": {
                "type": "integer",
                "description": "Number of days ahead to search. Defaults to 7.",
            },
        },
        "required": ["location", "duration_minutes"],
    },
}

TOOL_BOOK_APPOINTMENT = {
    "name": "book_appointment",
    "description": (
        "Create a calendar booking ONLY after the patient has verbally confirmed "
        "the slot, their full name, and phone number. "
        "Also sends confirmation SMS and logs to Google Sheets."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string", "description": "Patient's full name."},
            "phone": {"type": "string", "description": "Patient's mobile number."},
            "location": {
                "type": "string",
                "enum": ["alcester", "redditch"],
                "description": "Clinic location.",
            },
            "service": {
                "type": "string",
                "description": "Service being booked e.g. 'physiotherapy assessment'.",
            },
            "slot_iso": {
                "type": "string",
                "description": "Start datetime in ISO 8601 format, taken from the raw slot list returned by check_availability.",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "Appointment length in minutes.",
            },
            "is_new_patient": {
                "type": "boolean",
                "description": "True if this is the patient's first visit.",
            },
            "insurer_name": {
                "type": "string",
                "description": "Insurance company name if applicable.",
            },
            "policy_number": {
                "type": "string",
                "description": "Insurance policy number if applicable.",
            },
        },
        "required": [
            "patient_name", "phone", "location", "service",
            "slot_iso", "duration_minutes", "is_new_patient",
        ],
    },
}

TOOL_CANCEL_APPOINTMENT = {
    "name": "cancel_appointment",
    "description": (
        "Cancel an existing upcoming appointment. "
        "Searches for the appointment by patient name. "
        "Confirm the cancellation verbally with the patient before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string", "description": "Patient's full name."},
            "phone": {"type": "string", "description": "Patient's phone number."},
            "location": {"type": "string", "description": "Clinic location (alcester or redditch)."},
        },
        "required": ["patient_name", "phone", "location"],
    },
}

TOOL_RESCHEDULE_APPOINTMENT = {
    "name": "reschedule_appointment",
    "description": (
        "Move an existing appointment to a new slot. "
        "Call check_availability first to get the new slot_iso. "
        "Confirm with the patient before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string"},
            "phone": {"type": "string"},
            "location": {"type": "string"},
            "new_slot_iso": {
                "type": "string",
                "description": "New start datetime in ISO 8601, from check_availability raw list.",
            },
            "duration_minutes": {"type": "integer"},
        },
        "required": ["patient_name", "phone", "location", "new_slot_iso", "duration_minutes"],
    },
}

TOOL_GET_CLINIC_INFO = {
    "name": "get_clinic_info",
    "description": (
        "Get factual clinic information. Use for hours, address, prices, insurance, "
        "services, parking, cancellation policy, or what to bring. "
        "Never guess — always call this tool for factual questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": [
                    "hours", "address", "prices", "insurance",
                    "services", "parking", "cancellation_policy", "what_to_bring",
                ],
                "description": "The topic to retrieve information about.",
            },
        },
        "required": ["topic"],
    },
}

TOOL_COLLECT_AND_STORE = {
    "name": "collect_and_store",
    "description": (
        "Store a piece of information the patient has provided. "
        "Always call this when you learn the patient's name, phone, reason, "
        "location, insurer, or other booking details. "
        "Do NOT ask for the same field twice if it is already stored."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "enum": [
                    "name", "phone", "location", "reason", "insurer",
                    "policy_number", "time_preference", "patient_type", "service",
                ],
                "description": "Which field to store.",
            },
            "value": {
                "type": "string",
                "description": "The value to store.",
            },
        },
        "required": ["field", "value"],
    },
}

TOOL_TRANSFER_TO_HUMAN = {
    "name": "transfer_to_human",
    "description": (
        "Initiate a live transfer to the clinic team. "
        "Call this when: the patient explicitly asks to speak to someone, "
        "after 2+ failed attempts to understand them, or for emergency situations. "
        "After calling this tool, say a brief warm handover message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason for the transfer e.g. 'caller requested', 'repeated misunderstanding'.",
            },
        },
        "required": ["reason"],
    },
}

TOOL_SEND_FOLLOWUP_SMS = {
    "name": "send_followup_sms",
    "description": (
        "Send an SMS to the patient. Use sparingly — only for callback requests "
        "or when the patient asks for a text. Booking confirmations are handled "
        "automatically by book_appointment."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "Patient's mobile number."},
            "message_type": {
                "type": "string",
                "enum": ["callback_request", "general"],
                "description": "Type of SMS to send.",
            },
            "custom_message": {
                "type": "string",
                "description": "Custom message text — required for 'general' type.",
            },
        },
        "required": ["phone", "message_type"],
    },
}

TOOL_LOG_CALL_OUTCOME = {
    "name": "log_call_outcome",
    "description": (
        "Record the outcome of this call for reporting. "
        "Call this at natural end points: after a successful booking, "
        "after a FAQ-only call ends, after a transfer, or when the caller hangs up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["booked", "cancelled", "rescheduled", "faq_only", "transferred", "abandoned"],
            },
            "notes": {
                "type": "string",
                "description": "Optional brief notes about the call.",
            },
        },
        "required": ["outcome"],
    },
}

# Master list passed to the Anthropic API
TOOL_SCHEMAS = [
    TOOL_CHECK_AVAILABILITY,
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_RESCHEDULE_APPOINTMENT,
    TOOL_GET_CLINIC_INFO,
    TOOL_COLLECT_AND_STORE,
    TOOL_TRANSFER_TO_HUMAN,
    TOOL_SEND_FOLLOWUP_SMS,
    TOOL_LOG_CALL_OUTCOME,
]


# ---------------------------------------------------------------------------
# Executor: check_availability
# ---------------------------------------------------------------------------

async def _exec_check_availability(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.tools.slots import (
        generate_candidate_slots,
        filter_free_slots,
        format_slot,
        pick_first_n,
        next_7_days_window,
        parse_busy,
    )
    from app.tools.calendar_google import freebusy
    from app.clinic_config import get_clinic

    location = (args.get("location") or session.get("selected_location", "")).lower().strip()
    duration_min = int(args.get("duration_minutes") or 50)
    day_window_days = int(args.get("day_window") or 7)

    clinic = get_clinic(session.get("clinic_id"))
    working_hours = clinic.get("working_hours", {})

    now = datetime.now(LONDON_TZ)
    w_start = now
    w_end = now + timedelta(days=day_window_days)

    candidates = generate_candidate_slots(
        w_start, w_end,
        duration_min=duration_min,
        clinic_working_hours=working_hours,
    )

    tokens = await _get_tokens()
    if not tokens:
        top3 = pick_first_n(candidates, 3)
        if not top3:
            return {"error": "No slots found in the next 7 days.", "slots": []}
        labels = [format_slot(s) for s in top3]
        raw = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in top3]
        session["last_offered_slots"] = raw
        session["slot_labels"] = labels
        return {"slots": labels, "raw": raw, "note": "calendar_not_connected"}

    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        busy_raw = await asyncio.to_thread(freebusy, tokens, w_start, w_end, calendar_id)
        busy_blocks = parse_busy(busy_raw or [])
        free = filter_free_slots(candidates, busy_blocks)
        top3 = pick_first_n(free, 3)
    except Exception as e:
        logger.error("check_availability freebusy error: %r", e)
        return {"error": f"Calendar check failed: {e}", "slots": []}

    if not top3:
        return {"error": "No available slots found. Try a different time preference or wider window.", "slots": []}

    labels = [format_slot(s) for s in top3]
    raw = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in top3]
    session["last_offered_slots"] = raw
    session["slot_labels"] = labels
    return {"slots": labels, "raw": raw}


# ---------------------------------------------------------------------------
# Executor: book_appointment
# ---------------------------------------------------------------------------

async def _exec_book_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.tools.calendar_google import create_event
    from app.notifications.booking_sms import send_booking_confirmation
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    if not tokens:
        return {
            "success": False,
            "error": "Calendar not connected — booking logged for manual follow-up.",
        }

    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()

    try:
        start_dt = datetime.fromisoformat(args["slot_iso"])
        if start_dt.tzinfo is None:
            start_dt = LONDON_TZ.localize(start_dt)
        end_dt = start_dt + timedelta(minutes=int(args["duration_minutes"]))
    except Exception as e:
        return {"success": False, "error": f"Invalid slot datetime: {e}"}

    patient_name = args["patient_name"]
    phone = args["phone"]
    service = args.get("service", "physiotherapy")
    is_new = bool(args.get("is_new_patient", False))
    insurer = (args.get("insurer_name") or "").strip()
    policy = (args.get("policy_number") or "").strip()

    summary = f"{patient_name} — {service}"
    description_parts = [f"Phone: {phone}", f"Location: {location.title()}"]
    if is_new:
        description_parts.append("New patient")
    if insurer:
        description_parts.append(f"Insurer: {insurer}")
    if policy:
        description_parts.append(f"Policy: {policy}")
    description = "\n".join(description_parts)

    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        event = await asyncio.to_thread(
            create_event, tokens, start_dt, end_dt, summary, description, calendar_id
        )
        event_id = event.get("id", "")
    except Exception as e:
        logger.error("book_appointment create_event error: %r", e)
        return {"success": False, "error": str(e)}

    # Update session
    session.setdefault("collected", {})
    session["collected"]["name"] = patient_name
    session["collected"]["phone"] = phone
    session["collected"]["service"] = service
    session["collected"]["slot"] = args["slot_iso"]
    if insurer:
        session["collected"]["insurer"] = insurer
    if policy:
        session["collected"]["policy_number"] = policy
    session["calendar_event_id"] = event_id
    session["calendar_status"] = "created"

    # Confirmation SMS — failure must never fail the booking
    try:
        await send_booking_confirmation(
            patient_phone=phone,
            patient_name=patient_name,
            appointment_time=start_dt,
            location=location.title(),
            service=service,
            is_new_patient=is_new,
            has_insurance=bool(insurer),
            insurer=insurer or None,
            clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
            clinic_phone=clinic.get("phone"),
        )
    except Exception as e:
        logger.warning("book_appointment SMS failed (non-fatal): %r", e)

    # Sheets log — non-blocking
    try:
        from app.tools.handoff import send_to_sheet
        await asyncio.to_thread(
            send_to_sheet,
            patient_name, phone, "BOOK",
            f"Booked: {service} at {location.title()} on {start_dt.strftime('%d %b %Y %H:%M')}",
            session.get("call_sid", ""),
            "Phase3 AI Receptionist",
        )
    except Exception as e:
        logger.warning("book_appointment Sheets log failed (non-fatal): %r", e)

    return {
        "success": True,
        "event_id": event_id,
        "booked_slot": start_dt.strftime("%A %d %B at %H:%M"),
        "location": location.title(),
    }


# ---------------------------------------------------------------------------
# Executor: cancel_appointment
# ---------------------------------------------------------------------------

async def _exec_cancel_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.tools.calendar_google import list_upcoming_events, delete_event
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    if not tokens:
        return {"success": False, "error": "Calendar not connected."}

    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()
    calendar_id = _resolve_calendar_id(clinic, location)
    patient_name_norm = (args.get("patient_name") or "").strip().lower()

    try:
        events = await asyncio.to_thread(
            list_upcoming_events, tokens, 60, 25, calendar_id
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    found = None
    for ev in events:
        ev_summary = (ev.get("summary") or "").lower()
        if patient_name_norm and patient_name_norm in ev_summary:
            found = ev
            break

    if not found:
        return {"success": False, "error": "No upcoming appointment found for that name."}

    event_id = found["id"]
    event_summary = found.get("summary", "")
    event_start = (found.get("start") or {}).get("dateTime", "")

    try:
        await asyncio.to_thread(delete_event, tokens, event_id, calendar_id)
    except Exception as e:
        return {"success": False, "error": str(e)}

    session["calendar_status"] = "cancelled"

    # SMS notification — non-fatal
    try:
        from app.notifications.booking_sms import send_cancellation_confirmation
        from datetime import datetime as _dt
        appt_time = _dt.fromisoformat(event_start.replace("Z", "+00:00")) if event_start else None
        if appt_time:
            await send_cancellation_confirmation(
                patient_phone=args.get("phone", ""),
                patient_name=args.get("patient_name", ""),
                appointment_time=appt_time,
            )
    except Exception as e:
        logger.warning("cancel_appointment SMS failed (non-fatal): %r", e)

    return {
        "success": True,
        "cancelled_event": event_summary,
        "was_at": event_start,
    }


# ---------------------------------------------------------------------------
# Executor: reschedule_appointment
# ---------------------------------------------------------------------------

async def _exec_reschedule_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.tools.calendar_google import list_upcoming_events, patch_event_time
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    if not tokens:
        return {"success": False, "error": "Calendar not connected."}

    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()
    calendar_id = _resolve_calendar_id(clinic, location)
    patient_name_norm = (args.get("patient_name") or "").strip().lower()

    try:
        events = await asyncio.to_thread(
            list_upcoming_events, tokens, 60, 25, calendar_id
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    found = None
    for ev in events:
        ev_summary = (ev.get("summary") or "").lower()
        if patient_name_norm and patient_name_norm in ev_summary:
            found = ev
            break

    if not found:
        return {"success": False, "error": "No upcoming appointment found for that name."}

    event_id = found["id"]

    try:
        new_start = datetime.fromisoformat(args["new_slot_iso"])
        if new_start.tzinfo is None:
            new_start = LONDON_TZ.localize(new_start)
        new_end = new_start + timedelta(minutes=int(args["duration_minutes"]))
    except Exception as e:
        return {"success": False, "error": f"Invalid new slot datetime: {e}"}

    try:
        await asyncio.to_thread(
            patch_event_time, tokens, event_id, new_start, new_end, calendar_id
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    session["calendar_status"] = "patched"

    # SMS notification — non-fatal
    try:
        from app.notifications.booking_sms import send_reschedule_confirmation
        old_start_str = (found.get("start") or {}).get("dateTime", "")
        if old_start_str:
            old_time = datetime.fromisoformat(old_start_str.replace("Z", "+00:00"))
            await send_reschedule_confirmation(
                patient_phone=args.get("phone", ""),
                patient_name=args.get("patient_name", ""),
                old_time=old_time,
                new_time=new_start,
                location=location.title(),
            )
    except Exception as e:
        logger.warning("reschedule_appointment SMS failed (non-fatal): %r", e)

    return {
        "success": True,
        "rescheduled_to": new_start.strftime("%A %d %B at %H:%M"),
    }


# ---------------------------------------------------------------------------
# Executor: get_clinic_info
# ---------------------------------------------------------------------------

async def _exec_get_clinic_info(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.clinic_config import get_clinic

    clinic = get_clinic(session.get("clinic_id"))
    location_id = (session.get("selected_location") or "").lower()
    topic = args.get("topic", "")

    # Try location-specific data first (for Theorem's two locations)
    locations = clinic.get("locations", [])
    loc_cfg = next((loc for loc in locations if loc.get("id") == location_id), None)

    if topic == "hours":
        text = (loc_cfg.get("hours_summary") if loc_cfg else None) or clinic.get("hours_summary", "")
    elif topic == "address":
        text = (loc_cfg.get("address") if loc_cfg else None) or clinic.get("address", "")
    elif topic == "parking":
        text = (loc_cfg.get("parking") if loc_cfg else None) or clinic.get("parking", "")
    elif topic == "prices":
        text = clinic.get("pricing_summary", "")
    elif topic == "insurance":
        text = clinic.get("insurance_note", "")
    elif topic == "services":
        svcs = clinic.get("services", [])
        text = "Services include: " + ", ".join(svcs) if svcs else ""
    elif topic == "cancellation_policy":
        text = clinic.get("cancellation_policy", "")
    elif topic == "what_to_bring":
        text = clinic.get("what_to_bring", "")
    else:
        text = ""

    return {"topic": topic, "info": text or "I don't have that specific information to hand."}


# ---------------------------------------------------------------------------
# Executor: collect_and_store
# ---------------------------------------------------------------------------

async def _exec_collect_and_store(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    field = args.get("field", "")
    value = (args.get("value") or "").strip()

    if not field or not value:
        return {"error": "field and value are required"}

    session.setdefault("collected", {})

    # Normalise phone to E.164
    if field == "phone":
        try:
            from app.flows.triage_legacy import normalize_phone
            value = normalize_phone(value)
        except Exception:
            pass

    # Keep session location keys in sync
    if field == "location":
        session["selected_location"] = value.lower()
        session["location_selected"] = True

    session["collected"][field] = value
    return {"stored": field, "value": value}


# ---------------------------------------------------------------------------
# Executor: transfer_to_human
# ---------------------------------------------------------------------------

async def _exec_transfer_to_human(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    reason = (args.get("reason") or "caller requested").strip()

    # Critical: this flag is checked by twilio.py after handle_turn returns
    session["request_transfer"] = True
    session["human_requested"] = True
    session["manual_followup_reason"] = reason

    # Log to Sheets so clinic team sees the transfer
    try:
        from app.tools.handoff import send_to_sheet
        collected = session.get("collected") or {}
        await asyncio.to_thread(
            send_to_sheet,
            collected.get("name", "Unknown"),
            collected.get("phone", ""),
            "TRANSFER",
            f"Transfer requested: {reason}",
            session.get("call_sid", ""),
            "Phase3 AI Receptionist",
        )
    except Exception as e:
        logger.warning("transfer_to_human Sheets log failed (non-fatal): %r", e)

    return {"transfer_initiated": True, "reason": reason}


# ---------------------------------------------------------------------------
# Executor: send_followup_sms
# ---------------------------------------------------------------------------

async def _exec_send_followup_sms(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    phone = (args.get("phone") or "").strip()
    msg_type = args.get("message_type", "general")
    custom_msg = (args.get("custom_message") or "").strip()

    if not phone:
        return {"sent": False, "error": "Phone number is required."}

    try:
        if msg_type == "callback_request":
            from app.notifications.booking_sms import send_callback_confirmation
            collected = session.get("collected") or {}
            name = collected.get("name", "")
            await send_callback_confirmation(patient_phone=phone, patient_name=name)
            return {"sent": True, "type": msg_type}

        if msg_type == "general" and custom_msg:
            from app.notifications.sms import send_sms
            await send_sms(to=phone, message=custom_msg)
            return {"sent": True, "type": msg_type}

        return {"sent": False, "error": "No message sent — check message_type and custom_message."}

    except Exception as e:
        logger.error("send_followup_sms failed: %r", e)
        return {"sent": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Executor: log_call_outcome
# ---------------------------------------------------------------------------

async def _exec_log_call_outcome(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    outcome = args.get("outcome", "abandoned")
    notes = (args.get("notes") or "").strip()

    session["call_outcome_logged"] = outcome
    session["call_outcome_notes"] = notes
    session["intent"] = outcome.upper()

    return {"logged": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# Tool executor registry
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: Dict[str, Any] = {
    "check_availability":     _exec_check_availability,
    "book_appointment":       _exec_book_appointment,
    "cancel_appointment":     _exec_cancel_appointment,
    "reschedule_appointment": _exec_reschedule_appointment,
    "get_clinic_info":        _exec_get_clinic_info,
    "collect_and_store":      _exec_collect_and_store,
    "transfer_to_human":      _exec_transfer_to_human,
    "send_followup_sms":      _exec_send_followup_sms,
    "log_call_outcome":       _exec_log_call_outcome,
}
