import os
import secrets
from datetime import datetime, timedelta

import pytz
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.storage.redis_store import redis_get_json, redis_set_json
from app.tools.calendar_google import (
    get_auth_url,
    exchange_code_for_tokens,
    freebusy,
    create_event,
)
from app.tools.slots import (
    next_7_days_window,
    generate_candidate_slots,
    parse_busy,
    filter_free_slots,
    pick_first_n,
    format_slot,
)

router = APIRouter()

TOKENS_KEY = "google_tokens"
TZ = pytz.timezone("Europe/London")


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
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    print("CALLBACK URL:", str(request.url))

    if error:
        return JSONResponse(
            {"error": error, "error_description": error_description},
            status_code=400,
        )

    if not code:
        return JSONResponse({"error": "Missing code from Google"}, status_code=400)

    saved = await redis_get_json("google_oauth_state") or {}
    if not state or saved.get("state") != state:
        return JSONResponse({"error": "Invalid OAuth state"}, status_code=400)

    base = _base_url(request)
    redirect_uri = f"{base}/auth/google/callback"

    token_data = exchange_code_for_tokens(redirect_uri=redirect_uri, code=code)
    await redis_set_json(TOKENS_KEY, token_data, ttl_seconds=60 * 60 * 24 * 365)

    return JSONResponse({"status": "connected", "message": "Google Calendar connected successfully ✅"})


@router.get("/calendar/test/freebusy")
async def calendar_test_freebusy():
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return JSONResponse(
            {"error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    now = datetime.now(TZ)
    end = now + timedelta(days=7)

    busy = freebusy(tokens, time_min=now, time_max=end, calendar_id="primary")
    return {"ok": True, "busy": busy}


@router.get("/calendar/test/create-event")
async def calendar_test_create_event():
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return JSONResponse(
            {"error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    start = datetime.now(TZ) + timedelta(minutes=5)
    end = start + timedelta(minutes=30)

    event = create_event(
        stored_tokens=tokens,
        start_dt=start,
        end_dt=end,
        summary="RochSolutions Test Booking",
        description="Created by /calendar/test/create-event",
        calendar_id="primary",
    )

    return {"ok": True, "event_id": event.get("id"), "event_link": event.get("htmlLink")}


@router.get("/calendar/test/slots")
async def calendar_test_slots():
    tokens = await redis_get_json(TOKENS_KEY)
    if not tokens:
        return JSONResponse(
            {"error": "Google not connected. Run /auth/google/start first."},
            status_code=400,
        )

    w_start, w_end = next_7_days_window()

    # Generate candidate slots (Mon–Fri, 9–18, 30 min)
    candidates = generate_candidate_slots(
        w_start,
        w_end,
        duration_min=30,
        day_start_h=9,
        day_end_h=18,
    )

    # Busy from Google Calendar
    busy = freebusy(tokens, time_min=w_start, time_max=w_end, calendar_id="primary")
    busy_blocks = parse_busy(busy)

    # Filter out busy slots
    free_slots = filter_free_slots(candidates, busy_blocks)
    top3 = pick_first_n(free_slots, 3)

    return {
        "ok": True,
        "suggestions": [format_slot(s) for s in top3],
        "free_slots_found": len(free_slots),
    }
