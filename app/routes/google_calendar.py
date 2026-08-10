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
    tokens_key,
    resolve_tokens_key,
    GOOGLE_TOKENS_LEGACY_KEY,
)

router = APIRouter(tags=["google-calendar"])

# Tokens are keyed per clinic — see the note in app/tools/calendar_google.py.
#
# STATE_KEY is namespaced by the state token itself rather than being one global
# slot. It used to be a single key, so two clinics part-way through authorising
# at the same time would clobber each other's state and one would get "Invalid
# OAuth state" — which matters far more now that cutting every clinic over means
# running this flow several times in a sitting.
STATE_KEY_PREFIX = "google_oauth_state"
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


async def _get_tokens(clinic_id: str = "") -> Optional[Dict[str, Any]]:
    tokens = await redis_get_json(await resolve_tokens_key(clinic_id))
    return tokens if isinstance(tokens, dict) else None


async def _save_new_grant(tokens: Dict[str, Any], clinic_id: str = "") -> None:
    """
    Write a FRESH grant, from the OAuth callback ONLY.

    This is the one write that targets the namespaced key directly rather than
    going through resolve_tokens_key — creating the clinic's own key and cutting
    it over off the shared legacy one is the entire point of authorising.

    Nothing else may call this. Any other path writing a namespaced key would
    be silently ADOPTING whatever tokens happen to be in use into a
    clinic-specific slot, which on a shared Redis bakes one practice's
    credentials into another practice's key — the failure this change exists to
    prevent, only now invisible. Use _persist_refresh instead.
    """
    tokens = _normalize_tokens(tokens)
    await redis_set_json(
        tokens_key(clinic_id),
        tokens,
        ttl_seconds=60 * 60 * 24 * 365,  # 1 year
    )


async def _persist_refresh(tokens: Dict[str, Any], clinic_id: str = "") -> None:
    """
    Persist a refreshed access token back to the key ALREADY in use.

    Never creates a namespaced key. A clinic that has not been cut over keeps
    refreshing the legacy key, exactly as before.
    """
    tokens = _normalize_tokens(tokens)
    await redis_set_json(
        await resolve_tokens_key(clinic_id),
        tokens,
        ttl_seconds=60 * 60 * 24 * 365,  # 1 year
    )


async def _auth_fail(e: Exception, clinic_id: str = ""):
    """
    Any auth/refresh problem => wipe tokens so the next interaction
    reliably pushes user to reconnect instead of looping failures.
    """
    await redis_delete_key(await resolve_tokens_key(clinic_id))
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
    """Always timezone-aware."""
    return datetime.now(TZ)


# -------------------------
# OAuth endpoints
# -------------------------


@router.get("/auth/google/start")
async def google_start(request: Request, clinic_id: str = ""):
    """
    Begin an authorisation. `clinic_id` decides which clinic's calendar these
    tokens will drive — omit it and the grant lands on the legacy shared key,
    which is the pre-migration behaviour and is what you do NOT want on a Redis
    serving more than one clinic.
    """
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{_base_url(request)}/auth/google/callback"

    # The clinic rides with the state, not the query string of the callback:
    # Google echoes `state` back verbatim and nothing else we control, so this
    # is the only way the callback can know whose tokens it is holding.
    await redis_set_json(
        f"{STATE_KEY_PREFIX}:{state}",
        {"state": state, "clinic_id": (clinic_id or "").strip().lower()},
        ttl_seconds=600,
    )

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
            {
                "ok": False,
                "error": error,
                "error_description": error_description,
            },
            status_code=400,
        )

    if not code:
        return JSONResponse(
            {"ok": False, "error": "Missing code from Google"},
            status_code=400,
        )

    saved = await redis_get_json(f"{STATE_KEY_PREFIX}:{state}") or {}
    if not state or saved.get("state") != state:
        return JSONResponse(
            {"ok": False, "error": "Invalid OAuth state"},
            status_code=400,
        )
    grant_clinic_id = (saved.get("clinic_id") or "").strip().lower()

    redirect_uri = f"{_base_url(request)}/auth/google/callback"

    token_data = exchange_code_for_tokens(
        redirect_uri=redirect_uri,
        code=code,
    )
    token_data = _normalize_tokens(token_data)

    # --- Preserve existing refresh_token ---
    # Google only returns refresh_token on the VERY FIRST grant (or after the user
    # explicitly revokes and re-grants consent).  On every subsequent auth it omits
    # refresh_token entirely.  If we write token_data straight to Redis we silently
    # wipe the stored refresh_token with None, which is the root cause of weekly
    # re-auth.  Fix: load what we already have and keep the old refresh_token unless
    # Google handed us a brand-new one.
    #
    # Read the EXISTING tokens through the normal resolver, so a clinic being
    # cut over for the first time inherits the refresh_token from the legacy
    # key it is currently using. Without that, the first namespaced grant for a
    # clinic Google declines to re-issue a refresh_token for would land with
    # none at all and re-auth weekly.
    existing = await _get_tokens(grant_clinic_id) or {}
    refresh_token = token_data.get("refresh_token") or existing.get("refresh_token")
    token_data["refresh_token"] = refresh_token

    if not token_data.get("refresh_token"):
        print(
            "WARNING: No refresh_token available after preservation. "
            "Google has never issued one for this client/account combination. "
            "Run /auth/google/reset then /auth/google/start to force a new grant."
        )

    await _save_new_grant(token_data, grant_clinic_id)
    await redis_delete_key(f"{STATE_KEY_PREFIX}:{state}")

    has_refresh = bool(token_data.get("refresh_token"))
    return JSONResponse(
        {
            "ok": True,
            "status": "connected",
            "clinic_id": grant_clinic_id or "(legacy shared key)",
            "tokens_key": tokens_key(grant_clinic_id),
            "message": (
                "Google Calendar connected — auto-refresh active ✅"
                if has_refresh
                else (
                    "Google Calendar connected ⚠️ — no refresh token. "
                    "Visit /auth/google/reset then /auth/google/start to fix."
                )
            ),
            "has_refresh_token": has_refresh,
        }
    )


