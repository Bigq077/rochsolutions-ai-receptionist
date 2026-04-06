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
from datetime import datetime, timedelta, date as _date_type
from typing import Any, Dict, Optional

import pytz

logger = logging.getLogger(__name__)

LONDON_TZ = pytz.timezone("Europe/London")

# Google tokens are stored globally in Redis under this key (same as legacy)
_TOKENS_KEY = "google_tokens"

# ---------------------------------------------------------------------------
# Acuity default appointment type
# ---------------------------------------------------------------------------
# The Acuity appointment type ID used for all Theorem bookings.
# Format: "acuity_<raw_id>" (the adapter strips the prefix before calling the API).
# Override by setting DEFAULT_APPOINTMENT_TYPE_ID in the environment.
import os as _os
DEFAULT_ACUITY_APPOINTMENT_TYPE_ID: str = (
    f"acuity_{_os.getenv('DEFAULT_APPOINTMENT_TYPE_ID', '15823699')}"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ordinal(n: int) -> str:
    """Return English ordinal string: 1→'1st', 2→'2nd', 26→'26th', etc."""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _build_days_data(slot_tuples: list) -> list:
    """
    Group (start_dt, end_dt) tuples into per-day summaries for the day-first
    availability presentation flow.  Returns up to 8 unique days.
    """
    from collections import defaultdict as _dd
    days_map: "_dd[Any, list]" = _dd(list)
    for start, end in slot_tuples:
        days_map[start.date()].append((start, end))

    days_data = []
    for day in sorted(days_map.keys())[:8]:          # cap at 8 days
        day_slots = days_map[day]
        dt = day_slots[0][0]
        day_name  = dt.strftime("%A")                # "Thursday"
        day_label = f"{day_name} {_ordinal(dt.day)} {dt.strftime('%B')}"  # "Thursday 26th March"
        days_data.append({
            "date":       day.isoformat(),
            "day_label":  day_label,
            "slot_times": [s[0].strftime("%H:%M") for s in day_slots],
            "slots":      [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in day_slots],
        })
    return days_data


def _select_presented_tuples(slot_tuples: list, preference: str = "") -> list:
    """
    Pick up to 3 (start_dt, end_dt) tuples to present to the caller.
    Prefer one slot per day for variety.  Fall back to first 3 chronological
    slots when fewer than 3 days are available (e.g. all slots on same day).
    Ensures slot_labels[0/1/2] match exactly the 1st/2nd/3rd slot presented.

    Past slots are filtered out first.  If a preference string is given
    (e.g. 'Wednesday morning'), slots are further filtered to match the
    stated day-of-week and/or time-of-day so that slot_labels contains ONLY
    what the LLM will verbally present — preventing ordinal mismatch when
    the LLM filters by preference but slot_labels has unfiltered Acuity results.
    Falls back to all future slots if no preference-matching slots found.
    """
    now = datetime.now(LONDON_TZ)
    future_only = [(s, e) for s, e in slot_tuples if s > now]

    # Apply preference filtering so stored slot_labels match LLM verbal output
    pref = preference.lower()
    filtered = future_only

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    pref_days = [wd for name, wd in day_map.items() if name in pref]
    if pref_days:
        day_filtered = [(s, e) for s, e in filtered if s.weekday() in pref_days]
        if day_filtered:
            filtered = day_filtered

    if "morning" in pref:
        time_filtered = [(s, e) for s, e in filtered if s.hour < 12]
        if time_filtered:
            filtered = time_filtered
    elif "afternoon" in pref:
        time_filtered = [(s, e) for s, e in filtered if 12 <= s.hour < 17]
        if time_filtered:
            filtered = time_filtered
    elif "evening" in pref:
        time_filtered = [(s, e) for s, e in filtered if s.hour >= 17]
        if time_filtered:
            filtered = time_filtered

    # Fall back to all future slots if preference produced no matches
    if not filtered:
        filtered = future_only

    day_seen: set = set()
    day_firsts: list = []
    for start, end in sorted(filtered, key=lambda t: t[0]):
        day = start.date()
        if day not in day_seen:
            day_seen.add(day)
            day_firsts.append((start, end))
    if len(day_firsts) >= 3:
        return day_firsts[:3]
    # Fewer than 3 days — take first 3 slots chronologically
    return sorted(filtered, key=lambda t: t[0])[:3]


async def _get_tokens() -> Optional[Dict[str, Any]]:
    """Fetch Google Calendar OAuth tokens from Redis."""
    from app.storage.redis_store import redis_get_json
    try:
        return await redis_get_json(_TOKENS_KEY)
    except Exception:
        return None


async def _save_gcal_tokens(tokens: Dict[str, Any]) -> None:
    """
    Persist refreshed GCal tokens back to Redis.
    _refresh_if_needed() updates the token dict in-place but never writes to
    Redis itself; calling this after each calendar API call ensures the new
    access token and expiry are saved so the next request skips a needless
    refresh round-trip.  Failures are non-fatal — worst case is an extra
    refresh on the next call.
    """
    from app.storage.redis_store import redis_set_json
    try:
        await redis_set_json(_TOKENS_KEY, tokens, ttl_seconds=60 * 60 * 24 * 365)
    except Exception as e:
        logger.warning("_save_gcal_tokens: failed to persist updated tokens: %r", e)


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


def _resolve_slot_iso(slot_iso: str, session: dict) -> "datetime":
    """
    Parse slot_iso as an ISO 8601 datetime.

    If direct parsing fails (e.g. Claude passed a human-readable label like
    'Mon 02 Mar at 09:00', a slot number like '1', or a slightly wrong format),
    fall back to looking up the matching start time from the
    session["last_offered_slots"] list that was stored by check_availability.

    Always returns a timezone-aware datetime in Europe/London.
    Raises ValueError if nothing can be resolved.
    """

    def _to_london(dt: "datetime") -> "datetime":
        """
        FIX #8: Convert any datetime to Europe/London, handling both naive
        and aware inputs correctly.
        - Naive  → localize (treat as London wall-clock time)
        - Aware  → astimezone (convert from whatever tz it carries)
        Using localize() on an already-aware datetime would silently double
        the UTC offset, so we must branch on tzinfo presence.
        """
        if dt.tzinfo is None:
            return LONDON_TZ.localize(dt)
        return dt.astimezone(LONDON_TZ)

    # 1. Direct ISO parse — but only accept if it matches one of the offered slots.
    # Claude sometimes hallucinates a slot ISO that was never presented; the guard
    # below forces it back to the offered list so the wrong slot can never be booked.
    s = str(slot_iso or "").strip()
    offered_check = session.get("last_offered_slots") or []
    if s and offered_check:
        try:
            dt_candidate = _to_london(datetime.fromisoformat(s))
            # Accept only if it matches an offered slot (within 60 s tolerance)
            for offered_slot in offered_check:
                try:
                    offered_dt = _to_london(datetime.fromisoformat(offered_slot["start"]))
                    if abs((dt_candidate - offered_dt).total_seconds()) < 60:
                        logger.info("_resolve_slot_iso: direct ISO match verified against offered slot %s", offered_slot["start"])
                        return dt_candidate
                except Exception:
                    pass
            # Did not match any offered slot — fall through to index/label matching
            logger.warning("_resolve_slot_iso: ISO %r not in offered slots %s — falling back to index/label matching", s, [o['start'] for o in offered_check])
        except (ValueError, TypeError):
            pass
    elif s and not offered_check:
        # No offered slots in session (e.g. direct calendar booking) — accept as-is
        try:
            dt = datetime.fromisoformat(s)
            return _to_london(dt)
        except (ValueError, TypeError):
            pass

    offered = session.get("last_offered_slots") or []
    labels  = session.get("slot_labels") or []
    s_lower = s.lower()

    # 2. Numeric / ordinal index ("1", "first", "the first one", etc.)
    idx_map = {
        "1": 0, "first": 0,  "slot 1": 0, "option 1": 0, "slot1": 0,
        "the first": 0, "the first one": 0, "that first one": 0, "first one": 0,
        "2": 1, "second": 1, "slot 2": 1, "option 2": 1, "slot2": 1,
        "the second": 1, "the second one": 1, "that second one": 1, "second one": 1,
        "3": 2, "third": 2,  "slot 3": 2, "option 3": 2, "slot3": 2,
        "the third": 2, "the third one": 2, "that third one": 2, "third one": 2,
    }
    if s_lower in idx_map:
        idx = idx_map[s_lower]
        if idx < len(offered):
            try:
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: index match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass

    # 2a. Word-level ordinal fallback — catches "I'll take the first one please" etc.
    import re as _re
    _ordinal_words = {"first": 0, "second": 1, "third": 2}
    _tokens = set(_re.findall(r"\b\w+\b", s_lower))
    for word, idx in _ordinal_words.items():
        if word in _tokens and idx < len(offered):
            try:
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: ordinal-word match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass

    # 2b. "Last" / "final" keywords — dynamic index to the final offered slot
    _last_keywords = {
        "last", "last one", "last slot", "the last", "the last one",
        "that last one", "that last slot", "final", "final one", "final slot",
        "the final", "the final one", "that final one",
    }
    if s_lower in _last_keywords and offered:
        idx = len(offered) - 1
        try:
            dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
            logger.info("_resolve_slot_iso: 'last' match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
            return dt
        except Exception:
            pass

    # 2c. Informal British time expressions — "the morning one", "half nine",
    # "the twelve o'clock", "the early one", "the late one", etc. (Bug #6)
    _morning_kw = {"morning", "morning one", "the morning one", "early", "early one", "the early one", "earlier"}
    _afternoon_kw = {"afternoon", "afternoon one", "the afternoon one", "late", "late one", "the late one", "later", "the later one"}
    if offered:
        if s_lower in _morning_kw:
            # Pick the earliest offered slot
            try:
                dt = _to_london(datetime.fromisoformat(offered[0]["start"]))
                logger.info("_resolve_slot_iso: morning/early match %r → slot[0] %s", slot_iso, offered[0]["start"])
                return dt
            except Exception:
                pass
        if s_lower in _afternoon_kw:
            # Pick the latest offered slot
            try:
                idx = len(offered) - 1
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: afternoon/late match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass
        # "half nine" → 9:30, "nine o'clock" → 9:00, "twelve" → 12:00
        _time_map = {
            "half eight": (8, 30), "half nine": (9, 30), "half ten": (10, 30),
            "half eleven": (11, 30), "half twelve": (12, 30), "half one": (13, 30),
            "half two": (14, 30), "half three": (15, 30), "half four": (16, 30),
        }
        for phrase, (h, m) in _time_map.items():
            if phrase in s_lower:
                for i, slot in enumerate(offered):
                    try:
                        sdt = _to_london(datetime.fromisoformat(slot["start"]))
                        if sdt.hour == h and sdt.minute == m:
                            logger.info("_resolve_slot_iso: British time match %r → slot[%d]", phrase, i)
                            return sdt
                    except Exception:
                        pass

    # 3. Fuzzy match against human-readable labels
    for i, label in enumerate(labels):
        if i < len(offered):
            words = [w for w in s_lower.split() if len(w) > 2]
            if words and any(w in label.lower() for w in words):
                try:
                    dt = _to_london(datetime.fromisoformat(offered[i]["start"]))
                    logger.info("_resolve_slot_iso: fuzzy match %r → label[%d] %r", slot_iso, i, label)
                    return dt
                except Exception:
                    pass

    raise ValueError(f"Cannot parse or resolve slot datetime: {slot_iso!r}")


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format: input_schema, not OpenAI parameters)
# ---------------------------------------------------------------------------

TOOL_CHECK_AVAILABILITY = {
    "name": "check_availability",
    "description": (
        "Check available appointment slots at a clinic location. "
        "Call this BEFORE offering times. Returns `available_days` — a list of days, "
        "each with day_label, slot_times, and slots. Present available DAYS first "
        "(up to 4), then times for the chosen day (up to 4)."
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
            "service": {
                "type": "string",
                "description": (
                    "Type of appointment to check, e.g. 'physiotherapy assessment', "
                    "'physiotherapy follow-up', 'acupuncture'. Required for Acuity clinics."
                ),
            },
            "after_date": {
                "type": "string",
                "description": (
                    "Optional ISO date string (YYYY-MM-DD). Only return slots on or after this date. "
                    "Pass this when the caller says they are unavailable before a specific date — "
                    "e.g. 'not available this week' → pass next Monday's date; "
                    "'next week' → pass the coming Monday's date. "
                    "This is the ONLY guaranteed way to prevent excluded dates being offered."
                ),
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
        "Get factual clinic information. Use for hours, address, transport, prices, insurance, "
        "services, parking, cancellation policy, or what to bring. "
        "Never guess — always call this tool for factual questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": [
                    "hours", "address", "transport", "parking", "prices", "insurance",
                    "services", "cancellation_policy", "what_to_bring",
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
                    "name", "full_name", "phone", "location", "reason", "insurer",
                    "policy_number", "time_preference", "patient_type", "service",
                    "referral_source", "email",
                ],
                "description": (
                    "Which field to store. Use 'full_name' when collecting the caller's name "
                    "(always collected as a single full name, never split into first/last). "
                    "'full_name' and 'name' are equivalent — both are stored together."
                ),
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

TOOL_GET_PATIENT_HISTORY = {
    "name": "get_patient_history",
    "description": (
        "Look up a returning patient's recent appointment history to identify "
        "their current treatment type. Use this when a patient says they are on "
        "a treatment plan so you can announce what they have been coming in for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {
                "type": "string",
                "description": "Full name of the patient as given on the call.",
            },
            "phone": {
                "type": "string",
                "description": (
                    "Patient's phone number (optional). Helps disambiguate if "
                    "multiple patients share the same name."
                ),
            },
        },
        "required": ["patient_name"],
    },
}

TOOL_ADD_TO_WAITLIST = {
    "name": "add_to_waitlist",
    "description": (
        "Add a caller to the clinic waitlist when no appointment slots are available. "
        "Stores their name, phone, preferred location, and any notes about preferred "
        "days/times. The clinic team will contact them when a slot opens up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {
                "type": "string",
                "description": "Full name of the patient.",
            },
            "phone": {
                "type": "string",
                "description": "Patient's phone number.",
            },
            "location": {
                "type": "string",
                "description": "Preferred clinic location (e.g. 'alcester' or 'redditch').",
            },
            "service": {
                "type": "string",
                "description": "Requested service (e.g. 'physiotherapy assessment').",
            },
            "notes": {
                "type": "string",
                "description": "Any preferences — preferred days, times, urgency notes.",
            },
        },
        "required": ["patient_name", "phone"],
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
    TOOL_GET_PATIENT_HISTORY,
    TOOL_ADD_TO_WAITLIST,
]


# ===========================================================================
# ACUITY SCHEDULING — helpers and executors (Theorem clinic only)
# ===========================================================================

# Module-level cache: Acuity type name (lowercase) → "acuity_12345" ID
# Populated on first call, reused for the lifetime of the worker process.
_acuity_type_id_cache: Dict[str, str] = {}

# Module-level cache: UK (England/Wales) bank holiday dates fetched from GOV.UK API.
# None = not yet fetched; populated set = successfully fetched.
_uk_bank_holidays_cache: Optional[set] = None

# Hardcoded England/Wales bank holidays 2025-2027.
# Used as the baseline — always applied even if the GOV.UK API is unreachable.
# GOV.UK API results are merged on top of this set when available.
_UK_BANK_HOLIDAYS_FALLBACK: frozenset = frozenset({
    # 2025
    _date_type(2025,  1,  1),  # New Year's Day
    _date_type(2025,  4, 18),  # Good Friday
    _date_type(2025,  4, 21),  # Easter Monday
    _date_type(2025,  5,  5),  # Early May bank holiday
    _date_type(2025,  5, 26),  # Spring bank holiday
    _date_type(2025,  8, 25),  # Summer bank holiday
    _date_type(2025, 12, 25),  # Christmas Day
    _date_type(2025, 12, 26),  # Boxing Day
    # 2026
    _date_type(2026,  1,  1),  # New Year's Day
    _date_type(2026,  4,  3),  # Good Friday
    _date_type(2026,  4,  6),  # Easter Monday
    _date_type(2026,  5,  4),  # Early May bank holiday
    _date_type(2026,  5, 25),  # Spring bank holiday
    _date_type(2026,  8, 31),  # Summer bank holiday
    _date_type(2026, 12, 25),  # Christmas Day
    _date_type(2026, 12, 28),  # Boxing Day (substitute)
    # 2027
    _date_type(2027,  1,  1),  # New Year's Day
    _date_type(2027,  3, 26),  # Good Friday
    _date_type(2027,  3, 29),  # Easter Monday
    _date_type(2027,  5,  3),  # Early May bank holiday
    _date_type(2027,  5, 31),  # Spring bank holiday
    _date_type(2027,  8, 30),  # Summer bank holiday
    _date_type(2027, 12, 27),  # Christmas Day (substitute)
    _date_type(2027, 12, 28),  # Boxing Day (substitute)
})


async def _fetch_uk_bank_holidays() -> frozenset:
    """
    Return a frozenset of England/Wales bank holiday dates (datetime.date objects).

    Always returns at least _UK_BANK_HOLIDAYS_FALLBACK (2025-2027 hardcoded).
    On first call, merges with live data from the GOV.UK API and caches the
    combined result for the lifetime of the worker process.
    """
    global _uk_bank_holidays_cache
    if _uk_bank_holidays_cache is not None:
        return _uk_bank_holidays_cache

    import httpx

    api_holidays: set = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://www.gov.uk/bank-holidays.json")
            resp.raise_for_status()
            data = resp.json()

        for event in data.get("england-and-wales", {}).get("events", []):
            try:
                api_holidays.add(_date_type.fromisoformat(event["date"]))
            except Exception:
                pass

        logger.info(
            "_fetch_uk_bank_holidays: GOV.UK API returned %d holidays; "
            "merged with %d hardcoded fallback dates",
            len(api_holidays), len(_UK_BANK_HOLIDAYS_FALLBACK),
        )
    except Exception as exc:
        logger.warning(
            "_fetch_uk_bank_holidays: GOV.UK API failed (%r) — "
            "using hardcoded fallback (%d dates) only",
            exc, len(_UK_BANK_HOLIDAYS_FALLBACK),
        )

    combined = frozenset(api_holidays | _UK_BANK_HOLIDAYS_FALLBACK)
    _uk_bank_holidays_cache = combined
    return combined


def _filter_slots_by_working_hours(slots: list, location: str, location_working_hours: dict) -> list:
    """
    Remove slots that fall outside the configured working hours for the given location.

    location_working_hours format (from clinic_config):
        {"mon": (8.5, 21.0), "tue": (8.5, 21.0), ..., "sat": None, "sun": None}
    A day mapped to None means the clinic is closed; all slots on that day are removed.
    Hours are expressed as fractional hours (8.5 = 08:30).
    """
    _DAY_KEY = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    loc_hours = location_working_hours.get(location) or {}
    if not loc_hours:
        # No hours configured for this location — return slots unchanged
        return slots

    filtered = []
    skipped = 0
    for slot in slots:
        day_key = _DAY_KEY.get(slot.start_time.weekday())
        hours = loc_hours.get(day_key)
        if hours is None:
            # Closed on this weekday
            skipped += 1
            continue
        open_frac  = hours[0]                                     # e.g. 8.5 → 08:30
        close_frac = hours[1]                                     # e.g. 21.0 → 21:00
        slot_start = slot.start_time.hour + slot.start_time.minute / 60.0
        slot_end   = slot.end_time.hour   + slot.end_time.minute   / 60.0
        if slot_start >= open_frac and slot_end <= close_frac:
            filtered.append(slot)
        else:
            skipped += 1

    if skipped:
        logger.info(
            "_filter_slots_by_working_hours: removed %d/%d slots outside %r hours",
            skipped, len(slots), location,
        )
    return filtered

# ---------------------------------------------------------------------------
# Location normalisation — maps spoken/STT variants to canonical location IDs
# ---------------------------------------------------------------------------
_ALCESTER_VARIANTS = {
    "alcester", "alce", "alchester", "alcest",
    "allster", "alster", "all ster", "all chester", "all-ster",
    "awlster", "olster", "ulster", "alcester road",
}
_REDDITCH_VARIANTS = {
    "redditch", "reditch", "reddich", "redich",
    "reddich road", "bromsgrove road",
}
# Number-based location selection (Theorem: "say one for Alcester, two for Redditch")
_ALCESTER_NUMBERS = {"1", "one", "first", "option one", "option 1", "number one", "number 1"}
_REDDITCH_NUMBERS = {"2", "two", "second", "option two", "option 2", "number two", "number 2"}


def _normalize_location(value: str) -> str:
    """
    Map a spoken or STT-transcribed location string to a canonical location ID.
    Returns "alcester", "redditch", or the lowercased original (for single-location
    clinics or already-canonical values).
    """
    v = (value or "").lower().strip()
    # Number-based selection ("say one for Alcester, two for Redditch")
    if v in _ALCESTER_NUMBERS:
        return "alcester"
    if v in _REDDITCH_NUMBERS:
        return "redditch"
    if any(variant in v for variant in _ALCESTER_VARIANTS):
        return "alcester"
    if any(variant in v for variant in _REDDITCH_VARIANTS):
        return "redditch"
    return v


def _make_acuity_adapter():
    """
    Create a fresh AcuityAdapter using Theorem clinic credentials.
    Returns None (with a warning) if ACUITY_USER_ID / ACUITY_API_KEY are not set.
    """
    from app.booking.booking.providers.acuity import AcuityAdapter
    from app.clinic_config import get_acuity_config

    cfg = get_acuity_config("theorem")
    user_id = (cfg.get("user_id") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not user_id or not api_key:
        logger.warning(
            "Acuity credentials not configured — set ACUITY_USER_ID and ACUITY_API_KEY"
        )
        return None
    return AcuityAdapter(user_id=user_id, api_key=api_key, clinic_id="theorem")


# Module-level singleton — reuses the httpx.AsyncClient connection pool across
# every availability check and booking call.  Creating a new AcuityAdapter (and
# therefore a new httpx.AsyncClient) on every call forces a fresh TCP + TLS
# handshake to Acuity each time, adding hundreds of milliseconds per booking turn.
_acuity_adapter_singleton = None


def _get_acuity_adapter():
    """Return the shared AcuityAdapter singleton (lazy-initialised on first call)."""
    global _acuity_adapter_singleton
    if _acuity_adapter_singleton is None:
        _acuity_adapter_singleton = _make_acuity_adapter()
    return _acuity_adapter_singleton


async def _fetch_acuity_type_cache(adapter) -> Dict[str, str]:
    """
    Populate _acuity_type_id_cache from the Acuity API if not already cached.
    Returns {type_name_lower: "acuity_12345"} mapping.
    """
    global _acuity_type_id_cache
    if _acuity_type_id_cache:
        return _acuity_type_id_cache
    try:
        types = await adapter.get_appointment_types()
        for t in types:
            _acuity_type_id_cache[t.name.lower()] = t.id
        logger.info("Acuity appointment type cache: %s", list(_acuity_type_id_cache.keys()))
    except Exception as e:
        logger.error("Failed to fetch Acuity appointment types: %r", e)
    return _acuity_type_id_cache


# Types that should never be booked by the AI receptionist
_SKIP_TYPES = [
    "blocked", "training course", "home visit", "outreach",
    "gong bath", "sound therapy", "meditation", "breathe work",
    "nada gb cert", "package x",
]


def _match_service_to_acuity_id(
    service: str,
    type_cache: Dict[str, str],
    location: str = "",
) -> str:
    """
    Map a free-text service description to an Acuity appointment type ID.

    Theorem's Acuity types are named by location and practitioner
    (e.g. "theorem clinics alcester.", "theorem clinics redditch",
    "theorem clinics alcester. leanne "), not by service name.

    Priority:
      1. Exact match
      2. Location-first: if location is known, find the primary type for
         that location (prefers the "main" entry, avoids practitioner-specific
         or blocked entries).
      3. Service keyword fallback (for specialist types: acupuncture,
         rehab, psychotherapy, shockwave, prescribing).
      4. Absolute fallback: first non-blocked entry.
    """
    s = service.lower().strip()
    loc = location.lower().strip()

    # Helper: is this type name something we should skip?
    def _skippable(name: str) -> bool:
        n = name.lower()
        return any(skip in n for skip in _SKIP_TYPES)

    # 1. Exact match
    if s in type_cache:
        return type_cache[s]

    # 2. Location-first matching (handles Theorem's location-named types)
    #    When location is known, find the PRIMARY type for that location.
    #    "Primary" = contains the location name but NOT practitioner-specific
    #    suffixes like "leanne" (unless specifically requested).
    if loc:
        # 2a. Look for a main location type (location name, no practitioner suffix)
        #     Prefer the shortest/cleanest matching name.
        location_matches = [
            (name, tid) for name, tid in type_cache.items()
            if loc in name and not _skippable(name)
        ]
        # Sort: prefer entries that DON'T contain practitioner names
        # (i.e. the generic location-level type)
        practitioner_suffixes = ["leanne", "mark", "ins-", "insurance"]
        generic = [
            (n, t) for n, t in location_matches
            if not any(p in n for p in practitioner_suffixes)
        ]
        if generic:
            # Among generics, pick the shortest name (most likely to be the main type)
            best = min(generic, key=lambda x: len(x[0]))
            logger.info(
                "_match_service_to_acuity_id: location=%r → matched type %r (id=%s)",
                loc, best[0], best[1],
            )
            return best[1]
        # 2b. If only practitioner-specific types exist for this location, use first one
        if location_matches:
            best = location_matches[0]
            logger.info(
                "_match_service_to_acuity_id: location=%r → practitioner type %r (id=%s)",
                loc, best[0], best[1],
            )
            return best[1]

    # 3. Service keyword table (for specialist services or when no location given)
    _PRIORITY = [
        (["acupuncture", "needle", "needling"],
         ["acupuncture"]),
        (["psychotherapy", "therapy", "mental", "hypno", "spiritual"],
         ["psychotherapy"]),
        (["prescrib", "medication", "prescription"],
         ["prescribing"]),
        (["shockwave", "laser", "mls"],
         ["laser", "shockwave", "mls"]),
        (["rehab", "rehabilitation", "remedial", "yoga", "training"],
         ["rehab", "remedial", "yoga", "training"]),
        (["massage"],
         ["massage"]),
        (["follow-up", "follow up", "followup", "follow", "returning"],
         ["follow-up", "followup", "follow up"]),
        (["assessment", "initial", "first", "new", "physio", "consultation"],
         ["assessment", "physiotherapy", "clinics"]),
    ]
    for input_keywords, cache_keywords in _PRIORITY:
        if any(kw in s for kw in input_keywords):
            for cached_name, cached_id in type_cache.items():
                if _skippable(cached_name):
                    continue
                if any(kw in cached_name for kw in cache_keywords):
                    logger.info(
                        "_match_service_to_acuity_id: service keyword match %r → %r (id=%s)",
                        s, cached_name, cached_id,
                    )
                    return cached_id

    # 4. Absolute fallback: first non-blocked entry
    for name, tid in type_cache.items():
        if not _skippable(name):
            logger.warning(
                "_match_service_to_acuity_id: fallback to first non-blocked type %r (id=%s)",
                name, tid,
            )
            return tid

    if type_cache:
        return next(iter(type_cache.values()))

    return None


def _split_name(full_name: str):
    """Split 'John Smith' → ('John', 'Smith'). Single word → (word, '')."""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


# ---------------------------------------------------------------------------
# Manual slot generator (fallback when Acuity working hours aren't configured)
# ---------------------------------------------------------------------------

async def _generate_manual_slots(
    adapter,
    appointment_type_id: str,
    practitioner_id,
    start_date,
    end_date,
    slot_minutes: int = 50,
) -> list:
    """
    Build slots from Theorem's known working hours (Mon–Thu 08:30–21:00, 50-min)
    and subtract already-booked Acuity appointments.

    Used as a fallback when Acuity /availability/times returns 0 slots because
    working hours aren't configured inside the Acuity admin panel.
    The booking POST still works fine regardless of that config gap.
    """
    from app.booking.booking.models import Slot as _Slot

    # Theorem Alcester: Mon–Fri (0–4), 08:30 start, last slot starts at 20:10 so it ends at 21:00
    WORK_START_H, WORK_START_M = 8, 30
    WORKING_WEEKDAYS = {0, 1, 2, 3, 4}  # Mon=0 … Fri=4

    # ── fetch existing appointments to exclude conflicts ──────────────────
    booked_times: set = set()
    try:
        existing = await adapter.list_appointments(min_date=start_date, max_date=end_date)
        for appt in existing:
            dt_str = appt.get("datetime") or appt.get("time") or ""
            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = LONDON_TZ.localize(dt)
                    else:
                        dt = dt.astimezone(LONDON_TZ)
                    booked_times.add((dt.date(), dt.hour, dt.minute))
                except Exception:
                    pass
    except Exception as fetch_err:
        logger.warning(
            "_generate_manual_slots: could not fetch existing appointments to check conflicts: %r",
            fetch_err,
        )

    # ── generate slots ────────────────────────────────────────────────────
    slots: list = []
    current = start_date
    # End of last allowed slot must be <= 21:00, so last START is 21:00 - slot_minutes
    work_end_minutes = 21 * 60  # 21:00 in minutes from midnight
    start_minutes = WORK_START_H * 60 + WORK_START_M

    while current <= end_date:
        if current.weekday() in WORKING_WEEKDAYS:
            t_min = start_minutes
            while t_min + slot_minutes <= work_end_minutes:
                h, m = divmod(t_min, 60)
                naive_start = datetime(current.year, current.month, current.day, h, m)
                slot_start = LONDON_TZ.localize(naive_start)
                slot_end = slot_start + timedelta(minutes=slot_minutes)

                # Skip slots that are already in the past
                now = datetime.now(LONDON_TZ)
                if slot_start <= now:
                    t_min += slot_minutes
                    continue

                if (current, h, m) not in booked_times:
                    slots.append(
                        _Slot(
                            start_time=slot_start,
                            end_time=slot_end,
                            appointment_type_id=appointment_type_id,
                            practitioner_id=practitioner_id,
                            provider_slot_id=slot_start.isoformat(),
                        )
                    )
                t_min += slot_minutes
        current += timedelta(days=1)

    logger.info(
        "_generate_manual_slots: generated %d slots between %s and %s",
        len(slots), start_date, end_date,
    )
    return slots


# ---------------------------------------------------------------------------
# Acuity executor: check_availability
# ---------------------------------------------------------------------------

async def _check_availability_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """
    check_availability via Acuity Scheduling (Theorem clinic).

    Uses a progressive search window: 14 → 30 → 60 days, expanding automatically
    until slots are found. This ensures the next available appointment is always
    returned regardless of how far ahead it is.
    """
    from datetime import date as _date
    from app.clinic_config import THEOREM_LOCATIONS

    location = _normalize_location(args.get("location") or session.get("selected_location", ""))

    if not location and session.get("twilio_to") == "+447366530580":
        return {
            "error": "location_required",
            "error_detail": (
                "You must ask the caller which clinic they want before checking availability. "
                "Say: 'Which clinic would you like — say one for Alcester, or two for Redditch?' "
                "Then call collect_and_store(field='location', value='alcester' or 'redditch'), "
                "and only after that call check_availability again."
            ),
            "slots": [],
        }

    service = (args.get("service") or "physiotherapy assessment").strip()
    preference = (args.get("preference") or "").strip()

    # Explicit day_window from the LLM bypasses progressive search
    explicit_window = args.get("day_window")

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"error": "Booking system not configured. Please call the clinic directly.", "slots": []}

    try:
        # Always use the environment-configured appointment type ID.
        # /availability/times MUST always receive appointmentTypeID — calling
        # without it returns an empty list regardless of actual availability.
        appointment_type_id = DEFAULT_ACUITY_APPOINTMENT_TYPE_ID
        logger.info(
            "_check_availability_acuity: using appointment_type_id=%s (from env)",
            appointment_type_id,
        )

        loc_cfg = THEOREM_LOCATIONS.get(location, {})
        raw_cal_id = (loc_cfg.get("acuity_calendar_id") or "").strip()
        practitioner_id = f"acuity_cal_{raw_cal_id}" if raw_cal_id else None

        if location and not raw_cal_id:
            logger.warning(
                "No Acuity calendar ID configured for location %r — "
                "fetching all-calendar availability. "
                "Set ACUITY_CALENDAR_ID_%s on Render.",
                location, location.upper(),
            )

        today = _date.today()

        # Resolve after_date: earliest allowed date for returned slots
        after_date_str = (args.get("after_date") or "").strip()
        after_date_cutoff: "_date | None" = None
        if after_date_str:
            try:
                after_date_cutoff = _date.fromisoformat(after_date_str)
                logger.info(
                    "_check_availability_acuity: after_date_cutoff=%s (from args)",
                    after_date_cutoff,
                )
            except Exception:
                logger.warning(
                    "_check_availability_acuity: could not parse after_date=%r — ignoring",
                    after_date_str,
                )

        # Use after_date as the search start if it is later than today
        search_start = max(today, after_date_cutoff) if after_date_cutoff else today

        # Always scan a full 30-day window so near-term scarcity (bank holidays,
        # blocked days) doesn't leave the caller with only 1–2 options.
        # Explicit day_window from the LLM overrides this (e.g. for "next week").
        used_window = int(explicit_window) if explicit_window else 30
        end_date = search_start + timedelta(days=used_window)

        try:
            slots = await adapter.get_available_slots(
                appointment_type_id=appointment_type_id,
                start_date=search_start,
                end_date=end_date,
                practitioner_id=practitioner_id,
            )
        except Exception as api_err:
            logger.error(
                "_check_availability_acuity: Acuity API error location=%r window=%d: %r",
                location, used_window, api_err,
            )
            return {
                "error": (
                    f"Could not fetch availability for {location.title()}: {api_err}. "
                    "There may be a configuration issue — please call the clinic directly."
                ),
                "slots": [],
            }

        # Per-date logging: show how many raw slots Acuity returned per day
        if slots:
            from collections import defaultdict as _dd
            _per_day: dict = _dd(int)
            for _s in slots:
                _per_day[_s.start_time.date()] += 1
            for _day in sorted(_per_day):
                logger.info(
                    "_check_availability_acuity: %s — %d raw slot(s) from Acuity",
                    _day, _per_day[_day],
                )
            logger.info(
                "_check_availability_acuity: %d total raw slot(s) for %s over %d days",
                len(slots), location, used_window,
            )
        else:
            logger.info(
                "_check_availability_acuity: no slots for %s in %d days",
                location, used_window,
            )

        # ── Post-fetch filters ─────────────────────────────────────────────
        # 1. Minimum lead-time filter: drop slots starting within 2 hours of now.
        #    Prevents offering a 8:30 slot when the caller rings at 8:21 and
        #    the conversation itself takes several minutes.
        raw_slot_count = len(slots)  # count BEFORE lead-time filter
        if slots:
            now_london = datetime.now(LONDON_TZ)
            min_start  = now_london + timedelta(hours=2)
            before_lt  = len(slots)
            slots = [s for s in slots if s.start_time >= min_start]
            removed_lt = before_lt - len(slots)
            if removed_lt:
                logger.info(
                    "_check_availability_acuity: removed %d slot(s) within 2h lead-time window",
                    removed_lt,
                )

        # 2. Working-hours filter: remove slots outside configured clinic hours
        #    (guards against Acuity returning slots before open / after close).
        from app.clinic_config import get_clinic
        clinic_cfg = get_clinic(session.get("clinic_id", "theorem")) or {}
        loc_wh = clinic_cfg.get("location_working_hours", {})
        if slots and loc_wh:
            slots = _filter_slots_by_working_hours(slots, location, loc_wh)

        # 3. Bank-holiday filter: remove slots on England/Wales bank holidays.
        #    Always applied — _fetch_uk_bank_holidays() always returns at least
        #    the hardcoded 2025-2027 fallback set even if the GOV.UK API is down.
        #    NOTE: do NOT guard with "if bank_holidays:" — bool(empty set) is False
        #    and would silently skip the filter. Always run the comprehension.
        bank_holidays = await _fetch_uk_bank_holidays()
        if slots:
            before_bh = len(slots)
            slots = [s for s in slots if s.start_time.date() not in bank_holidays]
            removed_bh = before_bh - len(slots)
            if removed_bh:
                logger.info(
                    "_check_availability_acuity: removed %d slot(s) on UK bank holidays "
                    "(bank_holidays set size=%d)",
                    removed_bh, len(bank_holidays),
                )

        if not slots:
            # Distinguish between "lead-time filtered everything" vs "genuinely no slots"
            # so the LLM can use the appropriate message.
            if raw_slot_count > 0:
                # Slots existed but were all too soon (within 2h lead time).
                logger.warning(
                    "_check_availability_acuity: %d raw slot(s) for %s all within 2h lead-time window — "
                    "availability is limited today.",
                    raw_slot_count, location,
                )
                return {
                    "error": "lead_time_limited",
                    "error_detail": (
                        f"There are {raw_slot_count} slot(s) available at {location.title()} today "
                        "but all start within 2 hours — too soon to book. "
                        "Suggest the next available day or take contact details."
                    ),
                    "slots": [],
                }
            # Real Acuity availability returned nothing — report honestly.
            # Do NOT fall back to fake/manual slot generation.
            logger.warning(
                "_check_availability_acuity: 0 slots from Acuity for %s in %d days — "
                "no availability to offer.",
                location, used_window,
            )
            return {
                "error": "no_availability",
                "error_detail": (
                    f"No appointments available at {location.title()} in the next "
                    f"{used_window} days. The clinic may be fully booked — "
                    "please try the other location, or let the caller know the team will be in touch."
                ),
                "slots": [],
            }

        # Post-fetch date filter: safety net in case the Acuity API ignores start_date
        if after_date_cutoff:
            pre_filter_count = len(slots)
            slots = [s for s in slots if s.start_time.date() >= after_date_cutoff]
            logger.info(
                "_check_availability_acuity: after_date post-filter %s → %d/%d slots remaining",
                after_date_cutoff, len(slots), pre_filter_count,
            )

        # Build day-grouped structure for the day-first presentation flow.
        # Present exactly 3 slots (one per day where possible) so that
        # slot_labels[0/1/2] map 1:1 to the 1st/2nd/3rd slot spoken by Susie.
        slot_tuples = [(s.start_time, s.end_time) for s in slots]
        presented   = _select_presented_tuples(slot_tuples, preference=preference)
        # Build days_data from ALL slots so each day shows all its available times,
        # not just the one slot selected for variety by _select_presented_tuples.
        days_data   = _build_days_data(slot_tuples)

        pres_raw    = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in presented]
        pres_labels = [s.strftime("%a %d %b at %H:%M") for s, e in presented]

        session["last_offered_slots"]          = pres_raw
        session["slot_labels"]                 = pres_labels
        session["available_days"]              = days_data
        session["_acuity_appointment_type_id"] = appointment_type_id
        session["_acuity_practitioner_id"]     = practitioner_id

        return {"available_days": days_data, "total_days": len(days_data)}

    except Exception as e:
        logger.error("_check_availability_acuity unexpected error: %r", e, exc_info=True)
        return {"error": f"Availability check failed: {e}", "slots": []}


