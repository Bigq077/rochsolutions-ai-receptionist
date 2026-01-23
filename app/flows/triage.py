# app/flows/triage.py
from typing import Dict, Any, Tuple
import re
from datetime import datetime, timedelta
import pytz
from app.tools.handoff import send_to_sheet
from app.storage.redis_store import redis_get_json
from app.clinic_config import CLINICS  # config-driven clinics
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

# ✅ NEW: OpenAI router (you must add app/tools/llm_router.py)
from app.tools.llm_router import route_and_answer

# ---------- CONFIG ----------
TOKENS_KEY = "google_tokens"
DEFAULT_DURATION_MIN = 30

ACTIVE_CLINIC_KEY = "active_clinic"
LAST_OFFERED_SLOTS_KEY = "last_offered_slots"
SELECTED_SLOT_KEY = "selected_slot"

# ✅ NEW: store last prompt so the LLM can handle interruptions better
LAST_PROMPT_KEY = "last_prompt"


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
    All clinic data comes from clinic_config.py so you can swap clinics without changing code.
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


def parse_patient_type(text: str) -> str | None:
    """
    Robust handling for 'new' vs 'returning/existing/coming back'.
    """
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

    # Don't guess on vague answers
    if t in ("yes", "yeah", "yep", "ok", "okay", "sure"):
        return None
    return None


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    # Booking
    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    # Cancel explicitly
    if _contains_any(t, ["cancel", "cancellation", "call it off"]):
        return "RESCHEDULE"

    # Reschedule explicitly (avoid generic "change" triggering too often)
    if _contains_any(t, ["reschedule", "move", "rebook", "postpone", "change my appointment", "change my booking"]):
        return "RESCHEDULE"

    # FAQs
    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pricing", "payment", "pay"]):
        return "FAQ_PRICES"

    if _contains_any(t, ["hours", "open", "close", "opening", "when are you open", "weekend", "saturday", "sunday"]):
        return "FAQ_HOURS"

    if _contains_any(t, ["address", "location", "where are you", "parking", "postcode", "directions", "near", "map"]):
        return "FAQ_LOCATION"

    if _contains_any(
        t,
        ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"],
    ):
        return "FAQ_INSURANCE"

    if _contains_any(
        t,
        ["physio", "physiotherapy", "chiro", "chiropractor", "massage", "sports therapy", "rehab", "pain", "injury", "shockwave"],
    ):
        return "FAQ_SERVICES"

    if _contains_any(
        t,
        ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica", "sprain", "strain", "tendon", "post op", "surgery"],
    ):
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
    """
    Demo parser:
    - supports: today, tomorrow, weekdays (tuesday), "next tuesday"
    - returns a day window (00:00 to 23:59 local)
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


def _get_last_prompt(session: Dict[str, Any]) -> str:
    return (session.get(LAST_PROMPT_KEY) or "").strip()


def _set_last_prompt(session: Dict[str, Any], text: str) -> None:
    session[LAST_PROMPT_KEY] = (text or "").strip()


def _reply(session: Dict[str, Any], text: str) -> Tuple[str, Dict[str, Any]]:
    """
    Centralised reply helper so every path stores last prompt for LLM context.
    """
    _set_last_prompt(session, text)
    return text, session


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

# Reschedule / Cancel
RESCH_CHOICE = "RESCH_CHOICE"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_PICK_SLOT = "RESCH_PICK_SLOT"
RESCH_CONFIRM = "RESCH_CONFIRM"
CANCEL_CONFIRM = "CANCEL_CONFIRM"

# Demo reschedule (no calendar lookup)
RESCH_DEMO_ORIGINAL = "RESCH_DEMO_ORIGINAL"
RESCH_DEMO_NEW = "RESCH_DEMO_NEW"
RESCH_DEMO_CONFIRM = "RESCH_DEMO_CONFIRM"


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: int | None = None,
    pref_text: str | None = None,
    day_window: tuple[datetime, datetime] | None = None,
) -> tuple[list[dict], list[str], str | None]:
    """
    Slot suggestion:
    - If Google tokens exist: use real free/busy.
    - If tokens missing (demo): still generate sensible demo slots so it never "hangs".
    """
    clinic = get_clinic(session)
    tz = get_tz(clinic)

    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    w_start, w_end = next_7_days_window()

    # Clamp to requested day if provided
    if day_window:
        w_start, w_end = day_window

    # Time-of-day preference window (morning/afternoon/evening)
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

    # If preference window too strict, fall back to clinic default hours
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

    events = list_upcoming_events(
        tokens,
        days_ahead=30,
        max_results=25,
        calendar_id=clinic.get("calendar_id", "primary"),
    )
    for ev in events:
        desc = ev.get("description") or ""
        if target in _digits_only(desc):
            return ev
    return None


# ---------- FAQ ----------
def faq_answer(intent: str, user_said: str, clinic: Dict[str, Any]) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return clinic.get(
            "pricing_summary",
            "Initial assessment £65 (45 minutes). Follow-up £45 (30 minutes). Sports massage £40 (30 minutes) or £70 (60 minutes). Shockwave £55.",
        )

    if intent == "FAQ_HOURS":
        return clinic.get(
            "hours_summary",
            "We’re open Monday to Friday 8am to 7pm, Saturday 9am to 2pm, and closed Sunday.",
        )

    if intent == "FAQ_LOCATION":
        # Requirement: "Roch Physio is located at ..."
        address = clinic.get("address", "Roch Physio is located at 12 High Street, Coventry, CV1.")
        parking = clinic.get("parking", "")
        if "parking" in t and parking:
            return f"{address} Parking: {parking}"
        return address

    if intent == "FAQ_INSURANCE":
        note = clinic.get(
            "insurance_note",
            "We accept Bupa, AXA Health, Vitality, Aviva and WPA. If you’re with another provider, we can treat you self-pay and provide an invoice for reimbursement if your insurer allows it.",
        )
        return note

    if intent == "FAQ_SERVICES":
        services = clinic.get("services", [])
        if services:
            return "We offer: " + ", ".join(services) + "."
        return "We offer physiotherapy assessment and treatment, follow-ups, sports massage, and rehab plans."

    if intent == "FAQ_CONDITIONS":
        return (
            "We commonly help with back pain, neck or shoulder pain, sports injuries, joint pain, and post-operative rehab. "
            "If you tell me what’s going on, I can help you book."
        )

    if intent == "FAQ_REFERRAL":
        return (
            "A GP referral isn’t usually required for private appointments, but some insurance policies do require one. "
            "If you’re using insurance, it’s worth checking your policy conditions."
        )

    if intent == "FAQ_FIRST_VISIT":
        return (
            f"For a first visit: {clinic.get('what_to_bring', 'Wear comfortable clothing and bring any relevant scans or notes.')}"
            " Arriving 5 to 10 minutes early is ideal."
        )

    if intent == "FAQ_POLICIES":
        return clinic.get(
            "cancellation_policy",
            "If you need to cancel or move your appointment, please give at least 24 hours’ notice.",
        )

    if intent == "FAQ_PRIVACY":
        return (
            "Your information is treated as confidential and handled in line with data protection rules. "
            "For the demo, we store only what’s needed to manage your booking and provide a smooth service."
        )

    return "Sure — can you tell me a bit more about what you need so I can help accurately?"


def _reset_to_triage(session: Dict[str, Any]) -> Dict[str, Any]:
    session["state"] = TRIAGE
    session["collected"] = {}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    session.pop("resch_event_id", None)
    session.pop("resch_event_summary", None)
    session.pop("slot_labels", None)
    session.pop("selected_slot_label", None)
    # keep last_prompt so LLM can still reference it if needed
    return session


def _handle_llm_fallback(
    user_said: str,
    session: Dict[str, Any],
    clinic: Dict[str, Any],
    state: str,
    collected: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Called when our keyword intent detection returns UNKNOWN/OTHER.
    Uses OpenAI to:
      - route intent (BOOK/RESCHEDULE/FAQ/etc.)
      - extract entities
      - provide a concise reply
    """
    try:
        llm = route_and_answer(
            user_text=user_said,
            clinic=clinic,
            current_state=state,
            last_bot_prompt=_get_last_prompt(session),
        )

        # Merge entities into collected
        ents = llm.get("entities") or {}
        if isinstance(ents, dict):
            for k, v in ents.items():
                if isinstance(v, str) and v.strip():
                    collected[k] = v.strip()

        routed_intent = (llm.get("intent") or "OTHER").strip()
        faq_topic = (llm.get("faq_topic") or "other").strip()
        confidence = float(llm.get("confidence") or 0.0)

        # If low confidence, ask its follow-up question if it provided one
        if confidence < 0.55:
            fu = (llm.get("follow_up_question") or "").strip()
            if fu:
                return _reply(session, fu)
            return _reply(session, "Sorry — I didn’t catch that. Could you repeat?")

        # Route to deterministic flows
        if routed_intent == "BOOK":
            session = _reset_to_triage(session)
            session["state"] = BOOK_PATIENT_TYPE
            msg = (llm.get("reply") or "Sure — are you a new patient, or have you been here before?").strip()
            return _reply(session, msg)

        if routed_intent == "RESCHEDULE":
            session = _reset_to_triage(session)
            session["state"] = RESCH_NAME
            session["collected"]["resch_action"] = "RESCHEDULE"
            msg = (llm.get("reply") or "Sure — to reschedule, what’s your full name?").strip()
            return _reply(session, msg)

        if routed_intent == "FAQ":
            topic_map = {
                "prices": "FAQ_PRICES",
                "hours": "FAQ_HOURS",
                "location": "FAQ_LOCATION",
                "insurance": "FAQ_INSURANCE",
                "services": "FAQ_SERVICES",
                "policies": "FAQ_POLICIES",
                "first_visit": "FAQ_FIRST_VISIT",
                "other": "OTHER",
            }
            mapped = topic_map.get(faq_topic, "OTHER")
            if mapped.startswith("FAQ_"):
                msg = faq_answer(mapped, user_said, clinic)
                return _reply(session, msg)

            # fallback to model reply
            msg = (llm.get("reply") or "Sure — what would you like to know?").strip()
            fu = (llm.get("follow_up_question") or "").strip()
            if fu and fu not in msg:
                msg = f"{msg} {fu}"
            return _reply(session, msg)

        if routed_intent == "HUMAN":
            return _reply(session, "Okay. Please tell me your name and phone number and the clinic will call you back.")

        # Default conversational reply
        msg = (llm.get("reply") or "Sure — could you tell me a bit more?").strip()
        fu = (llm.get("follow_up_question") or "").strip()
        if fu and fu not in msg:
            msg = f"{msg} {fu}"
        return _reply(session, msg)

    except Exception as e:
        print("LLM fallback error:", repr(e))
        return _reply(
            session,
            "Sorry — I didn’t catch that. I can help with booking, rescheduling, prices, opening hours, location, insurance, or services.",
        )


