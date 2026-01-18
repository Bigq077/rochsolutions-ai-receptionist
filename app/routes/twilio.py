from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def gather_speech(action_url: str) -> Gather:
    """
    Gather without a 'what next' style sentence.
    We add a short pause so it doesn't feel abrupt.
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,
    )
    g.pause(length=1)
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()
    vr.say("Hello, welcome to Roch Solutions.", language="en-GB")
    vr.say(
        "I can help you book an appointment, reschedule, check prices, opening hours, location, insurance, or take a message.",
        language="en-GB",
    )

    action_url = str(request.url_for("turn"))
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,
    )
    g.say("How can I help today?", language="en-GB")
    vr.append(g)

    vr.say("Sorry — I didn’t catch that. Please say it again.", language="en-GB")
    vr.append(gather_speech(action_url))
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    session = await get_session(call_sid) or {}
    miss = int(session.get("miss_count", 0))
    action_url = str(request.url_for("turn"))

    try:
        # No speech => progressive short fallbacks, no reset
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            await save_session(call_sid, session)

            if miss == 1:
                vr.say("Sorry — I didn’t catch that. Could you repeat?", language="en-GB")
                vr.append(gather_speech(action_url))
                return xml(vr)

            if miss == 2:
                vr.say("No problem.", language="en-GB")
                g = Gather(
                    input="speech",
                    action=action_url,
                    method="POST",
                    language="en-GB",
                    speech_timeout="auto",
                    action_on_empty_result=True,
                )
                g.say("Are you looking to book, reschedule, or get prices or opening hours?", language="en-GB")
                vr.append(g)
                return xml(vr)

            vr.say("I can take a message and have the clinic call you back.", language="en-GB")
            g = Gather(
                input="speech",
                action=action_url,
                method="POST",
                language="en-GB",
                speech_timeout="auto",
                action_on_empty_result=True,
            )
            g.say("Please say your name, number, and what you need help with.", language="en-GB")
            vr.append(g)
            return xml(vr)

        # user spoke
        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)
        await save_session(call_sid, session)

        vr.say(reply_text, language="en-GB")
        vr.append(gather_speech(action_url))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.say("Sorry, there was a technical issue. Please try again.", language="en-GB")
        vr.append(gather_speech(action_url))
        return xml(vr)
