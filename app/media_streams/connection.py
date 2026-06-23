# app/media_streams/connection.py
"""
Twilio Media Streams WebSocket connection handler.

Manages the full lifecycle of a single Twilio call:
  - Receives JSON events from Twilio (connected / start / media / stop)
  - Coordinates the pipeline: STT -> LLM -> TTS -> audio output
  - Runs all pipeline coroutines concurrently via asyncio.gather
  - Handles graceful shutdown and error recovery

WebSocket message protocol (Twilio -> server):
  {"event": "connected", "protocol": "Call", "version": "1.0.0"}
  {"event": "start",     "streamSid": "...", "start": {"callSid": "...", ...}}
  {"event": "media",     "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "stop",      "streamSid": "...", "stop":  {"callSid": "..."}}

WebSocket message protocol (server -> Twilio):
  {"event": "media",  "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "clear",  "streamSid": "..."}  <- drains Twilio's audio buffer on barge-in

Pipeline queues (all asyncio.Queue, unbounded):
  audio_in_queue    : raw mulaw bytes from Twilio          -> AudioInputProcessor
  stt_input_queue   : PCM16 16kHz bytes (converted)        -> STTStream
  transcript_queue  : completed utterance strings          -> LLM loop
  tts_text_queue    : text chunks to synthesise            -> TTS loop
  audio_out_queue   : base64-encoded mulaw strings         -> send_loop -> Twilio

Error handling contract:
  - Silence re-ask: SilenceHandler fires after 4s of caller silence, re-asks
    the last question up to 2 times; 3rd silence triggers transfer
  - Pipeline failures: each component has fallback phrases; complete failure plays
    pre-recorded message then closes cleanly
  - Unstable call tracking: if call never completes one STT->LLM->TTS cycle,
    logs "UNSTABLE CALL" for monitoring
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
import traceback
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

import anthropic

import random as _random

from .config import (
    TWILIO_STARTED_TIMEOUT_SEC,
    PIPELINE_FAILURE_PHRASE,
    CLAUDE_ERROR_PHRASE,
    BOOKING_OPEN,
    BARGE_IN_THRESHOLD_MS,
    ANTHROPIC_API_KEY,
    HAIKU,
    ACK_FILLER_MARKER,
    FILLER_PHRASES,
)
from .filler_guard import FillerGuard

# Pre-slot TTS cancellation marker — mirrors PRE_SLOT_MARKER in llm_stream.py.
# Defined here independently so connection.py does not import from llm_stream
# at module level (llm_stream is imported lazily inside handlers to avoid
# circular imports).
PRE_SLOT_MARKER: str = "\x01PRE_SLOT\x01"

# µ-law 8kHz silence injected after a filler clip plays, so the LLM response
# doesn't start abruptly.  0x7F = mid-scale µ-law ≈ silence; 800 samples = 100ms.
_SILENCE_100MS: bytes = bytes([0x7F] * 800)

# ---------------------------------------------------------------------------
# Barge-in constants
# ---------------------------------------------------------------------------

_BARGE_IN_THRESHOLD_S: float = BARGE_IN_THRESHOLD_MS / 1000.0

# Phrases spoken after a confirmed barge-in (selected at random).
_BARGE_IN_ACKS: List[str] = [
    "Sorry — go ahead.",
    "Yes, go on.",
    "Sorry about that — you were saying?",
]
from .session import (
    get_or_create_session,
    save_session,
)
from .audio_in import AudioInputProcessor
from .audio_out import AudioOutputProcessor
from .stt_stream import STTStream

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain_queue(q: asyncio.Queue) -> int:
    """Remove all items from an asyncio.Queue without blocking. Returns item count."""
    count = 0
    while True:
        try:
            q.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            break
    return count


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _update_soft_context(session: dict, user_text: str, bot_text: str) -> None:
    """
    Use Haiku to extract caller context signals from a single turn and merge
    them into session["soft_context"].  Existing non-None values are never
    overwritten — the first reliable signal for each key wins.

    Keys extracted: time_preference, location_preference, condition_notes,
                    emotional_state, name, service, is_returning, insurer.

    theorem_v3 only — called via asyncio.create_task() from the free-form
    loop. Never raises; all errors are swallowed and debug-logged so a bad
    Haiku call cannot break a live call.
    """
    call_sid = session.get("call_sid", "")
    soft = session.setdefault("soft_context", {})

    null_keys = [k for k, v in soft.items() if v is None]
    if not null_keys:
        return  # Nothing left to fill in

    system_prompt = (
        "You extract caller context signals from a single conversation turn. "
        "Return ONLY a JSON object with the keys listed below. "
        "For each key, return the extracted value as a concise string, "
        "or null if the turn contains no clear signal for that key. "
        "Never invent information; only use what is explicitly stated or "
        "strongly implied.\n\n"
        f"Keys to extract: {', '.join(null_keys)}\n\n"
        "Definitions:\n"
        "  time_preference   – preferred appointment time/day (e.g. 'evenings', 'Monday mornings')\n"
        "  location_preference – preferred clinic branch or area\n"
        "  condition_notes   – brief description of the caller's complaint or condition\n"
        "  emotional_state   – caller's apparent emotional state (e.g. 'anxious', 'calm')\n"
        "  name              – caller's first name or full name\n"
        "  service           – the treatment or service they want to book\n"
        "  is_returning      – 'yes' if they mention being a returning patient, 'no' if new\n"
        "  insurer           – health insurance provider name if mentioned\n\n"
        "Return exactly one JSON object, no markdown, no extra keys."
    )

    user_message = (
        f"Caller said: {user_text!r}\n"
        f"Bot replied: {bot_text!r}"
    )

    try:
        # Read key at call time so it picks up whatever load_dotenv() set in
        # os.environ, even if config.ANTHROPIC_API_KEY was evaluated before
        # dotenv loaded (test contexts).
        import os as _os
        api_key = _os.environ.get("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=2.0)
        response = await client.messages.create(
            model=HAIKU,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if the model wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            if "```" in raw:
                raw = raw[: raw.index("```")]
        extracted: dict = json.loads(raw.strip())
    except Exception:
        logger.debug(
            "soft_context extraction failed for %s",
            call_sid,
            exc_info=True,
        )
        return

    changed = False
    for key in null_keys:
        value = extracted.get(key)
        if value is not None and soft.get(key) is None:
            soft[key] = value
            changed = True

    if changed:
        try:
            await save_session(call_sid, session)
        except Exception:
            logger.debug("save_session failed after soft_context update for %s", call_sid)


# ---------------------------------------------------------------------------
# Tail-fragment suppression
# ---------------------------------------------------------------------------

# How long (seconds) after a completed turn during which a tiny trailing
# STT final is considered a residual fragment of the same speech event.
_TAIL_FRAGMENT_WINDOW: float = 2.0

# Short utterances that are always legitimate booking answers — never
# suppressed even when they arrive within the tail-fragment window.
_TAIL_FRAGMENT_SAFE: frozenset = frozenset({
    "no", "yes", "yep", "yup", "nah", "nope",
    "ok", "okay",
    "hi", "hey",
    "am", "pm",
    "one", "two",
})


# ---------------------------------------------------------------------------
# theorem_v3 noise-fragment filter  (SPEC 3 / Bug 5)
# ---------------------------------------------------------------------------
# All four conditions apply to SINGLE-WORD transcripts only.
# Multi-word transcripts are NEVER discarded by this filter.

# Single-character words that are legitimate English and must never trigger
# the single_char_word discard — even in 2-word fragments.
# 'i' → "I believe", "I think", "I did", etc.
# 'a' → "a moment", "a bit", etc.
_SINGLE_CHAR_PRONOUNS: frozenset = frozenset({'i', 'a'})

# Condition 3 — production STT noise fragments seen on live calls.
# Single-word transcripts that exactly match any of these are silently
# dropped regardless of length.
_V3_NOISE_FRAGMENTS: frozenset = frozenset({
    # spec-mandated list (production observations)
    "ing", "ic", "terday", "reckon", "s", "er", "um", "uh",
    # additional mouth-noise / stutter artefacts
    "hmm", "hm", "mm", "ah", "eh", "mhm", "mmm", "uhh", "umm", "huh",
})

# PRESERVE_LIST — single-word transcripts that must ALWAYS pass through
# regardless of any noise heuristic (length, vowels, noise list, rapid).
_V3_PRESERVE: frozenset = frozenset({
    # spec-mandated list
    "yes", "no", "hi", "ok", "yeah", "nope", "sure", "fine", "good", "great",
    # additional meaningful short words
    "okay", "yep", "yup", "nah", "bye",
    # spoken digit slot-selection words (callers say "one", "two", etc.)
    "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
})

# SCHEDULING_SINGLES — single words that carry genuine scheduling intent and
# must always reach the LLM.  A single-word transcript that survived the noise
# filter above but is NOT in this set is re-armed to the silence timer instead
# of dispatching a wasted LLM call (CODE SPEC G).
_SCHEDULING_SINGLES: frozenset = frozenset({
    # Affirmatives / negatives
    "yes", "no", "yeah", "nope", "yep", "yup", "sure", "ok", "okay", "fine",
    "good", "great", "nah",
    # Time of day
    "mornings", "afternoons", "evenings", "morning", "afternoon", "evening",
    # Day names
    "monday", "tuesday", "wednesday", "thursday", "friday",
    # Numbers (spoken)
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve",
    # Digit strings
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
    # Greetings / presence signals (must reach LLM)
    "hello", "hi",
    # End-of-call signal
    "bye",
})

# Words that carry slot-selection meaning.  A transcript must contain at least
# one of these (after lower-casing) to be treated as a genuine slot selection
# attempt when v3_awaiting_slot_selection is active.  Phrases that happen to
# arrive in the slot window but carry NO slot signal (e.g. "with me", "suits
# me", "that one", "yes please") will re-arm silence instead of dispatching LLM.
_SLOT_SIGNALS: frozenset = frozenset({
    # Digit strings
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
    # Spoken numbers
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve",
    # Day names
    "monday", "tuesday", "wednesday", "thursday", "friday",
    # Ordinals used as slot references ("first one", "the third")
    "first", "second", "third", "last",
    # Part-of-day / clock references. With multi_day as the default, each day is
    # presented as e.g. "nine in the morning or five in the evening", so callers
    # routinely pick by part of day ("the afternoon one", "in the morning").
    # Treat these as slot-selection candidates so the utterance reaches the LLM
    # (which resolves it against the offered times, and clarifies when two slots
    # share a band) instead of being discarded as a meaningless fragment — the
    # cause of a caller having to repeat a clear pick several times (2026-06-17).
    "morning", "afternoon", "evening", "midday", "noon", "o'clock",
})


def _is_slot_selection_candidate(transcript: str) -> bool:
    """Return True if *transcript* contains at least one slot-signal word.

    Hard constraints (per Spec H):
    - 'with me', 'suits me', 'that one', 'yes please' → False (re-arm)
    - 'number three', 'thursday', 'the 21st', 'first one' → True (proceed)
    """
    words = transcript.lower().split()
    return any(w in _SLOT_SIGNALS for w in words)


# Spec J — phrases that indicate the LLM has asked for the patient's name.
# Checked against the full (untruncated) assistant turn via substring match.
_NAME_REQUEST_PHRASES: tuple = (
    "could i get your first name",
    "could i take your first name",
    "what's your first name",
    "what is your first name",
    "could i get your name",
    "first name",
)

# Spec J — short confirming responses the patient may give AFTER the system
# has confirmed a slot and asked for a name.  These carry no slot-signal word
# and would be rejected by the Spec H guard if post_slot_confirmation_pending
# were not checked first.
_POST_SLOT_CONFIRMATION_PHRASES: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "fine",
    "perfect", "great", "good", "sounds good", "that works",
    "that works best", "that suits me", "that suits", "suits me",
    "works for me", "works", "brilliant", "lovely", "fantastic",
})


def _is_post_slot_confirmation(transcript: str) -> bool:
    """Return True if *transcript* is a post-slot-confirmation phrase.

    Passes  : 'yes', 'perfect', 'sounds good', 'that works best'
    Fails   : 'actually wednesday', 'number three', 'with me'
    Strategy: exact membership OR startswith — handles "sounds good thanks".
    """
    t = transcript.lower().strip()
    return t in _POST_SLOT_CONFIRMATION_PHRASES or any(
        t.startswith(p) for p in _POST_SLOT_CONFIRMATION_PHRASES
    )


# Words whose presence in a transcript makes it communicative — i.e. the caller
# is expressing intent and the LLM must hear it, regardless of whether it
# contains a slot-signal word.  Used by _is_short_meaningless_fragment to gate
# the re-arm path inside the Spec H slot guard.
_COMMUNICATIVE_WORDS: frozenset = frozenset({
    "no", "not", "none", "never",
    "yes", "yeah", "please",
    "want", "need", "like", "have", "got",
    "can", "could", "would",
    "what", "when", "where", "which", "how", "why",
})


# Matches a single token of 5+ consecutive digits — any phone number or
# fragment thereof (07502211207, 07502, 11207 all match; 1120 does not).
# Must never be suppressed by any noise or single-word filter.
_PHONE_NUMBER_RE = re.compile(r"^\d{5,}$")


def _is_short_meaningless_fragment(transcript: str) -> bool:
    """Return True only when the transcript is safe to re-arm and discard.

    Hard constraints:
    - Single token of 5+ digits (phone number / fragment) → always False
    - 4+ words  → always False (LLM must hear it)
    - Any communicative word → always False (LLM must hear it)
    - Only True when: ≤3 words AND no communicative word AND not a phone number

    Examples:
    - '07502211207'         → False (phone number — LLM)
    - '07502'               → False (phone fragment 5 digits — LLM)
    - 'with me'             → True  (2 words, no communicative word — re-arm)
    - 'suits me'            → True  (2 words, no communicative word — re-arm)
    - 'no'                  → False ('no' is communicative — LLM)
    - 'no none of those'    → False (has 'no', 'none' — LLM)
    - 'actually'            → True  (1 word, not communicative — re-arm)
    - 'that one please'     → False ('please' is communicative — LLM)
    """
    words = transcript.strip().split()
    # Phone number / fragment — never suppress
    if len(words) == 1 and _PHONE_NUMBER_RE.match(words[0]):
        return False
    if len(words) > 3:
        return False
    return not any(w in _COMMUNICATIVE_WORDS for w in [w.lower() for w in words])


# CODE SPEC AJ — non-specific affirmations during DAY_SELECTION.
# Patient confirms something works but doesn't name which day.
_NON_SPECIFIC_SLOT_AFFIRMATIONS: frozenset = frozenset({
    "suits me", "any of them", "any of those", "that works",
    "fine with me", "any is fine", "any is good", "whatever",
    "any of those suit me", "they all work", "all good",
    "anytime", "any", "fine", "good", "okay", "ok",
    "that works for me", "works for me", "all fine", "all work",
    "either", "either works", "either of those", "both fine",
    "sounds good", "sounds fine", "any would work", "any works",
})


def _is_non_specific_slot_affirmation(transcript: str) -> bool:
    return transcript.lower().strip() in _NON_SPECIFIC_SLOT_AFFIRMATIONS


# Open-availability signals — used to detect rapid-continuation transcripts
# that express no-preference / any-time availability AFTER slots have already
# been presented.  When v3_awaiting_slot_selection is True and the new
# transcript matches one of these, the LLM call is suppressed: the slots
# already presented are the correct answer and re-running check_availability
# would produce a duplicate slot list.
_OPEN_AVAILABILITY_SIGNALS: frozenset = frozenset({
    "anytime", "any time", "any day",
    "free", "free all week", "free this week",
    "flexible", "doesn't matter", "don't mind",
    "don't really mind", "not really mind",
    "no preference", "whenever",
    "happy with anything", "not fussed",
    "either", "both", "all week",
    "i'm free", "im free",
    "whatever works", "whatever you have",
    "doesn't matter to me", "not bothered",
})


# Rejection / alternative-request signals.  When the caller REJECTS the
# presented slots or asks for DIFFERENT ones ("no, anything else?", "any
# others?", "a different day"), that is the OPPOSITE of a redundant open-
# availability repeat — it must reach the LLM (which re-runs check_availability
# for another day/week), never be suppressed.  Without this, the substring
# match on "any" inside "anything"/"any chance" mis-fired the open-availability
# suppression guard → dead air → caller hung up (abandoned call 2026-06-15).
# Rejection words are matched per-token (word boundary) so "no" does not match
# inside "another"/"now"; the alternative signals are distinctive enough for a
# substring test.
_SLOT_REJECTION_WORDS: frozenset = frozenset({
    "no", "nope", "nah", "not", "none", "don't", "dont",
    "doesn't", "doesnt", "won't", "wont", "can't", "cant",
})
_SLOT_ALTERNATIVE_SIGNALS: tuple = (
    "else", "other", "another", "different", "instead", "rather",
)


def _is_slot_rejection_or_alternative(text: str) -> bool:
    """Return True if *text* rejects the offered slots or requests alternatives.

    Used to EXEMPT such utterances from the open-availability suppression guard:
    "no, have you got anything else?" must reach the LLM, not be silenced.
    """
    t = text.lower()
    return (
        any(w in _SLOT_REJECTION_WORDS for w in t.split())
        or any(s in t for s in _SLOT_ALTERNATIVE_SIGNALS)
    )


def _is_open_availability_utterance(text: str) -> bool:
    """Return True if *text* is an open-availability / no-preference phrase.

    Uses substring matching (not exact) so 'i don't really mind to be honest'
    and 'i'm free all week to be honest' are caught alongside shorter forms.
    """
    t = text.lower().strip()
    return any(s in t for s in _OPEN_AVAILABILITY_SIGNALS)


def _extract_time_preference(text: str) -> "str | None":
    """Extract an explicit time-of-day preference from *text*.

    Returns 'mornings', 'afternoons', 'evenings', 'any', or None.
    Designed to fire on embedded preferences such as
    'anytime next week afternoons please' as well as standalone
    answers to the 'mornings or afternoons?' question.

    Guards against false-positives from call-opening greetings
    ('good morning', 'morning') which are ≤2-word forms containing
    only greeting words and must not be treated as slot preferences.
    """
    t = text.lower().strip()
    words = t.split()
    # Short greeting — not a scheduling preference.
    if len(words) <= 2 and all(
        w.rstrip("!?,") in ("good", "morning", "hi", "hello", "hey")
        for w in words
    ):
        return None
    if "morning" in t:
        return "mornings"
    if "afternoon" in t:
        return "afternoons"
    if "evening" in t:
        return "evenings"
    # No-preference / open-availability signals
    if any(s in t for s in (
        "anytime", "any time", "any day",
        "flexible", "doesn't matter",
        "no preference", "don't mind",
        "don't really mind", "not fussed",
        "not bothered", "either",
    )):
        return "any"
    return None


# Question signals — used to detect FAQ / question utterances that arrive
# while the location gate is active and Haiku returns unknown.  Any transcript
# matching one of these signals is a caller question, not an unclear location
# answer, and must be routed to the LLM for a proper FAQ response rather than
# firing the use-this-clinic biased confirm.
_QUESTION_SIGNALS: frozenset = frozenset({
    "what", "why", "how", "where",
    "when", "who", "which", "tell me",
    "can you tell", "what's the",
    "what is the", "difference",
    "is there", "do you", "does it",
    "what are", "how do",
})


def _transcript_is_question(text: str) -> bool:
    """Return True if *text* is a caller question rather than a location answer.

    Checks for a trailing '?' or the presence of any question-word signal.
    Used to gate the Haiku-unknown → use-this-clinic confirm flow: when Haiku
    returns unknown AND the transcript is a question, the caller wants a FAQ
    answer, not a location re-ask.
    """
    t = text.lower().strip()
    return t.endswith("?") or any(s in t for s in _QUESTION_SIGNALS)


# Use-this-clinic confirm gates.
# _USE_THIS_CLINIC_AFFIRMATIVES — the ONLY responses that trigger clinic
# confirmation.  Everything else (rejections, questions, ambiguous answers)
# routes to the LLM for proper handling so the caller is never silently
# assigned a clinic they did not choose.
# _USE_THIS_CLINIC_REJECTIONS — explicit negatives / question words that
# must never be interpreted as a "yes" to the biased confirm.
_USE_THIS_CLINIC_AFFIRMATIVES: frozenset = frozenset({
    "yes", "yeah", "yep", "yup",
    "correct", "that's right", "thats right", "that is right",
    "use this clinic", "use this one",
    "that one", "that's the one",
    "use that", "confirmed", "go ahead",
    "sounds right", "perfect",
    "yes please",
    "use this", "i did", "yes i did",
    # Caller drops the "use" the re-ask told them to say ("just say
    # 'use this clinic'" → "this clinic"). Recognise the truncated forms
    # so the confirm resolves deterministically instead of an LLM round-trip.
    # Safe under substring matching: the rejection guard runs first, so
    # "not this clinic"/"this one's wrong" route to the LLM before this gate.
    "this clinic", "this one",
})

_USE_THIS_CLINIC_REJECTIONS: frozenset = frozenset({
    "no", "nope", "not", "wrong",
    "different", "other", "actually",
    "wait", "what", "why", "how",
    "i asked", "i said", "i meant",
})


# ── Unified clinic-location ladder copy ──────────────────────────────────────
# Every "ask location" path (booking, FAQ, reschedule/cancel) shares this same
# 3-rung ladder so the caller hears identical, friendly wording everywhere —
# whether the re-ask is triggered by silence (watchdog) or by an unintelligible
# answer (the location-answer intercept):
#   Rung 1 — open choice (the first ask).
#   Rung 2 — biased confirm; arms the use-this-clinic handler.  KEEP the literal
#            "use this clinic" trigger phrase — the handler keys off it.
#   Rung 3 — DTMF keypad fallback.  KEEP "press 1 … 2" + the Awlstuh/Redditch
#            mapping — the DTMF handler and clinic binding rely on it.
# Spoken strings use "Awlstuh" (phonetic) for correct TTS pronunciation — do
# NOT change to "Alcester".  Rung 1 keeps "Awlstuh or Redditch" so the
# location-question detectors elsewhere (substring checks) still match.
_LOC_RUNG1_OPEN: str = (
    "Is this for our Awlstuh or Redditch clinic?"
)


def _loc_rung2_confirm(clinic_disp: str = "Awlstuh") -> str:
    """Rung-2 biased confirm, parametrised by clinic so the booking-ack path
    (which may bias Redditch) and the watchdog/silence ladder (always Awlstuh)
    speak the SAME wording.  Keeps the literal "use this clinic" trigger."""
    return (
        f"No worries — did you say the {clinic_disp} clinic? "
        f"If so, just say 'use this clinic'."
    )


# Convenience alias for the Awlstuh-constant sites (watchdog / silence / seeds).
_LOC_RUNG2_CONFIRM: str = _loc_rung2_confirm("Awlstuh")

_LOC_RUNG3_DTMF: str = (
    "No problem at all — on your keypad, just press 1 for Awlstuh, "
    "or 2 for Redditch."
)


# Patience-phrase guard — if the LLM response is a hold/wait phrase the
# caller has not expressed booking intent; suppress the booking ack handler.
_PATIENCE_SIGNALS: frozenset = frozenset({
    "take your time",
    "no rush",
    "whenever you're ready",
    "go ahead whenever",
    "of course — take",
    "of course — no rush",
    "not a problem — take",
    "no problem — take",
    "of course, take",
    "of course — bear with",
})


def _is_patience_response(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _PATIENCE_SIGNALS)


# Inline alias booking-intent gate.
# Only confirms a location alias when the same transcript contains at
# least one booking intent signal.  Pure FAQ questions that happen to
# name a clinic (e.g. "how many disabled bays at Redditch?") must NOT
# confirm v3_location_confirmed — doing so skips the location question
# when booking starts later, silently binding the wrong clinic.
_BOOKING_INTENT_SIGNALS: frozenset = frozenset({
    "book", "booking", "appointment", "appointments",
    "reschedule", "cancel", "move", "change my",
    "come in", "see you", "visit", "slot", "available",
    "availability", "get in", "book in", "make an appointment",
    "see mark", "get seen", "register", "new patient",
})


def _transcript_has_booking_intent(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _BOOKING_INTENT_SIGNALS)


# Spec K — lifecycle stage for the DTMF slot map.
# Transitions are strictly one-way within a booking flow:
#   DAY_SELECTION → TIME_SELECTION → NONE
# Reset to DAY_SELECTION when a new availability check fires (new turn).
class SlotMapStage(Enum):
    NONE           = 0   # name collection and beyond — no slot DTMF
    DAY_SELECTION  = 1   # numbered day options active
    TIME_SELECTION = 2   # numbered time options active (day map cleared)


# Vowel set used by Conditions 2a (no-vowel) and 2b (all-vowel).
_V3_VOWELS: frozenset = frozenset("aeiou")

# Condition 4 window — a single-word transcript arriving within this many
# seconds of the PREVIOUS ACCEPTED transcript's enqueue timestamp is treated
# as a rapid-continuation fragment and merged rather than discarded.
_V3_RAPID_ARRIVAL_SEC: float = 0.30

# ---------------------------------------------------------------------------
# Sentinel object placed on audio_out_queue AFTER the last audio chunk for a
# TTS utterance.  send_loop detects it and fires on_tts_finished() only once
# all audio for that utterance has actually been sent to Twilio.
_TTS_DONE_SENTINEL = object()

# Prefix marker prepended to a TTS text chunk by the no-input watchdog when it
# enqueues a silence-recovery re-ask.  _tts_loop strips the marker and bypasses
# the consecutive-duplicate dedup guard for that single chunk only — a watchdog
# replay of the same question is a deliberate recovery, not an accidental
# duplicate emission.  Normal dedup remains active for every other chunk.
_WATCHDOG_REASK_MARKER = "\x01WDG_REASK\x01"

# ---------------------------------------------------------------------------
# Spec O — strip leading affirmation before watchdog re-ask construction
# Applies only to the copy used for the re-ask; last_bot_prompt / last_question
# are never mutated.
# ---------------------------------------------------------------------------

_LEADING_AFFIRMATION_RES = [
    re.compile(r"^perfect\s*[—\-,]?\s*",        re.IGNORECASE),
    re.compile(r"^brilliant\s*[—\-,]?\s*",       re.IGNORECASE),
    re.compile(r"^great\s*[—\-,]?\s*",           re.IGNORECASE),
    re.compile(r"^wonderful\s*[—\-,]?\s*",       re.IGNORECASE),
    re.compile(r"^lovely\s*[—\-,]?\s*",          re.IGNORECASE),
    re.compile(r"^fantastic\s*[—\-,]?\s*",       re.IGNORECASE),
    re.compile(r"^excellent\s*[—\-,]?\s*",       re.IGNORECASE),
    re.compile(r"^of course\s*[—\-,]?\s*",       re.IGNORECASE),
    re.compile(r"^no problem\s*[—\-,]?\s*",      re.IGNORECASE),
    re.compile(r"^not a problem\s*[—\-,]?\s*",   re.IGNORECASE),
    re.compile(r"^awlstuh,?\s*perfect\.?\s*",    re.IGNORECASE),
]


def _strip_leading_affirmation(prompt: str) -> str:
    """
    Strip a leading affirmation word/phrase (e.g. 'Perfect —', 'Of course —')
    from *prompt* and re-capitalise the remainder.  Returns the original string
    unchanged if stripping would leave an empty result.

    Called only when building watchdog re-asks — never modifies the stored
    last_bot_prompt or last_question values.
    """
    for _pat in _LEADING_AFFIRMATION_RES:
        _m = _pat.match(prompt)
        if _m:
            _rest = prompt[_m.end():]
            if _rest:
                return _rest[0].upper() + _rest[1:]
            return prompt  # stripping left nothing — keep original
    return prompt


# ---------------------------------------------------------------------------
# Spec R — verbal reset guard helpers
# ---------------------------------------------------------------------------

_NAME_CORRECTION_SIGNALS: tuple = (
    "my name",
    "name is",
    "name isn't",
    "name's not",
    "wrong name",
    "name was wrong",
    "name correction",
    "got that wrong",
    "mistake on the name",
)

# Pre-compiled for the conversational-speech check (Change 3).
_DTMF_DIGIT_RUN_RE = re.compile(r"\d{2,}")


def _is_name_correction(transcript: str) -> bool:
    """Return True if the transcript is correcting a name, not a number."""
    lowered = transcript.lower()
    return any(sig in lowered for sig in _NAME_CORRECTION_SIGNALS)


def _is_conversational_during_dtmf(transcript: str) -> bool:
    """
    Return True when a transcript is almost certainly conversational speech
    rather than a number entry attempt: >4 words AND no run of 2+ digits.
    Only triggers the escape hatch when the DTMF buffer is also empty.
    """
    words = transcript.strip().split()
    if len(words) <= 4:
        return False
    return not _DTMF_DIGIT_RUN_RE.search(transcript)


# Phrases confirming "use the number I'm calling from" during the verbal
# phone-collection step.  Substring-matched against a lower-cased transcript.
_USE_THIS_NUMBER_SIGNALS: tuple = (
    "use this number", "use that number", "use the number",
    "use my number", "use this one", "use that one",
    "use this", "use that", "same number", "this number",
    "that number", "the same one", "number i'm calling",
    "number im calling", "number i'm on", "number im on",
    "one i'm calling", "one im calling", "calling from",
    "keep this number", "keep that number",
)

# Short bare affirmatives that, in the phone-confirm context (buffer empty,
# Susie just asked "say use this number"), can only mean "yes, use it".
_PHONE_CONFIRM_AFFIRMATIVES: frozenset = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "correct", "right",
    "that's right", "thats right", "fine", "that's fine", "thats fine",
    "ok", "okay", "please", "go ahead", "yes please", "perfect",
})


def _is_use_this_number(transcript: str) -> bool:
    """
    True when the caller is confirming they want the number they are calling
    from used for the booking.

    Matches explicit 'use this number' phrasings, or a short bare affirmative
    (<=3 words) — which, in the verbal phone-confirmation step, reliably means
    'yes, use the calling number'.  Negative intent ('no', 'a different number')
    is never matched, so it falls through to the LLM / keypad path unchanged.
    """
    lowered = transcript.strip().lower().rstrip(".!?")
    if not lowered:
        return False
    # Explicit negative intent must never be swallowed as a confirmation.
    if any(neg in lowered for neg in ("different", "another", "wrong", "not ", "no ")):
        return False
    if any(sig in lowered for sig in _USE_THIS_NUMBER_SIGNALS):
        return True
    words = lowered.split()
    if len(words) <= 3 and (
        lowered in _PHONE_CONFIRM_AFFIRMATIVES
        or any(w in _PHONE_CONFIRM_AFFIRMATIVES for w in words)
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Greeting (built at call start from clinic_config.json)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Question-worth-storing guard (mirrors the one in flow.py)
# ---------------------------------------------------------------------------

# Phrases whose presence anywhere in the text means we must NOT store it
# as last_question.  Checked with `phrase in text_lower` (substring match).
_NEVER_STORE_PHRASES = [
    # Re-ask / error phrases — must never overwrite the original question
    "sorry, i didn't quite catch",
    "sorry about that",
    "sorry, i'm having",
    "i'm having a little trouble",
    "didn't quite catch",
    "bear with me",
    "one moment",
    "let me check",
    "just bear",
    # Greeting / preamble phrases — not actionable questions
    "hi there",
    "hello",
    "this is susie",
    "roch solutions",
    "theorem health",
]

_KNOWN_QUESTION_PHRASES = [
    "what brings you in",
    "how long have you had",
    "does that sound ok",
    "been with us before",
    "work best for you",
    "first name please",
    "reach you on",
    "which would you prefer",
    "that right",
    "slot would you",
    "would you like",
    "no problem — which",
    "sound ok",
]


def _is_question_worth_storing(text: str) -> bool:
    """
    Return True only if text is a real question Susie asked.
    Rejects greetings, re-ask phrases, filler phrases, and error phrases.
    Uses substring match (not startswith) so "sorry about that — X?" is also rejected.
    """
    t = text.strip().lower()
    for phrase in _NEVER_STORE_PHRASES:
        if phrase in t:
            return False
    for q in _KNOWN_QUESTION_PHRASES:
        if q in t:
            return True
    if t.endswith("?"):
        return True
    return False


# ---------------------------------------------------------------------------
# SilenceHandler — re-ask after caller silence
# ---------------------------------------------------------------------------

# Single source of truth: is the flow explicitly expecting keypad input?
# When True, the normal speech watchdog must stand down — no generic
# "Sorry, I didn't catch that" re-asks, no arming, and any live watchdog
# must be cancelled on the first DTMF digit.  Any new keypad branch that
# sets one of these flags automatically inherits correct behaviour.
_DTMF_EXPECTED_FLAGS = (
    "phone_awaiting_dtmf",
    "location_awaiting_dtmf",
    "_faq_loc_awaiting_dtmf",
    "rc_kp_phone_pending",
    "v3_slot_dtmf_active",          # theorem_v3: numbered slot / time selection
    "v3_awaiting_location_dtmf",    # theorem_v3: keypad fallback for location resolution
)

# Regex to locate each slot anchor in an LLM slot-presentation response.
# Matches both "Number N" (new preferred format) and legacy "N —" dash form.
# Two capture groups: group(1) for "Number N", group(2) for "N —".
_V3_SLOT_ANCHOR_RE = re.compile(
    r"Number\s+([1-9])\b|(?<!\d)([1-9])\s*[—–\-]\s*",
    re.IGNORECASE,
)


def _parse_v3_slot_options(text: str) -> dict:
    """
    Extract a {digit: label} map from an LLM slot-presentation response.

    Supports both the preferred "Number N, label" format and the legacy
    "N — label" dash format.

    For each anchor the label is the text between the anchor end and the
    next anchor start, stripped of leading punctuation and trimmed at the
    first em-dash or full-stop (isolating just the day or time name).

    Example inputs handled correctly:
      "Number 1, Thursday the 7th — nine or two. Number 2, Monday..."
      "Number 1, nine in the morning. Number 2, two in the afternoon."
      "1 — Thursday the 7th of May, I've got nine ... 2 — Monday ..."

    Returns the map only when 2+ entries are found (single match is
    ambiguous and must not arm DTMF).
    """
    anchors = [
        (m.start(), m.end(), m.group(1) or m.group(2))
        for m in _V3_SLOT_ANCHOR_RE.finditer(text)
    ]
    if len(anchors) < 2:
        return {}

    result: dict = {}
    for i, (start, label_start, digit) in enumerate(anchors):
        # Text between this anchor's label start and the next anchor start
        next_start = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        label_full = text[label_start:next_start]
        # Strip leading comma/space (e.g. ", Thursday..." after "Number 1")
        label_full = label_full.lstrip(", ")
        # Trim at the first em-dash (separates day from times) or full-stop
        label = re.split(r"[—–\.]", label_full)[0].strip().rstrip(".,;- ")
        if label:
            result[digit] = label

    return result if len(result) >= 2 else {}


# ---------------------------------------------------------------------------
# theorem_v3 slot map context helper
# ---------------------------------------------------------------------------

def _is_time_map(slot_map: dict) -> bool:
    """
    Returns True when slot map values contain times rather than day names.

    Used to detect the day→time context shift: when the caller picks a day
    and check_availability returns time slots, the new map should overwrite
    the old day map so DTMF 1/2/3 maps to the correct time, not the old day.

    Time signals: clock words, period-of-day words, spoken digit hour names.
    Bare digit strings or day-name labels return False.
    """
    _TIME_SIGNALS = frozenset({
        "o'clock", "morning", "afternoon",
        "evening", "nine", "ten", "eleven",
        "twelve", "one", "two", "three",
        "four", "five", "six",
    })
    for v in slot_map.values():
        v_lower = v.lower()
        if any(sig in v_lower for sig in _TIME_SIGNALS):
            return True
    return False


# ---------------------------------------------------------------------------
# theorem_v3 name persistence helper (Bug 7)
# ---------------------------------------------------------------------------

# Patterns that indicate the LLM just confirmed the caller's name.
# Each captures the name in group(1).
_V3_NAME_CONFIRM_PATTERNS = [
    # Pattern 1a: "Thanks Sarah —" / "Thanks Sarah,"
    re.compile(r'[Tt]hanks\s+([A-Za-z][a-z]{1,25})[\s—–‒,.\-]'),
    # Pattern 1b: "So that's Sarah," / "So that's Sarah —"  (readback)
    re.compile(r"[Ss]o (?:that'?s|it'?s)\s+([A-Za-z][a-z]{1,25})[\s—–,\-]"),
    # Pattern 1c: "Right Sarah —" / "Right Sarah,"
    re.compile(r'[Rr]ight\s+([A-Za-z][a-z]{1,25})[\s—–,\-]'),
    # Pattern 1d: "Sarah — got it" / "Sarah — noted" / "Sarah — perfect"
    #   (em-dash/en-dash/hyphen, specific trailing phrase)
    re.compile(r'^([A-Za-z][a-z]{1,25})\s*[—–\-]+\s*'
               r'(?:got it|noted|perfect|right|could i|if you)'),
    # Pattern 1e: "Of course Sarah," (mid-sentence acknowledgement)
    re.compile(r'[Oo]f course\s+([A-Za-z][a-z]{1,25})[\s—–,\-]'),
    # Pattern 1f: "Just to confirm — that's Sarah," (alternate readback opening)
    re.compile(r"[Jj]ust to confirm[^,—]*[,—]\s*(?:that'?s\s+)?([A-Za-z][a-z]{1,25})[\s—–,\-]"),
    # Pattern 2 (Spec T amendment): name-first responses — "Sarah — got it.",
    #   "Sarah, noted.", "Sarah — if you'd like to use..."
    #   Permissive: title-case word + em-dash-or-comma + space.  The
    #   _V3_NAME_FALSE_POSITIVES check below prevents "Perfect — ...",
    #   "Awlstuh, perfect.", day-names, and other openers from matching.
    re.compile(r'^([A-Z][a-z]{1,19})\s*[—,]\s'),
]

# Words that must never be treated as names even if a pattern matches them.
# Extended by Spec T amendment: day-names and common opener words added so
# Pattern 2 (name-first) cannot false-positive on slot/location responses.
_V3_NAME_FALSE_POSITIVES = frozenset({
    "sorry", "right", "great", "perfect", "ok", "okay", "sure",
    "yes", "no", "of", "course", "me", "you", "we", "it", "is",
    "thanks", "thank", "hi", "hello", "hey", "now", "just", "that",
    "this", "then", "so", "and", "but", "the", "a", "an",
    # Spec T amendment additions
    "brilliant", "lovely", "noted", "awlstuh", "redditch",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    # CODE SPEC AB: time-of-day words — prevent slot-presentation phrases like
    # "Afternoons — I've got..." from being stored as a patient name.
    "morning", "mornings", "afternoon", "afternoons", "evening", "evenings",
    # Scheduling vocabulary that appears at the start of availability responses
    "number", "slot", "appointment", "available", "availability",
    # Month names — prevent date strings like "May —" being captured as a name
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})


# Stop-words that must never be captured as a surname. Combined with
# _V3_NAME_FALSE_POSITIVES at match time. Conversational/filler tokens that
# commonly co-occur in a name-answer utterance.
_SURNAME_STOPWORDS = frozenset({
    "is", "are", "was", "be", "my", "your", "the", "a", "an", "and", "or",
    "but", "name", "names", "first", "last", "given", "middle", "second",
    "surname", "family", "please", "thanks", "thank", "yes", "yeah", "yep",
    "no", "nope", "use", "this", "that", "number", "one", "calling", "from",
    "on", "with", "for", "it", "its", "im", "i", "me", "we", "you", "he",
    "she", "they", "clinic", "appointment", "booking", "book", "sorry",
    "mr", "mrs", "ms", "miss", "mister", "doctor", "dr", "there", "hi",
    "hello", "hey", "ok", "okay", "just", "spelt", "spelled", "spell",
    "like", "would", "to", "of", "as", "so", "well", "um", "uh", "er",
})

_SURNAME_TOKEN = r"[a-z][a-z'\-]{1,24}"


def _v3_extract_surname(caller_utterance: str, first_name: str) -> str:
    """Best-effort surname extraction from the caller's name-answer utterance.

    The first name is taken authoritatively from Susie's readback ("Thanks
    Quentin —"); this only recovers the SURNAME so the full name can be
    registered WITHOUT ever reading the surname back. Returns a capitalised
    surname, or "" if none can be confidently identified.

    Only invoked inside the name-collection phase (gated by the caller of
    _v3_try_persist_name and by a successful first-name readback match), so the
    utterance is a name answer — which keeps false positives low.
    """
    if not caller_utterance:
        return ""
    text = caller_utterance.lower()
    text = re.sub(r"[^a-z'\-\s]", " ", text)   # punctuation/digits → space
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    first_l = (first_name or "").lower()

    def _ok(tok: str) -> bool:
        return (
            bool(tok)
            and 2 <= len(tok) <= 25
            and tok != first_l
            and tok not in _SURNAME_STOPWORDS
            and tok not in _V3_NAME_FALSE_POSITIVES
        )

    # 1) Explicit surname marker — most reliable.
    #    "surname is rock", "last name rock", "family name is o'brien"
    m = re.search(
        r"(?:surname|last name|family name|second name)"
        r"(?:\s+is|\s+was|'s)?\s+(" + _SURNAME_TOKEN + r")",
        text,
    )
    if m and _ok(m.group(1)):
        return m.group(1).capitalize()

    # 2) "my name is X Y[ Z]", "it's X Y", "i'm X Y", "this is X Y".
    #    Surname = last name-like token of the captured tail.
    m = re.search(
        r"(?:my name is|name'?s|name is|it'?s|it is|i'?m|i am|this is)\s+"
        r"(" + _SURNAME_TOKEN + r")\s+(" + _SURNAME_TOKEN
        + r"(?:\s+" + _SURNAME_TOKEN + r")*)",
        text,
    )
    if m:
        cand = m.group(2).split()[-1]
        if _ok(cand):
            return cand.capitalize()

    # 3) Bare name "quentin rock" / "quentin james rock" — only when the first
    #    token matches the readback first name (high confidence).
    tokens = text.split()
    if 2 <= len(tokens) <= 4 and tokens[0] == first_l:
        cand = tokens[-1]
        if _ok(cand):
            return cand.capitalize()

    return ""


def _v3_try_persist_name(
    session: dict,
    last_bot: str,
    post_slot_pending: bool = False,
    caller_utterance: str = "",
) -> bool:
    """
    Scan the LLM's last reply for a name-confirmation pattern and immediately
    persist the name in session state if found and not already set.

    Phase gate (CODE SPEC AB): only runs when the system is actively in the
    name collection phase.  The gate passes when either:
      • post_slot_pending is True  — the PREVIOUS turn asked for the caller's
        name, so the current caller utterance was the name answer and this
        response is the LLM's confirmation ("Thanks Sarah — …").
      • The CURRENT last_bot itself contains a _NAME_REQUEST_PHRASES token —
        handles the rare case where the LLM asks for and acknowledges the name
        within the same single response.
    If neither condition is met the function returns False immediately, which
    prevents slot-presentation phrases like "Afternoons — I've got …" from
    being stored as a patient name.

    Writes to both:
      session["patient_name"]          — direct key for easy downstream reads
      session["collected"]["name"]     — existing path read by summaries/Sheets

    Returns True if a name was newly persisted, False if already set or not found.

    Called after every run_turn() in the theorem_v3 path so the name is stored
    at the moment of confirmation regardless of whether collect_and_store fired.
    """
    # Already persisted — nothing to do.
    if session.get("patient_name") or session.get("collected", {}).get("name"):
        return False

    if not last_bot:
        return False

    # ── Phase gate ────────────────────────────────────────────────────────────
    # Only proceed when we are in the name-collection phase of the call.
    _last_bot_lower = last_bot.lower()
    _name_requested_this_turn = any(
        p in _last_bot_lower for p in _NAME_REQUEST_PHRASES
    )
    if not post_slot_pending and not _name_requested_this_turn:
        return False

    for pattern in _V3_NAME_CONFIRM_PATTERNS:
        m = pattern.search(last_bot)
        if m:
            candidate = m.group(1).capitalize()
            if candidate.lower() not in _V3_NAME_FALSE_POSITIVES:
                # The readback only ever contains the FIRST name (Susie never
                # reads the surname back), so recover the surname from the
                # caller's own utterance and store the FULL name. First name
                # stays authoritative from the readback; surname is best-effort
                # from the (clean) transcript. Falls back to first-name-only.
                surname = _v3_extract_surname(caller_utterance, candidate)
                full = f"{candidate} {surname}" if surname else candidate
                session.setdefault("collected", {})["name"] = full
                session["patient_name"] = full
                return True

    return False


# ---------------------------------------------------------------------------
# theorem_v3 location alias matching
# ---------------------------------------------------------------------------

def _normalise_location_text(text: str) -> str:
    """
    Normalise STT output before location alias matching.
    Lowercases, strips punctuation, collapses spaces.
    "al-chester", "al chester", "alchester" → "al chester" / "alchester"
    all resolve to the same form so a single alias covers them.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)   # remove punctuation except spaces
    text = re.sub(r"\s+", " ", text)       # collapse multiple spaces
    return text.strip()


# Alcester alias set — 60+ entries covering phonetic variants, STT artefacts,
# vowel-shift forms, suffix variants, and confirmed call-log transcripts.
# Applied via substring scan on the normalised utterance (not exact-match),
# so "I'd like awlster please" hits "awlster" without a separate entry.
_ALCESTER_ALIASES: frozenset[str] = frozenset({
    # ── Exact and near-exact ────────────────────────────────────────────────
    "alcester",
    "alcester clinic",
    "the alcester",
    "the alcester clinic",
    "our alcester",
    "your alcester",

    # ── ch-insertion variants ────────────────────────────────────────────────
    "alchester",
    "al chester",
    "all chester",
    "the alchester",
    "our alchester",
    "alchester clinic",
    "awlchester",
    "olchester",
    "allchester",
    "orlchester",

    # ── Ulster family (/ɔː/ → /ʌ/) ─────────────────────────────────────────
    "ulster",
    "ulster clinic",
    "the ulster",
    "our ulster",

    # ── Vowel-shift variants ────────────────────────────────────────────────
    "olster",
    "orlster",
    "oldster",
    "holster",
    "awlster",
    "awster",
    "allster",
    "alster",
    "orster",
    "orchester",

    # ── Suffix variants ─────────────────────────────────────────────────────
    "alcestar",
    "alcesta",
    "alcestre",
    "alcestir",
    "alsester",
    "alseter",
    "alceser",
    "alcestere",

    # ── Confirmed call-log transcripts ──────────────────────────────────────
    "alcestra",
    "ausesta",
    # STT renders "Alcester" as "alstac" — \b boundary blocks "alsta" from
    # matching "alstac", so adding explicitly.  Observed: "your alstac clinic"
    "alstac",
    "alstac clinic",
    "your alstac",
    "alstack",
    "alstick",
    "your alcestra",
    "our alcestra",
    # NOTE: "the clinic" is intentionally NOT here. It is a GENERIC reference to
    # the business, not a name mishear, so it cannot disambiguate Alcester from
    # Redditch. It is handled separately via _GENERIC_CLINIC_ALIAS_RE with a
    # stricter confirm-gate (see the pre-ack inline alias scan ~line 7565), so a
    # generic FAQ like "does the clinic do sports massages" no longer silently
    # binds Alcester (Test A wrong-clinic bug, 2026-06-12).
    # STT mishears of "Alcester" as "alter" or "host" + clinic
    # Observed across calls 5–9; substring match covers "your alter clinic",
    # "your host clinic", "your alter clin" (split transcript) etc.
    # Bare "alter" added: when asking which clinic, a single-word "alter"
    # is unambiguously Alcester — the caller cannot be using "alter" to mean
    # "change" because no change-request context is active at this point.
    "alter",
    "alter clinic",
    "alter clin",
    "host clinic",
    "your host",

    # ── Non-native / mispronounced variants ─────────────────────────────────
    "al ses ter",
    "al sis ter",
    "alcister",
    "alkester",
    "oalster",
    "aolster",
    "aulster",
    "oister",
    "alces",

    # ── Two-word splits ──────────────────────────────────────────────────────
    "al ster",
    "ol ster",
    "all ster",
    "aul ster",
    "aul chester",

    # ── Clipped / schwa-final forms ──────────────────────────────────────────
    "awlsta",
    "olsta",
    "orlsta",
    "alsta",      # CODE SPEC AH — STT garbles observed or likely
    # -uh endings: Susie TTS says "Awlstuh" (non-rhotic schwa);
    # callers echo this back and STT renders the schwa as "-uh" not "-a"
    "awlstuh",
    "awlstah",
    "allstuh",
    "alstuh",
    "olstuh",

    # ── Short / partial forms (CODE SPEC AH) ────────────────────────────────
    "alce",
    "alstr",
    "alcest",
    "altice",

    # ── Possessive / with article ────────────────────────────────────────────
    "alcester s",
    "the alcester s",

    # ── Possessive / your-prefixed (CODE SPEC AH) ────────────────────────────
    "your alsta",
    "your alster",
    "your alce",
})

# Redditch alias set — covers common STT mishearings.
_REDDITCH_ALIASES: frozenset[str] = frozenset({
    # ── Exact and near-exact ────────────────────────────────────────────────
    "redditch",
    "redditch clinic",
    "the redditch",
    "the redditch clinic",
    "our redditch",
    "your redditch",

    # ── ch/dge-final variants ────────────────────────────────────────────────
    "reddich",
    "reddich clinic",
    "redich",
    "redich clinic",
    "reddidge",
    "reddidge clinic",
    "the reddidge",
    "our reddidge",
    "your reddidge",
    "reddige",
    "reddige clinic",
    "the reddige",

    # ── ish-final variants ───────────────────────────────────────────────────
    "reddish",
    "reddish clinic",
    "the reddish",
    "our reddish",
    "your reddish",
    "reddis",
    "redis",

    # ── red witch family ─────────────────────────────────────────────────────
    # Very common STT: /rɛdɪtʃ/ → "red witch"
    "red witch",
    "red witch clinic",
    "the red witch",
    "our red witch",
    "your red witch",

    # ── red rich / red ridge family ──────────────────────────────────────────
    # /rɛdɪtʃ/ → "red rich" or "red ridge"
    "red rich",
    "red rich clinic",
    "redrich",
    "red ridge",
    "red ridge clinic",
    "the red ridge",

    # ── red wick / red wich family ───────────────────────────────────────────
    # like Norwich → Norrich
    "red wich",
    "red wick",
    "redwich",
    "redwick",

    # ── Two-word split variants ──────────────────────────────────────────────
    "red ditch",
    "red ditch clinic",
    "red itch",
    "red itch clinic",
    "red dige",
    "red dich",
    "red dish",
    "red d itch",

    # ── Surname-style mishearings ────────────────────────────────────────────
    # STT often returns proper-noun-like forms
    "reddick",
    "reddick clinic",
    "the reddick",
    "redick",
    "reddik",
    "redik",

    # ── Vowel-shift variants ─────────────────────────────────────────────────
    "readitch",
    "readich",
    "ridditch",
    "riddich",
    "riditch",
    "ridich",
    "ruditch",
    "reditch",

    # ── Non-native / mispronounced ───────────────────────────────────────────
    "reddisch",
    "reddische",
    "reddiche",

    # ── Possessive / with article ────────────────────────────────────────────
    "redditch s",
    "the redditch s",
    "reddich s",
})

# Word-boundary regexes compiled from the alias sets above.
# Sorted longest-first so longer multi-word aliases win over shorter
# sub-strings in alternation matching.
# Used by the pre-ack inline alias scan (see _detect_location_alias_inline).
_ALCESTER_ALIAS_WB_RE: re.Pattern = re.compile(
    r"\b(?:" + "|".join(
        re.escape(a) for a in sorted(_ALCESTER_ALIASES, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)
_REDDITCH_ALIAS_WB_RE: re.Pattern = re.compile(
    r"\b(?:" + "|".join(
        re.escape(a) for a in sorted(_REDDITCH_ALIASES, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)

# Generic clinic references — phrases that name "a clinic" generically rather
# than mishearing a specific clinic NAME. These resolve to the default site
# (Alcester, the primary Mon–Fri clinic) but ONLY in a genuine clinic-choice
# context, so a generic FAQ ("does the clinic do sports massages") cannot
# silently bind a location. Gated separately from the name-mishear sets above:
# see the pre-ack inline alias scan (~line 7565).
_GENERIC_CLINIC_ALIASES: frozenset[str] = frozenset({
    "the clinic",
})
_GENERIC_CLINIC_ALIAS_RE: re.Pattern = re.compile(
    r"\b(?:" + "|".join(
        re.escape(a) for a in sorted(_GENERIC_CLINIC_ALIASES, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# FAQ clinic gate — topics that differ between Alcester and Redditch.
# When no clinic is confirmed and the caller asks about one of these topics,
# Susie must ask "Which clinic?" BEFORE answering.  Without this hard gate
# the LLM tends to summarise both clinics first and ask at the end, which
# fails the UX spec.  The gate injects the question directly, skips
# run_turn(), and writes the exchange into conversation_history so the
# following LLM turn has full parking/address context for the named clinic.
# ---------------------------------------------------------------------------
_FAQ_CLINIC_SPECIFIC_RE = re.compile(
    r"\b(?:"
    r"park(?:ing)?|car\s*park|"
    r"address|postcode|"
    r"open(?:ing)?(?:\s+hour[s]?|\s+time[s]?)?|business\s+hour[s]?|when.*open|"
    r"bus\s+(?:stop|route|number|line)|train\s+(?:station|line|stop)|"
    r"public\s+transport|how\s+(?:do\s+i|to)\s+get\s+there"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Spec Y — treatment-specific booking bypass
# When a caller names a specific treatment alongside booking intent, the
# booking ack handler must NOT auto-queue the location question.  Instead
# the transcript falls through to run_turn so the LLM can apply Prompt L
# framing (acknowledge treatment → recommend assessment → offer to book).
# ---------------------------------------------------------------------------
_TREATMENT_SIGNALS: frozenset = frozenset({
    "acupuncture",
    "shockwave",
    "dry needling",
    "sports massage",
    "deep tissue",
    "ultrasound",
    "laser",
    "massage",
    "needling",
    "manipulation",
    "mobilisation",
    "mobilization",
    "electrotherapy",
    "ultrasound therapy",
    "heat therapy",
    "taping",
    "strapping",
})


def _is_treatment_specific_booking(transcript: str) -> bool:
    """
    Return True when `transcript` contains any named treatment or therapy
    signal.  No booking-intent check — both FAQ mentions ("do you do
    acupuncture?") and explicit booking requests ("I'd like acupuncture")
    are routed to the LLM so Prompt L framing always fires first.

    Revised by Spec Y (amended): booking-intent gate removed per spec.
    """
    lowered = transcript.lower()
    return any(sig in lowered for sig in _TREATMENT_SIGNALS)


# ---------------------------------------------------------------------------
# Slot-selection day aliases — STT mishearing correction
# Applied ONLY when v3_awaiting_slot_selection is active so common words
# ("first", "year") cannot false-positive outside the slot-choice window.
#
# Each key is the canonical day name (lowercase).
# Values are phonetic STT garble variants confirmed from live call logs.
# ---------------------------------------------------------------------------

_V3_SLOT_DAY_ALIASES: dict[str, list[str]] = {
    "thursday": [
        "first year",
        "firs year",
        "first ear",
    ],
}

# Compile one regex per canonical day: longest alias first so multi-word
# entries win over any shorter overlap.
_V3_SLOT_DAY_ALIAS_RES: dict[str, re.Pattern] = {
    day: re.compile(
        r"\b(?:" + "|".join(
            re.escape(v) for v in sorted(variants, key=len, reverse=True)
        ) + r")\b",
        re.IGNORECASE,
    )
    for day, variants in _V3_SLOT_DAY_ALIASES.items()
}


def _apply_slot_day_aliases(text: str) -> str:
    """
    Replace STT day-name mishearings with the canonical day name.

    Only called when v3_awaiting_slot_selection is active, so false
    positives in other parts of the call are impossible.  Word-boundary
    matching means "first year" inside longer text is replaced cleanly
    while preserving surrounding words.
    """
    for day, pattern in _V3_SLOT_DAY_ALIAS_RES.items():
        replaced = pattern.sub(day, text)
        if replaced != text:
            logger.info(
                "[ms_stt] slot day alias applied: %r → %r",
                text[:80], replaced[:80],
            )
            text = replaced
    return text


def _is_dtmf_expected(session: Optional[Dict[str, Any]]) -> bool:
    if not session:
        return False
    for _flag in _DTMF_EXPECTED_FLAGS:
        if session.get(_flag):
            return True
    return False


# Short acknowledgement openers Susie uses to soften a turn ("No problem — …").
# When a stored question is replayed as a dead-air re-ask it is prefixed with its
# own filler ("Sorry — I can't quite hear you —"); a leading filler on the stored
# question would then stack two fillers (Bug C, 2026-06-14).
_LEADING_FILLERS = frozenset({
    "no problem", "no worries", "not to worry", "of course", "sure",
    "right", "okay", "ok", "alright", "all right", "absolutely", "got it",
})


def _strip_leading_filler(text: str) -> str:
    """Drop a single leading acknowledgement clause ("No problem — ", "Sure — ").

    Only strips when the leading clause is a KNOWN short filler followed by a
    dash separator, so real question content is never truncated.  Idempotent.
    """
    if not text:
        return text
    for _sep in (" — ", " – ", " - "):
        _idx = text.find(_sep)
        if 0 < _idx <= 24:
            _head = text[:_idx].strip().lower().rstrip(",.!")
            if _head in _LEADING_FILLERS:
                return text[_idx + len(_sep):].lstrip()
    return text


def _build_slot_clarify(slot_labels: list) -> str:
    """Build the 'which option?' clarify prompt listing EVERY offered slot.

    Bug B (2026-06-14): the old builder listed only the first three of N options,
    silently dropping later slots — a caller wanting the 4th/5th could not pick
    it.  List them all, with natural "A, B, … or Z" grammar.
    """
    _labels = [str(s).strip() for s in (slot_labels or []) if str(s).strip()]
    if not _labels:
        return "Which option works best for you?"
    if len(_labels) == 1:
        return f"Did you mean {_labels[0]}?"
    if len(_labels) == 2:
        return f"Which works best — {_labels[0]} or {_labels[1]}?"
    _body = ", ".join(_labels[:-1])
    return f"Which works best — {_body}, or {_labels[-1]}?"


class SilenceHandler:
    """
    Fires a re-ask phrase if the caller has been silent for an extended
    period after Susie asked a question.

    last_audio_received_at is updated ONLY by on_speech_started() and
    on_transcript_received() — NOT by on_audio_received() — so the
    since_audio guard reflects actual speech, not Twilio's continuous
    silence packets (which arrive every ~20ms regardless of speech).

    Silence windows:
        1st (26 s) → "Sorry, I didn't quite catch that — <question>"
        2nd (15 s) → "Sorry about that — <question>"
        3rd (15 s) → transfer phrase + trigger_transfer()

    Windows are sized so that:
      - Re-ask #1 fires AFTER the 25-second injection window used by the
        automated test runner (TURN_WAIT_SECONDS=25).  With typical LLM+TTS
        latency of 3-8 s, the timer arms at t=3-8s and would fire at t=23-28s
        — which overlaps with the t=25s injection window when questions are
        short.  Raising Window 1 to 26 s ensures the timer always fires at
        t=L+26 ≥ 26 s, well after the t=25s injection which cancels it.
      - For genuine silence scenarios (Phase 6 tests), no injection arrives
        during the 25s empty-turn window, so the timer still fires at ~36 s
        (greeting TTS ≈ 10 s + Window 1 = 26 s) and the re-ask plays correctly.
      - Transfer does NOT fire before a second silent turn's response
        arrives (~70 s from call start), allowing recovery scenarios to work.
      - The since_audio < 3.5 guard means the window fires only when
        genuinely no speech has been detected.
    """

    def __init__(
        self,
        tts_text_queue: asyncio.Queue,
        trigger_transfer_fn,
        on_reask=None,
        on_transfer=None,
        get_session=None,
        on_dead_air_ts_reset=None,
    ) -> None:
        self.reask_count:             int   = 0
        self.last_audio_received_at:  float = time.time()
        # last_engagement_at — broadest "caller was doing something" clock.
        # Updated by: speech-start/VAD, partial transcripts, final transcripts,
        # DTMF presses, confirmed barge-in, fragment-suppressed transcripts.
        # Unlike last_audio_received_at it is NOT reset between questions and
        # serves as the primary guard in _speech_recovery.
        self.last_engagement_at:      float = time.time()
        self.last_question:           str   = ""
        self._replay_flow_step:       int   = -1
        self.current_state:           str   = "default"
        self._consecutive_silence_count: int = 0
        self.currently_reasking:      bool  = False
        self._last_question_set_at:   float = time.time()
        self._task: Optional[asyncio.Task]  = None
        self._tts_text_queue                = tts_text_queue
        self._trigger_transfer              = trigger_transfer_fn
        self._llm_busy:               bool  = False
        self._on_reask                      = on_reask      # optional async callback(text)
        self._on_transfer                   = on_transfer   # optional async callback(text)
        # Callable that returns the current session dict (passed as lambda: self.session
        # from WebSocketCallHandler so it always reflects the live session after
        # the "start" event reassigns self.session).
        self._get_session                   = get_session   # () -> dict | None
        self._on_dead_air_ts_reset          = on_dead_air_ts_reset  # optional async callback() — resets WebSocketCallHandler._last_audio_or_transcript_ts
        self._recovery_task: Optional[asyncio.Task] = None  # re-arms timer if STT misses audio
        self._stt_miss_count: int = 0  # consecutive STT misses since last successful transcript
        self._cancelled: bool = False  # set by cancel() — hard synchronous guard for _run()/_transfer()
        # No-input watchdog: fires 3 s after TTS ended if there is zero caller
        # engagement (no VAD, no partial, no final transcript).  Unlike
        # _speech_recovery, it does not require a preceding VAD event — it is
        # the only recovery path when STT misses the utterance entirely.
        # _no_input_reask_count tracks how many watchdog prompts have fired for
        # the current question; reset on every real transcript or new question.
        self._no_input_reask_count: int = 0
        self._no_input_watchdog_task: Optional[asyncio.Task] = None
        # q_gen value that was current when the running watchdog was armed.
        # Used by _restart_timer() to skip watchdog re-creation when on_tts_finished()
        # fires multiple times for the same logical question (multi-chunk TTS).
        self._watchdog_q_gen: int = -1
        # Spec Z: deferred watchdog state.  Set when _restart_timer() is called
        # while _llm_busy=True or the current last_question has no question — the
        # watchdog start is deferred until on_llm_finished() fires (and TTS is idle).
        self._watchdog_deferred: bool = False
        # Scaffold-hold grace deadline: set by the scaffold-hold path to extend
        # the watchdog patience window without corrupting last_engagement_at's
        # semantics.  Watchdog reads max(last_engagement_at, _watchdog_grace_until)
        # as its activity anchor.  Naturally expires — no explicit reset needed.
        self._watchdog_grace_until: float = 0.0
        # Timestamp of the most recent DTMF keypress received in a COLLECT_PHONE
        # state.  Used by the watchdog Phase 3 guard to suppress firing during
        # active keypad entry even if phone_dtmf_buffer has been cleared by a race.
        self.last_dtmf_at: float = 0.0
        # Set True when the no-input watchdog retires after exhausting re-asks
        # for a given question generation.  on_tts_finished suppresses timer
        # re-arm while this is True so a late TTS callback cannot re-trigger
        # the watchdog cycle after it has already concluded.  Reset on every
        # new question (q_gen increment in on_question_asked).
        self._watchdog_has_retired: bool = False
        # Set True when WATCHDOG_RETIRE fires with reason=audible_reask_done.
        # The global silence safety net reads this flag and suppresses itself for
        # the current q_gen when True — the watchdog already did the re-ask, so
        # the safety net firing on top of it would be a duplicate.
        # Reset to False on WATCHDOG_START (new q_gen arm) and when a new FINAL
        # transcript is accepted and processed (caller spoke → fresh turn).
        self._reask_completed: bool = False
        # Timestamp when the last question's TTS audio finished playing (set in
        # on_tts_finished just before _restart_timer).  Used by _speech_recovery to
        # enforce a minimum response window so energy VAD noise before the caller
        # has realistically had time to answer cannot trigger a premature re-ask.
        self._tts_done_at: float = 0.0
        # Question generation counter — incremented by on_question_asked() for every
        # distinct question.  All timers (_run) and recovery tasks (_speech_recovery)
        # capture the generation at creation time and are no-ops if _q_gen has since
        # advanced.  This eliminates stale recovery from a previous question firing
        # during a new question and prevents double-fire in re-ask cycles.
        self._q_gen: int = 0
        # Timestamp of the most recent on_tts_started() call.  Used by
        # on_tts_finished() to detect whether a newer TTS chunk has already started,
        # preventing _tts_playing from being cleared prematurely during multi-chunk /
        # multi-part responses (FAQ answer + re-anchor, long PRESENT_DAYS lists, etc.).
        self._tts_last_start_ts: float = 0.0
        # True while any TTS chunk is actively being sent to Twilio.
        # Set in on_tts_started(), cleared in on_tts_finished().
        # _speech_recovery checks this as Guard 0 — no recovery phrase can fire
        # while Susie is already speaking, which was the root cause of prompts
        # playing on top of long PRESENT_DAYS / FAQ TTS responses.
        self._tts_playing: bool = False
        # Per-prompt caller-speech guard: True once ANY partial or final
        # transcript arrives for the currently active assistant prompt.
        # Reset in on_question_asked() when a genuinely new question is stored.
        # Watchdog checks this just before emitting a re-ask: if the caller has
        # started speaking, suppress the re-ask (don't talk over them).
        self.prompt_speech_detected: bool = False
        self.prompt_last_speech_ts: Optional[float] = None

    # ── per-prompt speech guard helpers ────────────────────────────────────

    def _reset_prompt_speech_guard_for_new_prompt(self) -> None:
        """Called when a genuinely new assistant prompt is being emitted."""
        self.prompt_speech_detected = False
        self.prompt_last_speech_ts = None
        logger.info(
            "[turn_taking] reset prompt speech guard state=%s flow_step=%d",
            self.current_state, self._replay_flow_step,
        )

    def _mark_prompt_speech_detected(self, source: str, text: str = "") -> None:
        """Record that the caller has started speaking for the current prompt.
        Also cancels any live watchdog task immediately so a re-ask in flight
        does not talk over the caller."""
        if not self.prompt_speech_detected:
            logger.info(
                "[turn_taking] prompt speech detected source=%s text=%r",
                source, (text or "")[:40],
            )
        self.prompt_speech_detected = True
        self.prompt_last_speech_ts = time.monotonic()
        if self._no_input_watchdog_task and not self._no_input_watchdog_task.done():
            self._no_input_watchdog_task.cancel()

    def _prompt_speech_started(self) -> bool:
        return self.prompt_speech_detected

    # ── public API ─────────────────────────────────────────────────────────

    def on_audio_received(self) -> None:
        """Called for every Twilio audio packet (~every 20ms, even during silence).
        Does NOT update last_audio_received_at — use on_speech_started() for that."""
        pass

    def on_speech_started(self, stt_source: bool = False) -> None:
        """Call when STT detects actual speech (partial transcript or energy VAD).

        stt_source — True when the call originates from a genuine STT event
        (PartialTranscript or FinalTranscript from AssemblyAI).  False (default)
        when the call comes from the energy VAD in _handle_media(), which fires on
        any non-silence inbound audio including phone-line echo of Susie's own TTS.

        Cancels the W1/W2/W3 silence cascade timer so Susie doesn't re-ask while
        the caller is speaking.

        WATCHDOG BEHAVIOUR — rolling deadline, no cancel/recreate:
        The no-input watchdog uses last_engagement_at (updated here) to extend its
        internal deadline without being cancelled or recreated.  One task owns the
        watchdog per question generation; partial speech / VAD events are hints that
        advance the deadline, not ownership-change events.

        Only a real final transcript (on_transcript_received) or a flow advance
        (_restart_timer / on_question_asked) should cancel the watchdog via
        _cancel_timer().  This avoids spawn/cancel churn on every VAD event.

        The barge_in_during_tts watchdog cancel is restricted to stt_source=True
        callers.  Energy VAD alone cannot reliably distinguish the caller speaking
        from phone-network sidetone of Susie's own TTS — allowing it to cancel the
        watchdog was the root cause of persistent re-ask after slot presentation
        (watchdog cancelled mid-TTS, restarted only from the terminal chunk).
        """
        _now = time.time()
        self.last_audio_received_at = _now
        # Debounce: only advance the watchdog deadline if at least 500 ms have
        # elapsed since the last update.  This prevents a flood of rapid partial-
        # transcript callbacks from perpetually pushing the deadline forward during
        # a single utterance, while still letting genuine re-engagement events
        # (e.g. caller starts speaking again after a pause) extend it correctly.
        if _now - self.last_engagement_at >= 0.5:
            self.last_engagement_at = _now

        # ── Cancel W1/W2/W3 main timer (caller is speaking; W1 would fire too early) ──
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.currently_reasking = False

        # ── Cancel stale recovery task before starting a new one ──────────────
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()

        # ── Barge-in during TTS: cancel the current-generation watchdog ──────
        # Restricted to stt_source=True (STT partial/final) so that energy VAD
        # from phone-line echo of Susie's TTS cannot falsely cancel the watchdog
        # mid-playback.  Without this guard the watchdog was cancelled before the
        # caller spoke, then restarted only at the terminal TTS chunk — too late
        # for long slot-presentation responses.
        # We also require TTS to still be flagged as playing — that distinguishes
        # a genuine barge-in (Susie was speaking) from a post-TTS speech event
        # where the rolling-deadline model should keep the watchdog alive.
        if stt_source and self._tts_playing:
            if self._no_input_watchdog_task and not self._no_input_watchdog_task.done():
                self._no_input_watchdog_task.cancel()
                self._no_input_watchdog_task = None
                logger.info(
                    "[ms_watchdog] WATCHDOG_CANCEL reason=barge_in_during_tts q_gen=%d",
                    self._q_gen,
                )
        elif self._no_input_watchdog_task is not None and self._no_input_watchdog_task.done():
            self._no_input_watchdog_task = None  # clean up completed reference

        # ── Arm speech-recovery as secondary safety net ────────────────────────
        # Capture the current flow_step so _speech_recovery can detect if the
        # state has advanced during its sleep and suppress a stale prompt.
        _recovery_step = self._replay_flow_step
        _my_q_gen = self._q_gen  # bind recovery to this question generation
        self._recovery_task = asyncio.create_task(
            self._speech_recovery(_recovery_step, _my_q_gen), name="ms_silence_speech_recovery"
        )
        logger.debug(
            "[ms_silence] speech started stt_source=%s — W1 timer cancelled, "
            "recovery armed (step=%d q_gen=%d); watchdog rolling deadline "
            "extended via last_engagement_at",
            stt_source, _recovery_step, _my_q_gen,
        )

    async def _speech_recovery(self, recovery_step: int = -1, q_gen: int = 0) -> None:
        """If STT doesn't transcribe within N seconds of speech detection, prompt the caller.

        The wait window is state-aware (not a hardcoded 5 s):
          - extra_slow states (PRESENT_DAYS, PRESENT_TIMES, COLLECT_REASON,
            CONFIRM_ASSESSMENT): 10 s — caller may still be choosing or thinking
            after a long option list or detailed explanation.
          - medium states (phone, name, day, time):  7 s
          - fast states (greeting, location, confirm yes/no): 5 s

        Guards (evaluated after the sleep, in order):
          0. TTS currently playing — never interrupt Susie mid-sentence.
             This was the primary root cause: energy VAD during PRESENT_DAYS
             playback fired a 5 s recovery that surfaced before the list ended.
          1. Stale flow_step — state has advanced since this recovery was armed.
          2. Minimum response window — TTS finished fewer than 8 s ago (belt-and-
             suspenders backup for Guard 0 in case _tts_playing is momentarily stale).
          3. Recent engagement — last_engagement_at < 3.5 s ago (extended from 2 s;
             consistent with the W1 since_audio guard).
          4. LLM busy or main timer running (transcript already being processed).
          5. STT miss cap — max 2 misses per question (stt_miss_count > 2).

        Sequencing fix (prevents double-fire loop):
          currently_reasking=True while phrase plays (blocks on_tts_started cancel
          and on_tts_finished re-arm), then _restart_timer() is called AFTER a 5 s
          TTS-play wait — exactly as _run() does for W1/W2.
        """
        # ── State-aware wait window ───────────────────────────────────────────
        import os as _os_r
        _env_w1 = _os_r.getenv("SILENCE_WINDOW_1_SEC")
        if _env_w1:
            # In test mode the env override shortens W1; keep recovery proportionally
            # shorter so tests are not blocked by a long recovery sleep.
            _recovery_wait = max(3.0, float(_env_w1) * 0.20)
        else:
            _sess_r = self._get_session() if self._get_session else {}
            _state_r = (_sess_r or {}).get("state", "")
            from app.silence_handler import get_silence_threshold as _gst
            _thresh_r = _gst(_state_r)
            # Scale recovery wait to match state cadence:
            #   extra_slow (≥30 s, e.g. PRESENT_DAYS/TIMES) → 8 s — caller may
            #     still be scanning a long option list or mid-thought.
            #   medium/default (≥10 s, e.g. default 26 s) → 5 s — down from 7 s;
            #     faster re-ask but still clears Guard-3's 3.5 s engagement window.
            #   fast (< 10 s, e.g. phone/name/confirm at 3 s) → 4 s — well above
            #     Guard-3's 3.5 s floor so a single VAD event always passes Guard 3.
            # The 0.5 s gap above Guard-3 (4.0 > 3.5) also protects against a
            # second VAD pulse at T+0.4 s pushing last_engagement_at forward and
            # causing Guard 3 to suppress, which would orphan the call.
            if _thresh_r >= 30.0:
                _recovery_wait = 8.0
            elif _thresh_r >= 10.0:
                _recovery_wait = 5.0
            else:
                _recovery_wait = 4.0

        try:
            await asyncio.sleep(_recovery_wait)
        except asyncio.CancelledError:
            return

        # Guard -1: stale question generation.  If on_question_asked() was called
        # after this recovery task was created, _q_gen has advanced and this task
        # belongs to the previous question — suppress unconditionally.
        # This eliminates stale recovery firing after a state transition and
        # prevents double-fire across re-ask cycles.
        if q_gen != 0 and q_gen != self._q_gen:
            logger.debug(
                "[ms_silence] recovery: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        # Guard -0.5: incomplete-turn continuation hold — flow is holding
        # the floor open for a fragment finalization.  Suppress this
        # recovery prompt so it does not speak on top of the caller; the
        # watchdog owns the single recovery once the hold window expires.
        _sess_ich = self._get_session() if self._get_session else {}
        _ich_until = float((_sess_ich or {}).get("_incomplete_hold_until") or 0.0)
        if time.time() < _ich_until:
            logger.info(
                "[ms_silence] recovery: incomplete-turn hold active (until=%.3f) — suppressing",
                _ich_until,
            )
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        # Guard 0: TTS is currently playing — never fire while Susie is speaking.
        # on_tts_started() sets _tts_playing=True; on_tts_finished() clears it.
        # This is the primary fix for energy VAD triggering recovery during long
        # PRESENT_DAYS / FAQ TTS responses.
        if self._tts_playing:
            logger.debug("[ms_silence] recovery: TTS currently playing — suppressed")
            return

        # Guard 1: stale flow_step — the flow has advanced since we were armed.
        # recovery_step == -1 means no question was active (e.g. greeting);
        # in that case skip step validation.
        if recovery_step != -1:
            _sess_chk = self._get_session() if self._get_session else {}
            _current_step = (_sess_chk or {}).get("flow_step", -1)
            if _current_step != recovery_step:
                logger.debug(
                    "[ms_silence] recovery: stale step stored=%d current=%d — suppressed",
                    recovery_step, _current_step,
                )
                return

        # Guard 2: minimum response window — belt-and-suspenders backup for Guard 0.
        # _tts_done_at is 0.0 at call start (no question asked yet); skip guard then.
        # Threshold is dynamic: _recovery_wait + 0.5 s.  This scales the echo-
        # protection window to the state's recovery cadence so that a legitimate
        # VAD event (caller spoke ≥ 0.5 s after TTS ended) always passes Guard 2
        # after one _recovery_wait sleep, while a near-instant echo (< 0.5 s) is
        # still suppressed and handled by the watchdog re-arm below.
        # Previously hardcoded at 8.0 s, which meant fast-state (3 s threshold)
        # recovery always suppressed here even 5 s after TTS — causing 8–9 s
        # total dead air instead of ~4 s.
        _guard2_min = _recovery_wait + 0.5
        if self._tts_done_at > 0 and (time.time() - self._tts_done_at) < _guard2_min:
            logger.debug(
                "[ms_silence] recovery: TTS finished only %.1fs ago (guard2_min=%.1fs) — suppressing premature re-ask",
                time.time() - self._tts_done_at, _guard2_min,
            )
            # Re-arm only if the no-input watchdog is NOT already live.
            # If it is running it will fire 3 s after TTS ends — resetting it
            # here would cancel the countdown and create a restart loop where
            # recovery perpetually resets the watchdog without ever letting it fire.
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        # Guard 3: recent engagement — extended from 2.0 s to 3.5 s to match the
        # W1 since_audio guard.  Protects split answers and delayed STT finals.
        since_engagement = time.time() - self.last_engagement_at
        if since_engagement < 3.5:
            logger.debug(
                "[ms_silence] recovery: recent engagement (%.1fs ago) — suppressing prompt",
                since_engagement,
            )
            # Same watchdog-preservation logic as Guard 2: only restart timers
            # when the watchdog is not already counting down.  The leading cause
            # of the perpetual-reset loop is recovery waking at T+3 (recovery_wait)
            # when last_engagement_at is T+0 — since_engagement=3.0 < 3.5 fires
            # this guard, which calls _restart_timer(), which cancels + resets
            # the watchdog, which cancels the just-armed watchdog — repeat forever.
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        # Guard 4: LLM busy or main timer running (transcript already being processed)
        if self._llm_busy or not (self._task is None or self._task.done()):
            return

        # Guard 5: STT miss cap — max 2 recovery prompts per question.
        # _stt_miss_count is reset ONLY by on_transcript_received, never by
        # on_tts_started, so this cap is now effective across re-arm cycles.
        self._stt_miss_count += 1
        if self._stt_miss_count > 2:
            logger.info(
                "[ms_silence] recovery: STT miss #%d — cap reached, suppressing prompt",
                self._stt_miss_count,
            )
            # Re-arm only if watchdog is not already live (same restart-loop
            # prevention as Guard 2/3 above).
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        _sess  = self._get_session() if self._get_session else {}
        _state = (_sess or {}).get("state", "")

        logger.info(
            "[ms_silence] recovery: STT miss #%d — prompting (step=%d state=%s tts_age=%.1fs)",
            self._stt_miss_count, recovery_step, _state,
            (time.time() - self._tts_done_at) if self._tts_done_at > 0 else -1.0,
        )

        # DTMF digits already in buffer — caller is actively typing.
        # Reset the silence timer silently; do not interrupt with a spoken prompt.
        if (_sess or {}).get("phone_dtmf_buffer") and _state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
            "RETURNING_PLAN_COLLECT_PHONE",
        ):
            self._restart_timer()
            return

        # State-specific repair prompts
        if _state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
            if (_sess or {}).get("lookup_correction_mode"):
                phrase = "Sorry — what first name and surname was the booking under?"
            elif (_sess or {}).get("rc_stage") == "lookup_done":
                phrase = "Sorry — was that the right appointment? Yes or no?"
            else:
                phrase = "Sorry — just bear with me while I look up your appointment."
        elif _state in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            _lq = (_sess or {}).get("last_question", "")
            phrase = _lq if _lq else "Sorry — which day works best for you?"
        elif _state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING", "RETURNING_PLAN_CONFIRM_PHONE"):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please say: use this number — "
                "or: do not use this number."
            )
        elif (_sess or {}).get("phone_awaiting_dtmf"):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please enter the phone number using your keypad."
            )
        elif _state in (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        ):
            # Use NC substate to pick the right scaffold prompt.
            # name_fragment == NC's first_name (set when first name is stored).
            # When present we are in the surname step; otherwise first-name step.
            _nf = (_sess or {}).get("name_fragment")
            if _nf:
                phrase = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my surname is..."
                )
            else:
                phrase = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my first name is..."
                )
        elif _state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
            "RETURNING_PLAN_COLLECT_PHONE",
        ):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please say the phone number slowly."
            )
        elif _state in ("GREETING", "DETECT_INTENT", ""):
            # v3 location re-ask ladder: when the location question is active,
            # escalate to "Did you say the Alcester clinic?" on the 2nd attempt.
            _sr_sess = self._get_session() if self._get_session else {}
            if (_sr_sess or {}).get("v3_location_q_active"):
                _sr_lrc = int((_sr_sess or {}).get("v3_location_reask_count", 0))
                if _sr_lrc == 0:
                    # Rung 2 — biased confirm; arms the use-this-clinic handler.
                    # (The open question was already the first ask; the first
                    # re-ask escalates straight to the biased confirm — same as
                    # the watchdog ladder.)
                    phrase = _LOC_RUNG2_CONFIRM
                    if _sr_sess:
                        _sr_sess["v3_awaiting_use_this_clinic"] = True
                        _sr_sess["last_question"] = phrase
                        _sr_sess["last_bot_prompt"] = phrase
                        _sr_sess["v3_use_this_clinic_bias"] = "alcester"
                        logger.info(
                            "[ms_conn v3] silence ladder rung 2"
                            " (biased confirm) — bias=alcester",
                        )
                else:
                    # Rung 3 — DTMF keypad fallback; deterministic terminal.
                    phrase = _LOC_RUNG3_DTMF
                    if _sr_sess:
                        _sr_sess["v3_awaiting_location_dtmf"] = True
                        _sr_sess["v3_awaiting_use_this_clinic"] = False
                        _sr_sess["v3_location_q_active"] = False
                        _sr_sess["last_question"] = phrase
                        _sr_sess["last_bot_prompt"] = phrase
                        logger.info(
                            "[ms_conn v3] silence ladder rung 3 (DTMF keypad)",
                        )
                if _sr_sess:
                    _sr_sess["v3_location_reask_count"] = _sr_lrc + 1
            else:
                phrase = "Sorry, I didn't quite catch that. Are you calling to book, reschedule, or cancel an appointment?"
        elif _state == "ASK_LOCATION":
            phrase = "Sorry, I didn't catch that. Which of our locations were you looking for — the Awlstuh clinic or the Redditch clinic?"
        else:
            phrase = "Sorry — I'm having a little trouble hearing you. Could you say that again?"

        # Set currently_reasking BEFORE enqueuing the phrase.
        # This prevents the double-fire loop:
        #   on_tts_started() checks `if not self.currently_reasking` before cancelling
        #   the silence timer; with currently_reasking=True it does nothing.
        #   on_tts_finished() returns early when currently_reasking=True, so it does
        #   not re-arm the timer — preventing W1 from firing a second phrase 26 s later.
        self.currently_reasking = True
        await self._tts_text_queue.put(phrase)
        if self._on_reask:
            asyncio.create_task(self._on_reask(phrase))

        # Wait ~5 s for TTS to finish, then hand off to the main cascade (_run).
        # _restart_timer() is called HERE (after the phrase plays), not before.
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False
        self._restart_timer()

    def set_state(self, state: str) -> None:
        """Update current_state so re-ask phrases are context-aware."""
        if state:
            self.current_state = state

    def on_question_asked(self, question: str) -> None:
        """Update last_question so re-asks have the right text.

        Also explicitly arms the no-input watchdog for the new q_gen.  Previously
        the watchdog was only armed in on_tts_finished when its is_question
        heuristic matched the final chunk text — which misses prompts whose
        tail chunk lacks a "?" and a listed keyword (e.g. initial COLLECT_NAME's
        "You can say: my first name is..." tail).  Arming here removes the
        dependency on per-chunk text heuristics: every flow question state that
        invokes on_question_asked is covered.  _restart_timer is idempotent
        per-q_gen so a subsequent tts_finished call is harmless.  _watchdog_grace_until
        (set at final audible completion in on_tts_finished) re-anchors the
        deadline to tts_finished + _wait, so arming earlier does NOT shorten
        the caller's real answer window.
        """
        if not question or not question.strip():
            return
        if _is_question_worth_storing(question):
            self.last_question         = question.strip()
            self.reask_count           = 0
            self._no_input_reask_count = 0  # new question — reset dead-air watchdog counter
            self._last_question_set_at = time.time()
            self._q_gen               += 1   # new question = new silence generation
            self._watchdog_has_retired = False  # new question — watchdog may fire again
            # Reset per-prompt caller-speech guard: this is a genuinely new
            # assistant question, so the next watchdog arm should fire normally
            # on true silence but suppress if the caller starts speaking first.
            self._reset_prompt_speech_guard_for_new_prompt()
            _session = self._get_session() if self._get_session else None
            self._replay_flow_step = (_session or {}).get("flow_step", -1) if _session else -1

            # Stale pause clearance: if caller_pause_active was set for a prior
            # question generation, a genuinely new flow question means the flow
            # has advanced past the pause context. Clear it so the watchdog can
            # re-ask on silence. DTMF-collect states don't invoke on_question_asked
            # mid-digit entry, so keypad flows are unaffected.
            if _session and _session.get("caller_pause_active"):
                _pause_q_gen = _session.get("caller_pause_q_gen")
                # Only clear when pause is tagged to a DIFFERENT (older) question
                # generation. A pause with no tag yet (None) is being freshly set
                # in the same event and must not be prematurely cleared.
                if _pause_q_gen is not None and _pause_q_gen != self._q_gen:
                    _session["caller_pause_active"] = False
                    _session["pause_silence_total"] = 0.0
                    _session.pop("caller_pause_q_gen", None)
                    _session.pop("caller_pause_state", None)
                    logger.info(
                        "[ms_pause] cleared: reason=new_question_asked old_q_gen=%s new_q_gen=%d",
                        _pause_q_gen, self._q_gen,
                    )

            # ── Explicit watchdog arm for EVERY flow question ─────────────────
            # Don't rely on on_tts_finished's keyword heuristic to arm the
            # no-input watchdog.  Fixes initial COLLECT_NAME (whose tail TTS
            # "You can say: my first name is..." matches no keyword and misses
            # "?" anchoring) and any other state whose tail chunk fails the
            # is_question heuristic.  DTMF-expected states are skipped by
            # _restart_timer's own guard.  Idempotent per-q_gen.
            if not _is_dtmf_expected(_session):
                self._restart_timer()
                logger.debug(
                    "[ms_silence] on_question_asked: watchdog armed q_gen=%d q=%r",
                    self._q_gen, self.last_question[:60],
                )

    def on_tts_started(self) -> None:
        """Track TTS activity and cancel silence/recovery timers before Susie speaks.

        _tts_playing is set unconditionally (even when currently_reasking=True) so
        _speech_recovery Guard 0 reliably suppresses recovery while the recovery
        phrase itself is playing — preventing a second recovery firing on top of
        the first.

        _recovery_task is cancelled when NOT currently_reasking: if TTS is starting
        for a flow response (not a re-ask), any pending recovery is stale because the
        flow has already decided to speak again.  Cancelling it here prevents the
        race where energy VAD fires during Susie's response, a 7-10 s recovery task
        starts, and the task later fires its prompt after the real response has ended.

        WATCHDOG: the no-input watchdog is intentionally NOT cancelled here.
        Previously _cancel_timer() was called, which killed the watchdog for ALL TTS
        events including non-question bridge/filler phrases.  When a non-question
        phrase's on_tts_finished() ran, is_question=False so _restart_timer() was
        never called, orphaning the call permanently.  The watchdog now survives TTS:
        Guard 2 (_tts_playing) suppresses it while audio is playing and re-arms it
        so it fires 3 s after TTS ends if no caller response arrives.

        NOTE: _stt_miss_count is intentionally NOT reset here.  It must only
        reset when a real caller transcript arrives (on_transcript_received).
        Resetting here allowed recovery to loop: miss→TTS starts→reset→miss→repeat.
        """
        self._tts_playing = True  # always track, even during re-ask playback
        self._tts_last_start_ts = time.time()  # record when this chunk started
        if not self.currently_reasking:
            # Cancel main silence timer (_task / W1-W2-W3) — Susie is speaking.
            # Do NOT cancel the no-input watchdog: it must survive non-question TTS
            # so it can fire once the audio ends if caller still hasn't responded.
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            # Cancel stale recovery task — TTS starting without a fresh transcript
            # means either (a) the flow responded to a previous utterance (recovery
            # is moot) or (b) energy VAD fired and a recovery task is pending; in
            # both cases the task would be stale by the time it wakes up.
            if self._recovery_task and not self._recovery_task.done():
                self._recovery_task.cancel()
                self._recovery_task = None
            logger.debug("[ms_silence] TTS started — W1 timer cancelled (watchdog preserved)")

    def on_llm_started(self) -> None:
        """Called when the LLM begins processing — suppress silence timer."""
        self._llm_busy = True
        self._cancel_timer()
        logger.debug("[ms_silence] LLM started — timer cancelled")

    def on_llm_finished(self) -> None:
        """Called when the LLM finishes processing — allow silence timer again.

        Spec Z: if a watchdog start was deferred because _llm_busy was True,
        fire it now — but only when TTS has also finished.  If TTS is still
        playing, _tts_playing=True; on_tts_finished() will call _restart_timer()
        naturally once audio ends (at which point _llm_busy=False and the normal
        question check in _restart_timer applies).
        """
        self._llm_busy = False
        if self._watchdog_deferred and not self._tts_playing and not self._cancelled:
            self._watchdog_deferred = False
            logger.info(
                "[ms_watchdog] WATCHDOG_DEFERRED_FIRE"
                " reason=llm_complete q_gen=%d last_q=%r",
                self._q_gen,
                (self.last_question or "")[:50],
            )
            self._restart_timer()
        elif self._watchdog_deferred:
            # TTS still playing — clear deferred flag so on_tts_finished()
            # runs normally; it will call _restart_timer() with _llm_busy=False.
            self._watchdog_deferred = False
            logger.info(
                "[ms_watchdog] WATCHDOG_DEFERRED_CLEAR"
                " reason=tts_still_playing q_gen=%d",
                self._q_gen,
            )

    # ── Spec Z — prompt question check ──────────────────────────────────────────

    _WATCHDOG_QUESTION_SIGNALS = frozenset({
        "which", "what", "when", "where", "who", "how",
        "would", "could", "can", "shall", "will",
        "is that", "do you", "did you", "have you", "are you",
        "is there", "was it",
    })

    def _prompt_contains_question(self, prompt: str) -> bool:
        """Return True if prompt contains a clear patient-facing question.

        Affirmation-only prompts like "Of course —" or "No problem." return
        False and must never start a watchdog countdown.

        Matches:
          • Any prompt ending with "?"
          • Prompts containing a question word / modal directed at the patient
        """
        stripped = prompt.strip().rstrip("—–-").strip()
        if not stripped:
            return False
        # Hard fast-path: question mark anywhere in the prompt
        if "?" in stripped:
            return True
        _lower = stripped.lower()
        return any(
            _lower.endswith(sig) or f" {sig} " in _lower
            for sig in self._WATCHDOG_QUESTION_SIGNALS
        )

    # ────────────────────────────────────────────────────────────────────────────

    def restart_for_question(self, question: str) -> None:
        """Re-arm the silence timer after fragment suppression (Bug 9 / Bug 6).
        Ensures the silence handler keeps waiting for a real utterance instead
        of going permanently silent when a fragment was discarded."""
        if question and question.strip():
            self.last_question = question.strip()
        self._restart_timer()
        logger.info("[ms_silence] restart_for_question: %r", (self.last_question or "")[:60])

    def on_tts_finished(self, text: str, chunk_started_at: float = 0.0) -> None:
        """After a flow question finishes playing, arm the silence timer.
        Never restarts timer while currently_reasking — _run() owns its timing.
        Never arms timer while LLM is still processing — the delayed TTS-done
        callback for the *previous* question can fire after on_transcript_received()
        cancels the timer but before _llm_busy is set; without this guard the
        timer re-arms and can fire during the check_availability tool call,
        causing a spurious re-ask concatenated with the slot list.
        Never arms if more TTS chunks are queued — prevents stacking re-asks
        after multi-part responses (FAQ answer + re-anchor question).

        chunk_started_at — the _tts_last_start_ts value captured when this chunk
        began synthesis (set in _tts_loop before the sub-chunk loop).  If a newer
        chunk has since started (_tts_last_start_ts > chunk_started_at), we must
        NOT clear _tts_playing — doing so would open a window where _speech_recovery
        Guard 0 passes while the new chunk is still playing.  This was the root
        cause of false recovery firing during long multi-chunk FAQ / PRESENT_DAYS
        responses."""
        # Conditionally clear _tts_playing — only if no newer TTS chunk has started.
        # When chunk N's _delayed_tts_finished fires while chunk N+1 is already
        # playing, _tts_last_start_ts will be > chunk_started_at (chunk N's timestamp),
        # so we leave _tts_playing=True and Guard 0 stays effective.
        if chunk_started_at == 0.0 or chunk_started_at >= self._tts_last_start_ts:
            self._tts_playing = False
        # else: a newer chunk is actively playing — preserve _tts_playing=True
        if self._cancelled:   # Bug 3: stale TTS callbacks must not restart after teardown
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return
        # Suppress if more TTS chunks are still pending (multi-part response)
        if not self._tts_text_queue.empty():
            logger.debug("[ms_silence] on_tts_finished: more TTS pending — suppressing timer")
            return
        if self.reask_count >= 1:
            # W1 (or both re-asks) has already fired; _run() owns its timing
            # through W2 and W3.  Do NOT restart the timer here — that would
            # cancel the in-progress _run() coroutine (e.g. the W2 sleep) and
            # prevent W2 / W3 from ever triggering.
            # reask_count is reset to 0 by on_transcript_received, so this
            # guard is lifted as soon as the caller speaks again.
            return
        if self._watchdog_has_retired:
            # The no-input watchdog has already exhausted re-asks for this
            # question generation.  A late TTS callback must not re-arm the
            # silence timer and restart the watchdog cycle — that would loop
            # indefinitely.  The flag is reset on the next on_question_asked().
            logger.debug("[ms_silence] on_tts_finished: watchdog retired — suppressing timer")
            return
        t = text.strip()
        if t.startswith("Sorry,") or t.startswith("Sorry about") or "didn't quite catch" in t:
            return  # Never restart timer for re-ask phrases
        # Guard: if the caller spoke more than 1 s after the last question was
        # set, on_transcript_received() already cancelled the timer for this
        # turn.  A late TTS-done callback (audio still playing when caller
        # spoke) must not re-arm the timer — doing so causes a spurious re-ask
        # ~26 s later.
        #
        # The 1 s floor prevents the energy VAD from poisoning this guard.
        # When on_question_asked() arms the watchdog, the audio-input loop
        # can fire on_speech_started() (energy VAD) within the same asyncio
        # tick — updating last_audio_received_at by ~1 ms.  Without the
        # floor, that 1 ms is > 0 and the guard fires incorrectly, preventing
        # on_tts_finished from ever re-arming the timer.  No human responds
        # within 1 s of on_question_asked (the question TTS hasn't even
        # started playing yet), so the floor is safe.
        if self.last_audio_received_at > self._last_question_set_at + 1.0:
            logger.debug(
                "[ms_silence] on_tts_finished: late TTS callback "
                "(audio received >1 s after question set) — suppressing timer restart"
            )
            return
        # ── Anchor the watchdog deadline to final audible completion ─────────
        # A question is considered "active" once on_question_asked has bound it
        # (last_question is set and _last_question_set_at is live).  Any final
        # TTS chunk reaching this point (intermediate-chunk suppression guard
        # above has already cleared) is the audible completion of an active
        # question OR a post-question bridge — either way the caller's answer
        # window should begin NOW.  Moved out of the is_question branch so
        # prompts whose final chunk text misses the heuristic keyword list
        # (e.g. COLLECT_NAME's "You can say: my first name is..." tail,
        # CONFIRM_PHONE's "say: use this number.") still anchor correctly.
        # Guarded by "question active within 60 s" so unrelated tail TTS long
        # after a transcript cannot hold a stale deadline indefinitely.
        # Risk-2 fix: also require this chunk started at or after the current
        # question was armed (_last_question_set_at).  Chunks from a prior turn
        # (chunk_started_at < _last_question_set_at) must not roll the live
        # grace window forward.  chunk_started_at == 0.0 means legacy/unknown —
        # allow (safe direction).
        if (
            self.last_question
            and (time.time() - self._last_question_set_at) < 60.0
            and (chunk_started_at == 0.0 or chunk_started_at >= self._last_question_set_at)
        ):
            self._watchdog_grace_until = max(self._watchdog_grace_until, time.time())
            logger.debug(
                "[ms_silence] grace_anchor: q_gen=%d chunk_ts=%.3f q_ts=%.3f grace_until=%.3f",
                self._q_gen, chunk_started_at, self._last_question_set_at,
                self._watchdog_grace_until,
            )
        is_question = (
            t.endswith("?") or
            any(p in t.lower() for p in [
                "what brings", "how long", "does that", "been with us",
                "work best", "full name", "reach you", "which would",
                "sound ok", "that right", "help you", "how can i",
                "your name", "your number", "shall i", "slot would",
            ])
        )
        if is_question:
            if _is_question_worth_storing(t):
                if not self.last_question:
                    # last_question was cleared by on_transcript_received and
                    # on_question_asked hasn't set it yet — let TTS completion fill it.
                    # Extract only the final question sentence so re-asks don't replay
                    # a full multi-sentence FAQ response (e.g. "The clinic is open Mon–Fri
                    # 8:30am–9pm. Would you like to book?" → re-ask = "Would you like to
                    # book?" not the whole opening-hours paragraph).
                    import re as _re
                    _parts = _re.split(r'(?<=[.!?])\s+|\n+', t)
                    _q = next(
                        (p.strip() for p in reversed(_parts) if p.strip().endswith('?')),
                        t,
                    )
                    self.last_question = _q
                    logger.debug("[ms_silence] on_tts_finished: last_question set → %r", _q[:60])
                else:
                    # on_question_asked already set last_question — do NOT overwrite.
                    # A stale TTS chunk completing after a step transition must never
                    # replace the live question (e.g. full-day phrase finishing after
                    # constrained offer was already committed).
                    logger.debug(
                        "[ms_silence] on_tts_finished: last_question already live %r — not overwriting stale %r",
                        self.last_question[:40], t[:40],
                    )
            # Record when the question's audio finished so _speech_recovery can
            # enforce a minimum response window before firing a premature re-ask.
            # (_watchdog_grace_until is set above, before the is_question split,
            # so both heuristic-matched and keyword-miss prompts re-anchor.)
            self._tts_done_at = time.time()
            self._restart_timer()
            logger.info("[ms_silence] timer restarted: %r", t[:50])
        elif self._task is None:
            # FIX C: Non-question TTS must NEVER arm or restart the silence
            # timer.  Only the `if is_question:` branch above can arm it.
            # This is a hard guarantee — bridge phrases ("Got that.",
            # "Of course — good to have you back.", "No problem — let's get
            # you sorted."), barge-in acks ("Sorry — go ahead."), and any
            # other non-question speech cannot own silence timing.  The timer
            # is armed exclusively when a real question finishes playing.
            logger.debug(
                "[ms_silence] non-question TTS — NOT arming timer: %r", t[:50]
            )

    def on_transcript_received(self, text: str = "") -> None:
        """Call whenever a FinalTranscript arrives from STT."""
        # Guard: garbage / junk finals (single chars, noise-only) must NOT cancel
        # the watchdog — the caller hasn't answered; the watchdog should fire.
        # Reuse the same _is_garbage_transcript predicate used by the STT stream
        # so both filters stay aligned if the predicate is ever updated.
        from app.media_streams.stt_stream import _is_garbage_transcript as _is_garbage_sil
        if _is_garbage_sil(text or ""):
            logger.info(
                "[ms_silence] garbage_transcript=%r — watchdog preserved", text
            )
            return
        self._cancel_timer()
        # Cancel recovery task — transcript arrived, no re-arm needed
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task              = None
        self.reask_count                 = 0
        self._stt_miss_count             = 0  # real transcript — reset STT miss counter
        self._no_input_reask_count       = 0  # real transcript — reset dead-air watchdog counter
        self._consecutive_silence_count  = 0
        self.currently_reasking          = False
        self.last_audio_received_at      = time.time()
        self.last_engagement_at          = time.time()
        self.last_question               = ""
        self._replay_flow_step           = -1
        logger.info("[ms_silence] transcript — timer cancelled")

    def cancel(self) -> None:
        """Cancel the timer. Call when the call ends."""
        self._cancelled = True  # synchronous flag — prevents stale re-asks/transfers racing asyncio
        self._cancel_timer()
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task = None

    # ── internal ───────────────────────────────────────────────────────────

    async def _no_input_watchdog(self, armed_at: float, q_gen: int) -> None:
        """Dead-air watchdog: fires after _wait seconds of continuous caller quiet.

        Single-owner rolling-deadline model.  Created once per question generation
        by _restart_timer.  Speech activity (VAD / partials) updates
        last_engagement_at; the watchdog extends its internal deadline by
        recomputing the remaining sleep on each iteration — no cancel/recreate
        needed per speech event.

        Only strong terminal events cancel this task via _cancel_timer():
          - final transcript received  (on_transcript_received)
          - new question / flow advance (on_question_asked / _restart_timer)
          - call cleanup               (_cancelled flag set)

        Escalation:
          Attempt 1 — state-specific "Sorry, I didn't catch that — ..."
          Attempt 2 — "I'm sorry, I'm still not hearing you — ..."
          Attempt 3+ — graceful exit phrase → _transfer()
        """
        import os as _os_w
        _wait = float(_os_w.getenv("NO_INPUT_WATCHDOG_SEC", "3.25"))
        if _wait <= 0:
            return
        # Relax watchdog patience in FAQ offer states.  After the AI finishes
        # speaking a FAQ answer + re-anchor, the caller naturally pauses to
        # process the information before deciding to ask another question or
        # proceed to booking.  A 4.5-second deadline gives callers a more natural
        # response window without feeling rushed.
        # 8 seconds matches the extra-slow PRESENT_DAYS/TIMES threshold and gives
        # the caller comfortable thinking time without feeling abandoned.
        _sess_faq_w = self._get_session() if self._get_session else {}
        if (_sess_faq_w or {}).get("state") in ("FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER"):
            _wait = max(_wait, 8.0)
            logger.info("[ms_watchdog] FAQ offer state — extended wait to %.1fs", _wait)
        # Greeting states: caller needs time to process the greeting and respond naturally.
        # 6 s post-TTS is generous without feeling abandoned on no-answer calls.
        if (_sess_faq_w or {}).get("state") in ("GREETING", "DETECT_INTENT", ""):
            _wait = max(_wait, 6.0)
            logger.info("[ms_watchdog] greeting_grace=%.1fs", _wait)
        # theorem_v3 location question — two-tier grace (CODE SPEC AH):
        #   First response (patient hasn't spoken yet): 9 s so shy callers
        #   aren't rushed before they've had a chance to reply at all.
        #   Subsequent attempts (patient already spoke but was garbled): 2.5 s
        #   so a fast biased-confirm re-ask fires without 10 s dead air.
        if (_sess_faq_w or {}).get("v3_location_q_active"):
            if (_sess_faq_w or {}).get("_location_q_patient_spoke"):
                _wait = max(_wait, 2.5)
                logger.info(
                    "[ms_watchdog] location_q_grace=%.1fs"
                    " (patient already spoke — fast re-ask)",
                    _wait,
                )
            else:
                _wait = max(_wait, 4.0)
                logger.info(
                    "[ms_watchdog] location_q_grace=%.1fs"
                    " (first response — shy-caller grace)",
                    _wait,
                )
        # theorem_v3 slot selection: the LLM just read out up to 3 dated options.
        # Now that multi_day presents ONE day + ONE time per option (short list,
        # ~7 s of audio instead of ~12 s), the caller has far less to process, so
        # the grace is back to 10 s (down from 15 s — the 15 s was sized for the
        # old 3-days x 2-times list).  Still comfortably above the 6-8 s natural
        # pause floor; do NOT drop toward the 5 s zone that once cut off a
        # thinking caller (P0 06dd4cb).
        if (_sess_faq_w or {}).get("v3_awaiting_slot_selection"):
            _wait = max(_wait, 10.0)
            logger.info(
                "[ms_watchdog] slot_selection_grace=%.1fs (v3_awaiting_slot_selection)",
                _wait,
            )
        # theorem_v3 phone confirmation: flow_step=0 is an explicit sentinel set
        # by the flow engine at the start of the phone-confirm phase (both the
        # keypad-request turn and the digit-readback turn) and reset to -1 after
        # each turn.  It is never present during greeting/location/other turns
        # (session key absent → .get returns None, not 0), so keying solely off
        # flow_step=0 is safe and covers both turns correctly.
        # The previous guard also checked last_bot_prompt for phone-signal words,
        # which worked for the keypad-request turn ("keypad" in prompt) but
        # missed the digit-readback turn ("Just to confirm — that's 0 7..."),
        # causing WATCHDOG_START wait=6.0s instead of 10.0s on the readback.
        if (
            (_sess_faq_w or {}).get("flow_step") == 0
            and not (_sess_faq_w or {}).get("v3_location_q_active")
        ):
            _wait = max(_wait, 10.0)
            logger.info(
                "[ms_watchdog] phone_confirm_grace=%.1fs (flow_step=0)",
                _wait,
            )
        # theorem_v3 preference question: the caller is being asked an open
        # preference question ("mornings or afternoons?", "particular day or
        # time?", "what works better for you?").  These aren't hard slot-choice
        # decisions but the caller still needs a moment to consider — 8 s
        # matches location_q_grace.
        _last_bot_w = (_sess_faq_w or {}).get("last_bot_prompt", "").lower()
        if (
            "mornings or afternoons" in _last_bot_w
            or "better for you" in _last_bot_w
            or "particular day or time" in _last_bot_w
        ):
            _wait = max(_wait, 8.0)
            logger.info("[ms_watchdog] preference_q_grace=%.1fs", _wait)
        # Caller-choice states: the AI has just asked a question that requires
        # the caller to parse spoken content and make a decision between multiple
        # options (pick a clinic, pick a day, pick a slot, confirm which
        # appointment).  Callers routinely pause 5-7 s while deliberating between
        # options, so raise the floor to 8 s.
        #
        # Simple yes/no confirmations (CONFIRM_PHONE, CONFIRM_BOOKING,
        # CONFIRM_RESCHEDULE*) and binary mid-flow questions (ASK_NEW_OR_RETURNING)
        # are intentionally NOT listed here — their answer space is small and a
        # post-audio 4.5 s default feels natural rather than sluggish.  The
        # deadline is anchored to final tts_finished (see _watchdog_grace_until
        # update in on_tts_finished), so 4.5 s post-audio is the true window.
        _CHOICE_GRACE_STATES = (
            "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
            "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
        )
        _sess_state_w = (_sess_faq_w or {}).get("state", "")
        _sess_rc_stage = (_sess_faq_w or {}).get("rc_stage", "")
        # COLLECT_REASON: caller needs time to articulate symptoms / reason
        # for visit. A short 4.5 s window cuts people off mid-thought; 7.5 s
        # gives a more natural pause for open-ended recall.
        if _sess_state_w == "COLLECT_REASON":
            _wait = max(_wait, 7.5)
            logger.info(
                "[ms_watchdog] reason_grace state=%s wait=%.1fs",
                _sess_state_w, _wait,
            )
        # ASK_LOCATION: binary choice between two named clinics — 4.5 s is
        # sufficient deliberation time without over-patience on dead air.
        elif _sess_state_w == "ASK_LOCATION":
            _wait = max(_wait, 4.5)
            logger.info(
                "[ms_watchdog] choice_grace state=%s wait=%.1fs",
                _sess_state_w, _wait,
            )
        elif _sess_state_w in _CHOICE_GRACE_STATES:
            _wait = max(_wait, 8.0)
            logger.info(
                "[ms_watchdog] choice_grace state=%s wait=%.1fs",
                _sess_state_w, _wait,
            )
        elif _sess_state_w in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
            # LOOKUP states present a read-back of the found appointment — the
            # caller is parsing it — so a longer window is usually appropriate.
            # Exception: when rc_stage=="lookup_done" the prompt is a short
            # binary "Is that you?" confirmation that needs only default timing.
            if _sess_rc_stage != "lookup_done":
                _wait = max(_wait, 8.0)
                logger.info(
                    "[ms_watchdog] choice_grace state=%s rc_stage=%s wait=%.1fs",
                    _sess_state_w, _sess_rc_stage, _wait,
                )
            else:
                logger.info(
                    "[ms_watchdog] lookup_confirm state=%s rc_stage=lookup_done "
                    "→ binary confirmation wait=%.1fs",
                    _sess_state_w, _wait,
                )

        # Ownership check: yield once so any pending cancellation of a superseded
        # task is delivered before we log WATCHDOG_START.  If a newer watchdog task
        # has already been assigned to _no_input_watchdog_task, this task is stale
        # and should exit silently rather than emit a duplicate WATCHDOG_START line.
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return  # superseded before we even started
        if asyncio.current_task() is not self._no_input_watchdog_task:
            return  # a newer task took ownership — exit silently

        logger.info("[ms_watchdog] WATCHDOG_START q_gen=%d wait=%.1fs", q_gen, _wait)
        self._reask_completed = False  # new q_gen — safety net may fire if needed

        while True:
            # ── Phase 1: Roll to deadline ─────────────────────────────────
            # Sleep until _wait seconds of continuous quiet since last activity.
            # last_engagement_at is updated by on_speech_started() / on_transcript_received().
            # If it advances while we sleep, the next loop iteration recomputes
            # _remaining and extends the deadline — no new task needed.
            while True:
                _last_activity = max(armed_at, self.last_engagement_at, self._watchdog_grace_until)
                _remaining = (_last_activity + _wait) - time.time()
                if _remaining <= 0.02:
                    break  # deadline reached — proceed to guards
                try:
                    await asyncio.sleep(_remaining)
                    await asyncio.sleep(0)  # deliver any pending cancels
                except asyncio.CancelledError:
                    logger.info("[ms_watchdog] WATCHDOG_CANCEL q_gen=%d", q_gen)
                    return

            # ── Phase 2: Terminal guards (abort — do not loop) ────────────
            if self._cancelled:
                logger.info("[ms_watchdog] WATCHDOG_ABORT q_gen=%d reason=call_cancelled", q_gen)
                return

            # DTMF-expected guard (terminal): while the flow is waiting for
            # keypad input (booking / returning / reschedule / cancel / FAQ
            # location / location-fallback), the speech watchdog must not
            # fire a generic "Sorry, I didn't catch that" re-ask.  Abort so
            # any DTMF-specific reminder is owned by the flow, not by this
            # speech-first re-ask path.  A fresh watchdog will be armed by
            # _restart_timer() once the flow leaves keypad mode.
            _sess_dtmf_exp = self._get_session() if self._get_session else {}
            if _is_dtmf_expected(_sess_dtmf_exp):
                logger.info(
                    "[ms_watchdog] WATCHDOG_ABORT q_gen=%d reason=dtmf_expected",
                    q_gen,
                )
                return

            if q_gen != 0 and q_gen != self._q_gen:
                logger.info(
                    "[ms_watchdog] WATCHDOG_ABORT q_gen=%d reason=stale_question current=%d",
                    q_gen, self._q_gen,
                )
                return

            _sess = self._get_session() if self._get_session else {}
            if (_sess or {}).get("caller_pause_active"):
                # Only honor pause when it is bound to the current question generation.
                # A pause tied to an older q_gen is stale (flow has advanced past the
                # pause context) and must not suppress re-asks for the fresh question.
                _pause_q_gen = (_sess or {}).get("caller_pause_q_gen")
                if _pause_q_gen is None or _pause_q_gen == self._q_gen:
                    logger.info(
                        "[ms_watchdog] pause_mode active and valid -> aborting re-ask q_gen=%d",
                        q_gen,
                    )
                    return
                logger.info(
                    "[ms_watchdog] pause_mode stale for q_gen=%d (pause_q_gen=%s current=%d) -> ignoring",
                    q_gen, _pause_q_gen, self._q_gen,
                )

            # ── Phase 3: Soft guards (wait 0.5 s, then re-evaluate) ──────
            # TTS playing: Susie is speaking — wait; last_engagement_at is NOT
            # updated during TTS so the deadline stays fixed and fires immediately
            # once _tts_playing clears.
            if self._tts_playing:
                try:
                    await asyncio.sleep(0.5)
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    logger.info("[ms_watchdog] WATCHDOG_CANCEL q_gen=%d", q_gen)
                    return
                continue

            # DTMF guard: caller is actively entering a phone number on the keypad.
            # Uses last_dtmf_at (not phone_dtmf_buffer) so a buffer-clear race cannot
            # produce a false fire.  _DTMF_QUIET_SEC gives the caller time between
            # individual keypresses without triggering a no-input re-ask.
            _DTMF_QUIET_SEC = 5.0
            _sess_dtmf = self._get_session() if self._get_session else {}
            if (_sess_dtmf or {}).get("state") in (
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
                "RETURNING_PLAN_COLLECT_PHONE",
            ):
                if (time.time() - self.last_dtmf_at) < _DTMF_QUIET_SEC:
                    logger.debug(
                        "[ms_watchdog] WATCHDOG_DTMF_HOLD q_gen=%d last_dtmf=%.1fs ago",
                        q_gen, time.time() - self.last_dtmf_at,
                    )
                    try:
                        await asyncio.sleep(0.5)
                        await asyncio.sleep(0)
                    except asyncio.CancelledError:
                        logger.info("[ms_watchdog] WATCHDOG_CANCEL q_gen=%d", q_gen)
                        return
                    continue

            # Activity re-check: last_engagement_at or _watchdog_grace_until may
            # have advanced while we were in the terminal-guard checks above.
            # Include _watchdog_grace_until so a late tts_finished callback
            # (race between Phase 1 exit and guard execution) is still respected.
            _last_activity = max(armed_at, self.last_engagement_at, self._watchdog_grace_until)
            if (time.time() - _last_activity) < _wait:
                logger.debug(
                    "[ms_watchdog] WATCHDOG_ACTIVITY q_gen=%d — deadline extended "
                    "(grace_until=%.3f)",
                    q_gen, self._watchdog_grace_until,
                )
                continue

            # Incomplete-turn continuation hold — flow has stashed an
            # unfinished STT final and is waiting for the caller to finish
            # their sentence.  Defer the re-ask until the hold window
            # expires so we never speak on top of an in-progress utterance.
            # Takes precedence over the normal grace / engagement checks.
            _ic_hold_until = float((_sess or {}).get("_incomplete_hold_until") or 0.0)
            if time.time() < _ic_hold_until:
                logger.info(
                    "[ms_watchdog] WATCHDOG_INCOMPLETE_HOLD q_gen=%d defer_until=%.3f",
                    q_gen, _ic_hold_until,
                )
                try:
                    await asyncio.sleep(max(0.1, _ic_hold_until - time.time() + 0.05))
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    return
                continue

            # Risk-1 / fire-time enforcement: honour _watchdog_grace_until as the
            # authoritative start of the caller's response window.  Even if Phase 1
            # and the guards above passed, if the grace window has not yet expired
            # we must not fire yet — defer back to Phase 1.
            if time.time() < self._watchdog_grace_until:
                logger.info(
                    "[ms_watchdog] WATCHDOG_GRACE_DEFER q_gen=%d "
                    "grace_until=%.3f now=%.3f",
                    q_gen, self._watchdog_grace_until, time.time(),
                )
                continue

            if self.currently_reasking:
                try:
                    await asyncio.sleep(0.5)
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    return
                continue

            if self._llm_busy:
                try:
                    await asyncio.sleep(0.5)
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    return
                continue

            # Active-speech guard: caller spoke (VAD / partial transcript) within
            # the last 2 s but STT hasn't delivered a final transcript yet.  Hold
            # and re-evaluate so we never fire a watchdog on top of an utterance
            # that's still being transcribed (e.g. CONFIRM_PHONE, COLLECT_NAME).
            # Phase 1 rolls the deadline via last_engagement_at, but there is a
            # small window between Phase 1 exiting and this check being reached
            # where a concurrent on_speech_started() update would be missed.
            _ENGAGEMENT_HOLD_SEC = 2.0
            _since_last_speech = time.time() - self.last_engagement_at
            if _since_last_speech < _ENGAGEMENT_HOLD_SEC:
                logger.debug(
                    "[ms_watchdog] WATCHDOG_ENGAGEMENT_HOLD q_gen=%d "
                    "last_engagement=%.2fs ago",
                    q_gen, _since_last_speech,
                )
                try:
                    await asyncio.sleep(0.5)
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    logger.info("[ms_watchdog] WATCHDOG_CANCEL q_gen=%d", q_gen)
                    return
                continue

            # ── Per-prompt caller-speech suppression ──────────────────────
            # If the caller has started speaking (any partial or final
            # transcript) for this prompt, do NOT re-ask over them. True
            # silence still fires normally because this flag is only set by
            # real transcript events.
            if self.prompt_speech_detected:
                _state_dbg = (_sess or {}).get("state", "")
                # How long since we last saw real speech?
                _since_speech = time.time() - self.last_engagement_at
                if _since_speech < 4.0:
                    # Caller is actively speaking — suppress
                    # and loop back to wait for their response
                    logger.info(
                        "[turn_taking] watchdog suppressed because caller already "
                        "started speaking state=%s q_gen=%d",
                        _state_dbg, q_gen,
                    )
                    try:
                        await asyncio.sleep(0.5)
                        await asyncio.sleep(0)
                    except asyncio.CancelledError:
                        return
                    continue
                else:
                    # Speech was detected but no transcript
                    # arrived in 4s — STT likely dropped it.
                    # Clear the flag and allow watchdog to fire
                    # so caller is not left in permanent silence.
                    logger.info(
                        "[turn_taking] watchdog suppression expired — "
                        "no transcript after %.1fs, re-arming "
                        "state=%s q_gen=%d",
                        _since_speech, _state_dbg, q_gen,
                    )
                    self.prompt_speech_detected = False
                    try:
                        await asyncio.sleep(0.5)
                        await asyncio.sleep(0)
                    except asyncio.CancelledError:
                        return
                    continue

            # ── Phase 4: Fire ─────────────────────────────────────────────
            self._no_input_reask_count += 1
            _attempt = self._no_input_reask_count
            _state = (_sess or {}).get("state", "")

            logger.info(
                "[ms_watchdog] WATCHDOG_FIRE q_gen=%d attempt=#%d state=%s",
                q_gen, _attempt, _state,
            )

            # CONFIRM_BOOKING: one clean re-ask only — hold patiently on further
            # silence instead of churning or escalating to transfer.  The caller
            # is deliberating; repeated prompts break the experience.
            if _state == "CONFIRM_BOOKING" and _attempt >= 2:
                self._no_input_reask_count -= 1  # keep counter at 1, no escalation
                armed_at = time.time()
                logger.info(
                    "[ms_watchdog] CONFIRM_BOOKING silence-hold q_gen=%d — "
                    "holding after 1 re-ask",
                    q_gen,
                )
                continue

            # Graceful exit on 3rd+ attempt — but let v3 location DTMF rung through.
            # The v3 ladder has 3 rungs (repeat → biased-confirm → DTMF keypad).
            # Rung 3 fires at _attempt == 3 but needs to reach the phrase-selection
            # block (line ~1781) to emit the DTMF prompt; intercepting it here would
            # skip that rung entirely and drop the caller into a silent dead end.
            _v3_rung3_pending = (
                (_sess or {}).get("v3_location_q_active")
                and int((_sess or {}).get("v3_location_reask_count", 0)) >= 1
            )
            if _attempt >= 3 and not _v3_rung3_pending:
                # Don't transfer if the caller engaged recently — a missed STT on
                # the 3rd attempt should not end the call while the caller is still
                # actively speaking.  Roll back the counter and wait for either a
                # transcript or the next natural deadline cycle.
                if (time.time() - self.last_engagement_at) < 2.0:
                    self._no_input_reask_count -= 1
                    armed_at = time.time()
                    logger.info(
                        "[ms_watchdog] WATCHDOG_TRANSFER_HOLD q_gen=%d "
                        "reason=recent_engagement (%.1fs ago) — deferring transfer",
                        q_gen, time.time() - self.last_engagement_at,
                    )
                    continue
                phrase = (
                    "I'm sorry, I'm having trouble hearing you right now. "
                    "Please call again in a moment."
                )
                self.currently_reasking = True
                await self._tts_text_queue.put(phrase)
                if self._on_reask:
                    asyncio.create_task(self._on_reask(phrase))
                logger.info("[ms_watchdog] graceful exit — max attempts reached")
                try:
                    await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    self.currently_reasking = False
                    return
                self.currently_reasking = False
                await self._transfer()
                return

            # Build contextual re-ask phrase
            if _attempt == 1:
                _prefix = "Sorry, I didn't catch that"
            else:  # attempt 2
                _prefix = "I'm sorry, I'm still not hearing you clearly. Let's try again"

            if _state in ("GREETING", "DETECT_INTENT", ""):
                # v3 bypasses the FlowEngine state machine so state stays
                # GREETING even after asking location / new-returning questions.
                # ── v3 slot selection re-ask ──────────────────────────────────
                # Slot selection: caller has just heard 2-3 options (day + times).
                # Do NOT re-read the CTA verbatim — the caller heard it once.
                # Do NOT use "Sorry, I didn't catch that" (G2 banned phrase).
                # Use a neutral prompt that doesn't presuppose they missed it.
                if (_sess or {}).get("v3_awaiting_slot_selection"):
                    phrase = "Still with you — which of those would you like?"
                # ── v3 location retry ladder ──────────────────────────────────
                # When the location question is active, escalate on the 2nd
                # re-ask to "Did you say the Alcester clinic?" — a biased binary
                # that lets the caller say yes once rather than repeating the
                # place name.  The v3_awaiting_use_this_clinic flag routes their
                # next response to the existing yes/no confirmation handler.
                elif (_sess or {}).get("v3_location_q_active"):
                    _v3_lrc = int((_sess or {}).get("v3_location_reask_count", 0))
                    if _v3_lrc == 0:
                        # Rung 1: biased confirm — lets the caller say yes/no once.
                        # Solicit "use this clinic" explicitly: a bare "yes" is
                        # frequently dropped by STT, whereas the distinct phrase
                        # "use this clinic" lands reliably (caller feedback
                        # 2026-06-12).
                        phrase = _LOC_RUNG2_CONFIRM
                        if _sess is not None:
                            _sess["v3_awaiting_use_this_clinic"] = True
                            _sess["last_question"] = phrase
                            _sess["last_bot_prompt"] = phrase
                            # Bias: rung-1 phrase is always "Did you say the
                            # Awlstuh clinic?" (Alcester).  Set directly rather
                            # than parsing the human-readable phrase — fragile
                            # if text ever changes.
                            _v3_bias = "alcester"
                            _sess["v3_use_this_clinic_bias"] = _v3_bias
                            logger.info(
                                "[ms_conn v3] watchdog bias set: %s"
                                " (rung-1 alcester constant)",
                                _v3_bias,
                            )
                    else:
                        # Rung 2: DTMF keypad fallback — completely deterministic, no STT.
                        # Clear v3_location_q_active so the ladder stops here.
                        phrase = _LOC_RUNG3_DTMF
                        if _sess is not None:
                            _sess["v3_awaiting_location_dtmf"] = True
                            _sess["v3_awaiting_use_this_clinic"] = False
                            _sess["v3_location_q_active"] = False
                            _sess["last_question"] = phrase
                    if _sess is not None:
                        _sess["v3_location_reask_count"] = _v3_lrc + 1
                else:
                    # Non-location GREETING re-ask: use last_question or generic.
                    # Strip any slot/time confirmation prefix that precedes the
                    # actual question — e.g. "Eleven on the 21st — could I get
                    # your first name?" becomes "could I get your first name?"
                    # so the re-ask doesn't replay the booking confirmation.
                    _lq_g = (_sess or {}).get("last_question") or self.last_question
                    if _lq_g and " — " in _lq_g:
                        _lq_parts = _lq_g.rsplit(" — ", 1)
                        _lq_tail = _lq_parts[-1].strip()
                        # Use trailing part if it's a question and the prefix
                        # looks like a date/time confirmation (contains a digit
                        # or ordinal suffix).
                        if (
                            _lq_tail.endswith("?")
                            and len(_lq_tail) >= 10
                            and re.search(r"\d|st\b|nd\b|rd\b|th\b", _lq_parts[0])
                        ):
                            _lq_g = _lq_tail
                    if _lq_g and _lq_g.strip() and "how can i help" not in _lq_g.lower():
                        # Spec O: strip leading affirmation before re-ask
                        _lq_body = _strip_leading_affirmation(_lq_g.strip())
                        phrase = _prefix + ". " + (_lq_body[0].upper() + _lq_body[1:])
                    else:
                        phrase = _prefix + " — how can I help today?"
            elif _state == "ASK_LOCATION":
                # Approved-copy watchdog with tier escalation.  Never invent
                # or shorten ASK_LOCATION wording.  Each watchdog fire must
                # advance the retry ladder (initial → first-retry → DTMF);
                # replaying `last_question` alone kept the caller stuck on
                # the initial prompt forever.  Drive escalation off the
                # same `location_retry_count` that flow.py uses so voice
                # retries and silence retries share one ladder.
                # Retry rung 1 is a biased binary — bet on Alcester being the
                # majority destination so the caller can say "yes" once.  If
                # they actually wanted Redditch, "no" / "no, I meant Redditch"
                # binds Redditch instantly via the forced-confirm block in
                # flow.py.  Setting location_pending_guess routes the next
                # spoken turn there.  Rung 2 is the DTMF keypad fallback.
                _APPROVED_LOC_RETRY = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "did you mean our Awlstuh clinic? "
                    "If not, just say: no, I meant Redditch."
                )
                _APPROVED_LOC_DTMF = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "could you please press 1 on your keypad for the Awlstuh clinic "
                    "or 2 on your keypad for the Redditch clinic."
                )
                _lrc_w = int((_sess or {}).get("location_retry_count", 0))
                if _lrc_w == 0:
                    phrase = _APPROVED_LOC_RETRY
                    if _sess is not None:
                        _sess["location_retry_count"]  = 1
                        _sess["location_pending_guess"] = "alcester"
                        _sess["last_question"] = phrase
                else:
                    phrase = _APPROVED_LOC_DTMF
                    if _sess is not None:
                        _sess["location_awaiting_dtmf"] = True
                        _sess.pop("location_pending_guess", None)
                        _sess.pop("location_pending_guess_reask", None)
                        _sess["location_retry_count"] = max(_lrc_w + 1, 2)
                        _sess["last_question"] = phrase
            elif _state in (
                "COLLECT_NAME", "COLLECT_NAME_RETURNING",
                "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
            ):
                _nf = (_sess or {}).get("name_fragment")
                if _nf:
                    phrase = _prefix + " \u2014 please say: my surname is\u2026"
                else:
                    phrase = _prefix + " \u2014 please say: my first name is\u2026"
            elif _state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING", "RETURNING_PLAN_CONFIRM_PHONE"):
                phrase = (
                    _prefix + " — please say: use this number — "
                    "or: do not use this number."
                )
            elif _state in (
                "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
                "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
            ):
                _lq = (_sess or {}).get("last_question", "")
                phrase = _lq if _lq else _prefix + " — which option works best?"
            elif _state == "CONFIRM_BOOKING":
                phrase = _prefix + " — please say yes to confirm, or no to change it."
            elif _state in (
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
                "RETURNING_PLAN_COLLECT_PHONE",
            ):
                if (_sess or {}).get("phone_awaiting_dtmf"):
                    phrase = _prefix + " — please enter the phone number using your keypad."
                else:
                    phrase = _prefix + " — please say the phone number slowly."
            elif _state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
                if (_sess or {}).get("lookup_correction_mode"):
                    phrase = _prefix + " — what first name and surname was the booking under?"
                else:
                    phrase = _prefix + " — could you say that again?"
            else:
                _lq = (_sess or {}).get("last_question") or self.last_question
                if _lq and _lq.strip():
                    phrase = _prefix + ". " + _lq.strip()
                else:
                    phrase = _prefix + " — could you say that again?"

            logger.info("[ms_watchdog] WATCHDOG_FIRE prompt=%r attempt=#%d", phrase[:80], _attempt)
            self.currently_reasking = True
            # ── Clear tts_inhibit before watchdog re-ask ──────────────────────
            # A barge-in sets tts_inhibit=True.  If the LLM turn never started
            # (transcript was dropped while _llm_busy=True), the flag is never
            # cleared by the normal "new turn begins" path, so the re-ask is
            # silently discarded by the _tts_loop inhibit check (line ~4713).
            # Clearing it here ensures the watchdog phrase always plays.
            if _sess:
                _sess["tts_inhibit"] = False
                logger.info("[ms_watchdog] cleared tts_inhibit before re-ask")
            # Tag with watchdog-reask marker so _tts_loop bypasses dedup for this
            # one chunk (a deliberate silence recovery is not an accidental dup).
            await self._tts_text_queue.put(_WATCHDOG_REASK_MARKER + phrase)
            if self._on_dead_air_ts_reset:
                asyncio.create_task(self._on_dead_air_ts_reset())
            logger.debug(
                "[ms_watchdog] dead-air ts reset on re-ask fire"
            )
            if self._on_reask:
                asyncio.create_task(self._on_reask(phrase))

            # Wait ~5 s for TTS; CancelledError = caller spoke mid-phrase.
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                self.currently_reasking = False
                return
            self.currently_reasking = False

            # Cap audible re-asks at one per question generation — EXCEPT for
            # states that have a real escalation ladder where silence must
            # advance through every rung (spoken reask → DTMF prompt) without
            # needing a fresh question to re-arm the watchdog.
            #
            # ASK_LOCATION silence path: attempt #1 spoke the spoken reask
            # (advancing location_retry_count 0→1).  If we retire here, the
            # caller can sit in silence forever and never hear the DTMF
            # keypad prompt because no new question will be asked to arm a
            # fresh watchdog.  Instead, keep the loop alive: reset armed_at
            # so the next silence tick fires attempt #2, which hits the
            # DTMF branch (line ~1426) and emits the keypad prompt.  Phase 4
            # still terminates cleanly at attempt #3 via the graceful-exit
            # / transfer path so we never loop forever.
            #
            # theorem_v3 GREETING path: v3 never enters a named FlowEngine
            # state so state stays "GREETING" throughout.  The location retry
            # ladder (v3_location_reask_count 0→1→2) mirrors the ASK_LOCATION
            # escalation: rung 1 repeats question, rung 2 fires biased confirm,
            # rung 3 fires DTMF and clears v3_location_q_active.  Once
            # v3_location_q_active is cleared the check below is False and
            # the watchdog retires cleanly after the DTMF prompt.
            if (
                _state in ("GREETING", "DETECT_INTENT", "")
                and bool((_sess or {}).get("v3_location_q_active"))
            ):
                armed_at = time.time()
                logger.info(
                    "[ms_watchdog] WATCHDOG_LADDER_CONTINUE q_gen=%d state=%s "
                    "attempt=#%d v3_lrc=%d — v3 location ladder active",
                    q_gen, _state, _attempt,
                    int((_sess or {}).get("v3_location_reask_count", 0)),
                )
                continue

            _ladder_states = {"ASK_LOCATION"}
            if _state in _ladder_states and _attempt < 2:
                armed_at = time.time()
                logger.info(
                    "[ms_watchdog] WATCHDOG_LADDER_CONTINUE q_gen=%d state=%s "
                    "attempt=#%d — deferring retire so DTMF can fire on next silence",
                    q_gen, _state, _attempt,
                )
                continue
            logger.info(
                "[ms_watchdog] WATCHDOG_RETIRE q_gen=%d reason=audible_reask_done",
                q_gen,
            )
            if self._on_dead_air_ts_reset:
                asyncio.create_task(self._on_dead_air_ts_reset())
            logger.debug(
                "[ms_watchdog] dead-air ts reset on retire"
            )
            self._watchdog_has_retired = True
            self._reask_completed = True   # safety net suppressed until new q_gen or transcript
            return

    def _restart_timer(self) -> None:
        if self._cancelled:   # guard: don't restart after teardown
            return
        # DTMF-expected short-circuit: never arm the speech watchdog while
        # keypad input is expected.  Cancel any live one too, since the
        # previous arming context is now stale.  Also cancel the W1/W2/W3
        # silence cascade so no speech-first re-ask path stays live.  The
        # flow owns any DTMF-specific reminder in keypad mode.
        _sess_restart = self._get_session() if self._get_session else None
        if _is_dtmf_expected(_sess_restart):
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            if (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            ):
                self._no_input_watchdog_task.cancel()
                logger.info(
                    "[ms_watchdog] WATCHDOG_SUPPRESS reason=dtmf_expected"
                )
            self._no_input_watchdog_task = None
            self.currently_reasking = False
            return
        # ── Cancel W1/W2/W3 silence cascade only ──────────────────────────
        # Do NOT call _cancel_timer() here: it also kills the watchdog, which
        # breaks the idempotency guard below.  The watchdog is managed separately
        # so that multiple on_tts_finished() callbacks for the same question
        # (multi-chunk TTS) do not cancel and re-arm it on every chunk.
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.currently_reasking = False
        _session = self._get_session() if self._get_session else None
        self._replay_flow_step = (_session or {}).get("flow_step", -1) if _session else -1
        _my_q_gen = self._q_gen  # bind timer to current question generation
        self._task = asyncio.create_task(self._run(_my_q_gen), name="ms_silence_timer")
        # ── Arm no-input watchdog (idempotent per q_gen) ─────────────────
        # If a watchdog is already live for this exact q_gen, leave it running —
        # its armed_at deadline is correct and re-arming would reset it, potentially
        # preventing it from ever firing on slow multi-chunk TTS responses.
        # If the existing watchdog belongs to a stale q_gen, cancel and replace it.
        # Set NO_INPUT_WATCHDOG_SEC=0 to disable (e.g. automated test harness).
        import os as _os_w
        _wdg_wait = float(_os_w.getenv("NO_INPUT_WATCHDOG_SEC", "4.5"))
        if _wdg_wait > 0:
            _live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if _live and self._watchdog_q_gen == _my_q_gen:
                logger.debug(
                    "[ms_watchdog] WATCHDOG_SKIP_IDEMPOTENT q_gen=%d", _my_q_gen
                )
            elif (
                self._no_input_reask_count > 0
                and self._watchdog_q_gen == _my_q_gen
            ):
                # An audible watchdog re-ask already played for this q_gen.
                # Cap at one per question generation: do not arm a fresh
                # watchdog that would re-fire and spam the caller.  The next
                # audible recovery only happens when a new question advances
                # q_gen (which resets _no_input_reask_count to 0).
                logger.info(
                    "[ms_watchdog] WATCHDOG_RETIRED_FOR_QGEN q_gen=%d "
                    "reask_count=%d — not re-arming",
                    _my_q_gen, self._no_input_reask_count,
                )
            else:
                if _live:
                    self._no_input_watchdog_task.cancel()
                    logger.info(
                        "[ms_watchdog] WATCHDOG_CANCEL_STALE old_q_gen=%d new_q_gen=%d",
                        self._watchdog_q_gen, _my_q_gen,
                    )
                # ── Spec Z Gate 1: never arm while LLM is in flight ────────────
                # Deferring avoids a stale armed_at timestamp and ensures the
                # watchdog fires only after the real response (and TTS) complete.
                # on_llm_finished() will fire _restart_timer() once _llm_busy=False.
                if self._llm_busy:
                    logger.info(
                        "[ms_watchdog] WATCHDOG_DEFERRED"
                        " reason=llm_in_flight q_gen=%d",
                        _my_q_gen,
                    )
                    self._watchdog_deferred = True
                    return
                # ── Spec Z Gate 2: never arm with a prompt that has no question ─
                # Affirmation-only TTS like "Of course —" must never produce a
                # watchdog — there is nothing to re-ask.  The next substantive
                # question (Prompt L etc.) will arm the watchdog correctly.
                _prompt_to_check = self.last_question or ""
                if not self._prompt_contains_question(_prompt_to_check):
                    logger.info(
                        "[ms_watchdog] WATCHDOG_SUPPRESSED"
                        " reason=no_question prompt=%r q_gen=%d",
                        _prompt_to_check[:60], _my_q_gen,
                    )
                    return
                # ── end Spec Z ─────────────────────────────────────────────────
                _armed_at = time.time()
                self._watchdog_q_gen = _my_q_gen
                self._no_input_watchdog_task = asyncio.create_task(
                    self._no_input_watchdog(_armed_at, _my_q_gen),
                    name="ms_silence_no_input_watchdog",
                )
        else:
            logger.info("[ms_watchdog] WATCHDOG_NOT_STARTED reason=NO_INPUT_WATCHDOG_SEC=0 q_gen=%d", _my_q_gen)
        logger.debug("[ms_silence] timer started (q_gen=%d)", _my_q_gen)

    def _cancel_timer(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task              = None
        # Cancel the no-input watchdog with the same triggers as the main timer.
        # All callers of _cancel_timer (speech detected, transcript received, TTS
        # starting for a new response, LLM busy) should also abort the watchdog.
        if self._no_input_watchdog_task and not self._no_input_watchdog_task.done():
            logger.info(
                "[ms_watchdog] WATCHDOG_CANCEL caller=%s",
                # cheaply identify the caller for log traceability
                __import__("traceback").extract_stack()[-2].name,
            )
            self._no_input_watchdog_task.cancel()
        self._no_input_watchdog_task = None
        self.currently_reasking = False

    async def _run(self, q_gen: int = 0) -> None:
        """
        Flat sequential re-ask coroutine.

        q_gen — the _q_gen value at timer creation.  If on_question_asked() fires
        after this task starts (new question), _q_gen advances and we return early
        at each window check.  This prevents stale timers from a previous question
        firing during a new question's silence window.

        Window 1: per-state sleep → since_audio guard → re-ask #1 → 5s TTS wait
        Window 2: 15s sleep → since_audio guard → re-ask #2 → 5s TTS wait
        Window 3: 15s sleep → since_audio guard → transfer

        Pause mode (caller said "hang on" etc.):
          While caller_pause_active: 45s extended windows, no re-ask.
          At 45s total silence: check-in phrase.
          At 90s total silence: termination phrase + transfer.

        Never recurses with create_task.  CancelledError exits cleanly at
        any sleep — caller spoke (on_speech_started) or Susie spoke
        (on_tts_started / on_transcript_received).
        """
        # ── Pause mode branch ─────────────────────────────────────────────
        _session = self._get_session() if self._get_session else None
        if _session and _session.get("caller_pause_active"):
            # Loop in 45-second increments while caller is paused.
            # CancelledError exits when speech is detected.
            while True:
                try:
                    await asyncio.sleep(45.0)
                    await asyncio.sleep(0)  # deliver pending cancels
                except asyncio.CancelledError:
                    return

                since_audio = time.time() - self.last_audio_received_at
                if since_audio < 3.5:
                    return  # speech detected — timer will be restarted by on_question_asked

                # Re-fetch session (may have been reassigned) to get latest state
                _session = self._get_session() if self._get_session else _session
                if not _session or not _session.get("caller_pause_active"):
                    return  # pause cleared by substantive utterance

                _session["pause_silence_total"] = (
                    _session.get("pause_silence_total", 0.0) + 45.0
                )
                _total = _session["pause_silence_total"]
                logger.info(
                    "[ms_silence] pause mode: %.0fs total silence", _total
                )

                if _total >= 90.0:
                    # Caller has been silent too long — terminate the call gracefully
                    phrase = (
                        "I'll let you go — give us a ring back when you're ready "
                        "and we'll get you sorted."
                    )
                    await self._tts_text_queue.put(phrase)
                    logger.info("[ms_silence] pause 90s limit reached — terminating")
                    await self._transfer()
                    return

                if _total >= 45.0:
                    # First check-in — let caller know we're still here
                    phrase = (
                        "No rush at all — take your time, "
                        "we're still here whenever you're ready."
                    )
                    await self._tts_text_queue.put(phrase)
                    logger.info("[ms_silence] pause 45s check-in played")
                    # Continue loop for another 45s

            return  # unreachable but guards against fall-through

        from app.silence_handler import get_silence_response, get_silence_threshold, log_silence_event
        q = self.last_question.strip()

        # ── Window 1: per-state silence threshold ──────────────────────────
        # Falls back to SILENCE_THRESHOLDS["default"] for unmapped states.
        # SILENCE_WINDOW_1_SEC env var overrides all per-state values (used by
        # the automated test runner to avoid collision with TURN_WAIT_SECONDS).
        import os as _os
        _env_override = _os.getenv("SILENCE_WINDOW_1_SEC")
        _w1 = float(_env_override) if _env_override else get_silence_threshold(self.current_state)
        try:
            await asyncio.sleep(_w1)
            # Yield once more so any task.cancel() that arrived while we were
            # sleeping (but after sleep() returned normally) is delivered here
            # before we check the guards — fixes the race where _llm_busy is
            # not yet True at the moment we check it.
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        # Stale question generation guard — if on_question_asked() fired after
        # this timer was created, _q_gen has advanced and we belong to the old
        # question.  Return silently; the new question has its own timer.
        if q_gen != 0 and q_gen != self._q_gen:
            logger.info(
                "[ms_silence] W1: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        _session_now = self._get_session() if self._get_session else None
        _current_step = (_session_now or {}).get("flow_step", -1) if _session_now else -1
        if _current_step != self._replay_flow_step:
            logger.info(
                "[ms_silence] W1 stale replay suppressed stored_step=%d current_step=%d",
                self._replay_flow_step, _current_step,
            )
            return
        if self._cancelled:
            return

        # Sync last_question from live session to prevent stale TTS content
        # from being replayed when on_tts_finished updated it after the step transition.
        _live_q_w1 = (_session_now or {}).get("last_question", "")
        if _live_q_w1 and _live_q_w1.strip() != self.last_question:
            logger.info(
                "[ms_silence] W1: syncing last_question from %r to live %r",
                self.last_question[:40], _live_q_w1[:40],
            )
            self.last_question = _live_q_w1.strip()
            q = self.last_question

        # Phone-capture DTMF guard: if digit collection is already underway,
        # do not interrupt with a spoken prompt — the keypress flow owns timing.
        if (
            self.current_state in (
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
            )
            and (_session_now or {}).get("phone_dtmf_buffer")
        ):
            logger.debug("[ms_silence] W1: DTMF digits in buffer — suppressing phone recovery")
            return

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase1 = get_silence_response(
            self.current_state, self._consecutive_silence_count
        )
        if self._consecutive_silence_count >= 1:
            self._consecutive_silence_count = 0
        else:
            self._consecutive_silence_count += 1
        log_silence_event(self.current_state, _w1, phrase1, self.reask_count - 1)
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        # Approved-copy replay for ASK_LOCATION with tier escalation.
        # The ladder is: tier 0 (initial) → tier 1 (first-retry wording)
        # → tier 2+ (DTMF).  W1 must advance the ladder; replaying
        # last_question alone kept callers stuck on the initial prompt.
        # location_retry_count is the shared ladder index with flow.py
        # so voice retries and silence retries never get out of sync.
        if self.current_state == "ASK_LOCATION":
            _APPROVED_LOC_RETRY_W1 = (
                "Sorry, I didn't quite catch that — "
                "could you say the Awlstuh clinic or the Redditch clinic?"
            )
            _APPROVED_LOC_DTMF_W1 = (
                "Sorry, I didn't quite catch that — "
                "could you please press 1 on your keypad for the Awlstuh clinic "
                "or 2 on your keypad for the Redditch clinic."
            )
            _lrc_w1 = int((_session_now or {}).get("location_retry_count", 0))
            if _lrc_w1 == 0:
                _reask1 = _APPROVED_LOC_RETRY_W1
                if _session_now is not None:
                    _session_now["location_retry_count"] = 1
                    _session_now["last_question"] = _reask1
            else:
                _reask1 = _APPROVED_LOC_DTMF_W1
                if _session_now is not None:
                    _session_now["location_awaiting_dtmf"] = True
                    _session_now["location_retry_count"] = max(_lrc_w1 + 1, 2)
                    _session_now["last_question"] = _reask1
        else:
            # Spec O: strip leading affirmation (e.g. "Perfect —") before appending
            _q_clean = _strip_leading_affirmation(q) if q else q
            _reask1 = phrase1 + (" " + _q_clean if _q_clean else "")

        # Name-capture structured recovery: replace generic phrase+last_question
        # with a substate-aware scaffold prompt. name_fragment is set (in session)
        # when the first name has been accepted, so its presence identifies the
        # surname step.  One recovery fires at 3 s; W2/W3 handle the fallback.
        if self.current_state in (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        ):
            if (_session_now or {}).get("name_fragment"):
                _reask1 = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my surname is..."
                )
            else:
                _reask1 = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my first name is..."
                )

        # Phone-capture structured recovery: replace generic phrase+last_question
        # with a targeted prompt. For COLLECT_PHONE, distinguish keypad vs speech.
        # One structured recovery fires at 3 s; W2/W3 handle the longer fallback.
        elif self.current_state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING"):
            _reask1 = (
                "Sorry, I didn't quite catch that — "
                "please say: use this number — "
                "or: do not use this number."
            )
        elif self.current_state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
        ):
            if (_session_now or {}).get("phone_awaiting_dtmf"):
                _reask1 = (
                    "Sorry, I didn't quite catch that — "
                    "please enter the phone number using your keypad."
                )
            else:
                _reask1 = (
                    "Sorry, I didn't quite catch that — "
                    "please say the phone number slowly."
                )

        await self._tts_text_queue.put(_reask1)
        if self._on_reask:
            asyncio.create_task(self._on_reask(_reask1))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 2: 15 s silence ─────────────────────────────────────────
        try:
            await asyncio.sleep(15.0)
            await asyncio.sleep(0)  # deliver any pending cancel before guard checks
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 1.0:   # reduced from 3.5 — AssemblyAI empty transcripts can reset the clock
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        # Stale question generation guard (same as W1)
        if q_gen != 0 and q_gen != self._q_gen:
            logger.info(
                "[ms_silence] W2: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        _session_now = self._get_session() if self._get_session else None
        _current_step = (_session_now or {}).get("flow_step", -1) if _session_now else -1
        if _current_step != self._replay_flow_step:
            logger.info(
                "[ms_silence] W2 stale replay suppressed stored_step=%d current_step=%d",
                self._replay_flow_step, _current_step,
            )
            return
        if self._cancelled:
            return

        # Sync last_question from live session (same guard as W1)
        _live_q_w2 = (_session_now or {}).get("last_question", "")
        if _live_q_w2 and _live_q_w2.strip() != self.last_question:
            logger.info(
                "[ms_silence] W2: syncing last_question from %r to live %r",
                self.last_question[:40], _live_q_w2[:40],
            )
            self.last_question = _live_q_w2.strip()
            q = self.last_question

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase2 = get_silence_response(
            self.current_state, self._consecutive_silence_count
        )
        if self._consecutive_silence_count >= 1:
            self._consecutive_silence_count = 0
        else:
            self._consecutive_silence_count += 1
        log_silence_event(self.current_state, 15.0, phrase2, self.reask_count - 1)
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        # Spec O: strip leading affirmation before appending question
        _q_clean_w2 = _strip_leading_affirmation(q) if q else q
        _reask2 = phrase2 + (" " + _q_clean_w2 if _q_clean_w2 else "")
        await self._tts_text_queue.put(_reask2)
        if self._on_reask:
            asyncio.create_task(self._on_reask(_reask2))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 3: 15 s silence → transfer ─────────────────────────────
        try:
            await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return

        await self._transfer()

    async def _transfer(self) -> None:
        if self._cancelled:
            logger.info("[ms_silence] _transfer suppressed — handler already cancelled (stale call)")
            return
        logger.info("[ms_silence] max reasks reached — transferring")
        phrase = (
            "I'm having a little trouble hearing you — "
            "let me transfer you to someone who can help."
        )
        # Save to conversation_history so the test evaluator can see it.
        # _on_transfer is the _silence_history_fn closure in WebSocketCallHandler.
        if self._on_transfer:
            asyncio.create_task(self._on_transfer(phrase))
        await self._tts_text_queue.put(phrase)
        # Set the silence_transfer flag so _should_allow_transfer() passes.
        # _trigger_transfer is a closure that sets session["silence_transfer"]
        # before calling _on_transfer_request — see SilenceHandler instantiation.
        try:
            await self._trigger_transfer()
        except Exception as exc:
            logger.error("[ms_silence] transfer error: %r", exc)


# ---------------------------------------------------------------------------
# Active handler registry (call_sid → WebSocketCallHandler)
# ---------------------------------------------------------------------------

# Maps inbound call_sid → active handler for that call.
# Used by the /ms/test/inject-transcript endpoint so the test runner can
# drive Susie's conversation pipeline directly without going through STT.
# Handlers register on "start" event and deregister in _cleanup().
_active_handlers: Dict[str, "WebSocketCallHandler"] = {}


# ---------------------------------------------------------------------------
# Main handler class
# ---------------------------------------------------------------------------

class WebSocketCallHandler:
    """
    Manages a single Twilio Media Streams WebSocket call.

    Instantiated once per incoming WebSocket connection by router.py.
    All pipeline state lives on this instance; nothing is shared between calls.

    Usage:
        handler = WebSocketCallHandler(websocket)
        await handler.handle()

    Stability contract:
        _call_stable is set True after the first complete STT -> LLM -> TTS cycle.
        If the call ends before this, router.py logs "UNSTABLE CALL" and may
        redirect to the legacy system.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket   = websocket

        # Call identity — populated from the "start" event
        self.call_sid:   Optional[str] = None
        self.stream_sid: Optional[str] = None

        # Session dict — loaded / created on "start" event
        self.session: Dict[str, Any] = {}

        # ── Pipeline queues ────────────────────────────────────────────────
        self.audio_in_queue:   asyncio.Queue = asyncio.Queue()  # raw mulaw bytes
        self.stt_input_queue:  asyncio.Queue = asyncio.Queue()  # PCM16 16kHz bytes
        self.transcript_queue: asyncio.Queue = asyncio.Queue()  # str utterances
        self.tts_text_queue:   asyncio.Queue = asyncio.Queue()  # str text chunks
        self.audio_out_queue:  asyncio.Queue = asyncio.Queue()  # base64 str payloads

        # ── Pipeline components ────────────────────────────────────────────
        self._audio_in_proc  = AudioInputProcessor()
        self._audio_out_proc = AudioOutputProcessor()
        self._stt_stream     = STTStream()

        # ── Filler clip guard (Change A) ───────────────────────────────────
        # Plays a short pre-synthesised clip on Acuity availability turns only.
        # Run scripts/synthesise_filler.py once to generate the clip.
        # clip_path_2: if filler_moment.ulaw exists it plays as a second filler
        # after 2.5s of silence post-primary; otherwise the primary clip repeats.
        self._filler = FillerGuard(
            clip_path=Path("audio_clips/filler_checking.ulaw"),
            clip_path_2=Path("audio_clips/filler_moment.ulaw"),
            send_audio=self._send_ulaw,
        )
        # Per-turn flag: True once the post-filler silence has been injected,
        # preventing multiple injections across consecutive TTS chunks.
        self._filler_breath_injected: bool = False

        # ── Control events ─────────────────────────────────────────────────
        self._stop_event    = asyncio.Event()  # set when "stop" received or WS closes
        self._started_event = asyncio.Event()  # set when "start" event is processed

        # ── Barge-in / TTS state ───────────────────────────────────────────
        self._tts_task:  Optional[asyncio.Task] = None  # current TTS chunk task
        self._clearing   = False   # True while Twilio buffer is draining after barge-in
        self._llm_busy   = False   # True while Claude is generating (silence handler)
        # Spec N — concurrent LLM call guard.
        # llm_in_flight is set True the moment a transcript is accepted for LLM
        # dispatch and cleared only after ALL iterations (including tool round-trips)
        # complete.  At most one pending transcript is held; newer overwrites older.
        self.llm_in_flight: bool = False
        self.pending_transcript: Optional[str] = None

        # ── C8-2 — location-ack race guard ─────────────────────────────────
        # When a location ack is emitted (caller's clinic resolved → next Q
        # queued), STT frequently delivers a second phantom final from the
        # same breath ~80ms–1.5s later ("i guess", "as soon as possible").
        # Routing it to Sonnet fires a redundant turn that re-asks the clinic
        # question or monologues opening hours, contradicting the ack just
        # heard.  We record the ack time and drop any transcript inside the
        # window below.  session["location_acked_this_turn"] is the one-shot
        # flag; _location_ack_ts is the monotonic stamp.
        self._location_ack_ts: float = 0.0
        self._LOCATION_ACK_DROP_WINDOW: float = 1.5  # seconds

        # Barge-in timing/state — used for false-trigger gate and ack injection
        self._current_tts_text:    str   = ""    # text being synthesised right now
        self._barge_in_pending:    bool  = False  # True between partial and final transcript
        self._barge_in_ts:         float = 0.0   # monotonic time when barge-in first fired
        self._barge_in_duration:   float = 0.0   # elapsed seconds (set by _on_final_transcript_clear)
        # Stale-transcript flush: set to time.monotonic() at each confirmed
        # barge-in. Any transcript enqueued BEFORE this timestamp is discarded
        # at dequeue time to prevent phantom LLM calls from stale STT finals.
        self._barge_in_flush_before: float = 0.0
        # Recovery flag: True after we've already played a barge-in ack and are
        # waiting for the caller's actual utterance.  Prevents ack-loop when the
        # caller's continued speech triggers a second barge-in before the first
        # utterance is processed.
        self._in_barge_in_recovery: bool = False
        # Clinical barge-in protection: True while a clinical/empathy response is
        # being synthesised.  When set, barge-in does NOT cancel the TTS so the
        # caller always hears the full empathy acknowledgement before Susie listens
        # to the next input.  Reset to False at the start of each new TTS chunk.
        self._clinical_response_active: bool = False

        # Keypad idle-finalize: scheduled when the DTMF buffer has enough digits
        # to plausibly be a complete number but the caller has paused.  If no
        # further digits arrive within _KEYPAD_IDLE_FINALIZE_SEC we finalize the
        # buffer as a synthetic transcript so the flow gate can readback.
        self._dtmf_idle_task: Optional[asyncio.Task] = None
        # Secondary near-complete safety net: independent 5 s timeout for
        # buffers of 9+ digits.  Does not cancel the standard idle task and is
        # not cancelled by it — both run in parallel.  Prevents the 40 s hang
        # that occurs when the standard idle task is interfered with and no new
        # digit ever arrives to reschedule it.
        self._dtmf_near_complete_task: Optional[asyncio.Task] = None

        # Name-collection debounce: prevents split STT finals ("my name is" +
        # "James") from firing two simultaneous LLM calls.
        self._name_collection_pending: bool = False
        self._name_collection_timer: Optional[asyncio.Task] = None
        # Set to True when _fire_name_reask puts the clarification phrase on
        # tts_text_queue.  Cleared when a subsequent name transcript drains
        # the phrase or when the normal run_turn path executes.
        self._name_clarification_queued: bool = False
        # Set to True by _fire_name_reask() immediately before queueing the
        # re-ask phrase.  Guards the normal run_turn path: the first non-name
        # utterance that arrives while this is True is silently discarded
        # (one-shot) so that noise cannot trigger a competing LLM response
        # while the clarification phrase is still queued or playing.  Cleared
        # by the name-drain/pending-cancel branches when the name arrives, or
        # cleared (one-shot) by the guard itself for non-name utterances.
        self._clarification_in_flight: bool = False

        # Timestamp of the last transcript that was PASSED to the LLM (not
        # dropped by any filter).  Used by the SPEC-3 rapid-arrival noise
        # check: a very short token that arrives within _V3_RAPID_ARRIVAL_SEC
        # of the previous accepted transcript is treated as an STT artifact.
        self._v3_last_processed_ts: float = 0.0
        # Raw text of the last accepted transcript — used by Condition 4
        # (rapid-continuation) to merge the fragment into a combined utterance
        # instead of firing a second LLM call.
        self._v3_last_transcript_text: str = ""

        # Prompt generation counter — monotonically increasing.
        # Incremented whenever a confirmed barge-in clears the active TTS.
        # Each _delayed_tts_finished task captures the generation at creation
        # time and is silently ignored if the generation has advanced, preventing
        # stale "does that sound OK?" callbacks from overwriting last_question
        # and re-arming the silence timer after the flow has moved on.
        self._tts_gen: int = 0

        # ── Latency / timing ──────────────────────────────────────────────
        # Monotonic timestamp set every time TTS audio finishes playing on the
        # caller's end (stamped at each on_tts_finished call-site in
        # _delayed_tts_finished).  Used by the echo suppression window check —
        # only transcripts arriving within ECHO_SUPPRESS_WINDOW_S of this
        # timestamp are candidates for TTS-echo suppression.
        self._tts_audio_done_at:      float = 0.0
        self._last_audio_at:          float = 0.0   # monotonic time of last audio sent to Twilio
        self._last_filler_at:         float = 0.0   # monotonic time of last filler phrase played
        self._bad_line_played         = False        # once-per-call bad-line phrase guard
        self._last_audio_received_at: float = 0.0   # monotonic time of last inbound Twilio audio
        # Monotonic timestamp when the most recent LLM turn completed (finally:
        # block cleared _llm_busy).  Used by the tail-fragment guard to discard
        # tiny residual STT finals that arrive immediately after a successful turn.
        self._last_turn_done_at:      float = 0.0
        # Text of the TTS utterance currently in-flight through audio_out_queue.
        # Set in _tts_loop when synthesis completes; cleared in send_loop when
        # the _TTS_DONE_SENTINEL is drained — at that point on_tts_finished fires.
        self._tts_text_pending: str = ""
        # _tts_last_start_ts captured when the current chunk's on_tts_started() fired.
        # Forwarded to _delayed_tts_finished so on_tts_finished() can detect whether a
        # newer chunk has started before clearing _tts_playing (fixes multi-chunk gap).
        self._tts_pending_chunk_start_ts: float = 0.0
        # q_gen captured when the current chunk's on_tts_started() fired.
        # Forwarded to _delayed_tts_finished so late tts_finished callbacks for an
        # older prompt cannot restart the silence timer / overwrite last_question
        # after the flow has advanced to a new question (stale-prompt ownership fix).
        self._tts_pending_q_gen: int = -1
        # TTS chunk sequencing — used to identify the terminal chunk of each
        # q_gen so stale tts_finished callbacks from non-terminal chunks cannot
        # trigger the silence timer or watchdog incorrectly.
        # _tts_chunk_seq    — monotonically increasing per sub-chunk synthesised.
        # _tts_expected_final_seq — seq of the last sub-chunk queued; the terminal
        #                           chunk guard uses this to suppress non-final callbacks.
        # _tts_pending_chunk_seq  — seq snapshot captured at sentinel placement;
        #                           forwarded through _send_loop to _delayed_tts_finished.
        # _current_chunk_seq      — seq of the chunk whose tts_finished is currently
        #                           being evaluated (set inside _delayed_tts_finished).
        self._tts_chunk_seq: int = 0
        self._tts_expected_final_seq: int = 0
        self._tts_pending_chunk_seq: int = 0
        self._current_chunk_seq: int = 0
        # Out-of-order chunk tracking.  Accumulates chunk_seq values whose
        # _delayed_tts_finished has fired.  When the terminal chunk fires before
        # an earlier (longer) chunk, its seq is stored as _tts_pending_terminal
        # and the silence timer is held until all preceding chunks have arrived.
        # Reset on barge-in (where _tts_chunk_seq also resets to 0).
        self._tts_chunks_completed: set = set()
        self._tts_pending_terminal: int = 0
        self._tts_pending_terminal_text: str = ""
        self._tts_pending_terminal_chunk_start_ts: float = 0.0
        # Cumulative playout clock (monotonic).  Twilio buffers audio
        # faster-than-realtime, so every chunk's TTS-done sentinel is processed
        # by _send_loop within ~1s of the others.  If each chunk's finish timer
        # slept only its OWN play duration from that near-simultaneous baseline,
        # a long middle chunk's timer would outlast the short terminal chunk's
        # timer — the terminal would "finish" first, trip the out-of-order
        # guard, and the 4s OOO backstop would force-fire the watchdog WHILE the
        # caller was still hearing the answer (premature "Sorry, I didn't catch
        # that" re-ask on every multi-sentence FAQ turn).  Instead we track the
        # absolute monotonic time the queued audio actually finishes playing and
        # schedule each chunk's callback for that instant, so chunks finish
        # strictly in order.  Reset to 0.0 on barge-in (Twilio buffer cleared).
        self._tts_playout_end_mono: float = 0.0

        # ── Global 10-second silence safety net ───────────────────────────
        # _last_audio_or_transcript_ts is updated at TTS start and on every
        # accepted transcript so _silence_safety_net() can detect genuine
        # dead-air periods where the entire pipeline has stalled.
        self._last_audio_or_transcript_ts: float = 0.0

        # ── Per-turn LLM task reference ───────────────────────────────────
        # Stored so that if a newer transcript arrives while an LLM call is
        # already in-flight (edge-case race: two rapid STT finals both pass
        # the _llm_busy guard before it is set), the stale task is cancelled
        # before the new one starts.  Reset to None in the turn finally block.
        self._current_llm_task: Optional[asyncio.Task] = None

        # ── Silence handler (4-second re-ask) ─────────────────────────────
        # Created eagerly so _handle_media can call on_audio_received() before
        # the LLM loop starts.  tts_text_queue exists from __init__ so it's
        # safe to pass here.
        # Wrap _on_transfer_request so silence-triggered transfers set the
        # silence_transfer flag before the guard runs.  We use a closure
        # (not a bound-method reference) because self.session is reassigned
        # on the "start" event — the closure captures `self`, not the dict.
        async def _silence_transfer_fn() -> None:
            self.session["silence_transfer"] = True
            await self._on_transfer_request()

        async def _silence_reask_fn(text: str) -> None:
            """Save re-ask to conversation_history so the test evaluator can see it."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        async def _silence_history_fn(text: str) -> None:
            """Save transfer phrase to conversation_history so evaluator can check it.
            The transfer phrase is played directly from SilenceHandler (bypassing
            FlowEngine) so without this callback it would be invisible to the test
            evaluator's transfer_played / transfer_has_trouble_hearing checks."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        async def _silence_dead_air_ts_reset_fn() -> None:
            """Reset the 10-second dead-air safety-net anchor when the watchdog
            fires a re-ask or retires — prevents the safety net from seeing
            the post-reask silence as a new dead-air stall."""
            self._last_audio_or_transcript_ts = time.monotonic()
            logger.debug(
                "[ms_conn] dead-air ts reset via watchdog callback"
            )

        self._silence_handler = SilenceHandler(
            tts_text_queue=self.tts_text_queue,
            trigger_transfer_fn=_silence_transfer_fn,
            on_reask=_silence_reask_fn,
            on_transfer=_silence_history_fn,
            # Lambda captures self (not the dict) so it always returns the
            # current session even after self.session is reassigned on "start".
            get_session=lambda: self.session,
            on_dead_air_ts_reset=_silence_dead_air_ts_reset_fn,
        )

        # ── Call stability ─────────────────────────────────────────────────
        # Set True after the first complete STT -> LLM -> TTS cycle.
        # Router uses this to distinguish "pipeline failed at startup" from
        # "call ended normally or after a stable conversation started".
        self._call_stable: bool = False

        # Heard-nothing slot recovery (Bug A): when a barge-in's tts_inhibit
        # flag discards an entire slot presentation before the caller hears any
        # option, re-queue the saved chunks once instead of going silent.
        self._inhibited_slot_chunks: list = []
        self._slot_represented_once: bool = False

        # Spec J: True when the last LLM response confirmed a slot AND asked
        # for the patient's name.  Short confirming responses ('yes', 'perfect',
        # 'sounds good') bypass the Spec H slot guard while this is True.
        # Evaluated post-turn based on the presence of _NAME_REQUEST_PHRASES
        # in the full (untruncated) last assistant message.
        self.post_slot_confirmation_pending: bool = False

        # Spec K: lifecycle stage for the DTMF slot map.
        # DAY_SELECTION → TIME_SELECTION → NONE (one-way per booking flow).
        # Reset to DAY_SELECTION on new patient turn (new availability check).
        self.slot_map_stage: SlotMapStage = SlotMapStage.NONE

        # Spec P: set True on the first confirmed booking ack; never cleared
        # within a call.  Once True, all subsequent ack detection and synthetic
        # timing-pref re-queues are suppressed so mid-flow "Of course —"
        # responses cannot re-trigger the booking ack handler.
        self.booking_flow_active: bool = False

    # ========================================================================
    # Public entry point
    # ========================================================================

    async def handle(self) -> None:
        """
        Main entry point called once per WebSocket connection.

        Starts all pipeline coroutines concurrently, waits for the stop event,
        then cancels and cleans up.

        Sets self._call_stable = True after the first complete STT->LLM->TTS cycle.
        Raises nothing — all exceptions are caught internally.
        """
        logger.info("[ms_conn] new WebSocket connection")
        await self.websocket.accept()

        tasks = [
            asyncio.create_task(self._receive_loop(),       name="ms_receive"),
            asyncio.create_task(self._audio_in_loop(),      name="ms_audio_in"),
            asyncio.create_task(self._stt_loop(),           name="ms_stt"),
            asyncio.create_task(self._llm_loop(),           name="ms_llm"),
            asyncio.create_task(self._tts_loop(),           name="ms_tts"),
            asyncio.create_task(self._send_loop(),          name="ms_send"),
            asyncio.create_task(self._silence_safety_net(), name="ms_safety_net"),
            # _silence_reask_loop replaced by SilenceHandler (event-driven)
        ]

        try:
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("[ms_conn] handle(): unexpected error: %r", exc)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cleanup()

    # ========================================================================
    # Receive loop
    # ========================================================================

    async def _receive_loop(self) -> None:
        """
        Read JSON messages from the Twilio WebSocket continuously.

          connected  -> log
          start      -> _handle_start() (creates session, sets _started_event)
          media      -> decode base64, enqueue raw mulaw bytes
          stop       -> set _stop_event, exit
        """
        try:
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(
                        self.websocket.receive_text(),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    logger.info("[ms_conn] Twilio WebSocket disconnected")
                    self._stop_event.set()
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[ms_conn] non-JSON frame: %r", raw[:80])
                    continue

                event = msg.get("event")

                if event == "connected":
                    logger.info(
                        "[ms_conn] connected protocol=%s version=%s",
                        msg.get("protocol"), msg.get("version"),
                    )

                elif event == "start":
                    await self._handle_start(msg)

                elif event == "media":
                    await self._handle_media(msg)

                elif event == "dtmf":
                    await self._handle_dtmf(msg)

                elif event == "stop":
                    logger.info("[ms_conn] stop event stream_sid=%s", msg.get("streamSid"))
                    self._stop_event.set()
                    break

                else:
                    logger.debug("[ms_conn] unknown event=%r", event)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _receive_loop error: %r", exc)
            self._stop_event.set()

    async def _handle_dtmf(self, msg: Dict[str, Any]) -> None:
        """
        Process a Twilio "dtmf" event (keypad digit press).

        Accumulates digits into session["phone_dtmf_buffer"].
        When 10–11 digits are collected, synthesises a transcript so the
        COLLECT_PHONE hard gate can process the phone number naturally.

        Only active while state == "COLLECT_PHONE" and phone_awaiting_dtmf=True.
        Resets the silence timer after each keypress so the caller can type
        without triggering a silence re-ask mid-entry.
        """
        if not self.session:
            return

        digit = (msg.get("dtmf") or {}).get("digit", "")
        if not digit:
            return

        logger.info("[ms_conn] DTMF raw digit=%r v3_phone_dtmf_active=%s", digit, self.session.get("v3_phone_dtmf_active", False))

        # Spec V — both * and # are reset keys; both clear the buffer and
        # re-prompt with the same message.  v3_phone_dtmf_active stays True
        # so the patient remains in keypad mode after a reset.
        if digit in {"*", "#"}:
            if self.session.get("v3_phone_dtmf_active"):
                # Ordering guarantee: clear the buffer and persist it BEFORE
                # queuing TTS.  The await asyncio.sleep(0) yields to the event
                # loop so the dict mutation is visible to any concurrent reader
                # before the TTS coroutine starts consuming the queue.
                self.session["phone_dtmf_buffer"] = ""
                logger.info("[ms_conn] DTMF %s — buffer cleared (reset key)", digit)
                await asyncio.sleep(0)          # yield: clear resolves before TTS
                await save_session(self.call_sid, self.session)  # persist before TTS
                _reset_msg = "No problem — buffer cleared. Go ahead and type the number again."
                await self.tts_text_queue.put(_reset_msg)
                self.session["last_bot_prompt"] = _reset_msg
                logger.info(
                    "[ms_conn] DTMF %s — reset announced (ordering: clear→save→TTS)", digit
                )
            return

        # First real keypad press: cancel any leftover speech watchdog / W1-W3
        # silence cascade.  The caller has switched to the keypad channel; any
        # pending speech-first "Sorry, I didn't catch that" re-ask must not
        # fire on top of the DTMF interaction.
        if self._silence_handler is not None:
            _wdg = getattr(self._silence_handler, "_no_input_watchdog_task", None)
            _tsk = getattr(self._silence_handler, "_task", None)
            if (_wdg is not None and not _wdg.done()) or (_tsk is not None and not _tsk.done()):
                logger.info("[ms_conn] DTMF digit received — cancelling speech watchdog")
                self._silence_handler._cancel_timer()

        # ── Specialist handlers: location / intro / slot / ASK_LOCATION ────────
        # Spec U: wrapped in `if not v3_phone_dtmf_active` so that no handler
        # can intercept a digit and return before it reaches the phone buffer.
        # When phone-collection mode is active every digit must reach line
        # `buf = ... + digit` below — the specialist handlers are irrelevant at
        # that point and must be skipped entirely.
        if not self.session.get("v3_phone_dtmf_active"):

            # ── theorem_v3 location DTMF fallback ────────────────────────────
            # Fires when two rounds of voice resolution failed and the caller
            # was asked to press 1 (Alcester) or 2 (Redditch) on their keypad.
            if self.session.get("v3_awaiting_location_dtmf"):
                if digit == "1":
                    _loc_dtmf = "alcester"
                elif digit == "2":
                    _loc_dtmf = "redditch"
                else:
                    # Invalid key — re-prompt once
                    _invalid_msg = (
                        "Press 1 for Awlstuh "
                        "or 2 for Redditch."
                    )
                    await self.tts_text_queue.put(_invalid_msg)
                    self.session["last_bot_prompt"] = _invalid_msg
                    await save_session(self.call_sid, self.session)
                    return

                # Valid digit — resolve location
                self.session["v3_awaiting_location_dtmf"] = False
                self.session["selected_location"] = _loc_dtmf
                self.session["v3_location_confirmed"] = True
                self.session["v3_location_asked"] = False
                self.session["v3_location_q_active"] = False
                _disp = _loc_dtmf.capitalize()
                _ack = f"{_disp}."
                _intent = self.session.get("v3_caller_intent", "booking")
                if _intent in ("reschedule", "cancel"):
                    _next_q = (
                        "Is the number you're calling on "
                        "the one associated with your "
                        "booking? If so, just say "
                        "'use this number'."
                    )
                    self.session["v3_awaiting_phone_confirm"] = True
                else:
                    # FAQ-before-clinic: if the caller asked a clinic-specific
                    # FAQ (parking/hours/etc.) and we only asked the clinic in
                    # order to answer it, re-queue that question now the clinic
                    # is known — do NOT drop them into the booking timing flow.
                    # Mirrors the verbal location-intercept non-booking path
                    # (keyed off v3_booking_intent).  synthetic=True so the
                    # re-injection clears the STT-phantom guards.
                    _dtmf_faq_pending = self.session.pop(
                        "v3_faq_pending_utterance", None
                    )
                    _dtmf_faq_requeued = False
                    if _dtmf_faq_pending and not self.session.get(
                        "v3_booking_intent", False
                    ):
                        _next_q = None
                        _dtmf_tp = ""
                        _dtmf_faq_requeued = True
                        await self.transcript_queue.put(
                            (time.monotonic(), _dtmf_faq_pending, True)
                        )
                        logger.info(
                            "[ms_conn v3] DTMF: FAQ pending re-queued after"
                            " clinic confirm (no booking Q): %r",
                            _dtmf_faq_pending[:60],
                        )
                    else:
                        # CODE SPEC AE REVISED — routing check mirrors direct intercept
                        _dtmf_sc = (self.session.get("soft_context") or {})
                        _dtmf_tp = (
                            _dtmf_sc.get("time_preference")
                            or self.session.get("time_of_day_preference")
                            or ""
                        )
                        logger.info(
                            "[ms_conn v3] DTMF location routing —"
                            " soft_context.time_preference=%r"
                            " time_of_day_preference=%r",
                            _dtmf_sc.get("time_preference"),
                            self.session.get("time_of_day_preference"),
                        )
                        _next_q = (
                            None if _dtmf_tp
                            else (
                                "Is there a particular day or time "
                                "that works best for you?"
                            )
                        )
                await self.tts_text_queue.put(_ack)
                if _next_q is not None:
                    await self.tts_text_queue.put(_next_q)
                    self.session["last_bot_prompt"] = _next_q
                    self.session["last_question"] = _next_q
                    # BUG 1 FIX (P0) — record the follow-up question in
                    # conversation_history so the LLM's message history reflects
                    # that the clinic is resolved and the question on the table
                    # is now day/time.  The DTMF ack + ladder re-asks all bypass
                    # run_turn, so without this the most-recent assistant turn in
                    # history stays the *clinic* question — and an ambiguous next
                    # reply ("I'm not too sure") reads as clinic indecision,
                    # letting the LLM re-open location and discard the resolved
                    # clinic.  Mirrors the proven-good verbal use-this-clinic path.
                    self.session.setdefault(
                        "conversation_history", []
                    ).append({
                        "role": "assistant",
                        "content": _next_q,
                    })
                elif (
                    _intent not in ("reschedule", "cancel")
                    and not _dtmf_faq_requeued
                ):
                    if self.booking_flow_active:
                        # Booking already active: ask the day/time question
                        # rather than re-queueing the stored timing (no tool
                        # fires, no strand).  Without this the call dead-airs
                        # after the clinic ack — same bug as the verbal
                        # use-this-clinic path (2026-06-19).
                        _dtmf_dt_q = (
                            "Is there a particular day or time "
                            "that works best for you?"
                        )
                        await self.tts_text_queue.put(_dtmf_dt_q)
                        self.session["last_bot_prompt"] = _dtmf_dt_q
                        self.session["last_question"] = _dtmf_dt_q
                        self.session.setdefault(
                            "conversation_history", []
                        ).append({
                            "role": "assistant",
                            "content": _dtmf_dt_q,
                        })
                        logger.info(
                            "[ms_conn v3] DTMF: booking active — asked"
                            " day/time Q (no strand)"
                        )
                    else:
                        await self.transcript_queue.put(
                            (time.monotonic(), _dtmf_tp)
                        )
                        logger.info(
                            "[ms_conn v3] DTMF: time preference known"
                            " (%r) — re-queued for check_availability",
                            _dtmf_tp,
                        )
                await save_session(self.call_sid, self.session)
                # Arm the watchdog for the follow-up question.  The verbal path
                # gets this for free because on_transcript_received() resets
                # _no_input_reask_count before _restart_timer runs.  DTMF never
                # triggers on_transcript_received, so _no_input_reask_count can
                # be > 0 from the previous "Press 1 or 2" watchdog fire — hitting
                # WATCHDOG_RETIRED_FOR_QGEN and leaving the new question unguarded.
                # on_question_asked increments _q_gen, resets _no_input_reask_count,
                # resets _watchdog_has_retired, and arms a fresh watchdog.
                if self._silence_handler is not None and _next_q is not None:
                    self._silence_handler.on_question_asked(_next_q)
                    logger.info(
                        "[ms_conn v3] DTMF location resolved: "
                        "%s (digit=%s) — watchdog armed for follow-up q", _loc_dtmf, digit,
                    )
                else:
                    logger.info(
                        "[ms_conn v3] DTMF location resolved: "
                        "%s (digit=%s)", _loc_dtmf, digit,
                    )
                return

            # theorem_v3 intro: digit 1 → transfer to Mark; any other digit is
            # swallowed (caller mis-pressed).  Clears the flag regardless so it
            # never leaks into subsequent turns.
            if self.session.get("v3_intro_dtmf_active"):
                self.session["v3_intro_dtmf_active"] = False
                if digit == "1":
                    logger.info("[ms_conn] theorem_v3: intro digit=1 — transferring to Mark")
                    await self.tts_text_queue.put(
                        "Transferring you to Mark now — one moment."
                    )
                    self.session["transfer_requested_by_caller"] = True
                    await self._on_transfer_request()
                return

            # theorem_v3 slot / time selection — fallback DTMF only.
            # DTMF is armed here (not at slot-presentation time) when:
            #   1. A slot map exists from a previous presentation turn, AND
            #   2. The LLM just re-asked with a "keypad" suggestion (indicating
            #      it could not understand the caller's spoken slot choice).
            # On the first digit after arming, inject the mapped label as a
            # synthetic transcript so the LLM processes it as a spoken choice.
            _slot_map = self.session.get("v3_dtmf_slot_map", {})
            # Spec K: only arm / process slot DTMF when the stage says a slot map
            # is active.  During name collection (NONE) any keypad press must fall
            # through to phone-number handling rather than being misread as a day
            # or time re-selection.
            _slot_stage_active = self.slot_map_stage in (
                SlotMapStage.DAY_SELECTION, SlotMapStage.TIME_SELECTION
            )
            if (
                _slot_map
                and _slot_stage_active
                and not self.session.get("v3_slot_dtmf_active")
                and "keypad" in self.session.get("last_bot_prompt", "").lower()
            ):
                logger.info(
                    "[ms_conn] theorem_v3: slot DTMF fallback armed "
                    "(stage=%s, last_bot_prompt contains 'keypad', map=%r)",
                    self.slot_map_stage.name,
                    _slot_map,
                )
                self.session["v3_slot_dtmf_active"] = True

            if self.session.get("v3_slot_dtmf_active") and digit in "123456789":
                if not _slot_stage_active:
                    # Stage has moved to NONE since arming — digit is not a slot
                    # selection; disarm silently and fall through to phone handling.
                    logger.info(
                        "[ms_conn] theorem_v3: slot DTMF digit=%r ignored"
                        " — stage is %s (not a slot stage)",
                        digit, self.slot_map_stage.name,
                    )
                    self.session.pop("v3_slot_dtmf_active", None)
                    # Fall through — do NOT return; phone handler may need digit.
                else:
                    _label = _slot_map.get(digit)
                    # Disarm regardless — one press = one selection
                    self.session.pop("v3_slot_dtmf_active",        None)
                    self.session.pop("v3_dtmf_slot_map",           None)
                    self.session.pop("v3_awaiting_slot_selection", None)
                    if _label:
                        logger.info(
                            "[ms_conn] theorem_v3: slot DTMF digit=%r → injecting %r"
                            " (stage=%s)",
                            digit, _label, self.slot_map_stage.name,
                        )
                        await self.transcript_queue.put((time.monotonic(), _label))
                    else:
                        logger.info(
                            "[ms_conn] theorem_v3: slot DTMF digit=%r — no mapping, ignored",
                            digit,
                        )
                    return

            # ASK_LOCATION: digit 1 → alcester, digit 2 → redditch (immediate, no accumulation)
            if self.session.get("state") == "ASK_LOCATION":
                if digit == "1":
                    logger.info("[ms_conn] DTMF digit=1 → synthetic transcript 'alcester'")
                    await self.transcript_queue.put((time.monotonic(), "alcester"))
                elif digit == "2":
                    logger.info("[ms_conn] DTMF digit=2 → synthetic transcript 'redditch'")
                    await self.transcript_queue.put((time.monotonic(), "redditch"))
                return

        # end: specialist handlers (skipped when v3_phone_dtmf_active=True)

        # theorem_v3 booking path: the LLM can ask the caller to "type on your
        # keypad" from run_turn() without any structured handler having set
        # v3_phone_dtmf_active.  Only fires when no slot map exists (slot map
        # takes priority for keypad detection above).
        if (
            self.session.get("clinic_id") == "theorem_v3"
            and not self.session.get("v3_phone_dtmf_active")
            and not self.session.get("v3_dtmf_slot_map")
            and "keypad" in self.session.get("last_bot_prompt", "").lower()
        ):
            logger.info(
                "[ms_conn] theorem_v3: auto-activating v3_phone_dtmf_active "
                "(last_bot_prompt contains 'keypad')"
            )
            self.session["v3_phone_dtmf_active"] = True

        # Only accumulate DTMF while in phone-collection state, keypad lookup
        # recovery, or theorem_v3 DTMF phone collection.
        if (
            self.session.get("state") not in (
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
                "RETURNING_PLAN_COLLECT_PHONE",
            )
            and not self.session.get("rc_kp_phone_pending")
            and not self.session.get("v3_phone_dtmf_active")
        ):
            return

        buf = self.session.get("phone_dtmf_buffer", "") + digit
        self.session["phone_dtmf_buffer"] = buf

        # Log immediately after buffering so buf= always appears in logs even
        # if TTS cancellation or finalization runs next (Spec U).
        logger.info("[ms_conn] DTMF digit=%r buf=%r", digit, buf)

        # Spec U — cancel TTS on the first digit so Susie stops speaking the
        # moment the caller starts typing.  Digits must reach the buffer
        # regardless of playback state; the cancellation is a side-effect, not
        # a gate.
        if len(buf) == 1:
            await self._cancel_tts_playback()

        # Each keypress resets the silence timer (caller is actively typing).
        # last_dtmf_at is the authoritative "DTMF is live" signal used by the
        # watchdog Phase 3 guard — it persists even if phone_dtmf_buffer is cleared.
        _now_dtmf = time.time()
        self._silence_handler.last_audio_received_at = _now_dtmf
        self._silence_handler.last_engagement_at     = _now_dtmf
        self._silence_handler.last_dtmf_at           = _now_dtmf
        # Reset safety-net dead-air anchor so the 10s backstop never fires
        # mid-number even if the caller pauses between digits.
        self._last_audio_or_transcript_ts = time.monotonic()

        # Cancel any pending idle-finalize tasks; a new digit just arrived so
        # the caller is still actively typing.  Fresh tasks are scheduled
        # below based on the current buffer length.
        if self._dtmf_idle_task and not self._dtmf_idle_task.done():
            self._dtmf_idle_task.cancel()
            self._dtmf_idle_task = None
        if self._dtmf_near_complete_task and not self._dtmf_near_complete_task.done():
            self._dtmf_near_complete_task.cancel()
            self._dtmf_near_complete_task = None

        if len(buf) >= 11:
            # Full UK number collected via keypad — push as synthetic
            # transcript immediately; no idle window needed.
            complete = buf[:11]
            self.session["phone_dtmf_buffer"]   = ""
            self.session["phone_awaiting_dtmf"] = False
            self.session["v3_phone_dtmf_active"] = False
            logger.info(
                "[ms_conn] DTMF 11-digit complete → immediate finalize %r",
                complete,
            )
            self._inject_phone_context_for_lookup(complete)
            await self.transcript_queue.put((time.monotonic(), complete))
            await save_session(self.call_sid, self.session)
            logger.info("[ms_conn v3] DTMF phone collection complete")
        elif len(buf) >= 10:
            # Plausibly complete (UK 10-digit without leading 0).  Wait a
            # short idle window for further digits; if none arrive, finalize.
            self._dtmf_idle_task = asyncio.create_task(
                self._dtmf_idle_finalize(buf), name="ms_dtmf_idle_finalize"
            )
            # Also arm the 5 s near-complete safety net independently.
            self._dtmf_near_complete_task = asyncio.create_task(
                self._dtmf_near_complete_finalize(buf),
                name="ms_dtmf_near_complete",
            )
        elif len(buf) >= 9:
            # Nearly complete: arm only the 5 s safety net (standard 3.5 s
            # idle task is not scheduled for sub-10 buffers).
            self._dtmf_near_complete_task = asyncio.create_task(
                self._dtmf_near_complete_finalize(buf),
                name="ms_dtmf_near_complete",
            )

    async def _dtmf_idle_finalize(self, expected_buf: str) -> None:
        """
        Finalize the keypad buffer after a short idle window when the caller
        has typed enough digits to plausibly complete a number but stopped.

        Cancelled by _handle_dtmf whenever a new digit arrives.  Only fires
        if the buffer is unchanged and still holds the same digits.
        """
        _KEYPAD_IDLE_FINALIZE_SEC = 3.5
        try:
            await asyncio.sleep(_KEYPAD_IDLE_FINALIZE_SEC)
        except asyncio.CancelledError:
            return
        if not self.session:
            return
        buf = self.session.get("phone_dtmf_buffer", "")
        if buf != expected_buf:
            # Another digit arrived during the sleep window (race) — newer
            # task will handle finalization.
            return
        if self.session.get("state") not in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
            "RETURNING_PLAN_COLLECT_PHONE",
        ) and not self.session.get("rc_kp_phone_pending") \
          and not self.session.get("v3_phone_dtmf_active"):
            return
        if len(buf) < 10:
            return
        # Pad 10-digit buffer with leading 0 so the flow gate's 11-digit
        # threshold accepts it; otherwise truncate to 11.
        complete = ("0" + buf) if len(buf) == 10 else buf[:11]
        self.session["phone_dtmf_buffer"]   = ""
        self.session["phone_awaiting_dtmf"] = False
        self.session["v3_phone_dtmf_active"] = False
        logger.info(
            "[ms_conn] DTMF idle-finalize after %.1fs → synthetic transcript %r",
            _KEYPAD_IDLE_FINALIZE_SEC, complete,
        )
        self._inject_phone_context_for_lookup(complete)
        await self.transcript_queue.put((time.monotonic(), complete))
        await save_session(self.call_sid, self.session)
        logger.info("[ms_conn v3] DTMF phone collection complete (idle-finalize)")

    async def _dtmf_near_complete_finalize(self, expected_buf: str) -> None:
        """
        Safety-net finalize for buffers of 9+ digits.

        Fires 5 seconds after the last digit when the buffer looks nearly
        complete but the standard 3.5 s idle task was either not scheduled
        (9-digit buffer) or was silently interfered with.  Independent of
        _dtmf_idle_finalize — neither task cancels the other.  If the
        standard idle task already fired and cleared the buffer, the
        ``buf != expected_buf`` guard below ensures this task is a no-op.
        """
        _NEAR_COMPLETE_SEC = 5.0
        try:
            await asyncio.sleep(_NEAR_COMPLETE_SEC)
        except asyncio.CancelledError:
            return
        if not self.session:
            return
        buf = self.session.get("phone_dtmf_buffer", "")
        if buf != expected_buf:
            # Buffer changed (more digits arrived, or standard task already
            # finalized) — nothing to do.
            return
        if self.session.get("state") not in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE",
            "RETURNING_PLAN_COLLECT_PHONE",
        ) and not self.session.get("rc_kp_phone_pending") \
          and not self.session.get("v3_phone_dtmf_active"):
            return
        if len(buf) < 9:
            return
        # Use the buffer as-is — the caller typed these digits and stopped.
        complete = buf
        self.session["phone_dtmf_buffer"]   = ""
        self.session["phone_awaiting_dtmf"] = False
        self.session["v3_phone_dtmf_active"] = False
        logger.info(
            "[ms_conn] DTMF near-complete finalize (len=%d, timeout=5s) → "
            "synthetic transcript %r",
            len(complete), complete,
        )
        self._inject_phone_context_for_lookup(complete)
        await self.transcript_queue.put((time.monotonic(), complete))
        await save_session(self.call_sid, self.session)
        logger.info("[ms_conn v3] DTMF phone collection complete (near-complete finalize)")

    def _inject_phone_context_for_lookup(self, phone: str) -> None:
        """Inject a synthetic assistant turn into conversation_history before a
        DTMF phone number is queued as a transcript in cancel / reschedule flows.

        Without this, the LLM receives the phone number without any prior
        context asking for it and asks for the number again instead of calling
        lookup_patient.  The injection makes the history look like:
            assistant: "Please enter the phone number you booked under…"
            user: "07426779875"
        so the LLM correctly calls lookup_patient(phone=..., purpose=...).
        """
        intent = self.session.get("v3_caller_intent", "")
        if intent not in ("cancel", "reschedule"):
            return

        history = self.session.setdefault("conversation_history", [])

        # Only inject if the last assistant message doesn't already ask for phone.
        _last_assistant = ""
        for _msg in reversed(history):
            if _msg.get("role") == "assistant":
                _last_assistant = (_msg.get("content") or "").lower()
                break
        _phone_signals = ("number", "phone", "keypad", "booked under", "calling from")
        if any(s in _last_assistant for s in _phone_signals):
            # History already has phone context — nothing to do.
            return

        # Use whatever was last asked; fall back to a sensible default.
        _ctx_q = (
            self.session.get("last_question")
            or self.session.get("last_bot_prompt")
            or (
                "No problem — could I take the number you booked under, "
                "or just say 'use this number' if you'd like me to use "
                "the one you're calling from."
            )
        )
        history.append({"role": "assistant", "content": _ctx_q})
        logger.info(
            "[ms_conn v3] injected phone context for %s lookup: %r",
            intent, _ctx_q[:80],
        )

    async def _fire_name_reask(self) -> None:
        """
        Fires 800 ms after an incomplete name utterance ("my name is…") if no
        follow-up final transcript arrived to complete it.  Asks the caller to
        repeat their name rather than letting two overlapping LLM calls both
        respond at once.
        """
        try:
            await asyncio.sleep(0.8)
        except asyncio.CancelledError:
            return
        if not self._name_collection_pending:
            return  # follow-up arrived in time — already resolved
        self._name_collection_pending = False
        self._name_collection_timer = None
        if not self.session:
            return
        _reask = (
            "Sorry, I didn't quite catch your name — "
            "could you say it again?"
        )
        # Mark the clarification as queued BEFORE putting it on the queue.
        # If a name transcript arrives after this point the drain logic can
        # remove this phrase before TTS synthesis starts.
        # Also set the in-flight guard so that the first noise utterance that
        # arrives while the phrase is still queued/playing is discarded rather
        # than triggering a competing LLM response.
        self._clarification_in_flight = True
        self._name_clarification_queued = True
        await self.tts_text_queue.put(_reask)
        self.session["last_bot_prompt"] = _reask
        self.session["last_question"]   = _reask
        await save_session(self.call_sid, self.session)
        logger.info("[ms_conn v3] name re-ask fired after 800ms timeout")

    async def _handle_start(self, msg: Dict[str, Any]) -> None:
        """
        Process the Twilio "start" event.

        Extracts call_sid / stream_sid, loads or creates the session,
        stamps stream_sid into the session, and fires _started_event so
        the other pipeline loops can begin.
        """
        start_data      = msg.get("start", {})
        self.stream_sid = msg.get("streamSid") or start_data.get("streamSid", "")
        self.call_sid   = start_data.get("callSid", "")

        custom_params = start_data.get("customParameters", {})
        twilio_from   = custom_params.get("twilio_from") or start_data.get("from", "")
        twilio_to     = custom_params.get("twilio_to")   or start_data.get("to",   "")

        logger.info(
            "[ms_conn] start call_sid=%s stream_sid=%s from=%s to=%s",
            self.call_sid, self.stream_sid, twilio_from, twilio_to,
        )

        # Check Redis for From/To numbers pre-cached by /ms/incoming POST handler.
        # Twilio does NOT reliably forward customParameters or caller numbers into
        # the WebSocket start event — Redis is the only reliable fallback.
        if (not twilio_from or not twilio_to) and self.call_sid:
            try:
                from .session import _get_redis
                _redis = _get_redis()
                if _redis:
                    if not twilio_from:
                        _cached_from = await _redis.get(f"ms_caller:{self.call_sid}")
                        if _cached_from:
                            twilio_from = _cached_from.decode() if isinstance(_cached_from, bytes) else _cached_from
                            logger.info("[ms_conn] twilio_from from Redis: %s", twilio_from)
                            await _redis.delete(f"ms_caller:{self.call_sid}")
                    if not twilio_to:
                        _cached_to = await _redis.get(f"ms_to:{self.call_sid}")
                        if _cached_to:
                            twilio_to = _cached_to.decode() if isinstance(_cached_to, bytes) else _cached_to
                            logger.info("[ms_conn] twilio_to from Redis: %s", twilio_to)
                            await _redis.delete(f"ms_to:{self.call_sid}")
            except Exception as _exc:
                logger.warning("[ms_conn] Redis caller lookup failed: %r", _exc)

        initial: Dict[str, Any] = {}

        # Direct-WS test mode: the call_runner sends a fake accountSid that
        # contains "direct_ws".  Flag it in the session so flow.py can
        # auto-complete steps that have no subsequent user turn in test scripts.
        _account_sid = start_data.get("accountSid", "")
        if "direct_ws" in _account_sid:
            initial["direct_ws_test"] = True
            logger.info("[ms_conn] direct_ws_test mode detected (accountSid=%s)", _account_sid)

        if twilio_from:
            initial["twilio_from"] = twilio_from
            if twilio_from.startswith("+44"):
                initial["twilio_from_local"] = "0" + twilio_from[3:]
        if twilio_to:
            initial["twilio_to"] = twilio_to
            # Resolve clinic_id from the dialled number so tools/SMS/config use the right clinic.
            from app.clinic_config import clinic_id_from_twilio_to
            initial["clinic_id"] = clinic_id_from_twilio_to(twilio_to)
            logger.info("[ms_conn] clinic_id resolved: %s (to=%s)", initial["clinic_id"], twilio_to)

        # ── Layer 2 fallback: env var override ───────────────────────────
        # If clinic_id is still not resolved (twilio_to was empty through all
        # three resolution paths: customParameters, Redis, start_data), use the
        # MEDIA_STREAMS_CLINIC_ID env var as an absolute last resort.
        # Set MEDIA_STREAMS_CLINIC_ID=theorem on Render for this service.
        if not initial.get("clinic_id"):
            import os as _os
            _env_cid = _os.getenv("MEDIA_STREAMS_CLINIC_ID", "").strip()
            if _env_cid:
                initial["clinic_id"] = _env_cid
                logger.warning(
                    "[ms_conn] clinic_id NOT resolved from twilio_to — "
                    "using env MEDIA_STREAMS_CLINIC_ID=%s (twilio_to=%r)",
                    _env_cid, twilio_to,
                )
            else:
                logger.error(
                    "[ms_conn] clinic_id unresolved AND MEDIA_STREAMS_CLINIC_ID not set — "
                    "calls will route to demo/Google Calendar. "
                    "Set MEDIA_STREAMS_CLINIC_ID on Render.",
                )

        self.session = await get_or_create_session(self.call_sid, initial=initial)
        self.session["stream_sid"]   = self.stream_sid
        self.session["ws_connected"] = True

        # Register in the active-handler map so /ms/test/inject-transcript
        # can drive the conversation without going through STT.
        if self.call_sid:
            _active_handlers[self.call_sid] = self
            logger.info(
                "[ms_conn] WS session registered sid=%s total_active=%d",
                self.call_sid, len(_active_handlers),
            )

        # Populate collected.phone from Twilio caller-ID so Susie never asks for it.
        if twilio_from:
            logger.info("[ms_conn] caller number from Twilio: %s", twilio_from)
            collected = self.session.setdefault("collected", {})
            if not collected.get("phone"):
                collected["phone"] = twilio_from
            self.session["phone_from_twilio"] = True
        else:
            logger.info("[ms_conn] no caller number from Twilio — will collect manually")

        await save_session(self.call_sid, self.session)

        # Instantiate per-call logger (stored on instance, not in session — not JSON-serialisable)
        from app.call_logger import CallLogger
        self._call_logger = CallLogger(self.call_sid, self.session)

        self._started_event.set()

        # Inject greeting asynchronously (no LLM round-trip)
        asyncio.create_task(self._inject_greeting())

    async def _handle_media(self, msg: Dict[str, Any]) -> None:
        """
        Process a Twilio "media" event.

        Decodes the base64 mulaw payload and puts raw bytes onto audio_in_queue.
        Audio ALWAYS flows to AssemblyAI regardless of _clearing state.
        Dropping audio while _clearing=True was the barge-in deadlock:
          _clearing=True → no audio → no STT final → _on_final_transcript_clear
          never fires → _clearing stays True forever.
        """
        payload_b64 = msg.get("media", {}).get("payload", "")
        if not payload_b64:
            return

        try:
            raw_mulaw = base64.b64decode(payload_b64)
        except Exception as exc:
            logger.warning("[ms_conn] base64 decode error: %r", exc)
            return

        self._last_audio_received_at = time.monotonic()
        self._silence_handler.on_audio_received()
        self.audio_in_queue.put_nowait(raw_mulaw)

        # ── Energy VAD: cancel silence timer the moment caller speaks ─────────
        # Twilio μ-law silence packets consist almost entirely of 0xFF bytes
        # (G.711 μ-law encoding of PCM zero).  When the caller speaks, non-0xFF
        # bytes appear immediately — cancelling the re-ask timer here closes the
        # 1-3 second gap between caller speaking and AssemblyAI delivering a
        # partial transcript, which was the root cause of questions being asked
        # twice during real calls.
        # Only checked when the silence timer is actually running (task exists
        # and is not done) so this adds near-zero overhead during normal flow.
        if (
            not self._clearing
            and self._silence_handler._task is not None
            and not self._silence_handler._task.done()
            and len(raw_mulaw) - raw_mulaw.count(0xFF) > 3
        ):
            self._silence_handler.on_speech_started()

    # ========================================================================
    # Audio input loop
    # ========================================================================

    async def _audio_in_loop(self) -> None:
        """
        Wait for the "start" event, then run AudioInputProcessor which
        converts mulaw 8kHz -> PCM16 16kHz and writes to stt_input_queue.
        """
        await self._wait_for_start("audio_in_loop")
        try:
            await self._audio_in_proc.process_stream(
                self.audio_in_queue,
                self.stt_input_queue,
                self._stop_event,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _audio_in_loop error: %r", exc)

    # ========================================================================
    # STT loop
    # ========================================================================

    async def _stt_loop(self) -> None:
        """
        Wait for the "start" event, then run STTStream which connects to
        AssemblyAI and puts FinalTranscript utterances into transcript_queue.

        On AssemblyAI disconnect: STTStream handles reconnect internally.
        If all reconnects fail, the STT loop exits — but the call continues
        (caller can still hear Susie; just can't be heard).
        """
        await self._wait_for_start("stt_loop")
        try:
            await self._stt_stream.start(
                stt_input_queue=self.stt_input_queue,
                transcript_queue=self.transcript_queue,
                stop_event=self._stop_event,
                on_partial=self._on_partial_transcript,
                on_final_clear=self._on_final_transcript_clear,
                tts_text_queue=self.tts_text_queue,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _stt_loop error: %r", exc)

    # ========================================================================
    # LLM loop  (FlowEngine-driven — single point of decision)
    # ========================================================================

    async def _llm_loop(self) -> None:
        """
        Wait for the "start" event, then drive the booking flow.

        First caller utterance → flow.ask_current_question()  (starts the flow)
        Every subsequent utterance → flow.handle_transcript()  (advances the flow)

        That is the entire conversation logic.  Nothing else here makes
        a decision about what Susie says.
        """
        await self._wait_for_start("llm_loop")

        clinic_id = self.session.get("clinic_id", "")

        if clinic_id == "theorem_v3":
            # ────────────────────────────────────────────────────────────────
            # theorem_v3 — free-form LLM loop (Prompt 5)
            # No FlowEngine. Every utterance is handed straight to run_turn(),
            # which streams TTS, fires tools, and appends conversation_history
            # internally.  This branch returns at the end so execution NEVER
            # falls through to the FlowEngine code below.
            # ────────────────────────────────────────────────────────────────
            from .llm_stream import LLMStream

            llm = LLMStream()

            try:
                while not self._stop_event.is_set():
                    try:
                        _raw_item = await asyncio.wait_for(
                            self.transcript_queue.get(),
                            timeout=1.0,
                        )
                    except asyncio.TimeoutError:
                        continue

                    # Unpack timestamped item from queue.
                    # Internal re-injections (FAQ pending / preference re-queues
                    # after a clinic ack) carry a 3rd element flagging them as
                    # synthetic.  These are NOT STT-origin finals, so they must
                    # bypass the STT-phantom guards below (barge-in flush, C8-2
                    # location-ack drop, same-breath straggler) — those guards
                    # exist only to discard stray STT fragments and would
                    # otherwise eat a deliberately re-queued utterance that was
                    # enqueued microseconds before the ack turn completed.
                    _synthetic = False
                    if isinstance(_raw_item, tuple):
                        if len(_raw_item) == 3:
                            _enqueue_ts, utterance, _synthetic = _raw_item
                        else:
                            _enqueue_ts, utterance = _raw_item
                    else:
                        _enqueue_ts, utterance = 0.0, _raw_item  # legacy safety

                    # Discard stale transcripts enqueued before the last confirmed
                    # barge-in — these are phantom STT finals from a burst that
                    # fired before the caller finished interrupting.
                    if not _synthetic and _enqueue_ts < self._barge_in_flush_before:
                        logger.info(
                            "[ms_conn] stale transcript discarded (pre-barge-in): %r",
                            utterance[:80],
                        )
                        continue

                    if not utterance or not utterance.strip():
                        continue

                    # Safety net anchor: accepted transcript keeps dead-air guard at bay.
                    self._last_audio_or_transcript_ts = time.monotonic()
                    # Caller spoke — reset watchdog reask_completed so safety net
                    # can fire again if the next turn goes silent.
                    self._silence_handler._reask_completed = False

                    # ── C8-2 — location-ack race guard ───────────────────────
                    # Drop phantom second finals that arrive in the brief window
                    # after a location ack this turn (see __init__ note).  The
                    # genuine answer to the queued question always arrives later
                    # than the window (the caller must hear the question first,
                    # and the question audio alone takes >1.5s to play), so a
                    # real reply is never suppressed.
                    if not _synthetic and self.session.get("location_acked_this_turn"):
                        _loc_ack_age = time.monotonic() - self._location_ack_ts
                        if _loc_ack_age < self._LOCATION_ACK_DROP_WINDOW:
                            logger.info(
                                "[ms_conn v3] C8-2 transcript dropped — %.0fms"
                                " after location ack (window %.1fs): %r",
                                _loc_ack_age * 1000.0,
                                self._LOCATION_ACK_DROP_WINDOW,
                                utterance[:60],
                            )
                            continue
                        # Window expired — clear the one-shot flag so the next
                        # legitimate turn is never suppressed.
                        self.session["location_acked_this_turn"] = False

                    # ── Same-breath straggler guard ──────────────────────────
                    # STT sometimes splits one spoken utterance into several
                    # FINALs.  A fragment enqueued BEFORE the previous turn
                    # finished was spoken before the caller could hear any
                    # response, so it cannot be a reply to that turn — it is a
                    # tail of the same breath.  The Spec N in-flight guard below
                    # misses it when the fragment is dequeued in the brief window
                    # just after the turn completes (llm_in_flight already
                    # cleared).  Dispatching it fires a redundant second turn
                    # (stress test 2026-06-12: long ankle sentence →
                    # "...that i go down to physiotherapy clinic" → two
                    # overlapping "Would you like to book one?" responses, which
                    # also interleaved the TTS chunk sequence and triggered the
                    # out-of-order stall).  A genuine reply is always enqueued
                    # AFTER the response audio plays (well after _last_turn_done_at),
                    # so this never drops a real answer.
                    if (
                        not _synthetic
                        and self._last_turn_done_at > 0.0
                        and _enqueue_ts > 0.0
                        and _enqueue_ts < self._last_turn_done_at
                    ):
                        logger.info(
                            "[ms_conn] same-breath straggler dropped — enqueued"
                            " %.0fms before prior turn completed (not a reply): %r",
                            (self._last_turn_done_at - _enqueue_ts) * 1000.0,
                            utterance[:60],
                        )
                        continue

                    # Spec N — concurrent LLM guard.
                    # If a turn is already in-flight (from transcript acceptance
                    # through to full completion including tool round-trips),
                    # queue at most one pending transcript; newer overwrites older.
                    if self.llm_in_flight:
                        if self.pending_transcript is not None:
                            logger.info(
                                "[ms_conn] pending transcript overwritten: %r → %r",
                                self.pending_transcript[:60],
                                utterance[:60],
                            )
                        else:
                            logger.info(
                                "[ms_conn] LLM busy — transcript queued: %r",
                                utterance[:60],
                            )
                        self.pending_transcript = utterance
                        continue

                    # Barge-in resolution: false triggers resume TTS without
                    # entering the LLM; confirmed barge-ins queue an ack and
                    # wait for the next utterance.
                    if await self._resolve_barge_in(utterance):
                        continue

                    # ── Booking-flow verbal phone confirm ────────────────────
                    # Reschedule/cancel set v3_awaiting_phone_confirm and have a
                    # deterministic "use this number" handler.  The BOOKING flow
                    # has no such flag — its phone-confirm step is LLM-generated
                    # ("…just say use this number") — so "use this number" used to
                    # fall through to the LLM, which re-ran check_availability
                    # (looping) and never stored the phone → phone=no → no
                    # confirmation SMS (2026-06-23 bug).  Store the calling number
                    # programmatically (mirrors the DTMF-branch intercept) so the
                    # POST-PHONE CONFIRMATION guard drives the LLM to the booking
                    # readback, and mark it confirmed so the SMS router has a
                    # number even if the caller then drops at the readback.
                    # Tightly gated: booking only, not during DTMF, not the
                    # reschedule/cancel path, only at the phone-confirm step
                    # (so a "yes" at slot confirmation can't trip it), and only
                    # when a calling number actually exists (else: no change).
                    if (
                        not self.session.get("v3_phone_dtmf_active")
                        and not self.session.get("v3_awaiting_phone_confirm")
                        and self.session.get("booking_flow_active")
                        and _is_use_this_number(utterance)
                    ):
                        _bk_caller_num = self.session.get("twilio_from_local", "")
                        _bk_lastq = (
                            self.session.get("last_question", "")
                            or self.session.get("last_bot_prompt", "")
                            or ""
                        ).lower()
                        _bk_phone_step = (
                            "use this number" in _bk_lastq
                            or "number you're calling on" in _bk_lastq
                            or "number you booked" in _bk_lastq
                        )
                        if _bk_caller_num and _bk_phone_step:
                            self.session.setdefault("collected", {})
                            self.session["collected"]["phone"] = _bk_caller_num
                            self.session["phone_confirmed"] = True
                            await save_session(self.call_sid, self.session)
                            logger.info(
                                "[ms_conn v3] booking verbal phone confirm — "
                                "stored calling number %s + phone_confirmed=True; "
                                "LLM will produce booking readback: %r",
                                _bk_caller_num, utterance[:60],
                            )
                            # Fall through to run_turn — phone now in CALL STATE.

                    # A2: verbal reset + DTMF mode management (Spec R).
                    # Intercept BEFORE _llm_busy is set.
                    if self.session.get("v3_phone_dtmf_active"):
                        _dtmf_buf  = self.session.get("phone_dtmf_buffer", "")
                        _utt_lower = utterance.lower()

                        # ── Change 2: name-correction exclusion ───────────────
                        # If the patient is correcting their name, exit DTMF
                        # mode and pass the utterance to the LLM regardless of
                        # buffer state.  A name correction is never a number
                        # reset.
                        if _is_name_correction(utterance):
                            logger.info(
                                "[ms_conn] verbal reset skipped — "
                                "name correction detected: %r",
                                utterance[:60],
                            )
                            logger.info(
                                "[ms_conn] name correction during DTMF — "
                                "exiting DTMF mode, passing to LLM: %r",
                                utterance[:60],
                            )
                            self.session["v3_phone_dtmf_active"] = False
                            self.session["phone_dtmf_buffer"] = ""
                            logger.info(
                                "[ms_conn] v3_phone_dtmf_active = False"
                                " (exited — name correction)"
                            )
                            # Fall through to normal LLM dispatch.

                        elif _dtmf_buf:
                            # ── Buffer has digits ─────────────────────────────
                            # Change 1 satisfied — verbal reset may fire.
                            _reset_words = {
                                "reset", "clear", "start over", "start again",
                                "wrong", "mistake", "again", "restart",
                            }
                            if any(w in _utt_lower for w in _reset_words):
                                self.session["phone_dtmf_buffer"] = ""
                                _verbal_reset_msg = (
                                    "Buffer cleared — please type your"
                                    " number again."
                                )
                                await self.tts_text_queue.put(_verbal_reset_msg)
                                self.session["last_bot_prompt"] = _verbal_reset_msg
                                self.session["last_question"]   = _verbal_reset_msg
                                await save_session(self.call_sid, self.session)
                                logger.info(
                                    "[ms_conn v3] verbal reset — DTMF"
                                    " buffer cleared: %r",
                                    utterance[:40],
                                )
                                continue
                            # Buffer non-empty, no reset word → suppress.
                            logger.info(
                                "[ms_conn] transcript suppressed — "
                                "phone DTMF active: %r",
                                utterance[:60],
                            )
                            continue

                        else:
                            # ── Buffer empty (Changes 1 + 3) ─────────────────
                            # Verbal reset must never fire on an empty buffer.
                            # If the speech is conversational (>4 words, no
                            # digit run) exit DTMF mode; otherwise also exit —
                            # the patient is clearly not typing a number.

                            # ── Verbal "use this number" intercept ───────────
                            # The caller can confirm the calling number by voice
                            # ("use this number") instead of typing.  The LLM is
                            # unreliable here — it has re-run check_availability
                            # instead of storing the phone (see 18:47 bug log).
                            # Store the calling number programmatically so that
                            # _phone_already_known flips in the system prompt and
                            # the POST-PHONE CONFIRMATION guard drives the LLM
                            # straight to the booking readback.  Only fires when
                            # a calling number is actually present.
                            _caller_num = self.session.get("twilio_from_local", "")
                            if _caller_num and _is_use_this_number(utterance):
                                self.session.setdefault("collected", {})
                                self.session["collected"]["phone"] = _caller_num
                                # phone_confirmed=True is REQUIRED: _get_confirmed_phone
                                # (smart_sms_router / actionable_summary) only returns
                                # collected["phone"] when phone_confirmed is True.  Without
                                # it the stored number is invisible to the SMS router →
                                # phone=no → no confirmation SMS even though the caller
                                # confirmed the number (2026-06-23 bug).
                                self.session["phone_confirmed"] = True
                                self.session["v3_phone_dtmf_active"] = False
                                self.session["phone_dtmf_buffer"] = ""
                                await save_session(self.call_sid, self.session)
                                logger.info(
                                    "[ms_conn v3] verbal phone confirm — stored"
                                    " calling number %s + phone_confirmed=True and"
                                    " exited DTMF; LLM will produce booking readback: %r",
                                    _caller_num, utterance[:60],
                                )
                                # Fall through to run_turn — phone is now in
                                # CALL STATE, so the LLM proceeds to the summary.
                            else:
                                logger.info(
                                    "[ms_conn] verbal reset skipped — "
                                    "buffer empty: %r",
                                    utterance[:60],
                                )
                                if _is_conversational_during_dtmf(utterance):
                                    logger.info(
                                        "[ms_conn] conversational speech in empty"
                                        " DTMF mode — exiting: %r",
                                        utterance[:60],
                                    )
                                self.session["v3_phone_dtmf_active"] = False
                                logger.info(
                                    "[ms_conn] v3_phone_dtmf_active = False"
                                    " (exited — conversational speech /"
                                    " name correction)"
                                )
                            # Fall through to normal LLM dispatch.

                    # ── CHANGE B: Name collection debounce ───────────────────
                    # STT often splits "my name is [name]" into two finals that
                    # arrive ~700 ms apart.  If the first final is just a prefix
                    # pattern with no actual name, park it and wait 800 ms for
                    # the real name to arrive.  If it does, cancel the re-ask.
                    # If it doesn't, _fire_name_reask plays a gentle re-prompt.
                    _is_name_collection = (
                        "name" in self.session.get(
                            "last_question", ""
                        ).lower()
                    )
                    if _is_name_collection:
                        _utt_stripped = utterance.strip().lower()
                        _INCOMPLETE_PATTERNS = (
                            "my name is",
                            "my name's",
                            "it's",
                            "it is",
                            "this is",
                            "i'm",
                            "its",
                        )
                        _is_incomplete = any(
                            _utt_stripped == p or _utt_stripped.endswith(p)
                            for p in _INCOMPLETE_PATTERNS
                        )
                        if _is_incomplete:
                            # Incomplete prefix — park and wait for full name.
                            self._name_collection_pending = True
                            if self._name_collection_timer:
                                self._name_collection_timer.cancel()
                            self._name_collection_timer = asyncio.create_task(
                                self._fire_name_reask()
                            )
                            logger.info(
                                "[ms_conn v3] incomplete name utterance — "
                                "waiting 800ms: %r",
                                utterance,
                            )
                            continue
                        elif self._name_collection_pending:
                            # Follow-up arrived within 800 ms — cancel re-ask.
                            self._name_collection_pending = False
                            self._name_clarification_queued = False
                            self._clarification_in_flight = False
                            if self._name_collection_timer:
                                self._name_collection_timer.cancel()
                                self._name_collection_timer = None
                            logger.info(
                                "[ms_conn v3] name follow-up received — "
                                "timer cancelled, passing to run_turn: %r",
                                utterance,
                            )
                            # fall through — process normally

                        elif self._name_clarification_queued:
                            # The 800ms re-ask timer already fired and put the
                            # clarification phrase on tts_text_queue, but the
                            # name has now arrived anyway.  Drain the phrase from
                            # the queue before TTS synthesis can consume it, then
                            # process this transcript normally so the caller only
                            # hears "Thanks [name]" — not both "Sorry, I didn't
                            # catch your name" AND "Thanks [name]" back-to-back.
                            _clarification_phrase = (
                                "Sorry, I didn't quite catch your name — "
                                "could you say it again?"
                            )
                            _held_items: list = []
                            _drained = 0
                            try:
                                while True:
                                    _qi = self.tts_text_queue.get_nowait()
                                    if _qi == _clarification_phrase:
                                        _drained += 1
                                    else:
                                        _held_items.append(_qi)
                            except asyncio.QueueEmpty:
                                pass
                            for _qi in _held_items:
                                self.tts_text_queue.put_nowait(_qi)
                            self._name_clarification_queued = False
                            self._clarification_in_flight = False
                            if _drained:
                                logger.info(
                                    "[ms_conn] clarification cancelled — "
                                    "superseded by transcript: %r",
                                    utterance[:60],
                                )
                            # fall through — process normally

                    self._in_barge_in_recovery = False
                    self.llm_in_flight = True   # Spec N: set before any gate/LLM work
                    self._llm_busy = True
                    self._silence_handler.on_llm_started()
                    self._last_audio_at = time.monotonic()
                    self.session["llm_generation_active"] = True
                    self.session["tts_inhibit"] = False
                    # Bug A — reset the TTS dedup baseline at the start of every
                    # v3 caller turn.  The legacy flow path enqueues this sentinel
                    # before handle_transcript, but the v3 path never did, so the
                    # consecutive-duplicate guard persisted ACROSS turns: a
                    # legitimate re-ask that repeats the previous question verbatim
                    # (caller gives an unusable day answer → LLM re-asks "Is there
                    # a particular day or time?") was dropped → ~10s dead air until
                    # the watchdog fired (18:30:59→18:31:10 Redditch call).
                    # Resetting per turn lets the repeat play immediately;
                    # within-turn dedup still works (sentinel precedes this turn's
                    # chunks in the FIFO queue).
                    await self.tts_text_queue.put("\x00DEDUP_RESET\x00")
                    # ── Slot-selection day-alias normalisation ────────────────
                    # Apply STT mishearing correction BEFORE the pop so the flag
                    # is still True here.  Rewrites e.g. "first year" → "thursday"
                    # only within the slot-choice window; harmless elsewhere.
                    if self.session.get("v3_awaiting_slot_selection"):
                        utterance = _apply_slot_day_aliases(utterance)
                    # ── Spec H + J: fragment / post-confirmation guard ────────
                    # If we're in the slot-selection window but the transcript
                    # has no slot-signal word, either:
                    #   (J) let it through as a confirmation if
                    #       post_slot_confirmation_pending is True and the phrase
                    #       is a known confirming response, OR
                    #   (H) re-arm the silence timer and discard.
                    if self.session.get("v3_awaiting_slot_selection") and not _is_slot_selection_candidate(utterance):
                        if (
                            self.post_slot_confirmation_pending
                            and _is_post_slot_confirmation(utterance)
                        ):
                            # Patient confirmed the offered slot (e.g. "yes",
                            # "sounds good", "that works best") — advance to
                            # name-collection turn without re-asking for a slot.
                            logger.info(
                                "[ms_conn] post-slot confirmation %r — bypassing slot guard, advancing to name flow",
                                utterance,
                            )
                            self.post_slot_confirmation_pending = False
                            # Fall through to normal LLM dispatch below.
                        elif (
                            self.slot_map_stage in (
                                SlotMapStage.DAY_SELECTION,
                                SlotMapStage.TIME_SELECTION,
                            )
                            and _is_non_specific_slot_affirmation(utterance)
                        ):
                            # CODE SPEC AJ — patient confirmed something works
                            # but didn't say WHICH option (day or time). Ask them
                            # to specify using the actual labels from the active
                            # slot map.  Bug B: previously gated to DAY_SELECTION
                            # only, so a non-specific affirmation during
                            # TIME_SELECTION ("works for me" with two times
                            # offered) fell through to _is_short_meaningless_
                            # fragment and was SILENTLY dropped → ~18s dead air
                            # (Redditch call 18:31:34). Covering TIME_SELECTION
                            # too re-asks which option instead of going silent.
                            _aj_map = self.session.get("v3_dtmf_slot_map", {})
                            # Bug B: list EVERY offered slot, not just the first
                            # three — dropping options left a caller unable to
                            # pick the 4th/5th time.
                            _clarify = _build_slot_clarify(list(_aj_map.values()))
                            logger.info(
                                "[ms_conn] non-specific slot affirmation %r"
                                " — asking to clarify: %r",
                                utterance, _clarify,
                            )
                            await self.tts_text_queue.put(_clarify)
                            self.session["last_bot_prompt"] = _clarify
                            self.session["last_question"] = _clarify
                            self.session.setdefault(
                                "conversation_history", []
                            ).append({
                                "role": "assistant",
                                "content": _clarify,
                            })
                            if self._silence_handler is not None:
                                self._silence_handler.on_question_asked(
                                    _clarify
                                )
                            self.llm_in_flight = False
                            self._llm_busy = False
                            self.session["llm_generation_active"] = False
                            continue
                        elif _is_short_meaningless_fragment(utterance):
                            # No slot signal AND the fragment is too short /
                            # carries no communicative word — safe to re-arm.
                            # Examples: "with me", "actually".
                            logger.info(
                                "[ms_conn] slot fragment ignored — re-arming: %r",
                                utterance,
                            )
                            _last_q = self.session.get("last_question", "")
                            if _last_q:
                                self._silence_handler.set_state(self.session.get("state", "default"))
                                self._silence_handler.on_question_asked(_last_q)
                            self.llm_in_flight = False  # Spec N: no LLM call fired
                            self._llm_busy = False
                            self.session["llm_generation_active"] = False
                            # on_llm_started() was called above (line 4818) before
                            # gates ran.  Since no LLM was actually launched, we must
                            # mirror it with on_llm_finished() so the watchdog's
                            # internal _llm_busy is cleared.  Without this, the
                            # deferred timer set by on_question_asked() above never
                            # fires → 10-15s dead air until the safety net kicks in.
                            self._silence_handler.on_llm_finished()
                            continue
                        else:
                            # No slot signal BUT the utterance is meaningful
                            # (4+ words OR contains a communicative word such as
                            # 'no', 'none', 'not', 'want', 'how', etc.).
                            # Pass to LLM — the patient is expressing intent.
                            # Examples: "no none of those suit me",
                            #           "that one please", "not really".
                            #
                            # Guard: if the new transcript is an open-
                            # availability / no-preference phrase AND slots
                            # were already presented this turn, suppress the
                            # LLM call.  The slots already presented are the
                            # correct answer — re-running check_availability
                            # would produce a duplicate slot list.
                            # Exempt rejections / alternative requests: "no,
                            # anything else?" / "any others?" / "a different
                            # day" REJECT the presented slots and ask for new
                            # ones — the opposite of a redundant repeat.  These
                            # must reach the LLM (re-fetch another day/week) and
                            # fall through below so the slot window is popped;
                            # suppressing them traps the caller (the suppression
                            # path never clears v3_awaiting_slot_selection), which
                            # caused repeated silence → hang-up (2026-06-15).
                            # Exempt genuine new questions / FAQs.  An utterance
                            # like "do you offer sports massages by any chance?"
                            # contains an availability signal substring but is a
                            # NEW question, not a no-preference continuation.
                            # Suppressing it traps the caller in a re-ask loop
                            # (observed 2026-06-18: asked 3x, never answered →
                            # abandoned).  Such utterances must reach the LLM.
                            _ut_low = utterance.lower()
                            _looks_like_new_question = (
                                "?" in utterance
                                or any(
                                    k in _ut_low for k in (
                                        "offer", "do you", "are you", "have you",
                                        "what", "how much", "how do", "how long",
                                        "can i", "could i", "is there", "price",
                                        "cost", "question",
                                    )
                                )
                            )
                            if (
                                _is_open_availability_utterance(utterance)
                                and not _is_slot_rejection_or_alternative(utterance)
                                and not _looks_like_new_question
                            ):
                                logger.info(
                                    "[ms_conn v3] open-availability continuation"
                                    " suppressed — slots already presented"
                                    " this turn: %r",
                                    utterance,
                                )
                                self.llm_in_flight = False
                                self._llm_busy = False
                                self.session["llm_generation_active"] = False
                                # Mirror on_llm_started() with on_llm_finished()
                                # so the watchdog's internal _llm_busy is cleared.
                                self._silence_handler.on_llm_finished()
                                continue
                            logger.info(
                                "[ms_conn] non-slot utterance during slot selection"
                                " — passing to LLM: %r",
                                utterance,
                            )
                            # Fall through to normal LLM dispatch below
                            # (do NOT continue — slot window stays open until
                            # pop() below clears it after LLM fires).
                    # Caller is responding — slot selection window has closed.
                    self.session.pop("v3_awaiting_slot_selection", None)
                    await save_session(self.call_sid, self.session)

                    logger.info(
                        "[ms_conn v3] transcript: %r", utterance[:120],
                    )

                    try:
                        # ── THEOREM_V3 LOCATION GATE (FIX 1) ────────────────
                        # If booking intent was flagged but the location
                        # question has not been asked yet, queue it directly
                        # and skip run_turn entirely.  Prevents the deadlock
                        # where both sides wait after a pure acknowledgement
                        # turn (Susie said ack, caller waits for question,
                        # LLM waits for transcript — nobody moves).
                        # ── Helper: extract location from caller utterance ──
                        def _v3_extract_location(utt: str) -> str:
                            """Return 'alcester', 'redditch', or ''."""
                            # Normalise: lowercase, strip punctuation, collapse spaces.
                            # This means "al-chester", "al chester", "alchester"
                            # all resolve identically before alias matching.
                            _n = _normalise_location_text(utt)

                            # ── Alias substring scan ─────────────────────────
                            # Module-level _ALCESTER_ALIASES / _REDDITCH_ALIASES
                            # cover 60+ phonetic variants, STT artefacts, and
                            # confirmed call-log transcripts.  Substring (not
                            # exact) so "I'd like awlster please" hits "awlster"
                            # without a dedicated entry.
                            if any(a in _n for a in _ALCESTER_ALIASES):
                                return "alcester"
                            if any(a in _n for a in _REDDITCH_ALIASES):
                                return "redditch"

                            # ── Ordinal / number variants ────────────────────
                            # Word-tuple exact-match for standalone ordinals
                            # ("one", "first", "two", "second", etc.).
                            # Kept separate from aliases because "one" as a
                            # substring would false-positive on "only", "money"
                            # etc. if used in a substring scan.
                            words = tuple(_n.split())
                            _alcester_ordinals = {
                                ("one",),
                                ("first",),
                                ("the", "first"),
                                ("first", "one"),
                                ("the", "first", "one"),
                                ("number", "one"),
                                ("option", "one"),
                                ("one", "please"),
                                ("first", "option"),
                                ("first", "one", "please"),
                                ("number", "one", "please"),
                                ("option", "one", "please"),
                                ("first", "option", "please"),
                            }
                            _redditch_ordinals = {
                                ("two",),
                                ("second",),
                                ("the", "second"),
                                ("second", "one"),
                                ("the", "second", "one"),
                                ("number", "two"),
                                ("option", "two"),
                                ("two", "please"),
                                ("second", "option"),
                                ("second", "one", "please"),
                                ("number", "two", "please"),
                                ("option", "two", "please"),
                                ("second", "option", "please"),
                            }
                            if words in _alcester_ordinals:
                                return "alcester"
                            if words in _redditch_ordinals:
                                return "redditch"
                            return ""

                        # ── Noise-fragment filter (SPEC 3 / Bug 5) ──────────
                        # Applied to every FINAL transcript before the LLM.
                        # Only single-word transcripts are evaluated — multi-
                        # word transcripts always pass through immediately.
                        #
                        # Condition 1 — TOO SHORT: single word ≤ 3 chars.
                        # Condition 2a — NO VOWELS: single word whose alpha
                        #   chars contain no vowel (e.g. "ng", "rch", "sht").
                        # Condition 2b — ALL VOWELS: ≤ 4 alpha chars, all
                        #   vowels (e.g. "oo", "ah", "ee" — mouth noise).
                        # Condition 3 — NOISE LIST: exact match in
                        #   _V3_NOISE_FRAGMENTS regardless of length.
                        # Condition 4 — RAPID CONTINUATION: single word
                        #   arriving within _V3_RAPID_ARRIVAL_SEC of the
                        #   previous ACCEPTED transcript (enqueue timestamp).
                        #   Instead of discarding, the fragment is MERGED with
                        #   the previous transcript and re-evaluated as a
                        #   combined utterance (which is multi-word and
                        #   therefore always passes Conditions 1-3).
                        #
                        # _V3_PRESERVE words always bypass all conditions.
                        # Log format: [ms_stt] fragment discarded (reason=…)
                        _stripped = utterance.strip().lower()
                        _utt_words = _stripped.split()
                        _is_single_word = len(_utt_words) == 1

                        # Condition 1 extension — 2-word fragments with a
                        # single-character word (e.g. "r clinic", "a there").
                        # A single letter/digit among two words is hallmark STT
                        # noise; discard before the location resolver and DTMF
                        # fallback.  Uses the same re-arm pattern as single-word
                        # drops so silence recovery still fires after the discard.
                        # Exemption: single-char words in _SINGLE_CHAR_PRONOUNS
                        # ('i', 'a') are valid English and must not trigger this
                        # discard — e.g. "i believe", "a moment" must pass through.
                        _non_pronoun_singles = [
                            w for w in _utt_words
                            if len(w) == 1 and w not in _SINGLE_CHAR_PRONOUNS
                        ]
                        if (
                            len(_utt_words) == 2
                            and _non_pronoun_singles
                            and _stripped not in _V3_PRESERVE
                        ):
                            logger.info(
                                "[ms_stt] fragment discarded "
                                "(reason=single_char_word): %r",
                                utterance.strip(),
                            )
                            _last_q = self.session.get("last_question", "")
                            if _last_q:
                                self._silence_handler.set_state(
                                    self.session.get("state", "default")
                                )
                                self._silence_handler.on_question_asked(_last_q)
                            continue

                        # Multi-word transcripts are NEVER filtered (beyond the
                        # single-char-word extension above).
                        if _is_single_word and _stripped not in _V3_PRESERVE:
                            _filter_reason: str = ""
                            _alpha_only = "".join(
                                c for c in _stripped if c.isalpha()
                            )
                            _gap_sec = (
                                _enqueue_ts - self._v3_last_processed_ts
                            )
                            # Condition 4 evaluated first — merges rather than
                            # discards, so it has priority over the drop path.
                            if _gap_sec < _V3_RAPID_ARRIVAL_SEC and self._v3_last_transcript_text:
                                _merged = (
                                    self._v3_last_transcript_text.strip()
                                    + " "
                                    + utterance.strip()
                                )
                                logger.info(
                                    "[ms_stt] rapid-continuation fragment "
                                    "merged (gap=%.3fs): %r + %r → %r",
                                    _gap_sec,
                                    self._v3_last_transcript_text,
                                    utterance.strip(),
                                    _merged,
                                )
                                utterance = _merged
                                _stripped = utterance.strip().lower()
                                # Merged utterance is multi-word — skip
                                # remaining noise conditions and continue
                                # normal processing below.
                            else:
                                # Conditions 1-3: drop path.
                                if len(_stripped) <= 3:
                                    _filter_reason = "too-short"
                                elif _alpha_only and not any(
                                    c in _V3_VOWELS for c in _alpha_only
                                ):
                                    _filter_reason = "no-vowels"
                                elif _alpha_only and len(_stripped) <= 4 and all(
                                    c in _V3_VOWELS for c in _alpha_only
                                ):
                                    _filter_reason = "all-vowels"
                                elif _stripped in _V3_NOISE_FRAGMENTS:
                                    _filter_reason = "noise-list"

                                if _filter_reason:
                                    logger.info(
                                        "[ms_stt] fragment discarded "
                                        "(reason=%s): %r",
                                        _filter_reason,
                                        utterance.strip(),
                                    )
                                    # Re-arm watchdog so silence recovery
                                    # still fires after a discarded fragment.
                                    _last_q = self.session.get(
                                        "last_question", ""
                                    )
                                    if _last_q:
                                        self._silence_handler.set_state(
                                            self.session.get(
                                                "state", "default"
                                            )
                                        )
                                        self._silence_handler.on_question_asked(
                                            _last_q
                                        )
                                    continue

                        # ── CODE SPEC G: non-scheduling single-word re-arm ───
                        # A single word that survived all noise conditions above
                        # but carries no scheduling intent (not a yes/no, day,
                        # time of day, number, or presence signal) should extend
                        # the silence window rather than dispatching a wasted
                        # LLM call.  The caller may be mid-sentence.
                        # Guard: len(_stripped.split()) == 1 excludes merged
                        # utterances produced by rapid-continuation (Cond 4).
                        # Phone-number exemption: any single token of 5+ digits
                        # (full number or spoken fragment) must always pass
                        # through — never re-arm.
                        # ── BUG-1 fix: answer-expected context exemption ─────
                        # A single word that is the EXPECTED ANSWER must not be
                        # dropped as noise.  Two contexts: (a) the name step
                        # (caller answers "James"/"Quentin"); (b) a yes/no confirm
                        # (caller answers "please"/"yeah").  Real STT garbage was
                        # already removed by the too-short / no-vowel / all-vowel
                        # / noise-list filters above, so a single word surviving
                        # to here is a plausible answer.  Keyed on the pending
                        # question text + post_slot flag, so the noise re-arm is
                        # unchanged in every OTHER context (zero regression to the
                        # mid-sentence-noise suppression this guard exists for).
                        # See [[susie-8call-sweep]] BUG-1 (dropped on Calls 1,2,4,6).
                        _lq_ctx = (
                            (self.session.get("last_question") or "")
                            + " "
                            + (self.session.get("last_bot_prompt") or "")
                        ).lower()
                        _name_step = (
                            bool(self.session.get("post_slot_confirmation_pending"))
                            or "your name" in _lq_ctx
                            or "first name" in _lq_ctx
                        )
                        _yesno_step = any(
                            _m in _lq_ctx
                            for _m in (
                                "shall i", "would you", "did you say",
                                "is that right", "is that correct",
                                "use this", "could i get", "can i take",
                            )
                        )
                        _answer_expected = _name_step or _yesno_step

                        if (
                            _is_single_word
                            and len(_stripped.split()) == 1
                            and _stripped not in _SCHEDULING_SINGLES
                        ):
                            if _PHONE_NUMBER_RE.match(_stripped):
                                logger.info(
                                    "[ms_stt] phone number/fragment %r — "
                                    "passing to LLM (phone exemption)",
                                    _stripped,
                                )
                                # Fall through to LLM dispatch below.
                            elif _answer_expected:
                                logger.info(
                                    "[ms_stt] single word %r accepted — "
                                    "answer-expected context (name=%s yesno=%s)",
                                    _stripped, _name_step, _yesno_step,
                                )
                                # Fall through to LLM dispatch below.
                            else:
                                logger.info(
                                    "[ms_stt] non-scheduling single word %r — "
                                    "silence timer re-armed",
                                    _stripped,
                                )
                                _last_q = self.session.get("last_question", "")
                                if _last_q:
                                    self._silence_handler.set_state(
                                        self.session.get("state", "default")
                                    )
                                    self._silence_handler.on_question_asked(_last_q)
                                continue

                        # ── Reschedule/cancel phone confirm ──────────────────
                        # Fires when the caller responds to the phone-first
                        # question queued after location is confirmed for
                        # reschedule/cancel intents.
                        if self.session.get("v3_awaiting_phone_confirm"):
                            self.session["v3_awaiting_phone_confirm"] = False
                            _utt_lower = utterance.lower()
                            _calling_number = self.session.get(
                                "twilio_from_local", ""
                            )
                            if (
                                "use this" in _utt_lower
                                or "yes" in _utt_lower
                                or "yeah" in _utt_lower
                                or "yep" in _utt_lower
                                or "yup" in _utt_lower
                            ):
                                # Caller confirmed → use calling number
                                self.session["lookup_phone"] = _calling_number
                                _filler = _random.choice(FILLER_PHRASES)
                                await self.tts_text_queue.put(_filler)
                                self.session["last_bot_prompt"] = _filler
                                await save_session(
                                    self.call_sid, self.session
                                )
                                logger.info(
                                    "[ms_conn v3] phone confirm — using"
                                    " calling number for lookup: %s",
                                    _calling_number,
                                )
                                # Fall through — gate/loc checks will be False,
                                # run_turn will fire and LLM calls lookup_appointment
                            else:
                                # Caller wants a different number — switch
                                # to DTMF keypad collection.
                                # Disarm any live slot DTMF handler BEFORE
                                # the prompt is played so the first keypad
                                # digit goes to phone collection, not the
                                # slot map handler.
                                _dtmf_prompt = (
                                    "No problem — please type the number "
                                    "on your keypad now. You can press the "
                                    "star key to reset at any time."
                                )
                                await self.tts_text_queue.put(_dtmf_prompt)
                                self.session[
                                    "last_bot_prompt"
                                ] = _dtmf_prompt
                                self.session[
                                    "last_question"
                                ] = _dtmf_prompt
                                # Write the keypad prompt into conversation_history
                                # so the LLM has context when DTMF digits arrive.
                                # Without this the history has two consecutive user
                                # turns ("no it was a different number" then the
                                # phone digits) with no assistant bridge, causing
                                # the LLM to re-ask for the number instead of
                                # calling lookup_patient.
                                self.session.setdefault(
                                    "conversation_history", []
                                ).append({
                                    "role": "assistant",
                                    "content": _dtmf_prompt,
                                })
                                logger.info(
                                    "[ms_conn v3] keypad prompt written to"
                                    " conversation_history for DTMF bridge"
                                )
                                self.session.pop("v3_dtmf_slot_map",           None)
                                self.session.pop("v3_slot_dtmf_active",        None)
                                self.session.pop("v3_awaiting_slot_selection", None)
                                self.session["v3_phone_dtmf_active"] = True
                                await save_session(
                                    self.call_sid, self.session
                                )
                                logger.info(
                                    "[ms_conn] slot DTMF disarmed → "
                                    "phone DTMF activated (pre-emptive)"
                                )
                                continue  # Skip run_turn; wait for digits

                        # ── Spec X: echo suppression — Priority 1 ────────────
                        # When v3_location_asked is active the phone line often
                        # echoes Susie's own TTS back through the mic.  Short
                        # transcripts (≤2 words) with no meaningful
                        # location/response token must be discarded BEFORE any
                        # handler fires.  The same check runs in
                        # _on_final_transcript_clear to suppress the silence
                        # timer; this check ensures the location interception
                        # block never sees an echo candidate.
                        #
                        # Timestamp guard: real echoes arrive within ~500 ms of
                        # TTS finishing; 1.5 s gives comfortable headroom.  A
                        # transcript arriving more than 1.5 s after the last TTS
                        # completed is real caller speech and must pass through.
                        _ECHO_SUPPRESS_WINDOW_S = 1.5
                        if (
                            self.session.get("v3_location_asked", False)
                            # Bypass echo suppression when the biased confirm is
                            # active ("Did you say the Awlstuh clinic?").  The
                            # caller's "yes I did" / "I did" is 2 words and
                            # contains no _SX_LOC_PASS token, so it would be
                            # silently dropped — preventing Alcester resolution.
                            and not self.session.get(
                                "v3_awaiting_use_this_clinic", False
                            )
                        ):
                            _sx_words = utterance.strip().lower().split()
                            # Build the pass set from the canonical alias sets so
                            # it never drifts out of sync.  Single-word aliases are
                            # enough: the check is word-level (any word in the
                            # utterance matches a pass token).  Confirmation tokens
                            # (yes/no/yep…) and ordinals are added manually.
                            _SX_LOC_PASS = (
                                frozenset(
                                    a for a in (
                                        _ALCESTER_ALIASES | _REDDITCH_ALIASES
                                    )
                                    if " " not in a
                                )
                                | {
                                    "yes", "no", "yeah", "nope", "yep",
                                    "yup", "nah", "use", "this",
                                    "one", "two", "first", "second",
                                }
                            )
                            _sx_in_window = (
                                self._tts_audio_done_at > 0
                                and (time.monotonic() - self._tts_audio_done_at)
                                    < _ECHO_SUPPRESS_WINDOW_S
                            )
                            if (
                                _sx_in_window
                                and 1 <= len(_sx_words) <= 2
                                and not any(w in _SX_LOC_PASS for w in _sx_words)
                            ):
                                logger.info(
                                    "[ms_conn v3] TTS-echo suppressed: %r"
                                    " (%d word(s), %.2fs after TTS done)"
                                    " — skipping all handlers",
                                    utterance, len(_sx_words),
                                    time.monotonic() - self._tts_audio_done_at,
                                )
                                continue
                        # ── end Spec X ────────────────────────────────────────

                        # CODE SPEC AH: mark that the patient has spoken at
                        # least once while the location question was active.
                        # Used by the watchdog to switch to the fast 3.5s grace
                        # (first-response grace is 9s for shy callers).
                        if self.session.get("v3_location_q_active"):
                            self.session["_location_q_patient_spoke"] = True

                        _v3_gate_fired = (
                            self.session.get("v3_booking_intent", False)
                            and not self.session.get(
                                "v3_location_asked", False
                            )
                            and not self.session.get(
                                "v3_location_confirmed", False
                            )
                        )
                        # Caller is answering the location question we just
                        # asked — intercept to guarantee only the ack plays
                        # (no bundled next question from the LLM).
                        _v3_loc_answering = self.session.get(
                            "v3_location_asked", False
                        )

                        if _v3_gate_fired:
                            _gate_intent = self.session.get(
                                "v3_caller_intent", "booking"
                            )
                            if _gate_intent in ("reschedule", "cancel"):
                                _loc_q = (
                                    "Was your original appointment at "
                                    "our Awlstuh or Redditch clinic?"
                                )
                            else:
                                _loc_q = _LOC_RUNG1_OPEN
                            await self.tts_text_queue.put(_loc_q)
                            self.session["last_bot_prompt"] = _loc_q
                            self.session["last_question"] = _loc_q
                            self.session["v3_location_asked"] = True
                            self.session["v3_location_q_active"] = True
                            self.session["_location_q_patient_spoke"] = False
                            await save_session(self.call_sid, self.session)
                            logger.info(
                                "[ms_conn v3] location gate fired — "
                                "intent=%s, skipping run_turn for: %r",
                                _gate_intent,
                                utterance[:60],
                            )

                        elif _v3_loc_answering:
                            # ── LOCATION ANSWER INTERCEPT ─────────────────
                            # 0. FAQ / question detection (must be first)
                            # 1. Intent pivot detection
                            # 2. use-this-clinic confirm handler
                            # 3. code-gate alias matching
                            # 4. Haiku resolver for anything the gate misses

                            # ── FAQ / question detection ──────────────────
                            # If the caller is asking a question rather than
                            # answering the location prompt, route to run_turn
                            # so the LLM can answer it.  The location question
                            # stays pending (v3_location_asked remains True)
                            # and will be re-asked after the LLM responds.
                            _FAQ_SIGNALS = {
                                "where are you", "where is", "where's",
                                "what is", "what's", "how do", "how much",
                                "how far", "do you", "are you", "is there",
                                "is it", "can you", "could you", "would you",
                                "have you", "did you", "when do", "when are",
                                "what time", "how long", "how many",
                                "what address", "what's the address",
                            }
                            _utt_lower_faq = utterance.lower().strip()
                            _is_faq = any(
                                _utt_lower_faq.startswith(sig)
                                or sig in _utt_lower_faq
                                for sig in _FAQ_SIGNALS
                            )
                            if _is_faq:
                                logger.info(
                                    "[ms_conn v3] FAQ detected in loc gate "
                                    "— routing to run_turn: %r",
                                    utterance[:60],
                                )
                                # Do NOT clear v3_location_asked — the
                                # location question remains pending and will
                                # be re-asked after the LLM responds.
                                if self._current_llm_task and not self._current_llm_task.done():
                                    logger.warning(
                                        "[ms_conn v3] stale LLM task at FAQ "
                                        "path — cancelling, new transcript wins"
                                    )
                                    self._current_llm_task.cancel()
                                    try:
                                        await self._current_llm_task
                                    except asyncio.CancelledError:
                                        pass
                                    self._current_llm_task = None
                                    # Flush any TTS the cancelled task had
                                    # already queued — prevents stale audio
                                    # playing over the new turn's response.
                                    _faq_flushed = 0
                                    try:
                                        while True:
                                            self.tts_text_queue.get_nowait()
                                            _faq_flushed += 1
                                    except asyncio.QueueEmpty:
                                        pass
                                    logger.info(
                                        "[ms_llm] task cancelled and TTS "
                                        "flushed: %d items (FAQ path)",
                                        _faq_flushed,
                                    )
                                # Capture last_question before run_turn for
                                # name-persistence check below (Bug 7).
                                _faq_pre_q = self.session.get(
                                    "last_question", ""
                                )
                                # ── Spec I: turn-level slot cache clear ──────
                                # New patient turn starting — invalidate any
                                # slots offered in the previous turn so the next
                                # check_availability call hits Acuity fresh.
                                # (Mid-turn re-query blocking is handled inside
                                # llm_stream.py and is NOT affected by this.)
                                if self.session.get("last_offered_slots") is not None:
                                    _prev_hint = self.session.get("last_date_hint")
                                    _prev_slots = self.session.get("last_offered_slots") or []
                                    logger.info(
                                        "[ms_llm] slot cache cleared on new turn"
                                        " (was date_hint=%r) [FAQ path]",
                                        _prev_hint,
                                    )
                                    self.session["last_offered_slots"] = None
                                    self.session["last_date_hint"] = None
                                    # Prefer the specific day from offered slots
                                    # over the broader week hint (e.g. save
                                    # "2026-06-23" not "week of 22 June 2026").
                                    # This ensures CALL STATE re-prompts the
                                    # exact day, not the whole week.
                                    _offered_day_iso = None
                                    if _prev_slots:
                                        try:
                                            _offered_day_iso = (
                                                _prev_slots[0]["start"][:10]
                                            )
                                        except (IndexError, KeyError, TypeError):
                                            pass
                                    if _offered_day_iso:
                                        self.session[
                                            "v3_last_presented_date_hint"
                                        ] = _offered_day_iso
                                        self.session[
                                            "v3_last_offered_day_iso"
                                        ] = _offered_day_iso
                                        logger.info(
                                            "[ms_llm] slot cache cleared [FAQ]:"
                                            " day iso=%r from offered slots",
                                            _offered_day_iso,
                                        )
                                    elif _prev_hint:
                                        self.session[
                                            "v3_last_presented_date_hint"
                                        ] = _prev_hint
                                        logger.info(
                                            "[ms_llm] slot cache cleared [FAQ]:"
                                            " date hint=%r preserved",
                                            _prev_hint,
                                        )
                                # Change B: arm filler before LLM call.
                                # No-op here (non-v3 path, booking_flow_active absent).
                                self._filler_breath_injected = False
                                await self._filler.arm(self.session)
                                self._current_llm_task = asyncio.create_task(
                                    llm.run_turn(
                                        user_text=utterance,
                                        session=self.session,
                                        call_sid=self.call_sid,
                                        stream_sid=self.stream_sid,
                                        tts_text_queue=self.tts_text_queue,
                                        audio_out_queue=self.audio_out_queue,
                                        websocket=self.websocket,
                                        on_transfer=self._on_transfer_request,
                                    ),
                                    name="ms_llm_turn",
                                )
                                _faq_run_cancelled = False
                                try:
                                    await self._current_llm_task
                                except asyncio.CancelledError:
                                    logger.info(
                                        "[ms_conn v3] FAQ run_turn cancelled"
                                        " — newer transcript wins"
                                    )
                                    _faq_run_cancelled = True
                                finally:
                                    self._current_llm_task = None
                                if not _faq_run_cancelled:
                                    # ── NAME PERSISTENCE (Bug 7, FAQ path) ──
                                    # The normal post-turn block is skipped by
                                    # the continue below, so we run the name
                                    # check here before saving.
                                    _faq_last_bot = ""
                                    for _fm in reversed(
                                        self.session.get(
                                            "conversation_history", []
                                        )
                                    ):
                                        if _fm.get("role") == "assistant":
                                            _faq_last_bot = (
                                                _fm.get("content", "") or ""
                                            )
                                            break
                                    if _v3_try_persist_name(
                                        self.session,
                                        _faq_last_bot,
                                        post_slot_pending=self.post_slot_confirmation_pending,
                                        caller_utterance=utterance,
                                    ):
                                        logger.info(
                                            "[ms_conn v3] name persisted "
                                            "(FAQ path): %r",
                                            self.session.get("patient_name"),
                                        )
                                        # Spec Q: same early activation as
                                        # normal path — phone collection phase
                                        # starts at name confirmation.
                                        if not self.session.get(
                                            "v3_phone_dtmf_active"
                                        ):
                                            self.session[
                                                "v3_phone_dtmf_active"
                                            ] = True
                                            logger.info(
                                                "[ms_conn] v3_phone_dtmf_active"
                                                " = True (name confirmed —"
                                                " phone collection phase,"
                                                " FAQ path)"
                                            )
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                continue

                            # ── Intent pivot detection ────────────────────
                            # If the caller changes their mind while the
                            # location gate is active, detect the new
                            # intent and re-route immediately.
                            _RESCHEDULE_PIVOTS = {
                                "reschedule", "rearrange", "move my",
                                "change my",
                            }
                            _CANCEL_PIVOTS = {
                                "cancel", "cancellation",
                            }
                            _utt_pivot = utterance.lower()
                            _pivot_intent = None
                            if any(
                                w in _utt_pivot for w in _RESCHEDULE_PIVOTS
                            ):
                                _pivot_intent = "reschedule"
                            elif any(
                                w in _utt_pivot for w in _CANCEL_PIVOTS
                            ):
                                _pivot_intent = "cancel"

                            if _pivot_intent:
                                self.session["v3_caller_intent"] = (
                                    _pivot_intent
                                )
                                self.session["v3_booking_intent"] = False
                                self.session["v3_location_asked"] = False
                                self.session[
                                    "v3_awaiting_use_this_clinic"
                                ] = False
                                _pivot_loc_q = (
                                    "Was your original appointment at "
                                    "our Awlstuh or Redditch clinic?"
                                )
                                await self.tts_text_queue.put(_pivot_loc_q)
                                self.session[
                                    "last_bot_prompt"
                                ] = _pivot_loc_q
                                self.session[
                                    "last_question"
                                ] = _pivot_loc_q
                                self.session[
                                    "v3_location_q_active"
                                ] = True
                                # Re-arm location gate: we temporarily cleared
                                # v3_location_asked above so the gate-fired
                                # check (which reads it) is not confused, but
                                # we immediately restore it so the NEXT
                                # transcript correctly enters _v3_loc_answering.
                                # Without this, the caller's location answer
                                # after the pivot question falls through to the
                                # free-form LLM path and is never intercepted.
                                self.session["v3_location_asked"] = True
                                await save_session(
                                    self.call_sid, self.session
                                )
                                logger.info(
                                    "[ms_conn v3] intent pivot in loc"
                                    " gate: %s from %r — loc gate restored",
                                    _pivot_intent, utterance[:60],
                                )
                                self._last_audio_or_transcript_ts = time.monotonic()
                                return

                            if self.session.get(
                                "v3_awaiting_use_this_clinic"
                            ):
                                # ── use-this-clinic confirm handler ────────
                                # Caller answered a "Did you say the X clinic?"
                                # biased confirm.  Resolve the target clinic
                                # from last_bot_prompt so the handler is
                                # clinic-agnostic — works for both Awlstuh and
                                # Redditch biased confirms.
                                # Affirmative → biased clinic (or alcester
                                # as safe default).
                                # Redditch signal / "no" → redditch.
                                # Genuinely unresolvable → DTMF keypad fallback.
                                self.session[
                                    "v3_awaiting_use_this_clinic"
                                ] = False

                                # ── Read pre-computed bias from session ──────
                                # v3_use_this_clinic_bias is set at prompt
                                # generation time (watchdog / SilenceHandler),
                                # derived directly from the phrase text.
                                # Reading last_bot_prompt here is unreliable —
                                # it may still hold a previous LLM response
                                # that contains "Redditch" (e.g. "Awlstuh or
                                # Redditch?"), causing the wrong clinic to be
                                # resolved.  Use the stored bias instead.
                                _biased_clinic = (
                                    self.session.get("v3_use_this_clinic_bias")
                                    or "alcester"
                                )

                                # ── Trailing fragment guard ───────────────
                                # STT sometimes splits a long utterance and
                                # sends a trailing single word as a second
                                # final transcript. A one-word fragment that
                                # isn't a known response word must not be
                                # treated as a definitive answer.
                                _DEFINITIVE_WORDS = {
                                    "use", "this", "clinic", "yes",
                                    "yeah", "yep", "yup", "redditch",
                                    "reditch", "no", "nope", "alcester",
                                    # Affirmatives added for biased-confirm path
                                    "correct", "right", "did",
                                }
                                _fragment_words = (
                                    utterance.strip().lower().split()
                                )
                                _is_definitive = (
                                    len(_fragment_words) >= 2
                                    or (
                                        len(_fragment_words) == 1
                                        and _fragment_words[0]
                                        in _DEFINITIVE_WORDS
                                    )
                                )
                                if not _is_definitive:
                                    # Single unrecognised word — likely a
                                    # trailing STT fragment. Restore flag
                                    # and wait for the next turn.
                                    self.session[
                                        "v3_awaiting_use_this_clinic"
                                    ] = True
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    logger.info(
                                        "[ms_conn v3] trailing fragment"
                                        " ignored in use-this-clinic"
                                        " handler: %r",
                                        utterance,
                                    )
                                    return

                                # ── Rejection / question guard ───────────
                                # "no i asked what is the difference"
                                # contains "no", "what", "i asked" — all
                                # rejection signals.  Route to LLM so the
                                # caller gets a proper response.
                                # v3_location_asked stays True so the
                                # location question remains pending and
                                # Susie will re-ask after the LLM responds.
                                _utt_lower = utterance.lower()

                                # ── Clean-negative → bind the OTHER clinic ────
                                # With only two clinics (Awlstuh + Redditch), an
                                # unambiguous "no" to "Did you say the Awlstuh
                                # clinic?" resolves Redditch directly instead of
                                # bouncing to the LLM (which used to re-ask and
                                # loop).  Caller-requested 2026-06-12.  Strictly
                                # gated: a bare negative with NO question/
                                # confusion words and NO mention of the guessed
                                # clinic — so "no, what's the difference?" or
                                # "no, the Awlstuh one" still fall through to the
                                # LLM rejection path below.
                                _other_clinic = (
                                    "redditch"
                                    if _biased_clinic != "redditch"
                                    else "alcester"
                                )
                                _neg_tokens = (
                                    "no", "nope", "nah", "wrong",
                                    "incorrect", "didn't", "did not",
                                )
                                _confusion_tokens = (
                                    "what", "why", "how", "which",
                                    "difference", "?", "asked", "mean",
                                    "actually", "wait", "pardon", "repeat",
                                    "again", "sorry", "problem", "worries",
                                    "rush", "bother",
                                )
                                _biased_aliases = (
                                    ("awlstuh", "alcester", "alster",
                                     "all stuh", "ouston", "ousto")
                                    if _biased_clinic == "alcester"
                                    else ("redditch", "reditch", "red ditch")
                                )
                                _clean_negative = (
                                    any(t in _utt_lower for t in _neg_tokens)
                                    and not any(
                                        t in _utt_lower
                                        for t in _confusion_tokens
                                    )
                                    and not any(
                                        a in _utt_lower for a in _biased_aliases
                                    )
                                    and len(_utt_lower.split()) <= 6
                                )
                                if _clean_negative:
                                    logger.info(
                                        "[ms_conn v3] use-this-clinic clean"
                                        " negative %r — binding other clinic"
                                        " %s (was bias=%s)",
                                        utterance[:60], _other_clinic,
                                        _biased_clinic,
                                    )

                                if (
                                    any(
                                        r in _utt_lower
                                        for r in _USE_THIS_CLINIC_REJECTIONS
                                    )
                                    and not _clean_negative
                                ):
                                    logger.info(
                                        "[ms_conn v3] use-this-clinic"
                                        " rejected — negative/question"
                                        " response: %r",
                                        utterance[:60],
                                    )
                                    self._filler_breath_injected = False
                                    await self._filler.arm(self.session)
                                    self._current_llm_task = (
                                        asyncio.create_task(
                                            llm.run_turn(
                                                user_text=utterance,
                                                session=self.session,
                                                call_sid=self.call_sid,
                                                stream_sid=self.stream_sid,
                                                tts_text_queue=(
                                                    self.tts_text_queue
                                                ),
                                                audio_out_queue=(
                                                    self.audio_out_queue
                                                ),
                                                websocket=self.websocket,
                                                on_transfer=(
                                                    self._on_transfer_request
                                                ),
                                            ),
                                            name="ms_llm_turn",
                                        )
                                    )
                                    try:
                                        await self._current_llm_task
                                    except asyncio.CancelledError:
                                        logger.info(
                                            "[ms_conn v3] use-this-clinic"
                                            " rejection LLM turn cancelled"
                                            " — newer transcript wins"
                                        )
                                    finally:
                                        self._current_llm_task = None
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    continue

                                # ── Affirmative-only gate ─────────────────
                                # Any response that is not an explicit
                                # affirmative (ambiguous, vague, or a direct
                                # location name) goes to the LLM.  The LLM
                                # sees v3_location_asked=True and handles
                                # it correctly (e.g. "Redditch" → confirm).
                                if (
                                    not any(
                                        a in _utt_lower
                                        for a in _USE_THIS_CLINIC_AFFIRMATIVES
                                    )
                                    and not _clean_negative
                                ):
                                    # Third try on the ANSWER path.  The caller
                                    # responded to the rung-2 biased confirm but
                                    # it's not a clear yes/no (questions and
                                    # rejections were already routed to the LLM
                                    # by the guard above).  If the answer names a
                                    # clinic (e.g. just "Redditch"), let the LLM
                                    # resolve it — must NOT force DTMF and lose a
                                    # valid choice.  If it names no clinic at all,
                                    # it's garble → escalate to the rung-3 DTMF
                                    # keypad so the ladder matches the booking
                                    # flow (open → biased confirm → DTMF).
                                    _utc_has_clinic = any(
                                        a in _utt_lower
                                        for a in (
                                            _ALCESTER_ALIASES
                                            | _REDDITCH_ALIASES
                                        )
                                    )
                                    if not _utc_has_clinic:
                                        _utc_dtmf = _LOC_RUNG3_DTMF
                                        self.session[
                                            "v3_awaiting_use_this_clinic"
                                        ] = False
                                        self.session[
                                            "v3_awaiting_location_dtmf"
                                        ] = True
                                        self.session[
                                            "v3_location_q_active"
                                        ] = False
                                        self.session[
                                            "v3_location_reask_count"
                                        ] = int(self.session.get(
                                            "v3_location_reask_count", 0)) + 1
                                        self.session[
                                            "last_question"
                                        ] = _utc_dtmf
                                        self.session[
                                            "last_bot_prompt"
                                        ] = _utc_dtmf
                                        logger.info(
                                            "[ms_conn v3] use-this-clinic — no"
                                            " clear answer & no clinic named"
                                            " after biased confirm → rung 3"
                                            " DTMF keypad: %r",
                                            utterance[:60],
                                        )
                                        await self.tts_text_queue.put(_utc_dtmf)
                                        self._silence_handler\
                                            .on_question_asked(_utc_dtmf)
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        continue
                                    # Clear the flag BEFORE passing to LLM so
                                    # the next utterance isn't intercepted again
                                    # by the use-this-clinic handler.
                                    self.session[
                                        "v3_awaiting_use_this_clinic"
                                    ] = False
                                    logger.info(
                                        "[ms_conn v3] use-this-clinic"
                                        " — clinic named but not a clean yes/no,"
                                        " passing to LLM (flag cleared): %r",
                                        utterance[:60],
                                    )
                                    self._filler_breath_injected = False
                                    await self._filler.arm(self.session)
                                    self._current_llm_task = (
                                        asyncio.create_task(
                                            llm.run_turn(
                                                user_text=utterance,
                                                session=self.session,
                                                call_sid=self.call_sid,
                                                stream_sid=self.stream_sid,
                                                tts_text_queue=(
                                                    self.tts_text_queue
                                                ),
                                                audio_out_queue=(
                                                    self.audio_out_queue
                                                ),
                                                websocket=self.websocket,
                                                on_transfer=(
                                                    self._on_transfer_request
                                                ),
                                            ),
                                            name="ms_llm_turn",
                                        )
                                    )
                                    try:
                                        await self._current_llm_task
                                    except asyncio.CancelledError:
                                        logger.info(
                                            "[ms_conn v3] use-this-clinic"
                                            " ambiguous LLM turn cancelled"
                                            " — newer transcript wins"
                                        )
                                    finally:
                                        self._current_llm_task = None
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    continue

                                # Confirm the clinic and proceed with booking.
                                # Affirmative → biased clinic; clean negative →
                                # the other clinic (only two exist).
                                _confirmed = (
                                    _other_clinic if _clean_negative
                                    else _biased_clinic
                                )
                                logger.info(
                                    "[ms_conn v3] use-this-clinic"
                                    " confirmed via %s: %s (bias=%s)",
                                    "negative-flip" if _clean_negative
                                    else "affirmative",
                                    _confirmed, _biased_clinic,
                                )

                                if _confirmed:
                                    _disp = _confirmed.capitalize()
                                    _ack = f"{_disp}."
                                    _intent = self.session.get(
                                        "v3_caller_intent", "booking"
                                    )
                                    if _intent in ("reschedule", "cancel"):
                                        _next_q = (
                                            "Is the number you're calling "
                                            "on the one associated with "
                                            "your booking? If so, just "
                                            "say 'use this number'."
                                        )
                                    else:
                                        # FAQ-before-clinic: re-queue a pending
                                        # clinic-specific FAQ now the clinic is
                                        # known instead of dropping the caller
                                        # into the booking timing flow.  Mirrors
                                        # the verbal location-intercept non-
                                        # booking path (keyed off
                                        # v3_booking_intent).  synthetic=True so
                                        # the re-injection clears the STT-phantom
                                        # guards.
                                        _utc_faq_pending = self.session.pop(
                                            "v3_faq_pending_utterance", None
                                        )
                                        _utc_faq_requeued = False
                                        if _utc_faq_pending and not (
                                            self.session.get(
                                                "v3_booking_intent", False
                                            )
                                        ):
                                            _next_q = None
                                            _utc_tp = ""
                                            _utc_faq_requeued = True
                                            await self.transcript_queue.put(
                                                (time.monotonic(),
                                                 _utc_faq_pending, True)
                                            )
                                            logger.info(
                                                "[ms_conn v3] use-this-clinic:"
                                                " FAQ pending re-queued after"
                                                " clinic confirm (no booking"
                                                " Q): %r",
                                                _utc_faq_pending[:60],
                                            )
                                        else:
                                            # CODE SPEC AE REVISED — routing
                                            # check mirrors direct intercept.
                                            _utc_sc = (
                                                self.session.get("soft_context")
                                                or {}
                                            )
                                            _utc_tp = (
                                                _utc_sc.get("time_preference")
                                                or self.session.get(
                                                    "time_of_day_preference"
                                                )
                                                or ""
                                            )
                                            logger.info(
                                                "[ms_conn v3] use-this-clinic"
                                                " routing —"
                                                " soft_context.time_preference"
                                                "=%r time_of_day_preference=%r",
                                                _utc_sc.get("time_preference"),
                                                self.session.get(
                                                    "time_of_day_preference"
                                                ),
                                            )
                                            _next_q = (
                                                None if _utc_tp
                                                else (
                                                    "Is there a particular day"
                                                    " or time that works best"
                                                    " for you?"
                                                )
                                            )
                                    self.session[
                                        "selected_location"
                                    ] = _confirmed
                                    self.session[
                                        "v3_location_confirmed"
                                    ] = True
                                    self.session[
                                        "v3_location_q_active"
                                    ] = False
                                    self.session[
                                        "v3_location_asked"
                                    ] = False
                                    self.session[
                                        "v3_booking_intent"
                                    ] = False
                                    if _intent in ("reschedule", "cancel"):
                                        self.session[
                                            "v3_awaiting_phone_confirm"
                                        ] = True
                                    await self.tts_text_queue.put(_ack)
                                    if _next_q is not None:
                                        await self.tts_text_queue.put(
                                            _next_q
                                        )
                                        self.session[
                                            "last_bot_prompt"
                                        ] = _next_q
                                        self.session[
                                            "last_question"
                                        ] = _next_q
                                        self.session.setdefault(
                                            "conversation_history", []
                                        ).append({
                                            "role": "assistant",
                                            "content": _next_q,
                                        })
                                        if self._silence_handler is not None:
                                            self._silence_handler\
                                                .on_question_asked(_next_q)
                                    elif (
                                        _intent not in ("reschedule", "cancel")
                                        and not _utc_faq_requeued
                                    ):
                                        if self.booking_flow_active:
                                            # Booking already active: do NOT
                                            # re-queue the stored timing (would
                                            # risk a double check_availability and
                                            # acts on a possibly-stale pref).  But
                                            # this path is ack-only — without a
                                            # next step the call dead-airs after
                                            # the clinic ack and abandons
                                            # (2026-06-19: "book Friday" → clinic
                                            # via use-this-clinic ladder →
                                            # silence).  Ask the day/time question
                                            # instead: no tool fires, caller
                                            # confirms in one breath (mirrors the
                                            # #1 booking-ack fix).
                                            _utc_dt_q = (
                                                "Is there a particular day or"
                                                " time that works best for you?"
                                            )
                                            await self.tts_text_queue.put(
                                                _utc_dt_q
                                            )
                                            self.session[
                                                "last_bot_prompt"
                                            ] = _utc_dt_q
                                            self.session[
                                                "last_question"
                                            ] = _utc_dt_q
                                            self.session.setdefault(
                                                "conversation_history", []
                                            ).append({
                                                "role": "assistant",
                                                "content": _utc_dt_q,
                                            })
                                            if self._silence_handler is not None:
                                                self._silence_handler\
                                                    .on_question_asked(_utc_dt_q)
                                            logger.info(
                                                "[ms_conn v3] use-this-clinic:"
                                                " booking active — asked day/time"
                                                " Q (no strand)"
                                            )
                                        else:
                                            await self.transcript_queue.put(
                                                (time.monotonic(), _utc_tp,
                                                 True)
                                            )
                                            logger.info(
                                                "[ms_conn v3] use-this-clinic:"
                                                " time preference known (%r)"
                                                " — re-queued for"
                                                " check_availability",
                                                _utc_tp,
                                            )
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    logger.info(
                                        "[ms_conn v3] use-this-clinic"
                                        " confirmed: %s, intent=%s",
                                        _confirmed,
                                        _intent,
                                    )
                                else:
                                    # Genuinely unresolvable after two voice
                                    # rounds — fall back to DTMF keypad
                                    # selection (completely deterministic,
                                    # no STT ambiguity possible).
                                    # ── Transfer suppression guard ────────
                                    # If a biased confirm is already active
                                    # (re-set by the trailing fragment guard
                                    # above), don't fire DTMF fallback yet —
                                    # the confirm must play out first.
                                    if self.session.get(
                                        "v3_awaiting_use_this_clinic"
                                    ):
                                        logger.info(
                                            "[ms_conn v3] DTMF fallback"
                                            " suppressed — use-this-"
                                            "clinic confirm already active"
                                        )
                                        return
                                    # ── DTMF location fallback ────────────
                                    _dtmf_loc_q = (
                                        "No problem — press 1 for "
                                        "Awlstuh or press 2 for "
                                        "Redditch on your keypad."
                                    )
                                    self.session[
                                        "v3_awaiting_location_dtmf"
                                    ] = True
                                    self.session[
                                        "v3_awaiting_use_this_clinic"
                                    ] = False
                                    await self.tts_text_queue.put(
                                        _dtmf_loc_q
                                    )
                                    self.session[
                                        "last_bot_prompt"
                                    ] = _dtmf_loc_q
                                    self.session[
                                        "last_question"
                                    ] = _dtmf_loc_q
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    logger.info(
                                        "[ms_conn v3] location"
                                        " unresolvable — DTMF"
                                        " fallback queued"
                                    )
                            else:
                                # ── Code-gate alias matching ───────────────
                                # Fast path: known alias in utterance.
                                # If not found: Haiku resolver.
                                _confirmed_loc = _v3_extract_location(
                                    utterance
                                )
                                if _confirmed_loc:
                                    _loc_label = _confirmed_loc.capitalize()
                                    _ack = f"{_loc_label}."
                                    await self.tts_text_queue.put(_ack)
                                    self.session["last_bot_prompt"] = _ack
                                    self.session["selected_location"] = (
                                        _confirmed_loc
                                    )
                                    self.session[
                                        "v3_location_confirmed"
                                    ] = True
                                    self.session[
                                        "v3_location_q_active"
                                    ] = False
                                    _was_booking = self.session.get(
                                        "v3_booking_intent", False
                                    )
                                    self.session[
                                        "v3_booking_intent"
                                    ] = False
                                    self.session[
                                        "v3_location_asked"
                                    ] = False
                                    # If captured during a booking flow, queue
                                    # next question based on caller intent.
                                    if _was_booking:
                                        # Clear any stale FAQ pending utterance:
                                        # the caller has expressed booking
                                        # intent, so a deferred FAQ question is
                                        # superseded by the booking flow.
                                        self.session.pop(
                                            "v3_faq_pending_utterance", None
                                        )
                                        _loc_display = (
                                            _confirmed_loc.capitalize()
                                        )
                                        _intent = self.session.get(
                                            "v3_caller_intent", "booking"
                                        )
                                        if _intent in ("reschedule", "cancel"):
                                            _new_ret_q = (
                                                "Is the number you're "
                                                "calling on the one "
                                                "associated with your "
                                                "booking? If so, just "
                                                "say 'use this number'."
                                            )
                                            self.session[
                                                "v3_awaiting_phone_confirm"
                                            ] = True
                                        else:
                                            _sc_tp = (
                                                self.session.get(
                                                    "soft_context"
                                                ) or {}
                                            ).get("time_preference")
                                            _existing_tp = (
                                                _sc_tp
                                                or self.session.get(
                                                    "time_of_day_preference"
                                                )
                                                or ""
                                            )
                                            logger.info(
                                                "[ms_conn v3] location"
                                                " intercept routing —"
                                                " soft_context.time_preference=%r"
                                                " time_of_day_preference=%r",
                                                _sc_tp,
                                                self.session.get(
                                                    "time_of_day_preference"
                                                ),
                                            )
                                            if _existing_tp:
                                                # Caller confirmed location —
                                                # always re-queue the known
                                                # time preference so the LLM
                                                # calls check_availability with
                                                # the correct confirmed location.
                                                # booking_flow_active is NOT
                                                # a suppression reason here:
                                                # this is a new patient turn
                                                # (location confirmation), not
                                                # a double-dispatch.
                                                await (
                                                    self.transcript_queue
                                                    .put((time.monotonic(), _existing_tp))
                                                )
                                                logger.info(
                                                    "[ms_conn v3] time_pref"
                                                    " already known (%r) —"
                                                    " timing Q skipped,"
                                                    " re-queued pref",
                                                        _existing_tp,
                                                    )
                                                _new_ret_q = None
                                            else:
                                                _new_ret_q = (
                                                    "Is there a particular"
                                                    " day or time that works"
                                                    " best for you?"
                                                )
                                        if _new_ret_q is not None:
                                            await self.tts_text_queue.put(
                                                _new_ret_q
                                            )
                                            self.session[
                                                "last_bot_prompt"
                                            ] = _new_ret_q
                                            self.session[
                                                "last_question"
                                            ] = _new_ret_q
                                            self.session.setdefault(
                                                "conversation_history", []
                                            ).append({
                                                "role": "assistant",
                                                "content": _new_ret_q,
                                            })
                                            self._silence_handler\
                                                .on_question_asked(
                                                    _new_ret_q
                                                )
                                    else:
                                        # Non-booking ack-only path.
                                        # If the location was confirmed while
                                        # answering a FAQ clinic gate question
                                        # (v3_faq_pending_utterance is set),
                                        # re-queue the original FAQ utterance so
                                        # run_turn() answers it with the now-
                                        # confirmed clinic in CALL STATE.
                                        # Do NOT inject a timing question —
                                        # the caller asked about parking/hours,
                                        # not to book.
                                        _faq_pending = self.session.pop(
                                            "v3_faq_pending_utterance", None
                                        )
                                        if _faq_pending:
                                            # synthetic=True: bypass STT-phantom
                                            # guards — this re-injection races the
                                            # ack turn's completion and would else
                                            # be dropped as a same-breath straggler.
                                            await self.transcript_queue.put(
                                                (time.monotonic(), _faq_pending,
                                                 True)
                                            )
                                            logger.info(
                                                "[ms_conn v3] FAQ pending"
                                                " utterance re-queued after"
                                                " clinic confirm: %r",
                                                _faq_pending[:60],
                                            )
                                        else:
                                            # Treatment bypass / non-booking
                                            # with no pending FAQ — route based
                                            # on what is already known.
                                            _ae_sc = (
                                                self.session.get(
                                                    "soft_context"
                                                ) or {}
                                            )
                                            _ae_tp = (
                                                _ae_sc.get("time_preference")
                                                or self.session.get(
                                                    "time_of_day_preference"
                                                )
                                                or ""
                                            )
                                            if _ae_tp:
                                                await self.transcript_queue.put(
                                                    (time.monotonic(), _ae_tp,
                                                     True)
                                                )
                                                logger.info(
                                                    "[ms_conn v3] time"
                                                    " preference already"
                                                    " known (%r) — re-queued"
                                                    " for check_availability"
                                                    " after ack",
                                                    _ae_tp,
                                                )
                                            else:
                                                _PREF_Q = (
                                                    "Is there a particular"
                                                    " day or time that works"
                                                    " best for you?"
                                                )
                                                await self.tts_text_queue.put(
                                                    _PREF_Q
                                                )
                                                self.session[
                                                    "last_bot_prompt"
                                                ] = _PREF_Q
                                                self.session[
                                                    "last_question"
                                                ] = _PREF_Q
                                                self.session.setdefault(
                                                    "conversation_history", []
                                                ).append({
                                                    "role": "assistant",
                                                    "content": _PREF_Q,
                                                })
                                                self._silence_handler\
                                                    .on_question_asked(
                                                        _PREF_Q
                                                    )
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    logger.info(
                                        "[ms_conn v3] location answer"
                                        " intercepted — ack-only, no"
                                        " run_turn: %s",
                                        _confirmed_loc,
                                    )
                                else:
                                    # ── CODE SPEC AI: active-question guard ───
                                    # _v3_extract_location found no exact alias.
                                    # Original logic: skip Haiku for ≤ 3-word
                                    # transcripts (assumed noise).  That was wrong
                                    # — short utterances like "your alsta clinic"
                                    # or "Alcester please" ARE real answers when
                                    # the system just asked a direct question.
                                    # The skip is now gated: only discard when NO
                                    # active question is awaiting a response.
                                    # Within v3_location_q_active the system is
                                    # always waiting, so the guard never fires here.
                                    _utt_words_pre = utterance.strip().split()
                                    _ai_active_q = (
                                        self.session.get("v3_location_q_active")
                                        or self.session.get(
                                            "post_slot_confirmation_pending"
                                        )
                                        or self.slot_map_stage in (
                                            SlotMapStage.DAY_SELECTION,
                                            SlotMapStage.TIME_SELECTION,
                                        )
                                    )
                                    if (
                                        len(_utt_words_pre) <= 3
                                        and not _ai_active_q
                                    ):
                                        _last_q_loc = self.session.get(
                                            "last_question", ""
                                        )
                                        if _last_q_loc:
                                            self._silence_handler.set_state(
                                                self.session.get(
                                                    "state", "default"
                                                )
                                            )
                                            self._silence_handler.on_question_asked(
                                                _last_q_loc
                                            )
                                        logger.info(
                                            "[ms_conn v3] short non-alias"
                                            " utterance %r (%d word(s))"
                                            " — no active question,"
                                            " Haiku skipped, timer re-armed",
                                            utterance[:60],
                                            len(_utt_words_pre),
                                        )
                                        continue
                                    elif len(_utt_words_pre) <= 3:
                                        logger.info(
                                            "[ms_conn v3] short non-alias"
                                            " utterance %r (%d word(s))"
                                            " — active question, passing"
                                            " to Haiku",
                                            utterance[:60],
                                            len(_utt_words_pre),
                                        )
                                        # fall through to Haiku resolver

                                    # ── Haiku location resolver ───────────
                                    # Dedicated small-model call — faster and
                                    # more accurate than string matching or
                                    # full LLM inference for this task.
                                    # Uses the same client pattern as line 156.
                                    try:
                                        import os as _os
                                        _h_key = _os.environ.get(
                                            "ANTHROPIC_API_KEY",
                                            ANTHROPIC_API_KEY,
                                        )
                                        _haiku_client = (
                                            anthropic.AsyncAnthropic(
                                                api_key=_h_key,
                                                timeout=8.0,
                                            )
                                        )
                                        _loc_resp = await (
                                            _haiku_client.messages.create(
                                                model=HAIKU,
                                                max_tokens=20,
                                                system=(
                                                    "Extract clinic location"
                                                    " from caller speech. "
                                                    "Theorem Health has"
                                                    " exactly two clinics:"
                                                    " Alcester and Redditch."
                                                    "\n\n"
                                                    "IMPORTANT PHONETIC"
                                                    " CONTEXT:\n"
                                                    "Alcester is pronounced"
                                                    " AWL-stuh /ˈɔːlstər/."
                                                    " Callers will NOT say"
                                                    " 'al-chess-ter'."
                                                    " They say something"
                                                    " like: alcester,"
                                                    " ulster, awlster,"
                                                    " olster, alchester,"
                                                    " al chester,"
                                                    " allchester, alcestra,"
                                                    " ausesta, oldster,"
                                                    " holster, orlster,"
                                                    " oalster, aulster."
                                                    " ALL of these mean"
                                                    " Alcester. Be very"
                                                    " generous with"
                                                    " Alcester"
                                                    " matching.\n\n"
                                                    "Redditch is pronounced"
                                                    " RED-itch /ˈrɛdɪtʃ/."
                                                    " Common mishearings:"
                                                    " red witch, red rich,"
                                                    " red ridge, reddick,"
                                                    " reddish, red ditch,"
                                                    " reddidge, reddich,"
                                                    " redich, ridditch,"
                                                    " readitch, red wick,"
                                                    " red wich, redrick."
                                                    " If the utterance"
                                                    " sounds like any of"
                                                    " these, return"
                                                    " 'redditch'.\n\n"
                                                    "Reply with JSON only"
                                                    " — one of exactly"
                                                    " these three: "
                                                    '{"location":'
                                                    ' "alcester"} '
                                                    '{"location":'
                                                    ' "redditch"} '
                                                    '{"location":'
                                                    ' "unknown"} '
                                                    "Only return unknown"
                                                    " if the caller is"
                                                    " genuinely asking a"
                                                    " question, changing"
                                                    " their mind, or"
                                                    " saying something"
                                                    " completely unrelated"
                                                    " to a clinic"
                                                    " location. If there"
                                                    " is ANY phonetic"
                                                    " resemblance to"
                                                    " either clinic name,"
                                                    " return that clinic."
                                                    " If genuinely"
                                                    " ambiguous between"
                                                    " the two, default to"
                                                    " 'alcester' — it is"
                                                    " open 5 days a week"
                                                    " vs Redditch's 1 day,"
                                                    " so statistically"
                                                    " more likely."
                                                ),
                                                messages=[{
                                                    "role": "user",
                                                    "content": utterance,
                                                }],
                                            )
                                        )
                                        _loc_raw = (
                                            _loc_resp.content[0].text
                                            .strip().lower()
                                        )
                                        if (
                                            "alcester" in _loc_raw
                                            and "redditch" not in _loc_raw
                                        ):
                                            _resolved = "alcester"
                                        elif (
                                            "redditch" in _loc_raw
                                            and "alcester" not in _loc_raw
                                        ):
                                            _resolved = "redditch"
                                        else:
                                            _resolved = "unknown"
                                    except Exception as _loc_err:
                                        logger.warning(
                                            "[ms_conn v3] Haiku location"
                                            " resolver failed: %s"
                                            " — defaulting to unknown",
                                            _loc_err,
                                        )
                                        _resolved = "unknown"

                                    if _resolved != "unknown":
                                        _disp = _resolved.capitalize()
                                        _ack = f"{_disp}."
                                        _intent = self.session.get(
                                            "v3_caller_intent", "booking"
                                        )
                                        # CODE SPEC AE REVISED — determine
                                        # next step based on intent and
                                        # whether time preference is known.
                                        if _intent in ("reschedule", "cancel"):
                                            _h_next_q = (
                                                "Is the number you're "
                                                "calling on the one "
                                                "associated with your "
                                                "booking? If so, just "
                                                "say 'use this number'."
                                            )
                                            _h_tp = ""
                                        else:
                                            _h_sc = (
                                                self.session.get(
                                                    "soft_context"
                                                ) or {}
                                            )
                                            _h_tp = (
                                                _h_sc.get("time_preference")
                                                or self.session.get(
                                                    "time_of_day_preference"
                                                )
                                                or ""
                                            )
                                            _h_next_q = (
                                                ""
                                                if _h_tp
                                                else (
                                                    "Is there a particular"
                                                    " day or time that works"
                                                    " best for you?"
                                                )
                                            )
                                        self.session[
                                            "selected_location"
                                        ] = _resolved
                                        self.session[
                                            "v3_location_confirmed"
                                        ] = True
                                        self.session[
                                            "v3_location_q_active"
                                        ] = False
                                        self.session[
                                            "v3_location_asked"
                                        ] = False
                                        self.session[
                                            "v3_booking_intent"
                                        ] = False
                                        if _intent in ("reschedule", "cancel"):
                                            self.session[
                                                "v3_awaiting_phone_confirm"
                                            ] = True
                                        await self.tts_text_queue.put(_ack)
                                        if _h_tp:
                                            # Time preference known — re-queue
                                            # so LLM fires check_availability.
                                            await self.transcript_queue.put(
                                                (time.monotonic(), _h_tp,
                                                 True)
                                            )
                                            logger.info(
                                                "[ms_conn v3] Haiku resolve:"
                                                " time preference already"
                                                " known (%r) — re-queued"
                                                " for check_availability",
                                                _h_tp,
                                            )
                                        elif _h_next_q:
                                            await self.tts_text_queue.put(
                                                _h_next_q
                                            )
                                            self.session[
                                                "last_bot_prompt"
                                            ] = _h_next_q
                                            self.session[
                                                "last_question"
                                            ] = _h_next_q
                                            self.session.setdefault(
                                                "conversation_history", []
                                            ).append({
                                                "role": "assistant",
                                                "content": _h_next_q,
                                            })
                                            self._silence_handler\
                                                .on_question_asked(
                                                    _h_next_q
                                                )
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        # C8-2: arm the race guard so any
                                        # phantom second final from the same
                                        # breath is dropped at dequeue.
                                        self.session[
                                            "location_acked_this_turn"
                                        ] = True
                                        self._location_ack_ts = (
                                            time.monotonic()
                                        )
                                        logger.info(
                                            "[ms_conn v3] Haiku resolved"
                                            " location: %s, intent=%s,"
                                            " from %r",
                                            _resolved,
                                            _intent,
                                            utterance[:60],
                                        )
                                    else:
                                        # Haiku returned unknown.
                                        # ── Question guard ──────────────────
                                        # If the caller asked a FAQ question
                                        # (e.g. "what is the difference
                                        # between the clinics?") rather than
                                        # giving an unclear location answer,
                                        # route to the LLM for a proper
                                        # response instead of firing the
                                        # use-this-clinic confirm.
                                        # v3_location_asked is NOT cleared —
                                        # the location question stays pending
                                        # and Susie will re-ask after the
                                        # LLM answers the FAQ.
                                        if _transcript_is_question(utterance):
                                            logger.info(
                                                "[ms_conn v3] location"
                                                " intercept — Haiku unknown"
                                                " + question detected,"
                                                " routing to LLM: %r",
                                                utterance[:60],
                                            )
                                            self._filler_breath_injected = (
                                                False
                                            )
                                            await self._filler.arm(
                                                self.session
                                            )
                                            self._current_llm_task = (
                                                asyncio.create_task(
                                                    llm.run_turn(
                                                        user_text=utterance,
                                                        session=self.session,
                                                        call_sid=self.call_sid,
                                                        stream_sid=(
                                                            self.stream_sid
                                                        ),
                                                        tts_text_queue=(
                                                            self.tts_text_queue
                                                        ),
                                                        audio_out_queue=(
                                                            self.audio_out_queue
                                                        ),
                                                        websocket=(
                                                            self.websocket
                                                        ),
                                                        on_transfer=(
                                                            self
                                                            ._on_transfer_request
                                                        ),
                                                    ),
                                                    name="ms_llm_turn",
                                                )
                                            )
                                            try:
                                                await self._current_llm_task
                                            except asyncio.CancelledError:
                                                logger.info(
                                                    "[ms_conn v3] Haiku-unknown"
                                                    " FAQ run_turn cancelled"
                                                    " — newer transcript wins"
                                                )
                                            finally:
                                                self._current_llm_task = None
                                            await save_session(
                                                self.call_sid, self.session
                                            )
                                            continue

                                        # ── Haiku unknown, non-question ─────
                                        # Garbled clinic answer (STT couldn't
                                        # resolve it) and NOT a question — climb
                                        # the SAME location ladder the silence
                                        # watchdog uses, so a voice-answer re-ask
                                        # and a silence re-ask are identical:
                                        #   reask_count 0  → rung 2 biased confirm
                                        #   reask_count ≥1 → rung 3 DTMF keypad
                                        # v3_location_reask_count is shared with
                                        # the watchdog so the two never double up.
                                        # (Previously routed to the LLM, which
                                        # re-asked with ad-hoc wording — the
                                        # 17:37 spiral.  Questions still go to the
                                        # LLM via the guard above.)
                                        _hu_lrc = int(
                                            self.session.get(
                                                "v3_location_reask_count", 0
                                            )
                                        )
                                        if _hu_lrc == 0:
                                            _hu_phrase = _LOC_RUNG2_CONFIRM
                                            self.session[
                                                "v3_awaiting_use_this_clinic"
                                            ] = True
                                            self.session[
                                                "v3_use_this_clinic_bias"
                                            ] = "alcester"
                                            logger.info(
                                                "[ms_conn v3] Haiku unknown"
                                                " non-question — rung 2 biased"
                                                " confirm (bias=alcester): %r",
                                                utterance[:60],
                                            )
                                        else:
                                            _hu_phrase = _LOC_RUNG3_DTMF
                                            self.session[
                                                "v3_awaiting_location_dtmf"
                                            ] = True
                                            self.session[
                                                "v3_awaiting_use_this_clinic"
                                            ] = False
                                            self.session[
                                                "v3_location_q_active"
                                            ] = False
                                            logger.info(
                                                "[ms_conn v3] Haiku unknown"
                                                " non-question — rung 3 DTMF"
                                                " keypad: %r",
                                                utterance[:60],
                                            )
                                        self.session[
                                            "v3_location_reask_count"
                                        ] = _hu_lrc + 1
                                        self.session[
                                            "last_question"
                                        ] = _hu_phrase
                                        self.session[
                                            "last_bot_prompt"
                                        ] = _hu_phrase
                                        # v3_location_asked stays True so the
                                        # caller's next answer re-enters this
                                        # intercept (rung 2 also armed the
                                        # use-this-clinic handler for it).
                                        await self.tts_text_queue.put(
                                            _hu_phrase
                                        )
                                        self._silence_handler\
                                            .on_question_asked(_hu_phrase)
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        continue

                        else:
                            # ── Normal path: run free-form LLM turn ─────────
                            # Handles TTS streaming, tool calls, and
                            # conversation_history append internally.
                            #
                            # Clear name-collection guards — either the caller
                            # answered directly or the clarification was
                            # already drained above.
                            self._name_clarification_queued = False
                            #
                            # SPEC 1B — clarification-in-flight guard.
                            # If a clarification phrase is still queued/playing
                            # (set by _fire_name_reask), discard this utterance
                            # so it cannot produce a competing LLM response.
                            # One-shot: clears the flag so the NEXT utterance
                            # (the caller's actual name) is processed normally.
                            if self._clarification_in_flight:
                                self._clarification_in_flight = False
                                logger.info(
                                    "[ms_conn v3] clarification in-flight — "
                                    "discarding noise utterance: %r",
                                    utterance[:60],
                                )
                                continue
                            #
                            # Capture last_question BEFORE run_turn in case the
                            # LLM updates it — used below for name persistence.
                            _pre_turn_last_q = self.session.get(
                                "last_question", ""
                            )
                            #
                            # Per-turn in-flight lock: cancel any stale LLM
                            # task before starting a new one so two rapid STT
                            # finals cannot produce concurrent Anthropic calls.
                            if self._current_llm_task and not self._current_llm_task.done():
                                logger.warning(
                                    "[ms_conn v3] stale LLM task at normal "
                                    "path — cancelling, new transcript wins"
                                )
                                self._current_llm_task.cancel()
                                try:
                                    await self._current_llm_task
                                except asyncio.CancelledError:
                                    pass
                                self._current_llm_task = None
                                # Flush any TTS the cancelled task had already
                                # queued — prevents stale audio playing over
                                # the new turn's response.
                                _norm_flushed = 0
                                try:
                                    while True:
                                        self.tts_text_queue.get_nowait()
                                        _norm_flushed += 1
                                except asyncio.QueueEmpty:
                                    pass
                                logger.info(
                                    "[ms_llm] task cancelled and TTS "
                                    "flushed: %d items (normal path)",
                                    _norm_flushed,
                                )

                            # Update rapid-arrival baseline — this transcript
                            # is accepted, so the next one is measured from now.
                            # Store raw text for Condition 4 merge (fragment
                            # continuation appends to this before re-eval).
                            self._v3_last_processed_ts = _enqueue_ts
                            self._v3_last_transcript_text = utterance.strip()

                            # ── Bug 5 / Extended: time-of-day preference capture ─
                            # Scan every accepted utterance for an embedded
                            # time-of-day preference signal BEFORE dispatching
                            # to the LLM, so that if the LLM calls
                            # check_availability in the same turn the preference
                            # is already in session state.  Fires on standalone
                            # answers ('mornings please') AND embedded phrases
                            # ('anytime next week afternoons').  The _pre_lbp
                            # gate has been removed — _extract_time_preference()
                            # handles false-positive avoidance internally.
                            # Once set this field is never cleared within a call.
                            if not self.session.get("time_of_day_preference"):
                                _tod = _extract_time_preference(utterance)
                                if _tod:
                                    self.session["time_of_day_preference"] = _tod
                                    _sc = self.session.setdefault("soft_context", {})
                                    if not _sc.get("time_preference"):
                                        _sc["time_preference"] = _tod
                                    logger.info(
                                        "[ms_conn v3] time_of_day_preference captured: %s"
                                        " (from utterance %r)",
                                        _tod,
                                        utterance,
                                    )

                            # ── Spec I: turn-level slot cache clear ──────────
                            # New patient turn starting — invalidate any slots
                            # offered in the previous turn so the next
                            # check_availability call hits Acuity fresh.
                            # (Mid-turn re-query blocking in llm_stream.py is
                            # unaffected — it only fires within the same turn.)
                            if self.session.get("last_offered_slots") is not None:
                                _prev_hint = self.session.get("last_date_hint")
                                _prev_slots = self.session.get("last_offered_slots") or []
                                logger.info(
                                    "[ms_llm] slot cache cleared on new turn"
                                    " (was date_hint=%r)",
                                    _prev_hint,
                                )
                                self.session["last_offered_slots"] = None
                                self.session["last_date_hint"] = None
                                # Prefer the specific day from offered slots
                                # over the broader week hint (e.g. save
                                # "2026-06-23" not "week of 22 June 2026").
                                # This ensures CALL STATE re-prompts the
                                # exact day, not the whole week.
                                _offered_day_iso = None
                                if _prev_slots:
                                    try:
                                        _offered_day_iso = (
                                            _prev_slots[0]["start"][:10]
                                        )
                                    except (IndexError, KeyError, TypeError):
                                        pass
                                if _offered_day_iso:
                                    self.session[
                                        "v3_last_presented_date_hint"
                                    ] = _offered_day_iso
                                    self.session[
                                        "v3_last_offered_day_iso"
                                    ] = _offered_day_iso
                                    logger.info(
                                        "[ms_llm] slot cache cleared:"
                                        " day iso=%r from offered slots",
                                        _offered_day_iso,
                                    )
                                elif _prev_hint:
                                    self.session[
                                        "v3_last_presented_date_hint"
                                    ] = _prev_hint
                                    logger.info(
                                        "[ms_llm] slot cache cleared:"
                                        " date hint=%r preserved",
                                        _prev_hint,
                                    )
                            # ── Spec Y REVISED: pre-run_turn treatment gate ───
                            # Must fire BEFORE run_turn because the LLM streams
                            # "Of course —" to TTS in real time (booking_flow
                            # step 1).  Setting booking_flow_active=True here
                            # and v3_treatment_mentioned=True lets the system
                            # prompt skip the ack and go straight to service
                            # type handling.  The post-turn ack block
                            # (if _is_booking_ack:) is suppressed because
                            # booking_flow_active is already True when it runs.
                            if _is_treatment_specific_booking(utterance):
                                # BUG-7 fix: a treatment mention always flags
                                # v3_treatment_mentioned (drives the assessment-
                                # first framing + treatment-aware routing), but it
                                # must enter the booking flow ONLY when there is
                                # ACTUAL booking intent.  A pure FAQ treatment
                                # question ("do you offer acupuncture?", "does
                                # shockwave hurt?") must NOT flip
                                # booking_flow_active — that adds the "BOOKING FLOW
                                # ACTIVE" CALL-STATE marker (susie_system_prompt
                                # line ~3246), which is the sole thing that makes
                                # the LLM tack a "day or time?" booking push onto
                                # every later FAQ answer (BUG-7, Calls 4 & 5).
                                # Real booking ("book a massage") has intent and
                                # still activates here; a later "yes" to an offer
                                # still activates via the CTA-affirm path.  The
                                # code already called this premature-flag out at
                                # the _is_booking_ack CTA-affirm arm.  Owner-signed
                                # FROZEN-zone change 2026-06-15. See [[susie-8call-sweep]].
                                self.session["v3_treatment_mentioned"] = True
                                if (
                                    not self.booking_flow_active
                                    and _transcript_has_booking_intent(utterance)
                                ):
                                    self.booking_flow_active = True
                                    self.session["booking_flow_active"] = True
                                    logger.info(
                                        "[ms_conn v3] treatment mention + booking"
                                        " intent — booking_flow_active=True: %r",
                                        utterance[:80],
                                    )
                                else:
                                    logger.info(
                                        "[ms_conn v3] treatment mention (FAQ, no"
                                        " booking intent) — v3_treatment_mentioned"
                                        " set, booking_flow_active left %s: %r",
                                        self.booking_flow_active, utterance[:80],
                                    )
                            # ── end Spec Y REVISED ────────────────────────────

                            # ── Duplicate slot guard ──────────────────────────
                            # Problem: rapid-continuation transcript pairs fire
                            # two LLM calls.  If LLM call 1 presents slots, the
                            # pending transcript re-queues and LLM call 2 also
                            # presents the same slots.  Caller hears two slot
                            # lists with two "Any of those suit you?" CTAs.
                            #
                            # Guard: if slot selection is still active AND the
                            # new utterance is short (< 8 words) AND contains
                            # no rejection or new-date signal, suppress run_turn
                            # — the slots are already in the caller's ear.
                            #
                            # NOT suppressed:
                            #   - Utterances ≥ 8 words (genuine new request)
                            #   - Any rejection/alternative signal word
                            #   - When v3_awaiting_slot_selection is False
                            #     (slot selection phase is over)
                            _sg_words = utterance.strip().split()
                            _SLOT_GUARD_PASS = frozenset({
                                "none", "nothing", "different", "another",
                                "else", "instead", "other", "change",
                                "doesn't", "dont", "won't", "wont",
                                "can't", "cant", "not", "no",
                                "june", "july", "august", "september",
                                "october", "november", "december",
                                "january", "february", "march", "april",
                                "week", "month", "earlier", "later",
                            })
                            _slot_repeat_suppressed = (
                                bool(self.session.get(
                                    "v3_awaiting_slot_selection"
                                ))
                                and len(_sg_words) < 8
                                and not any(
                                    w.lower().strip(".,?!'")
                                    in _SLOT_GUARD_PASS
                                    for w in _sg_words
                                )
                            )
                            if _slot_repeat_suppressed:
                                logger.info(
                                    "[ms_conn] slot repeat suppressed"
                                    " — slots already presented this"
                                    " turn: %r",
                                    utterance[:60],
                                )
                                continue
                            # ── end duplicate slot guard ──────────────────────

                            # ── FAQ clinic gate ──────────────────────────────
                            # Hard-coded intercept: if the caller asks about
                            # a clinic-specific topic (parking, address, hours,
                            # transport) and no clinic is confirmed yet, ask
                            # "Which clinic?" directly — skip run_turn() so
                            # the LLM cannot summarise both clinics first.
                            # Injects the exchange into conversation_history
                            # so the next LLM turn has full FAQ context for
                            # the specific clinic the caller names.
                            if (
                                not self.session.get("v3_location_confirmed")
                                and not self.booking_flow_active
                                and not self.session.get(
                                    "v3_location_asked", False
                                )
                                and bool(
                                    _FAQ_CLINIC_SPECIFIC_RE.search(utterance)
                                )
                            ):
                                _faq_clinic_q = _LOC_RUNG1_OPEN
                                await self.tts_text_queue.put(_faq_clinic_q)
                                # Write into history so next LLM turn has
                                # parking/address/hours context for the clinic.
                                _faq_h = self.session.setdefault(
                                    "conversation_history", []
                                )
                                _faq_h.append(
                                    {"role": "user", "content": utterance}
                                )
                                _faq_h.append(
                                    {
                                        "role": "assistant",
                                        "content": _faq_clinic_q,
                                    }
                                )
                                self.session["last_bot_prompt"] = _faq_clinic_q
                                self.session["last_question"] = _faq_clinic_q
                                self.session["_turn_speech_emitted"] = True
                                # Arm the location gate so the caller's next
                                # utterance is intercepted by the alias handler
                                # rather than falling through to run_turn().
                                # Store the original FAQ utterance so the
                                # location handler can re-run it (answer the
                                # parking/hours question) instead of injecting
                                # a booking-flow timing question.
                                self.session["v3_location_asked"] = True
                                self.session["v3_location_q_active"] = True
                                self.session[
                                    "_location_q_patient_spoke"
                                ] = False
                                self.session[
                                    "v3_faq_pending_utterance"
                                ] = utterance
                                self._silence_handler.on_question_asked(
                                    _faq_clinic_q
                                )
                                logger.info(
                                    "[ms_conn v3] FAQ clinic gate:"
                                    " no clinic confirmed — injecting"
                                    " 'Which clinic?' and skipping"
                                    " run_turn (utterance=%r)",
                                    utterance[:80],
                                )
                                continue  # finally: cleans up llm_in_flight
                            # ── end FAQ clinic gate ───────────────────────────

                            # Capture previous bot response BEFORE run_turn()
                            # overwrites last_bot_prompt.  Used by the booking
                            # ack detection below to recognise when the caller
                            # is affirming an LLM-generated booking CTA
                            # ("yes please" → "Would you like to book?").
                            _pre_turn_last_bot = self.session.get(
                                "last_bot_prompt", ""
                            )
                            # last_bot_prompt is truncated to [:200]
                            # (llm_stream.py).  A long clinical reply
                            # (empathy + physio sentence + "Would you like
                            # to book…") can push the booking CTA past char
                            # 200, hiding it from CTA-affirm detection and
                            # producing a silent turn on "yes please".
                            # Capture the FULL previous assistant message from
                            # conversation_history (run_turn() has not yet
                            # appended this turn's reply, so the last assistant
                            # entry is the previous turn's complete response).
                            _pre_turn_last_bot_full = ""
                            for _m in reversed(
                                self.session.get("conversation_history", [])
                            ):
                                if _m.get("role") == "assistant":
                                    _pre_turn_last_bot_full = (
                                        _m.get("content", "") or ""
                                    )
                                    break

                            # Change B: arm filler before LLM call.
                            # arm() is a no-op unless booking_flow_active is True.
                            self._filler_breath_injected = False
                            await self._filler.arm(self.session)

                            # B2 fix: establish a fresh per-turn speech flag and
                            # clear any stale deferred gate5 fallback before the
                            # LLM turn.  The v3 loop otherwise never resets
                            # _turn_speech_emitted; the _TrackedQueue sets it True
                            # on any audible enqueue during run_turn OR the
                            # post-turn recovery path, so the deferred-fallback
                            # decision below can rely on it.  Reaching this point
                            # means no early-continue branch spoke this turn.
                            self.session["_turn_speech_emitted"] = False
                            self.session.pop("_gate5_fallback_pending", None)

                            self._current_llm_task = asyncio.create_task(
                                llm.run_turn(
                                    user_text=utterance,
                                    session=self.session,
                                    call_sid=self.call_sid,
                                    stream_sid=self.stream_sid,
                                    tts_text_queue=self.tts_text_queue,
                                    audio_out_queue=self.audio_out_queue,
                                    websocket=self.websocket,
                                    on_transfer=self._on_transfer_request,
                                ),
                                name="ms_llm_turn",
                            )
                            _run_turn_cancelled = False
                            try:
                                await self._current_llm_task
                            except asyncio.CancelledError:
                                logger.info(
                                    "[ms_conn v3] run_turn task cancelled "
                                    "— newer transcript wins"
                                )
                                _run_turn_cancelled = True
                            finally:
                                self._current_llm_task = None

                            if _run_turn_cancelled:
                                # Skip all post-turn processing — outer finally
                                # at line ~4800 still clears _llm_busy before
                                # this continue reaches the while-loop top.
                                continue

                            # B2: tracks whether any post-turn recovery path
                            # queued speech to TTS.  The v3 tts_text_queue is a
                            # plain asyncio.Queue (not _TrackedQueue), so this
                            # local flag is the only reliable signal; it is
                            # checked by the deferred gate5 suppression block.
                            _v3_post_turn_speech = False

                            # Persist session
                            await save_session(self.call_sid, self.session)

                            # theorem_v3 slot DTMF: slot map is now extracted by
                            # _flush_slot_buf on the complete assembled response
                            # (Bug 7 fix — last_bot_prompt is [:200] truncated
                            # and must not be used for extraction).
                            # Here we only clear stale state when _flush_slot_buf
                            # did not produce a map this turn (no slot presentation
                            # in this response, or slot buf was not used).
                            if self.session.get("v3_dtmf_slot_map"):
                                _new_map = self.session["v3_dtmf_slot_map"]
                                # Arm slot-selection wait flag here (not solely in
                                # _flush_slot_buf) so the watchdog grace period check
                                # always sees it regardless of which extraction path
                                # produced the map.  Must be set before on_question_asked
                                # arms the watchdog a few lines below.
                                self.session["v3_awaiting_slot_selection"] = True
                                if _is_time_map(_new_map):
                                    # Day→time context shift: the new map contains
                                    # time options, not day options.  _flush_slot_buf
                                    # already wrote the new map into v3_dtmf_slot_map,
                                    # overwriting the stale day map.  Record context so
                                    # downstream code can distinguish day vs time DTMF.
                                    self.session["v3_dtmf_slot_context"] = "time"
                                    self.slot_map_stage = SlotMapStage.TIME_SELECTION
                                    logger.info(
                                        "[ms_conn v3] slot map active — %s: %r",
                                        self.slot_map_stage.name.lower(),
                                        _new_map,
                                    )
                                else:
                                    self.session["v3_dtmf_slot_context"] = "day"
                                    self.slot_map_stage = SlotMapStage.DAY_SELECTION
                                    logger.info(
                                        "[ms_conn v3] slot map active — %s "
                                        "(complete-response extraction): %r",
                                        self.slot_map_stage.name.lower(),
                                        _new_map,
                                    )
                            else:
                                # No numbered options this turn — clear any stale
                                # map so phone DTMF auto-activate is not blocked.
                                self.session.pop("v3_slot_dtmf_active",         None)
                                self.session.pop("v3_awaiting_slot_selection",  None)
                                self.session.pop("v3_dtmf_slot_context",        None)
                                if self.slot_map_stage != SlotMapStage.NONE:
                                    self.slot_map_stage = SlotMapStage.NONE
                                    logger.info(
                                        "[ms_conn v3] slot map stage → NONE"
                                        " (no slot map this turn)"
                                    )

                            # Pre-emptive slot → phone DTMF transition.
                            # If the LLM just asked the caller to type on the
                            # keypad (phone collection), any stale slot map
                            # MUST be cleared now — before the first digit
                            # arrives.  Without this, the slot handler arms
                            # on digit-1 and silently drops digit-2 (no slot
                            # mapping found), consuming the first two digits
                            # of the phone number before phone DTMF activates.
                            _post_lbp = self.session.get(
                                "last_bot_prompt", ""
                            ).lower()
                            if (
                                "keypad" in _post_lbp
                                and self.session.get("v3_dtmf_slot_map")
                            ):
                                self.session.pop("v3_dtmf_slot_map",           None)
                                self.session.pop("v3_slot_dtmf_active",        None)
                                self.session.pop("v3_awaiting_slot_selection", None)
                                self.session["v3_phone_dtmf_active"] = True
                                logger.info(
                                    "[ms_conn] slot DTMF disarmed → "
                                    "phone DTMF activated (pre-emptive)"
                                )

                            # Soft-context extraction — fire-and-forget,
                            # never raises.  Pull the most recent assistant
                            # message from history (run_turn appended it).
                            _last_bot = ""
                            for _msg in reversed(
                                self.session.get("conversation_history", [])
                            ):
                                if _msg.get("role") == "assistant":
                                    _last_bot = (
                                        _msg.get("content", "") or ""
                                    )
                                    break
                            asyncio.create_task(
                                _update_soft_context(
                                    self.session, utterance, _last_bot
                                )
                            )

                            # ── CTA COUNT TRACKING ────────────────────────────
                            # Count booking CTAs in bot replies so the prompt
                            # can suppress repetitive offers after 2 fires.
                            # Only count pre-booking-flow turns — CTAs inside
                            # the booking flow ("shall I go ahead and book?")
                            # are not the repetitive FAQ kind.
                            if not self.booking_flow_active:
                                _CTA_DETECT = (
                                    "would you like to book",
                                    "book an appointment",
                                    "book an assessment",
                                    "like to make an appointment",
                                    "shall i book",
                                    "book you in",
                                )
                                if any(
                                    p in _last_bot.lower()
                                    for p in _CTA_DETECT
                                ):
                                    self.session["v3_cta_count"] = (
                                        self.session.get("v3_cta_count", 0) + 1
                                    )
                            # ── end CTA COUNT TRACKING ────────────────────────

                            # ── NAME PERSISTENCE (Bug 7) ─────────────────────
                            # Persist name immediately when the LLM confirms it
                            # verbally ("Thanks Sarah — if you'd like to use
                            # the number...").  collected["name"] must not wait
                            # until book_appointment; if the call ends before
                            # booking the name would be lost.
                            # _v3_try_persist_name also writes session["patient_name"]
                            # as a direct key so summaries can find it even if
                            # the collected dict path is not traversed.
                            if _v3_try_persist_name(
                                self.session,
                                _last_bot,
                                post_slot_pending=self.post_slot_confirmation_pending,
                                caller_utterance=utterance,
                            ):
                                await save_session(
                                    self.call_sid, self.session
                                )
                                logger.info(
                                    "[ms_conn v3] name persisted "
                                    "(normal path): %r",
                                    self.session.get("patient_name"),
                                )
                                # Spec Q: activate DTMF at name-confirmed state.
                                # Phone collection begins the moment the name is
                                # persisted — digits may arrive before any
                                # "keypad" mention.  Keypad-mention activation
                                # (Spec M) remains as a secondary trigger.
                                if not self.session.get("v3_phone_dtmf_active"):
                                    self.session["v3_phone_dtmf_active"] = True
                                    logger.info(
                                        "[ms_conn] v3_phone_dtmf_active = True"
                                        " (name confirmed — phone collection"
                                        " phase)"
                                    )
                            # ── Spec J: post-slot confirmation flag ───────────
                            # Update post_slot_confirmation_pending based on
                            # whether this response asked for the patient's name.
                            # Uses _last_bot (full untruncated assistant message)
                            # so it is not affected by the 200-char last_bot_prompt
                            # truncation.  Flag is set True if a name-request
                            # phrase is found; cleared otherwise so a turn that
                            # does NOT ask for a name (e.g. a slot re-offer)
                            # resets the flag correctly.
                            _last_bot_j = _last_bot.lower()
                            if any(p in _last_bot_j for p in _NAME_REQUEST_PHRASES):
                                self.post_slot_confirmation_pending = True
                                logger.info(
                                    "[ms_conn] post_slot_confirmation_pending = True"
                                    " (name request detected in response)"
                                )
                                # Spec K: name request = slot flow complete.
                                # Transition stage to NONE and clear any residual
                                # slot map so DTMF digits are not misread as
                                # day/time re-selections during name collection.
                                if self.slot_map_stage != SlotMapStage.NONE:
                                    logger.info(
                                        "[ms_conn] slot map stage → NONE"
                                        " (name request — advancing beyond slot selection)"
                                    )
                                    self.slot_map_stage = SlotMapStage.NONE
                                self.session.pop("v3_dtmf_slot_map",           None)
                                self.session.pop("v3_slot_dtmf_active",        None)
                                self.session.pop("v3_awaiting_slot_selection",  None)
                                self.session.pop("v3_dtmf_slot_context",        None)
                            else:
                                if self.post_slot_confirmation_pending:
                                    logger.info(
                                        "[ms_conn] post_slot_confirmation_pending cleared"
                                        " (no name request this turn)"
                                    )
                                self.post_slot_confirmation_pending = False
                            # ── Spec M: sticky v3_phone_dtmf_active ───────────
                            # Activate phone DTMF mode whenever the LLM response
                            # mentions "keypad", regardless of whether a slot map
                            # was active.  This prevents the flag from being lost
                            # if the caller speaks mid-collection and a fresh LLM
                            # turn runs without an active slot map.
                            if (
                                not self.session.get("v3_phone_dtmf_active")
                                and "keypad" in _last_bot.lower()
                            ):
                                self.session["v3_phone_dtmf_active"] = True
                                logger.info(
                                    "[ms_conn] v3_phone_dtmf_active = True"
                                    " (keypad mention detected in response)"
                                )
                            # ── BOOKING ACK DETECTION + AUTO-QUEUE ───────────
                            # If the LLM generated a warm booking
                            # acknowledgement (no question), immediately queue
                            # the location question so it plays right after
                            # the ack audio drains — no caller input needed.
                            # Guard: only fire if location has NOT already been
                            # confirmed this call (prevents re-asking when the
                            # caller switches from one flow to another).
                            # ── Inline alias detection BEFORE booking ack ──
                            # Scan the full transcript against the complete
                            # _ALCESTER_ALIASES / _REDDITCH_ALIASES sets using
                            # word-boundary matching so that, e.g., "alter"
                            # resolves to alcester but "alternating" does not.
                            # If a location is detected here, v3_location_confirmed
                            # is set before the booking ack branch runs — the ack
                            # branch then skips the location question entirely.
                            #
                            # ALIAS CONFIRMATION LOGIC:
                            # 1. booking_flow_active=True → confirm
                            #    (caller already in booking flow,
                            #     location is for their appointment)
                            # 2. booking_flow_active=False AND
                            #    booking intent in transcript → confirm
                            #    (e.g. "book at Alcester" on first turn)
                            # 3. booking_flow_active=False AND no
                            #    booking intent → candidate only
                            #    (e.g. "disabled bays at Redditch" FAQ)
                            if not self.session.get("v3_location_confirmed"):
                                _n_inline = _normalise_location_text(utterance)
                                _has_alcester = bool(
                                    _ALCESTER_ALIAS_WB_RE.search(_n_inline)
                                )
                                _has_redditch = bool(
                                    _REDDITCH_ALIAS_WB_RE.search(_n_inline)
                                )
                                # Gate: only confirm when booking intent
                                # is present in this transcript OR the
                                # caller is already in the booking flow.
                                _inline_has_intent = (
                                    self.booking_flow_active
                                    or _transcript_has_booking_intent(
                                        utterance
                                    )
                                )
                                # Also confirm directly when the LLM had
                                # just asked "Which clinic?" — the caller's
                                # answer is a direct response to that
                                # question, so no biased re-confirm needed
                                # at booking time.
                                # Comma-strip so clarification variants like
                                # "Just to check — did you mean Awlstuh, or
                                # Redditch?" still match "awlstuh or redditch".
                                # Without this the caller's clinic answer to a
                                # clarification question is treated as a soft
                                # candidate and the location is never confirmed.
                                # Use the pre-run_turn snapshot: by this
                                # point run_turn() has already updated
                                # last_bot_prompt to the current response,
                                # so reading the session key would check
                                # the parking/hours answer rather than the
                                # previous "which clinic?" question.
                                _last_prompt_lower = re.sub(
                                    r",", "",
                                    _pre_turn_last_bot.lower(),
                                )
                                _prev_was_loc_q = any(
                                    kw in _last_prompt_lower
                                    for kw in (
                                        "which clinic",
                                        "which location",
                                        "awlstuh or redditch",
                                        "alcester or redditch",
                                        "alcester or reditch",
                                        "did you mean awlstuh",
                                        "did you mean alcester",
                                        "did you mean redditch",
                                    )
                                )
                                # ── Generic "the clinic" — SOFT alias ────────
                                # Two-tier alias model: STRONG aliases (specific
                                # name mishears — alter/awlstuh/host/redditch)
                                # hard-confirm on intent above. "the clinic" is a
                                # SOFT alias: it names the business generically
                                # and cannot disambiguate two clinics, so it must
                                # NEVER silently default and NEVER trigger the
                                # open "Alcester or Redditch?" question. Note it
                                # as a soft candidate (Alcester = the primary
                                # site) and let the booking-ack soft-candidate
                                # path (~line 8156) ask the BIASED binary confirm
                                # "Just to confirm — was that for our Awlstuh
                                # clinic?" and arm the use-this-clinic yes/no
                                # handler (yes/'use this clinic' → Alcester,
                                # clean 'no' → Redditch). This replaces the
                                # earlier hard-confirm (e6da612) which silently
                                # bound Alcester on "book at the clinic"; the
                                # soft-candidate path is the safer behaviour the
                                # owner signed off (2026-06-12).
                                if (
                                    _GENERIC_CLINIC_ALIAS_RE.search(_n_inline)
                                    and not _has_alcester
                                    and not _has_redditch
                                ):
                                    self.session[
                                        "v3_soft_location_candidate"
                                    ] = "alcester"
                                    logger.info(
                                        "[ms_conn v3] generic 'the clinic' —"
                                        " soft candidate (biased confirm at"
                                        " booking ack): alcester"
                                    )
                                if _has_alcester and not _has_redditch:
                                    if _inline_has_intent or _prev_was_loc_q:
                                        self.session["selected_location"] = (
                                            "alcester"
                                        )
                                        self.session[
                                            "v3_location_confirmed"
                                        ] = True
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        logger.info(
                                            "[ms_conn v3] inline alias"
                                            " detected pre-ack: alcester%s",
                                            " (answered loc Q)"
                                            if _prev_was_loc_q
                                            else "",
                                        )
                                    else:
                                        self.session[
                                            "v3_soft_location_candidate"
                                        ] = "alcester"
                                        logger.info(
                                            "[ms_conn v3] inline alias in"
                                            " FAQ context — candidate"
                                            " noted, not confirmed:"
                                            " alcester"
                                        )
                                elif _has_redditch and not _has_alcester:
                                    if _inline_has_intent or _prev_was_loc_q:
                                        self.session["selected_location"] = (
                                            "redditch"
                                        )
                                        self.session[
                                            "v3_location_confirmed"
                                        ] = True
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        logger.info(
                                            "[ms_conn v3] inline alias"
                                            " detected pre-ack: redditch%s",
                                            " (answered loc Q)"
                                            if _prev_was_loc_q
                                            else "",
                                        )
                                    else:
                                        self.session[
                                            "v3_soft_location_candidate"
                                        ] = "redditch"
                                        logger.info(
                                            "[ms_conn v3] inline alias in"
                                            " FAQ context — candidate"
                                            " noted, not confirmed:"
                                            " redditch"
                                        )

                                # ── FAQ follow-up re-queue ────────────────
                                # If the previous bot turn was "Which clinic?"
                                # (FAQ gate) and the alias just confirmed the
                                # clinic, but this LLM turn produced no
                                # audible TTS (the LLM was confused and
                                # re-asked "Which clinic?", which was
                                # deduplicated) — queue a clean synthetic
                                # utterance so the NEXT turn runs with
                                # location= in CALL STATE and can answer the
                                # original FAQ question (parking/hours/etc.)
                                # without requiring the watchdog to fire.
                                if (
                                    _prev_was_loc_q
                                    and self.session.get(
                                        "v3_location_confirmed"
                                    )
                                    and not self.session.get(
                                        "_turn_speech_emitted"
                                    )
                                ):
                                    _requeue_loc = self.session.get(
                                        "selected_location", "alcester"
                                    )
                                    _requeue_name = (
                                        "Alcester"
                                        if _requeue_loc == "alcester"
                                        else "Redditch"
                                    )
                                    _requeue_utt = (
                                        f"The {_requeue_name} clinic please"
                                    )
                                    self.pending_transcript = _requeue_utt
                                    logger.info(
                                        "[ms_conn v3] FAQ loc Q answered,"
                                        " no TTS emitted this turn"
                                        " — queuing synthetic %r so next"
                                        " turn answers original FAQ with"
                                        " location=%s confirmed",
                                        _requeue_utt,
                                        _requeue_loc,
                                    )

                            # ── First-turn date/time extraction ──────────
                            # Capture time/date preference from this
                            # utterance so the booking flow can skip the
                            # timing question entirely if it was stated
                            # up front.  Only runs before booking starts.
                            if not self.session.get("v3_location_confirmed"):
                                _utt_lower = utterance.lower()
                                _time_pref = None

                                # Day preferences
                                if "today" in _utt_lower:
                                    _time_pref = "today"
                                elif "as soon as possible" in _utt_lower \
                                        or "asap" in _utt_lower \
                                        or "soonest" in _utt_lower \
                                        or "earliest" in _utt_lower:
                                    _time_pref = "as soon as possible"
                                elif "monday" in _utt_lower:
                                    _time_pref = "Monday"
                                elif "tuesday" in _utt_lower:
                                    _time_pref = "Tuesday"
                                elif "wednesday" in _utt_lower:
                                    _time_pref = "Wednesday"
                                elif "thursday" in _utt_lower:
                                    _time_pref = "Thursday"
                                elif "friday" in _utt_lower:
                                    _time_pref = "Friday"
                                elif "tomorrow" in _utt_lower:
                                    _time_pref = "tomorrow"
                                elif "next week" in _utt_lower:
                                    _time_pref = "next week"
                                elif "this week" in _utt_lower:
                                    _time_pref = "this week"

                                # Time of day — appended to day if present
                                _tod = None
                                if "morning" in _utt_lower:
                                    _tod = "morning"
                                elif "afternoon" in _utt_lower:
                                    _tod = "afternoon"
                                elif "evening" in _utt_lower:
                                    _tod = "evening"

                                if _time_pref and _tod:
                                    _time_pref = f"{_time_pref} {_tod}"
                                elif _tod and not _time_pref:
                                    _time_pref = _tod

                                if _time_pref:
                                    _sc = (
                                        self.session.get("soft_context") or {}
                                    )
                                    if not _sc.get("time_preference"):
                                        _sc["time_preference"] = _time_pref
                                        self.session["soft_context"] = _sc
                                        logger.info(
                                            "[ms_conn v3] time_preference"
                                            " extracted: %s",
                                            _time_pref,
                                        )

                            # ── First-turn name extraction ────────────────
                            # If the caller introduces themselves in their
                            # first utterance, capture the name so we don't
                            # ask for it again later.
                            # Patterns: "it's [name]", "I'm [name]",
                            # "my name is [name]", "this is [name]",
                            # "[name] here", "hello it's [name]"
                            # re is already imported at module level (line 43).
                            _name_patterns = [
                                r"\b(?:it[‘’]?s|this is|i[‘’]?m|"
                                r"hello[,\s]+(?:it[‘’]?s)?)\s+"
                                r"([A-Za-z][a-z]{1,20})\b",
                                r"\bmy name is ([A-Za-z][a-z]{1,20})\b",
                                r"^([A-Za-z][a-z]{1,20}) here\b",
                            ]
                            _NOT_NAMES = {
                                "Like", "To", "An", "The", "A",
                                "And", "Book", "Please", "Just",
                                "Really", "Very", "Some", "My",
                                "Your", "Our", "Hi", "Hello",
                                "Yeah", "Yes", "No", "Ok", "Okay",
                                # common false-positive words
                                "Me", "It", "He", "She", "We",
                                "Be", "Do", "Go", "At", "In",
                                "On", "Up", "If", "As", "Is",
                            }
                            _name_found = None
                            for _pat in _name_patterns:
                                _nm = re.search(_pat, utterance, re.I)
                                if _nm:
                                    _candidate = _nm.group(1).capitalize()
                                    if _candidate not in _NOT_NAMES:
                                        _name_found = _candidate
                                        break

                            if _name_found:
                                _sc = (
                                    self.session.get("soft_context") or {}
                                )
                                if not _sc.get("name"):
                                    _sc["name"] = _name_found
                                    self.session["soft_context"] = _sc
                                    logger.info(
                                        "[ms_conn v3] first-turn name"
                                        " extracted: %s", _name_found,
                                    )

                            _V3_ACK_PHRASES = (
                                "right —",                                       # current scripted phrase (short ack)
                                "of course —",                                  # legacy (keep during transition)
                                "of course — let me get that sorted for you",  # legacy long form
                                "of course — i'd be happy to sort that",        # legacy fallback
                                "of course, let's get that moved",
                                "of course — let's get that sorted",
                                "no problem at all",
                                "let me get that sorted",
                            )
                            # Spec P: once booking flow is active, suppress all
                            # further ack detection so mid-flow "Of course —"
                            # responses (e.g. confirming slot, keypad prompt)
                            # cannot re-trigger the handler.
                            # Patience phrase guard: if the LLM responded with
                            # a hold/wait phrase (e.g. "Of course — take your
                            # time.") the caller has not expressed booking
                            # intent — suppress the ack handler entirely.
                            _patience = _is_patience_response(_last_bot)
                            if _patience:
                                logger.info(
                                    "[ms_conn v3] patience response detected"
                                    " — loc Q suppressed: %r",
                                    _last_bot[:80],
                                )
                            # Booking-intent guard: the short "of course —" phrase
                            # is a general affirmation the LLM also uses for FAQ
                            # responses (e.g. "Of course — which clinic?").
                            # Require that EITHER the caller's utterance contained
                            # a booking word OR the previous question Susie asked
                            # already contained booking context (handles "yeah" /
                            # "yes please" responses to "would you like to book?").
                            _caller_booking_words = re.search(
                                r"\b(?:book|booking|appointment|reschedule"
                                r"|cancel|move|change)\b",
                                utterance, re.IGNORECASE,
                            )
                            _last_q_lower = (
                                self.session.get("last_question") or ""
                            ).lower()
                            _prev_q_booking = any(
                                w in _last_q_lower
                                for w in ("book", "appointment", "sort that",
                                          "get that sorted", "would you like")
                            )
                            # Also detect caller affirming an LLM-generated
                            # booking CTA ("yes please" → "Would you like to
                            # book?").  _pre_turn_last_bot holds the bot
                            # response from before run_turn() was called
                            # (run_turn() overwrites last_bot_prompt with the
                            # current turn's reply, so we must use the snapshot).
                            # CTA-affirm detection — computed unconditionally
                            # so _cta_affirm is available to _is_booking_ack
                            # even when _prev_q_booking is already set.  Reads
                            # the FULL previous reply (not the [:200]-truncated
                            # last_bot_prompt) so a CTA at the tail of a long
                            # clinical response is still seen.
                            _prev_bot_lower = (
                                _pre_turn_last_bot_full
                                or _pre_turn_last_bot
                                or ""
                            ).lower()
                            _CTA_BOOKING_PHRASES = (
                                "would you like to book",
                                "book an appointment",
                                "book an assessment",
                                "like to make an appointment",
                                "shall i book",
                                "book you in",
                            )
                            _bot_had_cta = any(
                                p in _prev_bot_lower
                                for p in _CTA_BOOKING_PHRASES
                            )
                            # Affirm detection is split strong vs weak.  Strong
                            # tokens (yes/yeah/sure/…) are unambiguous.  Weak
                            # tokens ("i do", "i would", "course") also occur
                            # INSIDE wh-questions and distress phrases — e.g.
                            # "what do I do?" contains "\bi do\b".  A weak-only
                            # match inside a wh-question is NOT a booking
                            # affirmation, so it is discarded; strong tokens are
                            # left fully intact (so "yes, when can I come in?"
                            # still affirms).  (Call 6, 2026-06-18: an emergency
                            # "…might have broken my hip, what do I do, what do I
                            # do" matched "i do" and, with the prior "would you
                            # like to book one?" CTA, falsely fired a booking ack
                            # → location pivot right after the 999/A&E message.)
                            _utt_strong_affirm = bool(re.search(
                                r"\b(?:yes|yeah|yep|sure|okay|ok|yup"
                                r"|absolutely|definitely|go ahead)\b",
                                utterance, re.IGNORECASE,
                            ))
                            _utt_weak_affirm = bool(re.search(
                                r"\b(?:i would|i do|course)\b",
                                utterance, re.IGNORECASE,
                            ))
                            _utt_is_wh_question = bool(re.search(
                                r"\b(?:what|how|why|where|when|which)\b",
                                utterance, re.IGNORECASE,
                            ))
                            _utt_is_affirm = (
                                _utt_strong_affirm
                                or (_utt_weak_affirm and not _utt_is_wh_question)
                            )
                            _cta_affirm = _bot_had_cta and _utt_is_affirm
                            if _cta_affirm and not _prev_q_booking:
                                _prev_q_booking = True
                                logger.info(
                                    "[ms_conn v3] CTA affirm:"
                                    " prev_bot had booking CTA,"
                                    " caller=%r — booking context set",
                                    utterance[:60],
                                )
                            # Recovery fires when EITHER:
                            #  (a) the caller's CURRENT utterance contains a
                            #      booking word AND the current reply carries a
                            #      scripted ack phrase ("of course —"), OR
                            #  (b) we have a strong CTA-affirm signal (prev bot
                            #      offered to book + caller affirmed).
                            # The ack-phrase arm (a) deliberately requires
                            # _caller_booking_words (a booking verb in THIS
                            # utterance) rather than the broad
                            # _caller_has_booking_context.  Reason: once any CTA
                            # has been offered, _prev_q_booking stays True for the
                            # rest of the call (last_question still holds "…book
                            # one?"), so _caller_has_booking_context is True on
                            # every later turn.  Sonnet opens many unrelated
                            # replies (FAQ answers, "could you repeat that")
                            # with "Of course —", and _last_bot reads the raw
                            # pre-gate5 text from conversation_history (gate5
                            # strips the banned opener only from TTS, not from
                            # history) — so the ack phrase is present even when
                            # the caller never heard it.  Gating the ack arm on a
                            # booking word in the live utterance stops these
                            # non-booking turns from hijacking the location flow.
                            # The CTA-affirm arm (b) keeps its own independent
                            # signal so "yes please" → CTA still works.
                            _is_booking_ack = (
                                not _patience
                                and (
                                    (
                                        # Normal sentinel arm: only when flow
                                        # hasn't started and location not asked
                                        not self.booking_flow_active
                                        and not self.session.get(
                                            "v3_location_asked", False
                                        )
                                        and _caller_booking_words
                                        and any(
                                            p in _last_bot.lower()
                                            for p in _V3_ACK_PHRASES
                                        )
                                    )
                                    or (
                                        # CTA-affirm arm: explicit yes to a
                                        # booking offer — bypasses flow-state
                                        # gates (treatment detection can set
                                        # booking_flow_active=True prematurely,
                                        # blocking this path).  Guards: not
                                        # mid-slot-selection, not post-booking.
                                        _cta_affirm
                                        and not self.session.get(
                                            "v3_awaiting_slot_selection"
                                        )
                                        and not (
                                            self.session.get("acuity_booking_id")
                                            or self.session.get("booking_id")
                                            or self.session.get(
                                                "calendar_status"
                                            ) == "created"
                                        )
                                    )
                                )
                            )
                            if _is_booking_ack:
                                # ── end Spec Y (normal ack path) ──────────────
                                self.booking_flow_active = True
                                self.session["booking_flow_active"] = True
                                logger.info("[ms_conn] booking_flow_active = True")
                                self.session["v3_booking_intent"] = True
                                # Store which intent triggered the ack
                                if "let's get that moved" in _last_bot.lower():
                                    self.session["v3_caller_intent"] = (
                                        "reschedule"
                                    )
                                elif "no problem at all" in _last_bot.lower():
                                    self.session["v3_caller_intent"] = "cancel"
                                else:
                                    self.session["v3_caller_intent"] = "booking"
                                # ── FAQ-session bridge filler ──────────────────
                                # When the caller transitions from a long FAQ
                                # session to booking (q_gen ≥ 5), play a short
                                # filler phrase immediately after the booking ack
                                # so they hear a warm bridge while Susie switches
                                # modes.  Without this, long context windows can
                                # produce a 1-2 s gap between the LLM's ack
                                # phrase and the first booking question.
                                _faq_q_gen = self._silence_handler._q_gen
                                if _faq_q_gen >= 5:
                                    await self.tts_text_queue.put(
                                        "Let me get that sorted for you."
                                    )
                                    _v3_post_turn_speech = True
                                    logger.info(
                                        "[ms_conn v3] booking ack filler"
                                        " — FAQ session detected q_gen=%d",
                                        _faq_q_gen,
                                    )
                                # ── end FAQ-session bridge filler ─────────────
                                # Booking ack detected — advance to next question.
                                # If location already confirmed, skip location Q
                                # and go straight to new/returning.
                                if self.session.get("v3_location_confirmed"):
                                    _loc = self.session.get(
                                        "selected_location", "alcester"
                                    )
                                    _loc_display = _loc.capitalize()
                                    _intent = self.session.get(
                                        "v3_caller_intent", "booking"
                                    )
                                    if _intent in ("reschedule", "cancel"):
                                        _next_q = (
                                            "Is the number you're calling "
                                            "on the one associated with "
                                            "your booking? If so, just "
                                            "say 'use this number'."
                                        )
                                        self.session[
                                            "v3_awaiting_phone_confirm"
                                        ] = True
                                    else:
                                        # Always ask the timing question at the
                                        # booking ack.  We deliberately do NOT
                                        # skip it when a soft time_preference
                                        # already exists, because:
                                        #  (1) the old skip path re-queued the
                                        #      pref as a 2-tuple transcript, which
                                        #      the C8-2 location-ack guard armed
                                        #      ~80 lines below then dropped (it
                                        #      lacked synthetic=True) → the ack
                                        #      said "Let me get that sorted" then
                                        #      stalled → dead air / abandon; and
                                        #  (2) soft_context["time_preference"] is
                                        #      populated from ANY utterance incl.
                                        #      FAQs (e.g. "are you open Easter
                                        #      Monday"), so it is frequently a
                                        #      phantom the caller never stated for
                                        #      this booking.
                                        # Asking is safe; the caller confirms the
                                        # timing in one breath.  Clear the stale
                                        # pref so their fresh answer wins cleanly
                                        # (soft_context is otherwise first-wins).
                                        _sc2 = self.session.get("soft_context")
                                        if isinstance(_sc2, dict) and _sc2.get(
                                            "time_preference"
                                        ):
                                            logger.info(
                                                "[ms_conn v3] cleared stale soft"
                                                " time_preference (%r) at booking"
                                                " ack — will ask timing Q",
                                                _sc2.get("time_preference"),
                                            )
                                            _sc2.pop("time_preference", None)
                                        _next_q = (
                                            "Is there a particular day"
                                            " or time that works best"
                                            " for you?"
                                        )
                                    # ── Suppress timing Q if slots already
                                    # presented this turn (e.g. inline booking
                                    # ack where check_availability ran in the
                                    # same LLM turn).  The slot buffer CTA
                                    # ("Any of those suit you?") already
                                    # invites a response — queuing the timing
                                    # preference question on top would fire
                                    # two questions simultaneously.
                                    if (
                                        _next_q is not None
                                        and self.session.get(
                                            "v3_awaiting_slot_selection"
                                        )
                                    ):
                                        logger.info(
                                            "[ms_conn v3] timing Q suppressed"
                                            " — slots already presented"
                                            " this turn"
                                        )
                                        _next_q = None
                                    # ── Suppress timing Q if LLM response
                                    # already contains a question this turn.
                                    # Prevents two open questions firing back
                                    # to back (e.g. LLM asks clinic question
                                    # AND ack queues timing preference Q).
                                    if (
                                        _next_q is not None
                                        and "?" in _last_bot
                                    ):
                                        logger.info(
                                            "[ms_conn v3] timing Q suppressed"
                                            " — LLM response already contains"
                                            " a question this turn"
                                        )
                                        _next_q = None
                                    if _next_q is not None:
                                        await self.tts_text_queue.put(_next_q)
                                        _v3_post_turn_speech = True
                                        self.session[
                                            "last_bot_prompt"
                                        ] = _next_q
                                        self.session[
                                            "last_question"
                                        ] = _next_q
                                        # Inject into conversation_history so
                                        # the LLM has context on next turn.
                                        self.session.setdefault(
                                            "conversation_history", []
                                        ).append({
                                            "role": "assistant",
                                            "content": _next_q,
                                        })
                                    await save_session(
                                        self.call_sid, self.session
                                    )
                                    self.session["v3_booking_intent"] = False
                                    # C8-2: mark this turn as having ack'd the
                                    # location so a phantom second final from the
                                    # same breath is dropped at dequeue rather
                                    # than firing a redundant Sonnet turn.
                                    self.session["location_acked_this_turn"] = True
                                    self._location_ack_ts = time.monotonic()
                                    logger.info(
                                        "[ms_conn v3] booking ack — location "
                                        "known (%s), intent=%s, queued next Q",
                                        _loc,
                                        _intent,
                                    )
                                else:
                                    # Location unknown — queue intent-aware
                                    # location question.
                                    # If the caller mentioned a specific clinic
                                    # during this FAQ session (v3_soft_location_
                                    # candidate is set), use an immediate biased
                                    # confirm rather than the open two-choice
                                    # question.  This avoids asking "which clinic?"
                                    # when e.g. the caller just asked about
                                    # parking at Alcester then said they want to
                                    # book.  The caller answers yes/no once and
                                    # we move straight to timing.
                                    _loc_intent = self.session.get(
                                        "v3_caller_intent", "booking"
                                    )
                                    _soft_cand = self.session.get(
                                        "v3_soft_location_candidate"
                                    )
                                    if _loc_intent in ("reschedule", "cancel"):
                                        _loc_q = (
                                            "Was your original appointment "
                                            "at our Awlstuh or Redditch "
                                            "clinic?"
                                        )
                                    elif _soft_cand:
                                        # Caller named a clinic during the
                                        # call — ask a targeted confirmation
                                        # rather than the open two-choice Q.
                                        _cand_disp = (
                                            "Awlstuh"
                                            if _soft_cand == "alcester"
                                            else "Redditch"
                                        )
                                        # Shared rung-2 copy (parametrised by
                                        # clinic) so a no-input re-ask reads as
                                        # the SAME question the watchdog speaks,
                                        # and tells the caller how to answer.
                                        _loc_q = _loc_rung2_confirm(_cand_disp)
                                        # Arm the biased yes/no handler so
                                        # the caller's 'yes' immediately
                                        # confirms the candidate.
                                        self.session[
                                            "v3_awaiting_use_this_clinic"
                                        ] = True
                                        self.session[
                                            "v3_use_this_clinic_bias"
                                        ] = _soft_cand
                                        logger.info(
                                            "[ms_conn v3] soft candidate '%s'"
                                            " — biased confirm Q at booking"
                                            " ack",
                                            _soft_cand,
                                        )
                                    else:
                                        _loc_q = _LOC_RUNG1_OPEN
                                    if _loc_q is not None:
                                        # Only queue to TTS if the LLM didn't
                                        # already ask the location question in
                                        # its reply — prevents double-ask when
                                        # the model ignores the "stop after
                                        # ack" instruction and includes the
                                        # question.
                                        _llm_asked_loc = any(
                                            kw in _last_bot.lower()
                                            for kw in (
                                                "which clinic",
                                                "alcester or redditch",
                                                "alcester or reditch",
                                                "original appointment at",
                                            )
                                        )
                                        if not _llm_asked_loc:
                                            await self.tts_text_queue.put(
                                                _loc_q
                                            )
                                            _v3_post_turn_speech = True
                                        # Always set session flags so the
                                        # location gate arms correctly
                                        # regardless of whether we queued TTS.
                                        self.session[
                                            "last_bot_prompt"
                                        ] = _loc_q
                                        self.session[
                                            "last_question"
                                        ] = _loc_q
                                        self.session[
                                            "v3_location_asked"
                                        ] = True
                                        self.session[
                                            "v3_location_q_active"
                                        ] = True
                                        self.session[
                                            "_location_q_patient_spoke"
                                        ] = False
                                        # Add injected location Q to
                                        # conversation history so the LLM
                                        # knows what was asked if the caller's
                                        # response bypasses the intercept
                                        # handler and reaches run_turn().
                                        if not _llm_asked_loc:
                                            self.session.setdefault(
                                                "conversation_history", []
                                            ).append({
                                                "role": "assistant",
                                                "content": _loc_q,
                                            })
                                            self._silence_handler\
                                                .on_question_asked(_loc_q)
                                        await save_session(
                                            self.call_sid, self.session
                                        )
                                        logger.info(
                                            "[ms_conn v3] booking ack"
                                            " detected — intent=%s, loc Q %s",
                                            _loc_intent,
                                            "suppressed (LLM already asked)"
                                            if _llm_asked_loc
                                            else "queued",
                                        )

                        # ── CODE SPEC AD: treatment bypass clinic question arm ────
                        # When the treatment bypass fires pre-run_turn,
                        # booking_flow_active is set True before run_turn()
                        # executes.  This means _is_booking_ack is always False
                        # on these turns (it gates on `not booking_flow_active`),
                        # so v3_location_q_active is never armed by the booking
                        # ack path — even though the LLM asks the clinic question
                        # in its Prompt L response.
                        # Fix: after run_turn, scan _last_bot for clinic-question
                        # signals.  If found while v3_treatment_mentioned is True
                        # and location is not yet confirmed, arm the gate so the
                        # patient's next utterance is intercepted by the location
                        # handler (alias resolution, DTMF fallback, preference Q)
                        # exactly as in a normal booking flow.
                        _treatment_loc_signals = (
                            "which clinic",
                            "awlstuh or redditch",
                            "alcester or redditch",
                            "alcester or reditch",
                            "which location",
                        )
                        if (
                            self.session.get("v3_treatment_mentioned")
                            and not self.session.get("v3_location_q_active")
                            and not self.session.get("v3_location_confirmed")
                            and any(
                                sig in _last_bot.lower()
                                for sig in _treatment_loc_signals
                            )
                        ):
                            self.session["v3_location_q_active"] = True
                            self.session["v3_location_asked"] = True
                            self.session["_location_q_patient_spoke"] = False
                            await save_session(self.call_sid, self.session)
                            logger.info(
                                "[ms_conn v3] v3_location_q_active = True "
                                "(clinic question detected in treatment bypass response)"
                            )

                        # ── B2: deferred gate5 fallback emission ─────────────
                        # gate5 (llm_stream) deferred its empty-response fallback
                        # to here so it never races ahead of the post-turn
                        # recovery path above.  Emit it ONLY if no post-turn
                        # recovery path queued any speech (_v3_post_turn_speech)
                        # AND no synthetic continuation was queued
                        # (pending_transcript).  Note: the v3 tts_text_queue is a
                        # plain asyncio.Queue (not _TrackedQueue), so
                        # _turn_speech_emitted is NOT used here — _v3_post_turn_speech
                        # is the authoritative signal for post-run_turn puts.
                        _g5_pending = self.session.pop(
                            "_gate5_fallback_pending", None
                        )
                        if _g5_pending:
                            if (
                                _v3_post_turn_speech
                                or self.pending_transcript is not None
                            ):
                                logger.info(
                                    "[ms_conn v3] deferred gate5 fallback"
                                    " suppressed — turn recovered (%s)",
                                    "post-turn speech"
                                    if _v3_post_turn_speech
                                    else "synthetic re-queue",
                                )
                            else:
                                await self.tts_text_queue.put(_g5_pending)
                                logger.info(
                                    "[ms_conn v3] deferred gate5 fallback"
                                    " emitted — no recovery this turn"
                                )

                        # ── Watchdog re-arm (both gate-fired and normal) ─────
                        # Silence recovery needs last_question in all cases.
                        _last_q = self.session.get("last_question", "")
                        if _last_q and self.session.get("call_outcome") is None:
                            self._silence_handler.set_state(
                                self.session.get("state", "default")
                            )
                            self._silence_handler.on_question_asked(_last_q)

                        if not self._call_stable:
                            self._call_stable = True
                            logger.info(
                                "[ms_conn v3] call reached stable state"
                            )

                        # If a tool call set call_outcome (booked/transferred),
                        # the call is winding down — exit the loop cleanly.
                        if self.session.get("call_outcome") is not None:
                            logger.info(
                                "[ms_conn v3] call_outcome set (%s) — "
                                "loop exiting",
                                self.session.get("call_outcome"),
                            )
                            break

                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error(
                            "[ms_conn v3] turn error: %r\n%s",
                            exc, traceback.format_exc(),
                        )
                        await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)
                    finally:
                        self._last_turn_done_at = time.monotonic()
                        self.llm_in_flight = False   # Spec N: clear before re-queue check
                        self._llm_busy = False
                        self._silence_handler.on_llm_finished()
                        self.session["llm_generation_active"] = False
                        await save_session(self.call_sid, self.session)
                        # Spec N — re-process any pending transcript that arrived
                        # while the turn was in-flight.  Fresh timestamp so it is
                        # never discarded by the stale-transcript flush.
                        if self.pending_transcript is not None:
                            _queued_utt = self.pending_transcript
                            self.pending_transcript = None
                            logger.info(
                                "[ms_conn v3] LLM free — processing queued transcript: %r",
                                _queued_utt[:60],
                            )
                            await self.transcript_queue.put((time.monotonic(), _queued_utt))

            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("[ms_conn v3] _llm_loop fatal: %r", exc)

            return  # CRITICAL: do not fall through to FlowEngine path

        # FlowEngine path — theorem and theorem_v2
        # DO NOT CHANGE ANYTHING INSIDE THIS BLOCK
        from .llm_stream import LLMStream
        from .flow import FlowEngine

        llm = LLMStream()

        # Build the LLM callable the flow engine will use for LLM steps.
        # It streams output directly to tts_text_queue and returns full text.
        async def _llm_fn(instruction: str, allow_tools: bool = True, error_phrase: str = None) -> str:
            result = await llm.run_instruction(
                instruction=instruction,
                session=self.session,
                tts_text_queue=self.tts_text_queue,
                call_sid=self.call_sid,
                stream_sid=self.stream_sid,
                audio_out_queue=self.audio_out_queue,
                websocket=self.websocket,
                on_transfer=self._on_transfer_request,
                allow_tools=allow_tools,
                error_phrase=error_phrase,
            )
            # Mark that LLM produced audible speech this turn so the global
            # hard-fallback in the outer loop does not fire a duplicate response.
            if result and result.strip():
                self.session["_turn_speech_emitted"] = True
            return result

        flow = FlowEngine(
            session=self.session,
            tts_queue=self.tts_text_queue,
            llm_fn=_llm_fn,
        )
        self._flow = flow

        try:
            while not self._stop_event.is_set():
                try:
                    _raw_item = await asyncio.wait_for(
                        self.transcript_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # Unpack timestamped item from queue
                if isinstance(_raw_item, tuple):
                    _enqueue_ts, utterance = _raw_item
                else:
                    _enqueue_ts, utterance = 0.0, _raw_item  # legacy safety

                # Discard stale transcripts enqueued before the last confirmed
                # barge-in — these are phantom STT finals from a burst that
                # fired before the caller finished interrupting.
                if _enqueue_ts < self._barge_in_flush_before:
                    logger.info(
                        "[ms_conn] stale transcript discarded (pre-barge-in): %r",
                        utterance[:80],
                    )
                    continue

                if not utterance or not utterance.strip():
                    continue

                # Safety net anchor: accepted transcript keeps dead-air guard at bay.
                self._last_audio_or_transcript_ts = time.monotonic()
                # Caller spoke — reset watchdog reask_completed so safety net
                # can fire again if the next turn goes silent.
                self._silence_handler._reask_completed = False

                # Drop if the previous turn is still generating
                if self._llm_busy:
                    logger.info(
                        "[ms_conn] busy — dropping utterance: %r", utterance[:80],
                    )
                    continue

                # ── Tail-fragment guard ───────────────────────────────────────
                # Drop tiny residual STT finals that arrive immediately after a
                # successfully handled turn (e.g. "ic" trailing "alcester clin").
                # Conditions (all must be true):
                #   1. A turn has completed (_last_turn_done_at is set)
                #   2. Fragment arrived within _TAIL_FRAGMENT_WINDOW seconds
                #   3. Text is ≤ 3 chars (sub-word — cannot be a real answer)
                #   4. Not a whitelisted valid short answer ("no", "yes", "ok"…)
                # This is a true no-op: no state change, no silence/watchdog effect.
                _tf_text  = utterance.strip()
                _tf_since = time.monotonic() - self._last_turn_done_at
                if (
                    self._last_turn_done_at > 0
                    and _tf_since < _TAIL_FRAGMENT_WINDOW
                    and len(_tf_text) <= 3
                    and _tf_text.lower() not in _TAIL_FRAGMENT_SAFE
                ):
                    # ── ASK_LOCATION split-final stitch recovery ──────────────
                    # Before dropping a tiny tail fragment, check whether the
                    # flow is waiting on a clinic answer AND just failed to
                    # resolve a prior adjacent final.  STT can split a clearly
                    # spoken "the Alcester clinic" into two finals
                    # ("your author" + "ity" ≈ "your authority"); suppressing
                    # the tail destroys the answer and forces an ASK_LOCATION
                    # retry.  If a recent stitch candidate is available, merge
                    # the two transcripts and re-enter the flow with the
                    # stitched text — the flow's extractor / resolver gets a
                    # second chance on a richer utterance.  If that still
                    # fails, flow.py clears the marker and normal retry logic
                    # resumes on the next turn.
                    _stitch = self.session.get("_loc_stitch_pending") or {}
                    _stitch_text = str(_stitch.get("text") or "").strip()
                    _stitch_ts   = float(_stitch.get("ts") or 0.0)
                    _stitch_age  = time.monotonic() - _stitch_ts
                    if (
                        self.session.get("needs_location")
                        and _stitch_text
                        and _stitch_ts > 0
                        and _stitch_age <= 1.5
                    ):
                        # Build candidate stitched transcripts — the STT
                        # fragmentation case is tail-glued (no space), but
                        # we also try a space-separated form in case the
                        # fragment is a discrete word.  Flow.py will run
                        # its extractor/resolver on whichever we forward.
                        _stitched_glued  = (_stitch_text + _tf_text).strip()
                        _stitched_spaced = (_stitch_text + " " + _tf_text).strip()
                        logger.info(
                            "[ms_conn] stitch_attempt prior=%r tail=%r "
                            "candidates=[%r, %r] age=%.2fs",
                            _stitch_text[:60], _tf_text,
                            _stitched_glued[:80], _stitched_spaced[:80],
                            _stitch_age,
                        )
                        # Forward the glued variant (covers the observed
                        # "your author"+"ity" → "your authority" case) and
                        # mark session so flow.py knows this is a stitched
                        # re-entry (prevents infinite re-stitching).
                        self.session["_loc_stitch_from_merge"] = True
                        self.session.pop("_loc_stitch_pending", None)
                        utterance = _stitched_glued
                        logger.info(
                            "[ms_conn] stitch_forward replacing tail fragment "
                            "with stitched utterance %r (ASK_LOCATION recovery)",
                            utterance[:80],
                        )
                        # Fall through — don't continue/suppress.
                    else:
                        logger.info(
                            "[ms_conn] tail-fragment suppressed %r (%.2fs after last turn) — no-op",
                            _tf_text, _tf_since,
                        )
                        continue

                # ── Barge-in resolution ───────────────────────────────────────
                # Must run before setting _llm_busy so:
                #   - false triggers resume TTS without entering the flow
                #   - confirmed barge-ins queue an ack and wait for next utterance
                if await self._resolve_barge_in(utterance):
                    continue

                # A real utterance is being processed — barge-in recovery complete.
                self._in_barge_in_recovery = False
                self._llm_busy          = True
                self._silence_handler.on_llm_started()
                self._last_audio_at     = time.monotonic()
                self.session["llm_generation_active"] = True
                # New turn begins — allow TTS output for this response (Bug 5).
                self.session["tts_inhibit"] = False
                await save_session(self.call_sid, self.session)

                logger.info("[ms_conn] transcript received: %r", utterance[:120])

                try:
                    # ── Pause detection (before state machine) ─────────────────────
                    # If the caller said "hang on", "one sec", etc., enter pause mode.
                    # Do NOT pass utterance to the flow — do NOT advance state.
                    from app.pause_detector import detect_caller_pause_request as _detect_pause
                    _words = utterance.strip().split()
                    _is_pause = _detect_pause(utterance)
                    _is_substantive = len(_words) > 2 and not _is_pause

                    if _is_pause:
                        self.session["caller_pause_active"] = True
                        self.session["pause_silence_total"] = 0.0
                        await self.tts_text_queue.put("Take your time.")
                        # Don't pass to state machine; silence timer will use 45s window
                        # We still need to re-arm the silence handler after speaking.
                        # NOTE: on_question_asked bumps _q_gen, so bind caller_pause_q_gen
                        # to the POST-rearm value (otherwise the stale-pause guard in
                        # on_question_asked would immediately clear the pause we just set).
                        self._silence_handler.on_question_asked(self.session.get("last_question", ""))
                        _pause_q_gen = getattr(self._silence_handler, "_q_gen", 0)
                        _pause_state = self.session.get("state", "")
                        self.session["caller_pause_q_gen"] = _pause_q_gen
                        self.session["caller_pause_state"] = _pause_state
                        await save_session(self.call_sid, self.session)
                        logger.info(
                            "[ms_pause] set: reason=caller_requested_pause state=%s q_gen=%d",
                            _pause_state, _pause_q_gen,
                        )
                        # Fall through to finally: clears _llm_busy
                    else:
                        # Clear pause mode if caller resumes with a substantive utterance
                        if _is_substantive and self.session.get("caller_pause_active"):
                            self.session["caller_pause_active"] = False
                            self.session["pause_silence_total"] = 0.0
                            self.session.pop("caller_pause_q_gen", None)
                            self.session.pop("caller_pause_state", None)
                            logger.info("[ms_pause] cleared: reason=caller_substantive_utterance")

                    # Record utterance for tone detection (first two turns lock the tone)
                    if not _is_pause:
                        try:
                            from app.tone_detector import ToneDetector as _ToneDetector
                            _td = self.session.get("tone_detector")
                            if not isinstance(_td, _ToneDetector):
                                _td = _ToneDetector.from_dict(self.session.get("_tone_state") or {})
                            _td.record_utterance(utterance)
                            self.session["_tone_state"] = _td.to_dict()
                        except Exception as _td_err:
                            logger.warning("[ms_conn] ToneDetector record failed: %r", _td_err)

                    if not _is_pause:
                        # BUG 1 fix — clear stale LLM reply before each transcript so
                        # post-turn diagnostic log always reflects the NEW bot output
                        self.session["last_bot_prompt"] = ""
                        # Reset per-turn speech-emission flag.  _TrackedQueue and _llm_fn
                        # both set this True whenever audible text is enqueued.
                        self.session["_turn_speech_emitted"] = False
                        if not self.session.get("flow_started"):
                            # First caller utterance — detect intent then kick off the flow.
                            self.session["flow_started"] = True
                            logger.info("[ms_conn] flow start — first utterance: %r", utterance[:80])
                            await self.tts_text_queue.put("\x00DEDUP_RESET\x00")
                            await flow.handle_transcript(utterance)
                        else:
                            logger.info(
                                "[ms_conn] flow transcript: %r  step=%s",
                                utterance[:80], self.session.get("flow_step", 0),
                            )
                            await self.tts_text_queue.put("\x00DEDUP_RESET\x00")
                            await flow.handle_transcript(utterance)

                        # ── GLOBAL HARD FALLBACK ──────────────────────────────────────
                        # If handle_transcript completed without producing any audible
                        # speech, and the turn is not already handled by a deferred
                        # path (repair / repeat / fragment / transfer / graceful exit),
                        # emit a recovery phrase + the current live re-anchor question.
                        # This is the last-resort guarantee that no turn is ever silent.
                        #
                        # BLOCK in structured deterministic collection / confirmation
                        # states.  Those states own their recovery path: the watchdog
                        # fires the state-specific re-ask after 3 s of quiet.  Letting
                        # the generic blended fallback speak here produces pilot-bad
                        # wording ("I can't answer that properly right now") inside a
                        # deterministic booking flow, and double-fires on scaffold-hold
                        # turns (scaffold_continue sets _nc_scaffold_hold and returns
                        # silently; the timer re-arm runs AFTER this block, but the
                        # fallback would already have spoken first).
                        _STRUCTURED_STATES_NO_FB = frozenset({
                            "ASK_LOCATION",
                            "COLLECT_NAME",            "COLLECT_NAME_RETURNING",
                            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                            "CONFIRM_PHONE",           "CONFIRM_PHONE_RETURNING",
                            "PRESENT_DAYS",            "PRESENT_DAYS_RESCHEDULE",
                            "PRESENT_TIMES",           "PRESENT_TIMES_RESCHEDULE",
                            "CONFIRM_BOOKING",
                            "COLLECT_PHONE",           "COLLECT_PHONE_RETURNING",
                            "COLLECT_PHONE_RESCHEDULE",
                            "COLLECT_REASON",          "CONFIRM_ASSESSMENT",
                            "LOOKUP_RESCHEDULE",       "LOOKUP_CANCEL",
                        })
                        _turn_state = self.session.get("state", "")
                        _turn_silent = (
                            not self.session.get("_turn_speech_emitted")
                            and not self.session.get("repair_requested")
                            and not self.session.get("repeat_requested")
                            and not self.session.get("fragment_suppressed")
                            and not self.session.get("request_transfer")
                            and not self.session.get("graceful_exit")
                            and not flow.is_complete()
                            and _turn_state not in _STRUCTURED_STATES_NO_FB
                        )
                        if _turn_silent:
                            _fallback_lq = self.session.get("last_question", "")
                            _fallback_text = (
                                "Sorry, I can\u2019t answer that properly right now, "
                                "but I can still help you continue."
                            )
                            if _fallback_lq:
                                _fallback_text += f" {_fallback_lq}"
                            await self.tts_text_queue.put(_fallback_text)
                            self.session["last_question"] = _fallback_text
                            logger.warning(
                                "[ms_conn] GLOBAL HARD FALLBACK: no speech this turn "
                                "(state=%s) — emitting: %r",
                                self.session.get("state", "?"), _fallback_text[:100],
                            )

                    # ── Transfer check (deterministic flow path) ─────────────
                    # The LLM stream handles transfers that fire via tool call.
                    # The deterministic transfer path (intent=transfer in flow.py)
                    # sets request_transfer=True but bypasses the LLM stream entirely,
                    # so we must check here and fire the Twilio transfer directly.
                    if self.session.get("request_transfer"):
                        logger.info("[ms_conn] deterministic transfer flag detected — firing")
                        self.session["request_transfer"] = False
                        await self._on_transfer_request()

                    if not self._call_stable:
                        self._call_stable = True
                        logger.info("[ms_conn] call reached stable state")

                    # Diagnostic: log what the LLM last said and what question was stored
                    _llm_reply = self.session.get("last_bot_prompt", "")
                    if _llm_reply:
                        logger.info("[ms_conn] LLM response: %r", _llm_reply[:200])
                    _last_q = self.session.get("last_question", "")
                    if _last_q:
                        logger.info("[ms_conn] last_question stored: %r", _last_q[:120])
                        if flow.is_complete():
                            # Flow is done — do NOT re-arm silence handler.
                            # Also zero the handler's stored question so the silence
                            # timer cannot fire a stale re-ask after the flow completes.
                            self._silence_handler.last_question = ""
                            logger.info(
                                "[ms_conn] flow complete — silence handler cleared "
                                "(stale question suppressed: %r)", _last_q[:80]
                            )
                        else:
                            # Watchdog eligibility gate — declarative text (e.g.
                            # deterministic FAQ answer) is stored in last_question
                            # so repeat/recovery can replay it, but the no-input
                            # watchdog must NOT re-ask it. _store_last_question
                            # marks such text via _last_question_not_reaskable;
                            # when current last_question matches the marker we
                            # skip arming entirely. Any future real question
                            # overwrites last_question → marker mismatches →
                            # watchdog arms normally again.
                            _nonreaskable = self.session.get("_last_question_not_reaskable", "")
                            _watchdog_eligible = not (_nonreaskable and _nonreaskable == _last_q)
                            self._silence_handler.set_state(
                                self.session.get("state", "default")
                            )
                            if _watchdog_eligible:
                                self._silence_handler.on_question_asked(_last_q)
                            else:
                                # Clear handler's stored question so no stale prior
                                # prompt gets replayed either — the current stored
                                # text is declarative and owns the repeat-path only.
                                self._silence_handler.last_question = ""
                                logger.info(
                                    "[ms_conn] watchdog NOT armed — last_question is "
                                    "non-reaskable (declarative): %r", _last_q[:80],
                                )
                            # ── Stale-lifecycle repair: force-refresh canonical question ──
                            # on_question_asked() routes through the heuristic
                            # _is_question_worth_storing filter, which rejects ANY text
                            # containing "sorry, i didn't quite catch" — a phrase that
                            # legitimately prefixes every flow-emitted retry / DTMF
                            # prompt (ASK_LOCATION tier-2, COLLECT_PHONE keypad fallback,
                            # PRESENT_DAYS re-anchor, etc.).  Without this refresh, the
                            # silence handler's last_question and _q_gen remained pinned
                            # to the ORIGINAL question, so the no-input watchdog would
                            # later re-ask the stale original wording instead of the
                            # active tier's wording, and stale-generation guards could
                            # not retire the prior watchdog cleanly.
                            #
                            # Session["last_question"] is authoritative (flow owns it),
                            # so when it diverges from the handler's stored text we
                            # unconditionally overwrite, bump _q_gen, reset re-ask
                            # counters, and restart the timer.  The restart cancels
                            # any stale W1 task and re-arms the watchdog bound to the
                            # new _q_gen; the previous watchdog (if still live) will
                            # abort at its next iteration via the existing stale-q_gen
                            # guard.  No-op when session and handler already agree.
                            _lq_handler = self._silence_handler.last_question
                            if _watchdog_eligible and _last_q and _last_q != _lq_handler:
                                self._silence_handler.last_question         = _last_q
                                self._silence_handler.reask_count           = 0
                                self._silence_handler._no_input_reask_count = 0
                                self._silence_handler._last_question_set_at = time.time()
                                self._silence_handler._q_gen               += 1
                                self._silence_handler._restart_timer()
                                logger.info(
                                    "[ms_conn] last_question force-refreshed "
                                    "(filter bypass) q_gen=%d new=%r old=%r",
                                    self._silence_handler._q_gen,
                                    _last_q[:70], (_lq_handler or "")[:70],
                                )
                            # ── No-dead-state guarantee ──────────────────────────
                            # If the flow consumed the transcript without emitting
                            # TTS (filler suppression, fragment_suppressed, any
                            # silent no-op path), the normal on_tts_finished →
                            # _restart_timer chain never fires.  on_transcript_received
                            # already cancelled the watchdog when the transcript
                            # arrived, so without this explicit re-arm the state
                            # would sit with NO watchdog and NO TTS → dead state.
                            # Arming here is idempotent: if TTS was emitted, the
                            # subsequent on_tts_finished re-arm supersedes this.
                            _silent_turn = not self.session.get("_turn_speech_emitted")
                            if _silent_turn:
                                # Turn-finalisation fix: if this silent turn was a
                                # keep-listening fragment in a choice state, extend
                                # the watchdog grace window BEFORE re-arming, so the
                                # watchdog doesn't immediately replay the question
                                # over the caller's ongoing answer.
                                if self.session.get("_keep_listening_fragment"):
                                    self._silence_handler._watchdog_grace_until = (
                                        time.time() + 4.0
                                    )
                                self._silence_handler.restart_for_question(_last_q)
                                logger.info(
                                    "[ms_conn] silent-turn watchdog re-arm "
                                    "(fragment_suppressed=%s keep_listening=%s) "
                                    "state=%s q=%r",
                                    bool(self.session.get("fragment_suppressed")),
                                    bool(self.session.get("_keep_listening_fragment")),
                                    self.session.get("state", "?"),
                                    _last_q[:60],
                                )
                            # Scaffold continuation: fragment received but no TTS was
                            # spoken.  Backdate last_audio_received_at so W1's 3.5 s
                            # audio-recency guard doesn't suppress the recovery prompt,
                            # then arm the silence timer directly.
                            if self.session.pop("_nc_scaffold_hold", False):
                                self._silence_handler.last_audio_received_at = (
                                    time.time() - 4.0
                                )
                                # Extend watchdog patience via the dedicated grace field —
                                # NOT last_engagement_at (which has real-time semantics
                                # used by _speech_recovery and debounce guards).
                                # grace=5s + wait=3s → 8s total before first re-ask,
                                # giving the caller time to complete "my surname is [name]".
                                self._silence_handler._watchdog_grace_until = time.time() + 5.0
                                self._silence_handler.restart_for_question(_last_q)
                                logger.info(
                                    "[ms_conn] scaffold_hold: silence timer armed for %r",
                                    _last_q[:60],
                                )
                    logger.info(
                        "[ms_conn] state after turn: %s  flow_step=%s",
                        self.session.get("state", "?"),
                        self.session.get("flow_step", 0),
                    )
                    # Clear flow_step=0 after each turn so phone_confirm_grace
                    # does not persist into subsequent turns (greeting, location
                    # question, etc.).  Only reset the sentinel value (0); other
                    # non-zero values are left for the flow engine to manage.
                    if self.session.get("flow_step") == 0:
                        self.session["flow_step"] = -1
                        logger.debug("[ms_conn] flow_step reset to -1 after turn")

                    await save_session(self.call_sid, self.session)

                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(
                        "[ms_conn] flow error: %r\n%s", exc, traceback.format_exc(),
                    )
                    await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)
                    # Re-ask whatever question was pending so the caller isn't left
                    # in silence after the technical blip — but only if flow is still
                    # active; replaying a stale question after completion is wrong.
                    _lq = self.session.get("last_question", "")
                    if _lq and not flow.is_complete():
                        await self.tts_text_queue.put(_lq)
                finally:
                    self._last_turn_done_at               = time.monotonic()
                    self._llm_busy                        = False
                    self._silence_handler.on_llm_finished()
                    self.session["llm_generation_active"] = False
                    # Bug 5: drain pending TTS if a repair was detected this turn
                    # so old LLM output doesn't play after the repair phrase.
                    if self.session.pop("repair_requested", False):
                        while not self.tts_text_queue.empty():
                            try:
                                self.tts_text_queue.get_nowait()
                            except Exception:
                                break
                        logger.info("[ms_conn] repair_requested: TTS queue drained")
                        # Use the state-aware repair phrase set by flow.py (stored in
                        # last_question before repair_requested=True was set).  Fall back
                        # to the generic phrase only when flow.py left it empty, which
                        # should not happen for any mapped state.
                        _repair_phrase = (
                            self.session.get("last_question")
                            or "Sorry about that \u2014 what was your inquiry?"
                        )
                        # Enqueue repair phrase AFTER drain so it isn't wiped.
                        await self.tts_text_queue.put(_repair_phrase)
                    # Repeat request — drain stale TTS and replay last relevant answer.
                    if self.session.pop("repeat_requested", False):
                        while not self.tts_text_queue.empty():
                            try:
                                self.tts_text_queue.get_nowait()
                            except Exception:
                                break
                        logger.info("[ms_conn] repeat_requested: TTS queue drained")
                        _cur_state = self.session.get("state", "")
                        _lq  = self.session.get("last_question", "")
                        _lfa = self.session.get("last_faq_answer", "")
                        # Prompt 8 Bug 2 fix: last_question wins when it holds a
                        # specific active prompt (e.g. a clinic clarification like
                        # "Sure — is that Alcester or Redditch?").  Only fall back to
                        # last_faq_answer (the FAQ body) when last_question is the
                        # generic deferred placeholder or empty — meaning no distinct
                        # question is waiting for an answer.
                        _FAQ_OFFER_STATES = {"FAQ_BOOKING_OFFER", "GENERAL_BOOKING_OFFER"}
                        _GENERIC_LQ = {"Anything else you'd like to ask?", ""}
                        _use_faq_body = (
                            _cur_state in _FAQ_OFFER_STATES
                            and _lq in _GENERIC_LQ
                        )
                        _replay = (_lfa if _use_faq_body else "") or _lq
                        # Guard: always emit something — never let repeat leave the
                        # caller in silence when last_question/last_faq_answer are empty.
                        if not _replay:
                            _replay = "Sorry, could you say that again?"
                        await self.tts_text_queue.put(_replay)
                        logger.info("[ms_conn] repeat_requested: replaying %r", _replay[:60])
                    # Bug 9: restart silence timer after fragment suppression
                    # so the call doesn't go permanently silent.
                    if self.session.pop("fragment_suppressed", False):
                        _frag_lq = self.session.get("last_question", "")
                        if _frag_lq:
                            # Turn-finalisation fix: when the suppressed turn was
                            # a keep-listening fragment (clipped / filler / "one
                            # sec" in a choice state), extend the watchdog grace
                            # window so Susie does NOT replay the question on top
                            # of the caller's real answer still being formed.
                            # Only the first re-arm after the fragment gets the
                            # extension; normal silence cascade resumes afterwards.
                            if self.session.pop("_keep_listening_fragment", False):
                                self._silence_handler._watchdog_grace_until = (
                                    time.time() + 4.0
                                )
                                logger.info(
                                    "[ms_conn] keep-listening fragment: watchdog "
                                    "grace extended +4.0s before replay"
                                )
                            self._silence_handler.restart_for_question(_frag_lq)
                    await save_session(self.call_sid, self.session)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _llm_loop fatal: %r", exc)

    # ========================================================================
    # TTS loop
    # ========================================================================

    async def _tts_loop(self) -> None:
        """
        Wait for the "start" event, then consume text chunks from tts_text_queue
        and synthesise each through TTSStream -> audio_out_queue.

        Each chunk is a separate cancellable ElevenLabs request.
        Chunks always play in order.

        On ElevenLabs error: logs and continues to next chunk (audio may skip
        but pipeline keeps running). If ElevenLabs is completely down, the
        CLAUDE_ERROR_PHRASE chunks get silently dropped — the caller will
        hear dead air, and the watchdog will eventually play a bridge phrase.
        """
        await self._wait_for_start("tts_loop")

        from .tts_stream import (
            TTSStream,
            _apply_tts_substitutions_elevenlabs as _apply_tts_subs,
        )
        tts = TTSStream(clinic_id=self.session.get("clinic_id", ""))
        _last_tts_chunk: str = ""  # BUG 2: dedup — track last synthesised text chunk

        try:
            while not self._stop_event.is_set():
                try:
                    chunk_text = await asyncio.wait_for(
                        self.tts_text_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # Sentinel: enqueued before each handle_transcript call to reset dedup
                # state between caller turns so fresh identical phrases are not suppressed.
                if chunk_text == "\x00DEDUP_RESET\x00":
                    _last_tts_chunk = ""
                    continue

                if not chunk_text or not chunk_text.strip():
                    continue

                # Watchdog re-ask marker: deliberate silence-recovery replay.
                # Strip the marker and bypass the consecutive-duplicate dedup
                # guard for this one chunk only.  All other safety conditions
                # (q_gen, engagement, _tts_playing, barge-in) are enforced at
                # the watchdog fire site before the chunk was enqueued.
                _watchdog_reask = chunk_text.startswith(_WATCHDOG_REASK_MARKER)
                if _watchdog_reask:
                    chunk_text = chunk_text[len(_WATCHDOG_REASK_MARKER):]
                    if not chunk_text.strip():
                        continue

                # Ack-filler marker: background FILLER_PHRASE queued by
                # _delayed_filler() in llm_stream.py.  Strip the marker; if a
                # tool-call filler has since cancelled the ack filler for this
                # turn (_ack_filler_cancelled), discard the chunk silently so
                # the patient does not hear both fillers back-to-back.
                # If the ack filler already finished playing (cancelled flag not
                # set), strip the marker and play it normally — nothing to do.
                _ack_filler_chunk = chunk_text.startswith(ACK_FILLER_MARKER)
                if _ack_filler_chunk:
                    chunk_text = chunk_text[len(ACK_FILLER_MARKER):]
                    if not chunk_text.strip():
                        continue
                    if self.session.get("_ack_filler_cancelled"):
                        logger.info(
                            "[ms_tts] ack filler suppressed — "
                            "tool call filler took over: %r", chunk_text[:60],
                        )
                        self.session["_ack_filler_cancelled"] = False
                        continue

                # Pre-slot marker: text chunks prefixed by _one_streaming_call so
                # they can be discarded if check_availability was detected mid-stream.
                # Strip the marker; if _pre_slot_cancelled is True, drop the chunk so
                # the caller does not hear partial LLM text before the full slot data.
                _pre_slot_chunk = chunk_text.startswith(PRE_SLOT_MARKER)
                if _pre_slot_chunk:
                    chunk_text = chunk_text[len(PRE_SLOT_MARKER):]
                    if not chunk_text.strip():
                        continue
                    if self.session.get("_pre_slot_cancelled"):
                        logger.info(
                            "[ms_tts] pre-slot chunk suppressed — "
                            "check_availability detected this turn: %r",
                            chunk_text[:60],
                        )
                        continue

                # SPEC 4 / Bug 1: apply phonetic substitution to chunk_text NOW
                # so that every downstream tracking variable — dedup comparison,
                # _last_tts_chunk, and _tts_text_pending (→ tts_finished log,
                # watchdog re-ask prompt) — stores the substituted form ("Awlstuh")
                # rather than the canonical spelling ("Alcester").
                # synthesise_chunk applies the same substitution internally; the
                # regex is idempotent so double-application is harmless.
                chunk_text = _apply_tts_subs(chunk_text)

                # Bug 5: discard stale LLM chunks that arrived after a confirmed barge-in.
                # The flag is cleared in _llm_loop finally when the new turn starts.
                if self.session.get("tts_inhibit"):
                    logger.info(
                        "[ms_conn] tts_inhibit: discarding stale chunk %r", chunk_text[:60]
                    )
                    # CODE SPEC AC: track inhibited slot chunks so we can clear
                    # the DTMF slot map when ALL chunks from a slot presentation
                    # are discarded before the patient hears any option.
                    # _slot_chunks_sent is set by _flush_slot_buf only when a
                    # real slot map (≥2 entries) was extracted this turn.
                    _sc_sent = self.session.get("_slot_chunks_sent", 0)
                    if _sc_sent > 0 and re.search(
                        r"\bNumber\s+\d\b", chunk_text, re.IGNORECASE
                    ):
                        # Save the discarded chunk so we can re-present it if the
                        # WHOLE presentation gets inhibited (Bug A recovery).
                        self._inhibited_slot_chunks.append(chunk_text)
                        _sc_inh = (
                            int(self.session.get("_slot_chunks_inhibited", 0)) + 1
                        )
                        self.session["_slot_chunks_inhibited"] = _sc_inh
                        logger.info(
                            "[ms_conn] slot chunk inhibited %d/%d",
                            _sc_inh, _sc_sent,
                        )
                        if _sc_inh >= _sc_sent:
                            # All chunks gone — patient heard NOTHING.  Recover
                            # instead of going silent: clear the inhibit and
                            # re-queue the saved chunks so they actually play this
                            # time, keeping the slot map intact.  One-shot per
                            # burst (_slot_represented_once, reset when any chunk
                            # next plays) prevents a re-inhibit -> re-queue loop.
                            if (
                                not self._slot_represented_once
                                and self._inhibited_slot_chunks
                            ):
                                self._slot_represented_once = True
                                self.session["tts_inhibit"] = False
                                self.session["_slot_chunks_inhibited"] = 0
                                _saved = self._inhibited_slot_chunks
                                self._inhibited_slot_chunks = []
                                logger.info(
                                    "[ms_conn] all slot chunks inhibited — "
                                    "re-presenting %d chunk(s) (patient heard "
                                    "nothing, slot map kept)",
                                    len(_saved),
                                )
                                for _c in _saved:
                                    self.tts_text_queue.put_nowait(_c)
                            else:
                                # Already re-presented once and still inhibited —
                                # give up to avoid a loop; clear the map.
                                logger.info(
                                    "[ms_conn] all slot chunks inhibited again — "
                                    "clearing slot map (patient never heard options)"
                                )
                                self.session.pop("v3_dtmf_slot_map",          None)
                                self.session.pop("v3_awaiting_slot_selection", None)
                                self.session.pop("_slot_chunks_sent",          None)
                                self.session.pop("_slot_chunks_inhibited",     None)
                                self.slot_map_stage = SlotMapStage.NONE
                                self._inhibited_slot_chunks = []
                    continue

                # A chunk is about to play (not inhibited) — allow a future
                # heard-nothing re-presentation for the next slot burst.
                if self._slot_represented_once:
                    self._slot_represented_once = False

                # Skip consecutive identical chunks (dedup guard) — but never for
                # a watchdog re-ask, which is a deliberate replay of the question.
                if (
                    not _watchdog_reask
                    and chunk_text.strip().lower() == _last_tts_chunk.lower()
                ):
                    logger.info(
                        "[ms_conn] TTS dedup: skipping duplicate chunk %r",
                        chunk_text[:80],
                    )
                    continue
                if _watchdog_reask:
                    logger.info(
                        "[ms_conn] TTS watchdog re-ask: dedup bypassed for %r",
                        chunk_text[:80],
                    )
                _last_tts_chunk = chunk_text.strip()

                # Change C: cancel filler timer; inject 100ms breath gap if
                # the clip already fired this turn (one-shot: _filler_breath_injected
                # prevents multiple injections across consecutive TTS chunks).
                self._filler.cancel()
                if self._filler.has_played and not self._filler_breath_injected:
                    self._filler_breath_injected = True
                    await self._send_ulaw(_SILENCE_100MS)

                # Split long phrases into shorter sub-chunks so barge-in fires
                # sooner — at most ~1-2s of audio in Twilio's buffer instead of
                # up to ~6-7s for a full deterministic day/time phrase.
                from .chunker import split_tts_text
                sub_chunks = split_tts_text(chunk_text)
                _any_cancelled = False

                # ── Clinical barge-in protection ──────────────────────────────
                # Reset for each new chunk; re-arm if this chunk contains
                # empathy/clinical language without slot-selection content.
                # When armed, _on_partial_transcript will NOT cancel this TTS.
                self._clinical_response_active = False
                _CLINICAL_EMPATHY_PHRASES = (
                    "sounds uncomfortable", "sounds painful",
                    "sorry to hear", "that must be", "must be difficult",
                    "that sounds really", "really uncomfortable",
                    "really painful", "sorry about that",
                )
                _ct_lower = chunk_text.lower()
                _has_slot_content = bool(
                    re.search(r"\bnumber\s+\d\b", chunk_text, re.IGNORECASE)
                )
                if (
                    any(p in _ct_lower for p in _CLINICAL_EMPATHY_PHRASES)
                    and not _has_slot_content
                ):
                    self._clinical_response_active = True
                    logger.info(
                        "[ms_conn] clinical response active"
                        " — barge-in guard armed: %r",
                        chunk_text[:60],
                    )
                # ── end clinical barge-in protection ──────────────────────────

                # Notify silence handler ONCE per chunk (not per sub-chunk).
                # on_tts_started() is paired with exactly one on_tts_finished() call
                # (via _delayed_tts_finished after the sentinel).  Calling it per
                # sub-chunk created a counting imbalance that let chunk N's delayed
                # callback clear _tts_playing while chunk N+1 was already playing,
                # opening a Guard-0 gap in _speech_recovery.
                self._silence_handler.on_tts_started()
                # Capture the timestamp set by on_tts_started() so _delayed_tts_finished
                # can pass it to on_tts_finished() for the multi-chunk stale check.
                _chunk_tts_start_ts = self._silence_handler._tts_last_start_ts
                # Safety net anchor: TTS activity keeps the 10s dead-air guard at bay.
                self._last_audio_or_transcript_ts = time.monotonic()

                # Sequence counter: increment ONCE per text chunk (not per sub-chunk).
                # Each text chunk places exactly one sentinel → one _delayed_tts_finished
                # callback.  Incrementing per sub-chunk meant that a 2-sub-chunk text
                # item would assign seqs 1 and 2 but only fire a callback for seq 2,
                # making the OOO range check (range(1, N+1)) always flag seq 1 as
                # missing — a false positive that caused 4-second call hangs.
                self._tts_chunk_seq += 1
                _this_chunk_seq = self._tts_chunk_seq
                self._tts_expected_final_seq = self._tts_chunk_seq
                self._current_chunk_seq = _this_chunk_seq

                for sub_text in sub_chunks:
                    # Track current sub-chunk so barge-in resume is accurate.
                    self._current_tts_text = sub_text

                    self._tts_task = asyncio.create_task(
                        tts.synthesise_chunk(
                            text=sub_text,
                            audio_out_queue=self.audio_out_queue,
                            audio_out_processor=self._audio_out_proc,
                        )
                    )
                    try:
                        await self._tts_task
                    except asyncio.CancelledError:
                        logger.info("[ms_conn] TTS sub-chunk cancelled (barge-in)")
                        _any_cancelled = True
                        break
                    except Exception as exc:
                        logger.error("[ms_conn] TTS sub-chunk error: %r", exc)
                    finally:
                        self._tts_task = None

                    # Barge-in may have fired between sub-chunks (rare race).
                    if self._barge_in_pending:
                        _any_cancelled = True
                        break

                if not _any_cancelled:
                    # All sub-chunks completed — place sentinel so send_loop can
                    # fire on_tts_finished once every byte has been sent to Twilio.
                    self._tts_text_pending = chunk_text
                    self._tts_pending_chunk_start_ts = _chunk_tts_start_ts
                    # Snapshot q_gen at chunk-finish time so a late tts_finished
                    # callback whose owning prompt was superseded by a new question
                    # is rejected as stale (prevents old-prompt timer restarts).
                    self._tts_pending_q_gen = self._silence_handler._q_gen
                    # Snapshot the chunk sequence number so _delayed_tts_finished
                    # can compare against _tts_expected_final_seq and suppress
                    # non-terminal callbacks from intermediate chunks.
                    self._tts_pending_chunk_seq = self._tts_chunk_seq
                    await self.audio_out_queue.put(_TTS_DONE_SENTINEL)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _tts_loop fatal: %r", exc)

    # ========================================================================
    # Raw µ-law send helper  (used by FillerGuard)
    # ========================================================================

    async def _send_ulaw(self, ulaw_bytes: bytes) -> None:
        """
        Encode raw µ-law bytes as base64 and put them directly on audio_out_queue.

        Used by FillerGuard to inject the pre-synthesised filler clip into the
        audio stream without going through ElevenLabs synthesis.  The _send_loop
        picks the payload up and forwards it to Twilio exactly like any other
        audio frame.
        """
        if not ulaw_bytes:
            return
        b64 = base64.b64encode(ulaw_bytes).decode("ascii")
        await self.audio_out_queue.put(b64)

    # ========================================================================
    # Send loop
    # ========================================================================

    async def _send_loop(self) -> None:
        """
        Continuously read base64-encoded mulaw payloads from audio_out_queue
        and forward them to Twilio as JSON "media" events.

        Updates _last_audio_at on every successful send (used by watchdog).
        If the WebSocket closes mid-call, drain the queue and exit.
        """
        _tts_bytes_sent: int = 0  # mulaw bytes sent for the current TTS utterance

        try:
            while not self._stop_event.is_set():
                try:
                    b64_payload = await asyncio.wait_for(
                        self.audio_out_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not b64_payload:
                    continue

                # TTS-done sentinel: all audio for this utterance has been sent
                # to Twilio.  Schedule on_tts_finished to fire after the audio
                # has actually played out (bytes_sent / 8000 Hz = play duration).
                if b64_payload is _TTS_DONE_SENTINEL:
                    text = self._tts_text_pending
                    chunk_start_ts = self._tts_pending_chunk_start_ts
                    chunk_q_gen = self._tts_pending_q_gen
                    chunk_seq = self._tts_pending_chunk_seq
                    self._tts_text_pending = ""
                    self._tts_pending_chunk_start_ts = 0.0
                    self._tts_pending_q_gen = -1
                    self._tts_pending_chunk_seq = 0
                    play_secs = _tts_bytes_sent / 8000.0
                    _tts_bytes_sent = 0
                    # Only arm the silence timer if audio was actually delivered.
                    # If ElevenLabs failed (0 bytes sent), play_secs == 0 and we
                    # must NOT arm the timer — doing so triggers a spurious 26-second
                    # silence-transfer cascade (12s + 10s + 4s windows) even though
                    # Susie never spoke.
                    if text and play_secs > 0.01:
                        # Cumulative playout scheduling: this chunk's audio
                        # actually finishes playing only after all previously
                        # queued audio has played out.  Anchor to the running
                        # playout-end clock (or now, whichever is later, so a
                        # gap since the last chunk self-heals) and schedule the
                        # finish callback for that absolute instant.  This makes
                        # chunks finish strictly in order — the terminal chunk
                        # always last — so the OOO stall / 4s force-fire path is
                        # no longer reached on normal multi-sentence responses.
                        now = time.monotonic()
                        playout_start = max(now, self._tts_playout_end_mono)
                        playout_end   = playout_start + play_secs
                        self._tts_playout_end_mono = playout_end
                        sched_delay = max(0.0, playout_end - now)
                        logger.info(
                            "[ms_silence] tts_finished in %.1fs: %r",
                            sched_delay, text[:60],
                        )
                        asyncio.create_task(
                            self._delayed_tts_finished(sched_delay, text, self._tts_gen, chunk_start_ts, chunk_q_gen, chunk_seq),
                            name="ms_silence_tts_delay",
                        )
                    elif text:
                        logger.warning(
                            "[ms_silence] TTS sentinel with 0 bytes — ElevenLabs likely "
                            "rate-limited; silence timer NOT armed to prevent spurious transfer"
                        )
                    continue

                try:
                    await self.websocket.send_json({
                        "event":     "media",
                        "streamSid": self.stream_sid,
                        "media":     {"payload": b64_payload},
                    })
                    now = time.monotonic()
                    self._last_audio_at                = now
                    self.session["last_audio_sent_at"] = _iso_now()
                    # Count raw mulaw bytes for play-duration estimate.
                    # base64 encodes 3 bytes as 4 chars → multiply by 0.75.
                    _tts_bytes_sent += int(len(b64_payload) * 0.75)

                except WebSocketDisconnect:
                    logger.info("[ms_conn] send_loop: WS closed")
                    self._stop_event.set()
                    break
                except RuntimeError as exc:
                    if "close message" in str(exc):
                        self._stop_event.set()
                        break
                    logger.error("[ms_conn] send_loop runtime error: %r", exc)
                except Exception as exc:
                    logger.error("[ms_conn] send_loop error: %r", exc)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _send_loop fatal: %r", exc)

    async def _delayed_tts_finished(
        self,
        delay: float,
        text: str,
        gen: int = 0,
        chunk_started_at: float = 0.0,
        q_gen_at_start: int = -1,
        chunk_seq: int = 0,
    ) -> None:
        """
        Fire on_tts_finished after `delay` seconds so the silence timer starts
        only once the caller has actually heard the last word, not when the
        audio was merely enqueued into Twilio's buffer.
        delay = mulaw_bytes_sent / 8000 Hz

        gen — the _tts_gen value captured at creation time.  If a barge-in has
        occurred since this task was created, _tts_gen will have advanced and
        this callback is stale: firing it would overwrite last_question with an
        old prompt (e.g. "does that sound OK?") after the flow has already moved
        on, and re-arm the silence timer for the wrong question.

        chunk_started_at — the _tts_last_start_ts value when this chunk's
        on_tts_started() fired.  Forwarded to on_tts_finished() so it can
        detect whether a newer chunk has started, preventing premature
        clearing of _tts_playing during multi-chunk / multi-part responses.
        """
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            # Stale-generation guard: barge-in increments _tts_gen.
            if gen != self._tts_gen:
                logger.debug(
                    "[ms_silence] tts_finished ignored — stale gen %d vs current %d: %r",
                    gen, self._tts_gen, text[:60],
                )
                return
            # Record this chunk as PLAYED for the in-order terminal check before
            # the stale-q_gen / flow-complete guards below skip arming the timer.
            # Root cause of the #6 OOO dead-air: a chunk that finished playing but
            # was dropped as stale-q_gen (e.g. a previous turn's filler whose audio
            # ended after the next question began) never got recorded, leaving a
            # permanent gap in _tts_chunks_completed that blocked every later
            # terminal's all-done check and forced the _ooo_force_fire backstop.
            # Marking it here (it did play) lets the next terminal fire in-order.
            # NB: placed AFTER the barge-in guard — interrupted chunks must not count.
            if chunk_seq:
                self._tts_chunks_completed.add(chunk_seq)
            # Stale-question guard: if the flow has advanced to a new question
            # since this chunk was enqueued, the old prompt must not restart the
            # silence timer or overwrite last_question.  Fixes the "old prompt
            # owns the timer after state transition" bug (e.g. a late FAQ-answer
            # tts_finished firing while CONFIRM_PHONE is the active prompt).
            if (
                q_gen_at_start != -1
                and q_gen_at_start != self._silence_handler._q_gen
            ):
                logger.info(
                    "[ms_silence] stale tts_finished ignored: callback_q_gen=%d active_q_gen=%d text=%r",
                    q_gen_at_start, self._silence_handler._q_gen, text[:60],
                )
                return
            # Don't arm the silence timer once the booking flow is complete.
            # Without this guard, CONFIRM_BOOKING's LLM response (which often
            # ends with "?") re-arms the timer and causes a spurious CONFIRM_PHONE
            # re-ask after booking is confirmed, failing no_question_asked_twice /
            # no_state_corruption checks (seen in tests 2.7 and 6.4).
            if hasattr(self, "_flow") and self._flow.is_complete():
                logger.debug("[ms_silence] flow complete — skipping tts_finished")
                return
            # ── Out-of-order chunk guard ──────────────────────────────────────
            # Track every completed chunk_seq so the silence timer is only armed
            # once ALL chunks up to and including the terminal have finished
            # playing.  Fixes the case where a short terminal chunk's play-
            # duration timer expires before a longer earlier chunk's, causing the
            # watchdog to fire while the patient is still listening to prior audio.
            #
            # Three cases:
            #   1. Terminal fires and all preceding chunks already done → normal
            #      in-order path, fire immediately.
            #   2. Terminal fires but earlier chunks are still pending → stash the
            #      terminal context in _tts_pending_terminal and return.  Each
            #      subsequent non-terminal callback re-checks and fires once the
            #      set is complete.
            #   3. Non-terminal fires and no pending terminal → suppress (existing
            #      behaviour for normal multi-chunk responses).
            self._current_chunk_seq = chunk_seq
            _fired_seq    = chunk_seq
            _expected_seq = getattr(self, "_tts_expected_final_seq", 0)
            _is_terminal  = (_expected_seq > 0 and _fired_seq >= _expected_seq)

            # Mark this chunk as completed.
            self._tts_chunks_completed.add(_fired_seq)

            if _is_terminal:
                _all_done = all(
                    i in self._tts_chunks_completed
                    for i in range(1, _fired_seq + 1)
                )
                if _all_done:
                    # Normal case: terminal arrived last (or only chunk).
                    self._tts_pending_terminal = 0
                    logger.info(
                        "[ms_tts] tts_finished: terminal chunk %d — silence timer starting",
                        _fired_seq,
                    )
                    # fall through to fire on_tts_finished below
                else:
                    # Out-of-order: terminal arrived before some earlier chunk.
                    # Stash context and wait for remaining chunks to complete.
                    self._tts_pending_terminal                    = _fired_seq
                    self._tts_pending_terminal_text               = text
                    self._tts_pending_terminal_chunk_start_ts     = chunk_started_at
                    logger.info(
                        "[ms_tts] tts_finished: terminal chunk %d out-of-order — "
                        "waiting for earlier chunks (completed=%s expected=%d)",
                        _fired_seq, sorted(self._tts_chunks_completed), _expected_seq,
                    )
                    # Crash-only backstop: with cumulative playout scheduling
                    # (see _tts_playout_end_mono) chunks now finish strictly in
                    # order, so reaching this branch means a chunk's finish task
                    # genuinely never ran (e.g. cancelled/crashed), NOT merely a
                    # long middle chunk still playing.  The old 4s timeout fired
                    # mid-playback on normal multi-sentence answers and produced
                    # the premature "Sorry, I didn't catch that" re-ask; 30s makes
                    # this a true hang-guard that never trips during real audio.
                    _ooo_guard_seq = _expected_seq
                    _ooo_guard_text = text
                    _ooo_guard_ts   = chunk_started_at
                    # Capture the precise cumulative playout-end so recovery can
                    # fire ~3s after the audio ACTUALLY finishes, not on a blind
                    # 30s timer (which left the caller in dead air for half a
                    # minute when an earlier chunk's finish task never ran).
                    _ooo_playout_end = self._tts_playout_end_mono

                    async def _ooo_force_fire() -> None:
                        # Wait until just past the real audio-end (playout clock),
                        # falling back to a 30s cap only if the clock is unset.
                        _OOO_MARGIN = 3.0
                        if _ooo_playout_end > 0.0:
                            _wait = (_ooo_playout_end + _OOO_MARGIN) - time.monotonic()
                        else:
                            _wait = 30.0
                        await asyncio.sleep(max(2.0, _wait))
                        # Stale-guard: a new TTS response has superseded this one.
                        if self._tts_expected_final_seq != _ooo_guard_seq:
                            logger.debug(
                                "[ms_tts] _ooo_force_fire: seq mismatch "
                                "(%d != %d) — skipping",
                                self._tts_expected_final_seq, _ooo_guard_seq,
                            )
                            return
                        # If pending terminal has already been resolved normally
                        # (earlier chunks arrived in time), bail out.
                        if self._tts_pending_terminal == 0:
                            logger.debug(
                                "[ms_tts] _ooo_force_fire: pending terminal "
                                "already resolved — skipping"
                            )
                            return
                        logger.warning(
                            "[ms_tts] _ooo_force_fire: earlier chunks never "
                            "arrived by playout-end for terminal seq %d — "
                            "force-firing silence timer (chunk task likely "
                            "crashed/cancelled)",
                            _ooo_guard_seq,
                        )
                        # Clear stashed state so the normal resolver won't
                        # double-fire if a late chunk somehow arrives later.
                        self._tts_pending_terminal                = 0
                        self._tts_pending_terminal_text           = ""
                        self._tts_pending_terminal_chunk_start_ts = 0.0
                        if not self._silence_handler._watchdog_has_retired:
                            self._tts_audio_done_at = time.monotonic()
                            self._silence_handler.on_tts_finished(
                                _ooo_guard_text,
                                chunk_started_at=_ooo_guard_ts,
                            )
                        else:
                            logger.debug(
                                "[ms_tts] _ooo_force_fire: watchdog already "
                                "retired — silence timer not restarted"
                            )

                    asyncio.create_task(_ooo_force_fire())
                    return
            else:
                # Non-terminal chunk.  Check if this arrival resolves a stored
                # pending terminal (i.e. the terminal fired before us).
                _pending = self._tts_pending_terminal
                if _pending and all(
                    i in self._tts_chunks_completed
                    for i in range(1, _pending + 1)
                ):
                    _resolve_text     = self._tts_pending_terminal_text
                    _resolve_chunk_ts = self._tts_pending_terminal_chunk_start_ts
                    self._tts_pending_terminal                = 0
                    self._tts_pending_terminal_text           = ""
                    self._tts_pending_terminal_chunk_start_ts = 0.0
                    logger.info(
                        "[ms_tts] tts_finished: chunk %d resolved pending terminal %d — "
                        "silence timer starting",
                        _fired_seq, _pending,
                    )
                    logger.info(
                        "[ms_tts] tts_finished fired (resolved): chunk_text=%r q_size=%d",
                        _resolve_text[:60], self.tts_text_queue.qsize(),
                    )
                    self._tts_audio_done_at = time.monotonic()
                    self._silence_handler.on_tts_finished(
                        _resolve_text, chunk_started_at=_resolve_chunk_ts
                    )
                    logger.debug(
                        "[ms_silence] tts_finished (resolved) fired after %.1fs delay gen=%d",
                        delay, gen,
                    )
                    return
                else:
                    logger.info(
                        "[ms_tts] tts_finished: non-terminal "
                        "chunk %d (expected %d) — silence timer suppressed",
                        _fired_seq, _expected_seq,
                    )
                    return

            logger.info(
                "[ms_tts] tts_finished fired: chunk_text=%r q_size=%d",
                text[:60], self.tts_text_queue.qsize(),
            )

            # ── Spec W: watchdog restart for informational responses ──────────
            # SilenceHandler.on_tts_finished has a "late TTS callback" guard:
            #   last_audio_received_at > _last_question_set_at + 1 s → return
            # For informational turns (FAQ / treatment / pricing answers) this
            # guard ALWAYS fires because the caller's transcript arrived after
            # the previous question was asked and on_question_asked() was never
            # called during the new LLM turn, so _last_question_set_at was never
            # updated.  The result: no watchdog is ever armed, the silence safety
            # net fires immediately after TTS completion citing the elapsed time
            # from the LLM call start as dead air (e.g. "since=15.3 s").
            #
            # Fix: when we reach this point (terminal chunk, in-order, stale
            # guards cleared) and no watchdog is currently live, arm one
            # directly here — bypassing on_tts_finished entirely for the
            # watchdog arming step.  The subsequent on_tts_finished() call
            # below still handles _tts_playing and other bookkeeping; the
            # idempotent q_gen guard in _restart_timer means a double-arm
            # from that path is harmless.
            _sh_w = self._silence_handler
            _watchdog_live_w = (
                _sh_w._no_input_watchdog_task is not None
                and not _sh_w._no_input_watchdog_task.done()
            )
            if (
                not _watchdog_live_w
                and not _sh_w._watchdog_has_retired
                and not _sh_w._llm_busy
                and not _sh_w.currently_reasking
                and not _sh_w._cancelled
                and self.tts_text_queue.empty()   # no more TTS pending
            ):
                import re as _re_w
                _t_str_w = text.strip()
                _parts_w = _re_w.split(r'(?<=[.!?])\s+|\n+', _t_str_w)
                # Use last sentence as the re-ask prompt — avoids replaying a
                # full multi-sentence FAQ paragraph (spec W hard constraint).
                _last_sent_w = next(
                    (p.strip() for p in reversed(_parts_w) if p.strip()),
                    _t_str_w,
                )
                _has_question_w = bool(
                    _last_sent_w and _sh_w._prompt_contains_question(_last_sent_w)
                )
                # Fix A: while the clinic question is still pending, ALWAYS arm
                # the location ladder — even when the LLM's re-ask ends on a
                # statement (e.g. "We've got two locations, so I just want to
                # make sure I check the right one for you.").  Without this the
                # question-only guard below skipped arming, the generic 10s
                # _silence_safety_net fired its "how can I help today?" reset,
                # and the booking/location context was lost (stress test
                # 2026-06-12 12:23).  The watchdog fire path keys its prompt off
                # v3_location_q_active and builds the biased clinic confirm
                # itself, so a non-question last sentence is fine here.
                _loc_active_w = bool(self.session.get("v3_location_q_active"))
                if _has_question_w or _loc_active_w:
                    # Only arm on a genuine question OR an active clinic prompt —
                    # avoids WATCHDOG_SUPPRESSED noise for purely informational
                    # responses like "It's £75." or "Free parking is available."
                    if self.session.get("v3_awaiting_use_this_clinic"):
                        # Biased confirm is armed (soft-candidate / use-this-
                        # clinic path). The watchdog fire path speaks the rung-1
                        # biased confirm regardless of the last spoken sentence,
                        # so seed last_question with that SAME phrase — keeps the
                        # log and any non-ladder reader consistent with what is
                        # actually spoken. Checked first because the biased
                        # confirm ends on the instruction "...just say 'use this
                        # clinic'." (not a question), so _has_question_w would
                        # otherwise mis-seed it with the generic open Q. Matches
                        # the fire path's rung-1 alcester constant (line ~2854).
                        _arm_q_w = _LOC_RUNG2_CONFIRM
                    elif _has_question_w:
                        _arm_q_w = _last_sent_w
                    else:
                        # Location active but last sentence is a statement — seed
                        # last_question with the canonical clinic question so any
                        # non-ladder reader stays on-topic (the watchdog ladder
                        # overrides this with its own biased confirm phrase).
                        _arm_q_w = _LOC_RUNG1_OPEN
                    _sh_w.last_question         = _arm_q_w
                    _sh_w.reask_count           = 0
                    _sh_w._no_input_reask_count = 0
                    _sh_w._last_question_set_at = time.time()
                    _sh_w._q_gen               += 1
                    _sh_w._watchdog_has_retired = False
                    _sh_w._restart_timer()
                    logger.info(
                        "[ms_watchdog] restarted after informational response"
                        " q_gen=%d prompt=%r loc_active=%s",
                        _sh_w._q_gen, _arm_q_w[:60], _loc_active_w,
                    )
                else:
                    logger.debug(
                        "[ms_watchdog] Spec W: last sentence has no question — "
                        "skipping watchdog restart: %r",
                        _last_sent_w[:60],
                    )
            # ── end Spec W ────────────────────────────────────────────────────

            self._tts_audio_done_at = time.monotonic()
            # Reset the dead-air safety-net anchor when Susie stops speaking, so
            # "dead air" is measured from the end of her turn — never counting her
            # own (possibly multi-chunk) speech as caller silence.  Without this a
            # long response (e.g. slot confirm + "I'll need your name and number")
            # left the anchor at the response's START, so the safety net saw ~9s
            # of "silence" the instant TTS finished and — with the tightened A2
            # timing — fired a re-ask and then hung up on a caller who had been
            # given zero seconds to answer (no_audio close mid-booking, 12:17:57).
            self._last_audio_or_transcript_ts = time.monotonic()
            # Clinical protection no longer needed once the terminal chunk has
            # played out — reset so subsequent caller speech is handled normally.
            self._clinical_response_active = False
            self._silence_handler.on_tts_finished(text, chunk_started_at=chunk_started_at)
            logger.debug("[ms_silence] tts_finished fired after %.1fs delay gen=%d", delay, gen)
        except asyncio.CancelledError:
            pass

    # ========================================================================
    # TTS playback cancellation (DTMF helper)
    # ========================================================================

    async def _cancel_tts_playback(self) -> None:
        """Cancel any in-progress TTS synthesis and drain the Twilio playback buffer.

        Called when the caller presses the first phone-number digit while TTS is
        still playing.  The caller has switched to the keypad channel — there is
        no point continuing to speak over them.

        Mirrors the teardown performed by _on_partial_transcript on confirmed
        barge-in, but without setting _barge_in_pending (this is a deliberate
        DTMF-triggered stop, not a speech barge-in event).
        """
        _synth_active = bool(self._tts_task and not self._tts_task.done())
        _play_active  = bool(
            hasattr(self._silence_handler, "_tts_playing")
            and self._silence_handler._tts_playing
        )
        if not (_synth_active or _play_active):
            return

        if _synth_active:
            self._tts_task.cancel()

        _drain_queue(self.tts_text_queue)
        _drain_queue(self.audio_out_queue)
        # Twilio buffer is cleared below — discard the cumulative playout clock so
        # the next response schedules from `now`, not the cancelled audio's
        # future playout-end (mirrors the barge-in reset).
        self._tts_playout_end_mono = 0.0

        if self.stream_sid:
            try:
                await self.websocket.send_json({
                    "event":     "clear",
                    "streamSid": self.stream_sid,
                })
            except Exception:
                pass

        logger.info(
            "[ms_conn] DTMF: TTS playback cancelled "
            "(synthesis_active=%s playback_active=%s)",
            _synth_active, _play_active,
        )

    # ========================================================================
    # Barge-in
    # ========================================================================

    async def _on_partial_transcript(self, text: str) -> None:
        """
        Called by STTStream when a non-empty PartialTranscript arrives.

        Implements barge-in (only when TTS is actually playing):
          1. Cancel the current TTS streaming task
          2. Drain tts_text_queue (discard pending text chunks)
          3. Drain audio_out_queue (discard buffered audio)
          4. Send Twilio "clear" to drain its playback buffer
          5. Set _clearing=True to suppress energy VAD until final transcript arrives

        If TTS is NOT active (caller speaks after Susie finished), only the
        silence timer is cancelled — no queue drain, no Twilio clear, no _clearing.
        This prevents suppressing the energy VAD unnecessarily and avoids draining
        flow responses that arrive between the partial and final transcript.
        """
        if not text.strip():
            return

        logger.info("[ms_conn] barge-in: partial=%r", text[:60])

        # Reset safety-net dead-air anchor on every partial so the 10s backstop
        # never fires while the caller is mid-sentence.
        self._last_audio_or_transcript_ts = time.monotonic()

        # Always cancel the silence timer — caller is speaking.
        # on_transcript_received() handles the full reset when the utterance ends.
        # stt_source=True: this is a genuine PartialTranscript from AssemblyAI so
        # the barge_in_during_tts watchdog cancel is permitted.
        self._silence_handler.on_speech_started(stt_source=True)
        # Per-prompt speech guard: mark that the caller has started speaking
        # for the current prompt so any in-flight watchdog suppresses its re-ask.
        self._silence_handler._mark_prompt_speech_detected("partial", text)

        # Only perform barge-in teardown if TTS is actually playing.
        # When the caller speaks after Susie has already finished (e.g. right after
        # the greeting), there is nothing to interrupt — skip drain/clear/_clearing
        # and do NOT set _barge_in_pending so _resolve_barge_in() won't fire an
        # ack phrase and discard the utterance.
        #
        # IMPORTANT: Check BOTH synthesis and playback windows.
        # _tts_task tracks synthesis only (OpenAI HTTP stream, completes in ~1-2s).
        # _silence_handler._tts_playing tracks the full playback window (entire
        # audio duration, e.g. 8-10s for greeting). Without the playback check,
        # the 6-9s gap after synthesis-done-but-still-playing causes barge-in to
        # return early — Twilio `clear` never fires, Susie keeps talking.
        _synthesis_active = bool(self._tts_task and not self._tts_task.done())
        _playback_active  = bool(
            hasattr(self._silence_handler, "_tts_playing")
            and self._silence_handler._tts_playing
        )
        if not (_synthesis_active or _playback_active):
            return

        # Log when barge-in fires during the playback-only window — synthesis
        # complete but audio still playing. Previously this would have been
        # silently missed (the guard returned early before this point existed).
        if (
            self._silence_handler._tts_playing
            and not (
                self._tts_task
                and not self._tts_task.done()
            )
        ):
            logger.info(
                "[ms_conn] barge-in: playback-only window — "
                "synthesis done but audio still playing"
            )

        # ── Clinical barge-in guard ────────────────────────────────────────
        # When a clinical/empathy response is active, do NOT cancel the TTS.
        # The caller hears the full empathy acknowledgement; their barge-in
        # text is processed normally on the NEXT final transcript event.
        if self._clinical_response_active:
            logger.info(
                "[ms_conn] barge-in suppressed"
                " — clinical response completing: %r",
                self._current_tts_text[:60],
            )
            return
        # ── end clinical barge-in guard ───────────────────────────────────

        # Record barge-in start time (only once per barge-in event)
        if not self._barge_in_pending:
            self._barge_in_ts = time.monotonic()
            self._barge_in_pending = True
            # Snapshot the text currently being spoken for potential TTS resume
            self.session["interrupted_tts_text"] = self._current_tts_text
            # Advance the prompt generation so any in-flight _delayed_tts_finished
            # tasks for the interrupted TTS are treated as stale and ignored.
            self._tts_gen += 1
            # Reset chunk sequencing for the new q_gen so old sequence numbers
            # from the interrupted response cannot match new ones.
            self._tts_chunk_seq = 0
            self._tts_expected_final_seq = 0
            # Reset out-of-order tracking so stale completed-set entries from
            # the interrupted response cannot satisfy the next turn's range check.
            self._tts_chunks_completed = set()
            self._tts_pending_terminal = 0
            self._tts_pending_terminal_text = ""
            self._tts_pending_terminal_chunk_start_ts = 0.0
            # Reset the cumulative playout clock: the Twilio buffer is cleared
            # below, so any audio scheduled into the future is discarded.  Without
            # this, the next response's first chunk would be scheduled against the
            # interrupted response's stale (future) playout-end, adding phantom
            # delay before its finish callback / watchdog arming.
            self._tts_playout_end_mono = 0.0
            # Inhibit _tts_loop from speaking any LLM chunks that arrive after
            # the barge-in until the new turn completes (Bug 5 — stale output).
            self.session["tts_inhibit"] = True
            logger.info(
                "[ms_conn] barge-in start: synthesis_active=%s playback_active=%s "
                "interrupted_text=%r tts_gen=%d",
                _synthesis_active, _playback_active,
                self._current_tts_text[:60], self._tts_gen,
            )

        # Change E: cancel any armed filler so it doesn't play into a turn
        # the caller has already interrupted.
        self._filler.cancel()

        # Cancel synthesis only if still running — it may already be done
        # while audio is still draining through send_loop.
        if _synthesis_active:
            self._tts_task.cancel()

        drained_text  = _drain_queue(self.tts_text_queue)
        drained_audio = _drain_queue(self.audio_out_queue)
        if drained_text or drained_audio:
            logger.debug(
                "[ms_conn] barge-in drained: text_chunks=%d audio_chunks=%d",
                drained_text, drained_audio,
            )

        if self.stream_sid:
            try:
                await self.websocket.send_json({
                    "event":     "clear",
                    "streamSid": self.stream_sid,
                })
            except Exception:
                pass

    async def _resolve_barge_in(self, utterance: str = "") -> bool:
        """
        Check and resolve a pending barge-in event.

        Called from _llm_loop before processing each utterance.

        Returns True if the utterance should be SKIPPED (barge-in handled):
          - False trigger (speech < BARGE_IN_THRESHOLD_MS): TTS resumed from
            session["interrupted_tts_text"], utterance discarded.
          - Confirmed barge-in with empty/noise utterance: ack queued, utterance
            discarded so the NEXT utterance drives the flow.

        Returns False if no barge-in was pending — normal processing continues.
        Also returns False when a confirmed barge-in carries a substantive
        transcript (≥2 words) — the caller's answer is processed immediately
        instead of being dropped and re-asked.
        """
        if not self._barge_in_pending:
            return False

        self._barge_in_pending = False
        dur = self._barge_in_duration

        if dur < _BARGE_IN_THRESHOLD_S:
            # ── Bug 1/3 fix: before treating as a false trigger, check if the
            # utterance carries real content.  STT timing measurement starts from
            # the first partial, which may lag slightly behind actual speech onset —
            # a 2-word utterance like "no quentin" can register as < 300 ms even
            # when the caller spoke intentionally.  Don't discard it.
            # Rule: ≥ 2 non-noise words → always process regardless of state.
            # In structured confirmation/correction states even 1 non-noise word
            # matters (e.g. bare "no" at CONFIRM_PHONE must not be dropped).
            _ft_noise = frozenset({
                "uh", "um", "hmm", "ah", "er", "oh", "erm", "ehm", "hm",
                "mm", "mhm", "ugh", "huh",
            })
            _ft_words = [
                w for w in (utterance or "").strip().lower().split()
                if w not in _ft_noise
            ]
            _STRUCT_CORR_STATES = frozenset({
                "COLLECT_NAME", "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
                "CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING",
                "CONFIRM_BOOKING", "CONFIRM_RESCHEDULE", "CONFIRM_CANCEL",
                "COLLECT_REASON",
                # Short-answer question states: a bare one-word valid answer
                # ("Alcester", "Redditch", "Monday", "nine") arriving as short-duration
                # barge-in MUST be processed — dropping it is the canonical
                # "had to say it twice" bug.
                "ASK_LOCATION",
                "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
                "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
            })
            _cur_state_ft = self.session.get("state", "")
            _ft_meaningful = (
                len(_ft_words) >= 2
                or (_cur_state_ft in _STRUCT_CORR_STATES and len(_ft_words) >= 1)
            )
            if _ft_meaningful:
                # Real content in a short-duration window — process it, skip TTS resume.
                self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
                logger.info(
                    "[ms_conn] barge-in short-dur meaningful (%.0fms) %r state=%s "
                    "— processing instead of false-triggering",
                    dur * 1000, (utterance or "")[:60], _cur_state_ft,
                )
                return False  # process utterance — do NOT resume interrupted TTS
            # Genuine false trigger (noise/empty): resume interrupted TTS as before.
            interrupted = self.session.get("interrupted_tts_text", "")
            if interrupted:
                await self.tts_text_queue.put(interrupted)
            logger.info(
                "[ms_conn] barge-in false trigger (%.0fms < %dms threshold) — resuming TTS",
                dur * 1000, BARGE_IN_THRESHOLD_MS,
            )
            return True  # skip utterance

        # ── Confirmed barge-in ────────────────────────────────────────────
        # If we're already in recovery (ack was played, waiting for the real
        # utterance), skip the second ack and process this utterance directly.
        if self._in_barge_in_recovery:
            _state = self.session.get("state", "unknown")
            logger.info(
                "[ms_conn] barge-in during recovery — skipping ack, processing utterance directly (state=%s)",
                _state,
            )
            self._in_barge_in_recovery = False
            return False  # process utterance normally

        # ── FIX A: if the transcript carries a real answer, process it
        # immediately instead of dropping it and playing an ack.  The caller
        # already gave their answer — making them repeat it is the #1 observed
        # failure.  Only empty strings and pure filler noise ("uh", "um") get
        # the ack-and-wait treatment.  Single-word valid answers like "yes",
        # "new", "redditch", "recently" must be processed directly.
        _BARGE_NOISE = frozenset({
            "uh", "um", "hmm", "ah", "er", "oh", "erm", "ehm", "hm",
            "mm", "mhm", "ugh", "huh",
        })
        _barge_text = utterance.strip().lower() if utterance else ""
        _barge_words = _barge_text.split()
        _is_barge_noise = (
            not _barge_words
            or (len(_barge_words) == 1 and _barge_words[0] in _BARGE_NOISE)
        )
        if not _is_barge_noise:
            self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
            # Flush stale pre-barge-in transcripts — any item enqueued before
            # this moment will be discarded at dequeue time.
            self._barge_in_flush_before = time.monotonic()
            logger.info(
                "[ms_conn] barge-in #%d confirmed (%.0fms) — real transcript %r, "
                "processing directly instead of ack+drop (state=%s)",
                self.session["barge_in_count"], dur * 1000,
                utterance[:60], self.session.get("state", "unknown"),
            )
            self._in_barge_in_recovery = False
            return False  # process utterance normally — do NOT drop it

        ack = random.choice(_BARGE_IN_ACKS)
        await self.tts_text_queue.put(ack)
        self._in_barge_in_recovery = True
        self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
        _state = self.session.get("state", "unknown")
        # Flush stale pre-barge-in transcripts — any item enqueued before
        # this moment will be discarded at dequeue time.
        self._barge_in_flush_before = time.monotonic()
        logger.info(
            "[ms_conn] barge-in #%d confirmed (%.0fms) ack=%r state=%s",
            self.session["barge_in_count"], dur * 1000, ack, _state,
        )
        # slot question is NOT re-asked here — the NEXT utterance goes through
        # flow.handle_transcript() normally; re-ask only fires if that fails.
        return True  # skip current utterance (ack plays, next turn processes)

    async def _on_final_transcript_clear(self, text: str = "") -> None:
        """
        Called by STTStream on each FinalTranscript to reset _clearing.
        Ensures audio is no longer dropped once the caller finishes speaking.
        Also resets the SilenceHandler so the re-ask timer is cancelled.

        If a barge-in is pending, compute how long speech lasted so _llm_loop
        can decide: < threshold → false trigger (resume TTS), ≥ threshold → confirmed.
        """
        if self._barge_in_pending and self._barge_in_ts > 0:
            self._barge_in_duration = time.monotonic() - self._barge_in_ts
        self._clearing = False  # always reset — even garbage finals end the barge-in window

        # Per-prompt speech guard: a final transcript is the strongest signal
        # that the caller has spoken for this prompt. Mark BEFORE any downstream
        # logic so a watchdog re-ask cannot slip through.
        if (text or "").strip():
            self._silence_handler._mark_prompt_speech_detected("final", text)

        # Fix: if a watchdog repair phrase was queued/in-flight, kill it before
        # on_transcript_received() resets currently_reasking — otherwise the stale
        # TTS keeps playing over the caller's valid answer.
        if self._silence_handler.currently_reasking:
            # Cancel in-flight synthesis task
            if self._tts_task and not self._tts_task.done():
                self._tts_task.cancel()
                logger.info("[ms_conn] stale watchdog TTS cancelled (valid transcript arrived)")
            # Drain any queued repair phrases
            while not self._tts_text_queue.empty():
                try:
                    self._tts_text_queue.get_nowait()
                except Exception:
                    break

        # Tail-fragment gate: if this final arrived within the suppression window
        # of the last completed turn and is too short to be a real answer, skip
        # on_transcript_received so the watchdog timer is not spuriously cancelled.
        # _clearing=False (above) always runs — only the silence side-effect is gated.
        _fc_text  = (text or "").strip()
        _fc_since = time.monotonic() - self._last_turn_done_at
        if (
            self._last_turn_done_at > 0
            and _fc_since < _TAIL_FRAGMENT_WINDOW
            and 1 <= len(_fc_text) <= 3
            and _fc_text.lower() not in _TAIL_FRAGMENT_SAFE
        ):
            logger.info(
                "[ms_conn] tail-fragment in on_final_clear suppressed %r "
                "(%.2fs after last turn, %.2fs after last TTS start) — watchdog preserved",
                _fc_text, _fc_since,
                time.time() - self._tts_last_start_ts,
            )
        # ── theorem_v3 TTS-echo suppression ─────────────────────────────────
        # During the location question the phone line often echoes the bot's
        # own TTS audio back through the mic.  AssemblyAI transcribes single
        # words like "clinic", "alter", "hello" as FINAL transcripts, which
        # normally cancel the W1/W2/W3 silence timer — resetting the 22 s
        # sleep every ~5 s so a re-ask never fires.
        #
        # Guard: when v3_location_asked is active AND the transcript is ≤ 2
        # words AND none of those words is a meaningful location/yes/no token,
        # treat it as a TTS echo: skip on_transcript_received so the silence
        # timer is preserved.  The main transcript loop still receives the
        # text and will discard it via its own short-fragment guard.
        #
        # Meaningful tokens that must always pass through:
        #   • clinic names / phonetic variants  → _v3_extract_location handles
        #   • yes / no / yeah / nope / yep / yup (binary answers)
        #   • "use" / "this"  (start of "use this clinic")
        elif (
            self.session.get("v3_location_asked", False)
            # Bypass when biased confirm is active — caller's "yes I did" /
            # "I did" (2 words, no pass token) must not be echo-suppressed
            # or the silence timer never resets and Alcester is not resolved.
            and not self.session.get("v3_awaiting_use_this_clinic", False)
        ):
            _v3_echo_words = _fc_text.lower().split()
            _V3_LOC_PASS = frozenset({
                "yes", "no", "yeah", "nope", "yep", "yup", "nah",
                "use", "this",
                # single-word clinic name variants most likely to reach here
                # (the full alias set is checked by _v3_extract_location)
                "alcester", "redditch", "reditch", "reddich",
                "ulster", "olster", "awlster", "alchester",
                "one", "two", "first", "second",
            })
            # Timestamp guard: genuine mic bleed arrives within ~500 ms of TTS
            # finishing; 1.5 s gives headroom.  A transcript arriving more than
            # 1.5 s later is real caller speech and must reset the watchdog.
            _v3_in_window = (
                self._tts_audio_done_at > 0
                and (time.monotonic() - self._tts_audio_done_at) < 1.5
            )
            _v3_echo_candidate = (
                _v3_in_window
                and 1 <= len(_v3_echo_words) <= 2
                and not any(w in _V3_LOC_PASS for w in _v3_echo_words)
            )
            if _v3_echo_candidate:
                # Suppress on_transcript_received so the watchdog keeps running.
                # No grace extension needed — the timing gate already ensures
                # only genuine sub-500ms echoes are suppressed; after a real
                # echo the caller hasn't started speaking yet so the normal
                # watchdog timeout handles the silence correctly.
                logger.info(
                    "[ms_conn v3] TTS-echo candidate %r (%d word(s),"
                    " %.2fs after TTS done)"
                    " — on_transcript_received suppressed",
                    _fc_text, len(_v3_echo_words),
                    time.monotonic() - self._tts_audio_done_at,
                )
            else:
                self._silence_handler.on_transcript_received(text)
        else:
            self._silence_handler.on_transcript_received(text)

    # ========================================================================
    # Greeting injection
    # ========================================================================

    async def _inject_greeting(self) -> None:
        """
        Speak Susie's opening greeting directly via ElevenLabs TTS without
        an LLM round-trip — saves ~500ms on the first word of the call.

        Guards against double-fire (Twilio reconnect / duplicate start events).
        Advances state from GREETING → CLINIC_SELECTION so the LLM never
        sees GREETING state and tries to re-introduce itself.
        """
        # Guard: only fire once per call
        if self.session.get("greeting_delivered"):
            logger.info("[ms_conn] greeting already delivered — skipping")
            return

        # ────────────────────────────────────────────────────────────────────
        # theorem_v3 — hardcoded greeting with intro DTMF (digit 1 → Mark)
        # ────────────────────────────────────────────────────────────────────
        if self.session.get("clinic_id") == "theorem_v3":
            _v3_greeting = (
                "Hi there, I'm Susie, Theorem Health's AI receptionist — "
                "to speak to Mark directly press 1, "
                "otherwise how can I help you today?"
            )
            logger.info("[ms_conn v3] greeting: %r", _v3_greeting[:80])

            history = self.session.setdefault("conversation_history", [])
            history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
            history.append({"role": "assistant",  "content": _v3_greeting})
            self.session["last_bot_prompt"]  = _v3_greeting
            self.session["last_question"]    = ""
            self.session["greeting_delivered"] = True
            self.session["turn_count"]       = 1
            # Arm intro DTMF: digit 1 → transfer to Mark
            self.session["v3_intro_dtmf_active"] = True

            await save_session(self.call_sid, self.session)
            await self.tts_text_queue.put(_v3_greeting)
            return

        # ────────────────────────────────────────────────────────────────────
        # theorem / theorem_v2 — existing build_greeting() path UNCHANGED
        # ────────────────────────────────────────────────────────────────────
        from app.greeting_builder import build_greeting
        greeting = build_greeting()
        logger.info("[ms_conn] greeting: %r", greeting[:80])

        self.session.setdefault("turns", []).append({"role": "assistant", "text": greeting})
        history = self.session.setdefault("conversation_history", [])
        history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
        history.append({"role": "assistant", "content": greeting})
        self.session["last_bot_prompt"]    = greeting
        # Clear any stale last_question that may have been loaded from Redis
        # for this call_sid (e.g. previous call left "Just to confirm — shall I
        # use the number..." and the session was reloaded).  The silence handler
        # is also zeroed so no cross-call question can leak into the re-ask path.
        self.session["last_question"]       = ""
        self.session.pop("_last_question_not_reaskable", None)
        self._silence_handler.last_question = ""
        self.session["greeting_delivered"]  = True

        # State stays at GREETING after the initial greeting plays.
        # The first caller utterance triggers DETECT_INTENT → booking flow.
        # (No state advance here — keep GREETING until caller speaks.)

        await save_session(self.call_sid, self.session)

        await self.tts_text_queue.put(greeting)
        # The silence timer is armed automatically by _tts_loop's on_tts_finished()
        # hook once the greeting audio finishes playing.  No explicit call needed here.

    # ========================================================================
    # Transfer callback
    # ========================================================================

    def _should_allow_transfer(self) -> bool:
        """
        Single choke-point for transfer authorisation.
        Transfer fires ONLY under these exact conditions — nothing else can trigger it.
        """
        return (
            self.session.get("transfer_requested_by_caller") is True
            or self.session.get("medical_emergency_detected") is True
            or self.session.get("failed_understanding_count", 0) >= 3
            or self.session.get("request_transfer") is True      # set by transfer_to_human tool
            or self.session.get("silence_transfer") is True      # set by SilenceHandler after 3 re-asks
        )

    async def _on_transfer_request(self) -> None:
        """Initiate live call transfer via Twilio REST API."""
        if not self._should_allow_transfer():
            logger.warning("[ms_conn] transfer blocked — guard conditions not met session=%s", {
                "transfer_requested_by_caller": self.session.get("transfer_requested_by_caller"),
                "medical_emergency_detected":   self.session.get("medical_emergency_detected"),
                "failed_understanding_count":   self.session.get("failed_understanding_count"),
                "request_transfer":             self.session.get("request_transfer"),
            })
            return
        logger.info("[ms_conn] transfer authorised — initiating")
        try:
            from app.routes.realtime import _handle_transfer
            await _handle_transfer(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] transfer failed: %r", exc)

    # ========================================================================
    # Pipeline failure: complete collapse handler
    # ========================================================================

    async def play_pipeline_failure(self) -> None:
        """
        Play the pre-recorded total-failure message and close cleanly.

        Called by router.py if handle() raises an exception before the
        call reaches a stable state. The message gives the caller something
        to hear before the line drops.
        """
        logger.error("[ms_conn] playing pipeline failure message")
        try:
            await self.tts_text_queue.put(PIPELINE_FAILURE_PHRASE)
            # Give 4 seconds for TTS to play before closing
            await asyncio.sleep(4.0)
        except Exception:
            pass
        finally:
            self._stop_event.set()

    # ========================================================================
    # Global 10-second silence safety net
    # ========================================================================

    async def _silence_safety_net(self) -> None:
        """Last-resort 10-second dead-air backstop.

        Wakes every 10 seconds and emits a re-ask phrase when ALL of the
        following conditions hold simultaneously:

          1. No TTS has started and no transcript has arrived for ≥ 10 s.
          2. LLM is not busy (_llm_busy is False).
          3. SilenceHandler is not actively playing TTS (_tts_playing is False).
          4. No DTMF input is expected (_is_dtmf_expected returns False).
          5. The no-input watchdog is not already running.
          6. v3 phone DTMF collection is not active.
          7. (Part A) The watchdog has NOT already completed a re-ask for this
             q_gen (_reask_completed is False).  If it has, suppress entirely
             and log — duplicate re-asks are unhelpful and confusing.

        Part B — maximum 2 fires per q_gen:
          Fire 1: context-aware soft re-ask — slot phrase if v3_awaiting_slot_selection,
                  else replay last_question (prefixed "Sorry — I can't quite hear you —")
                  or fall back to "I'm just having a little trouble hearing you — apologies about that."
          Fire 2: graceful close phrase, wait for TTS, hang up cleanly.

        Part C — on graceful close, sets session["no_audio_close"] = True so
        the post-call SMS router sends a "connection issue" message rather than
        an "abandoned booking" message.

        This is intentionally narrow — it never fires during normal call flow
        because at least one of conditions 2–5 is always true then.
        """
        # Poll cadence for the dead-air backstop.  Kept at 10.0: the A2 hole was
        # closed by lowering the post-reask suppression window (20s → 8s) plus
        # resetting the dead-air anchor on TTS-finish (see _delayed_tts_finished).
        # A 5s interval was tried but made the two-strike graceful-close hang up
        # ~5s after the first re-ask — too fast, it cut off a caller mid-booking.
        _INTERVAL = 10.0
        _PHRASE_2 = (
            "I'm not able to hear you at the moment — "
            "feel free to call back and we'll get that sorted for you."
        )

        # Per-q_gen safety-net state (local vars survive for the call lifetime).
        _safety_net_count = 0
        _tracked_q_gen    = -1

        await self._wait_for_start("silence_safety_net")
        # Seed timestamp so we don't fire in the first interval of the call.
        self._last_audio_or_transcript_ts = time.monotonic()

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(_INTERVAL)
                if self._stop_event.is_set():
                    break

                _now   = time.monotonic()
                _since = _now - self._last_audio_or_transcript_ts

                # 1. Dead-air window
                if _since < _INTERVAL:
                    continue

                # 2. LLM currently generating
                if self._llm_busy:
                    continue

                # 3. TTS currently playing.
                #    Bug A backstop: _tts_playing can strand True when a chunk
                #    starts (bumping _tts_last_start_ts) but its finish callback
                #    never fires (chunk cancelled by barge-in, or an out-of-order
                #    terminal whose earlier chunk was eaten). Every other chunk's
                #    finish then sees chunk_started_at < _tts_last_start_ts and
                #    refuses to clear the flag (on_tts_finished ~2313), so BOTH
                #    silence nets stay inhibited → dead air until hangup.
                #    Use the precise cumulative playout clock to distinguish a
                #    genuinely-playing chunk (now < playout_end) from a stale
                #    flag. We only reach here when _since >= _INTERVAL (≥10s since
                #    the last chunk START), so the playout clock already reflects
                #    the current chunk — no risk of cutting a mid-flight chunk.
                if getattr(self._silence_handler, "_tts_playing", False):
                    _playout_end = getattr(self, "_tts_playout_end_mono", 0.0)
                    if _playout_end > 0.0 and _now < _playout_end + 2.0:
                        # Audio genuinely still playing — suppress.
                        continue
                    # No audio scheduled in flight but flag still set → stale.
                    logger.warning(
                        "[ms_safety_net] _tts_playing stale (playout ended "
                        "%.1fs ago, flag still set) — force-clearing "
                        "(Bug A backstop) q_gen=%d",
                        (_now - _playout_end) if _playout_end > 0.0 else -1.0,
                        getattr(self._silence_handler, "_q_gen", 0),
                    )
                    self._silence_handler._tts_playing = False
                    # fall through — safety net proceeds to recover

                # 4. DTMF expected — watchdog stands down in keypad mode
                if _is_dtmf_expected(self.session):
                    continue

                # 5. No-input watchdog is already active
                _wd = self._silence_handler._no_input_watchdog_task
                if _wd and not _wd.done():
                    continue

                # 6. v3 phone DTMF collection active.
                #    The generic safety net below escalates to a graceful HANGUP
                #    on its 2nd fire, so it must NEVER run during keypad entry.
                #    But fully suppressing it left a long dead-air hole when the
                #    caller never started typing (BUG-3: 29s silence, Call 8 — >25s,
                #    a G24 fail).  Emit ONE gentle, NON-terminal nudge if the caller
                #    has typed nothing for ~15s, then stay quiet (never repeats,
                #    never hangs up).  Safe against mid-dial firing two ways: each
                #    DTMF digit resets the dead-air anchor (_handle_dtmf ~4396) AND
                #    fills phone_dtmf_buffer — so a non-empty buffer means the
                #    caller has begun dialling and we skip the nudge entirely.
                if self.session.get("v3_phone_dtmf_active"):
                    # Threshold == _INTERVAL (10s): with the 10s poll cadence the
                    # nudge then fires in the [10s, 20s) window regardless of how
                    # the poll aligns with the prompt — worst case ~20s, a safe
                    # margin under the 25s G24 fail line (a 15s threshold could
                    # land as late as ~25s and brush the line; observed 22.9s).
                    if (
                        _since >= 10.0
                        and not self.session.get("_phone_dtmf_nudged")
                        and not self.session.get("phone_dtmf_buffer")
                        and not self._llm_busy
                        and not getattr(
                            self._silence_handler, "_tts_playing", False
                        )
                    ):
                        self.session["_phone_dtmf_nudged"] = True
                        self.session["tts_inhibit"] = False
                        _dtmf_nudge = (
                            "Take your time — just type the number on your "
                            "keypad whenever you're ready."
                        )
                        await self.tts_text_queue.put(
                            _WATCHDOG_REASK_MARKER + _dtmf_nudge
                        )
                        self._last_audio_or_transcript_ts = time.monotonic()
                        logger.info(
                            "[ms_safety_net] phone-DTMF one-time nudge"
                            " (since=%.1fs, buffer empty — caller not dialling)",
                            _since,
                        )
                    continue

                # ── Sync q_gen counter: reset count on new question generation ─
                _current_q_gen = getattr(self._silence_handler, "_q_gen", 0)
                if _current_q_gen != _tracked_q_gen:
                    _safety_net_count = 0
                    _tracked_q_gen    = _current_q_gen

                # 7. Part A — watchdog already completed re-ask for this q_gen.
                # Suppress within a 20s window to prevent an immediate double-
                # prompt.  After 20s with no transcript the watchdog retired
                # without ever getting a response (caller spoke but STT missed
                # it) — override the suppression so the safety net fires again.
                if getattr(self._silence_handler, "_reask_completed", False):
                    if _since < 8.0:
                        logger.info(
                            "[ms_safety_net] suppressed — watchdog reask done, "
                            "within 8s window (since=%.1fs q_gen=%d)",
                            _since, _current_q_gen,
                        )
                        continue
                    logger.warning(
                        "[ms_safety_net] watchdog reask done but _since=%.1fs"
                        " ≥8s — overriding suppression (q_gen=%d); "
                        "watchdog retired without transcript",
                        _since, _current_q_gen,
                    )

                # ── Part B: count-bounded re-ask / graceful close ─────────────
                _safety_net_count += 1

                if _safety_net_count == 1:
                    # First fire — standard soft re-ask
                    logger.warning(
                        "[ms_safety_net] 10s dead-air — emitting safety re-ask "
                        "(since=%.1fs llm_busy=%s tts_playing=%s q_gen=%d)",
                        _since,
                        self._llm_busy,
                        getattr(self._silence_handler, "_tts_playing", False),
                        _current_q_gen,
                    )
                    # Context-aware re-ask: slot phrase only when caller is actually
                    # choosing from a presented list.  At all other call stages use
                    # the last stored question (if any) or a neutral "still there?"
                    # prompt so we never ask about "days" before any slots have been
                    # shown to the caller.
                    if self.session.get("v3_location_q_active"):
                        # Location still unresolved (STT can't catch the clinic
                        # name — "ousto"/"ouston"/"the clinic").  Do NOT fall
                        # through to the generic "how can I help today?" reset:
                        # that throws away the booking/location context and
                        # forces the caller to start over (stress test
                        # 2026-06-12 12:23).  Mirror the watchdog location-ladder
                        # rung-1 biased binary confirm and solicit the
                        # STT-robust "use this clinic" phrase, routing the next
                        # answer through the existing use-this-clinic handler.
                        _phrase_1 = _LOC_RUNG2_CONFIRM
                        self.session["v3_awaiting_use_this_clinic"] = True
                        self.session["v3_use_this_clinic_bias"] = "alcester"
                        self.session["last_question"] = _phrase_1
                        self.session["last_bot_prompt"] = _phrase_1
                    elif self.session.get("v3_awaiting_slot_selection"):
                        _phrase_1 = "Still with you — which of those days suits you?"
                    else:
                        _last_q = getattr(self._silence_handler, "last_question", "")
                        # Never replay the opening greeting as a "re-ask".
                        # last_question can still hold the greeting (q_gen=1),
                        # which produced the jarring mid-call
                        # "Sorry — I can't quite hear you — Hi there, I'm Susie…
                        # press 1…" (stress test 2026-06-12).  A re-ask must be a
                        # short prompt, so skip the greeting (or any over-long
                        # stored line) and fall back to a brief re-anchor.
                        _lq_low = _last_q.lower()
                        _is_greeting = any(
                            m in _lq_low
                            for m in ("ai receptionist", "press 1", "i'm susie")
                        )
                        if _last_q and not _is_greeting and len(_last_q) <= 90:
                            # Bug C: strip the stored question's own leading
                            # filler so we don't stack "Sorry — … — No problem —".
                            _phrase_1 = (
                                "Sorry — I can't quite hear you — "
                                f"{_strip_leading_filler(_last_q)}"
                            )
                        else:
                            _phrase_1 = (
                                "Sorry, I can't quite hear you —"
                                " how can I help today?"
                            )
                    # Clear tts_inhibit in case a stale barge-in flag is blocking TTS.
                    self.session["tts_inhibit"] = False
                    await self.tts_text_queue.put(_WATCHDOG_REASK_MARKER + _phrase_1)
                    # Reset anchor to avoid immediate re-fire on next wake.
                    self._last_audio_or_transcript_ts = time.monotonic()

                else:
                    # Second fire — graceful close then hangup.
                    # Do NOT fire further re-asks after this point.
                    logger.warning(
                        "[ms_safety_net] max re-asks reached — executing graceful "
                        "close (count=%d since=%.1fs q_gen=%d)",
                        _safety_net_count - 1,
                        _since,
                        _current_q_gen,
                    )
                    # Part C: flag for post-call SMS routing (no_audio outcome)
                    self.session["tts_inhibit"]    = False
                    self.session["no_audio_close"] = True
                    await self.tts_text_queue.put(_WATCHDOG_REASK_MARKER + _PHRASE_2)
                    # Wait for TTS to finish (up to 5 s); brief start-delay then poll.
                    _tts_deadline = time.monotonic() + 5.0
                    await asyncio.sleep(0.5)   # give TTS a moment to start
                    while (
                        getattr(self._silence_handler, "_tts_playing", False)
                        and time.monotonic() < _tts_deadline
                    ):
                        await asyncio.sleep(0.2)
                    # Honour any remaining deadline regardless of _tts_playing state
                    _remaining = _tts_deadline - time.monotonic()
                    if _remaining > 0:
                        await asyncio.sleep(_remaining)
                    logger.info(
                        "[ms_safety_net] graceful close executed after 2 safety "
                        "re-asks q_gen=%d",
                        _current_q_gen,
                    )
                    self._stop_event.set()
                    return

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_safety_net] fatal: %r", exc)

    # ========================================================================
    # Cleanup
    # ========================================================================

    async def _cleanup(self) -> None:
        """
        Called once when the call ends.

        - Mark session flags as inactive
        - Save final session to ms_session: prefix
        - Mirror-save to call: prefix so /twilio/status webhook can read it
        """
        if not self.call_sid:
            return

        logger.info("[ms_conn] cleanup call_sid=%s stable=%s", self.call_sid, self._call_stable)

        # Deregister from the active-handler map
        _was_registered = self.call_sid in _active_handlers
        _active_handlers.pop(self.call_sid, None)
        logger.info(
            "[ms_conn] REMOVE sid=%s reason=cleanup was_registered=%s remaining=%d",
            self.call_sid, _was_registered, len(_active_handlers),
        )

        # Cancel the silence handler timer so it doesn't fire after the call ends
        self._silence_handler.cancel()

        # Spec N: discard any pending transcript — the call has ended
        if self.pending_transcript is not None:
            logger.info("[ms_conn] pending transcript discarded on cleanup")
            self.pending_transcript = None

        self.session["ws_connected"]          = False
        self.session["stt_active"]            = False
        self.session["tts_active"]            = False
        self.session["llm_generation_active"] = False

        # Flush structured per-call log
        call_logger = getattr(self, "_call_logger", None)
        if call_logger is not None:
            try:
                success = bool(self.session.get("booking_confirmed") or self.session.get("confirmation_sms_sent"))
                if self.session.get("graceful_exit"):
                    reason = "graceful_exit"
                elif self.session.get("booking_confirmed"):
                    reason = "booked"
                elif self.session.get("transfer_attempted"):
                    reason = "transferred"
                else:
                    reason = "caller_hung_up"
                call_logger.complete(success=success, reason=reason)
                await call_logger.flush()
            except Exception as _cl_exc:
                logger.error("[ms_conn] call_logger flush error: %r", _cl_exc)

        # Persist final call outcome to session for post-call reporting.
        # Additive — used by theorem_v3 free-form loop and any downstream
        # reporting; legacy FlowEngine paths are unaffected.
        if self.session.get("booking_confirmed"):
            self.session["call_outcome"] = "booked"
        elif (
            self.session.get("cancel_confirmed")
            or self.session.get("call_outcome_logged") == "cancelled"
        ):
            self.session["call_outcome"] = "cancelled"
            logger.info("[ms_conn v3] call_outcome=cancelled")
        elif self.session.get("transfer_attempted"):
            self.session["call_outcome"] = "transferred"
        else:
            self.session["call_outcome"] = "no_action"

        try:
            await save_session(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] cleanup save failed: %r", exc)

        # Mirror-save to the legacy call: key the /twilio/status webhook reads.
        # CRITICAL: /status loads via redis_store.get_session(), which keys on
        # _session_key() — i.e. call:{sid}:{hmac8} when SESSION_SECRET is set.
        # Writing a bare "call:{sid}" key (as this previously did) means /status
        # reads an empty default session once SESSION_SECRET is enabled on Render,
        # losing the entire enriched session (name, phone, phone_confirmed,
        # last_bot_prompt) → no SMS + wrong outcome classification (2026-06-23 bug).
        # Use _session_key() so the mirror lands on exactly the key /status reads,
        # in both secret-set and secret-unset modes.
        try:
            import copy
            import json as _json
            from app.storage.redis_store import redis_client, _session_key
            if redis_client:
                await redis_client.set(
                    _session_key(self.call_sid),
                    _json.dumps(copy.deepcopy(self.session)),
                    ex=7200,
                )
                logger.info("[ms_conn] mirrored to call: prefix call_sid=%s", self.call_sid)
        except Exception as exc:
            logger.warning("[ms_conn] mirror-save failed: %r", exc)

        # Notify staff if caller asked for a human but didn't get through
        if (
            self.session.get("human_requested")
            and not self.session.get("booking_confirmed")
            and not self.session.get("transfer_attempted")
        ):
            try:
                import os as _os
                from app.notifications.sms import send_sms as _send_sms
                _staff_phone = _os.getenv("THEOREM_NOTIFICATION_SMS")
                _caller      = (
                    self.session.get("twilio_from_local")
                    or self.session.get("twilio_from")
                    or "unknown number"
                )
                if _staff_phone:
                    await _send_sms(
                        to=_staff_phone,
                        message=(
                            f"Hi Mark, a caller just asked to speak to you "
                            f"but didn't get through. Their number is {_caller}. "
                            f"Give them a call back when you get a chance. — Susie"
                        ),
                    )
                    logger.info("[ms_conn] staff notify SMS sent → %s", _staff_phone)
            except Exception as _notify_exc:
                logger.warning("[ms_conn] staff notify SMS failed: %r", _notify_exc)

    # ========================================================================
    # Internal helper
    # ========================================================================

    async def _wait_for_start(self, loop_name: str) -> None:
        """Wait for the 'start' event to be processed before entering a loop."""
        try:
            await asyncio.wait_for(
                self._started_event.wait(),
                timeout=TWILIO_STARTED_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("[ms_conn] %s: timed out waiting for start event", loop_name)
            raise asyncio.CancelledError