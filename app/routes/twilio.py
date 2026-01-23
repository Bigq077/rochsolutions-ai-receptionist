from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.flows.triage import triage_turn
from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def build_gather(action_url: str, prompt: str | None = None) -> Gather:
    """
    A single, consistent Gather that feels natural:
    - bargeIn enabled: user can speak while the bot is talking
    - action_on_empty_result: Twilio will hit /turn even if silence
    - no 'what next' line
    """
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        action_on_empty_result=True,
        barge_in=True,  # IMPORTANT: makes it feel human
    )
    if prompt:
        g.say(prompt, language="en-GB")
    else:
        g.pause(length=1)
    return g


@router.api_route("/voice", methods=["GET", "POST"])
async def voice(request: Request):
    """
    Entry point: give a clean, one-time capability statement then gather.
    No loops, no re-using Gather objects.
    """
    vr = VoiceResponse()
    action_url = str(request.url_for("turn"))

    vr.say("Hello, welcome to Roch Solutions.", language="en-GB")
    vr.say(
        "I can help with booking, rescheduling, prices, opening hours, location, insurance, or taking a message.",
        language="en-GB",
    )

    vr.append(build_gather(action_url, "How can I help today?"))
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    """
    Main loop:
    - If silence: progressive fallback (repeat -> simplified menu -> take message)
    - If speech: pass to triage, then gather again
    - Never hard reset to /voice unless something is broken
    """
    vr = VoiceResponse()
    form = await request.form()

    call_sid = (form.get("CallSid") or "").strip()
    user_said = (form.get("SpeechResult") or "").strip()

    action_url = str(request.url_for("turn"))

    # Load session (safe)
    session = await get_session(call_sid) or {}
    miss = int(session.get("miss_count", 0))

    try:
        # -------------- SILENCE / EMPTY SPEECH --------------
        if not user_said:
            miss += 1
            session["miss_count"] = miss
            await save_session(call_sid, session)

            if miss == 1:
                vr.say("Sorry — I didn’t catch that. Could you repeat?", language="en-GB")
                vr.append(build_gather(action_url))
                return xml(vr)

            if miss == 2:
                vr.say("No problem.", language="en-GB")
                vr.append(
                    build_gather(
                        action_url,
                        "Are you looking to book, reschedule, check prices, or opening hours?",
                    )
                )
                return xml(vr)

            # 3+ times: move to message capture prompt (still via triage)
            vr.say("That’s okay.", language="en-GB")
            vr.append(
                build_gather(
                    action_url,
                    "If you’d like, say your name, your phone number, and what you need help with, and we’ll call you back.",
                )
            )
            return xml(vr)

        # -------------- USER SPOKE --------------
        session["miss_count"] = 0

        # Run triage
        reply_text, session = await triage_turn(user_said, session)

        # Save updated session
        await save_session(call_sid, session)

        # Speak reply + keep conversation open
        vr.say(reply_text, language="en-GB")
        vr.append(build_gather(action_url))
        return xml(vr)

    except Exception as e:
        # Log error
        print("ERROR in /twilio/turn:", repr(e))

        # Soft recovery: do NOT redirect to /voice (that resets conversation)
        vr.say("Sorry — there was a technical issue. Please say that again.", language="en-GB")
        vr.append(build_gather(action_url))
        return xml(vr)

