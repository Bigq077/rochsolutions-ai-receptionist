# app/tools/calendar_google.py

import os
from datetime import datetime
from typing import Optional, List, Dict, Any

import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

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
    Uses 'installed app' style secrets from env vars (client_id/client_secret)
    and creates a Flow for web auth.
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
        access_type="offline",          # ensures refresh_token on first consent
        include_granted_scopes="true",
        prompt="consent",               # forces refresh_token issuance more reliably
        state=state,
        response_type="code"
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


def get_calendar_service(stored_tokens: Dict[str, Any]):
    creds = creds_from_stored(stored_tokens)
    return build("calendar", "v3", credentials=creds)


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

    # Ensure timezone aware ISO strings
    if time_min.tzinfo is None:
        time_min = LONDON_TZ.localize(time_min)
    if time_max.tzinfo is None:
        time_max = LONDON_TZ.localize(time_max)

    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": calendar_id}],
    }
    resp = service.freebusy().query(body=body).execute()
    busy = resp["calendars"][calendar_id].get("busy", [])
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

    if start_dt.tzinfo is None:
        start_dt = LONDON_TZ.localize(start_dt)
    if end_dt.tzinfo is None:
        end_dt = LONDON_TZ.localize(end_dt)

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/London"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/London"},
    }

    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created

