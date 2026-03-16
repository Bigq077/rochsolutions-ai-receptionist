# app/media_streams/session.py
"""
Session storage for the Media Streams parallel voice pipeline.

Uses Redis with key prefix "ms_session:" to keep the media streams
sessions completely separate from the existing webhook sessions
(prefix "call:" in redis_store.py).

This allows both pipelines to run simultaneously without interference:
  - Legacy:      call:{call_sid}[:{hmac8}]  (redis_store.py)
  - Media Streams: ms_session:{call_sid}    (this module)

Session structure carries over ALL existing fields from DEFAULT_SESSION
in redis_store.py and adds new Media Streams-specific fields.

TTL: 2 hours (7200 seconds) — covers the longest realistic calls
     plus post-call status processing.

JSON serialisation: all values must be JSON-serialisable.
  - datetime objects: store as ISO strings
  - asyncio Tasks: never stored (runtime-only)
  - sets/frozensets: convert to lists before storing
"""
from __future__ import annotations

import copy
import json
import logging
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Call state machine
# ---------------------------------------------------------------------------

class CallState(str, Enum):
    GREETING             = "GREETING"
    COLLECT_REASON       = "COLLECT_REASON"
    COLLECT_DURATION     = "COLLECT_DURATION"
    CONFIRM_ASSESSMENT   = "CONFIRM_ASSESSMENT"
    NEW_OR_RETURNING     = "NEW_OR_RETURNING"
    COLLECT_AVAILABILITY = "COLLECT_AVAILABILITY"
    PRESENT_SLOTS        = "PRESENT_SLOTS"
    COLLECT_NAME         = "COLLECT_NAME"
    COLLECT_PHONE        = "COLLECT_PHONE"
    CONFIRM_BOOKING      = "CONFIRM_BOOKING"
    TRANSFER             = "TRANSFER"
    COMPLETE             = "COMPLETE"


# Ordered list for forward-only enforcement (TRANSFER/COMPLETE handled separately)
_STATE_ORDER = [
    CallState.GREETING,
    CallState.COLLECT_REASON,
    CallState.COLLECT_DURATION,
    CallState.CONFIRM_ASSESSMENT,
    CallState.NEW_OR_RETURNING,
    CallState.COLLECT_AVAILABILITY,
    CallState.PRESENT_SLOTS,
    CallState.COLLECT_NAME,
    CallState.COLLECT_PHONE,
    CallState.CONFIRM_BOOKING,
    CallState.COMPLETE,
]


def get_call_state(session: Dict[str, Any]) -> CallState:
    """Return the current CallState, defaulting to GREETING on unknown values."""
    try:
        return CallState(session.get("state", "GREETING"))
    except ValueError:
        return CallState.GREETING


def advance_state(session: Dict[str, Any], new_state: CallState) -> None:
    """
    Move state forward only. Never moves backward.
    TRANSFER and COMPLETE are always allowed regardless of current state.
    Logs every transition.
    """
    current = get_call_state(session)
    try:
        if _STATE_ORDER.index(new_state) > _STATE_ORDER.index(current):
            logger.info("[ms_state] %s → %s", current.value, new_state.value)
            session["state"] = new_state.value
    except ValueError:
        # new_state not in _STATE_ORDER (TRANSFER or COMPLETE)
        if new_state in (CallState.TRANSFER, CallState.COMPLETE):
            logger.info("[ms_state] %s → %s", current.value, new_state.value)
            session["state"] = new_state.value

# Redis key prefix — distinct from legacy "call:" prefix
MS_SESSION_PREFIX = "ms_session:"

# Session TTL: 2 hours
SESSION_TTL_SECONDS = 7200

# ---------------------------------------------------------------------------
# Default session structure
# ---------------------------------------------------------------------------

