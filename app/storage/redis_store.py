# app/storage/redis_store.py
from __future__ import annotations

import hashlib
import hmac
import json
import copy
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.config import REDIS_URL, SESSION_SECRET

redis_client = None

if REDIS_URL:
    import redis.asyncio as redis  # type: ignore
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DEFAULT_SESSION: Dict[str, Any] = {
    # Core state
    "intent": None,
    "state": "TRIAGE",              # Used by legacy path; Phase 3 ignores this
    "collected": {},                # IMPORTANT: mutable (deep-copied on use)
    "miss_count": 0,
    "error_count": 0,
    "last_bot_prompt": "",
    "call_sid": "",
    "session_id": "",
    "clinic_id": None,
    # Location
    "location_selected": False,
    "selected_location": None,
    "location_miss": 0,
    "location_redirect_count": 0,  # guards against infinite /voice redirect loop
    # Phase 3 fields
    "conversation_history": [],     # [{role: "user"|"assistant", content: str}]
    "turns": [],                    # [{role: str, text: str}] used by SMS router
    "call_start_time": None,        # ISO timestamp set on first handle_turn call
    # Insurance
    "insurance_flagged": False,
    "insurance_info": {},
    # Booking state
    "last_offered_slots": [],
    "slot_labels": [],
    "acuity_booking_id": None,
    "calendar_status": None,
    # Workflow flags
    "manual_followup_needed": False,
    "manual_followup_reason": None,
    "confirmation_sms_sent": False,
    "call_summary_logged": False,
    "transfer_attempted": False,
    "transfer_failed_status": None,
    "request_transfer": False,
    # Fast-path state
    "phone_part_one": None,          # First 5 digits of a caller-dictated phone number
    "phone_part_two": None,          # Last 6 digits of a caller-dictated phone number
    "selected_slot": None,           # Slot chosen by fast-path slot-selection handler
    "_fast_path_phone_confirmed": False,   # Caller confirmed caller-ID number is correct
    "_fast_path_slot_confirmed": False,    # Caller confirmed chosen slot
    "_fast_path_final_confirmed": False,   # Caller confirmed final booking summary
    "_fast_path_correction_needed": False, # Caller said "no" to a confirmation
    "_fast_path_full_phone": None,         # Assembled 11-digit phone from two-part collection
}


def _fresh_default_session() -> Dict[str, Any]:
    """
    Always return a deep copy so nested dicts like collected don't leak between calls.
    """
    return copy.deepcopy(DEFAULT_SESSION)


# ============================
# Session key helper
# ============================

def _session_key(call_sid: str) -> str:
    """
    Build the Redis key for a call session.

    When SESSION_SECRET is set, the key gains an 8-hex-char HMAC suffix derived
    from the secret + call_sid.  This makes session keys unguessable even if an
    attacker can enumerate the Redis keyspace, because they cannot compute the
    HMAC without the server secret.

    Without SESSION_SECRET the key is plain ``call:{call_sid}`` for backwards
    compatibility.  Changing SESSION_SECRET invalidates all in-flight sessions
    (90-min TTL means impact is minimal — at most one active call per worker).
    """
    if SESSION_SECRET:
        tag = hmac.new(
            SESSION_SECRET.encode(),
            call_sid.encode(),
            hashlib.sha256,
        ).hexdigest()[:8]
        return f"call:{call_sid}:{tag}"
    return f"call:{call_sid}"


# ============================
# Call session helpers
# ============================
async def get_session(call_sid: str) -> Dict[str, Any]:
    if not call_sid or not redis_client:
        return _fresh_default_session()

    key = _session_key(call_sid)
    raw = await redis_client.get(key)
    if not raw:
        return _fresh_default_session()

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _fresh_default_session()

        # fill missing defaults (do NOT overwrite existing)
        defaults = _fresh_default_session()
        for k, v in defaults.items():
            data.setdefault(k, v)

        # ensure collected is always a dict
        if not isinstance(data.get("collected"), dict):
            data["collected"] = {}

        # store the call sid into the session if missing
        data.setdefault("call_sid", call_sid)

        return data
    except Exception:
        return _fresh_default_session()


async def save_session(call_sid: str, session: Dict[str, Any]) -> None:
    if not call_sid or not redis_client:
        return

    key = _session_key(call_sid)

    # Defensive: ensure serializable + structure sane
    if not isinstance(session, dict):
        session = _fresh_default_session()
    if not isinstance(session.get("collected"), dict):
        session["collected"] = {}

    # keep it consistent
    session["call_sid"] = call_sid

    await redis_client.set(key, json.dumps(session), ex=60 * 90)  # 90 min TTL — covers long calls


