import json
from typing import Dict, Any, Optional

from app.config import REDIS_URL

# This is written so your app still runs even if REDIS_URL isn't set yet.
redis_client = None

if REDIS_URL:
    import redis.asyncio as redis  # type: ignore
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DEFAULT_SESSION: Dict[str, Any] = {
    "intent": None,
    "state": "TRIAGE",
    "collected": {},
}

async def get_session(call_sid: str) -> Dict[str, Any]:
    if not call_sid:
        return DEFAULT_SESSION.copy()

    if not redis_client:
        return DEFAULT_SESSION.copy()

    key = f"call:{call_sid}"
    raw = await redis_client.get(key)
    if not raw:
        return DEFAULT_SESSION.copy()
    try:
        return json.loads(raw)
    except Exception:
        return DEFAULT_SESSION.copy()

async def save_session(call_sid: str, session: Dict[str, Any]) -> None:
    if not call_sid or not redis_client:
        return
    key = f"call:{call_sid}"
    await redis_client.set(key, json.dumps(session), ex=60 * 30)  # 30 min TTL

