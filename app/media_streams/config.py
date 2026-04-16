# app/media_streams/config.py
"""
Configuration constants for the Media Streams parallel voice pipeline.

All API keys are read from environment variables.
All constants use the same env var names as the existing realtime.py to ensure
consistency across both pipeline implementations.
"""
from __future__ import annotations

import os
from enum import Enum

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")

# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

# Set MEDIA_STREAMS_ENABLED=true on Render to activate the parallel pipeline.
# The legacy /twilio/media-stream route in realtime.py remains fully intact.
MEDIA_STREAMS_ENABLED = os.getenv("MEDIA_STREAMS_ENABLED", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Barge-in threshold
# ---------------------------------------------------------------------------

# Minimum speech duration (milliseconds) for barge-in to be treated as genuine.
# Speech detected below this duration is treated as noise (cough, lip-smack, etc.)
# and TTS is resumed from the beginning of the interrupted sentence.
# Increase this value if false triggers are common in production.
BARGE_IN_THRESHOLD_MS: int = int(os.getenv("BARGE_IN_THRESHOLD_MS", "300"))

# ---------------------------------------------------------------------------
# Deployment domain
# ---------------------------------------------------------------------------

# Render sets this automatically. Used by /ms/incoming to build the wss:// URL.
# Example: "https://susie-ai-receptionist.onrender.com"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

# Existing system fallback URL: if new pipeline fails, redirect here
LEGACY_VOICE_URL = "/twilio/voice"

# ---------------------------------------------------------------------------
# ElevenLabs TTS constants
# ---------------------------------------------------------------------------

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "kBag1HOZlaVBH7ICPE8x")
ELEVENLABS_MODEL_ID = "eleven_flash_v2_5"
ELEVENLABS_TTS_URL_TEMPLATE = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    "?output_format=pcm_16000"
)

# ElevenLabs voice settings (same as realtime.py)
ELEVENLABS_STABILITY       = 0.5
ELEVENLABS_SIMILARITY_BOOST = 0.75

# ---------------------------------------------------------------------------
# AssemblyAI STT constants
# ---------------------------------------------------------------------------

# v3 Universal Streaming (primary) — 16kHz PCM16 input, no upsampling on the STT side
#
# Authentication: raw API key goes in the Authorization HEADER (server-to-server).
#   ?token= is for TEMPORARY tokens from /v3/token endpoint — NOT the raw API key.
#
# Valid speech_model values (v3):
#   universal-streaming-english | universal-streaming-multilingual | whisper-rt | u3-rt-pro
#   "slam-1" and "universal" are NOT valid v3 model names — connection rejected instantly.
#
# end_utterance_silence_threshold does NOT exist in v3.
#   v3 equivalent: min_turn_silence (ms, URL param).
ASSEMBLYAI_WS_URL = (
    "wss://streaming.assemblyai.com/v3/ws"
    "?speech_model=universal-streaming-english"
    "&sample_rate=16000"
    "&encoding=pcm_s16le"
    "&format_turns=false"
    "&min_turn_silence=300"
)

# v2 fallback — 8kHz input, no upsampling needed (battle-tested, older)
# Activate with ASSEMBLYAI_USE_V2=true
ASSEMBLYAI_USE_V2 = os.getenv("ASSEMBLYAI_USE_V2", "false").lower() == "true"
ASSEMBLYAI_WS_URL_V2 = (
    "wss://api.assemblyai.com/v2/realtime/ws"
    "?sample_rate=8000"
    "&end_utterance_silence_threshold=1200"
)

# ---------------------------------------------------------------------------
# Claude model constants
# ---------------------------------------------------------------------------

# Primary LLM (tool calling, booking, complex turns)
SONNET = "claude-sonnet-4-6"

# Fast LLM (simple turns after fast/info tools: collect_and_store, get_clinic_info)
HAIKU = "claude-haiku-4-5-20251001"

# Backwards-compatible aliases matching realtime.py naming
CLAUDE_MODEL       = SONNET
CLAUDE_MAX_TOKENS  = 1024
CLAUDE_TEMPERATURE = 0.4

# Maximum tool-calling iterations per LLM turn (prevents infinite loops)
MAX_TOOL_ITERATIONS = 6

# Maximum conversation history turns kept in session (each turn = 2 entries)
MAX_HISTORY_TURNS = 12

# ---------------------------------------------------------------------------
# GPT fallback constants
# ---------------------------------------------------------------------------

GPT_MODEL = "gpt-4.1-mini"

