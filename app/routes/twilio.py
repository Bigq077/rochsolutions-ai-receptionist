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
    Gather with barge-in enabled so callers can cut off the assistant.
    Tuned for short intent words like "booking" / "reschedule".
    """
    g = Gather(
        input="speech",
        action=action_url,          # MUST be absolute for reliability
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=10,                 # ✅ more time than 6s
        action_on_empty_result=True,
        barge_in=True,              # ✅ allow interrupt / cut-off
        enhanced=True,
        speech_model="phone_call",
        # ✅ Nudge recognition toward core intents / words
        hints="book,booking,appointment,availability,slot,reschedule,rescheduling,change,cancel,move my appointment,prices,cost,opening hours,location,address,insurance",
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()
    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    # Short greeting inside Gather (interruptible)
    vr.append(gather_speech(action_url, "Hi, Roch Physio speaking. How can I help today?"))

    # If they say nothing, ask again (more natural than "call again")
    vr.append(gather_speech(action_url, "Sorry — I didn’t catch that. Could you repeat?"))
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    # ✅ Debug: shows what Twilio is actually sending you (check Render logs)
    try:
        print("Twilio fields:", list(form.keys()))
        print("SpeechResult:", repr(form.get("SpeechResult")))
        print("UnstableSpeechResult:", repr(form.get("UnstableSpeechResult")))
        print("RecognitionStatus:", repr(form.get("RecognitionStatus")))
        print("Confidence:", repr(form.get("Confidence")))
    except Exception:
        pass

    call_sid = (form.get("CallSid") or "").strip()

    # ✅ IMPORTANT: read SpeechResult, but fallback to UnstableSpeechResult
    user_said = (form.get("SpeechResult") or "").strip()
    if not user_said:
        user_said = (form.get("UnstableSpeechResult") or "").strip()

    # Lazy import so /voice stays fast
    from app.flows.triage import triage_turn

    # Redis fail-safe
    try:
        session = await get_session(call_sid) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["call_sid"] = call_sid
    miss = int(session.get("miss_count", 0))

    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    try:
        # No speech detected => progressive fallbacks
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

        # Put reply inside Gather so caller can cut it off
        vr.append(gather_speech(action_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(action_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
