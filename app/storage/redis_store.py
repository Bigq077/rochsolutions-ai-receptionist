# app/storage/redis_store.py
from __future__ import annotations

import json
from typing import Dict, Any, Optional

from app.config import REDIS_URL

redis_client = None

if REDIS_URL:
    import redis.asyncio as redis  # type: ignore
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DEFAULT_SESSION: Dict[str, Any] = {
    "intent": None,
    "state": "TRIAGE",
    "collected": {},
    "miss_count": 0,
    "last_bot_prompt": "",
    "call_sid": "",
}


# ============================
# Call session helpers
# ============================
async def get_session(call_sid: str) -> Dict[str, Any]:
    if not call_sid or not redis_client:
        return DEFAULT_SESSION.copy()

    key = f"call:{call_sid}"
    raw = await redis_client.get(key)
    if not raw:
        return DEFAULT_SESSION.copy()

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return DEFAULT_SESSION.copy()

        for k, v in DEFAULT_SESSION.items():
            data.setdefault(k, v)

        return data
    except Exception:
        return DEFAULT_SESSION.copy()


async def save_session(call_sid: str, session: Dict[str, Any]) -> None:
    if not call_sid or not redis_client:
        return

    key = f"call:{call_sid}"
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
# DELETE helper (IMPORTANT)
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


# Backwards-compatible alias (optional but safe)
redis_delete_key = redis_delete
