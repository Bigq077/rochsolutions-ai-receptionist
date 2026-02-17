# app/routes/twilio.py
from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session
from app.clinic_config import clinic_id_from_twilio_to, get_clinic

router = APIRouter(prefix="/twilio")


# --------------------------------------------------
# Constants
# --------------------------------------------------

AI_NAME        = "Susie"
CLINIC_NAME    = "Theorem Health"

# Improvement 1: New opening greeting — Susie introduces herself and asks location
OPENING_GREETING = (
    f"Hi there! My name is {AI_NAME}, {CLINIC_NAME}'s AI receptionist. "
    f"Quick question before I start — "
    f"are you calling in regards to the Alcester clinic or the Redditch one?"
)

# Improvement 2: Correct location-specific opening hours
LOCATION_HOURS = {
    "alcester": (
        "The Alcester clinic is open Monday to Friday, "
        "eight thirty in the morning until nine at night. "
        "We're closed on weekends."
    ),
    "redditch": (
        "The Redditch clinic is open Monday to Saturday. "
        "Monday, Tuesday and Friday we're open nine to five. "
        "Wednesday and Thursday we're open nine to seven. "
        "And Saturday we're open nine to five. "
        "We're closed on Sundays."
    ),
}

LOCATION_ADDRESSES = {
    "alcester": (
        "We're at The Greig Sports Center, Kinwarton Road, Alcester, B49 6AD."
    ),
    "redditch": (
        "We're at 51 Bromsgrove Road, Redditch, B97 4RH."
    ),
}

# Confirmation messages after location selected
LOCATION_CONFIRMATIONS = {
    "alcester": "Great! I've got you down for the Alcester clinic.",
    "redditch": "Perfect! I've got you down for the Redditch clinic.",
}

