# app/flows/triage.py
from __future__ import annotations

from typing import Dict, Any, Tuple, Optional
import re
import random
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

FRIENDLY_REASSURE = [
    "No worries.",
    "That’s totally fine.",
    "We can sort that out.",
]

FRIENDLY_CHECKING = [
    "Okay — I’ll check that now.",
    "One moment — I’m checking.",
    "Sure — let me take a quick look.",
]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _maybe_prefix(text: str) -> str:
    if not text:
        return text

    lower = text.lower()

    # Don't mess with apologies or confirmations
    if lower.startswith(("sorry", "perfect", "thanks", "confirmed", "all done", "you’re", "you're")):
        return text

    if random.random() < 0.35:
        return f"{random.choice(FRIENDLY_ACK)} {text}"

    return text


def _maybe_suffix(text: str) -> str:
    if random.random() < 0.20:
        return f"{text} {random.choice(FRIENDLY_REASSURE)}"
    return text


def _friendly(text: str) -> str:
    text = _clean(text)
    text = _maybe_prefix(text)
    text = _maybe_suffix(text)
    return _clean(text)


def _say(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
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


def parse_slot_choice(text: str) -> Optional[int]:
    """
    Return 1/2/3 if the user picked a slot, else None.
    Supports: "1", "one", "option 1", "number one", etc.
    """
    t = _norm(text)

    # First: direct digit
    m = re.search(r"\b(1|2|3)\b", t)
    if m:
        return int(m.group(1))

    # Word mapping
    word_map = {
        "one": 1,
        "first": 1,
        "two": 2,
        "second": 2,
        "three": 3,
        "third": 3,
    }

    # Match whole words
    for w, n in word_map.items():
        if re.search(rf"\b{w}\b", t):
            return n

    return None


def looks_like_name(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2 or len(t) > 60:
        return False
    letters = re.findall(r"[A-Za-z]", t)
    digits = re.findall(r"\d", t)
    if len(letters) < 2:
        return False
    if len(digits) > 3:
        return False
    if _norm(t) in ("booking", "book", "reschedule", "cancel", "appointment", "new", "returning"):
        return False
    return True


def is_yes(text: str) -> bool:
    return _norm(text) in ("yes", "y", "yeah", "yep", "confirm", "ok", "okay", "sure")


def _contains_any(t: str, keywords: list[str]) -> bool:
    return any(k in t for k in keywords)


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def get_clinic(session: Dict[str, Any]) -> Dict[str, Any]:
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
        "returning",
        "existing",
        "been before",
        "follow up",
        "follow-up",
        "followup",
        "already a patient",
        "i've been",
        "i have been",
        "return patient",
        "recurring",
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


def _is_interrupt(text: str) -> bool:
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
    session.pop("manual_booking", None)
    session.pop("manual_reason", None)
    session.pop("manual_reschedule", None)
    return session


def _interrupt_reply(text: str, session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
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
    return _say("Of course. When you’re ready, tell me what you’d like to do.", session)


def detect_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "UNKNOWN"

    if _contains_any(t, ["book", "booking", "book in", "appointment", "schedule", "available", "slot", "availability"]):
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

    if _contains_any(
        t,
        ["insurance", "insured", "bupa", "axa", "vitality", "aviva", "wpa", "cigna", "claim", "receipt"],
    ):
        return "FAQ_INSURANCE"

    if _contains_any(
        t,
        ["treatments", "treatment", "services", "physio", "physiotherapy", "massage", "sports therapy", "rehab", "shockwave"],
    ):
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
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def parse_specific_day_window(text: str, tz) -> Optional[tuple[datetime, datetime]]:
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
BOOK_PICK_SLOT = "BOOK_PICK_SLOT"
BOOK_NAME = "BOOK_NAME"
BOOK_PHONE = "BOOK_PHONE"
BOOK_CONFIRM = "BOOK_CONFIRM"

# Reschedule
RESCH_NAME = "RESCH_NAME"
RESCH_ORIGINAL = "RESCH_ORIGINAL"
RESCH_NEW_PREF = "RESCH_NEW_PREF"
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

    # Debug (remove later)
    print("CALENDAR TOKENS PRESENT:", bool(tokens))
    if tokens:
        try:
            print("TOKENS KEYS:", list(tokens.keys()))
        except Exception:
            print("TOKENS KEYS: (not a dict)")

    candidates = generate_candidate_slots(
        w_start,
        w_end,
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

    # Live calendar (safe)
    try:
        busy = freebusy(
            tokens,
            time_min=w_start,
            time_max=w_end,
            calendar_id=clinic.get("calendar_id", "primary"),
        )
    except Exception as e:
        # Calendar unavailable → do NOT invent availability
        print("CALENDAR ERROR (freebusy):", repr(e))
        return [], [], (
            "No problem — I’m having trouble checking the live calendar right now. "
            "Tell me your preferred day or time, and I’ll log a booking request for the clinic to confirm."
        )

    busy_blocks = parse_busy(busy or [])
    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    # If preference window is too strict, widen to clinic hours
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

    raw = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in top3]
    labels = [format_slot((s, e)) for s, e in top3]
    return raw, labels, None


# ---------- RESCHEDULE HELPERS ----------
def _safe_parse_user_datetime(text: str, tz) -> Optional[datetime]:
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
    # TRIAGE: LLM + knowledge + deterministic routing
    # ==========================================================
    if state == TRIAGE:
        try:
            kb = retrieve_knowledge(user_said, clinic=clinic)
        except Exception:
            kb = ""

        try:
            llm = route_and_answer(
                user_text=((f"KNOWLEDGE:\n{kb}\n\n" if kb else "") + user_said),
                clinic=clinic,
                current_state=state,
                last_bot_prompt=session.get(LAST_BOT_PROMPT_KEY, ""),
            )
            llm_intent = (llm.get("intent") or "").strip()
            conf = float(llm.get("confidence") or 0.0)
            reply = (llm.get("reply") or "").strip()
            follow = (llm.get("follow_up_question") or "").strip()

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

                if llm_intent in ("FAQ", "OTHER", "MESSAGE"):
                    if reply:
                        if follow:
                            return _say(f"{reply} {follow}", session)
                        return _say(reply, session)

        except Exception:
            pass

        intent = detect_intent(user_said)

        if intent == "BOOK":
            session = _reset_to_triage(session)
            session["state"] = BOOK_PATIENT_TYPE
            return _say("Sure — are you a new patient, or have you been here before?", session)

        if intent == "RESCHEDULE":
            session = _reset_to_triage(session)
            session["state"] = RESCH_NAME
            return _say("Sure — to reschedule, what’s your full name?", session)

        if intent == "CANCEL":
            session = _reset_to_triage(session)
            return _say(
                "Sure — can I take your full name and the date and time of the appointment you want to cancel?",
                session,
            )

        if intent == "HUMAN":
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

        if intent.startswith("FAQ_"):
            return _say(faq_answer(intent, clinic), session)

        return _say(
            "I can help with booking, rescheduling, opening hours, location, prices, insurance, or general questions. What would you like to do?",
            session,
        )

    # ======================================================================
    # RESCHEDULE FLOW: name -> original appt -> desired new time -> offer -> pick -> confirm
    # ======================================================================
    if state == RESCH_NAME:
        intent_check = detect_intent(user_said)
        if intent_check in ("RESCHEDULE", "BOOK", "CANCEL"):
            return _say("Sure — what’s your full name?", session)

        collected["name"] = user_said.strip()
        session["state"] = RESCH_ORIGINAL
        return _say(
            f"{random.choice(FRIENDLY_ACK)} What was the date and time of your original appointment?",
            session,
        )

    if state == RESCH_ORIGINAL:
        collected["original_appt"] = user_said.strip()

        # Try calendar match by name + time
        try:
            ev = await find_event_by_name_and_time(
                session,
                collected.get("name", ""),
                collected.get("original_appt", ""),
            )
        except Exception as e:
            print("RESCHEDULE find_event_by_name_and_time error:", repr(e))
            ev = None

        if ev:
            session["resch_event_id"] = ev.get("id")
            session["resch_event_summary"] = ev.get("summary", "Appointment")
            session["state"] = RESCH_NEW_PREF
            return _say("Thanks. What day or time would you like to move it to?", session)

        # If we have tokens but couldn't match, ask phone as fallback
        tokens = await redis_get_json(TOKENS_KEY)
        if tokens:
            session["state"] = RESCH_PHONE_FALLBACK
            return _say(
                "Thanks. To find it quickly, what phone number was used for the booking?",
                session,
            )

        # No tokens → we can't match the booking, but we can still take the reschedule request
        session["manual_reschedule"] = True
        session["manual_reason"] = "no_calendar_tokens"
        session["state"] = RESCH_NEW_PREF
        return _say(
            "No problem — I can still help. What day or time would you like to move it to?",
            session,
        )

    if state == RESCH_PHONE_FALLBACK:
        phone_raw = user_said.strip()
        if not is_valid_phone(phone_raw):
            return _say("Sorry — I didn’t catch a valid phone number. Please say it again.", session)

        collected["phone"] = normalize_phone(phone_raw)

        tokens = await redis_get_json(TOKENS_KEY)

        # If tokens exist, try match by phone in event description
        if tokens:
            try:
                events = list_upcoming_events(
                    stored_tokens=tokens,
                    calendar_id=clinic.get("calendar_id", "primary"),
                    days_ahead=60,
                    max_results=50,
                )
                target = collected["phone"]
                ev = None
                for e in events:
                    desc = _digits_only((e.get("description") or ""))
                    if target and target in desc:
                        ev = e
                        break

                if ev:
                    session["resch_event_id"] = ev.get("id")
                    session["resch_event_summary"] = ev.get("summary", "Appointment")
                else:
                    session["manual_reschedule"] = True
                    session["manual_reason"] = "event_not_found"
            except Exception as e:
                print("RESCHEDULE list_upcoming_events error:", repr(e))
                session["manual_reschedule"] = True
                session["manual_reason"] = "calendar_lookup_error"
        else:
            session["manual_reschedule"] = True
            session["manual_reason"] = "no_calendar_tokens"

        session["state"] = RESCH_NEW_PREF
        return _say("Thanks. What day or time would you like to move it to?", session)

    if state == RESCH_NEW_PREF:
        collected["time_pref"] = user_said.strip()

        dw = parse_specific_day_window(collected["time_pref"], tz)
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"] = dw[1].isoformat()
        else:
            collected.pop("day_window_start", None)
            collected.pop("day_window_end", None)

        dw_parsed = None
        if collected.get("day_window_start") and collected.get("day_window_end"):
            dw_parsed = (
                datetime.fromisoformat(collected["day_window_start"]),
                datetime.fromisoformat(collected["day_window_end"]),
            )

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=collected.get("time_pref", ""),
            day_window=dw_parsed,
        )

        # Calendar fails → manual request
        if err:
            session["manual_reschedule"] = True
            session["manual_reason"] = "calendar_unavailable"
            session["state"] = RESCH_CONFIRM
            return _say(
                f"{err} No worries — I’ll log a reschedule request for the clinic to confirm. "
                "Please say yes to confirm I should pass this on, or no to cancel.",
                session,
            )

        # Safety: if slots/labels missing, manual
        if not labels or len(labels) < 3 or not raw_slots or len(raw_slots) < 3:
            session["manual_reschedule"] = True
            session["manual_reason"] = "no_slots_returned"
            session["state"] = RESCH_CONFIRM
            return _say(
                "No problem — I can’t see clear availability right now. "
                "I’ll log a reschedule request for the clinic to confirm. "
                "Please say yes to confirm, or no to cancel.",
                session,
            )

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY] = labels
        session["state"] = RESCH_PICK_SLOT
        return _say(
            f"{random.choice(FRIENDLY_ACK)} I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.",
            session,
        )

    if state == RESCH_PICK_SLOT:
        choice = parse_slot_choice(user_said)
        if not choice:
            return _say("Sorry — please say 1, 2, or 3.", session)

        idx = choice - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []

        if idx < 0 or idx >= len(slots):
            return _say("Sorry — please say 1, 2, or 3.", session)

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

        session["state"] = RESCH_CONFIRM
        return _say("Perfect. Please say yes to confirm, or no to cancel.", session)

    if state == RESCH_CONFIRM:
        if not is_yes(user_said):
            session = _reset_to_triage(session)
            return _say("No problem. What would you like to do instead?", session)

        label = session.get(SELECTED_SLOT_LABEL_KEY) or collected.get("time_pref") or "the new time"

        # Manual reschedule path
        if session.get("manual_reschedule"):
            if send_to_sheet is not None:
                try:
                    send_to_sheet(
                        name=collected.get("name", ""),
                        phone=collected.get("phone", ""),
                        intent="RESCHEDULE_REQUEST_MANUAL",
                        message=(
                            "Manual reschedule requested. "
                            f"Original appt: {collected.get('original_appt','')}. "
                            f"Requested new time: {label}. "
                            f"Reason: {session.get('manual_reason','')}. "
                            f"call_sid={session.get('call_sid','')}"
                        ),
                        call_sid=session.get("call_sid", ""),
                    )
                except Exception as e2:
                    print("send_to_sheet error:", repr(e2))

            session = _reset_to_triage(session)
            return _say(
                f"Perfect — I’ve logged your reschedule request for {label}. The clinic will confirm it shortly.",
                session,
            )

        # Normal calendar reschedule path
        tokens = await redis_get_json(TOKENS_KEY)
        chosen = session.get(SELECTED_SLOT_KEY)
        event_id = session.get("resch_event_id")

        if tokens and event_id and chosen:
            try:
                start = datetime.fromisoformat(chosen["start"])
                end = datetime.fromisoformat(chosen["end"])
                patch_event_time(
                    stored_tokens=tokens,
                    event_id=event_id,
                    start_dt=start,
                    end_dt=end,
                    calendar_id=clinic.get("calendar_id", "primary"),
                )
            except Exception as e:
                print("RESCHEDULE patch_event_time error:", repr(e))

                # If patch fails → log manual request
                if send_to_sheet is not None:
                    try:
                        send_to_sheet(
                            name=collected.get("name", ""),
                            phone=collected.get("phone", ""),
                            intent="RESCHEDULE_REQUEST_MANUAL",
                            message=(
                                "Calendar patch FAILED. "
                                f"Original appt: {collected.get('original_appt','')}. "
                                f"Requested new time: {label}. "
                                f"call_sid={session.get('call_sid','')}"
                            ),
                            call_sid=session.get("call_sid", ""),
                        )
                    except Exception:
                        pass

                session = _reset_to_triage(session)
                return _say(
                    f"Perfect — I’ve logged your reschedule request for {label}. The clinic will confirm it shortly.",
                    session,
                )

        session = _reset_to_triage(session)
        return _say(f"Confirmed — you’re rescheduled to {label}. We look forward to seeing you.", session)

    # ======================================================================
    # BOOKING FLOW
    # ======================================================================
    if state == BOOK_PATIENT_TYPE:
        intent_check = detect_intent(user_said)
        if intent_check in ("BOOK", "RESCHEDULE", "CANCEL"):
            return _say("Sure — are you a new patient, or have you been here before?", session)

        # If they accidentally give a name here, store it and continue
        if looks_like_name(user_said) and not collected.get("name"):
            collected["name"] = user_said.strip()
            return _say("Thanks. And are you a new patient, or have you been here before?", session)

        pt = parse_patient_type(user_said)
        if not pt:
            return _say("No problem — are you a new patient, or have you been here before?", session)

        collected["patient_type"] = pt
        session["state"] = BOOK_REASON
        return _say(
            "Great. What’s the appointment for — for example physio assessment, follow-up, sports massage, or shockwave?",
            session,
        )

    if state == BOOK_REASON:
        collected["reason"] = user_said.strip()
        session["state"] = BOOK_TIME_PREF
        return _say("Thanks. What day or time would you prefer?", session)

    if state == BOOK_TIME_PREF:
        collected["time_pref"] = user_said.strip()

        dw = parse_specific_day_window(collected["time_pref"], tz)
        if dw:
            collected["day_window_start"] = dw[0].isoformat()
            collected["day_window_end"] = dw[1].isoformat()
        else:
            collected.pop("day_window_start", None)
            collected.pop("day_window_end", None)

        dw_parsed = None
        if collected.get("day_window_start") and collected.get("day_window_end"):
            dw_parsed = (
                datetime.fromisoformat(collected["day_window_start"]),
                datetime.fromisoformat(collected["day_window_end"]),
            )

        raw_slots, labels, err = await suggest_top_slots(
            session,
            duration_min=int(clinic.get("slot_minutes", DEFAULT_DURATION_MIN)),
            pref_text=collected.get("time_pref", ""),
            day_window=dw_parsed,
        )

        # Calendar unavailable → manual booking request flow
        if err:
            session["manual_booking"] = True
            session["manual_reason"] = "calendar_unavailable"
            session["state"] = BOOK_NAME
            return _say(f"{err} To get that sorted, what’s your full name?", session)

        # Safety: if we didn't get 3 labels, go manual
        if not labels or len(labels) < 3 or not raw_slots or len(raw_slots) < 3:
            session["manual_booking"] = True
            session["manual_reason"] = "no_slots_returned"
            session["state"] = BOOK_NAME
            return _say(
                "No problem — I can’t see clear availability right now. What’s your full name so I can log a booking request?",
                session,
            )

        session[LAST_OFFERED_SLOTS_KEY] = raw_slots
        session[SLOT_LABELS_KEY] = labels
        session["state"] = BOOK_PICK_SLOT

        return _say(
            f"{random.choice(FRIENDLY_CHECKING)} I can do: 1) {labels[0]}, 2) {labels[1]}, 3) {labels[2]}. Say 1, 2, or 3.",
            session,
        )

    if state == BOOK_PICK_SLOT:
        choice = parse_slot_choice(user_said)
        if not choice:
            return _say("Sorry — please say 1, 2, or 3.", session)

        idx = choice - 1
        slots = session.get(LAST_OFFERED_SLOTS_KEY) or []
        labels = session.get(SLOT_LABELS_KEY) or []

        if idx < 0 or idx >= len(slots):
            return _say("Sorry — please say 1, 2, or 3.", session)

        session[SELECTED_SLOT_KEY] = slots[idx]
        if idx < len(labels):
            session[SELECTED_SLOT_LABEL_KEY] = labels[idx]

        session["state"] = BOOK_NAME
        return _say("Perfect — what’s your full name for the booking?", session)

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
        return _say("Great. Please say yes to confirm the booking, or no to cancel.", session)

     if state == BOOK_CONFIRM:
        # Only cancel on an explicit NO.
        # Anything unclear (including empty) should reprompt — never auto-cancel.
        if is_yes(user_said):
            # ✅ proceed to actually create / finalize booking here
            # (whatever your next state/function is)
            session["state"] = BOOK_FINALIZE  # <-- replace with your real next state
            return _say("Perfect — confirming now.", session)

        if is_no(user_said):
            session = _reset_to_triage(session)
            return _say("No problem — I’ve cancelled that. What would you like to do instead?", session)

        # Unclear / empty input -> reprompt
        return _say(
            "Sorry — just to confirm, should I book that appointment? "
            "Please say yes to confirm, or no to cancel. You can also press 1 for yes or 2 for no.",
            session,
        )


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

            return _say(f"Confirmed — you’re booked for {label}. Thanks, and we’ll see you then.", session)

        # Demo mode fallback
        session = _reset_to_triage(session)
        return _say(f"Confirmed — you’re booked for {label}. Thanks, and we’ll see you then.", session)

    # If we reach here, reset safely
    session = _reset_to_triage(session)
    return _say(
        "I can help with booking, rescheduling, opening hours, location, prices, insurance, or general questions. What would you like to do?",
        session,
    )
