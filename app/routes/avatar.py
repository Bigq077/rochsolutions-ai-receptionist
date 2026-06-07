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


# ✅ TTS request/response
class TTSRequest(BaseModel):
    text: str
    voice: str | None = None  # optional override


class TTSResponse(BaseModel):
    audio_b64: str
    mime_type: str


# ✅ Video start + status (prevents Render 504)
class AvatarVideoStartRequest(BaseModel):
    clinic_id: str
    session_id: str
    text: str


class AvatarVideoStartResponse(BaseModel):
    reply_text: str
    video_id: str
    session_id: str


class AvatarVideoStatusResponse(BaseModel):
    status: str
    video_url: str | None = None


# ✅ LiveAvatar (Mode 2, low-latency interactive)
class LiveAvatarStartRequest(BaseModel):
    clinic_id: str
    session_id: str


class LiveAvatarStartResponse(BaseModel):
    liveavatar_session_id: str
    livekit_url: str
    livekit_token: str


class LiveAvatarStopRequest(BaseModel):
    session_token: str


class LiveAvatarStopResponse(BaseModel):
    ok: bool


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

def normalize_livekit_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    return url


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
# 4A) VIDEO START: TEXT -> BRAIN -> HEYGEN VIDEO_ID (FAST)
# -----------------------------------------
@router.post("/video/start", response_model=AvatarVideoStartResponse)
async def avatar_video_start(payload: AvatarVideoStartRequest):
    user_text = (payload.text or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    # HeyGen env vars
    heygen_key = os.getenv("HEYGEN_API_KEY")
    heygen_talking_photo_id = os.getenv("HEYGEN_AVATAR_ID")  # Sandy's id
    heygen_voice_id = os.getenv("HEYGEN_VOICE_ID")  # Sandy's default voice id

    if not all([heygen_key, heygen_talking_photo_id, heygen_voice_id]):
        raise HTTPException(
            status_code=500,
            detail="Missing HeyGen env vars (HEYGEN_API_KEY / HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID)",
        )

    # Session
    session_key = f"avatar:{payload.clinic_id}:{payload.session_id}"
    try:
        session = await get_session(session_key) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["clinic_id"] = payload.clinic_id
    session["session_id"] = payload.session_id
    session["channel"] = "avatar_web_video"

    # Brain
    reply_text, session = await handle_user_text(user_text, session)

    # Save session
    try:
        await save_session(session_key, session)
    except Exception as e:
        print("Redis save_session error:", repr(e))

    # Create HeyGen video (NO polling here => avoids Render 504)
    create_url = "https://api.heygen.com/v2/video/generate"
    headers = {"X-API-KEY": heygen_key, "Content-Type": "application/json"}

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

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(create_url, headers=headers, json=create_payload)

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"HeyGen create failed: {resp.status_code} {resp.text}")

    data = resp.json()
    video_id = (data.get("data") or {}).get("video_id")
    if not video_id:
        raise HTTPException(status_code=500, detail=f"HeyGen create returned unexpected payload: {data}")

    return AvatarVideoStartResponse(reply_text=reply_text, video_id=video_id, session_id=payload.session_id)


# -----------------------------------------
# 4B) VIDEO STATUS: CHECK HEYGEN ONCE (FAST)
# -----------------------------------------
@router.get("/video/status", response_model=AvatarVideoStatusResponse)
async def avatar_video_status(video_id: str):
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    heygen_key = os.getenv("HEYGEN_API_KEY")
    if not heygen_key:
        raise HTTPException(status_code=500, detail="Missing HEYGEN_API_KEY")

    status_url = "https://api.heygen.com/v1/video_status.get"

    async with httpx.AsyncClient(timeout=15.0) as client:
        s = await client.get(status_url, headers={"X-API-KEY": heygen_key}, params={"video_id": video_id})

    if s.status_code != 200:
        raise HTTPException(status_code=500, detail=f"HeyGen status failed: {s.status_code} {s.text}")

    sd = s.json().get("data", {})
    status = sd.get("status")

    if status == "completed" and sd.get("video_url"):
        return AvatarVideoStatusResponse(status="completed", video_url=sd["video_url"])

    if status == "failed":
        return AvatarVideoStatusResponse(status="failed", video_url=None)

    return AvatarVideoStatusResponse(status="processing", video_url=None)