# After location confirmed — hand off to triage
HOW_CAN_I_HELP = "How can I help you today?"


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def _abs_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def gather_speech(
    action_url: str,
    prompt: str | None = None,
    timeout: int = 10,
) -> Gather:
    g = Gather(
        input="speech dtmf",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=timeout,
        action_on_empty_result=True,
        barge_in=True,
        num_digits=1,
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


def _append_turn(session: dict, role: str, text: str) -> dict:
    if not text:
        return session
    turns = session.get("turns", [])
    turns.append({"role": role, "text": text})
    session["turns"] = turns
    return session


def _ensure_clinic_on_session(session: dict, to_number: str | None) -> dict:
    if not session.get("clinic_id"):
        session["clinic_id"] = clinic_id_from_twilio_to(to_number)
    return session


def _init_session(session: dict, call_sid: str) -> dict:
    """Initialise fields that must exist from the very first request."""
    if not session.get("session_id"):
        session["session_id"] = str(uuid.uuid4())
    session["call_sid"] = call_sid
    # Location state — set by /twilio/location-select
    session.setdefault("location_selected", False)
    session.setdefault("selected_location", None)
    # Insurance state
    session.setdefault("insurance_info", {})
    # Miss counter
    session.setdefault("miss_count", 0)
    return session


def safe_error_response(request: Request) -> PlainTextResponse:
    """Return a graceful TwiML error response — never crash the call."""
    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    vr.append(
        gather_speech(
            turn_url,
            "I'm sorry, something went wrong on my end. "
            "Please bear with me — let me try that again.",
        )
    )
    return xml(vr)


# --------------------------------------------------
# TTS helper (ElevenLabs with Polly fallback)
# --------------------------------------------------

async def _say(
    vr: VoiceResponse,
    text: str,
    request: Request,
    gather_action: str | None = None,
) -> None:
    """
    Attempt ElevenLabs TTS; fall back to Polly.
    If gather_action is given, wraps the audio in a Gather so the
    caller can barge in.
    """
    try:
        from app.routes.tts_eleven import tts_eleven_url, TTSReq
        data = tts_eleven_url(TTSReq(text=text), request)
        audio_url = data["audio_url"]
        if gather_action:
            g = Gather(
                input="speech dtmf",
                action=gather_action,
                method="POST",
                language="en-GB",
                speech_timeout="auto",
                timeout=10,
                action_on_empty_result=True,
                barge_in=True,
            )
            g.play(audio_url)
            vr.append(g)
        else:
            vr.play(audio_url)
    except Exception as e:
        print("TTS_ELEVEN ERROR:", repr(e))
        if gather_action:
            vr.append(gather_speech(gather_action, text))
        else:
            vr.say(text, language="en-GB")


def attach_status_callback(vr: VoiceResponse, request: Request) -> None:
    vr.status_callback = _abs_url(request, "/twilio/status")
    vr.status_callback_method = "POST"


# --------------------------------------------------
# Location helper
# --------------------------------------------------

def _detect_location(speech: str) -> str | None:
    """
    Return 'alcester' or 'redditch' from caller's speech, or None.
    Deliberately loose matching — people slur place names on the phone.
    """
    s = speech.lower()
    if any(k in s for k in ("alcester", "alce", "alchester", "alcest")):
        return "alcester"
    if any(k in s for k in ("redditch", "reditch", "red", "redd")):
        return "redditch"
    return None


# --------------------------------------------------
# Insurance keyword detection
# --------------------------------------------------

_INSURANCE_KEYWORDS = (
    "insurance", "insured", "insurer", "claim", "policy",
    "cover", "covered", "bupa", "axa", "vitality", "aviva",
    "wpa", "health insurance", "private health", "simply health",
    "cigna", "benenden", "nuffield",
)


def _mentions_insurance(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _INSURANCE_KEYWORDS)


# =========================================================
# Call status webhook (end-of-call logging)
# =========================================================

@router.post("/status")
async def status(request: Request) -> PlainTextResponse:
    form = await request.form()

    call_sid    = (form.get("CallSid")    or "").strip()
    call_status = (form.get("CallStatus") or "").strip().lower()

    if call_status not in (
        "completed", "busy", "failed",
        "no-answer", "no_answer", "canceled", "cancelled",
    ):
        return PlainTextResponse("ok")

    if not call_sid:
        return PlainTextResponse("missing CallSid", status_code=400)

    try:
        from app.tools.call_summary import build_call_summary, summary_to_sheet_row
        from app.tools.handoff import append_summary_row
    except Exception as e:
        print("STATUS IMPORT ERROR:", repr(e))
        return PlainTextResponse("ok")

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    if session.get("call_summary_logged"):
        return PlainTextResponse("already logged")

    ended_at = datetime.utcnow().isoformat() + "Z"
    session["call_sid"]             = call_sid
    session["call_status"]          = call_status
    session["ended_at_utc"]         = ended_at
    session["twilio_from"]          = (form.get("From")         or "").strip()
    session["twilio_to"]            = (form.get("To")           or "").strip()
    session["twilio_direction"]     = (form.get("Direction")    or "").strip()
    session["twilio_duration_sec"]  = (form.get("CallDuration") or "").strip()
    session["twilio_timestamp"]     = (form.get("Timestamp")    or "").strip()

    try:
        session["twilio_status_payload"] = {k: str(v) for k, v in form.items()}
    except Exception:
        session["twilio_status_payload"] = {}

    session = _ensure_clinic_on_session(session, session.get("twilio_to"))

    # 🔴 TEMP TEST OVERRIDE — remove after go-live
    session["clinic_id"] = "theorem"

    try:
        summary = build_call_summary(session)
        row     = summary_to_sheet_row(summary)
        if append_summary_row(row):
            session["call_summary_logged"] = True
            await save_session(call_sid, session)
    except Exception as e:
        print("CALL SUMMARY ERROR:", repr(e))

    return PlainTextResponse("ok")


# =========================================================
# MAIN VOICE ENTRYPOINT
# Improvement 1: Susie greeting + location question
# =========================================================

@router.api_route("/voice", methods=["GET", "POST", "HEAD"])
async def voice(request: Request):
    if request.method in ("HEAD", "GET"):
        return Response(status_code=200)

    form       = await request.form()
    call_sid   = (form.get("CallSid") or "").strip()
    to_number  = (form.get("To")      or "").strip() or None

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

    # 🔴 TEMP TEST OVERRIDE — remove after go-live
    session["clinic_id"] = "theorem"

    await save_session(call_sid, session)

    vr = VoiceResponse()
    location_url = _abs_url(request, "/twilio/location-select")

    attach_status_callback(vr, request)

    # Improvement 1: Play Susie greeting and ask for location
    await _say(vr, OPENING_GREETING, request, gather_action=location_url)

    # Fallback redirect if caller says nothing
    vr.redirect(_abs_url(request, "/twilio/voice"), method="POST")
    return xml(vr)


# =========================================================
# LOCATION SELECTION
# Improvement 1 + 2: Detect Alcester / Redditch, store in session
# =========================================================

@router.post("/location-select")
async def location_select(request: Request):
    form     = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    speech   = (form.get("SpeechResult") or "").strip()
    digits   = (form.get("Digits") or "").strip()
    user_said = speech or digits

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)

    vr           = _abs_url(request, "/twilio/voice")
    location_url = _abs_url(request, "/twilio/location-select")
    turn_url     = _abs_url(request, "/twilio/turn")

    location = _detect_location(user_said) if user_said else None

    if not location:
        # Could not detect — ask again politely (max 2 retries)
        miss = int(session.get("location_miss", 0)) + 1
        session["location_miss"] = miss
        await save_session(call_sid, session)

        vr_resp = VoiceResponse()
        if miss <= 2:
            await _say(
                vr_resp,
                "Sorry, I didn't quite catch that. "
                "Are you calling about the Alcester clinic or the Redditch clinic?",
                request,
                gather_action=location_url,
            )
        else:
            # After 2 failed attempts, default to Alcester and carry on
            session["selected_location"] = "alcester"
            session["location_selected"] = True
            await save_session(call_sid, session)
            await _say(
                vr_resp,
                "No problem — I'll put you through to our general line. "
                + HOW_CAN_I_HELP,
                request,
                gather_action=turn_url,
            )
        return xml(vr_resp)

    # ✅ Location detected
    session["selected_location"] = location
    session["location_selected"] = True
    session["location_miss"]     = 0
    await save_session(call_sid, session)

    # Confirm location then ask how we can help
    confirmation = LOCATION_CONFIRMATIONS[location] + " " + HOW_CAN_I_HELP

    vr_resp = VoiceResponse()
    await _say(vr_resp, confirmation, request, gather_action=turn_url)
    vr_resp.redirect(_abs_url(request, "/twilio/voice"), method="POST")
    return xml(vr_resp)


