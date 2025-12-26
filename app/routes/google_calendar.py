import os
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.tools.calendar_google import get_auth_url, exchange_code_for_tokens
from app.storage.redis_store import redis_get_json, redis_set_json

router = APIRouter()

TOKENS_KEY = "google_tokens"


def _base_url(request: Request) -> str:
    return os.getenv("BASE_URL") or str(request.base_url).rstrip("/")


@router.get("/auth/google/start")
async def google_start(request: Request):
    state = secrets.token_urlsafe(24)
    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    # Save state for 10 minutes
    await redis_set_json("google_oauth_state", {"state": state}, ttl_seconds=600)

    url = get_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    saved = await redis_get_json("google_oauth_state") or {}

    if not code or not state or saved.get("state") != state:
        return JSONResponse(
            {"error": "Invalid OAuth state or missing code/state"},
            status_code=400,
        )

    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    token_data = exchange_code_for_tokens(redirect_uri=redirect_uri, code=code)

    # Store tokens long-term (1 year TTL)
    await redis_set_json(TOKENS_KEY, token_data, ttl_seconds=60 * 60 * 24 * 365)

    return JSONResponse({"status": "connected", "message": "Google Calendar connected successfully ✅"})
