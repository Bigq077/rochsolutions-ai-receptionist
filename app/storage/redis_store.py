# app/storage/redis_store.py
from __future__ import annotations

import json
import copy
from typing import Dict, Any, Optional, List

from app.config import REDIS_URL

redis_client = None

if REDIS_URL:
    import redis.asyncio as redis  # type: ignore
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DEFAULT_SESSION: Dict[str, Any] = {
    "intent": None,
    "state": "TRIAGE",
    "collected": {},          # IMPORTANT: mutable (deep-copied on use)
    "miss_count": 0,
    "last_bot_prompt": "",
    "call_sid": "",
}


def _fresh_default_session() -> Dict[str, Any]:
    """
    Always return a deep copy so nested dicts like collected don't leak between calls.
    """
    return copy.deepcopy(DEFAULT_SESSION)


# ============================
# Call session helpers
# ============================
async def get_session(call_sid: str) -> Dict[str, Any]:
    if not call_sid or not redis_client:
        return _fresh_default_session()

    key = f"call:{call_sid}"
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

    key = f"call:{call_sid}"

    # Defensive: ensure serializable + structure sane
    if not isinstance(session, dict):
        session = _fresh_default_session()
    if not isinstance(session.get("collected"), dict):
        session["collected"] = {}

    # keep it consistent
    session["call_sid"] = call_sid

    await redis_client.set(key, json.dumps(session), ex=60 * 30)  # 30 min TTL


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
