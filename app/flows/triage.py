from typing import Dict, Any, Tuple
import re
from datetime import datetime, timedelta
import pytz

from app.storage.redis_store import redis_get_json
from app.tools.calendar_google import create_event

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
    "common_insurers_uk": [
        "Bupa", "AXA Health", "Vitality", "Aviva", "WPA", "Cigna", "Simplyhealth"
    ],
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
    """
    Returns a high-level intent label.
    Keep it deterministic: keyword-based.
    """
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
    if _contains_any(t, ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"]):
        return "FAQ_INSURANCE"

    # Services / what you treat
    if _contains_any(t, ["physio", "physiotherapy", "chiro", "chiropractor", "massage", "sports therapy", "rehab", "pain", "injury"]):
        return "FAQ_SERVICES"

    # Conditions
    if _contains_any(t, ["back pain", "neck", "shoulder", "knee", "ankle", "hip", "sciatica", "sprain", "strain", "tendon", "post op", "surgery"]):
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

# (You defined these, but your current booking flow uses BOOKING_START/BOOK_DATE/BOOK_TIME/BOOK_DURATION.)
# Keep them here for later expansion; we won't break your current flow.
BOOK_START = "BOOK_START"
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_CONFIRM = "BOOK_CONFIRM"

RESCH_START = "RESCH_START"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_CONFIRM = "RESCH_CONFIRM"


# ---------- CALENDAR BOOKING ----------
async def book_calendar_event(collected: Dict[str, Any]) -> Dict[str, Any]:
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return {"ok": False, "error": "Google Calendar is not connected."}

    try:
        start_naive = datetime.strptime(
            f"{collected['date']} {collected['time']}",
            "%Y-%m-%d %H:%M",
        )
        start = TZ.localize(start_naive)
    except Exception:
        return {"ok": False, "error": "Invalid date or time format."}

    duration = int(collected.get("duration_min", DEFAULT_DURATION_MIN))
    end = start + timedelta(minutes=duration)

    event = create_event(
        stored_tokens=tokens,
        start_dt=start,
        end_dt=end,
        summary="RochSolutions Appointment",
        description="Booked via AI receptionist",
        calendar_id="primary",
    )

    return {
        "ok": True,
        "start": start.isoformat(),
        "event_id": event.get("id"),
        "event_link": event.get("htmlLink"),
    }


# ---------- FAQ RESPONSES ----------
def faq_answer(intent: str, user_said: str) -> str:
    t = _norm(user_said)

    if intent == "FAQ_PRICES":
        return (
            f"{DEMO_CLINIC['pricing_summary']} "
            "If you tell me whether it’s an initial assessment or a follow-up, I can give a clearer estimate for your clinic."
        )

    if intent == "FAQ_HOURS":
        # Handle common variants
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
        # Demo-safe answer
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
        # Do NOT claim contracts; give realistic guidance
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

    # ===== BOOKING FLOW (keep your current flow as-is) =====
    if state == "BOOKING_START":
        session["state"] = "BOOK_DATE"
        return "Sure. What date would you like? Please say it like 2025-12-30.", session

    if state == "BOOK_DATE":
        collected["date"] = user_said.strip()
        session["state"] = "BOOK_TIME"
        return "Thanks. What time? Please use 24 hour format, for example 14:30.", session

    if state == "BOOK_TIME":
        collected["time"] = user_said.strip()
        session["state"] = "BOOK_DURATION"
        return "How long should the appointment be? Say 30 or 60 minutes.", session

    if state == "BOOK_DURATION":
        collected["duration_min"] = user_said.strip()
        session["state"] = "BOOK_CONFIRM"
        return (
            f"Just to confirm: {collected['date']} at {collected['time']} "
            f"for {collected['duration_min']} minutes. Say yes or no.",
            session,
        )

    if state == "BOOK_CONFIRM":
        if user_said.lower() not in ("yes", "y", "yeah", "confirm", "ok"):
            session["state"] = TRIAGE
            session["collected"] = {}
            return "No problem. What would you like to do instead?", session

        result = await book_calendar_event(collected)
        session["state"] = TRIAGE
        session["collected"] = {}

        if not result["ok"]:
            return f"I couldn’t book that. {result['error']}", session

        return "Your appointment is booked. You’ll see it in the calendar. See you then.", session

    # ===== TRIAGE / FAQ / OTHER =====
    intent = detect_intent(user_said)
    session["intent"] = intent

    # Booking trigger
    if intent == "BOOK":
        session["state"] = "BOOKING_START"
        return "Sure — I can help you book an appointment.", session

    # Reschedule trigger (demo: deterministic but not implemented yet)
    if intent == "RESCHEDULE":
        return (
            "I can help with rescheduling in the full version. For the demo, I can take your name and number and the clinic will confirm changes. "
            "Please say your full name and phone number.",
            session,
        )

    # FAQ intents
    if intent.startswith("FAQ_"):
        # One-turn FAQ: answer and stay in TRIAGE
        return faq_answer(intent, user_said), session

    # Human handoff
    if intent == "HUMAN":
        return "Okay. Please say your name and phone number and someone will call you back.", session

    # Generic fallback
    return (
        "I can help with booking, rescheduling, prices, insurance, opening hours, location, or general questions. "
        "What would you like to do?",
        session,
    )
