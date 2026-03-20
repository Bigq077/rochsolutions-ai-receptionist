from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path

# Susie's demo number to call
# +447426779875 → /ms/incoming (Media Streams WebSocket pipeline)
# +447367002651 → /twilio/voice (legacy pipeline)
SUSIE_NUMBER = "+447426779875"

# Twilio credentials — read from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_TEST_NUMBER = os.getenv("TWILIO_TEST_NUMBER")
# This is the number that calls Susie
# Must be a Twilio number on your account

# Anthropic — for simulator and evaluator
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# AssemblyAI — for transcribing call recordings after the call ends
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

# Test settings
MAX_CALL_DURATION_SECONDS = 480  # 8 minutes — allows multiple 120s empty-gather retries
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

# Render server URL — used for warmup ping before running tests
RENDER_SERVER_URL = os.getenv(
    "RENDER_SERVER_URL",
    "https://rochsolutions-ai-receptionist.onrender.com",
)

# Ensure results directory exists
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