# ---------------------------------------------------------------------------
# Acuity executor: book_appointment
# ---------------------------------------------------------------------------

async def _book_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """book_appointment via Acuity Scheduling (Theorem clinic)."""
    from app.booking.booking.models import BookingRequest, InsuranceInfo
    from app.booking.booking.exceptions import SlotUnavailable, ProviderAuthError
    from app.notifications.booking_sms import send_booking_confirmation
    from app.clinic_config import get_clinic, THEOREM_LOCATIONS

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"success": False, "error": "Booking system not configured."}

    try:
        clinic = get_clinic(session.get("clinic_id"))
        location = _normalize_location(args.get("location") or session.get("selected_location", ""))

        if not location and session.get("twilio_to") == "+447366530580":
            return {
                "success": False,
                "error": (
                    "Location is required to complete this booking. "
                    "Ask: 'Which clinic would you like — say one for Alcester, or two for Redditch?' "
                    "Call collect_and_store(field='location', ...) first, then retry book_appointment."
                ),
            }

        service = (args.get("service") or "physiotherapy assessment").strip()
        patient_name = (args.get("patient_name") or "").strip()
        phone = (args.get("phone") or "").strip()
        is_new = bool(args.get("is_new_patient", True))
        insurer = (args.get("insurer_name") or "").strip()
        policy = (args.get("policy_number") or "").strip()

        if not patient_name or not phone:
            return {"success": False, "error": "patient_name and phone are required."}

        try:
            start_dt = _resolve_slot_iso(args.get("slot_iso", ""), session)
        except Exception as e:
            return {"success": False, "error": f"Invalid slot datetime: {e}"}

        # FIX #9: Reject slots in the past — a hallucinated or stale datetime
        # would reach Acuity and fail with a confusing error.
        now_london = datetime.now(LONDON_TZ)
        if start_dt <= now_london:
            return {
                "success": False,
                "error": (
                    f"Cannot book a slot in the past "
                    f"({start_dt.strftime('%a %d %b at %H:%M')}). "
                    "Please check availability again for current options."
                ),
            }

        # Appointment type — prefer cached from check_availability, else use env default.
        # appointmentTypeID MUST always be passed to Acuity for booking to succeed.
        appointment_type_id = (
            session.get("_acuity_appointment_type_id") or DEFAULT_ACUITY_APPOINTMENT_TYPE_ID
        )
        logger.info(
            "_book_appointment_acuity: using appointment_type_id=%s",
            appointment_type_id,
        )

        # Practitioner / calendar ID — prefer cached from check_availability
        practitioner_id = session.get("_acuity_practitioner_id")
        if not practitioner_id:
            loc_cfg = THEOREM_LOCATIONS.get(location, {})
            raw_cal_id = (loc_cfg.get("acuity_calendar_id") or "").strip()
            practitioner_id = f"acuity_cal_{raw_cal_id}" if raw_cal_id else None

        first_name, last_name = _split_name(patient_name)

        notes_parts = ["New patient" if is_new else "Returning patient"]
        if insurer:
            notes_parts.append(f"Insurance: {insurer}")
            if policy:
                notes_parts.append(f"Policy: {policy}")

        insurance_info = None
        if insurer:
            insurance_info = InsuranceInfo(
                provider_name=insurer,
                policy_number=policy or None,
            )

        request = BookingRequest(
            appointment_type_id=appointment_type_id,
            slot_start=start_dt,
            location_id=location,
            patient_first_name=first_name,
            patient_last_name=last_name,
            patient_phone=phone,
            notes=" | ".join(notes_parts),
            practitioner_id=practitioner_id,
            insurance_info=insurance_info,
            call_sid=session.get("call_sid", "unknown"),
            session_id=session.get("session_id", "unknown"),
        )

        booking = await adapter.create_booking(request)

        # Update session
        session.setdefault("collected", {})
        session["collected"]["name"] = patient_name
        session["collected"]["phone"] = phone
        session["collected"]["service"] = service
        session["collected"]["slot"] = args["slot_iso"]
        if insurer:
            session["collected"]["insurer"] = insurer
        session["acuity_booking_id"] = booking.provider_booking_id
        session["calendar_status"] = "created"

        # Confirmation SMS — non-fatal.
        # Suppressed when called as part of a reschedule (caller gets a reschedule
        # confirmation instead, sent by _reschedule_appointment_acuity).
        if not args.get("_suppress_sms"):
            try:
                await send_booking_confirmation(
                    patient_phone=phone,
                    patient_name=patient_name,
                    appointment_time=booking.start_time,
                    location=location.title(),
                    service=service,
                    is_new_patient=is_new,
                    has_insurance=bool(insurer),
                    insurer=insurer or None,
                    clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
                    clinic_phone=clinic.get("phone"),
                    session=session,
                )
            except Exception as e:
                logger.warning("_book_appointment_acuity SMS failed (non-fatal): %r", e)

        # Sheets log — non-fatal
        try:
            from app.tools.handoff import send_to_sheet
            await asyncio.to_thread(
                send_to_sheet,
                patient_name, phone, "BOOK",
                (
                    f"Booked: {service} at {location.title()} "
                    f"on {booking.start_time.strftime('%d %b %Y %H:%M')} "
                    f"(Acuity #{booking.provider_booking_id})"
                ),
                session.get("call_sid", ""),
                "Phase3 AI Receptionist",
            )
        except Exception as e:
            logger.warning("_book_appointment_acuity Sheets log failed (non-fatal): %r", e)

        return {
            "success": True,
            "acuity_booking_id": booking.provider_booking_id,
            "booked_slot": booking.start_time.strftime("%A %d %B at %H:%M"),
            "location": location.title(),
            "practitioner": booking.practitioner_name or "your practitioner",
        }

    except SlotUnavailable as e:
        logger.error(
            "[BOOKING FAILED] SlotUnavailable: location=%r service=%r slot=%r err=%r",
            args.get("location"), args.get("service"), args.get("slot_iso"), e,
        )
        return {
            "success": False,
            "error": "That slot has just been taken. Please call check_availability again for alternative times.",
        }
    except ProviderAuthError as e:
        logger.error(
            "[BOOKING FAILED] ProviderAuthError (Acuity credentials wrong or expired): %r", e,
        )
        return {
            "success": False,
            "error": "Booking system authentication error. Please ask the caller to call the clinic directly.",
        }
    except Exception as e:
        logger.error(
            "[BOOKING FAILED] Unexpected error: location=%r service=%r slot=%r "
            "patient=%r phone=%r appointment_type_id=%r practitioner_id=%r err=%r",
            args.get("location"), args.get("service"), args.get("slot_iso"),
            args.get("patient_name"), args.get("phone"),
            session.get("_acuity_appointment_type_id"),
            session.get("_acuity_practitioner_id"),
            e, exc_info=True,
        )
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Acuity executor: cancel_appointment
# ---------------------------------------------------------------------------

