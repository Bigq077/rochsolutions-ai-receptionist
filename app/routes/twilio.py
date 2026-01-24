from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    """
    Gather with barge-in enabled so callers can cut off the assistant.
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,
        barge_in=True,  # ✅ allow interrupt / cut-off
    )
    if prompt:
        g.say(prompt, language="en-GB")
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    vr = VoiceResponse()
    action_url = str(request.url_for("turn"))

    # Keep greeting short, inside Gather so it can be interrupted.
    vr.append(
        gather_speech(
            action_url,
            "Hi, Roch Physio speaking. How can I help today?",
        )
    )
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    session = await get_session(call_sid) or {}
    session["call_sid"] = call_sid  # ✅ ensure available for sheet handoff, logging, etc.
    miss = int(session.get("miss_count", 0))

    action_url = str(request.url_for("turn"))

    try:
        # No speech => progressive short fallbacks (no reset)
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            await save_session(call_sid, session)

            if miss == 1:
                vr.append(gather_speech(action_url, "Sorry — I didn’t catch that. Could you repeat?"))
                return xml(vr)

            if miss == 2:
                vr.append(gather_speech(action_url, "No problem. Are you looking to book, reschedule, or ask about prices or opening hours?"))
                return xml(vr)

            vr.append(gather_speech(action_url, "I can take a message. Please say your name, number, and what you need help with."))
            return xml(vr)

        # user spoke
        session["miss_count"] = 0

        reply_text, session = await triage_turn(user_said, session)
        await save_session(call_sid, session)

        # Put the bot reply inside Gather so the caller can cut it off
        vr.append(gather_speech(action_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(action_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)



