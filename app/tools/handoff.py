# app/tools/handoff.py
from __future__ import annotations

import json
import os
import asyncio
import threading
from datetime import datetime
from typing import Any, List, Optional

import logging

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()

# latency-eval isolation: Google Sheets writes are OFF unless explicitly enabled.
# This guarantees eval calls never pollute a live clinic's Sheet even if
# GOOGLE_SHEETS_ID is still set in the Render env (autoDeploy is off, so blanking
# the env var in the dashboard is unreliable). Belt-and-suspenders, mirrors the
# SMS_ENABLED default-flip. Do NOT port this default to main/theorem/jv live branches.
SHEETS_ENABLED = os.getenv("SHEETS_ENABLED", "false").strip().lower() == "true"

DEFAULT_MESSAGES_TAB = os.getenv("GOOGLE_SHEETS_MESSAGES_TAB", "Messages").strip()
DEFAULT_SUMMARY_TAB = os.getenv("GOOGLE_SHEETS_SUMMARY_TAB", "CallSummaries").strip()

# -----------------------------------------------------------------------------
# Cached Google Sheets client (avoid rebuilding per request)
# -----------------------------------------------------------------------------
_service_lock = threading.Lock()
_cached_service = None


def _get_service():
    """
    Returns a cached Google Sheets service client.
    Safe to call many times.
    """
    global _cached_service

    if not SHEETS_ENABLED:
        # WARNING, not print: this is the most common reason a clinic silently
        # writes no call records at all, and a bare print carries no level and
        # no logger name, so it does not show up when an operator greps the
        # Render log for warnings. Every live branch inherits the default
        # below, so this fires on a real clinic far more often than on eval.
        logger.warning(
            "Sheets append will be SKIPPED — SHEETS_ENABLED is not 'true' "
            "(env value %r). No call record is being written for this call.",
            os.getenv("SHEETS_ENABLED", ""),
        )
        return None

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw or not SHEET_ID:
        logger.warning(
            "Sheets not configured — append will be skipped: "
            "GOOGLE_SERVICE_ACCOUNT_JSON %s, GOOGLE_SHEETS_ID %s",
            "MISSING" if not raw else "present",
            "MISSING" if not SHEET_ID else "present",
        )
        return None

    with _service_lock:
        if _cached_service is not None:
            return _cached_service

        try:
            info = json.loads(raw)
        except Exception as e:
            logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: %r", e)
            return None

        try:
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            _cached_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            return _cached_service
        except Exception as e:
            logger.warning("Failed to build Google Sheets client: %r", e)
            return None


def _append_values(values: List[List[Any]], tab_name: str) -> bool:
    """
    Blocking call to Google Sheets. Do NOT call this directly from Twilio request path.
    Use fire-and-forget wrappers below.
    """
    service = _get_service()
    if not service:
        logger.warning(
            "Sheets append SKIPPED (no client) tab=%r rows=%d — see the "
            "'SHEETS_ENABLED is not' / 'Sheets not configured' / 'invalid JSON' "
            "/ 'Failed to build' warning above for the reason",
            tab_name, len(values),
        )
        return False

    body = {"values": values}

    try:
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()
        logger.info("Sheets append ok tab=%r rows=%d", tab_name, len(values))
        return True
    except Exception as e:
        logger.warning(
            "Sheets append failed: tab=%r sheet_id=%r error=%r — "
            "check the tab exists and GOOGLE_SHEETS_MESSAGES_TAB matches exactly",
            tab_name, SHEET_ID[:8] + "…" if SHEET_ID else "", e,
        )
        return False


# -----------------------------------------------------------------------------
# Existing API (sync) — kept for compatibility
# -----------------------------------------------------------------------------
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
    # wrapper kept as-is; summary optional
    return append_summary_row(row, tab_name=tab_name)


# -----------------------------------------------------------------------------
# ✅ Fire-and-forget async wrappers (USE THESE from Twilio webhook paths)
# -----------------------------------------------------------------------------
async def append_summary_row_async(row: List[Any], tab_name: Optional[str] = None) -> bool:
    """
    Runs the blocking Sheets append in a worker thread.
    """
    tab = (tab_name or DEFAULT_SUMMARY_TAB).strip()
    return await asyncio.to_thread(_append_values, [row], tab)


def fire_and_forget_append_summary_row(row: List[Any], tab_name: Optional[str] = None) -> None:
    """
    Non-blocking: schedules a background task if we're in an event loop.
    If called outside an event loop, runs in a thread.
    Never raises to caller.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(append_summary_row_async(row, tab_name=tab_name))
    except RuntimeError:
        # no running loop -> fallback: thread
        threading.Thread(
            target=_append_values,
            args=([row], (tab_name or DEFAULT_SUMMARY_TAB).strip()),
            daemon=True,
        ).start()
    except Exception:
        pass
