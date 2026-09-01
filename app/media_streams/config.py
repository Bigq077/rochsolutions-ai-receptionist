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
# Speaking rate
# ---------------------------------------------------------------------------
# `speed` is a voice_settings field on eleven_flash_v2_5.  Below 1.0 is slower.
# ElevenLabs documents the accepted band as 0.7-1.2 and rejects anything outside
# it with a 422, so both values are clamped here: a typo in the Render dashboard
# must not be able to turn every TTS request on a live call into a validation
# error.
#
# ELEVENLABS_SPEED is the whole-call default and stays at 1.0 — this is not a
# licence to slow Susie down generally, which would cost turn latency on every
# utterance for no benefit.
#
# ELEVENLABS_PHONE_SPEED applies to ONE kind of utterance: a phone number being
# read back to the caller, in the booking, reschedule and cancel flows alike.
# That turn is the one place where the caller has to check eleven digits against
# the number in their own hand, and where getting it wrong writes a wrong number
# to the calendar and the confirmation SMS.  Slower articulation is worth the
# extra second there and nowhere else.  Both are env-overridable so the rate can
# be tuned from the Render dashboard against a real call without a code deploy.
_SPEED_MIN, _SPEED_MAX = 0.7, 1.2


def _clamped_speed(raw: str, default: float) -> float:
    """Parse a speed from the environment, falling back to `default` on junk."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(_SPEED_MIN, min(_SPEED_MAX, value))


ELEVENLABS_SPEED       = _clamped_speed(os.getenv("ELEVENLABS_SPEED", ""), 1.0)
ELEVENLABS_PHONE_SPEED = _clamped_speed(os.getenv("ELEVENLABS_PHONE_SPEED", ""), 0.8)

# ELEVENLABS_HEAD_SPEED applies to the hold head, and to nothing else.
#
# A head is a ten-to-forty-character fragment synthesised on its own, seconds
# before the reply it belongs to. ElevenLabs flash gets no sentence around it,
# so at the call's default rate it comes out noticeably faster than the rest of
# Susie -- reported on the first live call to hear one (2026-08-29,
# CAe9eba9192c50c500c95b7e7a2a187729): "spoke too quickly compared to how Susie
# speaks". The words were right and the delivery was not.
#
# Slower here costs nothing that matters. The head exists to fill a wait that is
# already happening -- measured p50 time-to-first-audio is 1.9s and that call's
# first turn was 5.4s -- so a head that takes a quarter-second longer to say is
# a quarter-second less silence, not a quarter-second of added latency.
#
# Not 0.8: the phone-number rate is deliberately careful, an articulation the
# caller is meant to check digit by digit. A head is ordinary conversation and
# should sound like it.
ELEVENLABS_HEAD_SPEED  = _clamped_speed(os.getenv("ELEVENLABS_HEAD_SPEED", ""), 0.88)

# ---------------------------------------------------------------------------
# AssemblyAI STT constants
# ---------------------------------------------------------------------------

# v3 Universal Streaming (primary) — 16kHz PCM16 input, no upsampling on the STT side
#
# Authentication: raw API key goes in the Authorization HEADER (server-to-server).
#   ?token= is for TEMPORARY tokens from /v3/token endpoint — NOT the raw API key.
#
# Valid speech_model values (v3):
#   universal-streaming-english | universal-streaming-multilingual | whisper-rt
#   universal-3-5-pro   (Universal-3.5 Pro Realtime — see ASSEMBLYAI_USE_U35 below)
#   "slam-1" and "universal" are NOT valid v3 model names — connection rejected instantly.
#   NOTE: this comment previously listed "u3-rt-pro". That is not a name
#   AssemblyAI documents anywhere; corrected 2026-07-31 while adding U3.5.
#
# end_utterance_silence_threshold does NOT exist in v3.
#   v3 equivalent: min_turn_silence (ms, URL param).
ASSEMBLYAI_WS_URL = (
    "wss://streaming.assemblyai.com/v3/ws"
    "?speech_model=universal-streaming-english"
    "&sample_rate=16000"
    "&encoding=pcm_s16le"
    "&format_turns=false"
    # min_turn_silence: ms of silence before AssemblyAI ends the caller's turn.
    # Was 200ms — far too aggressive: any mid-sentence hesitation >200ms split
    # one spoken utterance into multiple FINALs, which fired overlapping LLM
    # turns (double responses) and interleaved the TTS chunk sequence (the
    # out-of-order stalls).  Stress test 2026-06-12: a ~0.9s pause mid-ramble
    # split "...meeting online" / "to check if mark has diagnosed..." into two
    # turns.  Raised to 800ms to capture run-on speech as a single FINAL.
    # Trade-off: more latency before the bot replies.  Tunable dial — go
    # higher (toward the v2-proven 1200) if long-pause ramblers still split;
    # lower if replies feel sluggish.
    # 2026-06-15: 800 -> 600 to shave ~200ms of dead air off every turn (latency
    # pass).  If a slow/pausing talker gets split mid-sentence, bump back to 700.
    "&min_turn_silence=600"
)

# ---------------------------------------------------------------------------
# Universal-3.5 Pro Realtime (latency-eval lever, default OFF)
# ---------------------------------------------------------------------------
# Same v3 socket, same Begin/Turn/Termination message types — only the
# speech_model and the endpointing params change, so stt_stream's receive loop
# needs no branching. Activate with ASSEMBLYAI_USE_U35=true.
#
# Why it is worth testing (vendor figures, 2026-07-31):
#   4.1% WER on AA-WER Streaming vs universal-streaming-english, ~0.4s to first
#   final, 6.99% pooled WER on Pipecat's real-agent-conversation benchmark.
#   $0.45/hr — CONFIRM the delta against current spend before any live use.
#
# BEHAVIOURAL DELTAS that matter to this codebase — read before flipping:
#
#  1. format_turns is REMOVED; formatting is always on. Finals now arrive
#     punctuated and capitalised.
#
#     🔴 CORRECTION 2026-08-04, checked against the live WebSocket API reference
#     (/docs/api-reference/streaming-api/universal-3-pro-streaming): this is
#     WRONG. `format_turns` IS accepted on the U3.5 socket and DEFAULTS TO
#     `false`, so formatting is not unconditional. The URL below now sends
#     `format_turns=false` explicitly. The reference also states unrecognised
#     query parameters are IGNORED, not rejected — so the "sending it risks a
#     rejected socket" fear was unfounded in both directions.
#     🔴 CORRECTION TO THE CORRECTION, 2026-08-04, from a LIVE CALL — the
#     original claim was closer to right. `format_turns=false` is now on the
#     socket, and finals STILL arrive formatted:
#         'um well just— i want— just want a deep tissue massage'
#     came off a socket that had requested it. So the parameter is accepted and
#     ignored for this model, or formatting is not what it gates. Either way:
#     U35_DEFORMAT is LOAD-BEARING, not belt-and-braces. Do not turn it off, and
#     do not trust the API reference over a transcript.
#     (That em-dash breaks name extraction outright — see _U35_PUNCT_RE.)
#
#     U35_DEFORMAT stays ON regardless: it is a no-op on unformatted text, and
#     it is the only thing standing between us and the three breakages below if
#     the vendor default ever moves.
#     Two other figures below are also stale against that reference: the vendor
#     max_turn_silence default is 1536ms (not 1000) and vad_threshold 0.2 (not
#     0.3), and end_of_turn_confidence_threshold still documents a 0.4 default
#     rather than reading as deprecated. None are load-bearing here — we set our
#     own endpointing — but do not cite the numbers in delta 3 as vendor truth.
#
#     A first audit checked only the two normalising
#     consumers (clinical_screening._norm(), fast_path._normalize()) and wrongly
#     concluded nothing else cared. A full sweep on 2026-07-31 found three
#     consumers that read the transcript RAW and break on punctuation:
#       - connection.py:411 _PHONE_NUMBER_RE ^\d{5,}$ — "07502211207." fails the
#         match, so the number falls through to _is_short_meaningless_fragment()
#         and is DISCARDED. Silent loss of the caller's phone number.
#       - flow.py:1450-1461 _NAME_WRAPPER_PATTERNS — "My name is." no longer
#         matches ^my name(?:\s+is)?$, so the label is stored AS the name.
#       - name_collector.py:406 _NAME_AFTER_IS_RE — anchored \s*$, stops firing.
#     Rather than patch each anchor (and every future one), U35_DEFORMAT below
#     restores the exact text contract the engine was written against. See
#     stt_stream._deformat_transcript().
#     The COMMENT at clinical_screening.py ~L616 ("no final is ever punctuated")
#     stays true while U35_DEFORMAT is on, and is false if it is turned off.
#  2. Partials become stable and fully-transcribed rather than word-by-word.
#     connection.py's barge-in noise gate (_BARGE_NOISE, single-word reject) is
#     tuned against word-by-word partials — re-check barge-in feel first.
#  3. Turn detection is punctuation-based, not confidence-based, and the vendor
#     defaults move (min 400->100, max 1280->1000, vad 0.4->0.3). Our 600ms was
#     hand-tuned against the OLD endpointer, so it is NOT carried over blind:
#     the values below are separate and env-sweepable. Do not assume the knee
#     is in the same place.
#  4. end_of_turn_confidence_threshold is deprecated (already unused here — see
#     the WS-C note below), `language` is replaced by native code-switching,
#     and turn_is_formatted is gone (not read anywhere in this repo).
#
# NOT yet wired: the new `prompt` param (conversation context injectable at
# connect and refreshable after each agent turn with no reconnect) and
# mid-stream keyterms_prompt updates. Both are follow-ups — build_keyterms()
# currently fires once at connection time.
ASSEMBLYAI_USE_U35 = os.getenv(
    "ASSEMBLYAI_USE_U35", "false"
).strip().lower() in ("true", "1", "yes", "on")

# Endpointing for U3.5, deliberately independent of the 600ms tuned for the old
# model. Defaults start AT our current conversation values rather than the
# vendor defaults, so the first A/B changes ONE variable (the model) and not two.
U35_MIN_TURN_SILENCE = int(os.getenv("U35_MIN_TURN_SILENCE", "600"))
U35_MAX_TURN_SILENCE = int(os.getenv("U35_MAX_TURN_SILENCE", "1280"))

# U3.5 cannot turn formatting off, so we turn it off on our side of the socket:
# lowercase + strip terminal/interior punctuation, restoring byte-for-byte the
# shape every downstream matcher in this engine was written against (see the
# three raw-transcript consumers listed above).
#
# Default ON, and it should stay on for the first A/B: the point of that test is
# to vary the acoustic model, not the text contract. Set U35_DEFORMAT=false only
# to deliberately evaluate punctuation as a truncation signal — and expect the
# phone-number and name-wrapper regressions above until they are fixed properly.
# Inert unless ASSEMBLYAI_USE_U35 is also on.
U35_DEFORMAT = os.getenv(
    "U35_DEFORMAT", "true"
).strip().lower() in ("true", "1", "yes", "on")

ASSEMBLYAI_WS_URL_U35 = (
    "wss://streaming.assemblyai.com/v3/ws"
    "?speech_model=universal-3-5-pro"
    "&sample_rate=16000"
    "&encoding=pcm_s16le"
    # format_turns=false — see the CORRECTION note above delta 1. Asking for the
    # unformatted contract at the socket is strictly better than undoing
    # formatting afterwards; U35_DEFORMAT stays on behind it as defence in depth.
    "&format_turns=false"
    f"&min_turn_silence={U35_MIN_TURN_SILENCE}"
    f"&max_turn_silence={U35_MAX_TURN_SILENCE}"
)

# v2 fallback — 8kHz input, no upsampling needed (battle-tested, older)
# Activate with ASSEMBLYAI_USE_V2=true
ASSEMBLYAI_USE_V2 = os.getenv("ASSEMBLYAI_USE_V2", "false").lower() == "true"
ASSEMBLYAI_WS_URL_V2 = (
    "wss://api.assemblyai.com/v2/realtime/ws"
    "?sample_rate=8000"
    "&end_utterance_silence_threshold=1200"
)


def assemblyai_ws_url() -> str:
    """Return the STT socket URL for the active flag combination.

    Precedence is V2 > U3.5 > default, deliberately: ASSEMBLYAI_USE_V2 is the
    break-glass fallback to the battle-tested 8kHz socket, so if someone sets it
    during an incident it must win even if the U3.5 lever was left on.
    """
    if ASSEMBLYAI_USE_V2:
        return ASSEMBLYAI_WS_URL_V2
    if ASSEMBLYAI_USE_U35:
        return ASSEMBLYAI_WS_URL_U35
    return ASSEMBLYAI_WS_URL

# ---------------------------------------------------------------------------
# Claude model constants
# ---------------------------------------------------------------------------

# Primary LLM (tool calling, booking, complex turns)
SONNET = "claude-sonnet-4-6"

# Fast LLM (simple turns after fast/info tools: collect_and_store, get_clinic_info)
HAIKU = "claude-haiku-4-5-20251001"

# ── Booking-affirmation classifier (L2) ─────────────────────────────────────
# The book/reschedule gates settle clear yes/no answers deterministically (L1)
# and hand only the ambiguous middle to Haiku. Default ON: with it OFF an
# unsettled reply blocks and re-asks, which is the pre-1-Aug-2026 behaviour and
# the reason CA7e389a47 lost a booking ("go for it" matched nothing).
# Set BOOK_CLASSIFIER_ENABLED=false to disable without a redeploy if the
# classifier misbehaves live — L1 alone is still strictly better than before,
# because it is what blocks "don't book it" and "yes but make it Friday".
BOOK_CLASSIFIER_ENABLED = os.getenv(
    "BOOK_CLASSIFIER_ENABLED", "true"
).lower() == "true"
# Short and explicit. The write-ack filler is already playing when the gate runs
# (measured 1.25s ahead of it), so this hides under audio; an unbounded wait
# here would be dead air at the exact moment the caller expects to be booked.
BOOK_CLASSIFIER_TIMEOUT_S = float(os.getenv("BOOK_CLASSIFIER_TIMEOUT_S", "1.5"))

# Backwards-compatible aliases matching realtime.py naming
CLAUDE_MODEL       = SONNET
CLAUDE_MAX_TOKENS  = 1024
CLAUDE_TEMPERATURE = 0.4

# Maximum tool-calling iterations per LLM turn (prevents infinite loops)
MAX_TOOL_ITERATIONS = 6

# Session key: the previous iteration blocked a tool call and told the model IN
# THE TOOL RESULT to speak instead. Set at the block, read once at the top of
# the next iteration, which sends tool_choice={"type": "none"} so the model
# structurally cannot call a tool again.
#
# CAd34a122247 (Vital Edge, 2026-08-08) is why this is a request parameter and
# not stronger wording. check_availability was blocked with a message that opens
# "Do NOT call check_availability. Produce the booking summary now" — and the
# model called it again anyway, on two separate turns. That is not the model
# ignoring an instruction: a tool result carrying `"error"` reads as a FAILED
# call, and retrying a failed call is the correct default behaviour. The
# instruction and the frame it arrives in say opposite things, and the frame
# wins. Each ignored retry costs one full model round trip — ~2.3s measured —
# so the two turns took 7.05s and 8.68s against ~2.3s for every single-iteration
# turn in the same call.
#
# tool_choice removes the choice instead of arguing with it.
FORCE_TEXT_NEXT_ITERATION = "_force_text_next_iteration"

# Maximum conversation history turns kept in session (each turn = 2 entries)
MAX_HISTORY_TURNS = 10

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

# WS-A (latency-eval) — first-chunk fast-emit lever.
# Replaces the old dead `MIN_CHUNK_WORDS = 8`, which was imported by nobody
# (the live chunker hardcodes MIN_WORDS=15). This retires that dead-constant
# class of bug: these values are actually read (passed into ResponseChunker
# from llm_stream). Default OFF => the chunker behaves byte-identically to live.
# WS_A_MIN_WORDS_FIRST is only consulted when the flag is ON, so the threshold
# (4/5/6/8) can be swept from the env without redeploying code.
# Do NOT port this default-ON to main/theorem/jv live branches.
WS_A_FAST_FIRST_CHUNK = os.getenv(
    "WS_A_FAST_FIRST_CHUNK", "false"
).strip().lower() in ("true", "1", "yes", "on")
WS_A_MIN_WORDS_FIRST = int(os.getenv("WS_A_MIN_WORDS_FIRST", "6"))

# ---------------------------------------------------------------------------
# WS-C (latency-eval) — phase-aware endpointing + capture-phase HARD GATE
# ---------------------------------------------------------------------------
# RC-1 (2026-07-19): AssemblyAI ended a name turn ~41ms after the last partial
# (endpoint_wait_ms=41) and a hesitant name split into a garbage 'n'. The 600ms
# min_turn_silence is one global guess — too tight for someone spelling a name
# or reading a number, too slack for a crisp "yes". WS-C makes it PHASE-AWARE:
# it raises AssemblyAI's silence thresholds during name/phone capture (via a
# mid-session UpdateConfiguration message — v3 supports this without a
# reconnect) and restores them afterwards.
#
# API note (verified 2026-07-19, universal-streaming-english): the original
# LATENCY_WS-C.md §3 plan keyed on `end_of_turn_confidence_threshold`, but that
# param is now DEPRECATED on Universal Streaming — AssemblyAI directs you to
# `min_turn_silence` / `max_turn_silence` instead. So this lever is
# silence-based, not confidence-based. Both are mid-session updatable.
#
# Default OFF => nothing is ever sent mid-session and the URL keeps
# min_turn_silence=600, so the branch is byte-behaviour-identical to live.
WS_C_SEMANTIC_ENDPOINT = os.getenv(
    "WS_C_SEMANTIC_ENDPOINT", "false"
).strip().lower() in ("true", "1", "yes", "on")

# Per-phase silence profiles in ms, env-sweepable so the knee is found without a
# redeploy. Conversation defaults match today's effective config (600ms floor,
# AssemblyAI's 1280ms max) so turning the lever ON changes ONLY the capture
# phases — it never makes a conversation turn more aggressive than live.
WS_C_CONV_MIN_SILENCE = int(os.getenv("WS_C_CONV_MIN_SILENCE", "600"))
WS_C_CONV_MAX_SILENCE = int(os.getenv("WS_C_CONV_MAX_SILENCE", "1280"))
WS_C_CAP_MIN_SILENCE  = int(os.getenv("WS_C_CAP_MIN_SILENCE",  "800"))
WS_C_CAP_MAX_SILENCE  = int(os.getenv("WS_C_CAP_MAX_SILENCE",  "1600"))


def ws_c_profile_for_phase(phase: str):
    """Return (min_turn_silence, max_turn_silence) in ms for a capture_phase, or
    ``None`` when the lever is OFF. Pure — safe to call regardless of the flag.

    HARD GATE (LATENCY_WS-C §3.3): a name/phone capture turn must NEVER be more
    aggressive than a conversation turn. The capture min/max are therefore
    floored at the conversation values, so even a misconfigured env (CAP < CONV)
    cannot let the endpointer clip a spelled name or read-out number to save
    latency. Latency is always secondary to not-clipping in capture.
    """
    if not WS_C_SEMANTIC_ENDPOINT:
        return None
    conv_min = WS_C_CONV_MIN_SILENCE
    conv_max = max(WS_C_CONV_MAX_SILENCE, conv_min)
    if phase in ("name", "phone"):
        cap_min = max(WS_C_CAP_MIN_SILENCE, conv_min)   # hard-gate floor
        cap_max = max(WS_C_CAP_MAX_SILENCE, cap_min)
        return (cap_min, cap_max)
    return (conv_min, conv_max)

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

# How long to wait for first LLM text chunk before playing a filler phrase.
# 1800ms: outlier latency-mask. Normal turns get their first token in ~400-700ms
# (cached prompt) and cancel this, so it NEVER fires on a normal turn — only when
# Sonnet stalls past the normal ceiling (cold start / Anthropic retry / overload),
# i.e. the "some turns take too long" spikes. Kept well above normal TTFT so it
# stays rare and never feels robotic. Phrases must be context-neutral (they are).
# Was 6000 (so slow turns sat in dead air up to 6s before any audio).
# Measured on 73 turns of live traffic, 21-22 Aug 2026: llm_ttft p50 = 1734ms,
# p90 = 3116ms. At 1800ms this fired on 42% of ALL turns — i.e. just past the
# median, so it was interrupting normal conversation, and by then the caller had
# already sat through ~1125ms of endpointer silence plus the wait itself. A hold
# phrase that arrives after the dead air does not cover it, it just adds words.
#
# The measured knee is 3500ms (8% of turns). The BAR is 3.0s - CLAUDE.md section
# 6, "no dead air over 3s without a filler or acknowledgement" - and a regression
# test pins it. The bar wins: 3000ms fires on 12% of turns instead of 42%, which
# is most of the win, and raising the bar is the owner's call, not a side effect
# of a latency change.
#
# Worth stating plainly, because it will come up again: the bar and "do not talk
# over a normal turn" are in genuine tension right now. A caller has already
# waited ~1125ms in the endpointer before this timer even starts, so 3000ms here
# is ~4.1s of real silence - the bar is already being missed on the caller's
# clock, and moving this number cannot fix that. Only cutting the endpointer
# wait and the 1656ms first-token time can.
LLM_FIRST_CHUNK_TIMEOUT_MS = 3000

# How long to wait before speaking a SITUATIONAL head -- one chosen from what
# the caller just asked for rather than from the work in flight.
#
# The 3000ms above is the price of a GUESS. A contentless head ("Still with you
# -") can only be justified once the caller has waited long enough that
# acknowledging the wait is the honest thing, which is ~8% of turns; speaking it
# earlier put an empty marker in front of instant replies, and the model opens
# with the same marker, so the caller heard "Right. Right, what's...".
#
# A head built from the caller's own words is not a guess. "On price -" is
# correct the moment they ask the price, however fast the answer comes, so it
# does not have to earn its place by waiting. Measured over the 753-call obs
# corpus, turn time-to-first-audio is p50 1,938ms and p25 1,390ms: at 600ms the
# head lands in front of most replies rather than only the slow tail, and a turn
# that answers faster than this cancels the task having said nothing.
#
# Do NOT lower this to zero. It is what stops a head on a turn the fast path
# was going to answer immediately.
HOLD_HEAD_DELAY_MS = 600

# How long to wait for a TTS chunk to complete before moving to the next
TTS_CHUNK_TIMEOUT_MS = 3000

# Filler cooldown: minimum seconds between filler phrases ("Just one moment...")
# 8s (was 20): with the 1800ms outlier trigger, fillers are already rare; a
# shorter cooldown lets a cluster of slow turns (e.g. an Anthropic bad minute
# with several retries) each get masked instead of leaving the 2nd+ in dead air.
# Still long enough that a normal cadence never stacks fillers.
LLM_FILLER_COOLDOWN_SEC = 8.0

# When the re-armed (second) hold phrase may speak, and why it is now measured
# from the TURN rather than from the first phrase.
#
# B-19: the filler was one-shot — the background task fired once and ended, so a
# 14s upstream spike produced one phrase and ~12s of bare silence. That breaks
# the CLAUDE.md §6 bar of "no dead air over 3s" on exactly the turns the filler
# exists to cover. So a re-arm must exist. It is deliberately NOT a loop: owner
# decision 2026-08-03, three or four phrases on a slow turn sounds anxious.
#
# What changed 2026-09-01, and it is a correction rather than a new opinion.
# The delay used to be 5000ms measured FROM THE FIRST PHRASE. dc6f521e then
# moved the situational head from 3000ms to 600ms and did not touch this
# number, so the second phrase silently slid from firing at 8.0s to firing at
# 5.6s. Measured over 294 turns in the obs corpus:
#
#     turns over 8.0s   4.8%      <- the old effective trigger
#     turns over 5.6s  13.9%      <- what it became, unnoticed
#
# It tripled the rate as a by-product of an unrelated commit. Five of the ten
# "Still with you —" emissions in the 57 calls since that change landed on top
# of another head, which the owner heard on CAc119b8838f556ac2 as
# "Sorry to hear that —" … "Still with you —" and reported as sounding off.
#
# Two constants instead of one, because the bug was a single relative number
# that no longer meant what it said:
#
#   STALL_MS is ABSOLUTE, from LLM dispatch. It cannot drift when the head
#   timing moves again. 10s is the knee in the corpus — over 5.6s is 13.9% of
#   turns, over 10s is 2.0% — and it is the point where silence is
#   unambiguously the worse fault rather than a judgement call.
#
#   MIN_GAP_MS is the structural guard that makes stacking unrepresentable
#   whatever the other numbers become. In practice it never binds (a 600ms head
#   is 9.4s clear of the 10s deadline); it exists so that the next timing change
#   cannot recreate this defect the way dc6f521e did.
LLM_FILLER_SECOND_STALL_MS = 10000
LLM_FILLER_SECOND_MIN_GAP_MS = 4000

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

# Played while LLM is generating (if first chunk exceeds LLM_FIRST_CHUNK_TIMEOUT_MS).
# Multiple phrases — llm_stream picks one at random each turn.
#
# 2026-08-05, owner instruction: these fire on ANY slow turn, including the
# cancel and reschedule turns, and a clipped "Just a second…" during a
# cancellation reads as being made to wait rather than being helped. Reworded to
# carry a little warmth without getting longer — length matters here, because
# this plays exactly when the turn is already late.
#
# "Just a second…" is dropped outright: it is the phrase the owner named, and it
# is also the closest of the four to `SILENCE_RULE`'s banned "just a moment".
FILLER_PHRASES = [
    "Just getting that for you…",
    "Right with you…",
    "One moment…",
    "Let me just check that…",
]
# Keep the singular alias so any other import of FILLER_PHRASE still compiles.
FILLER_PHRASE = FILLER_PHRASES[0]

# Prefix marker prepended to FILLER_PHRASE by the background filler task in
# _stream_one_claude_turn().  _tts_loop strips the marker and — if a tool call
# has since cancelled the ack filler — discards the chunk silently rather than
# playing it on top of the tool-call filler.  Using a marker (rather than
# string comparison) means the suppression is exact and cannot false-positive
# on genuine LLM text that happens to echo the same phrase.
ACK_FILLER_MARKER = "\x01ACK_FILLER\x01"

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

# PCM buffer flush threshold for AssemblyAI (1 frame = 20ms)
# Reduced from 3 frames (60ms) to 1 frame (20ms) — saves 40ms STT latency.
# AssemblyAI v3 streaming accepts any chunk size; no minimum frame requirement.
# v3 requires 16kHz PCM16: 640 bytes/frame -> flush at  640 bytes
# v2 requires  8kHz PCM16: 320 bytes/frame -> flush at  320 bytes
PCM_FLUSH_FRAMES = 1
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
# Marker text: when session["last_question"] equals this value, the stored
# prompt is declarative (e.g. deterministic FAQ answer) and the no-input
# watchdog MUST NOT arm/fire against it. Any subsequent write of different
# text to last_question naturally invalidates the marker (text differs) and
# restores default watchdog eligibility — so existing write sites need no
# changes. Only write sites that intentionally store declarative / answer
# text update this marker (via _store_last_question(..., watchdog_eligible=False)).
F_LAST_QUESTION_NOT_REASKABLE = "_last_question_not_reaskable"

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
    # B-09: shared anchors. Computed inline here until 3 Aug 2026 and seven days
    # late on Sundays, in lockstep with two other copies.
    from app.date_context import week_anchors as _week_anchors
    _anchors         = _week_anchors(_now.date())
    _this_sunday     = _anchors.this_sunday
    _next_monday     = _anchors.next_monday
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
