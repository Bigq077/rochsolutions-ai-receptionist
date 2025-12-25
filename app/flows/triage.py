from typing import Dict, Any, Tuple
import re

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

def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not user_said:
        return "I didn't catch that. Please tell me what you need help with.", session

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

