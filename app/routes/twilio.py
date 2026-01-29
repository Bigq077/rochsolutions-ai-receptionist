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
    Gather with barge-in enabled so callers can cut off the assistant.
    """
    g = Gather(
        input="speech",
        action=action_url,   # MUST be absolute for Twilio reliability
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,
        barge_in=True,
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()

    # Absolute URL so Twilio never deals with relative paths
    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    # Short greeting inside Gather (interruptible)
    vr.append(gather_speech(action_url, "Hi, Roch Physio speaking. How can I help today?"))

    # If user says nothing, Twilio will continue after timeout:
    vr.say("Sorry — please call again.", language="en-GB")
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    # Important: lazy import so /voice stays fast
    from app.flows.triage import triage_turn

    # Redis: fail-safe so a Redis blip doesn't kill the call
    try:
        session = await get_session(call_sid) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["call_sid"] = call_sid
    miss = int(session.get("miss_count", 0))

    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    try:
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
                vr.append(gather_speech(action_url, "No problem. Are you looking to book, reschedule, or ask about prices or opening hours?"))
                return xml(vr)

            vr.append(gather_speech(action_url, "I can take a message. Please say your name, number, and what you need help with."))
            return xml(vr)

        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)

        try:
            await save_session(call_sid, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        vr.append(gather_speech(action_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(action_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
