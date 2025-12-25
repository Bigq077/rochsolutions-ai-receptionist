from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__)

@app.route("/twilio/voice", methods=["POST", "GET"])
def voice():
    response = VoiceResponse()
    response.say(
        "Hello. You have reached Roch Solutions. "
        "This is our AI receptionist for physiotherapy clinics. "
        "We are currently setting things up. Please call back shortly.",
        voice="alice",
        language="en-GB"
    )
    return str(response)

@app.route("/")
def health():
    return "RochSolutions AI Receptionist is running."

if __name__ == "__main__":
    app.run()
