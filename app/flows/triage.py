from typing import Dict, Any, Tuple
import re
from datetime import datetime
import pytz

from app.storage.redis_store import redis_get_json
from app.tools.calendar_google import create_event, freebusy
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
TZ = pytz.timezone("Europe/London")
DEFAULT_DURATION_MIN = 30

# Demo-safe defaults (don’t claim specific contracts/policies beyond “varies by clinic”)
DEMO_CLINIC = {
    "name": "RochSolutions Clinic (Demo)",
    "service_area": "UK",
    "hours_summary": "Monday to Friday 9am to 6pm. Some clinics also offer early mornings or evenings.",
    "pricing_summary": "Pricing varies by clinic and appointment type. Typical ranges: initial assessment £50–£90, follow-up £40–£75.",
    "cancellation_policy": "If you need to cancel or change, please give at least 24 hours’ notice to avoid a late cancellation fee (policy varies by clinic).",
    "what_to_bring": "A photo ID, any relevant medical notes or imaging reports, and comfortable clothing.",
    "accessibility": "Most clinics can accommodate accessibility needs. Tell us what you need and we’ll confirm.",
    "insurance_note": (
        "Many clinics can provide receipts for you to claim back from your insurer. "
        "Coverage depends on your policy and whether your plan requires a referral."
    ),
    "common_insurers_uk": ["Bupa", "AXA Health", "Vitality", "Aviva", "WPA", "Cigna", "Simplyhealth"],
    "payment_methods": "Most clinics accept card payments. Some also accept bank transfer. We’ll confirm for your clinic.",
}


