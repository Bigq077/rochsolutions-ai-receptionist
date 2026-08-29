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
# The dialled number is what selects the CLINIC: call_runner sends it as "to" in
# the direct-WS start payload, and the service resolves its clinic from it. There
# are no per-scenario `twilio_to` overrides left, so this one value decides which
# clinic all 118 scenarios talk to.
#
# It used to be Theorem's live number. Against the demo service that resolved to
# `vital_edge` — a LIVE clinic — which is exactly how a booking scenario could
# reach a real practitioner's calendar. Default it to the demo line instead, and
# make it env-overridable so a run can be pointed at another clinic deliberately
# rather than by inheriting a stale constant.
#
# 2026-08-29: this line now resolves to `northgate`, the demo tenant, NOT jv_v1.
# That matters more than it used to — canonical's jv_v1 now carries Joint
# Venture's REAL calendar rather than a demo repoint, so a suite pointed at it
# would book into a live practitioner's diary. tests/tenancy asserts this
# default never resolves to a live patient clinic; override SUSIE_NUMBER only
# with your eyes open.
SUSIE_NUMBER = os.getenv("SUSIE_NUMBER", "+447366263180")  # demo line -> northgate

# Clinic identity the greeting assertions check. Was hardcoded to "theorem
# health" in two scenarios, which failed on every other clinic and looked like a
# Susie bug rather than a pinned test.
EXPECTED_CLINIC_NAME = os.getenv("EXPECTED_CLINIC_NAME", "northgate")
# Names that must NOT appear — another clinic's identity leaking into a greeting
# is a real defect, so this stays an assertion, just not a Theorem-only one.
FOREIGN_CLINIC_TERMS = [
    t.strip() for t in os.getenv(
        "FOREIGN_CLINIC_TERMS", "alcester,redditch,say one,say two",
    ).split(",") if t.strip()
]

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


# ── HARD SAFETY GATE: never drive a non-demo target ──────────────────────────
# This harness drives a full conversation against a REAL deployed service, which
# books into THAT service's Acuity calendar — in BOTH direct-WS and --real-calls
# modes. Left ungated it will create real appointments in a real practitioner's
# calendar (it does not cancel them). To make that impossible except against an
# explicitly-declared demo target, the runner refuses to contact any target
# unless BOTH of the following are set:
#   1. RUN_LIVE_CALL_TESTS=1                      (explicit opt-in), and
#   2. CALL_TEST_TARGET_ALLOWLIST=<demo target>  (the exact demo number and/or
#      demo base URL, comma-separated).
# Both are empty by default, so by default the runner cannot drive ANY target.
# List ONLY the demo deployment's number/URL — never a real clinic's. The target
# checked is the phone number (real-call mode) or RENDER_SERVER_URL (direct-WS).
def call_target_is_allowed(target: str) -> bool:
    opt_in = os.getenv("RUN_LIVE_CALL_TESTS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not opt_in:
        return False
    allow = {
        t.strip()
        for t in os.getenv("CALL_TEST_TARGET_ALLOWLIST", "").split(",")
        if t.strip()
    }
    return bool(target) and target.strip() in allow
