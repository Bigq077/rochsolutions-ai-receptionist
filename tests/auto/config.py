from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path

# Susie's demo number to call
SUSIE_NUMBER = "+447425779875"

# Twilio credentials — read from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_TEST_NUMBER = os.getenv("TWILIO_TEST_NUMBER")
# This is the number that calls Susie
# Must be a Twilio number on your account

# Anthropic — for simulator and evaluator
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Test settings
MAX_CALL_DURATION_SECONDS = 180
TURN_TIMEOUT_SECONDS = 15
SILENCE_BETWEEN_TURNS_MS = 500
MAX_TURNS_PER_CALL = 20

# TTS for playing patient responses during call
# Uses ElevenLabs to generate realistic speech
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_PATIENT_VOICE_ID = os.getenv(
    "ELEVENLABS_PATIENT_VOICE_ID",
    "21m00Tcm4TlvDq8ikWAM"  # default neutral voice
)

# Results directory
RESULTS_DIR = Path(__file__).parent / "results"

# Pass criteria
MIN_PASS_RATE = 0.97  # 97% to be clinic ready

# Ensure results directory exists
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