async def _cancel_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """cancel_appointment via Acuity Scheduling (Theorem clinic)."""
    from datetime import date as _date

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"success": False, "error": "Booking system not configured."}

    try:
        _cancel_location = _normalize_location(
            args.get("location") or session.get("selected_location", "")
        )
        if session.get("twilio_to") == "+447366530580" and not _cancel_location:
            return {
                "success": False,
                "error": (
                    "Location is required before cancelling. "
                    "Ask: 'Which clinic is your appointment at — say one for Alcester or two for Redditch?' "
                    "Call collect_and_store(field='location', ...) first, then retry cancel_appointment."
                ),
            }

        patient_name_lower = (args.get("patient_name") or "").strip().lower()
        today = datetime.now(LONDON_TZ).date()
        end = today + timedelta(days=60)

        from app.clinic_config import THEOREM_LOCATIONS as _TL
        _cal_id = (
            _TL.get(_cancel_location, {}).get("acuity_calendar_id")
            if _cancel_location else None
        )
        appointments = await adapter.list_appointments(min_date=today, max_date=end, calendar_id=_cal_id)

        found = None
        for appt in appointments:
            full = f"{appt.get('firstName', '')} {appt.get('lastName', '')}".strip().lower()
            if patient_name_lower and patient_name_lower in full:
                found = appt
                break

        if not found:
            return {
                "success": False,
                "error": "No upcoming appointment found for that name. Please check the name and try again.",
            }

        provider_id = str(found["id"])
        appt_time_str = found.get("datetime", "")
        appt_type = found.get("type", "appointment")

        success = await adapter.cancel_booking(provider_id)

        if not success:
            return {"success": False, "error": "Cancellation failed. Please ask the caller to call the clinic directly."}

        session["calendar_status"] = "cancelled"

        # SMS confirmation — non-fatal.
        # Suppressed when called as part of a reschedule (to avoid sending a cancel
        # SMS alongside the reschedule confirmation that the caller will also receive).
        if not args.get("_suppress_sms"):
            try:
                from app.notifications.booking_sms import send_cancellation_confirmation
                if appt_time_str:
                    dt = datetime.fromisoformat(appt_time_str.replace("Z", "+00:00"))
                    await send_cancellation_confirmation(
                        patient_phone=args.get("phone", ""),
                        patient_name=args.get("patient_name", ""),
                        appointment_time=dt,
                    )
            except Exception as e:
                logger.warning("_cancel_appointment_acuity SMS failed (non-fatal): %r", e)
            session["confirmation_sms_sent"] = True

        return {
            "success": True,
            "cancelled": appt_type,
            "was_at": appt_time_str,
        }

    except Exception as e:
        logger.error("_cancel_appointment_acuity error: %r", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Acuity executor: reschedule_appointment
# ---------------------------------------------------------------------------

async def _reschedule_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """
    reschedule_appointment via Acuity: cancel old appointment then book new one.
    Requires args to include both patient_name/phone/location (for finding old)
    and new_slot_iso/duration_minutes/service (for creating new).
    """
    # LOCATION GUARD for theorem_v2: resolve location before cancel+rebook.
    # Without this, cancel succeeds and book fails → appointment is lost with no new booking.
    if session.get("twilio_to") == "+447366530580":
        _early_location = _normalize_location(
            args.get("location") or session.get("selected_location", "")
        )
        if not _early_location:
            return {
                "success": False,
                "error": (
                    "Location is required before rescheduling. "
                    "Ask: 'Which clinic is your appointment at — Alcester or Redditch?' "
                    "Call collect_and_store(field='location', value='alcester' or 'redditch') first, "
                    "then retry reschedule_appointment."
                ),
            }

    # Step 1: cancel the existing appointment.
    # Suppress the cancel SMS — we'll send a single reschedule confirmation below.
    cancel_result = await _cancel_appointment_acuity(
        {**args, "_suppress_sms": True}, session
    )
    if not cancel_result.get("success"):
        return {
            "success": False,
            "error": f"Could not locate original appointment: {cancel_result.get('error')}",
        }

    # Step 2: book the new slot.
    # Also suppress the booking SMS for the same reason.
    book_args = {
        **args,
        "slot_iso": args["new_slot_iso"],  # map new_slot_iso → slot_iso for book executor
        "_suppress_sms": True,
    }
    book_result = await _book_appointment_acuity(book_args, session)

    if book_result.get("success"):
        session["calendar_status"] = "rescheduled"

        # Send ONE reschedule confirmation SMS (cancel + booking SMS already suppressed above)
        try:
            from app.notifications.booking_sms import send_reschedule_confirmation
            location = (args.get("location") or session.get("selected_location", "")).lower().strip()
            old_time_str = cancel_result.get("was_at", "")
            new_time = _resolve_slot_iso(args["new_slot_iso"], session)
            if old_time_str:
                old_time = datetime.fromisoformat(old_time_str.replace("Z", "+00:00"))
                await send_reschedule_confirmation(
                    patient_phone=args.get("phone", ""),
                    patient_name=args.get("patient_name", ""),
                    old_time=old_time,
                    new_time=new_time,
                    location=location.title(),
                )
        except Exception as e:
            logger.warning("_reschedule_appointment_acuity SMS failed (non-fatal): %r", e)

        session["confirmation_sms_sent"] = True

        return {
            "success": True,
            "rescheduled_to": book_result.get("booked_slot"),
            "location": book_result.get("location"),
            "acuity_booking_id": book_result.get("acuity_booking_id"),
        }
    else:
        return {
            "success": False,
            "error": (
                f"Old appointment cancelled but new booking failed: {book_result.get('error')}. "
                "Please ask the caller to call the clinic to complete rescheduling."
            ),
        }


# ===========================================================================
# GOOGLE CALENDAR — original executors (demo clinic + fallback)
# ===========================================================================

# ---------------------------------------------------------------------------
# Executor: check_availability
# ---------------------------------------------------------------------------

def _resolve_clinic_id(session: Dict[str, Any]) -> str:
    """
    Return the clinic_id for this session, re-deriving it if missing.

    Defensive helper — clinic_id should always be set by connection.py but
    in case the resolution chain failed (Twilio customParameters unreliable,
    Redis miss, env var not set), attempt to recover from session["twilio_to"]
    before falling back to "demo".
    """
    cid = session.get("clinic_id")
    if cid:
        return cid
    twilio_to = session.get("twilio_to", "")
    if twilio_to:
        from app.clinic_config import clinic_id_from_twilio_to
        cid = clinic_id_from_twilio_to(twilio_to)
        session["clinic_id"] = cid
        logger.warning(
            "_resolve_clinic_id: re-derived clinic_id=%s from twilio_to=%s",
            cid, twilio_to,
        )
        return cid
    logger.error(
        "_resolve_clinic_id: clinic_id missing and twilio_to empty — "
        "defaulting to 'demo'. Tools will use Google Calendar.",
    )
    return "demo"


async def _exec_check_availability(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # Theorem clinic uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) == "theorem":
        return await _check_availability_acuity(args, session)

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

    # Apply after_date: shift the search window start forward if caller is unavailable before that date
    after_date_str = (args.get("after_date") or "").strip()
    if after_date_str:
        try:
            from datetime import date as _gcal_date
            _after_naive = datetime.combine(
                _gcal_date.fromisoformat(after_date_str),
                datetime.min.time(),
            )
            _after_dt = LONDON_TZ.localize(_after_naive)
            if _after_dt > w_start:
                w_start = _after_dt
                logger.info(
                    "_exec_check_availability (gcal): after_date=%s applied, w_start shifted",
                    after_date_str,
                )
        except Exception as _ae:
            logger.warning(
                "_exec_check_availability (gcal): could not parse after_date=%r — ignoring: %r",
                after_date_str, _ae,
            )

    w_end = w_start + timedelta(days=day_window_days)

    candidates = generate_candidate_slots(
        w_start, w_end,
        duration_min=duration_min,
        clinic_working_hours=working_hours,
    )

    tokens = await _get_tokens()
    if not tokens:
        if not candidates:
            return {"error": "No slots found in the next 7 days.", "slots": []}
        presented  = _select_presented_tuples(candidates)
        days_data  = _build_days_data(presented)
        pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
        pres_labels = [format_slot(s) for s in presented]
        session["last_offered_slots"] = pres_raw
        session["slot_labels"]        = pres_labels
        session["available_days"]     = days_data
        return {"available_days": days_data, "total_days": len(days_data), "note": "calendar_not_connected"}

    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        busy_raw = await asyncio.to_thread(freebusy, tokens, w_start, w_end, calendar_id)
        await _save_gcal_tokens(tokens)   # persist any token refresh that happened inside freebusy
        busy_blocks = parse_busy(busy_raw or [])
        free_slots = filter_free_slots(candidates, busy_blocks)
    except Exception as e:
        # Calendar API failed — fall back to unfiltered candidate slots (same
        # behaviour as when calendar tokens are absent).  This keeps the
        # conversation alive so Susie can still offer times and the caller
        # can complete their booking.  Slots may overlap existing appointments
        # but that is far better than the conversation dying with an error.
        logger.error(
            "check_availability freebusy error: %r — falling back to unfiltered candidates", e
        )
        free_slots = candidates
        if not free_slots:
            return {"error": "No candidate slots found in the next 7 days.", "slots": []}
        presented  = _select_presented_tuples(free_slots)
        days_data  = _build_days_data(presented)
        pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
        pres_labels = [format_slot(s) for s in presented]
        session["last_offered_slots"] = pres_raw
        session["slot_labels"]        = pres_labels
        session["available_days"]     = days_data
        return {"available_days": days_data, "total_days": len(days_data), "note": "calendar_check_failed_unfiltered"}

    if not free_slots:
        return {"error": "No available slots found. Try a different time preference or wider window.", "slots": []}

    presented  = _select_presented_tuples(free_slots)
    days_data  = _build_days_data(presented)
    pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
    pres_labels = [format_slot(s) for s in presented]
    session["last_offered_slots"] = pres_raw
    session["slot_labels"]        = pres_labels
    session["available_days"]     = days_data
    return {"available_days": days_data, "total_days": len(days_data)}


# ---------------------------------------------------------------------------
# Executor: book_appointment
# ---------------------------------------------------------------------------

async def _exec_book_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # Theorem clinic uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) == "theorem":
        return await _book_appointment_acuity(args, session)

    from app.tools.calendar_google import create_event
    from app.notifications.booking_sms import send_booking_confirmation
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()
    patient_name = args.get("patient_name", "")
    phone = args.get("phone", "")
    service = args.get("service", "physiotherapy")
    is_new = bool(args.get("is_new_patient", False))
    insurer = (args.get("insurer_name") or "").strip()
    policy = (args.get("policy_number") or "").strip()

    # Resolve slot_iso — handles ISO strings, labels, and slot indices
    try:
        start_dt = _resolve_slot_iso(args.get("slot_iso", ""), session)
        end_dt = start_dt + timedelta(minutes=int(args.get("duration_minutes", 30)))
    except Exception as e:
        return {"success": False, "error": f"Invalid slot datetime: {e}"}

    # FIX #9: Reject slots in the past
    now_london = datetime.now(LONDON_TZ)
    if start_dt <= now_london:
        return {
            "success": False,
            "error": (
                f"Cannot book a slot in the past "
                f"({start_dt.strftime('%a %d %b at %H:%M')}). "
                "Please check availability again for current options."
            ),
        }

    if not tokens:
        # Calendar not connected — log intent to Sheets so the clinic can follow up,
        # then tell Claude the booking succeeded so it doesn't loop with "slot unavailable".
        booked_label = start_dt.strftime("%A %d %B at %H:%M")
        try:
            from app.tools.handoff import send_to_sheet
            await asyncio.to_thread(
                send_to_sheet,
                patient_name, phone, "BOOK_MANUAL",
                (
                    f"MANUAL BOOKING NEEDED: {service} at {location.title()} on {booked_label} "
                    f"({'new' if is_new else 'returning'} patient)"
                    + (f" | Insurer: {insurer}" if insurer else "")
                    + (f" | Policy: {policy}" if policy else "")
                ),
                session.get("call_sid", ""),
                "Phase3 AI Receptionist",
            )
        except Exception as e:
            logger.warning("book_appointment (no calendar) Sheets log failed (non-fatal): %r", e)

        session.setdefault("collected", {})
        session["collected"]["name"] = patient_name
        session["collected"]["phone"] = phone
        session["collected"]["service"] = service
        session["collected"]["slot"] = start_dt.isoformat()
        session["calendar_status"] = "manual_followup"

        return {
            "success": True,
            "booked_slot": booked_label,
            "location": location.title(),
            "note": "Calendar not connected — logged for manual confirmation by clinic team.",
        }

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
        await _save_gcal_tokens(tokens)   # persist any token refresh that happened inside create_event
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
            session=session,
        )
        # Tell the smart SMS router at call end that a confirmation was already sent
        session["confirmation_sms_sent"] = True
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
    # Theorem clinic uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) == "theorem":
        return await _cancel_appointment_acuity(args, session)

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

    # Prevent smart router from sending a duplicate follow-up SMS
    session["confirmation_sms_sent"] = True

    return {
        "success": True,
        "cancelled_event": event_summary,
        "was_at": event_start,
    }


# ---------------------------------------------------------------------------
# Executor: reschedule_appointment
# ---------------------------------------------------------------------------

async def _exec_reschedule_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # Theorem clinic uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) == "theorem":
        return await _reschedule_appointment_acuity(args, session)

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
        new_start = _resolve_slot_iso(args.get("new_slot_iso", ""), session)
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

    # Prevent smart router from sending a duplicate follow-up SMS
    session["confirmation_sms_sent"] = True

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
    elif topic == "transport":
        text = (loc_cfg.get("transport") if loc_cfg else None) or clinic.get("transport", "")
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

_WORD_DIGIT_MAP: Dict[str, str] = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9", "niner": "9",
}


