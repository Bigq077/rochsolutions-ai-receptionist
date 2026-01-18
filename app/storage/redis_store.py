# app/storage/redis_store.py
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
}

def _fresh_default_session() -> Dict[str, Any]:
    # Avoid shared mutable dicts (collected)
    return {
        "intent": None,
        "state": "TRIAGE",
        "collected": {},
    }

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
        # Ensure required keys exist
        data.setdefault("intent", None)
        data.setdefault("state", "TRIAGE")
        data.setdefault("collected", {})
        return data
    except Exception:
        return _fresh_default_session()

async def save_session(call_sid: str, session: Dict[str, Any]) -> None:
    if not call_sid or not redis_client:
        return
    key = f"call:{call_sid}"
    await redis_client.set(key, json.dumps(session), ex=60 * 30)  # 30 min TTL

# --- Generic helpers for storing arbitrary JSON (used for Google OAuth) ---

async def redis_set_json(key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
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
