# app/routes/admin.py
import os
from fastapi import APIRouter
from app.storage.redis_store import redis_delete_key  # we’ll add this helper

router = APIRouter(prefix="/admin")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")

@router.get("/clear_google_tokens")
async def clear_google_tokens(key: str):
    if not ADMIN_KEY or key != ADMIN_KEY:
        return {"ok": False, "error": "unauthorized"}

    await redis_delete_key("google_tokens")
    return {"ok": True}
