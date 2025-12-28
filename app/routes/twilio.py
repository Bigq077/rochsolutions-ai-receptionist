from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from app.config import BASE_URL
from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")

def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")

@router.api_route("/voice", methods=["GET", "POST"])
async def voice(_: Request):
    vr = VoiceResponse()
    vr.say("Hello, welcome to Roch Solutions.", language="en-GB")

    g = Gather(
        input="speech",
        action="/twilio/turn",
        method="POST",
        language="en-GB",
        speech_timeout="auto",
    )
    g.say("How can I help? You can say booking, rescheduling, prices, or opening hours.", language="en-GB")
    vr.append(g)

    vr.say("Sorry, I didn't catch that. Let's try again.", language="en-GB")
    vr.redirect("/twilio/voice")
    return xml(vr)

@router.api_route("/turn", methods=["POST"])
async def turn(request: Request):
    form = await request.form()
    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    # Load session (memory). If Redis not set yet, it still works.
    session = await get_session(call_sid)

    # Run triage logic (final product will expand this)
   reply_text, session = await triage_turn(user_said, session)

    # Save session back
    await save_session(call_sid, session)

    vr = VoiceResponse()
    vr.say(reply_text, language="en-GB")

    # Keep the conversation going
    g = Gather(
        input="speech",
        action="/twilio/turn",
        method="POST",
        language="en-GB",
        speech_timeout="auto",
    )
    g.say("What would you like to do next?", language="en-GB")
    vr.append(g)

    vr.say("Sorry, I didn't catch that. Let's try again.", language="en-GB")
    vr.redirect("/twilio/voice")
    return xml(vr)