DEFAULT_MS_SESSION: Dict[str, Any] = {
    # ------------------------------------------------------------------ #
    # Fields carried over from redis_store.py DEFAULT_SESSION
    # ------------------------------------------------------------------ #

    # Core state
    "intent":         None,
    "state":          "GREETING",
    "greeting_delivered": False,  # set True the moment greeting fires; guards re-entry
    "collected":      {},       # mutable — always deep-copied
    "miss_count":     0,
    "error_count":    0,
    "last_bot_prompt": "",
    "last_question":  "",

    # Call identity
    "call_sid":       "",
    "session_id":     "",
    "clinic_id":      None,

    # Location
    "location_selected":          False,
    "selected_location":          None,
    "location_miss":              0,
    "location_redirect_count":    0,

    # Phase 3 / conversation history
    "conversation_history": [],  # [{role: "user"|"assistant", content: str}]
    "turns":                [],  # [{role: str, text: str}] — used by SMS router

    # Timestamps
    "call_start_time": None,     # ISO timestamp set on first handle_turn call

    # Insurance
    "insurance_flagged": False,
    "insurance_info":    {},

    # Booking state
    "last_offered_slots":  [],
    "slot_labels":         [],
    "acuity_booking_id":   None,
    "calendar_status":     None,

    # Workflow flags
    "manual_followup_needed":   False,
    "manual_followup_reason":   None,
    "confirmation_sms_sent":    False,
    "call_summary_logged":      False,
    "transfer_attempted":       False,
    "transfer_failed_status":   None,
    "request_transfer":         False,

    # Fast-path state (carried over from DEFAULT_SESSION)
    "phone_part_one":              None,   # First 5 digits of caller-dictated phone
    "phone_part_two":              None,   # Last 6 digits of caller-dictated phone
    "selected_slot":               None,   # Slot chosen by fast-path slot-selection
    "_fast_path_phone_confirmed":  False,  # Caller confirmed caller-ID number
    "_fast_path_slot_confirmed":   False,  # Caller confirmed chosen slot
    "_fast_path_final_confirmed":  False,  # Caller confirmed final booking summary
    "_fast_path_correction_needed": False, # Caller said "no" to a confirmation
    "_fast_path_full_phone":       None,   # Assembled 11-digit phone from two-part

    # New booking flow intake fields
    "reason":              None,   # Why caller is booking (their condition)
    "duration":            None,   # How long they've had the condition
    "assessment_confirmed": None,  # True when caller confirms physiotherapy assessment
    "phone_number":        None,   # Caller phone number (single field, replaces two-part)

    # Caller phone numbers from Twilio
    "twilio_from":       None,   # Caller's inbound number (E.164)
    "twilio_from_local": None,   # UK local format (07xxxxxxxxx)
    "twilio_to":         None,   # Dialled number (used for clinic_id lookup)
    "phone_from_twilio": False,  # True when phone was auto-detected from caller-ID

    # ------------------------------------------------------------------ #
    # New fields specific to the Media Streams parallel pipeline
    # ------------------------------------------------------------------ #

    # WebSocket identifiers
    "stream_sid": None,          # Twilio stream SID from "start" event

    # Connection state flags
    "ws_connected":          False,  # True while Twilio WS is open
    "stt_active":            False,  # True while AssemblyAI session is live
    "tts_active":            False,  # True while ElevenLabs TTS is streaming audio

    # LLM / generation state
    "llm_generation_active": False,  # True while Claude is generating tokens
    "current_chunk_index":   0,      # Incremented for each TTS chunk sent to Twilio

    # Timestamps for latency tracking and bad-line detection
    "last_audio_sent_at":    None,   # ISO timestamp of last TTS audio packet sent

    # Fast-path diagnostics
    "fast_path_last_resolved": None, # FastPathTurnType value of last fast-path match

    # Slot presentation guard (Fix 2) — set True the moment slots are first
    # presented to the caller so the LLM never re-presents them.
    "slots_presented":         False,

    # ------------------------------------------------------------------ #
    # Linear flow engine fields (flow.py / FlowEngine)
    # ------------------------------------------------------------------ #
    "flow_step":           0,        # index into FLOW[] — advances forward only
    "flow_started":        False,    # True after first caller utterance starts flow
    "reason":              None,     # step 0 — why the caller is booking
    "duration":            None,     # step 1 — how long they've had the condition
    "assessment_confirmed": None,    # step 2 — accepted physio assessment recommendation
    "new_or_returning":    None,     # step 3 — "new" or "returning"
    "availability":        None,     # step 4 — caller's preferred days/times
    "slots_count":         0,        # set by llm_stream when slots are returned
    "selected_slot":       None,     # step 5 — the slot the caller chose
    "full_name":           None,     # step 6 — caller's full name
    "phone_number":        None,     # step 7 — caller's phone number
    "booking_confirmed":   None,     # step 8 — booking acknowledged
    "last_question":       "",       # last question Susie asked (for SilenceHandler re-ask)
}