# =========================================================
# TURN HANDLER
# Improvements 3-6: Location-aware triage, knowledge base,
# insurance detection, service explanations
# =========================================================

@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr   = VoiceResponse()
    form = await request.form()

    call_sid  = (form.get("CallSid")  or "").strip()
    to_number = (form.get("To")       or "").strip() or None
    digits    = (form.get("Digits")   or "").strip() or None
    speech    = (form.get("SpeechResult") or "").strip()
    unstable  = (form.get("UnstableSpeechResult") or "").strip()

    user_said = digits if digits else (speech or unstable)

    # Normalise simple yes/no
    if user_said:
        t = re.sub(r"\s+", " ", user_said.lower())
        if re.fullmatch(r"(yes|yeah|yep|ok|okay|confirm)[\W_]*", t):
            user_said = "yes"
        elif re.fullmatch(r"(no|nope|cancel|stop)[\W_]*", t):
            user_said = "no"

    from app.flows.triage import triage_turn

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session = _init_session(session, call_sid)
    session = _ensure_clinic_on_session(session, to_number)

    # 🔴 TEMP TEST OVERRIDE — remove after go-live
    session["clinic_id"] = session.get("clinic_id") or "theorem"
    session["clinic_id"] = "theorem"

    turn_url  = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    # --------------------------------------------------
    # Safety net: if location was never set, send back
    # to the greeting so Susie can ask again
    # --------------------------------------------------
    if not session.get("location_selected"):
        await save_session(call_sid, session)
        vr.redirect(voice_url, method="POST")
        return xml(vr)

    # --------------------------------------------------
    # Handle empty input
    # --------------------------------------------------
    if not user_said:
        miss = int(session.get("miss_count", 0)) + 1
        session["miss_count"] = miss
        await save_session(call_sid, session)

        if miss == 1:
            vr.append(gather_speech(turn_url, "Sorry — I didn't catch that. Could you repeat?"))
            return xml(vr)

        if miss == 2:
            vr.append(
                gather_speech(
                    turn_url,
                    "Are you looking to book, ask about a treatment, "
                    "or get information about opening times or location?",
                )
            )
            return xml(vr)

        # 3rd miss — offer to take a message
        vr.append(
            gather_speech(
                turn_url,
                "Not to worry. I can take a message for the team. "
                "Please say your name, your number, "
                "and what you'd like help with.",
            )
        )
        return xml(vr)

    session["miss_count"] = 0

    # --------------------------------------------------
    # Improvement 5: Insurance detection — intercept before triage
    # so we can flag it explicitly in session before triage_turn sees it
    # --------------------------------------------------
    if _mentions_insurance(user_said) and not session.get("insurance_flagged"):
        session["insurance_flagged"] = True
        # Let triage_turn handle the full insurance flow,
        # but prime the session so it knows to start the calm explanation
        session["insurance_info"]["mentioned"] = True
        session["insurance_info"]["trigger_explanation"] = True

    # --------------------------------------------------
    # Improvement 2: Location-aware keyword shortcuts
    # Handle hours / address / parking before hitting triage AI
    # so we get instant, accurate, location-specific answers
    # --------------------------------------------------
    quick_reply = _try_quick_answer(user_said, session)
    if quick_reply:
        session = _append_turn(session, "caller", user_said)
        session = _append_turn(session, "assistant", quick_reply)
        await save_session(call_sid, session)
        vr.append(gather_speech(turn_url, quick_reply))
        return xml(vr)

    # --------------------------------------------------
    # Main triage turn
    # session["selected_location"] is available to triage_turn
    # so all recommendations / availability checks are location-aware
    # --------------------------------------------------
    session = _append_turn(session, "caller", user_said)

    try:
        reply_text, session = await triage_turn(user_said, session)
    except Exception as e:
        print("TRIAGE ERROR:", repr(e))
        vr.append(
            gather_speech(turn_url, "Sorry, something went wrong. Let me try that again.")
        )
        return xml(vr)

    session = _append_turn(session, "assistant", reply_text)
    await save_session(call_sid, session)

    vr.append(gather_speech(turn_url, reply_text))
    return xml(vr)


