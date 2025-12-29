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


# ---------- HELPERS ----------
def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if any(k in t for k in ["book", "booking", "appointment", "schedule", "available"]):
        return "BOOK"
    if any(k in t for k in ["reschedule", "change", "move", "cancel"]):
        return "RESCHEDULE"
    if any(k in t for k in ["price", "cost", "fee", "how much"]):
        return "PRICES"
    if any(k in t for k in ["hours", "open", "close", "address", "location"]):
        return "HOURS_LOCATION"
    if any(k in t for k in ["human", "person", "receptionist"]):
        return "HUMAN"

    return "OTHER"

TRIAGE = "TRIAGE"

# Booking states
BOOK_START = "BOOK_START"
BOOK_PATIENT_TYPE = "BOOK_PATIENT_TYPE"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_REASON = "BOOK_REASON"
BOOK_TIME_PREF = "BOOK_TIME_PREF"
BOOK_OFFER_SLOTS = "BOOK_OFFER_SLOTS"
BOOK_CONFIRM = "BOOK_CONFIRM"

# Reschedule states
RESCH_START = "RESCH_START"
RESCH_NAME = "RESCH_NAME"
RESCH_PHONE = "RESCH_PHONE"
RESCH_FIND = "RESCH_FIND"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_CONFIRM = "RESCH_CONFIRM"

# FAQ states
FAQ_PRICES = "FAQ_PRICES"
FAQ_HOURS = "FAQ_HOURS"
FAQ_LOCATION = "FAQ_LOCATION"



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


# ---------- MAIN STATE MACHINE ----------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not user_said:
        return "I didn’t catch that. What would you like to do?", session

    state = session.get("state", "TRIAGE")
    collected = session.setdefault("collected", {})

    # ===== BOOKING FLOW =====
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
            session["state"] = "TRIAGE"
            session["collected"] = {}
            return "No problem. What would you like to do instead?", session

        result = await book_calendar_event(collected)
        session["state"] = "TRIAGE"
        session["collected"] = {}

        if not result["ok"]:
            return f"I couldn’t book that. {result['error']}", session

        return "Your appointment is booked. You’ll see it in the calendar. See you then.", session

    # ===== INTENT DETECTION =====
    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session["state"] = "BOOKING_START"
        return "I can help with that.", session

    if intent == "RESCHEDULE":
        return "Rescheduling is not available yet. Please contact the clinic directly.", session

    if intent == "PRICES":
        return "Prices depend on the appointment type. Is it an initial assessment or follow up?", session

    if intent == "HOURS_LOCATION":
        return "The clinic is open Monday to Friday, 9am to 6pm.", session

    if intent == "HUMAN":
        return "Okay. Please say your name and phone number and someone will call you back.", session

    return "I can help with booking, prices, or opening hours. What would you like to do?", session
