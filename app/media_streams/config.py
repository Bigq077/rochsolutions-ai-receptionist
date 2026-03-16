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
    "&min_turn_silence=800"
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
    """
    CLINIC_SELECTION  = "clinic_selection"
    PHONE_CONFIRM_YES = "phone_confirm_yes"   # caller confirmed Twilio caller-ID number
    PHONE_CONFIRM_NO  = "phone_confirm_no"    # caller rejected Twilio caller-ID number
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

# Rotating phrases played by the watchdog timer (cycles through in order)
WATCHDOG_PHRASES = [
    "Just bear with me one moment...",
    "Let me just check that for you...",
    "One moment please...",
]

# ---------------------------------------------------------------------------
# Silence / re-ask constants
# ---------------------------------------------------------------------------

# If the caller has been silent for this many seconds after Susie asked a
# question, re-ask the question with a "Sorry about that — " prefix.
QUESTION_SILENCE_SEC = 5.0

# Maximum number of times the same question is re-asked before giving up
# and offering a transfer.
MAX_REASK_ATTEMPTS = 2

# Prefix added before the re-asked question text
REASK_PREFIX = "Sorry about that — "

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
    "I'm sorry, I'm having some technical difficulties. "
    "Please call back and I'll be ready to help you."
)

SAFE_FALLBACK_PHRASE = (
    "Sorry, I had a bit of a blip there -- "
    "could you give me just a moment and try again?"
)

BAD_LINE_PHRASE = "Sorry about that — could you say that again for me?"

# Played while LLM is generating (if first chunk exceeds LLM_FIRST_CHUNK_TIMEOUT_MS)
FILLER_PHRASE = "Just one moment..."

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
# Booking opening line (Bug 2 fix — hardcoded, never varies)
# ---------------------------------------------------------------------------

# This EXACT line is injected via TTS whenever booking intent is detected,
# bypassing the LLM entirely so the wording is deterministic every time.
BOOKING_OPEN = (
    "Of course you can book an appointment — "
    "are you looking to visit our Alcester clinic "
    "or our Redditch one?"
)

# Booking intent keywords — matched against normalised transcript to detect
# when the caller wants to book before they have named a clinic.
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
    "SILENCE RULE — CRITICAL:\n"
    "After asking a question, say NOTHING until the caller responds. "
    "Do not comment on silence. Do not say you are waiting. "
    "Do not ask if they are still there.\n"
    "Silence after a question is completely normal in a phone call — wait for it.\n"
    "NEVER output any of the following (or any variation):\n"
    "  - 'I am waiting for your answer'\n"
    "  - 'I'm waiting for you to respond'\n"
    "  - 'Are you still there?'\n"
    "  - 'Hello?'\n"
    "  - 'Just waiting for your response'\n"
    "  - Any sentence that comments on the caller being silent or slow to respond\n"
    "Simply stop after your question. The re-ask system handles genuine silence "
    "automatically — you must never do this yourself.\n"
)

# ---------------------------------------------------------------------------
# Phone confirm prompts (Bug 4 fix)
# ---------------------------------------------------------------------------

# Played (via fast-path, no LLM) when the Twilio caller-ID number is present
# and we need to confirm whether to use it for the booking.
PHONE_CONFIRM_QUESTION = (
    "Just to confirm — shall I use the number you're calling from "
    "for the booking?"
)

PHONE_CONFIRM_YES_REPLY = (
    "Perfect — and could I take your full name please?"
)

PHONE_CONFIRM_NO_REPLY = (
    "No problem — what number would you like to use for the booking? "
    "Could you give me the first five digits?"
)

# ---------------------------------------------------------------------------
# Noise-only words
# ---------------------------------------------------------------------------

NOISE_ONLY_WORDS: frozenset = frozenset({
    "mm", "mmm", "mhm", "hmm", "hm", "uh", "um", "ah", "eh",
    "oh", "er", "erm", "ha", "huh",
})
