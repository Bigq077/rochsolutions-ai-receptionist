# app/tools/handoff.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()

DEFAULT_MESSAGES_TAB = os.getenv("GOOGLE_SHEETS_MESSAGES_TAB", "Messages").strip()
DEFAULT_SUMMARY_TAB = os.getenv("GOOGLE_SHEETS_SUMMARY_TAB", "CallSummaries").strip()


def _get_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw or not SHEET_ID:
        print("Sheets not configured: missing GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SHEETS_ID")
        return None

    try:
        info = json.loads(raw)
    except Exception as e:
        print("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON:", repr(e))
        return None

    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        print("Failed to build Google Sheets client:", repr(e))
        return None


def _append_values(values: List[List[Any]], tab_name: str) -> bool:
    service = _get_service()
    if not service:
        return False

    body = {"values": values}

    try:
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"{tab_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()
        print(f"✅ Sheets append ok -> tab={tab_name} rows={len(values)}")
        return True
    except Exception as e:
        print("❌ Sheets append failed:", repr(e))
        print("Sheet ID:", SHEET_ID)
        print("Tab:", tab_name)
        return False


def send_to_sheet(
    name: str,
    phone: str,
    intent: str,
    message: str,
    call_sid: str,
    source: str = "AI Receptionist",
    tab_name: Optional[str] = None,
) -> bool:
    tab = (tab_name or DEFAULT_MESSAGES_TAB).strip()

    values = [[
        datetime.utcnow().isoformat() + "Z",
        name,
        phone,
        intent,
        message,
        source,
        call_sid,
    ]]

    return _append_values(values, tab)


def append_summary_row(row: List[Any], tab_name: Optional[str] = None) -> bool:
    tab = (tab_name or DEFAULT_SUMMARY_TAB).strip()
    return _append_values([row], tab)


def send_call_summary(
    *,
    summary: dict | None = None,
    row: List[Any],
    call_sid: str = "",
    tab_name: Optional[str] = None,
) -> bool:
    # We keep this wrapper as-is. 'summary' is optional.
    return append_summary_row(row, tab_name=tab_name)