def _fresh_session() -> Dict[str, Any]:
    """Return a deep copy of DEFAULT_MS_SESSION so mutable fields never leak."""
    return copy.deepcopy(DEFAULT_MS_SESSION)


def _session_key(call_sid: str) -> str:
    """Build the Redis key for a Media Streams session."""
    return f"{MS_SESSION_PREFIX}{call_sid}"


# ---------------------------------------------------------------------------
# Redis client (lazy import)
# ---------------------------------------------------------------------------

def _get_redis():
    """
    Return the shared Redis async client from app.storage.redis_store.
    Returns None if Redis is not configured.
    """
    try:
        from app.storage.redis_store import redis_client
        return redis_client
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Public session API
# ---------------------------------------------------------------------------

async def create_session(call_sid: str, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Create a new Media Streams session and persist it in Redis.

    If initial is provided, its values are merged into the default session.
    Always sets call_sid on the session.

    Returns the created session dict.
    """
    session = _fresh_session()
    if initial:
        for k, v in initial.items():
            session[k] = v
    session["call_sid"] = call_sid

    await _save(call_sid, session)
    logger.info("[ms_session] created call_sid=%s", call_sid)
    return session


async def get_session(call_sid: str) -> Dict[str, Any]:
    """
    Retrieve a session from Redis by call_sid.

    If the session does not exist, returns a fresh default session
    (does NOT persist it — caller must call save_session explicitly).

    Fills in any missing defaults for forward-compatibility when new
    fields are added to DEFAULT_MS_SESSION.
    """
    if not call_sid:
        return _fresh_session()

    redis = _get_redis()
    if not redis:
        return _fresh_session()

    key = _session_key(call_sid)
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.warning("[ms_session] Redis get failed call_sid=%s: %r", call_sid, exc)
        return _fresh_session()

    if not raw:
        return _fresh_session()

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _fresh_session()

        # Fill in any missing defaults (forward-compatibility)
        defaults = _fresh_session()
        for k, v in defaults.items():
            data.setdefault(k, v)

        # Ensure collected is always a dict
        if not isinstance(data.get("collected"), dict):
            data["collected"] = {}

        # Ensure call_sid is always set
        data.setdefault("call_sid", call_sid)

        return data

    except Exception as exc:
        logger.warning("[ms_session] session decode failed call_sid=%s: %r", call_sid, exc)
        return _fresh_session()


async def update_session(call_sid: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply partial updates to an existing session and persist.

    Fetches the current session, merges updates, and saves back.
    Returns the updated session dict.

    Use this for targeted field updates to avoid overwriting concurrent
    changes (though in practice a single asyncio loop makes this safe).
    """
    session = await get_session(call_sid)
    session.update(updates)
    session["call_sid"] = call_sid
    await _save(call_sid, session)
    return session


async def save_session(call_sid: str, session: Dict[str, Any]) -> None:
    """
    Persist the full session dict to Redis.

    This is the primary save path — call this after any session mutation.
    """
    if not call_sid:
        return
    session["call_sid"] = call_sid
    await _save(call_sid, session)


async def delete_session(call_sid: str) -> None:
    """
    Remove a session from Redis (e.g. on call end after summary logged).
    Safe no-op if session does not exist.
    """
    redis = _get_redis()
    if not redis or not call_sid:
        return
    try:
        await redis.delete(_session_key(call_sid))
        logger.info("[ms_session] deleted call_sid=%s", call_sid)
    except Exception as exc:
        logger.warning("[ms_session] delete failed call_sid=%s: %r", call_sid, exc)


async def get_or_create_session(
    call_sid: str,
    initial: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return an existing session if it exists in Redis, otherwise create one.

    This is the preferred entry point in the WebSocket handler — avoids
    the risk of overwriting a session that was seeded by /twilio/voice.
    """
    redis = _get_redis()
    if redis:
        key = _session_key(call_sid)
        try:
            raw = await redis.get(key)
            if raw:
                # Session already exists — return it (with defaults filled in)
                return await get_session(call_sid)
        except Exception as exc:
            logger.warning("[ms_session] existence check failed call_sid=%s: %r", call_sid, exc)

    # No existing session — create one
    return await create_session(call_sid, initial=initial)


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def field_already_set(session: Dict[str, Any], field_name: str) -> bool:
    """
    Return True if a session field has already been set (is non-empty / truthy).

    Handles the common pattern of checking whether a step in the booking
    workflow has already been completed so the LLM or fast-path can skip it.

    Treats empty string, None, False, 0, [], {} as "not set".
    Treats any other truthy value as "already set".

    Examples:
      field_already_set(session, "selected_location")  -> True if location chosen
      field_already_set(session, "phone_part_one")     -> True if 5 digits captured
      field_already_set(session, "collected")          -> True if any collected data
    """
    value = session.get(field_name)
    # Empty dict/list is falsy — treat as not set
    if isinstance(value, (dict, list)):
        return bool(value)
    return bool(value)


def get_collected_field(session: Dict[str, Any], field_name: str) -> Optional[str]:
    """
    Convenience getter for fields nested inside session["collected"].

    Returns None if collected is missing or field not found.
    """
    collected = session.get("collected")
    if not isinstance(collected, dict):
        return None
    return collected.get(field_name)


# ---------------------------------------------------------------------------
# Internal save helper
# ---------------------------------------------------------------------------

async def _save(call_sid: str, session: Dict[str, Any]) -> None:
    """
    Serialise and persist a session dict to Redis with TTL.
    Best-effort: logs warning on failure but never raises.
    """
    redis = _get_redis()
    if not redis:
        return

    # Defensive: ensure collected is always a dict
    if not isinstance(session.get("collected"), dict):
        session["collected"] = {}

    try:
        payload = json.dumps(session)
        await redis.set(
            _session_key(call_sid),
            payload,
            ex=SESSION_TTL_SECONDS,
        )
    except TypeError as exc:
        # JSON serialisation failure — log the offending keys and save a clean copy
        logger.error(
            "[ms_session] JSON serialisation failed call_sid=%s: %r — saving stripped copy",
            call_sid, exc,
        )
        _safe_save_stripped(redis, call_sid, session)
    except Exception as exc:
        logger.warning("[ms_session] Redis save failed call_sid=%s: %r", call_sid, exc)


def _safe_save_stripped(redis, call_sid: str, session: Dict[str, Any]) -> None:
    """
    Synchronous fallback: strip non-serialisable values and save.
    Only called when json.dumps raises TypeError.
    """
    stripped = {}
    for k, v in session.items():
        try:
            json.dumps(v)
            stripped[k] = v
        except TypeError:
            stripped[k] = str(v)  # coerce to string as last resort
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            loop.create_task(redis.set(
                _session_key(call_sid),
                json.dumps(stripped),
                ex=SESSION_TTL_SECONDS,
            ))
    except Exception:
        pass  # If even this fails, accept the loss