# ============================
# Generic JSON helpers (OAuth, etc.)
# ============================
async def redis_set_json(
    key: str,
    value: Dict[str, Any],
    ttl_seconds: Optional[int] = None,
) -> None:
    if not redis_client:
        return

    payload = json.dumps(value)
    if ttl_seconds:
        await redis_client.set(key, payload, ex=ttl_seconds)
    else:
        await redis_client.set(key, payload)


async def redis_get_json(key: str) -> Optional[Dict[str, Any]]:
    if not redis_client:
        return None

    raw = await redis_client.get(key)
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ============================
# DELETE helpers
# ============================
async def redis_delete(key: str) -> None:
    """
    Delete a key from Redis.
    Used to wipe broken google_tokens, sessions, etc.
    Safe no-op if Redis isn't configured.
    """
    if not redis_client:
        return
    try:
        await redis_client.delete(key)
    except Exception:
        pass


async def redis_delete_prefix(prefix: str) -> int:
    """
    Delete all keys that start with prefix.
    Example: prefix="call:" to wipe all call sessions.
    Returns number of keys deleted.
    """
    if not redis_client:
        return 0

    deleted = 0
    try:
        # Use scan_iter to avoid blocking Redis
        keys: List[str] = []
        async for k in redis_client.scan_iter(match=f"{prefix}*"):
            keys.append(k)

        if keys:
            deleted = await redis_client.delete(*keys)
    except Exception:
        return 0

    return int(deleted or 0)


# Backwards-compatible alias (your routes import this name)
redis_delete_key = redis_delete


# ============================
# Optional: health helpers
# ============================
async def redis_ping() -> bool:
    """
    Useful to debug whether Redis is connected from Render.
    """
    if not redis_client:
        return False
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False


# ============================
# Pending name-confirmation helpers (Stage 2)
# ============================

_PENDING_NAME_TTL = 60 * 60 * 24  # 24 hours


def _pending_name_key(normalized_phone: str) -> str:
    return f"pending_name:{normalized_phone}"


async def create_pending_name_confirmation(
    phone: str,
    first_name: str,
    appointment_id: str,
    location: str,
) -> None:
    """
    Store a pending-name-confirmation record keyed by normalized phone.
    Non-blocking: safe no-op if Redis is unavailable.
    """
    if not redis_client:
        return
    record = {
        "pending_id": str(uuid.uuid4()),
        "phone": phone,
        "first_name": first_name,
        "appointment_id": appointment_id,
        "location": location,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sms_reply_received": False,
    }
    await redis_client.set(
        _pending_name_key(phone),
        json.dumps(record),
        ex=_PENDING_NAME_TTL,
    )


async def get_pending_name_confirmation(normalized_phone: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a pending-name-confirmation record by normalized phone.
    Returns None if not found or Redis unavailable.
    """
    if not redis_client:
        return None
    raw = await redis_client.get(_pending_name_key(normalized_phone))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def complete_pending_name_confirmation(normalized_phone: str) -> None:
    """
    Mark a pending-name-confirmation record as completed.
    Safe no-op if the record does not exist or Redis is unavailable.
    """
    if not redis_client:
        return
    key = _pending_name_key(normalized_phone)
    raw = await redis_client.get(key)
    if not raw:
        return
    try:
        record = json.loads(raw)
        if isinstance(record, dict):
            record["status"] = "completed"
            record["sms_reply_received"] = True
            # Preserve remaining TTL — fetch it first
            ttl = await redis_client.ttl(key)
            ex = ttl if ttl and ttl > 0 else _PENDING_NAME_TTL
            await redis_client.set(key, json.dumps(record), ex=ex)
    except Exception:
        pass


async def acquire_once_lock(key: str, ttl_seconds: int = 300) -> bool:
    """
    Atomically acquire a one-shot processing lock via Redis SET NX.

    Returns True  → this caller is the first; proceed with the operation.
    Returns False → another request already holds the lock; skip.

    Safe no-op (returns True — allow processing) if Redis is not configured,
    so the system degrades gracefully in local dev without Redis.

    Use this to prevent duplicate side-effects when Twilio may fire the same
    webhook twice (e.g. /status called twice for the same CallSid).
    """
    if not redis_client:
        return True  # No Redis — allow processing; accept the duplicate risk
    try:
        result = await redis_client.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(result)  # True if set, None (→ False) if key already existed
    except Exception:
        return True  # Redis error — allow processing rather than silently drop