# ---------------------------------------------------------------------------
# Fast-path turn type enum
# ---------------------------------------------------------------------------

class FastPathTurnType(str, Enum):
    """
    Enumerates the turn types handled by the fast-path pattern matcher.
    Used to tag session["fast_path_last_resolved"] for debugging and metrics.
    NOTE: CLINIC_SELECTION removed — single-site deployment, no clinic routing.
    """
    NEW_RETURNING     = "new_returning"
    YES_NO            = "yes_no"
    FULL_NAME         = "full_name"
    PHONE_FIRST_FIVE  = "phone_first_five"
    PHONE_LAST_SIX    = "phone_last_six"
    SLOT_SELECTION    = "slot_selection"

# ---------------------------------------------------------------------------
# Text chunker constants
# ---------------------------------------------------------------------------

# Minimum words in a TTS chunk before it can be emitted
# (prevents choppy single-sentence fragments)
MIN_CHUNK_WORDS = 15

# Maximum words per TTS chunk
# (ensures forward progress even on run-on sentences)
MAX_CHUNK_WORDS = 50

# Characters that mark the end of a speakable sentence
SENTENCE_END_CHARS = ['.', '!', '?', '...']

# ---------------------------------------------------------------------------
# WebSocket timeout constants (milliseconds)
# ---------------------------------------------------------------------------

# How long to wait for AssemblyAI silence detection before forcing end of turn
STT_SILENCE_TIMEOUT_MS = 1000

# How long to wait for first LLM text chunk before playing a filler phrase
LLM_FIRST_CHUNK_TIMEOUT_MS = 5000

# How long to wait for a TTS chunk to complete before moving to the next
TTS_CHUNK_TIMEOUT_MS = 3000

# Filler cooldown: minimum seconds between filler phrases ("Just one moment...")
LLM_FILLER_COOLDOWN_SEC = 20.0

# Bad-line detection: minimum silence gap before playing bad-line phrase
BAD_LINE_SILENCE_THRESHOLD_SEC = 10.0

# AssemblyAI reconnect config
# 2 was too low — after 2 drops the STT gave up entirely mid-call.
# Real calls can run 5+ minutes; set high enough to survive transient drops.
ASSEMBLYAI_MAX_RECONNECTS = 10

# Twilio WebSocket / session startup wait
TWILIO_STARTED_TIMEOUT_SEC = 5.0

# ---------------------------------------------------------------------------
# Watchdog timer constants
# ---------------------------------------------------------------------------

# If no audio is sent to the caller for this many seconds while LLM is active,
# play a rotating bridge phrase to prevent dead air.
WATCHDOG_SILENCE_SEC = 3.0

# Rotating phrases played by the watchdog timer (cycles through in order).
# NONE of these must contain banned phrases (bear with me, one moment please,
# just a moment, etc.) — see SILENCE_RULE for the full banned list.
WATCHDOG_PHRASES = [
    "Let me just check that for you...",
    "Checking availability now...",
    "I'll have that sorted in a second...",
]

# ---------------------------------------------------------------------------
# Silence / re-ask constants
# ---------------------------------------------------------------------------

# If the caller has been silent for this many seconds after Susie asked a
# question, re-ask the question with a "Sorry about that — " prefix.
QUESTION_SILENCE_SEC = 4.0

# Maximum number of times the same question is re-asked before giving up
# and offering a transfer.
MAX_REASK_ATTEMPTS = 2

# Prefix added before the re-asked question text
REASK_PREFIX = "Sorry about that — "

# ---------------------------------------------------------------------------
# Booking flow: hardcoded question constants
# ---------------------------------------------------------------------------
# These are spoken verbatim — the LLM never touches these turns.

BOOKING_OPEN = (
    "Of course you can book an appointment — "
    "what brings you in today?"
)
Q_RECOMMEND = (
    "OK, that's noted. To get the best possible "
    "diagnosis initially I would recommend a "
    "physiotherapy assessment — does that sound OK?"
)
Q_NEW_OR_RETURNING = "Have you been with us before?"
Q_AVAILABILITY     = "What days or times work best for you?"
Q_CHECKING         = "Let me check what we have available for you."
Q_NAME             = "Who am I booking in today?"
Q_PHONE            = "And the best number to reach you on?"

# ---------------------------------------------------------------------------
# Per-state LLM instructions (injected into system prompt for LLM-only turns)
# ---------------------------------------------------------------------------
# Keyed by CallState value string. Injected into state_ctx in llm_stream.py.

