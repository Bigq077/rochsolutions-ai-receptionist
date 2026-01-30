# app/routes/twilio.py
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def _abs_url(request: Request, path: str) -> str:
    """
    Build a reliable absolute URL like:
    https://rochsolutions-ai-receptionist.onrender.com/twilio/turn

    Uses request.base_url so it cannot accidentally become relative
    and cannot accidentally include /twilio/voice.
    """
    base = str(request.base_url).rstrip("/")  # e.g. https://host
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    """
    Safe Gather config.
    - barge_in=True => caller can interrupt
    - timeout=10 => helps short words like "book"
    - action_on_empty_result=True => always posts even if silence
    """
    g = Gather(
        input="speech",
        action=action_url,  # MUST be absolute
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=10,
        action_on_empty_result=True,
        barge_in=True,
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


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()

    # ✅ Absolute URL for Twilio to post speech results to
    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    # 1) Gather user speech
    vr.append(gather_speech(turn_url, "Hi, Roch Physio speaking. How can I help today?"))

    # 2) If no speech / timeout: say something and loop back to /voice
    vr.say("Sorry — I didn’t catch that.", language="en-GB")
    vr.redirect(voice_url, method="POST")

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

    # SpeechResult sometimes empty; UnstableSpeechResult often contains the transcript
    user_said = (form.get("SpeechResult") or "").strip()
    if not user_said:
        user_said = (form.get("UnstableSpeechResult") or "").strip()

    # Lazy import so startup stays fast
    from app.flows.triage import triage_turn

    # Redis: fail-safe so Redis blip doesn't kill the call
    try:
        session = await get_session(call_sid) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["call_sid"] = call_sid
    miss = int(session.get("miss_count", 0))

    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

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
                vr.append(gather_speech(turn_url, "Sorry — I didn’t catch that. Could you repeat?"))
                return xml(vr)

            if miss == 2:
                vr.append(
                    gather_speech(
                        turn_url,
                        "No problem. Are you looking to book, reschedule, or ask about prices, location, or opening hours?",
                    )
                )
                return xml(vr)

            # 3rd miss: offer message then loop
            vr.append(gather_speech(turn_url, "I can take a message. Please say your name, number, and what you need help with."))
            vr.say("If you prefer, you can call back at any time.", language="en-GB")
            vr.redirect(voice_url, method="POST")
            return xml(vr)

        # user spoke
        session["miss_count"] = 0

        session = _append_turn(session, "caller", user_said)
        reply_text, session = await triage_turn(user_said, session)
        session = _append_turn(session, "assistant", reply_text)

        try:
            await save_session(call_sid, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        # Reply inside Gather so caller can cut it off
        vr.append(gather_speech(turn_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(turn_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