# -----------------------------------------
# 5) LIVEAVATAR START (Mode 2): return LiveKit URL + token
#    - Accepts 200 OR 201 from /sessions/start
#    - Uses livekit_client_token from response
#    - Caches session to avoid concurrency errors
# -----------------------------------------
@router.post("/live/start", response_model=LiveAvatarStartResponse)
async def liveavatar_start(payload: LiveAvatarStartRequest):
    """
    Mode 2 (low-latency): create a LiveAvatar FULL session and start it.

    IMPORTANT:
    - Most accounts have concurrency=1. Starting multiple sessions triggers 4032.
    - We cache the LiveKit URL/token per (clinic_id, session_id) and reuse it.

    Env vars required:
      LIVEAVATAR_API_KEY
      LIVEAVATAR_AVATAR_ID
      LIVEAVATAR_VOICE_ID
      LIVEAVATAR_LANGUAGE (optional, default en)
      LIVEAVATAR_CONTEXT_ID (optional)
    """
    api_key = os.getenv("LIVEAVATAR_API_KEY")
    avatar_id = os.getenv("LIVEAVATAR_AVATAR_ID")
    voice_id = os.getenv("LIVEAVATAR_VOICE_ID")
    language = os.getenv("LIVEAVATAR_LANGUAGE", "en")
    context_id = os.getenv("LIVEAVATAR_CONTEXT_ID")  # optional

    if not api_key or not avatar_id or not voice_id:
        raise HTTPException(
            status_code=500,
            detail="Missing LIVEAVATAR env vars (LIVEAVATAR_API_KEY / LIVEAVATAR_AVATAR_ID / LIVEAVATAR_VOICE_ID)",
        )

    # Reuse an existing LiveAvatar session for this browser session (prevents 4032)
    live_cache_key = f"liveavatar:{payload.clinic_id}:{payload.session_id}"
    try:
        cached = await get_session(live_cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("livekit_url")
            and cached.get("livekit_token")
            and cached.get("liveavatar_session_id")
        ):
            return LiveAvatarStartResponse(
                liveavatar_session_id=cached["liveavatar_session_id"],
                livekit_url=cached["livekit_url"],
                livekit_token=cached["livekit_token"],
            )
    except Exception as e:
        print("Redis live cache get error:", repr(e))

    # Store that this user session is in live mode (optional)
    session_key = f"avatar:{payload.clinic_id}:{payload.session_id}"
    try:
        session = await get_session(session_key) or {}
    except Exception as e:
        print("Redis get_session error:", repr(e))
        session = {}

    session["clinic_id"] = payload.clinic_id
    session["session_id"] = payload.session_id
    session["channel"] = "avatar_liveavatar"

    try:
        await save_session(session_key, session)
    except Exception as e:
        print("Redis save_session error:", repr(e))

    # 1) Create session token
    token_url = "https://api.liveavatar.com/v1/sessions/token"
    token_headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    token_body: dict = {
        "mode": "FULL",
        "avatar_id": avatar_id,
        "avatar_persona": {
            "voice_id": voice_id,
            "language": language,
        },
    }
    if context_id:
        token_body["avatar_persona"]["context_id"] = context_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        tok = await client.post(token_url, headers=token_headers, json=token_body)

    if tok.status_code != 200:
        raise HTTPException(status_code=500, detail=f"LiveAvatar token error: {tok.status_code} {tok.text}")

    tok_json = tok.json()
    tok_data = tok_json.get("data") if isinstance(tok_json, dict) else None
    tok_data = tok_data if isinstance(tok_data, dict) else tok_json

    session_token = tok_data.get("session_token")
    if not session_token:
        raise HTTPException(status_code=500, detail=f"Unexpected LiveAvatar token payload: {tok_json}")

    # 2) Start session (returns LiveKit connection details)
    start_url = "https://api.liveavatar.com/v1/sessions/start"
    start_headers = {
        "authorization": f"Bearer {session_token}",
        "accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        st = await client.post(start_url, headers=start_headers)

    # LiveAvatar commonly returns 201 Created on success
    if st.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"LiveAvatar start error: {st.status_code} {st.text}")

    st_json = st.json()
    st_data = st_json.get("data") if isinstance(st_json, dict) else None
    st_data = st_data if isinstance(st_data, dict) else st_json

    liveavatar_session_id = st_data.get("session_id")
    livekit_url = st_data.get("livekit_url")
    livekit_token = st_data.get("livekit_client_token")

    if not liveavatar_session_id or not livekit_url or not livekit_token:
        raise HTTPException(status_code=500, detail=f"Unexpected LiveAvatar start payload: {st_json}")

    # Cache so we reuse this LiveAvatar session and avoid concurrency errors
    try:
        await save_session(
            live_cache_key,
            {
                "liveavatar_session_id": liveavatar_session_id,
                "session_token": session_token,
                "livekit_url": livekit_url,
                "livekit_token": livekit_token,
            },
        )
    except Exception as e:
        print("Redis live cache save error:", repr(e))

    return LiveAvatarStartResponse(
        liveavatar_session_id=liveavatar_session_id,
        livekit_url=livekit_url,
        livekit_token=livekit_token,
    )


# -----------------------------------------
# 6) LIVEAVATAR STOP: frees concurrency slot
# -----------------------------------------
@router.post("/live/stop", response_model=LiveAvatarStopResponse)
async def liveavatar_stop(payload: LiveAvatarStopRequest):
    """
    Stops the current LiveAvatar session (frees concurrency slot).
    """
    session_token = (payload.session_token or "").strip()
    if not session_token:
        raise HTTPException(status_code=400, detail="session_token is required")

    stop_url = "https://api.liveavatar.com/v1/sessions/stop"
    headers = {
        "authorization": f"Bearer {session_token}",
        "accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(stop_url, headers=headers)

    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=f"LiveAvatar stop error: {r.status_code} {r.text}")

    return LiveAvatarStopResponse(ok=True)


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

    voice_model = voice or "aura-asteria-en"
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