@router.get("/auth/google/status")
async def google_status(clinic_id: str = ""):
    """
    This is the cutover check. `migrated` is the field that matters: False means
    the clinic is still reading the legacy shared key and is NOT yet isolated,
    even though `connected` is True.
    """
    in_use = await resolve_tokens_key(clinic_id)
    tokens = await redis_get_json(in_use)
    tokens = tokens if isinstance(tokens, dict) else None
    return {
        "ok": True,
        "clinic_id": clinic_id or "(none given)",
        "tokens_key_in_use": in_use,
        "migrated": in_use != GOOGLE_TOKENS_LEGACY_KEY,
        "connected": bool(tokens),
        "has_refresh_token": bool(tokens and tokens.get("refresh_token")),
        "has_expiry": bool(tokens and tokens.get("expiry")),
        "scopes": (tokens or {}).get("scopes", []),
    }


async def _reset(clinic_id: str) -> dict:
    """
    Delete only the key this clinic is actually using.

    Deleting the legacy key disconnects EVERY clinic that has not been cut over
    yet, so it is not done implicitly: reset without a clinic_id targets the
    legacy key by design, and with one targets that clinic alone.
    """
    key = await resolve_tokens_key(clinic_id)
    await redis_delete_key(key)
    return {
        "ok": True,
        "deleted_key": key,
        "message": (
            f"Google tokens deleted for {clinic_id or 'the legacy shared key'}. "
            f"Reconnect via /auth/google/start?clinic_id={clinic_id}"
        ),
    }


@router.get("/auth/google/reset")
async def google_reset_get(clinic_id: str = ""):
    return await _reset(clinic_id)


@router.post("/auth/google/reset")
async def google_reset_post(clinic_id: str = ""):
    return await _reset(clinic_id)


# -------------------------
# Test endpoints
# -------------------------


@router.get("/calendar/test/freebusy")
async def calendar_test_freebusy(clinic_id: str = ""):
    tokens = await _get_tokens(clinic_id)
    if not tokens:
        return JSONResponse(
            {
                "ok": False,
                "error": "Google not connected. Run /auth/google/start first.",
            },
            status_code=400,
        )

    now = _tz_now()
    end = now + timedelta(days=7)

    try:
        busy = freebusy(
            tokens,
            time_min=now,
            time_max=end,
            calendar_id="primary",
        )
        await _persist_refresh(tokens, clinic_id)
        return {"ok": True, "busy": busy}
    except (GoogleCalendarAuthError, TypeError) as e:
        return await _auth_fail(e, clinic_id)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": "freebusy failed", "detail": repr(e)},
            status_code=500,
        )


@router.get("/calendar/test/create-event")
async def calendar_test_create_event(clinic_id: str = ""):
    tokens = await _get_tokens(clinic_id)
    if not tokens:
        return JSONResponse(
            {
                "ok": False,
                "error": "Google not connected. Run /auth/google/start first.",
            },
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
            calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        )
        await _persist_refresh(tokens, clinic_id)
        return {
            "ok": True,
            "event_id": event.get("id"),
            "event_link": event.get("htmlLink"),
        }
    except (GoogleCalendarAuthError, TypeError) as e:
        return await _auth_fail(e, clinic_id)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": "create_event failed", "detail": repr(e)},
            status_code=500,
        )


@router.get("/calendar/test/events")
async def calendar_test_events(clinic_id: str = ""):
    tokens = await _get_tokens(clinic_id)
    if not tokens:
        return JSONResponse(
            {
                "ok": False,
                "error": "Google not connected. Run /auth/google/start first.",
            },
            status_code=400,
        )

    try:
        events = list_upcoming_events(
            stored_tokens=tokens,
            days_ahead=14,
            max_results=10,
            calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        )
        await _persist_refresh(tokens, clinic_id)
        return {
            "ok": True,
            "count": len(events),
            "events": events,
        }
    except (GoogleCalendarAuthError, TypeError) as e:
        return await _auth_fail(e, clinic_id)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": "list events failed", "detail": repr(e)},
            status_code=500,
        )
