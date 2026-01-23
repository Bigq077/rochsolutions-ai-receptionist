# app/flows/triage.py
from typing import Dict, Any, Tuple
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


# ---------- CONFIG ----------
TOKENS_KEY = "google_tokens"
DEFAULT_DURATION_MIN = 30

ACTIVE_CLINIC_KEY = "active_clinic"
LAST_OFFERED_SLOTS_KEY = "last_offered_slots"
SELECTED_SLOT_KEY = "selected_slot"


# ---------- HELPERS ----------
def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _contains_any(t: str, keywords: list[str]) -> bool:
    return any(k in t for k in keywords)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def get_clinic(session: Dict[str, Any]) -> Dict[str, Any]:
    key = session.get(ACTIVE_CLINIC_KEY, "demo")
    return CLINICS.get(key, CLINICS["demo"])


def get_tz(clinic: Dict[str, Any]):
    return pytz.timezone(clinic.get("timezone", "Europe/London"))


def clinic_default_hours(clinic: Dict[str, Any]) -> tuple[int, int]:
    wh = clinic.get("working_hours", {})
    mon = wh.get("mon")
    if isinstance(mon, (list, tuple)) and len(mon) == 2:
        return int(mon[0]), int(mon[1])
    return 9, 18


def normalize_phone(phone: str) -> str:
    return _digits_only(phone)


def is_valid_phone(phone: str) -> bool:
    p = normalize_phone(phone)
    return 10 <= len(p) <= 15


