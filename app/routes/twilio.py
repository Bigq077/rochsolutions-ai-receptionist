# app/routes/twilio.py
from __future__ import annotations

import os
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://rochsolutions-ai-receptionist.onrender.com",
).rstrip("/")


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    """
    Safe Gather config (works with Twilio helper versions reliably).
    - barge_in=True => caller can interrupt
    - timeout=10 => fixes short intent words being missed
    - action_on_empty_result=True => always hits /turn even if no speech
    """
    g = Gather(
        input="speech",
        action=action_url,                # absolute URL
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=10,                       # ✅ more time than 6s
        action_on_empty_result=True,
        barge_in=True,
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()
    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    # Put greeting inside Gather so it can be interrupted
    vr.append(gather_speech(action_url, "Hi, Roch Physio speaking. How can I help today?"))

    # If they say nothing, ask again (keeps call alive)
    vr.append(gather_speech(action_url, "Sorry — I didn’t catch that. Could you repeat?"))
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    # ✅ Debug: check Render logs to see what Twilio is sending
    try:
        print("---- TWILIO TURN ----")
        print("keys:", list(form.keys()))
        print("CallSid:", form.get("CallSid"))
        print("SpeechResult:", repr(form.get("SpeechResult")))
        print("UnstableSpeechResult:", repr(form.get("UnstableSpeechResult")))
        print("Confidence:", repr(form.get("Confidence")))
        print("RecognitionStatus:", repr(form.get("RecognitionStatus")))
    except Exception:
        pass

    call_sid = (form.get("CallSid") or "").strip()

    # ✅ SpeechResult sometimes empty; UnstableSpeechResult often contains the transcript
    user_said = (form.get("SpeechResult") or "").strip()
    if not user_said:
        user_said = (form.get("UnstableSpeechResult") or "").strip()

    # Lazy import so /voice stays fast
    from app.flows.triage import triage_turn

    # Redis: fail-safe so Redis blip doesn't kill the call
    try:
        session = await get_session(call_sid) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["call_sid"] = call_sid
    miss = int(session.get("miss_count", 0))

    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    try:
        # No speech => progressive fallbacks
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            try:
                await save_session(call_sid, session)
            except Exception as e:
                print("Redis save_session error:", repr(e))

            if miss == 1:
                vr.append(gather_speech(action_url, "Sorry — I didn’t catch that. Could you repeat?"))
                return xml(vr)

            if miss == 2:
                vr.append(
                    gather_speech(
                        action_url,
                        "No problem. Are you looking to book, reschedule, or ask about prices, location, or opening hours?",
                    )
                )
                return xml(vr)

            vr.append(gather_speech(action_url, "I can take a message. Please say your name, number, and what you need help with."))
            return xml(vr)

        # user spoke
        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)

        try:
            await save_session(call_sid, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        # Reply inside Gather so caller can cut it off
        vr.append(gather_speech(action_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(action_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
