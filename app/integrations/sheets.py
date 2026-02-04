from __future__ import annotations

import json
import os
import time
from typing import Any, Optional, List

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


def append_row(
    spreadsheet_id: str,
    sheet_range: str,
    values: List[Any],
    retries: int = 3,
) -> None:
    """
    Appends one row to a Google Sheet.
    sheet_range example: "Calls!A:Z" or "Sheet1!A:Z"
    values is a list (row) to append.
    """
    creds = _get_creds()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

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
            # Retry on transient errors
            status = getattr(e.resp, "status", None)
            if attempt < retries - 1 and status in (429, 500, 503):
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