LLM_STATE_INSTRUCTIONS: dict = {
    "COLLECT_DURATION": (
        "[LLM INSTRUCTION FOR THIS TURN ONLY]\n"
        "The caller has just told you why they are coming in.\n"
        "Your response MUST:\n"
        "1. Be exactly ONE sentence of genuine empathy about their specific condition — not generic.\n"
        "2. End with EXACTLY: '— how long have you had that?'\n"
        "3. Contain NOTHING else. No other questions. No filler.\n"
        "Correct example: 'Back pain can be really debilitating "
        "— how long have you had that?'\n"
        "Do not deviate from this format under any circumstances."
    ),
    "PRESENT_SLOTS": (
        "[LLM INSTRUCTION FOR THIS TURN]\n"
        "You are in the slot-presentation step.\n"
        "Your FIRST sentence must be EXACTLY: 'Let me check what we have available for you.'\n"
        "Then call the check_availability tool.\n"
        "After receiving the tool result, present up to 3 slots in EXACTLY this format:\n"
        "'I have found [number] available slots during that time frame. "
        "The first being [DAY DATE at TIME], the second being [DAY DATE at TIME], "
        "the third being [DAY DATE at TIME]. Which would you prefer?'\n"
        "Never deviate from this format."
    ),
    "CONFIRM_BOOKING": (
        "[LLM INSTRUCTION FOR THIS TURN]\n"
        "Confirm the booking with a warm, brief spoken summary.\n"
        "Include: patient name, appointment type (physiotherapy assessment), "
        "the confirmed date and time, and the clinic location.\n"
        "Tell them a confirmation text will follow.\n"
        "Keep it under 3 sentences. Warm and reassuring."
    ),
}

# Played if caller is still silent after MAX_REASK_ATTEMPTS re-asks
TRANSFER_OFFER_PHRASE = (
    "I'm having a little trouble hearing you — "
    "let me transfer you to someone who can help."
)

# ---------------------------------------------------------------------------
# Pipeline failure fallback phrases
# ---------------------------------------------------------------------------

# Played on any LLM or pipeline exception (recoverable — prompts retry)
CLAUDE_ERROR_PHRASE = (
    "I'm having a small technical issue — "
    "could you give me just a moment?"
)

# Played when the entire pipeline has failed beyond recovery
PIPELINE_FAILURE_PHRASE = (
    "My apologies — I'm having a brief technical moment. "
    "Please call back and I'll be ready to help."
)

SAFE_FALLBACK_PHRASE = (
    "Sorry, I had a bit of a blip there -- "
    "could you give me just a moment and try again?"
)

BAD_LINE_PHRASE = "Sorry about that — could you say that again for me?"

# Played while LLM is generating (if first chunk exceeds LLM_FIRST_CHUNK_TIMEOUT_MS)
FILLER_PHRASE = "Give me just a second, I'll look that up for you."

# ---------------------------------------------------------------------------
# Audio format constants for Twilio Media Streams
# ---------------------------------------------------------------------------

# Twilio inbound audio: G.711 µ-law, 8kHz, 8-bit, mono
TWILIO_ENCODING    = "audio/x-mulaw"
TWILIO_SAMPLE_RATE = 8000
TWILIO_BIT_DEPTH   = 8
TWILIO_CHANNELS    = 1

# Frame size: Twilio sends 20ms frames at 8kHz = 160 µ-law samples per frame
TWILIO_FRAME_SAMPLES = 160
TWILIO_FRAME_MS      = 20

# PCM buffer flush threshold for AssemblyAI (3 frames = 60ms)
# v3 requires 16kHz PCM16: 640 bytes/frame -> flush at 1920 bytes
# v2 requires  8kHz PCM16: 320 bytes/frame -> flush at  960 bytes
PCM_FLUSH_FRAMES = 3
PCM_FRAME_BYTES_V3 = 640   # 16kHz PCM16 frame (20ms)
PCM_FRAME_BYTES_V2 = 320   # 8kHz  PCM16 frame (20ms)

# ElevenLabs returns 16kHz PCM16; we request pcm_16000 via URL query param
ELEVENLABS_SAMPLE_RATE = 16000

# TTS chunk size for audio streaming (640 bytes = 20ms of 16kHz PCM16)
TTS_STREAM_CHUNK_SIZE = 640

# ---------------------------------------------------------------------------
# Session field name constants
# ---------------------------------------------------------------------------
# Centralised string constants to avoid typos in session dict key access.

