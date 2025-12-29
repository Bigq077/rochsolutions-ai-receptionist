from typing import Dict, Any, Tuple
import re
from datetime import datetime
import pytz

from app.storage.redis_store import redis_get_json
from app.clinic_config import CLINICS  # ✅ Step 6: config-driven clinics
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
    """
    Step 6: All clinic data comes from clinic_config.py so you can swap clinics without changing code.
    """
    key = session.get(ACTIVE_CLINIC_KEY, "demo")
    return CLINICS.get(key, CLINICS["demo"])


def get_tz(clinic: Dict[str, Any]):
    return pytz.timezone(clinic.get("timezone", "Europe/London"))


def clinic_default_hours(clinic: Dict[str, Any]) -> tuple[int, int]:
    """
    Default working hours used for slot generation.
    For demo simplicity: use Monday hours if present, else 9-18.
    """
    wh = clinic.get("working_hours", {})
    mon = wh.get("mon")
    if isinstance(mon, (list, tuple)) and len(mon) == 2:
        return int(mon[0]), int(mon[1])
    return 9, 18


def normalize_phone(phone: str) -> str:
    """
    Basic demo-safe phone normalization (digits only).
    """
    return _digits_only(phone)


def is_valid_phone(phone: str) -> bool:
    """
    Demo validation: 10-15 digits.
    """
    p = normalize_phone(phone)
    return 10 <= len(p) <= 15


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    if _contains_any(t, ["reschedule", "change", "move", "cancel", "cancellation", "rebook", "postpone"]):
        return "RESCHEDULE"

    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pricing", "payment", "pay"]):
        return "FAQ_PRICES"

    if _contains_any(t, ["hours", "open", "close", "opening", "when are you open", "weekend", "saturday", "sunday"]):
        return "FAQ_HOURS"

    if _contains_any(t, ["address", "location", "where are you", "parking", "postcode", "directions", "near", "map"]):
        return "FAQ_LOCATION"

    if _contains_any(t, ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"]):
        return "FAQ_INSURANCE"

    if _contains_any(t, ["physio", "physiotherapy", "chiro", "chiropractor", "massage", "sports therapy", "rehab", "pain", "injury"]):
        return "FAQ_SERVICES"

    if _contains_any(t, ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica", "sprain", "strain", "tendon", "post op", "surgery"]):
        return "FAQ_CONDITIONS"

    if _contains_any(t, ["referral", "gp", "doctor", "nhs", "letter", "prescription"]):
        return "FAQ_REFERRAL"

    if _contains_any(t, ["what should i bring", "bring", "what do i wear", "clothes", "arrive", "late", "parking"]):
        return "FAQ_FIRST_VISIT"

    if _contains_any(t, ["cancel policy", "cancellation policy", "late fee", "refund", "missed appointment"]):
        return "FAQ_POLICIES"

    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"

    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to"]):
        return "HUMAN"

    return "OTHER"


def preference_window(pref: str) -> tuple[int, int] | None:
    """
    Simple time-of-day filter for demo.
    morning: 9-12
    afternoon: 12-17
    evening: 17-20
    """
    p = _norm(pref)
    if "morning" in p:
        return (9, 12)
    if "afternoon" in p:
        return (12, 17)
    if "evening" in p or "after work" in p or "afterwork" in p:
        return (17, 20)
    return None


# ---------- STATES ----------
TRIAGE = "TRIAGE"

# Booking (Step 4)
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_PICK_SLOT = "BOOK_PICK_SLOT"
BOOK_CONFIRM_SLOT = "BOOK_CONFIRM_SLOT"

# Reschedule / Cancel (Step 5)
RESCH_CHOICE = "RESCH_CHOICE"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_PICK_SLOT = "RESCH_PICK_SLOT"
RESCH_CONFIRM = "RESCH_CONFIRM"
CANCEL_CONFIRM = "CANCEL_CONFIRM"


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: int | None = None,
    pref_text: str | None = None
) -> tuple[list[dict], list[str], str | None]:
    clinic = get_clinic(session)
    tz = get_tz(clinic)

    # slot length: clinic config wins
    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return [], [], "The clinic calendar is currently offline. Please try again shortly."

    w_start, w_end = next_7_days_window()  # should already be tz-aware or UTC; your pipeline handles it

    # Start with preference window
    win = preference_window(pref_text or "")
    if win:
        day_start_h, day_end_h = win
    else:
        day_start_h, day_end_h = clinic_default_hours(clinic)

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

    # If preference window too strict, fall back to clinic default hours
    if not top3 and win:
        day_start_h2, day_end_h2 = clinic_default_hours(clinic)
        candidates2 = generate_candidate_slots(w_start, w_end, duration_min=duration_min, day_start_h=day_start_h2, day_end_h=day_end_h2)
        free_slots2 = filter_free_slots(candidates2, busy_blocks)
        top3 = pick_first_n(free_slots2, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days. Would you like me to take your details for a call-back?"

    raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw_slots, labels, None


# ---------- RESCHEDULE HELPERS ----------
async def find_event_for_patient(session: Dict[str, Any], phone: str) -> Dict[str, Any] | None:
    """
    Demo approach:
    - Look at upcoming events (next 30 days)
    - Match if phone digits appear in event description
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


# ---------- FAQ ----------
def faq_answer(intent: str, user_said: str, clinic: Dict[str, Any]) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return (
            f"{clinic.get('pricing_summary', 'Pricing varies by clinic and appointment type.')}"
            " If you tell me whether it’s an initial assessment or a follow-up, I can give a clearer estimate."
        )

    if intent == "FAQ_HOURS":
        if "weekend" in t or "saturday" in t or "sunday" in t:
            return (
                "Weekend availability depends on the clinic. "
                "Many are Monday to Friday 9am–6pm, and some offer Saturday mornings. "
                "Tell me your preferred day and I’ll check availability."
            )
        if "evening" in t or "late" in t or "after work" in t:
            return (
                "Some clinics offer early morning or evening appointments, but it varies by location. "
                "Tell me what time you’re aiming for and I’ll try to find the closest match."
            )
        return clinic.get("hours_summary", "Opening hours vary by clinic. Many are Mon–Fri 9am–6pm.")

    if intent == "FAQ_LOCATION":
        address = clinic.get("address", "Address depends on the clinic location.")
        parking = clinic.get("parking", "")
        if "parking" in t and parking:
            return f"{address}. Parking: {parking}"
        if "wheelchair" in t or "accessible" in t:
            return (
                "Most clinics can accommodate accessibility needs. Tell me what you need and we’ll confirm."
            )
        return address

    if intent == "FAQ_INSURANCE":
        insurers = ", ".join(clinic.get("common_insurers", [])) or "Bupa, AXA Health, Vitality, Aviva"
        note = clinic.get("insurance_note", "Coverage depends on your policy.")
        return (
            f"{note} Common insurers people use in the UK include {insurers}. "
            "If you tell me your insurer name and whether you have a membership or policy number, I can note it for the clinic."
        )

    if intent == "FAQ_SERVICES":
        services = clinic.get("services", [])
        if services:
            return "This clinic typically offers: " + ", ".join(services) + ". What would you like help with?"
        return (
            "Most MSK clinics help with assessment and treatment plans for pain and injuries. "
            "If you tell me what’s going on, I can book the right appointment type."
        )

    if intent == "FAQ_CONDITIONS":
        return (
            "Clinics commonly see issues like back pain, neck/shoulder pain, sports injuries, joint pain, and post-operative rehab. "
            "If you tell me where it hurts and how long it’s been going on, I can help you book."
        )

    if intent == "FAQ_REFERRAL":
        return (
            "A GP referral isn’t always required for private appointments, but some insurance policies do require one. "
            "If you’re using insurance, it’s worth checking your policy conditions."
        )

    if intent == "FAQ_FIRST_VISIT":
        return (
            f"For a first visit: {clinic.get('what_to_bring', 'Bring photo ID and any relevant notes/scans.')}"
            " Arriving 5–10 minutes early is ideal."
        )

    if intent == "FAQ_POLICIES":
        return clinic.get("cancellation_policy", "Cancellation policy varies by clinic.")

    if intent == "FAQ_PRIVACY":
        return (
            "Your information is treated as confidential and handled in line with data protection rules. "
            "For the demo, we store only what’s needed to manage your booking and provide a smooth service."
        )

    return "Sure — can you tell me a bit more about what you need so I can help accurately?"


# ---------- MAIN STATE MACHINE ----------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not user_said:
        return "I didn’t catch that. What would you like to do?", session

    clinic = get_clinic(session)

    state = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})
    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)

    # --- global demo commands ---
    if _norm(user_said) in ("restart", "start over", "reset"):
        session["state"] = TRIAGE
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None
        session.pop("resch_event_id", None)
        session.pop("resch_event_summary", None)
        return "Okay — starting over. What would you like to do?", session

    # --- global repeat helper ---
    if _norm(user_said) in ("repeat", "say again") and state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
        return "Sure. Please say 1, 2, or 3.", session

    # ======================================================================
    # STEP 5 — RESCHEDULE / CANCEL FLOW
    # ======================================================================
    if state == RESCH_CHOICE:
        t = _norm(user_said)
        if "cancel" in t:
            collected["resch_action"] = "CANCEL"
            session["state"] = RESCH_NAME
            return "Okay — to cancel, I just need to confirm who you are. What's your full name?", session
        collected["resch_action"] = "RESCHEDULE"
        session["state"] = RESCH_NAME
        return "Okay — to reschedule, I just need to confirm who you are. What's your full name?", session

    if state == RESCH_NAME:
        collected["name"] = user_said.strip()
        session["state"] = RESCH_PHONE
        return "Thanks. What's the phone number used for the booking?", session

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
            session["state"] = TRIAGE
            return "The clinic calendar is currently offline. Please try again shortly.", session

        ev = await find_event_for_patient(session, collected.get("phone", ""))
        if not ev:
            session["state"] = TRIAGE
            return (
                "I couldn’t find a matching appointment in the next 30 days. "
                "For the demo, please book first and then try reschedule.",
                session,
            )

        session["resch_event_id"] = ev.get("id")
        session["resch_event_summary"] = ev.get("summary", "Appointment")

        if collected.get("resch_action") == "CANCEL":
            session["state"] = CANCEL_CONFIRM
            return f"I found your appointment: {session['resch_event_summary']}. Do you want to cancel it? Say yes or no.", session

        session["state"] = RESCH_OFFER_SLOTS
        return "I found your appointment. Let me check new availability.", session

    if state == RESCH_OFFER_SLOTS:
        raw_slots, labels, err = await suggest_top_slots(session, duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)), pref_text="")
        if err:
            session["state"] = TRIAGE
            return err, session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["state"] = RESCH_PICK_SLOT
        return f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session

    if state == RESCH_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        session["state"] = RESCH_CONFIRM
        return f"Great. Please confirm rescheduling to option {idx + 1}. Say yes or no.", session

    if state == RESCH_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok"):
            session["state"] = TRIAGE
            session["collected"] = {}
            session[LAST_OFFERED_SLOTS_KEY] = None
            session[SELECTED_SLOT_KEY] = None
            session.pop("resch_event_id", None)
            session.pop("resch_event_summary", None)
            return "No problem. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = TRIAGE
            return "The clinic calendar is currently offline. Please try again shortly.", session

        event_id = session.get("resch_event_id")
        chosen = session.get(SELECTED_SLOT_KEY)
        if not event_id or not chosen:
            session["state"] = TRIAGE
            return "Something went wrong rescheduling. Please try again.", session

        start = datetime.fromisoformat(chosen["start"])
        end = datetime.fromisoformat(chosen["end"])

        patch_event_time(
            stored_tokens=tokens,
            event_id=event_id,
            start_dt=start,
            end_dt=end,
            calendar_id=clinic.get("calendar_id", "primary"),
        )

        session["state"] = TRIAGE
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None
        session.pop("resch_event_id", None)
        session.pop("resch_event_summary", None)

        return "All set — your appointment has been rescheduled.", session

    if state == CANCEL_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok"):
            session["state"] = TRIAGE
            session["collected"] = {}
            session.pop("resch_event_id", None)
            session.pop("resch_event_summary", None)
            return "Okay — I won’t cancel it. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = TRIAGE
            return "The clinic calendar is currently offline. Please try again shortly.", session

        event_id = session.get("resch_event_id")
        if not event_id:
            session["state"] = TRIAGE
            return "I couldn’t identify the appointment to cancel. Please try again.", session

        delete_event(
            stored_tokens=tokens,
            event_id=event_id,
            calendar_id=clinic.get("calendar_id", "primary"),
        )

        session["state"] = TRIAGE
        session["collected"] = {}
        session.pop("resch_event_id", None)
        session.pop("resch_event_summary", None)

        return "Done — your appointment has been cancelled.", session

    # ======================================================================
    # STEP 4 — BOOKING FLOW
    # ======================================================================
    if state == BOOK_PATIENT_TYPE:
        collected["patient_type"] = user_said.strip()
        session["state"] = BOOK_NAME
        return "Thanks. What's your full name?", session

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = BOOK_PHONE
        return "And what's the best phone number for the appointment?", session

    if state == BOOK_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return "Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = BOOK_REASON
        return "What is the appointment for? For example back pain, knee injury, or a follow-up.", session

    if state == BOOK_REASON:
        collected["reason"] = user_said.strip()
        session["state"] = BOOK_TIME_PREF
        return "When would you prefer? You can say tomorrow morning, Friday afternoon, or next week.", session

    if state == BOOK_TIME_PREF:
        collected["time_pref"] = user_said.strip()
        session["state"] = BOOK_OFFER_SLOTS
        return "Great — let me check availability.", session

    if state == BOOK_OFFER_SLOTS:
        pref = collected.get("time_pref", "")
        raw_slots, labels, err = await suggest_top_slots(session, duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)), pref_text=pref)
        if err:
            session["state"] = TRIAGE
            return err, session

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["state"] = BOOK_PICK_SLOT

        return (
            f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. "
            "Say 1, 2, or 3.",
            session,
        )

    if state == BOOK_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        session["state"] = BOOK_CONFIRM_SLOT
        return f"Great. Please confirm booking for option {idx + 1}. Say yes or no.", session

    if state == BOOK_CONFIRM_SLOT:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok"):
            session["state"] = TRIAGE
            session["collected"] = {}
            session[LAST_OFFERED_SLOTS_KEY] = None
            session[SELECTED_SLOT_KEY] = None
            return "No problem. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = TRIAGE
            return "The clinic calendar is currently offline. Please try again shortly.", session

        chosen = session.get(SELECTED_SLOT_KEY)
        if not chosen:
            session["state"] = TRIAGE
            return "Something went wrong selecting the slot. Please try again.", session

        start = datetime.fromisoformat(chosen["start"])
        end = datetime.fromisoformat(chosen["end"])

        # Step 6: clinic-specific calendar + richer metadata
        calendar_id = clinic.get("calendar_id", "primary")
        summary = f"{collected.get('name', 'Patient')} – {collected.get('reason', 'Appointment')}"
        description = (
            f"Clinic: {clinic.get('display_name', clinic.get('display_name', 'Clinic'))}\n"
            f"Patient type: {collected.get('patient_type', '')}\n"
            f"Phone: {collected.get('phone', '')}\n"
            f"Reason: {collected.get('reason', '')}\n"
            f"Preference: {collected.get('time_pref', '')}\n"
            "Booked via RochSolutions AI receptionist (demo)."
        )

        event = create_event(
            stored_tokens=tokens,
            start_dt=start,
            end_dt=end,
            summary=summary,
            description=description,
            calendar_id=calendar_id,
        )

        session["state"] = TRIAGE
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None

        if not event or not event.get("id"):
            return "I couldn’t create the booking. Please try again.", session

        return "You’re booked. See you then.", session

    # ======================================================================
    # TRIAGE / FAQ / OTHER
    # ======================================================================
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None
        session.pop("resch_event_id", None)
        session.pop("resch_event_summary", None)
        session["state"] = BOOK_PATIENT_TYPE
        return "Sure — are you a new or returning patient?", session

    if intent == "RESCHEDULE":
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None
        session.pop("resch_event_id", None)
        session.pop("resch_event_summary", None)
        session["state"] = RESCH_CHOICE
        return "Sure — do you want to reschedule or cancel your appointment?", session

    if intent.startswith("FAQ_"):
        return faq_answer(intent, user_said, clinic), session

    if intent == "HUMAN":
        return "Okay. Please tell me your name and phone number and the clinic will call you back.", session

    return (
        "I can help with booking, rescheduling or cancelling, prices, insurance, opening hours, location, or general questions. "
        "What would you like to do?",
        session,
    )
