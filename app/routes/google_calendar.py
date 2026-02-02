# app/routes/google_calendar.py
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytz
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.storage.redis_store import (
    redis_get_json,
    redis_set_json,
    redis_delete_key,
)

from app.tools.calendar_google import (
    GoogleCalendarAuthError,
    get_auth_url,
    exchange_code_for_tokens,
    freebusy,
    create_event,
    list_upcoming_events,
    patch_event_time,
    delete_event,
)

router = APIRouter(tags=["google-calendar"])

TOKENS_KEY = "google_tokens"
STATE_KEY = "google_oauth_state"
TZ = pytz.timezone("Europe/London")


def _base_url(request: Request) -> str:
    # Prefer explicit BASE_URL in Render env.
    # Example: https://rochsolutions-ai-receptionist.onrender.com
    base = os.getenv("BASE_URL") or str(request.base_url)
    return base.strip().rstrip("/")


def _normalize_tokens(tokens: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure tokens is JSON-serializable and has expected fields.
    (Also protects against accidental datetime objects being stored.)
    """
    out: Dict[str, Any] = {}
    for k, v in tokens.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def _get_tokens() -> Optional[Dict[str, Any]]:
    tokens = await redis_get_json(TOKENS_KEY)
    return tokens if isinstance(tokens, dict) else None


async def _save_tokens(tokens: Dict[str, Any]) -> None:
    # Persist refreshed tokens (tools mutate token/expiry in-place)
    tokens = _normalize_tokens(tokens)
    await redis_set_json(TOKENS_KEY, tokens, ttl_seconds=60 * 60 * 24 * 365)  # 1 year


async def _auth_fail(e: Exception):
    """
    Any auth/refresh problem => wipe tokens so the next interaction
    reliably pushes user to reconnect instead of looping failures.
    """
    await redis_delete_key(TOKENS_KEY)
    return JSONResponse(
        {
            "ok": False,
            "error": "Google Calendar auth expired/revoked or token refresh failed. Please reconnect.",
            "detail": str(e),
            "next_step": "Open /auth/google/start to reconnect.",
        },
        status_code=401,
    )


def _tz_now() -> datetime:
    """
    Always timezone-aware. Keeps all test endpoints clean.
    """
    return datetime.now(TZ)


# -------------------------
# OAuth endpoints
# -------------------------

@router.get("/auth/google/start")
async def google_start(request: Request):
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{_base_url(request)}/auth/google/callback"

    # Save state for 10 minutes
    await redis_set_json(STATE_KEY, {"state": state}, ttl_seconds=600)

    url = get_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if error:
        return JSONResponse(
            {"ok": False, "error": error, "error_description": error_description},
            status_code=400,
        )

    if not code:
        return JSONResponse({"ok": False, "error": "Missing code from Google"}, status_code=400)

    saved = await redis_get_json(STATE_KEY) or {}
    if not state or saved.get("state") != state:
        return JSONResponse({"ok": False, "error": "Invalid OAuth state"}, status_code=400)

    redirect_uri = f"{_base_url(request)}/auth/google/callback"

    token_data = exchange_code_for_tokens(redirect_uri=redirect_uri, code=code)
    token_data = _normalize_tokens(token_data)

    # If Google didn’t issue a refresh_token, you will eventually hit invalid_grant / refresh failures.
    # This usually happens if the Google account already consented earlier and Google "reuses" consent.
    # Your get_auth_url includes prompt="consent" which *should* force a refresh_token, but we sanity-check.
    if not token_data.get("refresh_token"):
        # Save anyway so the user can test immediately, but warn clearly
        await _save_tokens(token_data)
        return JSONResponse(
            {
                "ok": True,
                "status": "connected_with_warning",
                "message": "Connected ✅ (but no refresh_token was issued). If it stops working, reset and reconnect.",
                "next_step": "If you hit auth errors: open /auth/google/reset then /auth/google/start again.",
            }
        )

    await _save_tokens(token_data)
    return JSONResponse({"ok": True, "status": "connected", "message": "Google Calendar connected successfully ✅"})


@router.get("/auth/google/status")
async def google_status():
    tokens = await _get_tokens()
    return {
        "ok": True,
        "connected": bool(tokens),
        "has_refresh_token": bool(tokens and tokens.get("refresh_token")),
        "has_expiry": bool(tokens and tokens.get("expiry")),
        "scopes": (tokens or {}).get("scopes", []),
    }


@router.get("/auth/google/reset")
async def google_reset_get():
    await redis_delete_key(TOKENS_KEY)
    return {"ok": True, "message": "Google tokens deleted. Reconnect via /auth/google/start"}


@router.post("/auth/google/reset")
async def google_reset_post():
    await redis_delete_key(TOKENS_KEY)
    return {"ok": True, "message": "Google tokens deleted. Reconnect via /auth/google/start"}


# -------------------------
# Test endpoints
# -------------------------

@router.get("/calendar/test/freebusy")
async def calendar_test_freebusy():
    tokens = await _get_tokens()
    if not tokens:
        return JSONResponse(
            {"ok": False, "error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    now = _tz_now()
    end = now + timedelta(days=7)

    try:
        busy = freebusy(tokens, time_min=now, time_max=end, calendar_id="primary")
        await _save_tokens(tokens)
        return {"ok": True, "busy": busy}
    except (GoogleCalendarAuthError, TypeError) as e:
        # TypeError catches: "can't compare offset-naive and offset-aware datetimes"
        return await _auth_fail(e)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "freebusy failed", "detail": repr(e)}, status_code=500)


@router.get("/calendar/test/create-event")
async def calendar_test_create_event():
    tokens = await _get_tokens()
    if not tokens:
        return JSONResponse(
            {"ok": False, "error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    start = _tz_now() + timedelta(minutes=10)
    end = start + timedelta(minutes=30)

    try:
        event = create_event(
            stored_tokens=tokens,
            start_dt=start,
            end_dt=end,
            summary="RochSolutions Test Booking",
            description="Created by /calendar/test/create-event",
            calendar_id="primary",
        )
        await _save_tokens(tokens)
        return {"ok": True, "event_id": event.get("id"), "event_link": event.get("htmlLink")}
    except (GoogleCalendarAuthError, TypeError) as e:
        return await _auth_fail(e)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "create_event failed", "detail": repr(e)}, status_code=500)


@router.get("/calendar/test/events")
async def calendar_test_events():
    tokens = await _get_tokens()
    if not tokens:
        return JSONResponse(
            {"ok": False, "error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    try:
        events = list_upcoming_events(tokens, days_ahead=14, max_results=10, calendar_id="primary")
        await _save_tokens(tokens)
        return {"ok": True, "count": len(events), "events": events}
    except (GoogleCalendarAuthError, TypeError) as e:
        return await _auth_fail(e)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "list events failed", "detail": repr(e)}, status_code=500)
