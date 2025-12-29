from typing import Dict, Any, Tuple
import re
from datetime import datetime, timedelta
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

# Demo “clinic-ish” defaults (safe placeholders)
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

    # Booking / schedule
    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    # Reschedule / cancel
    if _contains_any(t, ["reschedule", "change", "move", "cancel", "cancellation", "rebook", "postpone"]):
        return "RESCHEDULE"

    # Prices / billing
    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pricing", "payment", "pay"]):
        return "FAQ_PRICES"

    # Hours / location
    if _contains_any(t, ["hours", "open", "close", "opening", "when are you open", "weekend", "saturday", "sunday"]):
        return "FAQ_HOURS"

    if _contains_any(t, ["address", "location", "where are you", "parking", "postcode", "directions", "near", "map"]):
        return "FAQ_LOCATION"

    # Insurance
    if _contains_any(
        t, ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"]
    ):
        return "FAQ_INSURANCE"

    # Services / what you treat
    if _contains_any(t, ["physio", "physiotherapy", "chiro", "chiropractor", "massage", "sports therapy", "rehab", "pain", "injury"]):
        return "FAQ_SERVICES"

    # Conditions
    if _contains_any(
        t, ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica", "sprain", "strain", "tendon", "post op", "surgery"]
    ):
        return "FAQ_CONDITIONS"

    # Referrals / GP / NHS
    if _contains_any(t, ["referral", "gp", "doctor", "nhs", "letter", "prescription"]):
        return "FAQ_REFERRAL"

    # First visit logistics
    if _contains_any(t, ["what should i bring", "bring", "what do i wear", "clothes", "arrive", "late", "parking"]):
        return "FAQ_FIRST_VISIT"

    # Policies
    if _contains_any(t, ["cancel policy", "cancellation policy", "late fee", "refund", "missed appointment"]):
        return "FAQ_POLICIES"

    # Data/privacy
    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"

    # Human handoff
    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to"]):
        return "HUMAN"

    return "OTHER"


# ---------- STATES ----------
TRIAGE = "TRIAGE"

# Booking (slot-based) states
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_PICK_SLOT = "BOOK_PICK_SLOT"
BOOK_CONFIRM_SLOT = "BOOK_CONFIRM_SLOT"

# (Keep these for later expansion if you want richer booking data collection)
BOOK_START = "BOOK_START"
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"

# Reschedule states (demo stub for now)
RESCH_START = "RESCH_START"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_CONFIRM = "RESCH_CONFIRM"


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(duration_min: int = 30) -> tuple[list[dict], list[str], str | None]:
    """
    Returns:
      - raw_slots: [{"start": iso, "end": iso}, ...]
      - labels: ["Tue 02 Jan at 10:00", ...] (spoken options)
      - error: optional string
    """
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return [], [], "Google Calendar is not connected."

    w_start, w_end = next_7_days_window()

    candidates = generate_candidate_slots(
        w_start,
        w_end,
        duration_min=duration_min,
        day_start_h=9,
        day_end_h=18,
    )

    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id="primary")
    busy_blocks = parse_busy(busy)

    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days."

    raw_slots = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw_slots, labels, None


# ---------- FAQ RESPONSES ----------
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
    session.setdefault("collected", {})  # keep for future; not required for demo booking
    session.setdefault("last_offered_slots", None)
    session.setdefault("selected_slot", None)

    # ===== BOOKING FLOW (slot offering) =====
    if state == BOOK_OFFER_SLOTS:
        raw_slots, labels, err = await suggest_top_slots(duration_min=30)
        if err:
            session["state"] = TRIAGE
            return err, session

        session["last_offered_slots"] = raw_slots
        session["state"] = BOOK_PICK_SLOT

        # Speak 3 options
        return (
            f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. "
            "Say 1, 2, or 3.",
            session,
        )

    if state == BOOK_PICK_SLOT:
        choice = _norm(user_said)
        m = re.search(r"\b(1|2|3)\b", choice)
        if not m:
            return "Please say 1, 2, or 3.", session

        idx = int(m.group(1)) - 1
        slots = session.get("last_offered_slots") or []
        if idx < 0 or idx >= len(slots):
            return "Please say 1, 2, or 3.", session

        session["selected_slot"] = slots[idx]
        session["state"] = BOOK_CONFIRM_SLOT
        return f"Great. Please confirm booking for option {idx + 1}. Say yes or no.", session

    if state == BOOK_CONFIRM_SLOT:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok"):
            session["state"] = TRIAGE
            session["last_offered_slots"] = None
            session["selected_slot"] = None
            return "No problem. What would you like to do instead?", session

        tokens = await redis_get_json(TOKENS_KEY)
        if not tokens:
            session["state"] = TRIAGE
            return "Google Calendar is not connected. Please connect it first.", session

        chosen = session.get("selected_slot")
        if not chosen:
            session["state"] = TRIAGE
            return "Something went wrong selecting the slot. Please try again.", session

        # ISO → datetime (already includes tz offset)
        start = datetime.fromisoformat(chosen["start"])
        end = datetime.fromisoformat(chosen["end"])

        event = create_event(
            stored_tokens=tokens,
            start_dt=start,
            end_dt=end,
            summary="RochSolutions Appointment (Demo)",
            description="Booked via AI receptionist demo",
            calendar_id="primary",
        )

        session["state"] = TRIAGE
        session["last_offered_slots"] = None
        session["selected_slot"] = None

        if not event or not event.get("id"):
            return "I couldn’t create the booking. Please try again.", session

        return "You’re booked. See you then.", session

    # ===== TRIAGE / FAQ / OTHER =====
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session["state"] = BOOK_OFFER_SLOTS
        return "Sure — let me check availability.", session

    if intent == "RESCHEDULE":
        return (
            "I can help with rescheduling in the full version. For the demo, I can take your name and number and the clinic will confirm changes. "
            "Please say your full name and phone number.",
            session,
        )

    if intent.startswith("FAQ_"):
        return faq_answer(intent, user_said), session

    if intent == "HUMAN":
        return "Okay. Please say your name and phone number and someone will call you back.", session

    return (
        "I can help with booking, rescheduling, prices, insurance, opening hours, location, or general questions. "
        "What would you like to do?",
        session,
    )
