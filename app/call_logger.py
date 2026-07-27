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

        Same dict that flush() writes to JSONL — reused by the Phase 1 durable
        capture (app/obs/store.py) so the Postgres row and the JSONL log never
        diverge. Read-only; does not mutate state.
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
                # ── Booking integrity (F-021) ────────────────────────────────
                # book_appointment can book a different service than the one
                # check_availability was called with — reproducible 4/4, still
                # open. Until now the record held neither value, so "did she
                # book what the caller asked for?" was answerable only by
                # listening back to the call.
                #
                # Both already exist on the session: every booking path writes
                # collected["service"] (receptionist_tools 2528/4243/4681/4808),
                # and check_availability pins _checked_service (:3765) so that
                # booking uses the same one. They were simply never copied here.
                #
                # A booked row where these two disagree IS F-021 — no audio
                # required. `location` is the modality (bolton / remote /
                # home_visit), which determines price and duration.
                "service":         collected.get("service"),
                "checked_service": s.get("_checked_service"),
                "location":        collected.get("location") or s.get("selected_location"),
            },
            # bool() so a session that still holds the seeded None (see
            # media_streams/session.py) records False rather than NULL — a NULL
            # here read as "capture didn't populate it" and cost an hour of
            # diagnosis on 2026-07-26.
            "booking_confirmed": bool(s.get("booking_confirmed")),
            "acuity_booking_id": s.get("acuity_booking_id"),
            # Clinics on Google Calendar never set acuity_booking_id — they set
            # calendar_event_id, which was captured nowhere. So for those clinics
            # the durable record held NO evidence a booking existed: no flag, no
            # id. Booking integrity was unmeasurable, which is bar #1 in
            # CLAUDE.md §6.
            "calendar_event_id": s.get("calendar_event_id") or None,
            "transfer_attempted": s.get("transfer_attempted", False),
            "graceful_exit":      s.get("graceful_exit", False),
            "total_retries":      total_retries,
            "slot_retry_counts":  slot_retry_counts,
            "turn_count":         len(turns),
            "tone":               (s.get("_tone_state") or {}).get("tone"),
            "screening":          self._screening_summary(),
        }

    def _screening_summary(self) -> Dict[str, Any]:
        """Clinical screening state, for the durable call record.

        All of this already lived in the session; none of it was captured. The
        consequence showed up in Jules's 2026-07-25 sweep: the finding that
        mattered most — `dvt ORPHAN×1, ARMED×0`, meaning the deterministic layer
        was dormant and the model was silently doing the whole job — was only
        visible to a human reading a full call log. It could not be queried, so
        it could not be trended across a sweep or alerted on.

        `arm_paths` is the field that answers it: {screen_id: how_it_armed}, with
        "trigger" meaning Layer 1 caught the presentation and "orphan" meaning
        only the model did. An `orphan` with no `trigger` anywhere in a day's
        calls is the dormant-Layer-1 signature, and it is now one SQL query.

        Deliberately NOT latency: the per-turn timings live on the connection
        object rather than the session, so capturing them means editing
        connection.py (12k lines, the danger zone in CLAUDE.md §4). The `[LAT]`
        log lines already feed scripts/analyse_calls.py, which is the right tool
        for that until it can be done safely.

        Returns {} for clinics without screening, so the column stays null
        rather than filling with empty structures.
        """
        s = self._session_ref
        completed = s.get("screens_completed") or []
        arm_paths = s.get("screen_arm_paths") or {}
        red_flag = s.get("screen_red_flag")
        pending = s.get("pending_screen")
        truncated = s.get("screen_truncation_downgrades") or []
        escalated = bool(s.get("safety_escalation"))

        if not (completed or arm_paths or red_flag or pending or truncated or escalated):
            return {}

        return {
            # How each screen armed — "trigger" (Layer 1) vs "orphan" (Layer 2 only).
            "arm_paths":       dict(arm_paths),
            "completed":       list(completed),
            # Set and still set at teardown = the screen was never resolved.
            "pending_at_end":  pending,
            "red_flag":        red_flag,
            # A safety answer the endpointer cut mid-clause; we re-asked.
            "truncated":       list(truncated),
            "safety_escalation": escalated,
        }