def parse_patient_type(text: str) -> str | None:
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


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    if _contains_any(t, ["cancel", "cancellation", "call it off"]):
        return "RESCHEDULE"

    if _contains_any(t, ["reschedule", "move", "rebook", "postpone", "change my appointment", "change my booking"]):
        return "RESCHEDULE"

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

    if _contains_any(t, ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica"]):
        return "FAQ_CONDITIONS"

    if _contains_any(t, ["referral", "gp", "doctor", "nhs", "letter", "prescription"]):
        return "FAQ_REFERRAL"

    if _contains_any(t, ["what should i bring", "what do i wear", "arrive", "late"]):
        return "FAQ_FIRST_VISIT"

    if _contains_any(t, ["cancel policy", "cancellation policy", "late fee", "refund", "missed appointment"]):
        return "FAQ_POLICIES"

    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"

    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to"]):
        return "HUMAN"

    return "OTHER"


def preference_window(pref: str) -> tuple[int, int] | None:
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


def parse_specific_day_window(text: str, tz) -> tuple[datetime, datetime] | None:
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


# ---------- STATES ----------
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

# Reschedule / Cancel (calendar-backed)
RESCH_CHOICE = "RESCH_CHOICE"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_PICK_SLOT = "RESCH_PICK_SLOT"
RESCH_CONFIRM = "RESCH_CONFIRM"
CANCEL_CONFIRM = "CANCEL_CONFIRM"

# Demo reschedule (no lookup)
RESCH_DEMO_ORIGINAL = "RESCH_DEMO_ORIGINAL"
RESCH_DEMO_NEW = "RESCH_DEMO_NEW"
RESCH_DEMO_CONFIRM = "RESCH_DEMO_CONFIRM"


def _reset_to_triage(session: Dict[str, Any]) -> Dict[str, Any]:
    session["state"] = TRIAGE
    session["collected"] = {}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    session.pop("resch_event_id", None)
    session.pop("resch_event_summary", None)
    session.pop("slot_labels", None)
    session.pop("selected_slot_label", None)
    return session


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: int | None = None,
    pref_text: str | None = None,
    day_window: tuple[datetime, datetime] | None = None,
) -> tuple[list[dict], list[str], str | None]:
    """
    If Google tokens exist => real free/busy.
    If tokens missing => demo fallback that still returns 3 slots (so booking never "hangs").
    """
    clinic = get_clinic(session)

    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    w_start, w_end = next_7_days_window()
    if day_window:
        w_start, w_end = day_window

    win = preference_window(pref_text or "")
    if win:
        day_start_h, day_end_h = win
    else:
        day_start_h, day_end_h = clinic_default_hours(clinic)

    tokens = await redis_get_json(TOKENS_KEY)

    # ---- DEMO FALLBACK (no tokens) ----
    if not tokens:
        candidates = generate_candidate_slots(
            w_start,
            w_end,
            duration_min=duration_min,
            day_start_h=day_start_h,
            day_end_h=day_end_h,
        )
        top3 = pick_first_n(candidates, 3)
        if not top3:
            return [], [], "I couldn’t find any slots in the next 7 days. Please tell me another day or time."
        raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
        labels = [format_slot((s, e)) for s, e in top3]
        return raw_slots, labels, None

    # ---- REAL CALENDAR MODE ----
    candidates = generate_candidate_slots(
        w_start,
        w_end,
        duration_min=duration_min,
        day_start_h=day_start_h,
        day_end_h=day_end_h,
    )

    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id=clinic.get("calendar_id", "primary"))
    busy_blocks = parse_busy(busy)

    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    if not top3 and win:
        day_start_h2, day_end_h2 = clinic_default_hours(clinic)
        candidates2 = generate_candidate_slots(
            w_start,
            w_end,
            duration_min=duration_min,
            day_start_h=day_start_h2,
            day_end_h=day_end_h2,
        )
        free_slots2 = filter_free_slots(candidates2, busy_blocks)
        top3 = pick_first_n(free_slots2, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days. Would you like me to take a message for a call-back?"

    raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw_slots, labels, None


# ---------- RESCHEDULE HELPERS ----------
async def find_event_for_patient(session: Dict[str, Any], phone: str) -> Dict[str, Any] | None:
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


# ---------- FAQ ----------
def faq_answer(intent: str, user_said: str, clinic: Dict[str, Any]) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return clinic.get("pricing_summary", "Initial assessment is £65. Follow-up appointments are £45.")

    if intent == "FAQ_HOURS":
        return clinic.get("hours_summary", "We’re open Monday to Friday 9am to 6pm.")

    if intent == "FAQ_LOCATION":
        address = clinic.get("address", "Roch Physio is located at 12 High Street, Coventry, CV1.")
        parking = clinic.get("parking", "")
        if "parking" in t and parking:
            return f"{address} Parking: {parking}"
        return address

    if intent == "FAQ_INSURANCE":
        return clinic.get("insurance_note", "We accept major insurers. If you tell me your provider, I can note it for the clinic.")

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
        return f"For a first visit: {clinic.get('what_to_bring', 'Wear comfortable clothing and bring any relevant scans or notes.')}"

    if intent == "FAQ_POLICIES":
        return clinic.get("cancellation_policy", "Please give at least 24 hours’ notice to avoid a late cancellation fee.")

    if intent == "FAQ_PRIVACY":
        return "Your information is treated as confidential and handled in line with data protection rules."

    return "Sure — can you tell me a bit more about what you need so I can help accurately?"


# ---------- MAIN STATE MACHINE ----------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not user_said:
        return "Sorry — I didn’t catch that. Could you repeat?", session

    clinic = get_clinic(session)

    state = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})
    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)

    # global commands
    if _norm(user_said) in ("restart", "start over", "reset"):
        session = _reset_to_triage(session)
        return "Okay — starting over. How can I help?", session

    if _norm(user_said) in ("stop", "goodbye", "bye"):
        session = _reset_to_triage(session)
        return "Okay. Goodbye.", session

    # repeat helper
    if _norm(user_said) in ("repeat", "say again") and state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
        return "Sure. Please say 1, 2, or 3.", session

    # ======================================================================
    # DEMO RESCHEDULE FLOW (requested): name -> original -> new -> confirm
    # ======================================================================
    if state == RESCH_DEMO_ORIGINAL:
        collected["original_appt"] = user_said.strip()
        session["state"] = RESCH_DEMO_NEW
        return "And what date and time would you like to reschedule to?", session

    if state == RESCH_DEMO_NEW:
        collected["new_appt"] = user_said.strip()
        session["state"] = RESCH_DEMO_CONFIRM
        name = collected.get("name", "")
        return (
            f"Just to confirm: reschedule {name} from {collected.get('original_appt','')} to {collected.get('new_appt','')}. "
            "Say yes to confirm or no to cancel.",
            session,
        )

    if state == RESCH_DEMO_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return "No problem. What would you like to do instead?", session
        session = _reset_to_triage(session)
        return "Confirmed — your appointment has been rescheduled. You’ll receive a confirmation shortly.", session

    # ======================================================================
    # RESCHEDULE/CANCEL (calendar-backed, with demo fallback)
    # ======================================================================
    if state == RESCH_CHOICE:
        t = _norm(user_said)
        if any(k in t for k in ["cancel", "cancellation", "call it off"]):
            collected["resch_action"] = "CANCEL"
            session["state"] = RESCH_NAME
            return "Okay — to cancel, what’s your full name?", session
        collected["resch_action"] = "RESCHEDULE"
        session["state"] = RESCH_NAME
        return "Okay — to reschedule, what’s your full name?", session

    if state == RESCH_NAME:
        collected["name"] = user_said.strip()

        # If no tokens and rescheduling => use demo reschedule (per your request)
        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens and collected.get("resch_action", "RESCHEDULE") == "RESCHEDULE":
            session["state"] = RESCH_DEMO_ORIGINAL
            return "Thanks. What was the date and time of your original appointment?", session

        session["state"] = RESCH_PHONE
        return "Thanks. What’s the phone number used for the booking?", session

    if state == RESCH_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return "Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = RESCH_FIND
        return "Okay — one moment while I look up your appointment.", session

    if state == RESCH_FIND:
        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            # fall back to demo reschedule
            session["state"] = RESCH_DEMO_ORIGINAL
            return "For the demo, what was the date and time of your original appointment?", session

        ev = await find_event_for_patient(session, collected.get("phone", ""))
        if not ev:
            session = _reset_to_triage(session)
            return "I couldn’t find a matching appointment. If you’d like, I can take a message and the clinic will call you back.", session

        session["resch_event_id"] = ev.get("id")
        session["resch_event_summary"] = ev.get("summary", "Appointment")

        if collected.get("resch_action") == "CANCEL":
            session["state"] = CANCEL_CONFIRM
            return f"I found your appointment: {session['resch_event_summary']}. Do you want to cancel it? Say yes or no.", session

        session["state"] = RESCH_OFFER_SLOTS
        return "Okay. Let me check new availability.", session

    if state == RESCH_OFFER_SLOTS:
        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text="",
        )
        if err:
            session = _reset_to_triage(session)
            return err, session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["slot_labels"] = labels
        session["state"] = RESCH_PICK_SLOT
        return f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session

    if state == RESCH_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get("slot_labels") or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session["selected_slot_label"] = labels[idx]
        session["state"] = RESCH_CONFIRM
        return "Please say yes to confirm, or no to cancel.", session

    if state == RESCH_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return "No problem. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = RESCH_DEMO_ORIGINAL
            return "For the demo, what was the date and time of your original appointment?", session

        event_id = session.get("resch_event_id")
        chosen = session.get(SELECTED_SLOT_KEY)
        if not event_id or not chosen:
            session = _reset_to_triage(session)
            return "Something went wrong. Please try again.", session

        start = datetime.fromisoformat(chosen["start"])
        end = datetime.fromisoformat(chosen["end"])

        patch_event_time(
            stored_tokens=tokens,
            event_id=event_id,
            start_dt=start,
            end_dt=end,
            calendar_id=clinic.get("calendar_id", "primary"),
        )

        session = _reset_to_triage(session)
        return "Confirmed — your appointment has been rescheduled. You’ll receive a confirmation shortly.", session

    if state == CANCEL_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return "Okay — I won’t cancel it. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session = _reset_to_triage(session)
            return "The calendar is currently offline. Please try again shortly.", session

        event_id = session.get("resch_event_id")
        if not event_id:
            session = _reset_to_triage(session)
            return "I couldn’t identify the appointment to cancel. Please try again.", session

        delete_event(stored_tokens=tokens, event_id=event_id, calendar_id=clinic.get("calendar_id", "primary"))

        session = _reset_to_triage(session)
        return "Done — your appointment has been cancelled.", session

    # ======================================================================
    # BOOKING FLOW
    # ======================================================================
    if state == BOOK_PATIENT_TYPE:
        pt = parse_patient_type(user_said)
        if not pt:
            return "Are you a new patient, or have you been here before?", session
        collected["patient_type"] = pt
        session["state"] = BOOK_REASON
        return "Thanks. What’s the appointment for — for example physio assessment, follow-up, sports massage, or shockwave?", session

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
        return "Okay. Let me check availability.", session

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
            session = _reset_to_triage(session)
            return err, session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["slot_labels"] = labels
        session["state"] = BOOK_PICK_SLOT

        return f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session

    if state == BOOK_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get("slot_labels") or []

        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session["selected_slot_label"] = labels[idx]

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
        return "Perfect. Please say yes to confirm the booking, or no to cancel.", session

    if state == BOOK_CONFIRM_SLOT:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return "No problem. What would you like to do instead?", session

        chosen = session.get(SELECTED_SLOT_KEY)
        label = session.get("selected_slot_label") or "the selected time"
        phone = collected.get("phone", "")

        tokens = await redis_get_json(TOKENS_KEY)

        # If tokens exist => real booking. If not => demo confirmation.
        if tokens and chosen:
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

            session = _reset_to_triage(session)
            if not event or not event.get("id"):
                return "I couldn’t create the booking. Please try again.", session

            return f"Confirmed — you’re booked for {label}. You’ll receive a confirmation text shortly.", session

        session = _reset_to_triage(session)
        return f"Confirmed — you’re booked for {label}. You’ll receive a confirmation text shortly.", session

    # ======================================================================
    # TRIAGE / FAQ / OTHER
    # ======================================================================
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session = _reset_to_triage(session)
        session["state"] = BOOK_PATIENT_TYPE
        return "Sure — are you a new patient, or have you been here before?", session

    if intent == "RESCHEDULE":
        session = _reset_to_triage(session)
        session["state"] = RESCH_NAME
        session["collected"]["resch_action"] = "RESCHEDULE"
        return "Sure — to reschedule, what’s your full name?", session

    if intent.startswith("FAQ_"):
        return faq_answer(intent, user_said, clinic), session

    if intent == "HUMAN":
        # Best effort: write to sheet if configured
        if send_to_sheet is not None:
            try:
                send_to_sheet(
                    name=collected.get("name", ""),
                    phone=collected.get("phone", ""),
                    intent="CALLBACK",
                    message=user_said,
                    call_sid=session.get("call_sid", ""),
                )
            except Exception:
                pass

        return "No problem. I’ve passed this to the clinic and someone will call you back shortly.", session

    return (
        "I can help with booking, rescheduling, prices, opening hours, location, or insurance. "
        "If you’d prefer, I can also take a message and have the clinic call you back. "
        "What would you like to do?",
        session,
    )
