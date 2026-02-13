# app/routes/twilio.py
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.booking import BookingService
from app.booking.providers import AcuityAdapter
from app.booking.exceptions import BookingError
from app.clinic_config import get_acuity_config
from app.storage.redis_store import get_session, save_session
from app.clinic_config import clinic_id_from_twilio_to, get_clinic

router = APIRouter(prefix="/twilio")


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

async def create_booking_service_for_clinic(
    clinic_id: str,
    redis_client,
) -> BookingService:
    """
    Factory to create BookingService with Acuity provider.
    
    Args:
        clinic_id: Clinic identifier (e.g., "theorem")
        redis_client: Your existing Redis client
    
    Returns:
        Configured BookingService instance
    """
    # Get Acuity credentials from clinic_config
    acuity_config = get_acuity_config(clinic_id)
    
    # Create Acuity provider
    provider = AcuityAdapter(
        user_id=acuity_config["user_id"],
        api_key=acuity_config["api_key"],
        clinic_id=clinic_id,
    )
    
    # Create and return BookingService
    return BookingService(
        provider=provider,
        redis_client=redis_client,
        clinic_id=clinic_id,
    )
def attach_status_callback(vr: VoiceResponse, request: Request) -> None:
    vr.status_callback = _abs_url(request, "/twilio/status")
    vr.status_callback_method = "POST"


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    g = Gather(
        input="speech dtmf",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=10,
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
    """
    Production behavior: resolve clinic_id from Twilio 'To' number.
    For testing Theorem on your demo number, we override clinic_id below.
    """
    if not session.get("clinic_id"):
        session["clinic_id"] = clinic_id_from_twilio_to(to_number)
    return session


# =========================================================
# Call status webhook (end-of-call logging)
# =========================================================
@router.post("/status")
async def status(request: Request) -> PlainTextResponse:
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    call_status = (form.get("CallStatus") or "").strip().lower()

    # Log only terminal statuses
    if call_status not in (
        "completed",
        "busy",
        "failed",
        "no-answer",
        "no_answer",
        "canceled",
        "cancelled",
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

    # Load session
    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    # prevent duplicates
    if session.get("call_summary_logged"):
        return PlainTextResponse("already logged")

    # Attach Twilio end-of-call metadata
    ended_at = datetime.utcnow().isoformat() + "Z"
    session["call_sid"] = call_sid
    session["call_status"] = call_status
    session["ended_at_utc"] = ended_at

    session["twilio_from"] = (form.get("From") or "").strip()
    session["twilio_to"] = (form.get("To") or "").strip()
    session["twilio_direction"] = (form.get("Direction") or "").strip()
    session["twilio_duration_sec"] = (form.get("CallDuration") or "").strip()
    session["twilio_timestamp"] = (form.get("Timestamp") or "").strip()

    # Keep the full payload for debugging
    try:
        session["twilio_status_payload"] = {k: str(v) for k, v in form.items()}
    except Exception:
        session["twilio_status_payload"] = {}

    # Ensure clinic_id exists even if voice/turn never saved it
    session = _ensure_clinic_on_session(session, session.get("twilio_to"))

    # 🔴 TEMP TEST OVERRIDE (REMOVE AFTER TESTING)
    # Force Theorem on your existing Twilio demo number
    session["clinic_id"] = "theorem"

    # Build summary + write to sheets
    try:
        summary = build_call_summary(session)
        row = summary_to_sheet_row(summary)

        if append_summary_row(row):
            session["call_summary_logged"] = True
            await save_session(call_sid, session)
    except Exception as e:
        print("CALL SUMMARY ERROR:", repr(e))

    return PlainTextResponse("ok")


# =========================================================
# MAIN VOICE ENTRYPOINT
# =========================================================
@router.api_route("/voice", methods=["GET", "POST", "HEAD"])
async def voice(request: Request):
    if request.method in ("HEAD", "GET"):
        return Response(status_code=200)

    form = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    to_number = (form.get("To") or "").strip() or None

    # Load session
    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    session["call_sid"] = call_sid
    session = _ensure_clinic_on_session(session, to_number)

    # 🔴 TEMP TEST OVERRIDE (REMOVE AFTER TESTING)
    # Force Theorem on your existing Twilio demo number
    session["clinic_id"] = "theorem"

    await save_session(call_sid, session)

    clinic = get_clinic(session.get("clinic_id"))
    clinic_name = clinic.get("display_name", "the clinic")

    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    start_text = f"Hi, {clinic_name} speaking. How can I help today?"

    # Always attach StatusCallback so summaries fire
    attach_status_callback(vr, request)

    try:
        from app.routes.tts_eleven import tts_eleven_url, TTSReq

        data = tts_eleven_url(TTSReq(text=start_text), request)
        audio_url = data["audio_url"]
        vr.play(audio_url)
        vr.append(gather_speech(turn_url))
    except Exception as e:
        print("VOICE ELEVEN ERROR:", repr(e))
        vr.append(gather_speech(turn_url, start_text))

    vr.redirect(voice_url, method="POST")
    return xml(vr)


# =========================================================
# TURN HANDLER
# =========================================================
@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    to_number = (form.get("To") or "").strip() or None

    digits = (form.get("Digits") or "").strip() or None
    speech = (form.get("SpeechResult") or "").strip()
    unstable = (form.get("UnstableSpeechResult") or "").strip()

    user_said = digits if digits else (speech or unstable)

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

    session["call_sid"] = call_sid
    session = _ensure_clinic_on_session(session, to_number)

    # 🔴 TEMP TEST OVERRIDE (REMOVE AFTER TESTING)
    # Force Theorem on your existing Twilio demo number
    session["clinic_id"] = session.get("clinic_id") or "theorem"
    session["clinic_id"] = "theorem"

    miss = int(session.get("miss_count", 0))

    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    if not user_said:
        miss += 1
        session["miss_count"] = miss
        await save_session(call_sid, session)

        if miss == 1:
            vr.append(gather_speech(turn_url, "Sorry — I didn’t catch that. Could you repeat?"))
            return xml(vr)

        if miss == 2:
            vr.append(
                gather_speech(
                    turn_url,
                    "Are you looking to book, reschedule, or ask about prices, location, or opening hours?",
                )
            )
            return xml(vr)

        vr.append(
            gather_speech(
                turn_url,
                "I can take a message. Please say your name, number, and what you need help with.",
            )
        )
        vr.redirect(voice_url, method="POST")
        return xml(vr)

    session["miss_count"] = 0
    session = _append_turn(session, "caller", user_said)

    try:
        # triage_turn can read session["clinic_id"] and pick the right clinic internally
        reply_text, session = await triage_turn(user_said, session)
    except Exception as e:
        print("TRIAGE ERROR:", repr(e))
        vr.append(gather_speech(turn_url, "Sorry, something went wrong. Please try again."))
        return xml(vr)

    session = _append_turn(session, "assistant", reply_text)
    await save_session(call_sid, session)

    vr.append(gather_speech(turn_url, reply_text))
    return xml(vr)
