# app/tools/handoff.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ✅ Keep your existing env var name to avoid breaking production
SHEET_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()

# Optional: default tabs
DEFAULT_MESSAGES_TAB = os.getenv("GOOGLE_SHEETS_MESSAGES_TAB", "Messages").strip()
DEFAULT_SUMMARY_TAB = os.getenv("GOOGLE_SHEETS_SUMMARY_TAB", "CallSummaries").strip()


def _get_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw or not SHEET_ID:
        return None

    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    # ✅ cache_discovery=False is safer on some hosts
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _append_values(values: List[List[Any]], tab_name: str) -> bool:
    """
    Low-level append. Returns True/False, never raises.
    """
    service = _get_service()
    if not service:
        return False

    # If tab doesn't exist, Sheets API will error — we just return False
    body = {"values": values}

    try:
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"{tab_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()
        return True
    except Exception:
        return False


# -------------------------------------------------------------------
# EXISTING API (kept) — logs callbacks / manual notes / messages
# -------------------------------------------------------------------
def send_to_sheet(
    name: str,
    phone: str,
    intent: str,
    message: str,
    call_sid: str,
    source: str = "AI Receptionist",
    tab_name: Optional[str] = None,
) -> bool:
    """
    Keeps your existing contract.
    Now supports writing to a specific tab (default: DEFAULT_MESSAGES_TAB).
    """
    tab = (tab_name or DEFAULT_MESSAGES_TAB).strip()

    values = [[
        datetime.utcnow().isoformat(),
        name,
        phone,
        intent,
        message,
        source,
        call_sid,
    ]]

    return _append_values(values, tab)


# -------------------------------------------------------------------
# NEW API — call summaries (one row per call at end of call)
# -------------------------------------------------------------------
def append_summary_row(row: List[Any], tab_name: Optional[str] = None) -> bool:
    """
    Append a pre-flattened call summary row (already in column order).
    """
    tab = (tab_name or DEFAULT_SUMMARY_TAB).strip()
    values = [row]
    return _append_values(values, tab)


def send_call_summary(
    *,
    summary: dict | None = None,
    row: List[Any],
    call_sid: str = "",
    tab_name: Optional[str] = None,
) -> bool:
    """
    Convenience wrapper. Doesn't require 'summary' but allows you to pass it for debugging.
    """
    # Optional: ensure call_sid is included somewhere if you want.
    # We do NOT mutate 'row' to avoid changing your row schema.
    return append_summary_row(row, tab_name=tab_name)