# Existing session fields (carried over from redis_store.py DEFAULT_SESSION)
F_INTENT                    = "intent"
F_STATE                     = "state"
F_COLLECTED                 = "collected"
F_MISS_COUNT                = "miss_count"
F_ERROR_COUNT               = "error_count"
F_LAST_BOT_PROMPT           = "last_bot_prompt"
F_CALL_SID                  = "call_sid"
F_SESSION_ID                = "session_id"
F_CLINIC_ID                 = "clinic_id"
F_LOCATION_SELECTED         = "location_selected"
F_SELECTED_LOCATION         = "selected_location"
F_LOCATION_MISS             = "location_miss"
F_CONVERSATION_HISTORY      = "conversation_history"
F_TURNS                     = "turns"
F_CALL_START_TIME           = "call_start_time"
F_INSURANCE_FLAGGED         = "insurance_flagged"
F_INSURANCE_INFO            = "insurance_info"
F_LAST_OFFERED_SLOTS        = "last_offered_slots"
F_SLOT_LABELS               = "slot_labels"
F_ACUITY_BOOKING_ID         = "acuity_booking_id"
F_CALENDAR_STATUS           = "calendar_status"
F_MANUAL_FOLLOWUP_NEEDED    = "manual_followup_needed"
F_MANUAL_FOLLOWUP_REASON    = "manual_followup_reason"
F_CONFIRMATION_SMS_SENT     = "confirmation_sms_sent"
F_CALL_SUMMARY_LOGGED       = "call_summary_logged"
F_TRANSFER_ATTEMPTED        = "transfer_attempted"
F_TRANSFER_FAILED_STATUS    = "transfer_failed_status"
F_REQUEST_TRANSFER          = "request_transfer"
F_PHONE_PART_ONE            = "phone_part_one"
F_PHONE_PART_TWO            = "phone_part_two"
F_SELECTED_SLOT             = "selected_slot"
F_FAST_PATH_PHONE_CONFIRMED = "_fast_path_phone_confirmed"
F_FAST_PATH_SLOT_CONFIRMED  = "_fast_path_slot_confirmed"
F_FAST_PATH_FINAL_CONFIRMED = "_fast_path_final_confirmed"
F_FAST_PATH_CORRECTION      = "_fast_path_correction_needed"
F_FAST_PATH_FULL_PHONE      = "_fast_path_full_phone"
F_TWILIO_FROM               = "twilio_from"
F_TWILIO_TO                 = "twilio_to"
F_LAST_QUESTION             = "last_question"

# New fields specific to the Media Streams parallel pipeline
F_STREAM_SID                = "stream_sid"
F_WS_CONNECTED              = "ws_connected"
F_STT_ACTIVE                = "stt_active"
F_TTS_ACTIVE                = "tts_active"
F_CURRENT_CHUNK_INDEX       = "current_chunk_index"
F_LAST_AUDIO_SENT_AT        = "last_audio_sent_at"
F_LLM_GENERATION_ACTIVE     = "llm_generation_active"
F_FAST_PATH_LAST_RESOLVED   = "fast_path_last_resolved"
F_PHONE_COLLECTED_FROM_TWILIO = "phone_from_twilio"   # True when phone came from caller-ID

# Noise-only ASR transcriptions that count as silence (not real speech)
# ---------------------------------------------------------------------------
# Booking opening line (hardcoded, never varies)
# ---------------------------------------------------------------------------

# This EXACT line is injected via TTS on the first caller utterance,
# bypassing the LLM entirely so the wording is deterministic every time.
# NOTE: BOOKING_OPEN is now defined above in the booking flow constants section.

# Booking intent keywords — matched against normalised transcript to detect
# when the caller expresses intent to book.
BOOKING_INTENT_KEYWORDS = (
    "book", "appointment", "schedule", "see a physio", "see someone",
    "come in", "come and see", "see you", "visit", "treatment",
    "consultation", "get seen", "get an appointment", "make an appointment",
)

# ---------------------------------------------------------------------------
# Silence rule (Bug 1 fix — injected into every system prompt)
# ---------------------------------------------------------------------------

