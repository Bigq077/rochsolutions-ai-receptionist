# app/flows/triage.py
from __future__ import annotations

from typing import Dict, Any, Tuple, Optional, List
import re
from datetime import datetime, timedelta
import pytz

from app.storage.redis_store import redis_get_json
from app.clinic_config import CLINICS
from app.tools.calendar_google import (
    create_event,
    freebusy,
    list_upcoming_events,
    patch_event_time,
    delete_event,
)
from app.tools.slots import (
    next_7_days_window,
    generate_candidate_slots,
    parse_busy,
    filter_free_slots,
    pick_first_n,
    format_slot,
)

# Optional: Google Sheet handoff (won't crash if file/env not ready yet)
try:
    from app.tools.handoff import send_to_sheet  # type: ignore
except Exception:
    send_to_sheet = None  # type: ignore


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
TOKENS_KEY = "google_tokens"
DEFAULT_DURATION_MIN = 30

ACTIVE_CLINIC_KEY = "active_clinic"
LAST_OFFERED_SLOTS_KEY = "last_offered_slots"
SELECTED_SLOT_KEY = "selected_slot"
SLOT_LABELS_KEY = "slot_labels"
SELECTED_SLOT_LABEL_KEY = "selected_slot_label"


# -----------------------------------------------------------------------------
# TEXT / PARSING HELPERS
# -----------------------------------------------------------------------------
def _norm(t: str) -> str:
    t = (t or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _contains_any(t: str, keywords: List[str]) -> bool:
    return any(k in t for k in keywords)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _looks_like_option_123(text: str) -> Optional[int]:
    m = re.search(r"\b(1|2|3)\b", _norm(text))
    if not m:
        return None
    return int(m.group(1))


def _is_yes(text: str) -> bool:
    return _norm(text) in ("yes", "y", "yeah", "yep", "confirm", "confirmed", "ok", "okay", "sure")


def _is_no(text: str) -> bool:
    return _norm(text) in ("no", "n", "nope", "cancel", "stop", "nevermind", "never mind")


def normalize_phone(phone: str) -> str:
    return _digits_only(phone)


def is_valid_phone(phone: str) -> bool:
    p = normalize_phone(phone)
    return 10 <= len(p) <= 15


def _safe_first_clinic_key() -> str:
    # If user removes "demo", we still need a valid default.
    if not CLINICS:
        return "demo"
    return next(iter(CLINICS.keys()))


def get_clinic(session: Dict[str, Any]) -> Dict[str, Any]:
    key = session.get(ACTIVE_CLINIC_KEY) or _safe_first_clinic_key()
    if key in CLINICS:
        return CLINICS[key]
    # fallback to first clinic
    return CLINICS[_safe_first_clinic_key()]


def get_tz(clinic: Dict[str, Any]):
    return pytz.timezone(clinic.get("timezone", "Europe/London"))


def clinic_default_hours(clinic: Dict[str, Any]) -> tuple[int, int]:
    wh = clinic.get("working_hours", {}) or {}
    mon = wh.get("mon")
    if isinstance(mon, (list, tuple)) and len(mon) == 2:
        return int(mon[0]), int(mon[1])
    return 9, 18


def parse_patient_type(text: str) -> Optional[str]:
    t = _norm(text)
    returning = [
        "returning",
        "existing",
        "come back",
        "coming back",
        "been before",
        "follow up",
        "follow-up",
        "followup",
        "i've been",
        "i have been",
        "already a patient",
        "return patient",
    ]
    new = [
        "new",
        "first time",
        "never been",
        "not been before",
        "initial",
        "first visit",
        "new patient",
    ]
    if any(k in t for k in returning):
        return "RETURNING"
    if any(k in t for k in new):
        return "NEW"
    return None


def preference_window(pref: str) -> Optional[tuple[int, int]]:
    p = _norm(pref)
    if "morning" in p:
        return (9, 12)
    if "afternoon" in p:
        return (12, 17)
    if "evening" in p or "after work" in p or "afterwork" in p:
        return (17, 20)
    return None


WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_specific_day_window(text: str, tz) -> Optional[tuple[datetime, datetime]]:
    """
    Supports: today, tomorrow, weekday (tuesday), "next tuesday"
    Returns: local day window (00:00 .. 23:59:59)
    """
    t = _norm(text)
    now = datetime.now(tz)

    if "today" in t:
        day = now
    elif "tomorrow" in t:
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = base + timedelta(days=1)
    else:
        wd = None
        for k, v in WEEKDAYS.items():
            if re.search(rf"\b{k}\b", t):
                wd = v
                break
        if wd is None:
            return None

        days_ahead = (wd - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7 if "next" in t else 0

        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = base + timedelta(days=days_ahead)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=59, microsecond=0)
    return start, end


# -----------------------------------------------------------------------------
# INTENTS
# -----------------------------------------------------------------------------
def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    # Booking
    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    # Cancel / reschedule
    if _contains_any(t, ["cancel", "cancellation", "call it off", "call it off", "cancel my appointment"]):
        return "CANCEL"

    if _contains_any(t, ["reschedule", "move", "rebook", "postpone", "change my appointment", "change my booking"]):
        return "RESCHEDULE"

    # FAQs
    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pricing", "payment", "pay"]):
        return "FAQ_PRICES"

    if _contains_any(t, ["hours", "open", "close", "opening", "when are you open", "weekend", "saturday", "sunday"]):
        return "FAQ_HOURS"

    if _contains_any(t, ["address", "location", "where are you", "parking", "postcode", "directions", "near", "map"]):
        return "FAQ_LOCATION"

    if _contains_any(t, ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"]):
        return "FAQ_INSURANCE"

    if _contains_any(t, ["physio", "physiotherapy", "massage", "sports therapy", "rehab", "shockwave"]):
        return "FAQ_SERVICES"

    if _contains_any(t, ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica", "sprain", "strain"]):
        return "FAQ_CONDITIONS"

    if _contains_any(t, ["referral", "gp", "doctor", "nhs", "letter", "prescription"]):
        return "FAQ_REFERRAL"

    if _contains_any(t, ["what should i bring", "what do i wear", "arrive", "late"]):
        return "FAQ_FIRST_VISIT"

    if _contains_any(t, ["cancel policy", "cancellation policy", "late fee", "refund", "missed appointment"]):
        return "FAQ_POLICIES"

    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"

    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to", "call back"]):
        return "HUMAN"

    return "OTHER"


# -----------------------------------------------------------------------------
# STATES
# -----------------------------------------------------------------------------
TRIAGE = "TRIAGE"

# Booking
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_PICK_SLOT = "BOOK_PICK_SLOT"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_CONFIRM_SLOT = "BOOK_CONFIRM_SLOT"

# Reschedule (best UX): name -> original datetime -> new preferred datetime -> confirm
RESCH_NAME = "RESCH_NAME"
RESCH_ORIGINAL = "RESCH_ORIGINAL"
RESCH_NEW_PREF = "RESCH_NEW_PREF"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_PICK_SLOT = "RESCH_PICK_SLOT"
RESCH_CONFIRM = "RESCH_CONFIRM"

# Cancel: name -> phone -> find -> confirm
CANCEL_NAME = "CANCEL_NAME"
CANCEL_PHONE = "CANCEL_PHONE"
CANCEL_FIND = "CANCEL_FIND"
CANCEL_CONFIRM = "CANCEL_CONFIRM"

# Human callback capture
CALLBACK_NAME = "CALLBACK_NAME"
CALLBACK_PHONE = "CALLBACK_PHONE"
CALLBACK_MESSAGE = "CALLBACK_MESSAGE"


# -----------------------------------------------------------------------------
# RESET
# -----------------------------------------------------------------------------
def _reset_to_triage(session: Dict[str, Any]) -> Dict[str, Any]:
    session["state"] = TRIAGE
    session["intent"] = None
    session["collected"] = {}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    session.pop(SLOT_LABELS_KEY, None)
    session.pop(SELECTED_SLOT_LABEL_KEY, None)
    session.pop("resch_event_id", None)
    session.pop("resch_event_summary", None)
    return session


# -----------------------------------------------------------------------------
# FAQ
# -----------------------------------------------------------------------------
def faq_answer(intent: str, user_said: str, clinic: Dict[str, Any]) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return clinic.get("pricing_summary", "Please tell me what you’re looking to book and I’ll confirm the price.")

    if intent == "FAQ_HOURS":
        return clinic.get("hours_summary", "We’re open Monday to Friday during the day.")

    if intent == "FAQ_LOCATION":
        # Required phrasing: "Roch Physio is located at ..."
        address = clinic.get("address", "Roch Physio is located at 12 High Street, Coventry, CV1.")
        parking = clinic.get("parking", "")
        if "parking" in t and parking:
            return f"{address} Parking: {parking}"
        return address

    if intent == "FAQ_INSURANCE":
        return clinic.get("insurance_note", "If you tell me your insurer, I can note it for the clinic.")

    if intent == "FAQ_SERVICES":
        services = clinic.get("services", [])
        if services:
            return "We offer: " + ", ".join(services) + "."
        return "We offer physiotherapy assessment and treatment, follow-ups, sports massage, and rehab plans."

    if intent == "FAQ_CONDITIONS":
        return "We commonly help with back pain, neck or shoulder pain, sports injuries, joint pain, and post-operative rehab."

    if intent == "FAQ_REFERRAL":
        return "A GP referral isn’t usually required for private appointments, but some insurance policies do require one."

    if intent == "FAQ_FIRST_VISIT":
        return clinic.get("what_to_bring", "Please wear comfortable clothing and bring any relevant scans or reports if you have them.")

    if intent == "FAQ_POLICIES":
        return clinic.get("cancellation_policy", "Please give at least 24 hours’ notice to avoid a late cancellation fee.")

    if intent == "FAQ_PRIVACY":
        return "Your information is treated as confidential and handled in line with data protection rules."

    return "Tell me what you need help with and I’ll point you in the right direction."


# -----------------------------------------------------------------------------
# CALENDAR HELPERS
# -----------------------------------------------------------------------------
async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: Optional[int] = None,
    pref_text: Optional[str] = None,
    day_window: Optional[tuple[datetime, datetime]] = None,
) -> tuple[list[dict], list[str], Optional[str]]:
    """
    Always returns slots (demo fallback) so the assistant never says “let me check” and then hangs.
    """
    clinic = get_clinic(session)
    tz = get_tz(clinic)

    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    # Base window
    w_start, w_end = next_7_days_window()
    if day_window:
        w_start, w_end = day_window

    # Preference hours
    win = preference_window(pref_text or "")
    if win:
        day_start_h, day_end_h = win
    else:
        day_start_h, day_end_h = clinic_default_hours(clinic)

    tokens = await redis_get_json(TOKENS_KEY)

    # Candidate slots
    candidates = generate_candidate_slots(
        w_start,
        w_end,
        duration_min=duration_min,
        day_start_h=day_start_h,
        day_end_h=day_end_h,
    )

    # DEMO MODE (no calendar)
    if not tokens:
        top3 = pick_first_n(candidates, 3)
        if not top3:
            return [], [], "I couldn’t find any slots in the next 7 days. Tell me another day or time."
        raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
        labels = [format_slot((s, e)) for s, e in top3]
        return raw_slots, labels, None

    # REAL MODE (calendar free/busy)
    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id=clinic.get("calendar_id", "primary"))
    busy_blocks = parse_busy(busy)
    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    # If preference too strict, relax to clinic hours
    if not top3 and win:
        dsh, deh = clinic_default_hours(clinic)
        candidates2 = generate_candidate_slots(
            w_start,
            w_end,
            duration_min=duration_min,
            day_start_h=dsh,
            day_end_h=deh,
        )
        free_slots2 = filter_free_slots(candidates2, busy_blocks)
        top3 = pick_first_n(free_slots2, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days. I can take a message for the clinic to call you back."

    raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw_slots, labels, None


async def find_event_for_patient_by_phone(session: Dict[str, Any], phone: str) -> Optional[Dict[str, Any]]:
    """
    Calendar-backed search:
    - List upcoming events next 30 days
    - Match digits in description
    """
    clinic = get_clinic(session)
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return None

    target = normalize_phone(phone)
    if not target:
        return None

    events = list_upcoming_events(tokens, days_ahead=30, max_results=25, calendar_id=clinic.get("calendar_id", "primary"))
    for ev in events:
        desc = ev.get("description") or ""
        if target in _digits_only(desc):
            return ev
    return None


# -----------------------------------------------------------------------------
# MAIN STATE MACHINE
# -----------------------------------------------------------------------------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not user_said:
        return "Sorry — I didn’t catch that. Could you repeat?", session

    clinic = get_clinic(session)
    state = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})
    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)

    # Global commands
    if _norm(user_said) in ("restart", "start over", "reset"):
        session = _reset_to_triage(session)
        return "Okay — starting over. What would you like to do?", session

    if _norm(user_said) in ("bye", "goodbye", "stop"):
        session = _reset_to_triage(session)
        return "Okay. Goodbye.", session

    # Repeat helper when choosing slots
    if _norm(user_said) in ("repeat", "say again") and state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
        labels = session.get(SLOT_LABELS_KEY) or []
        if len(labels) >= 3:
            return f"Options are: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session
        return "Please say 1, 2, or 3.", session

    # -------------------------------------------------------------------------
    # CALLBACK (HUMAN) FLOW
    # -------------------------------------------------------------------------
    if state == CALLBACK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = CALLBACK_PHONE
        return "Thanks. What’s the best phone number for the clinic to call you on?", session

    if state == CALLBACK_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return "Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = CALLBACK_MESSAGE
        return "Thanks. What would you like the clinic to know?", session

    if state == CALLBACK_MESSAGE:
        collected["message"] = user_said.strip()

        if send_to_sheet is not None:
            try:
                send_to_sheet(
                    name=collected.get("name", ""),
                    phone=collected.get("phone", ""),
                    intent="CALLBACK",
                    message=collected.get("message", ""),
                    call_sid=session.get("call_sid", ""),
                )
            except Exception:
                pass

        session = _reset_to_triage(session)
        return "No problem. I’ve passed this to the clinic and someone will call you back shortly.", session

    # -------------------------------------------------------------------------
    # RESCHEDULE FLOW (requested UX)
    # - DO NOT ask “are you an existing patient”
    # - Ask: name -> original appointment date/time -> new preference -> offer slots -> confirm
    # - If calendar connected, we’ll try to update the event (using phone lookup only if needed)
    #   But for clean UX we keep it simple: we offer slots; if tokens exist and we have a matching event id, patch it.
    #   If we can’t locate the event, we still confirm and (optionally) hand off to sheet.
    # -------------------------------------------------------------------------
    if state == RESCH_NAME:
        collected["name"] = user_said.strip()
        session["state"] = RESCH_ORIGINAL
        return "Thanks. What was the date and time of your original appointment?", session

    if state == RESCH_ORIGINAL:
        collected["original_appt"] = user_said.strip()
        session["state"] = RESCH_NEW_PREF
        return "What day or time would you like instead? For example, Tuesday afternoon.", session

    if state == RESCH_NEW_PREF:
        pref_text = user_said.strip()
        collected["time_pref"] = pref_text

        tz = get_tz(clinic)
        dw = parse_specific_day_window(pref_text, tz)
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"] = dw[1].isoformat()
        else:
            collected.pop("day_window_start", None)
            collected.pop("day_window_end", None)

        session["state"] = RESCH_OFFER_SLOTS
        return "Okay — I’m checking availability now.", session

    if state == RESCH_OFFER_SLOTS:
        pref = collected.get("time_pref", "")
        dw = None
        if collected.get("day_window_start") and collected.get("day_window_end"):
            dw = (
                datetime.fromisoformat(collected["day_window_start"]),
                datetime.fromisoformat(collected["day_window_end"]),
            )

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=pref,
            day_window=dw,
        )
        if err:
            # If reschedule fails to find slots, offer callback capture
            session = _reset_to_triage(session)
            session["state"] = CALLBACK_NAME
            collected2 = session.setdefault("collected", {})
            collected2["message"] = f"Reschedule request: {collected.get('name','')} / original {collected.get('original_appt','')} / preference {pref}"
            return f"{err} I can take your details and the clinic will call you back. What’s your name?", session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY] = labels
        session["state"] = RESCH_PICK_SLOT
        return f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session

    if state == RESCH_PICK_SLOT:
        opt = _looks_like_option_123(user_said)
        if opt is None:
            return "Please say 1, 2, or 3.", session

        idx = opt - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

        session["state"] = RESCH_CONFIRM
        chosen_label = session.get(SELECTED_SLOT_LABEL_KEY) or f"option {opt}"
        return f"Confirm rescheduling to {chosen_label}? Say yes or no.", session

    if state == RESCH_CONFIRM:
        if _is_no(user_said):
            session = _reset_to_triage(session)
            return "Okay — not rescheduled. What would you like to do?", session

        if not _is_yes(user_said):
            return "Please say yes to confirm, or no to cancel.", session

        chosen = session.get(SELECTED_SLOT_KEY)
        chosen_label = session.get(SELECTED_SLOT_LABEL_KEY) or "the selected time"
        tokens = await redis_get_json(TOKENS_KEY)

        # Best effort calendar patch:
        # We *try* to locate event by phone only if we already have it (we don't in this UX),
        # so we confirm and optionally hand off to sheet if we can't patch.
        patched = False
        if tokens and chosen:
            # If you want true patching, you need a reliable lookup key.
            # Optional: if you store phone in event description, you can ask for phone in this flow too.
            # For now: we do not ask phone (per your requirement), so we do not patch blindly.
            patched = False

        # Handoff to sheet as a “task” for clinic (recommended for client-ready reliability)
        if send_to_sheet is not None:
            try:
                send_to_sheet(
                    name=collected.get("name", ""),
                    phone=collected.get("phone", ""),
                    intent="RESCHEDULE",
                    message=f"Original: {collected.get('original_appt','')}; New slot: {chosen_label}; Preference: {collected.get('time_pref','')}",
                    call_sid=session.get("call_sid", ""),
                )
            except Exception:
                pass

        session = _reset_to_triage(session)
        # Professional confirmation
        if patched:
            return f"Confirmed — you’re rescheduled to {chosen_label}.", session
        return f"Confirmed — I’ve requested the change to {chosen_label} and the clinic will send confirmation shortly.", session

    # -------------------------------------------------------------------------
    # CANCEL FLOW (calendar-backed, needs phone to reliably find appointment)
    # -------------------------------------------------------------------------
    if state == CANCEL_NAME:
        collected["name"] = user_said.strip()
        session["state"] = CANCEL_PHONE
        return "Thanks. What’s the phone number used for the booking?", session

    if state == CANCEL_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return "Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = CANCEL_FIND
        return "Okay — one moment while I look up your appointment.", session

    if state == CANCEL_FIND:
        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            # No calendar -> handoff to sheet
            if send_to_sheet is not None:
                try:
                    send_to_sheet(
                        name=collected.get("name", ""),
                        phone=collected.get("phone", ""),
                        intent="CANCEL",
                        message="Cancel request (calendar not connected).",
                        call_sid=session.get("call_sid", ""),
                    )
                except Exception:
                    pass
            session = _reset_to_triage(session)
            return "Okay — I’ve sent the cancellation request to the clinic and they’ll confirm shortly.", session

        ev = await find_event_for_patient_by_phone(session, collected.get("phone", ""))
        if not ev:
            # If not found, still handoff to sheet
            if send_to_sheet is not None:
                try:
                    send_to_sheet(
                        name=collected.get("name", ""),
                        phone=collected.get("phone", ""),
                        intent="CANCEL",
                        message="Cancel request (no matching event found automatically).",
                        call_sid=session.get("call_sid", ""),
                    )
                except Exception:
                    pass
            session = _reset_to_triage(session)
            return "I couldn’t locate the appointment automatically, but I’ve sent the cancellation request to the clinic.", session

        session["resch_event_id"] = ev.get("id")
        session["resch_event_summary"] = ev.get("summary", "Appointment")
        session["state"] = CANCEL_CONFIRM
        return f"I found your appointment: {session['resch_event_summary']}. Confirm cancellation? Say yes or no.", session

    if state == CANCEL_CONFIRM:
        if _is_no(user_said):
            session = _reset_to_triage(session)
            return "Okay — not cancelled. What would you like to do?", session

        if not _is_yes(user_said):
            return "Please say yes to confirm, or no to keep the booking.", session

        tokens = await redis_get_json(TOKENS_KEY)
        event_id = session.get("resch_event_id")
        if tokens and event_id:
            try:
                delete_event(stored_tokens=tokens, event_id=event_id, calendar_id=clinic.get("calendar_id", "primary"))
            except Exception:
                # fall back to sheet handoff
                if send_to_sheet is not None:
                    try:
                        send_to_sheet(
                            name=collected.get("name", ""),
                            phone=collected.get("phone", ""),
                            intent="CANCEL",
                            message=f"Cancel request (delete failed) for event_id={event_id}",
                            call_sid=session.get("call_sid", ""),
                        )
                    except Exception:
                        pass
                session = _reset_to_triage(session)
                return "I’ve sent this to the clinic to cancel and they’ll confirm shortly.", session

            session = _reset_to_triage(session)
            return "Cancelled — your appointment has been removed.", session

        # No tokens -> sheet
        if send_to_sheet is not None:
            try:
                send_to_sheet(
                    name=collected.get("name", ""),
                    phone=collected.get("phone", ""),
                    intent="CANCEL",
                    message="Cancel request (no calendar tokens).",
                    call_sid=session.get("call_sid", ""),
                )
            except Exception:
                pass
        session = _reset_to_triage(session)
        return "Okay — I’ve sent the cancellation request to the clinic and they’ll confirm shortly.", session

    # -------------------------------------------------------------------------
    # BOOKING FLOW (professional, deterministic)
    # -------------------------------------------------------------------------
    if state == BOOK_PATIENT_TYPE:
        pt = parse_patient_type(user_said)
        if not pt:
            return "Are you a new patient, or have you been here before?", session
        collected["patient_type"] = pt
        session["state"] = BOOK_REASON
        return "What’s the appointment for? For example physio assessment, follow-up, sports massage, or shockwave.", session

    if state == BOOK_REASON:
        collected["reason"] = user_said.strip()
        session["state"] = BOOK_TIME_PREF
        return "When would you prefer? For example tomorrow morning or Tuesday afternoon.", session

    if state == BOOK_TIME_PREF:
        pref_text = user_said.strip()
        collected["time_pref"] = pref_text

        tz = get_tz(clinic)
        dw = parse_specific_day_window(pref_text, tz)
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"] = dw[1].isoformat()
        else:
            collected.pop("day_window_start", None)
            collected.pop("day_window_end", None)

        session["state"] = BOOK_OFFER_SLOTS
        return "Okay — I’m checking availability now.", session

    if state == BOOK_OFFER_SLOTS:
        pref = collected.get("time_pref", "")
        dw = None
        if collected.get("day_window_start") and collected.get("day_window_end"):
            dw = (
                datetime.fromisoformat(collected["day_window_start"]),
                datetime.fromisoformat(collected["day_window_end"]),
            )

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=pref,
            day_window=dw,
        )
        if err:
            # Offer callback capture rather than dead-end
            session = _reset_to_triage(session)
            session["state"] = CALLBACK_NAME
            collected2 = session.setdefault("collected", {})
            collected2["message"] = f"Booking request: reason={collected.get('reason','')} pref={pref}"
            return f"{err} I can take your details and the clinic will call you back. What’s your name?", session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY] = labels
        session["state"] = BOOK_PICK_SLOT
        return f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session

    if state == BOOK_PICK_SLOT:
        opt = _looks_like_option_123(user_said)
        if opt is None:
            return "Please say 1, 2, or 3.", session

        idx = opt - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

        session["state"] = BOOK_NAME
        return "Great. What’s your full name for the booking?", session

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = BOOK_PHONE
        return "Thanks. What’s the best mobile number for the booking confirmation?", session

    if state == BOOK_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return "Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = BOOK_CONFIRM_SLOT
        chosen_label = session.get(SELECTED_SLOT_LABEL_KEY) or "the selected time"
        return f"Confirm booking for {chosen_label}? Say yes or no.", session

    if state == BOOK_CONFIRM_SLOT:
        if _is_no(user_said):
            session = _reset_to_triage(session)
            return "Okay — not booked. What would you like to do?", session

        if not _is_yes(user_said):
            return "Please say yes to confirm, or no to cancel.", session

        chosen = session.get(SELECTED_SLOT_KEY)
        chosen_label = session.get(SELECTED_SLOT_LABEL_KEY) or "the selected time"
        tokens = await redis_get_json(TOKENS_KEY)

        # Create event if possible; otherwise demo-confirm + sheet handoff
        created_ok = False
        if tokens and chosen:
            try:
                start = datetime.fromisoformat(chosen["start"])
                end = datetime.fromisoformat(chosen["end"])
                calendar_id = clinic.get("calendar_id", "primary")
                summary = f"{collected.get('name', 'Patient')} – {collected.get('reason', 'Appointment')}"
                description = (
                    f"Clinic: {clinic.get('display_name', 'Clinic')}\n"
                    f"Patient type: {collected.get('patient_type', '')}\n"
                    f"Phone: {collected.get('phone', '')}\n"
                    f"Reason: {collected.get('reason', '')}\n"
                    f"Preference: {collected.get('time_pref', '')}\n"
                    "Booked via RochSolutions AI receptionist."
                )
                event = create_event(
                    stored_tokens=tokens,
                    start_dt=start,
                    end_dt=end,
                    summary=summary,
                    description=description,
                    calendar_id=calendar_id,
                )
                created_ok = bool(event and event.get("id"))
            except Exception:
                created_ok = False

        # Always also handoff booking details (client-ready safety net)
        if send_to_sheet is not None:
            try:
                send_to_sheet(
                    name=collected.get("name", ""),
                    phone=collected.get("phone", ""),
                    intent="BOOK",
                    message=f"Booked: {chosen_label}; Reason: {collected.get('reason','')}; Patient type: {collected.get('patient_type','')}",
                    call_sid=session.get("call_sid", ""),
                )
            except Exception:
                pass

        session = _reset_to_triage(session)

        if created_ok:
            return f"Confirmed — you’re booked for {chosen_label}. You’ll receive a confirmation shortly.", session
        return f"Confirmed — you’re booked for {chosen_label}. The clinic will send confirmation shortly.", session

    # -------------------------------------------------------------------------
    # TRIAGE (entry point)
    # -------------------------------------------------------------------------
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session = _reset_to_triage(session)
        session["state"] = BOOK_PATIENT_TYPE
        return "Sure — are you a new patient, or have you been here before?", session

    if intent == "RESCHEDULE":
        session = _reset_to_triage(session)
        session["state"] = RESCH_NAME
        return "Sure — to reschedule, what’s your full name?", session

    if intent == "CANCEL":
        session = _reset_to_triage(session)
        session["state"] = CANCEL_NAME
        return "Sure — to cancel, what’s your full name?", session

    if intent.startswith("FAQ_"):
        return faq_answer(intent, user_said, clinic), session

    if intent == "HUMAN":
        session = _reset_to_triage(session)
        session["state"] = CALLBACK_NAME
        return "Okay. What’s your name?", session

    # Helpful fallback that sounds human, without “anything else I can help with”
    return (
        "I can help you book an appointment, reschedule, cancel, or answer questions about prices, opening hours, location, or insurance. "
        "What would you like to do?",
        session,
    )
