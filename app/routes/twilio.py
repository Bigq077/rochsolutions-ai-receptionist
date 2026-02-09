# app/routes/twilio.py
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session

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


# =========================================================
# Call status webhook (end-of-call logging)
# =========================================================
@router.post("/status")
async def status(request: Request) -> PlainTextResponse:
    form = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    call_status = (form.get("CallStatus") or "").strip().lower()

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

    # Optional imports (safe)
    try:
        from app.call_summary import build_call_summary, summary_to_sheet_row
        from app.tools.handoff import append_summary_row
    except Exception:
        return PlainTextResponse("ok")

    try:
        session = await get_session(call_sid) or {}
    except Exception:
        session = {}

    if session.get("call_summary_logged"):
        return PlainTextResponse("already logged")

    session["call_sid"] = call_sid
    session["call_status"] = call_status
    session["ended_at_utc"] = datetime.utcnow().isoformat() + "Z"

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

    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    start_text = "Hi, Roch Physio speaking. How can I help today?"

    # ✅ Use the working ElevenLabs->MP3 URL generator you already have
    try:
        # IMPORTANT: adjust this import if your avatar router is not app/routes/avatar.py
        from app.routes.avatar import tts_eleven_url, TTSReq

        data = tts_eleven_url(TTSReq(text=start_text), request)
        audio_url = data["audio_url"]  # https://.../avatar/audio/<uuid>.mp3

        vr.play(audio_url)
        vr.append(gather_speech(turn_url))

    except Exception as e:
        print("VOICE ELEVEN ERROR:", repr(e))
        # fallback: Twilio TTS (never break the call)
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
        reply_text, session = await triage_turn(user_said, session)
    except Exception as e:
        print("TRIAGE ERROR:", repr(e))
        vr.append(gather_speech(turn_url, "Sorry, something went wrong. Please try again."))
        return xml(vr)

    session = _append_turn(session, "assistant", reply_text)
    await save_session(call_sid, session)

    vr.append(gather_speech(turn_url, reply_text))
    return xml(vr)