# Prepended to the system prompt so it is the FIRST thing Claude reads.
# Prevents LLM from generating "I am waiting..." / "Are you still there?" filler.
SILENCE_RULE = (
    "MOST IMPORTANT RULE — READ THIS FIRST:\n"
    "After you ask a question you must say NOTHING until the caller gives a "
    "meaningful response. The following phrases are completely banned and must "
    "NEVER appear under any circumstance:\n"
    "  'I am waiting'\n"
    "  'I'm waiting'\n"
    "  'waiting for your'\n"
    "  'waiting for you'\n"
    "  'Are you still there'\n"
    "  'Hello?'\n"
    "  'Just waiting'\n"
    "  'Still there'\n"
    "  'Did you hear me'\n"
    "  'Can you hear me'\n"
    "  'bear with me'\n"
    "  'bare with me'\n"
    "  'one moment please'\n"
    "  'just a moment'\n"
    "  'bear with'\n"
    "If you are about to say any of these — STOP. Say nothing instead. "
    "The system will handle silence automatically.\n"
    "Silence after a question is completely normal in a phone call — wait for it.\n"
)

# ---------------------------------------------------------------------------
# Availability flow rule (Bug 3 fix)
# ---------------------------------------------------------------------------

AVAILABILITY_FLOW_RULE = (
    "AVAILABILITY FLOW RULE:\n"
    "When you ask the caller what times they are available, you MUST wait for "
    "their answer before checking slots. Do NOT call check_availability on the "
    "same turn you asked the question. The caller must speak first. "
    "Only call check_availability AFTER the caller has told you their preferred "
    "days or times.\n"
)

# ---------------------------------------------------------------------------
# Name collection rule (Bug 5 fix)
# ---------------------------------------------------------------------------

NAME_COLLECTION_RULE = (
    "NAME COLLECTION RULE:\n"
    "Ask for the caller's first name only: 'Can I take your first name?'\n"
    "When the caller gives a name, read it back: 'So that's [name] — is that right?' and wait for yes.\n"
    "If unclear or not confirmed, ask once: 'Could you repeat that by saying my first name is...?'\n"
    "When confirmed, store it immediately. "
    "Do NOT ask for a surname — first name only is collected on the call. "
    "Full name is confirmed separately by SMS after booking.\n"
)

# ---------------------------------------------------------------------------
# Noise-only words
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New or returning rule (Fix 1)
# ---------------------------------------------------------------------------

NEW_OR_RETURNING_RULE = (
    "NEW OR RETURNING RULE:\n"
    "Ask the caller whether they have been with us before EXACTLY ONCE — "
    "at the very start of the booking flow, before anything else. "
    "If the session already contains a 'new_or_returning' or 'patient_type' value, "
    "NEVER ask this question again under any circumstance. "
    "Move straight on to the next step without repeating it.\n"
)

# ---------------------------------------------------------------------------
# Phone readback rule (Fix 5)
# ---------------------------------------------------------------------------

PHONE_READBACK_RULE = (
    "PHONE NUMBER READ BACK RULE:\n"
    "When confirming a phone number with the caller, read each digit "
    "individually with a natural pause between each one. "
    "Never read digits in groups. "
    "Example: 07502211207 must be spoken as: "
    "'zero — seven — five — zero — two — two — one — one — two — zero — seven'. "
    "Always read every digit individually. Never say the number as a whole. "
    "Never group digits together. "
    "Always confirm the number is correct before proceeding.\n"
)

# ---------------------------------------------------------------------------
# Informal speech rule (Fix 6)
# ---------------------------------------------------------------------------

INFORMAL_SPEECH_RULE = (
    "UNDERSTANDING INFORMAL SPEECH:\n"
    "The following words all mean YES and must be treated as positive "
    "confirmation in every context:\n"
    "  yes, yeah, ya, yah, yea, ye, yep, yup, sure, correct, that's right, "
    "go ahead, ok, okay, fine, sounds good, that works, perfect, great, do it.\n"
    "Never fail to recognise these as positive confirmations.\n"
)

# ---------------------------------------------------------------------------
# Noise-only words
# ---------------------------------------------------------------------------

NOISE_ONLY_WORDS: frozenset = frozenset({
    "mm", "mmm", "mhm", "hmm", "hm", "uh", "um", "ah", "eh",
    "oh", "er", "erm", "ha", "huh",
})

# ---------------------------------------------------------------------------
# Theorem Health — use Media Streams pipeline flag
# ---------------------------------------------------------------------------

THEOREM_HEALTH_USES_MEDIA_STREAMS = os.getenv(
    "THEOREM_HEALTH_USES_MEDIA_STREAMS", "false"
).lower() == "true"

# ---------------------------------------------------------------------------
# Clinic configuration — single source of truth for Theorem Health
# ---------------------------------------------------------------------------