def _spoken_to_digits(text: str) -> str:
    """
    Convert a spoken/transcribed UK phone number to a digit string.

    Handles every format ASR may produce:
    - Pure digit string: "07870166861" → "07870166861"
    - E.164 / with prefix: "+447870166861" → returned as-is (normalize_phone handles it)
    - Spoken words: "zero seven eight seven oh one six six eight six one" → "07870166861"
    - Space-separated single digits: "0 7 8 7 0 1 6 6 8 6 1" → "07870166861"
    - Grouped digits: "07870 166861" → "07870166861"
    - Mixed: "07870 one six six eight six one" → "07870166861"
    - Letter O for zero: "O 7 8 7 0" → "07870"
    - double/treble shorthand: "double six" → "66"
    - Digits with punctuation: "078-701-66-861" → "07870166861"
    """
    import re as _re

    stripped = text.strip()

    # Fast path: already looks like a formatted phone number (only digits, spaces,
    # hyphens, dots, parens, and optional leading +)
    # Let normalize_phone handle E.164 / already-formatted strings directly.
    _clean = _re.sub(r'[\s\-\.\(\)\+]', '', stripped)
    if _clean.isdigit() and len(_clean) >= 7:
        return stripped  # pass through unchanged; normalize_phone strips non-digits

    # Expand "double X" → "X X" and "treble X" → "X X X"
    text = _re.sub(
        r'\bdouble\s+(\w+)',
        lambda m: f"{m.group(1)} {m.group(1)}",
        stripped, flags=_re.IGNORECASE,
    )
    text = _re.sub(
        r'\btreble\s+(\w+)',
        lambda m: f"{m.group(1)} {m.group(1)} {m.group(1)}",
        text, flags=_re.IGNORECASE,
    )

    tokens = _re.split(r'[\s\-,\.\;\(\)]+', text.lower())
    digits: list[str] = []
    for token in tokens:
        if not token:
            continue
        # Pure digit chunk (e.g. "07870", "7", "166")
        if token.isdigit():
            digits.append(token)
            continue
        # Exact word match (e.g. "zero", "oh", "seven")
        if token in _WORD_DIGIT_MAP:
            digits.append(_WORD_DIGIT_MAP[token])
            continue
        # Mixed token like "o7870" or "oh7" — scan char by char
        # Try longest-matching word first, then single char
        i = 0
        local: list[str] = []
        while i < len(token):
            if token[i].isdigit():
                local.append(token[i])
                i += 1
                continue
            # Try to match a known word at this position (longest match first)
            matched = False
            for word in sorted(_WORD_DIGIT_MAP.keys(), key=len, reverse=True):
                if token[i:i + len(word)] == word:
                    local.append(_WORD_DIGIT_MAP[word])
                    i += len(word)
                    matched = True
                    break
            if not matched:
                i += 1  # skip unrecognised character
        if local:
            digits.append("".join(local))
        # non-digit, non-word tokens ("my", "number", "is") → ignored

    return "".join(digits)


