

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

app = FastAPI()

BASE_URL = "https://rochsolutions-ai-receptionist.onrender.com"

# ---------- Health check ----------
@app.get("/")
def health():
    return {"status": "RochSolutions AI Receptionist running"}

# ---------- Twilio entrypoint ----------
@app.api_route("/twilio/voice", methods=["GET", "POST"])
def twilio_voice(request: Request):
    vr = VoiceResponse()

    vr.say(
        "Hello. Welcome to Roch Solutions. "
        "Please tell me what you need help with today. "
        "For example, booking an appointment, rescheduling, or asking about prices.",
        language="en-GB",
    )

    gather = Gather(
        input="speech",
        action=f"{BASE_URL}/twilio/turn",
        method="POST",
        language="en-GB",
        speech_timeout="auto",
    )
    gather.say("Go ahead.", language="en-GB")
    vr.append(gather)

    vr.say("Sorry, I didn't catch that. Let's try again.")
    vr.redirect(f"{BASE_URL}/twilio/voice")

    return PlainTextResponse(str(vr), media_type="application/xml")


# ---------- Conversation turn ----------
@app.api_route("/twilio/turn", methods=["POST"])
async def twilio_turn(request: Request):
    form = await request.form()
    user_said = (form.get("SpeechResult") or "").strip()

    vr = VoiceResponse()

    if not user_said:
        vr.say("I didn't hear anything. Please try again.", language="en-GB")
        vr.redirect(f"{BASE_URL}/twilio/voice")
        return PlainTextResponse(str(vr), media_type="application/xml")

    vr.say(f"Thanks. You said: {user_said}.", language="en-GB")
    vr.say(
        "This is where the intelligent booking logic will run next.",
        language="en-GB",
    )

    vr.redirect(f"{BASE_URL}/twilio/voice")
    return PlainTextResponse(str(vr), media_type="application/xml")
