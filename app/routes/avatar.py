# app/routes/avatar.py

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage.redis_store import get_session, save_session
from app.flows.brain import handle_user_text

router = APIRouter(prefix="/avatar", tags=["avatar"])


class AvatarChatRequest(BaseModel):
    clinic_id: str
    session_id: str
    message: str


class AvatarChatResponse(BaseModel):
    reply_text: str
    session_id: str


@router.post("/chat", response_model=AvatarChatResponse)
async def avatar_chat(payload: AvatarChatRequest):
    """
    Text endpoint for Web/Avatar UI.

    - Accepts a message (text)
    - Loads session from Redis (so it has memory)
    - Calls the SAME AI brain as Twilio (handle_user_text -> triage_turn)
    - Saves session back to Redis
    - Returns reply_text
    """
    user_text = (payload.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    # Use a stable Redis key so this conversation persists in the browser
    session_key = f"avatar:{payload.clinic_id}:{payload.session_id}"

    # Load session
    try:
        session = await get_session(session_key) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    # Add helpful metadata (optional)
    session["clinic_id"] = payload.clinic_id
    session["session_id"] = payload.session_id
    session["channel"] = "avatar_web"

    # ✅ Call the real AI brain (same logic used by Twilio)
    reply_text, session = await handle_user_text(user_text, session)

    # Save session
    try:
        await save_session(session_key, session)
    except Exception as e:
        print("Redis save_session error:", repr(e))

    return AvatarChatResponse(reply_text=reply_text, session_id=payload.session_id)

