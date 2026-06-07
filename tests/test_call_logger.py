"""
tests/test_call_logger.py
-------------------------
Unit tests for app.call_logger.CallLogger.

Covers:
  1. flush() writes a JSONL file in logs/ on the expected date
  2. Record contains expected top-level keys
  3. complete(success=True, reason="booked") is reflected in the record
  4. complete(success=False, reason="graceful_exit") is reflected
  5. duration_s is calculated correctly
  6. flush() is safe when complete() was never called (graceful degradation)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.call_logger import CallLogger, _LOG_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**overrides):
    base = {
        "clinic_id": "alcester",
        "twilio_from": "+441234567890",
        "twilio_to": "+441789000001",
        "state": "COMPLETE",
        "collected": {"name": "Alice", "phone": "+441234567890"},
        "reason": "back pain",
        "chosen_day": "Monday",
        "selected_slot": "10:00",
        "booking_confirmed": True,
        "acuity_booking_id": "abc123",
        "transfer_attempted": False,
        "graceful_exit": False,
        "slot_retry_counts": {},
        "turns": [{"role": "assistant", "text": "Hi"}, {"role": "user", "text": "Hello"}],
        "_tone_state": {"tone": "warm", "locked": True, "word_counts": []},
        "confirmation_sms_sent": False,
    }
    base.update(overrides)
    return base


async def _flush_in_tmp(logger: CallLogger, tmp_dir: Path) -> dict:
    """Flush the logger using tmp_dir as the log directory and return the parsed record."""
    with patch("app.call_logger._LOG_DIR", tmp_dir):
        await logger.flush()

    date_str = logger._start_utc.strftime("%Y-%m-%d")
    log_file = tmp_dir / f"calls_{date_str}.jsonl"
    assert log_file.exists(), f"Expected log file not found: {log_file}"
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCallLoggerFlush:
    """Test 1 — flush() creates the expected file."""

    def test_creates_jsonl_file_in_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session()
            cl = CallLogger("CA_test001", session)
            cl.complete(success=True, reason="booked")
            asyncio.run(_flush_in_tmp(cl, tmp_dir))

            date_str = cl._start_utc.strftime("%Y-%m-%d")
            assert (tmp_dir / f"calls_{date_str}.jsonl").exists()


class TestCallLoggerRecord:
    """Test 2 — Record contains expected top-level keys."""

    def test_record_has_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session()
            cl = CallLogger("CA_test002", session)
            cl.complete(success=True, reason="booked")
            record = asyncio.run(_flush_in_tmp(cl, tmp_dir))

        required_keys = {
            "call_sid", "clinic_id", "start_utc", "end_utc", "duration_s",
            "success", "reason", "caller_number", "dialled_number",
            "final_state", "collected", "booking_confirmed", "acuity_booking_id",
            "transfer_attempted", "graceful_exit", "total_retries",
            "slot_retry_counts", "turn_count", "tone",
        }
        for key in required_keys:
            assert key in record, f"Missing key: {key}"


class TestCallLoggerSuccessReason:
    """Test 3 — complete(success=True, reason='booked') reflected in record."""

    def test_success_true_reason_booked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session(booking_confirmed=True)
            cl = CallLogger("CA_test003", session)
            cl.complete(success=True, reason="booked")
            record = asyncio.run(_flush_in_tmp(cl, tmp_dir))

        assert record["success"] is True
        assert record["reason"] == "booked"
        assert record["booking_confirmed"] is True


class TestCallLoggerFailureReason:
    """Test 4 — complete(success=False, reason='graceful_exit') reflected."""

    def test_success_false_reason_graceful_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session(
                booking_confirmed=False,
                graceful_exit=True,
                state="COLLECT_NAME",
            )
            cl = CallLogger("CA_test004", session)
            cl.complete(success=False, reason="graceful_exit")
            record = asyncio.run(_flush_in_tmp(cl, tmp_dir))

        assert record["success"] is False
        assert record["reason"] == "graceful_exit"
        assert record["graceful_exit"] is True


class TestCallLoggerDuration:
    """Test 5 — duration_s is calculated correctly."""

    def test_duration_approximately_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session()
            cl = CallLogger("CA_test005", session)

            # Manually set start 90 seconds ago
            cl._start_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            cl.complete(success=True, reason="booked")
            cl._end_utc = datetime(2026, 1, 1, 12, 1, 30, tzinfo=timezone.utc)

            record = asyncio.run(_flush_in_tmp(cl, tmp_dir))

        assert record["duration_s"] == 90


class TestCallLoggerNoComplete:
    """Test 6 — flush() without complete() does not raise."""

    def test_flush_without_complete_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            session = _make_session()
            cl = CallLogger("CA_test006", session)
            # Do NOT call complete() — should not raise
            record = asyncio.run(_flush_in_tmp(cl, tmp_dir))

        assert record["success"] is None
        assert record["reason"] is None
        assert record["call_sid"] == "CA_test006"
