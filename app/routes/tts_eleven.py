from __future__ import annotations

import asyncio
import os
import base64
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.storage.redis_store import (
    redis_set_json,
    redis_get_json,
    redis_delete,
)

router = APIRouter(prefix="/avatar", tags=["avatar"])

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

AUDIO_DIR = Path("/tmp/tts")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# MUST be set in Render
# https://rochsolutions-ai-receptionist.onrender.com
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class TTSReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class TTSTokenReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=800)  # Twilio prompts only


# -----------------------------------------------------------------------------
# ElevenLabs helper (stateless)
# -----------------------------------------------------------------------------

def _eleven_tts_bytes(text: str) -> bytes:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

    if not api_key or not voice_id:
        raise HTTPException(
            status_code=500,
            detail="Missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID",
        )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.8,
            "style": 0.2,
            "use_speaker_boost": True,
        },
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=8)
        r.raise_for_status()
        return r.content
    except requests.HTTPError:
        detail = (r.text or "")[:600]
        raise HTTPException(status_code=502, detail=detail or "ElevenLabs HTTP error")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {str(e)}")


async def _eleven_tts_bytes_async(text: str) -> bytes:
    """
    Non-blocking wrapper around the synchronous ElevenLabs API call.

    Runs _eleven_tts_bytes() in the default thread pool executor so the
    asyncio event loop is never blocked — other webhook coroutines
    (e.g. /turn) can be handled while ElevenLabs is generating audio.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _eleven_tts_bytes, text)


# -----------------------------------------------------------------------------
# Browser / avatar (base64)
# -----------------------------------------------------------------------------

@router.post("/tts-eleven")
def tts_eleven(req: TTSReq):
    """
    Returns base64 audio for browser-based avatars.
    NOT used by Twilio.
    """
    text = (req.text or "").strip()
    audio_bytes = _eleven_tts_bytes(text)

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return {"audio_b64": audio_b64, "mime_type": "audio/mpeg"}


# -----------------------------------------------------------------------------
# Legacy /tmp-based endpoint (kept but NOT recommended)
# -----------------------------------------------------------------------------

@router.post("/tts-eleven-url")
def tts_eleven_url(req: TTSReq, request: Request):
    """
    ⚠️ NOT production-safe on multi-instance deploys.
    Kept only for backward compatibility.
    """
    text = (req.text or "").strip()

    if len(text) > 800:
        text = text[:800].rsplit(" ", 1)[0].strip() + "…"

    audio_bytes = _eleven_tts_bytes(text)

    filename = f"{uuid.uuid4().hex}.mp3"
    path = AUDIO_DIR / filename
    path.write_bytes(audio_bytes)

    base = str(request.base_url).rstrip("/")
    public_url = f"{base}/avatar/audio/{filename}"
    return {"audio_url": public_url, "mime_type": "audio/mpeg"}


@router.get("/audio/{filename}")
def serve_audio(filename: str):
    """
    Serves MP3 files written to /tmp (legacy).
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=filename,
        headers={"Cache-Control": "public, max-age=300"},
    )


# -----------------------------------------------------------------------------
# ✅ PRODUCTION-GRADE TWILIO FLOW (USE THIS)
# -----------------------------------------------------------------------------

@router.post("/tts-eleven-token")
def tts_eleven_token(req: TTSTokenReq):
    """
    Generates a short-lived token.
    Twilio will <Play> the URL returned here.
    """
    if not PUBLIC_BASE_URL:
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_BASE_URL env var is not set",
        )

    token = uuid.uuid4().hex
    text = (req.text or "").strip()

    # Store for 2 minutes (enough for Twilio fetch)
    redis_set_json(
        f"tts:{token}",
        {"text": text},
        ex=120,
    )

    audio_url = f"{PUBLIC_BASE_URL}/avatar/tts-eleven-bytes?token={token}"
    return {"token": token, "audio_url": audio_url, "mime_type": "audio/mpeg"}


@router.get("/tts-eleven-bytes")
def tts_eleven_bytes(token: str):
    """
    Twilio <Play> hits this endpoint.
    Audio is generated ON DEMAND.
    """
    data = redis_get_json(f"tts:{token}")

    if not data or not data.get("text"):
        raise HTTPException(status_code=404, detail="TTS token not found or expired")

    text = data["text"].strip()
    audio_bytes = _eleven_tts_bytes(text)

    # Single-use token (important)
    redis_delete(f"tts:{token}")

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
