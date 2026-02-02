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
    """Raised when Google OAuth tokens are invalid/revoked (invalid_grant etc.)."""
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
        include_granted_scopes="false",  # 👈 IMPORTANT FIX
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
    """
    Always return timezone-aware datetime in UTC (or None).
    Accepts '...Z' or '+00:00' or naive ISO; coerces to UTC aware.
    """
    if not expiry:
        return None
    try:
        dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expiry_dt: Optional[datetime], skew_seconds: int = 90) -> bool:
    """
    Expiry check with a small skew so we refresh slightly early.
    Prevents mid-request expiry.
    """
    if not expiry_dt:
        return True
    expiry_dt = expiry_dt.astimezone(timezone.utc)
    return expiry_dt <= (_now_utc() + timedelta(seconds=skew_seconds))


def creds_from_stored(data: Dict[str, Any]) -> Credentials:
    expiry_dt = _parse_expiry(data.get("expiry"))
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
        expiry=expiry_dt,  # always aware UTC (or None)
    )


def _refresh_if_needed(creds: Credentials, stored_tokens: Dict[str, Any]) -> None:
    """
    Refresh without using creds.expired (avoids naive/aware comparison bugs).
    Updates stored_tokens IN-PLACE so upstream can persist it.
    """
    try:
        expiry_dt = creds.expiry.astimezone(timezone.utc) if creds.expiry else None
        needs_refresh = _is_expired(expiry_dt)

        if needs_refresh:
            if not creds.refresh_token:
                raise GoogleCalendarAuthError("No refresh_token available (reconnect Google Calendar).")

            creds.refresh(Request())

            # Update in-memory token dict for this request chain
            stored_tokens["token"] = creds.token
            stored_tokens["expiry"] = creds.expiry.isoformat() if creds.expiry else None

            # Google often does not return refresh_token on refresh; keep existing unless provided
            if creds.refresh_token:
                stored_tokens["refresh_token"] = creds.refresh_token

    except RefreshError as e:
        # invalid_grant, revoked, etc.
        raise GoogleCalendarAuthError(str(e)) from e
    except GoogleCalendarAuthError:
        raise
    except Exception as e:
        raise GoogleCalendarAuthError(f"Token refresh failed: {e}") from e


def get_calendar_service(stored_tokens: Dict[str, Any]):
    creds = creds_from_stored(stored_tokens)
    _refresh_if_needed(creds, stored_tokens)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _ensure_tz(dt: datetime) -> datetime:
    """
    Ensure dt is timezone-aware in Europe/London.
    """
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
    return resp.get("calendars", {}).get(calendar_id, {}).get("busy", [])


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
    return service.events().insert(calendarId=calendar_id, body=event).execute()


def list_upcoming_events(
    stored_tokens: Dict[str, Any],
    days_ahead: int = 30,
    max_results: int = 25,
    calendar_id: str = "primary",
) -> List[Dict[str, Any]]:
    service = get_calendar_service(stored_tokens)

    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(days=days_ahead)

    resp = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=now_utc.isoformat(),
            timeMax=end_utc.isoformat(),
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
    return service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()


def delete_event(
    stored_tokens: Dict[str, Any],
    event_id: str,
    calendar_id: str = "primary",
) -> bool:
    service = get_calendar_service(stored_tokens)
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return True
