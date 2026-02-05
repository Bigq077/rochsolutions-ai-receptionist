from __future__ import annotations

import os
import base64
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/avatar", tags=["avatar"])

AUDIO_DIR = Path("/tmp/tts")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


class TTSReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


def _eleven_tts_bytes(text: str) -> bytes:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

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
        r = requests.post(url, json=payload, headers=headers, timeout=25)
        r.raise_for_status()
        return r.content
    except requests.HTTPError:
        detail = (r.text or "")[:600]
        raise HTTPException(status_code=502, detail=detail or "ElevenLabs HTTP error")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {str(e)}")


@router.post("/tts-eleven")
def tts_eleven(req: TTSReq):
    """
    Returns base64 audio (useful for browser/avatar).
    """
    text = (req.text or "").strip()
    audio_bytes = _eleven_tts_bytes(text)

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return {"audio_b64": audio_b64, "mime_type": "audio/mpeg"}


@router.post("/tts-eleven-url")
def tts_eleven_url(req: TTSReq, request: Request):
    """
    Twilio-friendly: saves MP3 to /tmp and returns a PUBLIC URL.
    """
    text = (req.text or "").strip()

    # Keep Twilio prompts snappy + reduce ElevenLabs latency/cost
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
    Serves generated MP3 files for Twilio <Play>.
    """
    # basic path traversal hardening
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