async def _exec_collect_and_store(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    field = args.get("field", "")
    value = (args.get("value") or "").strip()

    if not field or not value:
        return {"error": "field and value are required"}

    # Guard: reject confirmation words stored as phone numbers.
    # Catches the common LLM mistake of collect_and_store(field="phone", value="yes")
    # when the caller confirmed their caller_number — the actual digits must be stored.
    if field == "phone":
        _CONFIRM_WORDS = {
            "yes", "yeah", "yep", "yup", "correct", "that's right", "that's it",
            "right", "sure", "ok", "okay", "confirmed", "affirmative",
        }
        if value.lower() in _CONFIRM_WORDS:
            return {
                "error": (
                    f"'{value}' is not a valid phone number — you stored a confirmation word. "
                    "Store the actual phone number digits, not the caller's spoken confirmation. "
                    "Check caller_number in the known context and call collect_and_store again "
                    "with those exact digits as the value."
                )
            }

    session.setdefault("collected", {})

    # Normalise phone: convert spoken words to digits, then to E.164
    if field == "phone":
        # First convert any word-based digits ("zero seven eight..." → "0780...")
        converted = _spoken_to_digits(value)
        if converted:
            value = converted
        try:
            from app.flows.triage_legacy import normalize_phone
            value = normalize_phone(value)
        except Exception:
            pass

        # Guard: reject partial phone numbers — UK mobiles are 11 digits (07xxx xxxxxxx).
        # Catching 5-digit partials here prevents the "first five digits" mid-collection
        # from being silently stored and causing the booking to proceed with bad data.
        import re as _re
        _digit_count = len(_re.sub(r"\D", "", value))
        if _digit_count < 10:
            return {
                "error": (
                    f"Partial phone number — only {_digit_count} digit(s) received. "
                    "Do NOT store phone after the first five digits alone. "
                    "Ask for the remaining digits (Part 2), combine both parts into the "
                    "full number, confirm it with the caller, THEN call collect_and_store."
                )
            }

    # Keep session location keys in sync (normalise STT variants → canonical ID)
    if field == "location":
        session["selected_location"] = _normalize_location(value)
        session["location_selected"] = True

    # full_name is the preferred field for collecting the caller's name as a
    # single utterance.  Store under both "full_name" and "name" so all
    # downstream code (booking, context display, call summary) continues to
    # work regardless of which key it reads from.
    if field == "full_name":
        session["collected"]["full_name"] = value
        session["collected"]["name"] = value
        return {"ok": True}

    session["collected"][field] = value
    return {"ok": True}


# ---------------------------------------------------------------------------
# Executor: transfer_to_human
# ---------------------------------------------------------------------------

async def _exec_transfer_to_human(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    reason = (args.get("reason") or "caller requested").strip()

    # Critical: this flag is checked by twilio.py after handle_turn returns
    session["request_transfer"] = True
    session["human_requested"] = True
    session["manual_followup_reason"] = reason

    collected = session.get("collected") or {}
    caller_name  = collected.get("name", "")
    caller_phone = session.get("twilio_from", "") or collected.get("phone", "")
    call_reason  = collected.get("reason", "") or reason

    # Fire-and-forget heads-up SMS — don't block the tool return waiting for Twilio
    try:
        from app.clinic_config import get_clinic
        from app.notifications.sms import send_sms
        clinic = get_clinic(session.get("clinic_id"))
        transfer_phone = clinic.get("transfer_phone", "")
        if transfer_phone:
            caller_snippet = (
                f" from {caller_phone}"
                if (caller_phone and not caller_phone.startswith("client:"))
                else ""
            )
            asyncio.create_task(send_sms(
                to=transfer_phone,
                message=f"📞 Susie is transferring a patient{caller_snippet} — call coming through now.",
            ))
    except Exception as e:
        logger.warning("transfer_to_human SMS alert failed (non-fatal): %r", e)

    # Fire-and-forget Sheets log — don't block the tool return waiting for Sheets
    try:
        from app.tools.handoff import send_to_sheet
        asyncio.create_task(asyncio.to_thread(
            send_to_sheet,
            caller_name or "Unknown",
            caller_phone or collected.get("phone", ""),
            "TRANSFER",
            f"Transfer requested: {reason}",
            session.get("call_sid", ""),
            "Phase3 AI Receptionist",
        ))
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
    session["call_ended"] = True  # Signal to pipeline that call should wind down

    # Fire-and-forget: log to Google Sheets if configured
    try:
        from app.tools.handoff import fire_and_forget_append_summary_row
        fire_and_forget_append_summary_row(session)
    except Exception:
        pass

    return {"logged": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# Tool executor registry
# ---------------------------------------------------------------------------

async def _exec_get_patient_history(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """Look up a patient's recent appointment history in Acuity to identify their treatment."""
    if session.get("clinic_id") != "theorem":
        return {"found": False, "message": "Patient history lookup only available for Theorem clinic"}

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"found": False, "message": "Scheduling system not configured"}

    patient_name = (args.get("patient_name") or "").strip()
    if not patient_name:
        return {"found": False, "message": "Patient name required"}

    today = datetime.now(LONDON_TZ).date()
    min_date = today - timedelta(days=120)   # look back 4 months
    max_date = today + timedelta(days=30)    # include upcoming sessions on the plan

    try:
        appointments = await adapter.list_appointments(min_date=min_date, max_date=max_date)
    except Exception as exc:
        logger.warning("get_patient_history: list_appointments failed: %r", exc)
        return {"found": False, "message": "Could not retrieve appointment history"}

    # Match by name — fuzzy matching with rapidfuzz (handles typos, accents)
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None

    name_lower = patient_name.strip().lower()
    matching = []
    for appt in appointments:
        first = (appt.get("firstName") or "").lower()
        last  = (appt.get("lastName") or "").lower()
        full  = f"{first} {last}".strip()
        if not full:
            continue
        if fuzz:
            score = fuzz.token_sort_ratio(name_lower, full)
            if score >= 75:
                matching.append(appt)
        else:
            # Fallback: substring match (original behaviour)
            name_parts = [p.lower() for p in patient_name.split() if p]
            if any(part in full for part in name_parts):
                matching.append(appt)

    if not matching:
        return {"found": False, "message": f"No appointments found for {patient_name}"}

    # Sort most-recent first
    def _dt(a: dict) -> datetime:
        try:
            raw = a.get("datetime") or a.get("time") or ""
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=pytz.utc)

    matching.sort(key=_dt, reverse=True)

    # Collect unique treatment types from the 5 most recent appointments
    seen: list = []
    for appt in matching[:5]:
        t = (appt.get("type") or "").strip()
        if t and t not in seen:
            seen.append(t)

    most_recent_type = seen[0] if seen else "physiotherapy"
    return {
        "found": True,
        "most_recent_type": most_recent_type,
        "recent_types": seen,
        "appointment_count": len(matching),
    }


async def _exec_add_to_waitlist(args: dict, session: dict) -> dict:
    """Add a caller to the clinic waitlist in Redis."""
    patient_name = (args.get("patient_name") or "").strip()
    phone = (args.get("phone") or "").strip()
    location = (args.get("location") or "").strip()
    service = (args.get("service") or "").strip()
    notes = (args.get("notes") or "").strip()

    if not patient_name or not phone:
        return {"error": "Name and phone are required for the waitlist."}

    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            import json as _json
            waitlist_entry = {
                "patient_name": patient_name,
                "phone": phone,
                "location": location,
                "service": service,
                "notes": notes,
                "added_at": _iso_now(),
                "call_sid": session.get("call_sid", ""),
            }
            key = f"waitlist:{phone}:{patient_name.lower().replace(' ', '_')}"
            await redis_client.setex(key, 60 * 60 * 24 * 30, _json.dumps(waitlist_entry))  # 30 days
            logger.info("Waitlist entry added for ***%s", phone[-4:] if phone else "????")
            return {"success": True, "message": f"{patient_name} has been added to the waitlist."}
        else:
            # No Redis — store in session for later pickup
            session.setdefault("waitlist_request", {
                "patient_name": patient_name,
                "phone": phone,
                "location": location,
                "service": service,
                "notes": notes,
            })
            return {"success": True, "message": f"{patient_name} has been added to the waitlist."}
    except Exception as exc:
        logger.error("Waitlist add failed: %r", exc)
        return {"error": "I wasn't able to add you to the waitlist — the team will follow up."}


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
    "get_patient_history":    _exec_get_patient_history,
    "add_to_waitlist":        _exec_add_to_waitlist,
}
