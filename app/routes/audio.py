from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/audio")

AUDIO_DIR = Path("/tmp/tts")


@router.get("/{filename}")
def serve_audio(filename: str):
    # Prevent path traversal
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="bad filename")

    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="audio/mpeg")
