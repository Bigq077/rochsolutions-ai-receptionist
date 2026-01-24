# app/routes/twilio.py
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def _gather(action_url: str, prompt: str | None = None) -> Gather:
    """
    Single helper to avoid duplicated gathers and repeated menus.
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=5,
        action_on_empty_result=True,
        barge_in=True,  # user can interrupt the bot
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()
    action_url = str(request.url_for("turn"))

    # short, human intro (no huge list)
    vr.say("Hi, you’re through to Roch Physio.", language="en-GB")
    vr.append(_gather(action_url, "How can I help?"))
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()
    action_url = str(request.url_for("turn"))

    session = await get_session(call_sid) or {}
    miss = int(session.get("miss_count", 0))

    try:
        # Silence / no speech -> progressive fallbacks (no resets)
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            await save_session(call_sid, session)

            if miss == 1:
                vr.say("Sorry — I didn’t catch that.", language="en-GB")
                vr.append(_gather(action_url, "Could you repeat that?"))
                return xml(vr)

            if miss == 2:
                vr.say("No problem.", language="en-GB")
                vr.append(_gather(action_url, "Are you looking to book, reschedule, or ask about prices or opening hours?"))
                return xml(vr)

            # 3+ misses: steer to callback capture
            vr.say("I can take a message and have the clinic call you back.", language="en-GB")
            vr.append(_gather(action_url, "Please say your name, number, and what you need help with."))
            return xml(vr)

        # User spoke -> reset miss counter
        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)
        await save_session(call_sid, session)

        vr.say(reply_text, language="en-GB")
        vr.append(_gather(action_url))  # no “what next”
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.say("Sorry — something went wrong. Please try again.", language="en-GB")
        vr.append(_gather(action_url, "How can I help?"))
        return xml(vr)