# =========================================================
# QUICK ANSWER HANDLER
# Improvement 2: Location-specific instant answers
# for hours, address, parking — no AI needed
# =========================================================

def _try_quick_answer(user_said: str, session: dict) -> str | None:
    """
    Return a canned, location-specific answer for common factual
    questions. Returns None if the question should go to triage_turn.
    """
    location = session.get("selected_location", "alcester")
    t = user_said.lower()

    # Opening hours
    if any(kw in t for kw in ("hour", "open", "close", "when are you", "what time")):
        hours    = LOCATION_HOURS.get(location, LOCATION_HOURS["alcester"])
        location_label = "Alcester" if location == "alcester" else "Redditch"
        return (
            f"{hours} "
            f"Is there anything else I can help you with, "
            f"or would you like to book an appointment at {location_label}?"
        )

    # Address / directions
    if any(kw in t for kw in ("address", "where are you", "location", "find you", "directions")):
        address = LOCATION_ADDRESSES.get(location, LOCATION_ADDRESSES["alcester"])
        return (
            f"{address} "
            f"Would you like to book an appointment, or is there anything else I can help with?"
        )

    # Parking
    if "park" in t:
        if location == "alcester":
            return (
                "There's parking available at the Greig Sports Center. "
                "Can I help you with anything else?"
            )
        else:
            return (
                "There's street parking on Bromsgrove Road by the Redditch clinic. "
                "Can I help you with anything else?"
            )

    # What to wear / bring
    if any(kw in t for kw in ("wear", "bring", "prepare", "what should i")):
        return (
            "Just wear loose or comfortable clothing if you can. "
            "If you have any scans or reports, do bring those along. "
            "But honestly, don't worry if you haven't — just come as you are. "
            "Anything else I can help with?"
        )

    # Cancellation policy
    if any(kw in t for kw in ("cancel", "cancellation", "reschedule", "change appointment")):
        return (
            "We have a twenty-four hour cancellation policy. "
            "If you need to cancel or reschedule, please let us know at least "
            "twenty-four hours before your appointment, otherwise the full fee applies. "
            "You can reach us on 07870 166861. "
            "Is there anything else I can help you with?"
        )

    # Price / cost
    if any(kw in t for kw in ("cost", "price", "how much", "fee", "charge")):
        return (
            "Physiotherapy sessions are seventy-five pounds for fifty minutes. "
            "Rehabilitation sessions are sixty-five pounds. "
            "Psychotherapy is also seventy-five pounds. "
            "If we use specialist equipment like shockwave or laser therapy during your session, "
            "there's an additional forty-five pounds for that. "
            "And prescribing consultations are twelve pounds fifty. "
            "Would you like to book an appointment?"
        )

    return None


# =========================================================
# BOOKING SERVICE FACTORY
# =========================================================

async def create_booking_service_for_clinic(
    clinic_id: str,
    redis_client,
):
    """Factory to create BookingService with Acuity provider."""
    from app.booking import BookingService
    from app.booking.providers.acuity import AcuityAdapter
    from app.clinic_config import get_acuity_config

    acuity_config = get_acuity_config(clinic_id)

    provider = AcuityAdapter(
        user_id=acuity_config["user_id"],
        api_key=acuity_config["api_key"],
        clinic_id=clinic_id,
    )

    return BookingService(
        provider=provider,
        redis_client=redis_client,
        clinic_id=clinic_id,
    )
