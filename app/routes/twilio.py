# routes/twilio.py
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def gather_speech(action_url: str, prompt: str) -> Gather:
    """
    Centralised Gather config so behaviour is consistent everywhere.
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,  # IMPORTANT: call action even if user says nothing
    )
    g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()

    # Start / welcome (say the full menu ONCE here)
    vr.say("Hello, welcome to Roch Solutions.", language="en-GB")
    vr.say(
        "I can help you book an appointment, reschedule, check prices, opening hours, location, insurance, or take a message.",
        language="en-GB",
    )

    # Start conversation turn
    action_url = str(request.url_for("turn"))
    vr.append(gather_speech(action_url, "How can I help today?"))

    # If Twilio still doesn't get speech (rare because action_on_empty_result),
    # do a short retry WITHOUT repeating the full menu.
    vr.say("Sorry — I didn’t catch that. Please say it again.", language="en-GB")
    vr.append(gather_speech(action_url, "You can say: book, reschedule, prices, or opening hours."))

    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    # Load session
    session = await get_session(call_sid) or {}

    # Track misunderstandings at the Twilio layer so we can do short fallbacks
    miss = int(session.get("miss_count", 0))

    action_url = str(request.url_for("turn"))

    try:
        # If user said nothing, do progressive short fallbacks WITHOUT resetting
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            await save_session(call_sid, session)

            if miss == 1:
                vr.say("Sorry — I didn’t catch that. Could you repeat?", language="en-GB")
                vr.append(gather_speech(action_url, "Just tell me what you need. For example: book an appointment."))
                return xml(vr)

            if miss == 2:
                vr.say("No problem.", language="en-GB")
                vr.append(gather_speech(action_url, "Are you looking to book, reschedule, or get prices or opening hours?"))
                return xml(vr)

            # 3rd time: go to message capture (demo-safe)
            vr.say("I can take a message and have the clinic call you back.", language="en-GB")
            vr.append(gather_speech(action_url, "Please say your name and phone number."))
            return xml(vr)

        # User spoke -> reset miss counter
        session["miss_count"] = 0

        # Run triage logic
        reply_text, session = await triage_turn(user_said, session)

        # Save session
        await save_session(call_sid, session)

        # Say the assistant reply
        vr.say(reply_text, language="en-GB")

        # Keep conversation going, but DO NOT auto-say "what next" every time.
        # Use a neutral short continuation prompt.
        vr.append(gather_speech(action_url, "Anything else I can help with?"))

        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))

        # Still return valid TwiML
        vr.say("Sorry, there was a technical issue. Please try again.", language="en-GB")
        vr.append(gather_speech(action_url, "How can I help?"))
        return xml(vr)

