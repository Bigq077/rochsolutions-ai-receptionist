# app/flows/triage.py
from __future__ import annotations

from typing import Dict, Any, Tuple, Optional
import re
from datetime import datetime, timedelta
import pytz

from app.storage.redis_store import redis_get_json
from app.clinic_config import CLINICS

from app.tools.llm_router import route_and_answer
from app.tools.knowledge import retrieve_knowledge

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

# Optional: Google Sheet handoff (won't crash if you haven't set it up yet)
try:
    from app.tools.handoff import send_to_sheet  # type: ignore
except Exception:
    send_to_sheet = None  # type: ignore

import random
import re
from typing import Dict, Any, Tuple

# -----------------------------
# Friendly tone engine
# -----------------------------

FRIENDLY_ACK = [
    "No problem.",
    "Of course.",
    "Sure thing.",
    "Absolutely.",
    "Got it.",
    "That’s fine.",
]

FRIENDLY_CHECKING = [
    "Let me check that for you.",
    "One moment while I check.",
    "Just a second — I’ll check now.",
    "Okay — let me take a quick look.",
]

FRIENDLY_REASSURE = [
    "No worries.",
    "That’s totally fine.",
    "We can sort that out.",
]

def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _maybe_prefix(text: str) -> str:
    """
    Adds light friendliness sometimes, not always,
    so it doesn't sound robotic.
    """
    if not text:
        return text

    lower = text.lower()

    # Don't mess with apologies or confirmations
    if lower.startswith((
        "sorry",
        "perfect",
        "thanks",
        "confirmed",
        "all done",
        "you’re",
        "you're",
    )):
        return text

    if random.random() < 0.4:
        return f"{random.choice(FRIENDLY_ACK)} {text}"

    return text

def _maybe_suffix(text: str) -> str:
    if random.random() < 0.3:
        return f"{text} {random.choice(FRIENDLY_REASSURE)}"
    return text

def _friendly(text: str) -> str:
    text = _clean(text)
    text = _maybe_prefix(text)
    text = _maybe_suffix(text)
    return _clean(text)

# -----------------------------
# DROP-IN REPLACEMENT FOR _say
# -----------------------------

