# app/routes/twilio.py
from __future__ import annotations

import re

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.storage.redis_store import get_session, save_session

router = APIRouter(prefix="/twilio")


def xml(resp: VoiceResponse) -> PlainTextResponse:
    return PlainTextResponse(str(resp), media_type="application/xml")


def _abs_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def gather_speech(action_url: str, prompt: str | None = None) -> Gather:
    """
    Speech gather with barge-in enabled.
    Use speech + dtmf so callers can say "one" OR press 1.
    """
    g = Gather(
        input="speech dtmf",
        action=action_url,
        method="POST",
        language="en-GB",
        speech_timeout="auto",
        timeout=10,
        action_on_empty_result=True,
        barge_in=True,
        num_digits=1,
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


@router.api_route("/voice", methods=["GET", "POST", "HEAD"])
async def voice(request: Request):
    """
    Twilio will POST here, but probes/monitors may HEAD/GET.
    Return 200 for HEAD/GET to avoid 405 spam.
    """
    if request.method in ("HEAD", "GET"):
        return Response(status_code=200)

    vr = VoiceResponse()
    turn_url = _abs_url(request, "/twilio/turn")
    voice_url = _abs_url(request, "/twilio/voice")

    vr.append(gather_speech(turn_url, "Hi, Roch Physio speaking. How can I help today?"))

    vr.say("Sorry — I didn’t catch that.", language="en-GB")
    vr.redirect(voice_url, method="POST")
    return xml(vr)


@router.api_route("/turn", methods=["POST"], name="turn")
async def turn(request: Request):
    vr = VoiceResponse()
    form = await request.form()

    # --- Debug ---
    try:
        print("---- TWILIO TURN ----")
        print("keys:", list(form.keys()))
        print("CallSid:", form.get("CallSid"))
        print("SpeechResult:", repr(form.get("SpeechResult")))
        print("UnstableSpeechResult:", repr(form.get("UnstableSpeechResult")))
        print("Digits:", repr(form.get("Digits")))
        print("msg:", repr(form.get("msg")))
        print("Confidence:", repr(form.get("Confidence")))
        print("RecognitionStatus:", repr(form.get("RecognitionStatus")))
    except Exception:
        pass

    call_sid = (form.get("CallSid") or "").strip()

    # -----------------------------
    # Normalize user input
    # -----------------------------
    digits_raw = form.get("Digits")
    digits = (digits_raw or "").strip()
    if digits == "":
        digits = None

    speech = (form.get("SpeechResult") or "").strip()
    unstable = (form.get("UnstableSpeechResult") or "").strip()

    if digits:
        user_said = digits
    else:
        user_said = speech or unstable

    # Normalize yes / no with punctuation (e.g. "Yes.")
    if user_said:
        t = user_said.strip().lower()
        t = re.sub(r"\s+", " ", t)

        if re.fullmatch(r"(yes|yeah|yep|yup|ok|okay|confirm|correct)[\W_]*", t):
            user_said = "yes"
        elif re.fullmatch(r"(no|nope|nah|cancel|stop|dont|do not|nevermind|never mind)[\W_]*", t):
            user_said = "no"

    from app.flows.triage import triage_turn

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
                        "Are you looking to book, reschedule, or ask about prices, location, or opening hours?",
                    )
                )
                return xml(vr)

            vr.append(
                gather_speech(
                    turn_url,
                    "I can take a message. Please say your name, number, and what you need help with.",
                )
            )
            vr.redirect(voice_url, method="POST")
            return xml(vr)

        session["miss_count"] = 0
        session = _append_turn(session, "caller", user_said)

        reply_text, session = await triage_turn(user_said, session)

        session = _append_turn(session, "assistant", reply_text)
        try:
            await save_session(call_sid, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        vr.append(gather_speech(turn_url, reply_text))
        return xml(vr)

    except Exception as e:
        print("ERROR in /twilio/turn:", repr(e))
        vr.append(gather_speech(turn_url, "Sorry, there was a technical issue. Please try again."))
        return xml(vr)
