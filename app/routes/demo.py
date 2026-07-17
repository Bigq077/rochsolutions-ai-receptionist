# app/routes/demo.py
"""
Public, read-only transcript feed for the website's live demo call.

The Roch Solutions website places a real browser call to Susie (Twilio Voice
SDK -> TwiML App -> /twilio/voice). The browser knows its call SID and polls
this endpoint every couple of seconds to render the conversation live.

Unlike /admin/test/session/{call_sid}, this returns ONLY the words spoken —
no collected slots, phone numbers, flow state or debug fields. A call SID is
a 34-char unguessable token, which is the access control: you can only fetch
the transcript of a call whose SID you hold.
"""

import json
import logging
import re

from fastapi import APIRouter

logger = logging.getLogger("demo")

router = APIRouter(prefix="/demo")

_CALL_SID_RE = re.compile(r"^CA[0-9a-fA-F]{32}$")
_MAX_TURNS = 200
_MAX_TEXT = 600


@router.get("/transcript/{call_sid}")
async def demo_transcript(call_sid: str):
    """Return the live transcript of a call as [{speaker, text}] turns.

    An empty `turns` with ok=true means "no session yet — keep polling":
    the media-stream session is created a beat after the call connects.
    """
    if not _CALL_SID_RE.match(call_sid):
        return {"ok": False, "error": "bad_call_sid"}

    from app.media_streams.session import MS_SESSION_PREFIX, _get_redis

    redis = _get_redis()
    if not redis:
        return {"ok": False, "error": "unavailable"}

    try:
        data = await redis.get(f"{MS_SESSION_PREFIX}{call_sid}")
    except Exception as exc:  # Redis hiccup — the site just polls again
        logger.warning("[demo] transcript redis get failed call_sid=%s: %r", call_sid, exc)
        return {"ok": False, "error": "unavailable"}

    if not data:
        return {"ok": True, "turns": []}

    try:
        session = json.loads(data)
    except Exception:
        return {"ok": False, "error": "unavailable"}

    history = session.get("conversation_history") or []
    turns = []
    for entry in history[-_MAX_TURNS:]:
        role = entry.get("role")
        text = (entry.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            turns.append(
                {
                    "speaker": "caller" if role == "user" else "susie",
                    "text": text[:_MAX_TEXT],
                }
            )

    return {"ok": True, "turns": turns}