def _say(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Centralised response function.
    Every reply passes through friendly tone logic.
    """
    friendly_text = _friendly(text)
    session["last_bot_prompt"] = friendly_text
    return friendly_text, session


# ---------- CONFIG ----------
TOKENS_KEY = "google_tokens"
DEFAULT_DURATION_MIN = 30

ACTIVE_CLINIC_KEY = "active_clinic"
LAST_OFFERED_SLOTS_KEY = "last_offered_slots"
SELECTED_SLOT_KEY = "selected_slot"

SLOT_LABELS_KEY = "slot_labels"
SELECTED_SLOT_LABEL_KEY = "selected_slot_label"
LAST_BOT_PROMPT_KEY = "last_bot_prompt"
LAST_USER_TEXT_KEY = "last_user_text"


# ---------- HELPERS ----------
def _norm(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t

def looks_like_name(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2 or len(t) > 60:
        return False
    # Has letters, not mostly numbers
    letters = re.findall(r"[A-Za-z]", t)
    digits = re.findall(r"\d", t)
    if len(letters) < 2:
        return False
    if len(digits) > 3:
        return False
    # Avoid treating intent words as a name
    if _norm(t) in ("booking", "book", "reschedule", "cancel", "appointment"):
        return False
    return True


def is_yes(text: str) -> bool:
    return _norm(text) in ("yes", "y", "yeah", "yep", "confirm", "ok", "okay", "sure")


def _contains_any(t: str, keywords: list[str]) -> bool:
    return any(k in t for k in keywords)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def get_clinic(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Robust clinic getter:
    - If session specifies a key, use it if present
    - Else use 'demo' if exists
    - Else fallback to the first clinic in CLINICS
    """
    key = session.get(ACTIVE_CLINIC_KEY)
    if key and key in CLINICS:
        return CLINICS[key]
    if "demo" in CLINICS:
        return CLINICS["demo"]
    return next(iter(CLINICS.values()))


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


def parse_patient_type(text: str) -> Optional[str]:
    t = _norm(text)
    returning = [
        "returning", "existing", "come back", "coming back", "been before",
        "follow up", "follow-up", "followup", "already a patient",
        "i've been", "i have been", "return patient",
    ]
    new = [
        "new", "first time", "never been", "not been before", "initial", "first visit",
        "new patient",
    ]
    if any(k in t for k in returning):
        return "RETURNING"
    if any(k in t for k in new):
        return "NEW"
    return None


def _is_interrupt(text: str) -> bool:
    """
    Allows caller to cut the receptionist off.
    Keep this strict to avoid accidental triggers.
    """
    t = _norm(text)
    if not t:
        return False
    return t in {
        "stop",
        "cancel",
        "wait",
        "hold on",
        "hang on",
        "one second",
        "a second",
        "pause",
        "restart",
        "start over",
        "reset",
        "go back",
        "back",
        "main menu",
        "menu",
    }


def _interrupt_reply(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Friendly + professional interruption handling.
    """
    t = _norm(text)
    if t in {"restart", "start over", "reset"}:
        session = _reset_to_triage(session)
        return _say("Okay — starting over. What would you like to do?", session)
    if t in {"main menu", "menu", "go back", "back"}:
        session = _reset_to_triage(session)
        return _say("No problem — what would you like to do: book, reschedule, or ask a question?", session)
    if t in {"stop", "cancel"}:
        session = _reset_to_triage(session)
        return _say("No problem. What would you like to do instead?", session)
    # generic pause / wait / hold on
    return _say("Of course. When you’re ready, tell me what you’d like to do.", session)


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if _contains_any(t, ["book", "booking", "appointment", "schedule", "available", "slot", "availability"]):
        return "BOOK"

    if _contains_any(t, ["reschedule", "move", "rebook", "postpone", "change my appointment", "change my booking"]):
        return "RESCHEDULE"

    if _contains_any(t, ["cancel appointment", "cancel my appointment", "cancel", "cancellation", "call it off"]):
        return "CANCEL"

    if _contains_any(t, ["price", "cost", "fee", "how much", "charge", "rates", "pricing", "payment", "pay"]):
        return "FAQ_PRICES"

    if _contains_any(t, ["hours", "open", "close", "opening", "when are you open", "weekend", "saturday", "sunday"]):
        return "FAQ_HOURS"

    if _contains_any(t, ["address", "location", "where are you", "parking", "postcode", "directions", "near", "map"]):
        return "FAQ_LOCATION"

    if _contains_any(t, ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"]):
        return "FAQ_INSURANCE"

    if _contains_any(t, ["treatments", "treatment", "services", "physio", "physiotherapy", "massage", "sports therapy", "rehab", "shockwave"]):
        return "FAQ_SERVICES"

    if _contains_any(t, ["cancel policy", "cancellation policy", "late fee", "refund", "missed appointment"]):
        return "FAQ_POLICIES"

    if _contains_any(t, ["first visit", "what should i bring", "what do i wear", "arrive", "late"]):
        return "FAQ_FIRST_VISIT"

    if _contains_any(t, ["privacy", "data", "gdpr", "recording", "confidential"]):
        return "FAQ_PRIVACY"

    if _contains_any(t, ["human", "person", "receptionist", "someone", "call me back", "speak to", "call back"]):
        return "HUMAN"

    return "OTHER"


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
    Supports: today, tomorrow, weekday names, "next tuesday"
    Returns: full-day window in local tz
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


def _say(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    session[LAST_BOT_PROMPT_KEY] = text
    return text, session


def _reset_to_triage(session: Dict[str, Any]) -> Dict[str, Any]:
    session["state"] = "TRIAGE"
    session["intent"] = None
    session["collected"] = {}
    session[LAST_OFFERED_SLOTS_KEY] = None
    session[SELECTED_SLOT_KEY] = None
    session.pop("resch_event_id", None)
    session.pop("resch_event_summary", None)
    session.pop(SLOT_LABELS_KEY, None)
    session.pop(SELECTED_SLOT_LABEL_KEY, None)
    return session


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
BOOK_CONFIRM = "BOOK_CONFIRM"

# Reschedule
RESCH_NAME = "RESCH_NAME"
RESCH_ORIGINAL = "RESCH_ORIGINAL"
RESCH_NEW_PREF = "RESCH_NEW_PREF"
RESCH_OFFER_SLOTS = "RESCH_OFFER_SLOTS"
RESCH_PICK_SLOT = "RESCH_PICK_SLOT"
RESCH_CONFIRM = "RESCH_CONFIRM"
RESCH_PHONE_FALLBACK = "RESCH_PHONE_FALLBACK"


# ---------- SLOT SUGGESTION ----------
async def suggest_top_slots(
    session: Dict[str, Any],
    duration_min: Optional[int] = None,
    pref_text: str = "",
    day_window: Optional[tuple[datetime, datetime]] = None,
) -> tuple[list[dict], list[str], Optional[str]]:
    """
    If tokens exist => real free/busy.
    If tokens missing => demo fallback that still returns 3 slots (so it never hangs).
    """
    clinic = get_clinic(session)

    slot_minutes = int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN))
    duration_min = int(duration_min or slot_minutes)

    w_start, w_end = next_7_days_window()
    if day_window:
        w_start, w_end = day_window

    win = preference_window(pref_text)
    if win:
        day_start_h, day_end_h = win
    else:
        day_start_h, day_end_h = clinic_default_hours(clinic)

    tokens = await redis_get_json(TOKENS_KEY)

    candidates = generate_candidate_slots(
        w_start, w_end,
        duration_min=duration_min,
        day_start_h=day_start_h,
        day_end_h=day_end_h,
    )

    # Demo fallback if calendar not connected
    if not tokens:
        top3 = pick_first_n(candidates, 3)
        if not top3:
            return [], [], "I couldn’t find any slots in the next 7 days. Please tell me another day or time."
        raw = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
        labels = [format_slot((s, e)) for s, e in top3]
        return raw, labels, None

    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id=clinic.get("calendar_id", "primary"))
    busy_blocks = parse_busy(busy)
    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    if not top3 and win:
        day_start_h2, day_end_h2 = clinic_default_hours(clinic)
        candidates2 = generate_candidate_slots(
            w_start, w_end,
            duration_min=duration_min,
            day_start_h=day_start_h2,
            day_end_h=day_end_h2,
        )
        free_slots2 = filter_free_slots(candidates2, busy_blocks)
        top3 = pick_first_n(free_slots2, 3)

    if not top3:
        return [], [], "I couldn’t find any free slots in the next 7 days. Would you like me to take a message for a call-back?"

    raw = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw, labels, None


# ---------- RESCHEDULE HELPERS ----------
def _safe_parse_user_datetime(text: str, tz) -> Optional[datetime]:
    """
    Parses user-provided datetime like:
    '25 Jan 3pm', 'next Tuesday at 10', etc.
    Requires python-dateutil in requirements.txt.
    """
    try:
        from dateutil import parser as dtparser  # type: ignore
        dt = dtparser.parse(text, fuzzy=True)
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        else:
            dt = dt.astimezone(tz)
        return dt
    except Exception:
        return None


async def find_event_by_name_and_time(
    session: Dict[str, Any],
    name: str,
    when_text: str,
) -> Optional[Dict[str, Any]]:
    """
    Tries to find an event by matching:
    - patient name in summary (case-insensitive)
    - and start time close to parsed datetime (within 3 hours)
    """
    clinic = get_clinic(session)
    tz = get_tz(clinic)
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return None

    target_dt = _safe_parse_user_datetime(when_text, tz)
    if not target_dt:
        return None

    events = list_upcoming_events(
        stored_tokens=tokens,
        calendar_id=clinic.get("calendar_id", "primary"),
        days_ahead=60,
        max_results=50,
    )

    name_n = _norm(name)
    best: Optional[Dict[str, Any]] = None
    best_diff = 10**9

    for ev in events:
        summary = _norm(ev.get("summary") or "")
        if name_n and name_n not in summary:
            continue

        start = (ev.get("start") or {}).get("dateTime")
        if not start:
            continue

        try:
            s = start.replace("Z", "+00:00")
            ev_start = datetime.fromisoformat(s)
            if ev_start.tzinfo is None:
                ev_start = tz.localize(ev_start)
            else:
                ev_start = ev_start.astimezone(tz)

            diff = abs((ev_start - target_dt).total_seconds())
            if diff <= 3 * 3600 and diff < best_diff:
                best = ev
                best_diff = diff
        except Exception:
            continue

    return best


# ---------- FAQ (deterministic fallback) ----------
def faq_answer(intent: str, clinic: Dict[str, Any]) -> str:
    if intent == "FAQ_PRICES":
        return clinic.get("pricing_summary", "Please ask the clinic for pricing.")
    if intent == "FAQ_HOURS":
        return clinic.get("hours_summary", "Please ask the clinic for opening hours.")
    if intent == "FAQ_LOCATION":
        return clinic.get("address", "Roch Physio is located at ...")
    if intent == "FAQ_INSURANCE":
        return clinic.get("insurance_note", "Please ask the clinic about insurance.")
    if intent == "FAQ_SERVICES":
        services = clinic.get("services", [])
        return "We offer: " + ", ".join(services) + "." if services else "We offer physiotherapy services."
    if intent == "FAQ_POLICIES":
        return clinic.get("cancellation_policy", "Please give at least 24 hours’ notice to cancel or reschedule.")
    if intent == "FAQ_FIRST_VISIT":
        return clinic.get("what_to_bring", "Please wear comfortable clothing and bring any relevant notes or scans.")
    if intent == "FAQ_PRIVACY":
        return "Your information is treated as confidential and handled in line with UK data protection rules."
    return "How can I help?"


# ---------- MAIN STATE MACHINE ----------
async def triage_turn(user_said: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    # Allow user to cut the receptionist off at any time
    if _is_interrupt(user_said):
        return _interrupt_reply(user_said, session)

    if not user_said:
        return _say("Sorry — I didn’t catch that. Could you repeat?", session)

    clinic = get_clinic(session)
    tz = get_tz(clinic)

    state = session.get("state", TRIAGE)
    collected = session.setdefault("collected", {})
    session.setdefault(LAST_OFFERED_SLOTS_KEY, None)
    session.setdefault(SELECTED_SLOT_KEY, None)

    session[LAST_USER_TEXT_KEY] = user_said

    # Repeat helper
    if _norm(user_said) in ("repeat", "say again") and state in (BOOK_PICK_SLOT, RESCH_PICK_SLOT):
        return _say("Sure. Please say 1, 2, or 3.", session)

    # ==========================================================
    # TRIAGE: LLM + knowledge retrieval (professional responses)
    # ==========================================================
    if state == TRIAGE:
        # 1) Knowledge retrieval first (for "tell me more about treatments", etc.)
        try:
            kb = retrieve_knowledge(user_said, clinic=clinic)  # should return string (best passage)
        except Exception:
            kb = ""

        # 2) LLM router to decide intent and produce concise answer
        try:
            llm = route_and_answer(
                user_text=(
                    (f"KNOWLEDGE:\n{kb}\n\n" if kb else "") + user_said
                ),
                clinic=clinic,
                current_state=state,
                last_bot_prompt=session.get(LAST_BOT_PROMPT_KEY, ""),
            )
            llm_intent = (llm.get("intent") or "").strip()
            conf = float(llm.get("confidence") or 0.0)
            reply = (llm.get("reply") or "").strip()
            follow = (llm.get("follow_up_question") or "").strip()

            # If confident, route/answer
            if conf >= 0.55:
                if llm_intent == "BOOK":
                    session = _reset_to_triage(session)
                    session["state"] = BOOK_PATIENT_TYPE
                    return _say("Sure — are you a new patient, or have you been here before?", session)

                if llm_intent == "RESCHEDULE":
                    session = _reset_to_triage(session)
                    session["state"] = RESCH_NAME
                    return _say("Sure — to reschedule, what’s your full name?", session)

                if llm_intent == "HUMAN":
                    # Optional sheet handoff if set up
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
                    return _say(
                        "No problem — please say your name, number, and what you need help with, and the clinic will call you back.",
                        session,
                    )

                # FAQ / OTHER / MESSAGE => answer directly
                if llm_intent in ("FAQ", "OTHER", "MESSAGE"):
                    if reply:
                        if follow:
                            return _say(f"{reply} {follow}", session)
                        return _say(reply, session)

        except Exception:
            # If LLM fails, continue to deterministic fallback below
            pass

        # Deterministic fallback if LLM didn't return something useful
        intent = detect_intent(user_said)
        if intent.startswith("FAQ_"):
            return _say(faq_answer(intent, clinic), session)

       # Professional generic prompt (NO “anything else I can help with”)
        return _say(
            "I can help with booking, rescheduling, opening hours, location, prices, insurance, or general questions. What would you like to do?",
            session,
        )

    # ======================================================================
    # RESCHEDULE FLOW: name -> original appt -> desired new time -> offer -> pick -> confirm
    # ======================================================================
    if state == RESCH_NAME:
        # If they just say “reschedule” again, re-ask
        intent_check = detect_intent(user_said)
        if intent_check in ("RESCHEDULE", "BOOK", "CANCEL"):
            return _say("Sure — what’s your full name?", session)

        collected["name"] = user_said.strip()
        session["state"] = RESCH_ORIGINAL
        return _say(
            "Thanks. What was the date and time of your original appointment?",
            session,
        )

    if state == RESCH_ORIGINAL:
        collected["original_appt"] = user_said.strip()

        # Try calendar match by name + time
        ev = await find_event_by_name_and_time(
            session,
            collected.get("name", ""),
            collected.get("original_appt", ""),
        )
        if ev:
            session["resch_event_id"] = ev.get("id")
            session["resch_event_summary"] = ev.get("summary", "Appointment")
            session["state"] = RESCH_NEW_PREF
            return _say(
                "Thanks. What day or time would you like to move it to?",
                session,
            )

    # If we have tokens but couldn't match, ask phone as fallback
    tokens = await redis_get_json(TOKENS_KEY)
    if tokens:
        session["state"] = RESCH_PHONE_FALLBACK
        return _say(
            "Thanks. To find it quickly, what phone number was used for the booking?",
            session,
        )

    # No tokens => demo reschedule
    session["state"] = RESCH_NEW_PREF
    return _say(
        "Thanks. What day or time would you like to move it to?",
        session,
    )


if state == RESCH_PHONE_FALLBACK:
    phone_raw = user_said.strip()
    if not is_valid_phone(phone_raw):
        return _say(
            "Sorry — I didn’t catch a valid phone number. Please say it again.",
            session,
        )

    collected["phone"] = normalize_phone(phone_raw)

    tokens = await redis_get_json(TOKENS_KEY)
    if tokens:
        ev = None
        events = list_upcoming_events(
            tokens,
            calendar_id=clinic.get("calendar_id", "primary"),
            days_ahead=60,
            max_results=50,
        )
        target = collected["phone"]
        for e in events:
            desc = _digits_only((e.get("description") or ""))
            if target and target in desc:
                ev = e
                break

        if ev:
            session["resch_event_id"] = ev.get("id")
            session["resch_event_summary"] = ev.get("summary", "Appointment")

    session["state"] = RESCH_NEW_PREF
    return _say(
        "Thanks. What day or time would you like to move it to?",
        session,
    )


if state == RESCH_NEW_PREF:
    collected["time_pref"] = user_said.strip()

    dw = parse_specific_day_window(collected["time_pref"], tz)
    if dw:
        collected["day_window_start"] = dw[0].isoformat()
        collected["day_window_end"] = dw[1].isoformat()
    else:
        collected.pop("day_window_start", None)
        collected.pop("day_window_end", None)

    session["state"] = RESCH_OFFER_SLOTS
    return _say("Okay — let me check availability.", session)


if state == RESCH_OFFER_SLOTS:
    dw = None
    if collected.get("day_window_start") and collected.get("day_window_end"):
        dw = (
            datetime.fromisoformat(collected["day_window_start"]),
            datetime.fromisoformat(collected["day_window_end"]),
        )

    raw_slots, labels, err = await suggest_top_slots(
        session,
        duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
        pref_text=collected.get("time_pref", ""),
        day_window=dw,
    )

    if err:
        session = _reset_to_triage(session)
        return _say(err, session)

    session[LAST_OFFERED_SLOTS_KEY] = raw_slots
    session[SLOT_LABELS_KEY] = labels
    session["state"] = RESCH_PICK_SLOT
    return _say(
        f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.",
        session,
    )


if state == RESCH_PICK_SLOT:
    m = re.search(r"\b(1|2|3)\b", _norm(user_said))
    if not m:
        return _say("Please say 1, 2, or 3.", session)

    idx = int(m.group(1)) - 1
    slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
    labels = session.get(SLOT_LABELS_KEY) or []

    if idx < 0 or idx >= len(slots):
        return _say("Please say 1, 2, or 3.", session)

    session[SELECTED_SLOT_KEY] = slots[idx]
    if idx < len(labels):
        session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

    session["state"] = RESCH_CONFIRM
    return _say("Please say yes to confirm, or no to cancel.", session)


if state == RESCH_CONFIRM:
    if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
        session = _reset_to_triage(session)
        return _say("No problem. What would you like to do instead?", session)

    tokens = await redis_get_json(TOKENS_KEY)
    chosen = session.get(SELECTED_SLOT_KEY)
    label = session.get(SELECTED_SLOT_LABEL_KEY) or "the new time"

    event_id = session.get("resch_event_id")
    if tokens and event_id and chosen:
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
    return _say(f"Confirmed — you’re rescheduled to {label}.", session)

    # ======================================================================
    # BOOKING FLOW (type -> reason -> time -> offer -> pick -> name -> phone -> confirm)
    # ======================================================================

    if state == BOOK_PATIENT_TYPE:
    # If user repeats "booking"/"reschedule", just re-ask clearly
    intent_check = detect_intent(user_said)
    if intent_check in ("BOOK", "RESCHEDULE", "CANCEL"):
        return _say("Sure — are you a new patient, or have you been here before?", session)

    # If they give their name here, store it and continue
    if looks_like_name(user_said) and not collected.get("name"):
        collected["name"] = user_said.strip()
        return _say("Thanks. And are you a new patient, or have you been here before?", session)

    pt = parse_patient_type(user_said)
    if not pt:
        return _say("No problem — are you a new patient, or have you been here before?", session)

    collected["patient_type"] = pt
    session["state"] = BOOK_REASON
    return _say(
        "Thanks. What’s the appointment for — for example physio assessment, follow-up, sports massage, or shockwave?",
        session,
    )
    
    if state == BOOK_TIME_PREF:
        collected["time_pref"] = user_said.strip()

        dw = parse_specific_day_window(collected["time_pref"], tz)
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"] = dw[1].isoformat()
        else:
            collected.pop("day_window_start", None)
            collected.pop("day_window_end", None)

        session["state"] = BOOK_OFFER_SLOTS
        return _say("Okay — let me check availability.", session)

    if state == BOOK_OFFER_SLOTS:
        dw = None
        if collected.get("day_window_start") and collected.get("day_window_end"):
            dw = (datetime.fromisoformat(collected["day_window_start"]), datetime.fromisoformat(collected["day_window_end"]))

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=collected.get("time_pref", ""),
            day_window=dw,
        )
        if err:
            session = _reset_to_triage(session)
            return _say(err, session)

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY] = labels
        session["state"] = BOOK_PICK_SLOT
        return _say(f"I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.", session)

    if state == BOOK_PICK_SLOT:
        m = re.search(r"\b(1|2|3)\b", _norm(user_said))
        if not m:
            return _say("Please say 1, 2, or 3.", session)

        idx = int(m.group(1)) - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []
        if idx < 0 or idx >= len(slots):
            return _say("Please say 1, 2, or 3.", session)

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

        session["state"] = BOOK_NAME
        return _say("Great. What’s your full name for the booking?", session)

    if state == BOOK_NAME:
        collected["name"] = user_said.strip()
        session["state"] = BOOK_PHONE
        return _say("Thanks. What’s the best mobile number for the booking?", session)

    if state == BOOK_PHONE:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return _say("Sorry — I didn’t catch a valid phone number. Please say the phone number again.", session)
        collected["phone"] = normalize_phone(phone_raw)
        session["state"] = BOOK_CONFIRM
        return _say("Perfect. Please say yes to confirm the booking, or no to cancel.", session)

    if state == BOOK_CONFIRM:
        if _norm(user_said) not in ("yes", "y", "yeah", "confirm", "ok", "okay"):
            session = _reset_to_triage(session)
            return _say("No problem. What would you like to do instead?", session)

        chosen = session.get(SELECTED_SLOT_KEY)
        label = session.get(SELECTED_SLOT_LABEL_KEY) or "the selected time"

        tokens = await redis_get_json(TOKENS_KEY)

        # Calendar mode
        if tokens and chosen:
            start = datetime.fromisoformat(chosen["start"])
            end = datetime.fromisoformat(chosen["end"])

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
                calendar_id=clinic.get("calendar_id", "primary"),
            )

            session = _reset_to_triage(session)
            if not event or not event.get("id"):
                return _say("I couldn’t create the booking. Please try again.", session)

            return _say(f"Confirmed — you’re booked for {label}.", session)

        # Demo mode
        session = _reset_to_triage(session)
        return _say(f"Confirmed — you’re booked for {label}.", session)

    # ======================================================================
    # If we reach here, reset safely
    # ======================================================================
    session = _reset_to_triage(session)
    return _say(
        "I can help with booking, rescheduling, opening hours, location, prices, insurance, or general questions. What would you like to do?",
        session,
    )
