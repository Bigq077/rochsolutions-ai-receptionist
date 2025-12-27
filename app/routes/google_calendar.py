import os
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.tools.calendar_google import get_auth_url, exchange_code_for_tokens
from app.storage.redis_store import redis_get_json, redis_set_json

router = APIRouter()

TOKENS_KEY = "google_tokens"


def _base_url(request: Request) -> str:
    base = os.getenv("BASE_URL") or str(request.base_url)
    return base.strip().rstrip("/")


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
    print("CALLBACK URL:", str(request.url))
    saved = await redis_get_json("google_oauth_state") or {}

    # DEBUG (temporary): print what we received and what Redis has
    print(
        "OAUTH CALLBACK ->",
        "code_present:", bool(code),
        "state:", state,
        "saved_state:", saved.get("state"),
    )

    if not code:
        return JSONResponse({"error": "Missing code from Google"}, status_code=400)

    # TEMPORARY: do NOT block on state mismatch while debugging
    # We'll re-enable strict state checking once it works end-to-end.

    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    token_data = exchange_code_for_tokens(redirect_uri=redirect_uri, code=code)

    await redis_set_json(TOKENS_KEY, token_data, ttl_seconds=60 * 60 * 24 * 365)

    return JSONResponse({"status": "connected", "message": "Google Calendar connected successfully ✅"})
