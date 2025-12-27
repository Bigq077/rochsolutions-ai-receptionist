from typing import Dict, Any, Tuple
import re
from datetime import datetime, timedelta
import pytz

from app.storage.redis_store import redis_get_json
from app.tools.calendar_google import create_event

TOKENS_KEY = "google_tokens"
TZ = pytz.timezone("Europe/London")
DEFAULT_DURATION_MIN = 30


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
    if any(k in t for k in ["reschedule", "change", "move", "cancel", "cancellation"]):
        return "RESCHEDULE"
    if any(k in t for k in ["price", "cost", "fee", "how much"]):
        return "PRICES"
    if any(k in t for k in ["hours", "open", "close", "opening", "address", "location"]):
        return "HOURS_LOCATION"
    if any(k in t for k in ["human", "person", "receptionist", "someone"]):
        return "HUMAN"

    return "OTHER"


def _is_yes(text: str) -> bool:
    t = _norm(text)
    return t in {"yes", "y", "yeah", "yep", "ok", "okay", "confirm", "sure"}


def _is_no(text: str) -> bool:
    t = _norm(text)
    return t in {"no", "n", "nope", "cancel", "stop"}


def _valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except Exception:
        return False


def _valid_time(time_str: str) -> bool:
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except Exception:
        return False


async def _book_calendar_event(collected: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Uses stored Google tokens to create an event.
    Returns (ok, message_to_user)
    """
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return False, "Google Calendar is not connected yet. Please connect it first, then try again."

    date_str = collected.get("date")
    time_str = collected.get("time")
    duration_min = collected.get("duration_min", DEFAULT_DURATION_MIN)

    if not date_str or not time_str:
        return False, "I’m missing the date or time."

    try:
        start_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        start = TZ.localize(start_naive)
    except Exception:
        return False, "That date or time format doesn’t look right."

    try:
        duration_min = int(duration_min)
    except Exception:
        duration_min = DEFAULT_DURATION_MIN

    end = start + timedelta(minutes=duration_min)

    patient_type = collected.get("patient_type", "")
    summary = "RochSolutions Appointment"
    if patient_type:
        summary = f"RochSolutions Appointment ({patient_type})"

    description = "Booked via AI receptionist"
    try:
        event = create_event(
            stored_tokens=tokens,
            start_dt=start,
            end_dt=end,
            summary=summary,
            description=description,
            calendar_id="primary",
        )
        # htmlLink is useful for debug, but not needed for the caller
        return True, f"Done. You’re booked for {date_str} at {time_str}."
    except Exception as e:
        return False, f"I couldn’t create the calendar event. {str(e)}"


async def _handle_booking(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Booking flow states:
      BOOKING_START -> BOOK_DATE -> BOOK_TIME -> BOOK_DURATION -> BOOK_CONFIRM
    """
    collected = session.setdefault("collected", {})
    state = session.get("state", "")

    # Step 0: new/returning
    if state == "BOOKING_START":
        t = _norm(user_said)
        if "new" in t:
            collected["patient_type"] = "new"
        elif "return" in t or "existing" in t:
            collected["patient_type"] = "returning"
        else:
            return "Are you a new or returning patient? Please say 'new' or 'returning'.", session

        session["state"] = "BOOK_DATE"
        return "Great. What date would you like? Please use YYYY-MM-DD, like 2025-12-30.", session

    # Step 1: date
    if state == "BOOK_DATE":
        date_str = user_said.strip()
        if not _valid_date(date_str):
            return "Please say the date in this format: YYYY-MM-DD. For example: 2025-12-30.", session

        collected["date"] = date_str
        session["state"] = "BOOK_TIME"
        return "Thanks. What time? Please use 24-hour format like 14:30.", session

    # Step 2: time
    if state == "BOOK_TIME":
        time_str = user_said.strip()
        if not _valid_time(time_str):
            return "Please say the time in 24-hour format like 09:00 or 14:30.", session

        collected["time"] = time_str
        session["state"] = "BOOK_DURATION"
        return "How long should it be in minutes? Say 30 or 60. If you’re not sure, say 30.", session

    # Step 3: duration
    if state == "BOOK_DURATION":
        t = _norm(user_said)
        # simple parsing: accept "30" / "60"
        duration = re.findall(r"\d+", t)
        if not duration:
            return "Please say a number of minutes, like 30 or 60.", session

        collected["duration_min"] = int(duration[0])
        session["state"] = "BOOK_CONFIRM"
        return (
            f"Confirm: book on {collected['date']} at {collected['time']} for {collected['duration_min']} minutes? "
            "Say yes or no.",
            session,
        )

    # Step 4: confirm and book
    if state == "BOOK_CONFIRM":
        if _is_no(user_said):
            session["state"] = "TRIAGE"
            return "Okay, cancelled. What would you like to do instead?", session

        if not _is_yes(user_said):
            return "Please say yes to confirm or no to cancel.", session

        ok, msg = await _book_calendar_event(collected)
        session["state"] = "TRIAGE"
        return msg, session

    # If state doesn't match, fall back
    session["state"] = "TRIAGE"
    return "I can help with booking, rescheduling, prices, or opening hours. What would you like to do?", session


async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Main entry point for each user message.
    IMPORTANT: now async because booking may call Redis + Google Calendar.
    """
    if not user_said:
        return "I didn't catch that. Please tell me what you need help with.", session

    # If we're in the middle of booking, continue booking flow
    if session.get("state", "").startswith("BOOK"):
        return await _handle_booking(user_said, session)

    intent = detect_intent(user_said)
    session["intent"] = intent

    if intent == "BOOK":
        session["state"] = "BOOKING_START"
        return "Sure. I can help you book an appointment. Are you a new or returning patient?", session

    if intent == "RESCHEDULE":
        session["state"] = "RESCHEDULE_START"
        return "No problem. Do you want to reschedule or cancel an existing appointment?", session

    if intent == "PRICES":
        return "Prices depend on the clinic and appointment type. Is it for an initial assessment or a follow up?", session

    if intent == "HOURS_LOCATION":
        return "Do you need opening hours, or the address and parking information?", session

    if intent == "HUMAN":
        return "Okay. I can take a message and have the clinic call you back. Please say your name and number.", session

    return "I can help with booking, rescheduling, prices, or opening hours. What would you like to do?", session
