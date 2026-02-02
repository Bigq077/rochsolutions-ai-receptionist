# app/routes/avatar.py

from __future__ import annotations

import os
import tempfile
import base64

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
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


class VoiceTurnResponse(BaseModel):
    transcript: str
    reply_text: str
    session_id: str


# ✅ NEW: TTS request/response
class TTSRequest(BaseModel):
    text: str
    voice: str | None = None  # optional override


class TTSResponse(BaseModel):
    audio_b64: str
    mime_type: str


# -----------------------------
# 1) TEXT CHAT (already working)
# -----------------------------
@router.post("/chat", response_model=AvatarChatResponse)
async def avatar_chat(payload: AvatarChatRequest):
    user_text = (payload.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    session_key = f"avatar:{payload.clinic_id}:{payload.session_id}"

    try:
        session = await get_session(session_key) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["clinic_id"] = payload.clinic_id
    session["session_id"] = payload.session_id
    session["channel"] = "avatar_web"

    reply_text, session = await handle_user_text(user_text, session)

    try:
        await save_session(session_key, session)
    except Exception as e:
        print("Redis save_session error:", repr(e))

    return AvatarChatResponse(reply_text=reply_text, session_id=payload.session_id)


# -----------------------------------------
# 2) VOICE TURN: AUDIO -> STT -> BRAIN -> TXT
# -----------------------------------------
@router.post("/voice-turn", response_model=VoiceTurnResponse)
async def avatar_voice_turn(
    clinic_id: str = Form(...),
    session_id: str = Form(...),
    audio: UploadFile = File(...),
):
    """
    Receives an audio blob from the browser, transcribes it (STT via Deepgram),
    then runs the same AI brain as Twilio, returns transcript + reply.
    """
    if not clinic_id.strip() or not session_id.strip():
        raise HTTPException(status_code=400, detail="clinic_id and session_id are required")

    if not audio:
        raise HTTPException(status_code=400, detail="audio file is required")

    # Save audio to a temp file
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        content = await audio.read()
        tmp.write(content)

    try:
        # 1) Transcribe (Deepgram)
        transcript = await transcribe_audio_file(tmp_path)
        transcript = (transcript or "").strip()
        if not transcript:
            raise HTTPException(status_code=400, detail="Could not transcribe audio")

        # 2) Load session
        session_key = f"avatar:{clinic_id}:{session_id}"
        try:
            session = await get_session(session_key) or {}
        except Exception as e:
            print("Redis get_session error:", repr(e))
            session = {}

        session["clinic_id"] = clinic_id
        session["session_id"] = session_id
        session["channel"] = "avatar_web_voice"

        # 3) Run brain
        reply_text, session = await handle_user_text(transcript, session)

        # 4) Save session
        try:
            await save_session(session_key, session)
        except Exception as e:
            print("Redis save_session error:", repr(e))

        return VoiceTurnResponse(
            transcript=transcript,
            reply_text=reply_text,
            session_id=session_id,
        )

    finally:
        # Cleanup temp file
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# -----------------------------------------
# 3) TTS: TEXT -> AUDIO (Deepgram)
# -----------------------------------------
@router.post("/tts", response_model=TTSResponse)
async def avatar_tts(payload: TTSRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    audio_bytes, mime_type = await deepgram_tts(text, voice=payload.voice)
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return TTSResponse(audio_b64=audio_b64, mime_type=mime_type)


# -----------------------------
# Deepgram STT
# -----------------------------
async def transcribe_audio_file(path: str) -> str:
    """
    Transcribe an audio file using Deepgram's prerecorded endpoint (/v1/listen).
    Expects DEEPGRAM_API_KEY to be set in Render environment variables.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPGRAM_API_KEY env var")

    url = "https://api.deepgram.com/v1/listen"
    params = {
        "model": "nova-2",
        "smart_format": "true",
        "punctuate": "true",
        "language": "en-GB",
    }

    with open(path, "rb") as f:
        audio_bytes = f.read()

    headers = {
        "Authorization": f"Token {api_key}",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, params=params, headers=headers, content=audio_bytes)

    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram STT error {resp.status_code}: {resp.text}")

    data = resp.json()

    try:
        transcript = data["results"]["channels"][0]["alternatives"][0].get("transcript", "")
    except Exception:
        transcript = ""

    return transcript or ""


# -----------------------------
# Deepgram TTS
# -----------------------------
async def deepgram_tts(text: str, voice: str | None = None) -> tuple[bytes, str]:
    """
    Generate speech audio using Deepgram TTS (/v1/speak).
    Returns (audio_bytes, mime_type).
    Expects DEEPGRAM_API_KEY in environment variables.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPGRAM_API_KEY env var")

    # Default voice (can override by passing payload.voice from frontend)
    # If you get "model not found", change this to a voice available on your Deepgram account.
    voice_model = voice or "aura-asteria-en"

    # MP3 is easiest to play in browser + avatar tools
    url = f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=mp3"

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }

    body = {"text": text}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)

    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram TTS error {resp.status_code}: {resp.text}")

    return resp.content, "audio/mpeg"
