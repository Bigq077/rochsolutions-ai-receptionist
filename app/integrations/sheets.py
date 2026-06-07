# app/integrations/sheets.py
from __future__ import annotations

import json
import os
import time
from typing import Any, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_creds() -> Credentials:
    """
    Loads service account creds from env.
    Prefer GOOGLE_SA_JSON (full JSON). Fallback to GOOGLE_SA_FILE.
    """
    sa_json = os.getenv("GOOGLE_SA_JSON")
    if sa_json:
        info = json.loads(sa_json)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    sa_file = os.getenv("GOOGLE_SA_FILE")
    if sa_file:
        return Credentials.from_service_account_file(sa_file, scopes=SCOPES)

    raise RuntimeError("Missing GOOGLE_SA_JSON or GOOGLE_SA_FILE for Google Sheets auth.")


def _get_service():
    """
    Build Sheets API client. Kept internal so we can reuse later if desired.
    """
    creds = _get_creds()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def append_row(
    spreadsheet_id: str,
    sheet_range: str,
    values: List[Any],
    retries: int = 3,
) -> None:
    """
    Appends one row to a Google Sheet.

    Args:
        spreadsheet_id: Google Sheet ID (from URL).
        sheet_range: e.g. "Calls!A:Z" or "Sheet1!A:Z"
        values: row values list
        retries: transient retry count (429/500/503)
    """
    service = _get_service()
    body = {"values": [values]}

    for attempt in range(retries):
        try:
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=sheet_range,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
            return
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if attempt < retries - 1 and status in (429, 500, 503):
                time.sleep(0.6 * (attempt + 1))
                continue
            raise


def safe_append_row(
    spreadsheet_id: str,
    sheet_range: str,
    values: List[Any],
    retries: int = 3,
) -> Optional[str]:
    """
    Same as append_row but never raises.
    Returns:
        None on success, or a short error string on failure.
    """
    try:
        append_row(
            spreadsheet_id=spreadsheet_id,
            sheet_range=sheet_range,
            values=values,
            retries=retries,
        )
        return None
    except Exception as e:
        return str(e)[:300]
