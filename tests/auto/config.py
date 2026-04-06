from dotenv import load_dotenv
from pathlib import Path as _Path
# Load test-specific .env first (tests/auto/.env), then fall back to project root .env
# override=True ensures values from .env always win over existing shell env vars
load_dotenv(dotenv_path=_Path(__file__).parent / ".env", override=True)
load_dotenv(override=False)  # project root fallback (don't override test .env)

import os
from pathlib import Path

# Susie's demo number to call
# +447426779875 → /ms/incoming (Media Streams WebSocket pipeline) — live Theorem number
# +447367002651 → /twilio/voice (legacy pipeline)
# +447366530580 → theorem_v2 test line (two-clinic guards active)
SUSIE_NUMBER = "+447426779875"  # theorem — Phase 1-14 (single-location, no clinic question)

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

# Direct WebSocket mode — bypasses Twilio calls entirely (no cost).
# Default: True (free). Set USE_DIRECT_WS=false or pass --real-calls to use real Twilio calls.
USE_DIRECT_WS = os.getenv("USE_DIRECT_WS", "true").lower() != "false"
