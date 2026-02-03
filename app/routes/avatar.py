# app/routes/avatar.py

from __future__ import annotations

import os
import tempfile
import base64
import time
import hashlib
import asyncio

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


# ✅ NEW: Video request/response
class AvatarVideoRequest(BaseModel):
    clinic_id: str
    session_id: str
    text: str


class AvatarVideoResponse(BaseModel):
    reply_text: str
    video_url: str
    session_id: str


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

    tmp_path: str | None = None

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
        if tmp_path:
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


# -----------------------------------------
# 4) VIDEO: TEXT -> BRAIN -> HEYGEN VIDEO URL
# -----------------------------------------
@router.post("/video", response_model=AvatarVideoResponse)
async def avatar_video(payload: AvatarVideoRequest):
    """
    Input: clinic_id, session_id, text
    Output: reply_text + video_url (HeyGen pre-generated avatar video)
    """

    user_text = (payload.text or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    # --- Load HeyGen env vars (must be set in Render) ---
    heygen_key = os.getenv("HEYGEN_API_KEY")
    heygen_talking_photo_id = os.getenv("HEYGEN_AVATAR_ID")  # Sandy's id
    heygen_voice_id = os.getenv("HEYGEN_VOICE_ID")          # Sandy's default voice id

    if not heygen_key or not heygen_talking_photo_id or not heygen_voice_id:
        raise HTTPException(
            status_code=500,
            detail="Missing HeyGen env vars (HEYGEN_API_KEY / HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID)",
        )

    # --- Session (shared memory) ---
    session_key = f"avatar:{payload.clinic_id}:{payload.session_id}"
    try:
        session = await get_session(session_key) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["clinic_id"] = payload.clinic_id
    session["session_id"] = payload.session_id
    session["channel"] = "avatar_web_video"

    # 1) Run brain
    reply_text, session = await handle_user_text(user_text, session)

    # Save session back (even if HeyGen fails, we still keep memory)
    try:
        await save_session(session_key, session)
    except Exception as e:
        print("Redis save_session error:", repr(e))

    # 2) Optional cache (saves money): cache by clinic + reply_text hash for 24h
    # If your redis_store only stores JSON, that's fine — we store {"video_url": "..."}.
    cache_ttl_seconds = 86400
    reply_hash = hashlib.sha256(reply_text.encode("utf-8")).hexdigest()[:24]
    cache_key = f"heygen_video:{payload.clinic_id}:{reply_hash}"

    try:
        cached = await get_session(cache_key)
        if isinstance(cached, dict) and cached.get("video_url"):
            return AvatarVideoResponse(
                reply_text=reply_text,
                video_url=cached["video_url"],
                session_id=payload.session_id,
            )
    except Exception as e:
        print("Redis cache get error:", repr(e))

    # 3) Create HeyGen video
    create_url = "https://api.heygen.com/v2/video/generate"
    status_url = "https://api.heygen.com/v1/video_status.get"

    create_payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": heygen_talking_photo_id,
                },
                "voice": {
                    "type": "text",
                    "input_text": reply_text,
                    "voice_id": heygen_voice_id,
                },
            }
        ],
        "dimension": {"width": 720, "height": 1280},
    }

    headers = {
        "X-API-KEY": heygen_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(create_url, headers=headers, json=create_payload)

    if resp.status_code != 200:
        # helpful for debugging in Render logs
        raise HTTPException(status_code=500, detail=f"HeyGen create failed: {resp.status_code} {resp.text}")

    data = resp.json()
    video_id = (data.get("data") or {}).get("video_id")
    if not video_id:
        raise HTTPException(status_code=500, detail=f"HeyGen create returned unexpected payload: {data}")

    # 4) Poll status until completed (timeout ~40s)
    deadline = time.time() + 40.0
    poll_interval = 1.5

    async with httpx.AsyncClient(timeout=15.0) as client:
        while time.time() < deadline:
            s = await client.get(status_url, headers={"X-API-KEY": heygen_key}, params={"video_id": video_id})

            if s.status_code == 200:
                sd = s.json().get("data", {})
                status = sd.get("status")

                if status == "completed" and sd.get("video_url"):
                    video_url = sd["video_url"]

                    # cache success (24h)
                    try:
                        await save_session(cache_key, {"video_url": video_url, "created_at": time.time()})
                        # If your save_session supports TTL, set it there; if not, ignore.
                        # (Many simple redis wrappers don't support TTL in this function.)
                    except Exception as e:
                        print("Redis cache save error:", repr(e))

                    return AvatarVideoResponse(
                        reply_text=reply_text,
                        video_url=video_url,
                        session_id=payload.session_id,
                    )

                if status == "failed":
                    break

            # Wait and poll again
            await asyncio.sleep(poll_interval)

    # Timeouts are normal sometimes — frontend should fallback to /tts
    raise HTTPException(status_code=504, detail="HeyGen video timed out or failed")


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