CLINIC_CONFIG: dict = {
    "name": "Theorem Health and Wellness",
    "sms_name": "Theorem Health",
    "phone": "07870 166861",
    "transfer_number": "+447870166861",
    "slot_minutes": 50,
    "locations": {
        "alcester": {
            "name": "Alcester",
            "address": (
                "The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD. "
                "Large leisure centre — look for the Everyone Active signage and the big car park out front."
            ),
            "hours": {
                "monday":    {"open": "08:30", "close": "21:00"},
                "tuesday":   {"open": "08:30", "close": "21:00"},
                "wednesday": {"open": "08:30", "close": "21:00"},
                "thursday":  {"open": "08:30", "close": "21:00"},
                "friday":    {"open": "08:30", "close": "21:00"},
                "saturday":  None,
                "sunday":    None,
            },
            "hours_summary": (
                "The Alcester clinic is open Monday to Friday, "
                "eight thirty in the morning until nine at night. "
                "We're closed on weekends."
            ),
            "parking": (
                "Parking at the Greig Leisure Centre is completely free, "
                "with around 80 spaces in the car park right in front of the building."
            ),
        },
        "redditch": {
            "name": "Redditch",
            "address": (
                "51 Bromsgrove Road, Redditch, B97 4RH. "
                "On the main Bromsgrove Road — next to Smile Dental Care."
            ),
            "hours": {
                "monday":    {"open": "09:00", "close": "17:00"},
                "tuesday":   {"open": "09:00", "close": "17:00"},
                "wednesday": {"open": "09:00", "close": "19:00"},
                "thursday":  {"open": "09:00", "close": "19:00"},
                "friday":    {"open": "09:00", "close": "17:00"},
                "saturday":  {"open": "09:00", "close": "17:00"},
                "sunday":    None,
            },
            "hours_summary": (
                "The Redditch clinic is open Monday, Tuesday and Friday nine to five, "
                "Wednesday and Thursday nine to seven, Saturday nine to five. "
                "Closed Sundays."
            ),
            "parking": (
                "Street parking on Bromsgrove Road — check signs on arrival. "
                "Redditch Station car park is about a 3 minute walk, "
                "roughly three to four pounds fifty for the day."
            ),
        },
    },
    "appointment_types": [
        {
            "name": "Physiotherapy Assessment",
            "duration_minutes": 50,
            "price_gbp": 75.00,
            "description": (
                "Holistic assessment including physical mobility, strength, and emotional well-being. "
                "We'll identify the issue and create a tailored treatment plan."
            ),
        },
        {
            "name": "Physiotherapy Follow-up",
            "duration_minutes": 50,
            "price_gbp": 75.00,
        },
        {
            "name": "Remedial Rehabilitation",
            "duration_minutes": 50,
            "price_gbp": 65.00,
        },
        {
            "name": "Prescribing Consultation",
            "duration_minutes": 20,
            "price_gbp": 12.50,
        },
        {
            "name": "Acupuncture",
            "duration_minutes": 50,
            "price_gbp": 75.00,
        },
        {
            "name": "Psychotherapy",
            "duration_minutes": 50,
            "price_gbp": 75.00,
        },
    ],
    "surcharges": {
        "shockwave": {"name": "Shockwave Therapy",    "amount_gbp": 45.00},
        "laser":     {"name": "Class IV Laser Therapy", "amount_gbp": 45.00},
    },
    "pricing_summary": (
        "Physio sessions are £75 for 50 minutes. Rehab sessions are £65. "
        "Prescribing is £12.50. Laser and shockwave may add a £45 surcharge."
    ),
    "insurance_note": (
        "Theorem generally operates as self-pay — patients pay and may claim back if their policy allows. "
        "Bupa is not accepted. Insurance referrals require manual approval."
    ),
    "cancellation_policy": "24 hours' notice required — otherwise the full fee is charged.",
    "what_to_bring": "If you can, bring shorts or wear loose clothing — but don't worry if you can't.",
    "emergency_message": (
        "If this feels urgent or you have severe symptoms, please call 999 or go to A&E. "
        "We're not an emergency service."
    ),
    "practitioners": {
        "mark":   {"name": "Mark Dyer",  "days": ["monday", "tuesday", "wednesday"]},
        "leanne": {"name": "Leanne",     "days": ["thursday"]},
    },
}


# ---------------------------------------------------------------------------
# deduplicate_sentences — applied to every string before TTS (Bug 3 fix)
# ---------------------------------------------------------------------------