# ---------- MAIN STATE MACHINE ----------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    # NOTE: Twilio layer now handles empty speech better, but keep this safe fallback.
    if not user_said:
        return _reply(session, "Sorry — I didn’t catch that. Could you repeat?")

    clinic = get_clinic(session)

    state = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})
    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)

    # --- global demo commands ---
    if _norm(user_said) in ("restart", "start over", "reset"):
        session = _reset_to_triage(session)
        return _reply(session, "Okay — starting over. What would you like to do?")

    # --- global repeat helper ---
    if _norm(user_said) in ("repeat", "say again"):
        # If we're in slot picking, repeat that instruction; otherwise repeat last prompt if available
        if state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
            return _reply(session, "Sure. Please say 1, 2, or 3.")
        lp = _get_last_prompt(session)
        if lp:
            return _reply(session, lp)
        return _reply(session, "Sorry — I didn’t catch that. Could you repeat?")

    # ======================================================================
    # DEMO RESCHEDULE FLOW (requested): name -> original appt -> new appt -> confirm
    # (No existing/returning question. No phone lookup required.)
    # ======================================================================
    if state == RESCH_DEMO_ORIGINAL:
        collected["original_appt"] = user_said.strip()
        session["state"] = RESCH_DEMO_NEW
        return _reply(session, "And what date and time would you like to reschedule to?")

    if state == RESCH_DEMO_NEW:
        collected["new_appt"] = user_said.strip()
        session["state"] = RESCH_DEMO_CONFIRM
        name = collected.get("name", "")
        return _reply(
            session,
            f"Just to confirm: reschedule {name} from {collected.get('original_appt','')} to {collected.get('new_appt','')}. Say yes to confirm or no to cancel.",
        )

    if state == RESCH_DEMO_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return _reply(session, "No problem. What would you like to do instead?")
        session = _reset_to_triage(session)
        return _reply(session, "All set — your appointment has been rescheduled.")

    # ======================================================================
    # RESCHEDULE / CANCEL FLOW (calendar-backed)
    # ======================================================================
    if state == RESCH_CHOICE:
        t = _norm(user_said)
        if any(k in t for k in ["cancel", "cancellation", "call it off"]):
            collected["resch_action"] = "CANCEL"
            session["state"] = RESCH_NAME
            return _reply(session, "Okay — to cancel, what’s your full name?")

        collected["resch_action"] = "RESCHEDULE"
        session["state"] = RESCH_NAME
        return _reply(session, "Okay — to reschedule, what’s your full name?")

    if state == RESCH_NAME:
        collected["name"] = user_said.strip()

        # If this is a demo (no tokens), switch to demo reschedule flow
        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens and collected.get("resch_action", "RESCHEDULE") == "RESCHEDULE":
            session["state"] = RESCH_DEMO_ORIGINAL
            return _reply(session, "Thanks. What was the date and time of your original appointment?")

        session["state"] = RESCH_PHONE
        return _reply(session, "Thanks. What’s the phone number used for the booking?")

    if state == RESCH_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return _reply(session, "Sorry — I didn’t catch a valid phone number. Please say the phone number again.")
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = RESCH_FIND
        return _reply(session, "Okay — one moment while I look up your appointment.")

    if state == RESCH_FIND:
        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            # fallback to demo reschedule (rather than failing)
            session["state"] = RESCH_DEMO_ORIGINAL
            return _reply(session, "For the demo, what was the date and time of your original appointment?")

        ev = await find_event_for_patient(session, collected.get("phone", ""))
        if not ev:
            session = _reset_to_triage(session)
            return _reply(
                session,
                "I couldn’t find a matching appointment in the next 30 days. For the demo, please book first and then try reschedule.",
            )

        session["resch_event_id"] = ev.get("id")
        session["resch_event_summary"] = ev.get("summary", "Appointment")

        if collected.get("resch_action") == "CANCEL":
            session["state"] = CANCEL_CONFIRM
            return _reply(
                session,
                f"I found your appointment: {session['resch_event_summary']}. Do you want to cancel it? Say yes or no.",
            )

        session["state"] = RESCH_OFFER_SLOTS
        return _reply(session, "I found your appointment. Let me check new availability.")

    if state == RESCH_OFFER_SLOTS:
        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text="",
        )
        if err:
            session = _reset_to_triage(session)
            return _reply(session, err)

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["state"] = RESCH_PICK_SLOT
        return _reply(session, f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.")

    if state == RESCH_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return _reply(session, "Please say 1, 2, or 3.")

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return _reply(session, "Please say 1, 2, or 3.")

        session[SELECTED_SLOT_KEY] = slots[idx]
        session["state"] = RESCH_CONFIRM
        return _reply(session, f"Great. Please confirm rescheduling to option {idx + 1}. Say yes or no.")

    if state == RESCH_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return _reply(session, "No problem. What would you like to do instead?")

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = RESCH_DEMO_ORIGINAL
            return _reply(session, "For the demo, what was the date and time of your original appointment?")

        event_id = session.get("resch_event_id")
        chosen = session.get(SELECTED_SLOT_KEY)
        if not event_id or not chosen:
            session = _reset_to_triage(session)
            return _reply(session, "Something went wrong rescheduling. Please try again.")

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
        return _reply(session, "All set — your appointment has been rescheduled.")

    if state == CANCEL_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return _reply(session, "Okay — I won’t cancel it. What would you like to do instead?")

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session = _reset_to_triage(session)
            return _reply(session, "The clinic calendar is currently offline. Please try again shortly.")

        event_id = session.get("resch_event_id")
        if not event_id:
            session = _reset_to_triage(session)
            return _reply(session, "I couldn’t identify the appointment to cancel. Please try again.")

        delete_event(
            stored_tokens=tokens,
            event_id=event_id,
            calendar_id=clinic.get("calendar_id", "primary"),
        )

        session = _reset_to_triage(session)
        return _reply(session, "Done — your appointment has been cancelled.")

    # ======================================================================
    # BOOKING FLOW (type -> reason -> time -> offer -> pick -> name -> phone -> confirm)
    # ======================================================================
    if state == BOOK_PATIENT_TYPE:
        pt = parse_patient_type(user_said)
        if not pt:
            return _reply(session, "No problem — are you a new patient, or have you been here before?")
        collected["patient_type"] = pt
        session["state"] = BOOK_REASON
        return _reply(session, "Thanks. What’s the appointment for — for example assessment, follow-up, massage, or shockwave?")

    if state == BOOK_REASON:
        collected["reason"] = user_said.strip()
        session["state"] = BOOK_TIME_PREF
        return _reply(session, "When would you prefer? You can say tomorrow morning, Tuesday afternoon, or next week.")

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
        return _reply(session, "Great — let me check availability.")

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
            return _reply(session, err)

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session["slot_labels"] = labels
        session["state"] = BOOK_PICK_SLOT
        return _reply(session, f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.")

    if state == BOOK_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return _reply(session, "Please say 1, 2, or 3.")

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get("slot_labels") or []

        if idx < 0 or idx >= len(slots):
            return _reply(session, "Please say 1, 2, or 3.")

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session["selected_slot_label"] = labels[idx]

        session["state"] = BOOK_NAME
        return _reply(session, "Great. What’s your full name for the booking?")

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = BOOK_PHONE
        return _reply(session, "Thanks. Could I take a mobile number for the booking confirmation?")

    if state == BOOK_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return _reply(session, "Sorry — I didn’t catch a valid phone number. Please say the phone number again.")
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = BOOK_CONFIRM_SLOT
        return _reply(session, "Perfect. Please say yes to confirm the booking, or no to cancel.")

    if state == BOOK_CONFIRM_SLOT:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return _reply(session, "No problem. What would you like to do instead?")

        chosen = session.get(SELECTED_SLOT_KEY)
        label = session.get("selected_slot_label") or "the selected time"
        phone = collected.get("phone", "")

        tokens = await redis_get_json(TOKENS_KEY)

        # If tokens exist, try real calendar booking. If not, still confirm (demo).
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

            session = _reset_to_triage(session)
            if not event or not event.get("id"):
                return _reply(session, "I couldn’t create the booking. Please try again.")

            return _reply(session, f"Confirmed — you’re booked for {label}. We’ll send a confirmation text to {phone}.")

        # Demo confirmation (no calendar connected)
        session = _reset_to_triage(session)
        return _reply(session, f"Confirmed — you’re booked for {label}. We’ll send a confirmation text to {phone}.")

    # ======================================================================
    # TRIAGE / FAQ / OTHER
    # ======================================================================
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session = _reset_to_triage(session)
        session["state"] = BOOK_PATIENT_TYPE
        return _reply(session, "Sure — are you a new patient, or have you been here before?")

    if intent == "RESCHEDULE":
        session = _reset_to_triage(session)
        session["state"] = RESCH_NAME
        session["collected"]["resch_action"] = "RESCHEDULE"
        return _reply(session, "Sure — to reschedule, what’s your full name?")

    if intent.startswith("FAQ_"):
        return _reply(session, faq_answer(intent, user_said, clinic))

        if intent == "HUMAN":
        send_to_sheet(
            name=collected.get("name", ""),
            phone=collected.get("phone", ""),
            intent="CALLBACK",
            message=user_said,
            call_sid=session.get("call_sid", ""),
        )
        return (
            "No problem. I’ve passed this to the clinic and someone will call you back shortly.",
            session,
        )


    # ✅ NEW: If we don’t recognise it, let OpenAI route + answer safely
    if intent in ("UNKNOWN", "OTHER"):
        return _handle_llm_fallback(user_said, session, clinic, state, collected)

    # Fallback (should rarely hit)
    return _reply(
        session,
        "I can help with booking, rescheduling or cancelling, prices, insurance, opening hours, location, or general questions. What would you like to do?",
    )
