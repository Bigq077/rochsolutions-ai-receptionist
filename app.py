from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/")
def health():
    return "RochSolutions AI Receptionist is running."

@app.route("/twilio/voice", methods=["POST", "GET"])
def voice():
    vr = VoiceResponse()

    vr.say(
        "Hello. Welcome to Roch Solutions. "
        "Please tell me what you need help with today. "
        "For example: booking an appointment, rescheduling, or asking about prices.",
        voice="alice",
        language="en-GB",
    )

    gather = Gather(
        input="speech",
        action="/twilio/process",
        method="POST",
        language="en-GB",
        speech_timeout="auto",
    )
    gather.say("Go ahead after the beep.", voice="alice", language="en-GB")
    vr.append(gather)

    # If they say nothing, loop politely
    vr.say("Sorry, I didn't catch that. Let's try again.", voice="alice", language="en-GB")
    vr.redirect("/twilio/voice")

    return str(vr)

@app.route("/twilio/process", methods=["POST"])
def process():
    user_said = (request.form.get("SpeechResult") or "").strip()

    vr = VoiceResponse()

    if not user_said:
        vr.say("I didn't hear anything. Please try again.", voice="alice", language="en-GB")
        vr.redirect("/twilio/voice")
        return str(vr)

    # For now: simple response (we'll replace this with LLM + state machine next)
    vr.say(f"Thanks. You said: {user_said}.", voice="alice", language="en-GB")
    vr.say("This is the next question: are you a new patient or a returning patient?", voice="alice", language="en-GB")

    gather = Gather(
        input="speech",
        action="/twilio/process",
        method="POST",
        language="en-GB",
        speech_timeout="auto",
    )
    gather.say("Please say new patient or returning patient.", voice="alice", language="en-GB")
    vr.append(gather)

    vr.say("Sorry, I didn't catch that. Let's try again.", voice="alice", language="en-GB")
    vr.redirect("/twilio/voice")

    return str(vr)

if __name__ == "__main__":
    app.run()

