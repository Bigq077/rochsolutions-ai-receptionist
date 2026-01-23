import json
import os
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")


def _get_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw or not SHEET_ID:
        return None

    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def send_to_sheet(
    name: str,
    phone: str,
    intent: str,
    message: str,
    call_sid: str,
    source: str = "AI Receptionist",
):
    service = _get_service()
    if not service:
        return False

    values = [[
        datetime.utcnow().isoformat(),
        name,
        phone,
        intent,
        message,
        source,
        call_sid,
    ]]

    body = {"values": values}

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()

    return True
