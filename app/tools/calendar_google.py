# app/tools/calendar_google.py

import os
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from app.storage.redis_store import redis_set_json, redis_delete

TOKENS_KEY = "google_tokens"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

LONDON_TZ = pytz.timezone("Europe/London")


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v.strip()


def build_flow(redirect_uri: str) -> Flow:
    """
    Uses web-style secrets from env vars and creates a Flow for OAuth.
    """
    client_id = _require_env("GOOGLE_CLIENT_ID")
    client_secret = _require_env("GOOGLE_CLIENT_SECRET")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def get_auth_url(redirect_uri: str, state: str) -> str:
    """
    Returns the Google URL the user should visit to grant access.
    """
    flow = build_flow(redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def exchange_code_for_tokens(redirect_uri: str, code: str) -> Dict[str, Any]:
    """
    Exchanges the 'code' returned by Google into tokens.
    Returns token dict suitable for storage.
    """
    flow = build_flow(redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else [],
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }


def creds_from_stored(data: Dict[str, Any]) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
    )


def _ensure_tz(dt: datetime) -> datetime:
    """
    Ensure dt is timezone-aware in Europe/London.
    If dt already has tzinfo, convert to London time.
    """
    if dt.tzinfo is None:
        return LONDON_TZ.localize(dt)
    return dt.astimezone(LONDON_TZ)


def _safe_async_fire_and_forget(coro) -> None:
    """
    We are in sync code but Redis helpers are async.
    If running inside an event loop (FastAPI), schedule task.
    Otherwise run it directly (for scripts/tests).
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def _store_refreshed_tokens(creds: Credentials, stored_tokens: Dict[str, Any]) -> None:
    """
    Persist refreshed access token + expiry back to Redis.
    Keep refresh_token (may be None on refresh, so preserve existing).
    """
    updated = dict(stored_tokens)

    # Access token changes on refresh
    updated["token"] = creds.token

    # Google may not resend refresh_token on refresh; keep old one
    if creds.refresh_token:
        updated["refresh_token"] = creds.refresh_token

    updated["token_uri"] = creds.token_uri or updated.get("token_uri")
    updated["client_id"] = creds.client_id or updated.get("client_id")
    updated["client_secret"] = creds.client_secret or updated.get("client_secret")
    updated["scopes"] = list(creds.scopes) if creds.scopes else (updated.get("scopes") or SCOPES)
    updated["expiry"] = creds.expiry.isoformat() if creds.expiry else updated.get("expiry")

    _safe_async_fire_and_forget(redis_set_json(TOKENS_KEY, updated))


def _clear_tokens_if_invalid_grant(err: Exception) -> None:
    """
    If refresh token is dead (invalid_grant), wipe tokens from Redis.
    That forces re-auth and prevents repeated dead-air failures.
    """
    if "invalid_grant" in str(err).lower():
        _safe_async_fire_and_forget(redis_delete(TOKENS_KEY))


def _get_authed_creds(stored_tokens: Dict[str, Any]) -> Credentials:
    """
    Build creds and refresh them if expired.
    If refresh fails due to invalid_grant, clear stored tokens.
    """
    creds = creds_from_stored(stored_tokens)

    # If already valid, great
    if creds.valid:
        return creds

    # If expired but refreshable, refresh now
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _store_refreshed_tokens(creds, stored_tokens)
            return creds
        except RefreshError as e:
            _clear_tokens_if_invalid_grant(e)
            raise
        except Exception as e:
            # other unexpected token errors
            raise

    # Not valid and can't refresh (no refresh_token)
    raise RefreshError("Missing or invalid refresh_token; please re-auth Google Calendar.")


def get_calendar_service(stored_tokens: Dict[str, Any]):
    """
    Returns Google Calendar API service with refreshed credentials (if needed).
    """
    creds = _get_authed_creds(stored_tokens)

    # cache_discovery=False avoids file caching issues on some hosts
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def freebusy(
    stored_tokens: Dict[str, Any],
    time_min: datetime,
    time_max: datetime,
    calendar_id: str = "primary",
) -> List[Dict[str, str]]:
    """
    Returns busy blocks between time_min/time_max.
    """
    service = get_calendar_service(stored_tokens)

    time_min = _ensure_tz(time_min)
    time_max = _ensure_tz(time_max)

    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": calendar_id}],
    }

    resp = service.freebusy().query(body=body).execute()
    busy = resp.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return busy


def create_event(
    stored_tokens: Dict[str, Any],
    start_dt: datetime,
    end_dt: datetime,
    summary: str,
    description: str = "",
    calendar_id: str = "primary",
) -> Dict[str, Any]:
    """
    Creates a calendar event and returns the created event object.
    """
    service = get_calendar_service(stored_tokens)

    start_dt = _ensure_tz(start_dt)
    end_dt = _ensure_tz(end_dt)

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/London"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/London"},
    }

    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created


def list_upcoming_events(
    stored_tokens: Dict[str, Any],
    days_ahead: int = 30,
    max_results: int = 25,
    calendar_id: str = "primary",
) -> List[Dict[str, Any]]:
    """
    Returns upcoming events for the next N days.
    """
    service = get_calendar_service(stored_tokens)

    now = datetime.utcnow().isoformat() + "Z"
    end = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + "Z"

    resp = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=now,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )
    return resp.get("items", [])


def patch_event_time(
    stored_tokens: Dict[str, Any],
    event_id: str,
    start_dt: datetime,
    end_dt: datetime,
    calendar_id: str = "primary",
) -> Dict[str, Any]:
    """
    Updates an existing event’s start/end time.
    """
    service = get_calendar_service(stored_tokens)

    start_dt = _ensure_tz(start_dt)
    end_dt = _ensure_tz(end_dt)

    body = {
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/London"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/London"},
    }

    updated = service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
    return updated


def delete_event(
    stored_tokens: Dict[str, Any],
    event_id: str,
    calendar_id: str = "primary",
) -> bool:
    """
    Deletes an event. Returns True if no error.
    """
    service = get_calendar_service(stored_tokens)
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return True
