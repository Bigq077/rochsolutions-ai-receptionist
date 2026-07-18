"""
app/call_logger.py
------------------
Structured per-call logging.

Each call gets one JSONL entry written to logs/calls_YYYY-MM-DD.jsonl.
The logger is instantiated at call-start, updated throughout the call,
and flushed to disk on teardown.

Usage:
    logger = CallLogger(call_sid, session)
    ...
    logger.complete(success=True, reason="booked")
    await logger.flush()
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

_LOG_DIR = Path("logs")


class CallLogger:
    """Accumulates per-call data and writes one JSONL record on flush."""

    def __init__(self, call_sid: str, session: Dict[str, Any]) -> None:
        self.call_sid: str = call_sid
        self._start_utc: datetime = datetime.now(timezone.utc)
        self._end_utc: Optional[datetime] = None
        self._success: Optional[bool] = None
        self._reason: Optional[str] = None

        # Snapshot lightweight fields from session at call-start
        self._clinic_id: Optional[str] = session.get("clinic_id")
        self._twilio_from: Optional[str] = session.get("twilio_from")
        self._twilio_to: Optional[str] = session.get("twilio_to")

        # These are updated from the live session reference just before flush
        self._session_ref: Dict[str, Any] = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(self, success: bool, reason: str) -> None:
        """
        Mark the call as finished.

        :param success: True if the call ended in a booking or clean resolution.
        :param reason:  Short label, e.g. "booked", "transferred", "graceful_exit",
                        "pipeline_error", "caller_hung_up".
        """
        self._end_utc = datetime.now(timezone.utc)
        self._success = success
        self._reason = reason

    async def flush(self) -> None:
        """Write one JSONL record to logs/calls_YYYY-MM-DD.jsonl."""
        record = self._build_record()

        date_str = self._start_utc.strftime("%Y-%m-%d")
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _LOG_DIR / f"calls_{date_str}.jsonl"

        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            _log.info("[call_logger] flushed call_sid=%s to %s", self.call_sid, log_path)
        except OSError as exc:
            _log.error("[call_logger] flush failed call_sid=%s: %r", self.call_sid, exc)

    def build_record(self) -> Dict[str, Any]:
        """
        Public accessor for the structured per-call record.

        Same dict that flush() writes to JSONL — reused by the operator
        failure-alerting layer (app/obs/alerts.py) so what triggers an alert and
        what lands in the log never diverge. Read-only; does not mutate state.
        """
        return self._build_record()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_record(self) -> Dict[str, Any]:
        s = self._session_ref
        end = self._end_utc or datetime.now(timezone.utc)

        duration_s = round((end - self._start_utc).total_seconds())

        collected: Dict[str, Any] = s.get("collected") or {}

        # Build turn list: [{role, text}]
        turns: List[Dict[str, str]] = s.get("turns") or []

        # Count retries across all slots
        slot_retry_counts: Dict[str, int] = s.get("slot_retry_counts") or {}
        total_retries = sum(slot_retry_counts.values())

        return {
            "call_sid":        self.call_sid,
            "clinic_id":       self._clinic_id,
            "start_utc":       self._start_utc.isoformat(),
            "end_utc":         end.isoformat(),
            "duration_s":      duration_s,
            "success":         self._success,
            "reason":          self._reason,
            "caller_number":   self._twilio_from,
            "dialled_number":  self._twilio_to,
            "final_state":     s.get("state"),
            "collected": {
                "name":          collected.get("name") or s.get("full_name"),
                "phone":         collected.get("phone") or s.get("phone_number"),
                "reason":        collected.get("reason") or s.get("reason"),
                "chosen_day":    collected.get("chosen_day") or s.get("chosen_day"),
                "selected_slot": collected.get("selected_slot") or s.get("selected_slot"),
            },
            "booking_confirmed": s.get("booking_confirmed", False),
            "acuity_booking_id": s.get("acuity_booking_id"),
            "transfer_attempted": s.get("transfer_attempted", False),
            "graceful_exit":      s.get("graceful_exit", False),
            "total_retries":      total_retries,
            "slot_retry_counts":  slot_retry_counts,
            "turn_count":         len(turns),
            "tone":               (s.get("_tone_state") or {}).get("tone"),
        }