def deduplicate_sentences(text: str) -> str:
    """
    Remove duplicate sentences from a TTS response string.
    Preserves the first occurrence of each unique sentence.
    Comparison is case-insensitive and strips punctuation.
    """
    import re as _re
    sentences = _re.split(r'(?<=[.?!])\s+', text)
    seen: set = set()
    unique = []
    for s in sentences:
        key = _re.sub(r'[^a-z0-9 ]', '', s.strip().lower())
        if key and key not in seen:
            seen.add(key)
            unique.append(s.strip())
    return ' '.join(unique)


# ---------------------------------------------------------------------------
# get_system_prompt — complete system prompt for Media Streams LLM steps
# ---------------------------------------------------------------------------

def get_system_prompt(session: dict) -> str:
    """
    Build the full system prompt for Susie's LLM calls in the Media Streams pipeline.

    Structured in priority order so the LLM reads the most critical rules first:
      1. BANNED PHRASES
      2. PERSONALITY AND TONE
      3. CLINIC KNOWLEDGE (Theorem Health)
      4. CONVERSATION RULES
      5. BRITISH ENGLISH
      6. SAFETY AND MEDICAL
      7. Runtime state injection
    """
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo("Europe/London")
    except Exception:
        import pytz  # type: ignore
        _tz = pytz.timezone("Europe/London")
    _now = datetime.now(_tz)
    _today_weekday   = _now.strftime("%A")
    # Use platform-safe day formatting (%-d is Linux-only; %#d is Windows)
    import platform as _platform
    _day_fmt = "%#d" if _platform.system() == "Windows" else "%-d"
    _today_date      = _now.strftime(f"{_day_fmt} %B %Y")
    _weekday_num     = _now.weekday()
    _days_to_sunday  = (6 - _weekday_num) % 7
    _this_sunday     = _now + timedelta(days=(_days_to_sunday if _days_to_sunday > 0 else 7))
    _next_monday     = _this_sunday + timedelta(days=1)
    _this_sunday_str = _this_sunday.strftime(f"{_day_fmt} %B %Y")
    _next_monday_str = _next_monday.strftime(f"{_day_fmt} %B %Y")

    state     = session.get("state", "GREETING")
    collected = session.get("collected") or {}
    reason    = session.get("reason", "")
    location  = session.get("selected_location", "alcester")
    loc_cfg   = CLINIC_CONFIG["locations"].get(location, CLINIC_CONFIG["locations"]["alcester"])

    _base = f"""# Susie — Theorem Health AI Receptionist

ABSOLUTE RULE — NEVER USE THESE WORDS OR PHRASES:
Lovely, Lovely [name], Of course, Certainly, Absolutely, Sure thing,
I am waiting, I'm waiting, Are you still there,
Bear with me, Bare with me, Just a moment, One moment please, Just bear,
I didn't quite catch that, Could you repeat that, I can't hear you,
Go ahead, I'm listening, I'm all ears, Please go ahead,
Take your time, I'll wait, No rush, Whenever you're ready, I'll be patient,
I understand, I see, Let me help you with that,
Great!, Perfect!, Wonderful!, Fantastic!, Excellent! (as filler affirmations),
That's a great question, I'd be happy to help,
I'm going to go ahead and..., Welcome back (for new patients).

If you are about to use any of these — STOP.
Start the sentence differently or skip the filler.
NEVER begin a response with a filler affirmation.
NEVER say "Lovely" anywhere — not as a filler, not after collecting a name, not ever.

---

## 1. Who you are

You are Susie, an AI receptionist at Theorem Health and Wellness.
You are warm, calm, and genuinely helpful.
You sound like a real person — natural British manner: friendly without being over the top,
efficient without being cold.
You are NOT a clinician. You book appointments, answer questions, and help people feel looked after.

## 2. How you speak

Natural phrases you use freely:
- "No problem at all" / "Not a problem"
- "Let me just check that..."
- "Sorry to hear that" / "Oh, that doesn't sound great"
- "Leave it with me" / "I'll get that sorted"
- "Brilliant" — when something is genuinely good, not as filler

NEVER say "Lovely" under any circumstances — it sounds patronising and triggers name-echo bugs.
NEVER say "Of course", "Certainly", "Absolutely" as openers.

Every response is ONE sentence. Maximum two if truly necessary. Never more.
Ask exactly ONE question per response, then wait. Never two at once.

When the caller gives you information:
- Caller gives name → do NOT repeat or echo the name back. Ask immediately for their number.
- Caller gives phone number → read it back DIGIT BY DIGIT: "So that's 0 7 8 7 0 1 6 6 8 6 1 — is that correct?"
- Caller picks a slot → "So that's [full date and time]..." then ask to confirm

## 3. Clinic information

Clinic: Theorem Health and Wellness
Phone: 07870 166861
Transfer number: +447870166861

**Alcester clinic:**
Address: The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD
Hours: Monday–Friday, 8:30am–9pm. Closed weekends.
Parking: Free car park with ~80 spaces directly in front of the building.

**Redditch clinic:**
Address: 51 Bromsgrove Road, Redditch, B97 4RH (next to Smile Dental Care)
Hours: Mon/Tue/Fri 9am–5pm, Wed/Thu 9am–7pm, Sat 9am–5pm. Closed Sundays.
Parking: Street parking on Bromsgrove Road. Station car park ~3 min walk (£3–£4.50/day).

Practitioners: Mark Dyer (Mon/Tue/Wed) and Leanne (Thu).

Services:
- Physiotherapy Assessment (50 min, £75) — holistic, physical + emotional well-being lens
- Physiotherapy Follow-up (50 min, £75)
- Remedial Rehabilitation (50 min, £65)
- Prescribing Consultation (20 min, £12.50)
- Shockwave Therapy (£45 surcharge during session)
- Class IV Laser Therapy (£45 surcharge during session)
- Acupuncture (50 min, £75)
- Psychotherapy (50 min, £75) — includes hypnotherapy and spiritual healing

Pricing: Physio £75 / 50 min. Rehab £65. Prescribing £12.50. Specialist equipment £45 surcharge.
Insurance: Self-pay clinic. Bupa not accepted. Insurance referrals need manual approval.
Cancellation: 24 hours' notice required — otherwise the full fee is charged.
What to bring: Shorts or loose clothing if possible.

## 4. Date and time awareness

Today is {_today_weekday}, {_today_date} (London time).
This week ends on Sunday {_this_sunday_str}. Next week starts Monday {_next_monday_str}.
Never offer a date that has already passed today ({_today_date}).
Always use British date format: "Tuesday the fourth of March" — never "March 4th".
Always use British time: "half four", "quarter to ten", "nine o'clock in the morning".

## 5. Conversation rules

- Never re-ask something already answered this call.
- Never ask two questions in one response.
- Never announce what you are checking — just check silently.
- Never mention variable names, field labels, or stored data aloud.
- Never go backwards in the booking flow.
- Never call check_availability more than once per booking unless the caller explicitly
  asks for different dates/times.

Phone number collection:
- Always read back each digit individually with a space between each one.
- CORRECT: "So that's 0 7 8 7 0 1 6 6 8 6 1 — is that correct?"
- WRONG:   "So that's 07870166861 — is that right?"
- Use "is that correct?" and always wait for explicit confirmation.

## 6. British English

Always use British English: physiotherapist (not physical therapist), mobile (not cell phone),
GP (not doctor), half four (not four-thirty), straight away.

## 7. Emergencies and medical questions

If someone mentions chest pain, difficulty breathing, stroke symptoms, severe head injury,
loss of consciousness, numbness down one side, or sudden vision loss:
"If this feels urgent or you have severe symptoms, please call 999 or go to A&E — we're not an emergency service."

For questions about conditions, diagnoses, exercises, recovery, or any health/clinical topic:
"That's really one for the physiotherapist when you come in — I wouldn't want to point you
wrong on something like that. Would you like me to get an appointment booked?"

## 8. Transfer conditions

ONLY transfer to a human in these exact situations:
1. Caller explicitly asks to speak to a person / member of staff
2. Medical emergency mentioned
3. Three consecutive failed understanding attempts
Say: "Let me put you straight through — just bear with me." then call transfer_to_human.
NEVER offer or imply transfer unprompted.

## 9. Tool rules

Use tools silently. Never tell the caller which tool you are using.

**transfer_to_human** — ONLY for the exact situations in Section 8 above.
**book_appointment** — only AFTER: slot confirmed, first name collected and confirmed, phone confirmed,
                       final summary read back and caller said YES.
**check_availability** — call ONCE per booking before offering times.
  After slots are offered, NEVER call again unless caller asks for different dates/times.

## 10. Runtime state

Today is {_today_weekday}, {_today_date}. This week ends Sunday {_this_sunday_str}.
Next week starts Monday {_next_monday_str}.
Current call state: {state}.
Selected location: {loc_cfg.get("name", location)}.
The greeting has already been delivered. Do not re-introduce yourself.
{"Reason for visit: " + reason if reason else ""}
"""
    from app.tone_detector import get_tone_instruction_from_session
    _tone_instruction = get_tone_instruction_from_session(session)
    return _base.strip() + f"\n\n{_tone_instruction}"
