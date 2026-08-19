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

        # Per-turn latency, drained once from the timing buffer (see
        # _latency_block). None = not drained yet; {} = drained and empty.
        self._latency: Optional[Dict[str, Any]] = None

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

        # ── Gate 5f state (F-023) ────────────────────────────────────────────
        # The obs transcript is built from `full_reply`, which llm_stream
        # assembles RAW (:1109) — Gate 5f runs on the TTS path only (:1497).
        # So a transcript can end "All booked" on a call that booked nothing,
        # and there is no way to tell from the row whether the caller actually
        # heard that (a real phantom) or heard the guard's re-steer instead.
        # That ambiguity was reported as a phantom on 2026-07-27 and cost an
        # evening. Capturing the guard's own counters settles it:
        #   fired > 0 and not booked -> guard caught it, caller heard the
        #                               re-steer. NOT a phantom.
        #   "All booked" and fired == 0 -> the guard never matched. REAL. Escalate.
        # int()/bool() coerced, and never allowed to raise: this runs at
        # teardown on every call.
        try:
            _fc_fired = int(s.get("_false_confirm_guard_fired") or 0)
        except (TypeError, ValueError):
            _fc_fired = 0

        # Which build served this call. Recorded rather than reconstructed:
        # detect_defects' deploy-boundary list has misattributed calls four
        # times, most recently on 2026-07-31 when a boundary set from push time
        # plus the usual Render lag estimate labelled a call with the previous
        # build — the deploy had landed in about a minute. A wrong build label
        # makes a fix look unproven when it worked, or proven when it never ran.
        from app.build_info import build_sha as _build_sha

        return {
            "call_sid":        self.call_sid,
            "clinic_id":       self._clinic_id,
            "build_sha":       _build_sha(),
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
            # Output-guard state — see the Gate 5f note above.
            # Booking-path guard state. Each of these already existed on the
            # session and was visible only in the Render logs, so answering "did
            # the guard fire on that call" meant opening a log window for the
            # right service in the right five-minute window — which on 30 Jul
            # cost three round-trips and still did not get answered. They are
            # counters, not booleans: firing once is the guard working, firing
            # repeatedly is the caller stuck in it, and that difference is the
            # whole diagnosis. int() because a session that never armed one holds
            # None, and a NULL here reads as "capture didn't populate it" — the
            # ambiguity that cost an hour on 2026-07-26.
            "guards": {
                "false_confirm_fired":    _fc_fired,
                "false_confirm_resteered": bool(s.get("_false_confirm_resteered")),
                # d88e0da — refused a booking whose day was not the day spoken.
                "c1_write_guard_fired":   int(s.get("_c1_write_guard_fired") or 0),
                # 6f63057 — pushed the model to check_availability after the
                # caller named a different day (Bug B).
                "different_day_steer_fired": int(
                    s.get("_different_day_steer_fired") or 0
                ),
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
            "latency":            self._latency_block(),
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

        Latency is captured separately, by _latency_block below — this method's
        earlier note that it could not be was over-cautious. The timings do live
        on the connection object rather than the session, but they did not need
        to be reached from here: TurnTiming.emit() already holds every figure,
        so the only genuine gap was that a turn did not know its call_sid. That
        cost one keyword argument in connection.py, not surgery on it.

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

    def _latency_block(self) -> Optional[Dict[str, Any]]:
        """Per-turn latency for this call: {"summary": {...}, "turns": [...]}.

        Drains app.media_streams.latency_timing's per-call buffer, which
        TurnTiming.emit() has been filling turn by turn. Returns None when there
        is nothing — LATENCY_TIMING OFF (the default), or a call that never
        reached a measured turn — so the obs column stays NULL rather than
        filling with empty structures, the same convention _screening_summary
        follows.

        DRAINED ONCE, THEN CACHED. This is load-bearing, not an optimisation:
        _build_record runs three times per teardown — flush() writes the JSONL,
        then connection.py calls build_record() again for obs capture and a third
        time for alert routing. A drain on each call would hand the turns to the
        JSONL and leave obs and the alert route with nothing, which is precisely
        the row this whole change exists to populate.

        Never raises. This runs at teardown on every call, and an observability
        layer must not be able to break one.
        """
        if self._latency is not None:
            return self._latency or None
        try:
            from app.media_streams import latency_timing as _lat

            turns = _lat.drain_call(self.call_sid)
            self._latency = (
                {"summary": _lat.summarise(turns), "turns": turns} if turns else {}
            )
        except Exception as exc:  # pragma: no cover - defensive; teardown path
            _log.warning("[call_logger] latency drain failed call_sid=%s: %r",
                         self.call_sid, exc)
            self._latency = {}
        return self._latency or None
