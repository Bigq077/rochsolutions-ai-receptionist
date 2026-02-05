from __future__ import annotations

import os
import base64
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/avatar", tags=["avatar"])

class TTSReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)

@router.post("/tts-eleven")
def tts_eleven(req: TTSReq):
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
        "text": req.text,
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
    except requests.HTTPError:
        # Surface ElevenLabs error text if any (helps debugging on Render)
        detail = r.text[:600] if "r" in locals() else "ElevenLabs HTTP error"
        raise HTTPException(status_code=502, detail=detail)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {str(e)}")

    audio_b64 = base64.b64encode(r.content).decode("utf-8")
    return {"audio_b64": audio_b64, "mime_type": "audio/mpeg"}