# ---------- HELPERS ----------
def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _contains_any(t: str, keywords: list[str]) -> bool:
    return any(k in t for k in keywords)


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
    evening: 17-20 (if you don't offer evenings, we will still try and then fall back)
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

# Booking (Step 4 full flow)
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_PICK_SLOT = "BOOK_PICK_SLOT"
BOOK_CONFIRM_SLOT = "BOOK_CONFIRM_SLOT"

# Reschedule (demo stub)
RESCH_START = "RESCH_START"

# Session keys
LAST_OFFERED_SLOTS_KEY = "last_offered_slots"
SELECTED_SLOT_KEY = "selected_slot"


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(duration_min: int = 30, pref_text: str | None = None) -> tuple[list[dict], list[str], str | None]:
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return [], [], "Google Calendar is not connected."

    w_start, w_end = next_7_days_window()

    # Preference filtering by hours (demo)
    win = preference_window(pref_text or "")
    if win:
        day_start_h, day_end_h = win
    else:
        day_start_h, day_end_h = 9, 18

    candidates = generate_candidate_slots(
        w_start,
        w_end,
        duration_min=duration_min,
        day_start_h=day_start_h,
        day_end_h=day_end_h,
    )

    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id="primary")
    busy_blocks = parse_busy(busy)

    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    # If preference window is too strict, fall back to normal clinic hours for demo
    if not top3 and win:
        candidates2 = generate_candidate_slots(
            w_start, w_end, duration_min=duration_min, day_start_h=9, day_end_h=18
        )
        free_slots2 = filter_free_slots(candidates2, busy_blocks)
        top3 = pick_first_n(free_slots2, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days."

    raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw_slots, labels, None


# ---------- FAQ ----------
def faq_answer(intent: str, user_said: str) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return (
            f"{DEMO_CLINIC['pricing_summary']} "
            "If you tell me whether it’s an initial assessment or a follow-up, I can give a clearer estimate for your clinic."
        )

    if intent == "FAQ_HOURS":
        if "weekend" in t or "saturday" in t or "sunday" in t:
            return (
                "Weekend availability depends on the clinic. "
                "Many are Monday to Friday 9am–6pm, and some offer Saturday mornings. "
                "Tell me your preferred day and I’ll check availability."
            )
        if "evening" in t or "late" in t or "after work" in t or "7" in t or "8" in t:
            return (
                "Some clinics offer early morning or evening appointments, but it varies by location. "
                "Tell me what time you’re aiming for and I’ll try to find the closest match."
            )
        return DEMO_CLINIC["hours_summary"]

    if intent == "FAQ_LOCATION":
        if "parking" in t:
            return (
                "Parking depends on the clinic site. Many have nearby paid parking or short-stay street parking. "
                "If you tell me the area or postcode, I can give more specific directions."
            )
        if "wheelchair" in t or "accessible" in t:
            return (
                f"{DEMO_CLINIC['accessibility']} "
                "If you share your needs (step-free access, lift, etc.), we’ll confirm before your visit."
            )
        return (
            "For the demo, the exact address depends on the clinic you’re booking with. "
            "If you tell me your city or postcode, I can route you to the closest clinic and share directions."
        )

    if intent == "FAQ_INSURANCE":
        insurers = ", ".join(DEMO_CLINIC["common_insurers_uk"])
        return (
            f"{DEMO_CLINIC['insurance_note']} "
            f"Common insurers people use in the UK include {insurers}. "
            "If you tell me your insurer name and whether you have a membership or policy number, I can note it for the clinic and advise what they typically need for a claim."
        )

    if intent == "FAQ_SERVICES":
        return (
            "Most MSK clinics typically help with assessment and treatment plans for pain and injuries—"
            "for example physiotherapy-style rehab, exercise plans, mobility work, and advice for return to sport or work. "
            "If you tell me what’s going on, I can book you with the most appropriate appointment type."
        )

    if intent == "FAQ_CONDITIONS":
        return (
            "Yes — clinics commonly see issues like back pain, neck/shoulder pain, sports injuries, joint pain, and post-operative rehab. "
            "If you tell me where it hurts, how long it’s been going on, and whether it started after an injury, I can book the right type of appointment."
        )

    if intent == "FAQ_REFERRAL":
        return (
            "A GP referral isn’t always required for private appointments, but some insurance policies do require one. "
            "If you’re using insurance, it’s worth checking your policy conditions. If you’re self-paying, you can usually book directly."
        )

    if intent == "FAQ_FIRST_VISIT":
        return (
            f"For a first visit: {DEMO_CLINIC['what_to_bring']} "
            "If you’ve had scans or reports (MRI, X-ray), bring those too. Arriving 5–10 minutes early is ideal."
        )

    if intent == "FAQ_POLICIES":
        return DEMO_CLINIC["cancellation_policy"]

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
        return "Okay — starting over. What would you like to do?", session

    # ===== BOOKING FLOW (Step 4: collect details) =====
    if state == BOOK_PATIENT_TYPE:
        collected["patient_type"] = user_said.strip()
        session["state"] = BOOK_NAME
        return "Thanks. What's your full name?", session

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = BOOK_PHONE
        return "And what's the best phone number for the appointment?", session

    if state == BOOK_PHONE:
        collected["phone"] = user_said.strip()
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

    # ===== Offer slots =====
    if state == BOOK_OFFER_SLOTS:
        pref = collected.get("time_pref", "")
        raw_slots, labels, err = await suggest_top_slots(duration_min=DEFAULT_DURATION_MIN, pref_text=pref)
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

    # ===== Pick slot =====
    if state == BOOK_PICK_SLOT:
        choice = _norm(user_said)
        if choice in ("repeat", "again", "say again"):
            return "Sure. Please say 1, 2, or 3.", session

        m = re.search(r"\b(1|2|3)\b", choice)
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session[SELECTED_SLOT_KEY] = slots[idx]
        session["state"] = BOOK_CONFIRM_SLOT
        return f"Great. Please confirm booking for option {idx + 1}. Say yes or no.", session

    # ===== Confirm + create calendar event =====
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
            return "Google Calendar is not connected. Please connect it first.", session

        chosen = session.get(SELECTED_SLOT_KEY)
        if not chosen:
            session["state"] = TRIAGE
            return "Something went wrong selecting the slot. Please try again.", session

        start = datetime.fromisoformat(chosen["start"])
        end = datetime.fromisoformat(chosen["end"])

        summary = f"{collected.get('name', 'Patient')} – {collected.get('reason', 'Appointment')}"
        description = (
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
            calendar_id="primary",
        )

        session["state"] = TRIAGE
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None

        if not event or not event.get("id"):
            return "I couldn’t create the booking. Please try again.", session

        return "You’re booked. See you then.", session

    # ===== TRIAGE / FAQ / OTHER =====
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session["collected"] = {}
        session[LAST_OFFERED_SLOTS_KEY] = None
        session[SELECTED_SLOT_KEY] = None
        session["state"] = BOOK_PATIENT_TYPE
        return "Sure — are you a new or returning patient?", session

    if intent == "RESCHEDULE":
        # Step 5 will implement true reschedule. Keep demo-safe.
        session["state"] = TRIAGE
        return (
            "I can help with rescheduling in the full version. For the demo, please contact the clinic directly to change an appointment.",
            session,
        )

    if intent.startswith("FAQ_"):
        return faq_answer(intent, user_said), session

    if intent == "HUMAN":
        return "Okay. Please tell me your name and phone number and the clinic will call you back.", session

    return (
        "I can help with booking, prices, insurance, opening hours, location, or general questions. "
        "What would you like to do?",
        session,
    )
