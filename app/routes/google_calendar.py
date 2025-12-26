# app/routes/google_calendar.py

import os
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.tools.calendar_google import get_auth_url, exchange_code_for_tokens

# You have redis_store.py — adapt these calls to your actual functions.
from app.storage.redis_store import redis_get_json, redis_set_json

router = APIRouter()

TOKENS_KEY = "google_tokens"   # you can make per-user later


def _base_url(request: Request) -> str:
    # Prefer BASE_URL env for production stability, otherwise infer from request
    return os.getenv("BASE_URL") or str(request.base_url).rstrip("/")


@router.get("/auth/google/start")
def google_start(request: Request):
    state = secrets.token_urlsafe(24)
    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    # store state to validate later (simple version)
    redis_set_json("google_oauth_state", {"state": state}, ttl_seconds=600)

    url = get_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url)


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = ""):
    saved = redis_get_json("google_oauth_state") or {}
    if not code or not state or saved.get("state") != state:
        return JSONResponse({"error": "Invalid OAuth state or missing code/state"}, status_code=400)

    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    token_data = exchange_code_for_tokens(redirect_uri=redirect_uri, code=code)
    redis_set_json(TOKENS_KEY, token_data, ttl_seconds=60 * 60 * 24 * 365)  # 1 year

    return JSONResponse({"status": "connected", "message": "Google Calendar connected successfully ✅"})

