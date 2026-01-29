# app/routes/twilio.py
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def _normalize_public_base_url(raw: str) -> str:
    """
    Render env vars sometimes get set to a full path by mistake.
    We force PUBLIC_BASE_URL to be scheme://host (no path, no query).
    Examples:
      - https://example.com/twilio/voice  -> https://example.com
      - http://example.com               -> http://example.com
    """
    raw = (raw or "").strip()
    if not raw:
        return "https://rochsolutions-ai-receptionist.onrender.com"

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        # If someone set it like "rochsolutions-ai-receptionist.onrender.com/twilio/voice"
        # we fallback to the default host.
        return "https://rochsolutions-ai-receptionist.onrender.com"

    return f"{parts.scheme}://{parts.netloc}"


PUBLIC_BASE_URL = _normalize_public_base_url(
    os.getenv("PUBLIC_BASE_URL", "https://rochsolutions-ai-receptionist.onrender.com")
)


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    """
    Gather with barge-in enabled so callers can cut off the assistant.
    IMPORTANT: action_url MUST be absolute and must point to /twilio/turn.
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=6,
        action_on_empty_result=True,
        barge_in=True,
        enhanced=True,
        speech_model="phone_call",
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()

    # Always absolute; never derived from request.url (prevents /twilio/voice/twilio/turn bugs)
    action_url = f"{PUBLIC_BASE_URL}/twilio/turn"

    vr.append(gather_speech(action_url, "Hi, Roch Physio speaking. How can I help today?"))

    # Only plays if Gather finishes and no further verbs are appended
    vr.say("Sorry — please call again.", language="en-GB")
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    # Debug: helps you verify Twilio is actually sending SpeechResult
    # (View in Render logs)
    # print("TWILIO FORM KEYS:", list(form.keys()))
    # print("SpeechResult:", form.get("SpeechResult"))
    # print("Confidence:", form.get("Confidence"))

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    # Lazy import keeps /voice fast
    from app.flows.triage import triage_turn

    # Redis fail-safe (never kill the call because Redis blipped)
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
                vr.append(
                    gather_speech(
                        action_url,
                        "No problem. Are you looking to book, reschedule, or ask about prices or opening hours?",
                    )
                )
                return xml(vr)

            vr.append(
                gather_speech(
                    action_url,
                    "I can take a message. Please say your name, number, and what you need help with.",
                )
            )
            return xml(vr)

        # user spoke
        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)

        try:
            await save_session(call_sid, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        # Put reply inside Gather so caller can interrupt
        vr.append(gather_speech(action_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(action_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
