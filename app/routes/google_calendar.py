# app/tools/calendar_google.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

LONDON_TZ = pytz.timezone("Europe/London")


class GoogleCalendarAuthError(RuntimeError):
    """
    Raised when Google OAuth tokens are invalid/revoked (invalid_grant etc.).
    Caller should handle by clearing stored tokens and asking user to reconnect.
    """
    pass


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v.strip()


def build_flow(redirect_uri: str) -> Flow:
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
    flow = build_flow(redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def exchange_code_for_tokens(redirect_uri: str, code: str) -> Dict[str, Any]:
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


def _parse_expiry(expiry: Optional[str]) -> Optional[datetime]:
    if not expiry:
        return None
    try:
        # Handles "2026-02-01T12:34:56.123456" or with timezone
        dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def creds_from_stored(data: Dict[str, Any]) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
        expiry=_parse_expiry(data.get("expiry")),
    )


def get_calendar_service(stored_tokens: Dict[str, Any]):
    """
    Build a Calendar API service.
    Proactively refreshes if expired.
    Raises GoogleCalendarAuthError on invalid_grant / revoked tokens.
    """
    creds = creds_from_stored(stored_tokens)

    # Proactive refresh prevents random failures mid-call
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except RefreshError as e:
        # This is your "invalid_grant" case
        raise GoogleCalendarAuthError(str(e)) from e

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return LONDON_TZ.localize(dt)
    return dt.astimezone(LONDON_TZ)


def freebusy(
    stored_tokens: Dict[str, Any],
    time_min: datetime,
    time_max: datetime,
    calendar_id: str = "primary",
) -> List[Dict[str, str]]:
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
    service = get_calendar_service(stored_tokens)
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return True
