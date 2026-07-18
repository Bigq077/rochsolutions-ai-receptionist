"""
Fixtures for the LLM-as-judge tests.

No real Anthropic call is ever made — `judge._call_model` is mocked in the tests
that need it. Transcripts are synthetic (no PII). The store is a throwaway SQLite
DB so the end-to-end run_and_store path can be exercised offline.
"""
from __future__ import annotations

import pytest

from app import config
from app.obs import store


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    db_path = tmp_path / "calls.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    assert store.init_db() is True
    yield store
    store.reset_engine()


@pytest.fixture
def judge_enabled(monkeypatch):
    monkeypatch.setattr(config, "OBS_JUDGE_ENABLED", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    return config


@pytest.fixture
def alerts_on(monkeypatch):
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")  # synthetic
    monkeypatch.setattr(config, "OBS_SLACK_WEBHOOK", "")
    return config


@pytest.fixture
def fixture_call() -> dict:
    """A stored-call dict as get_call() would return it."""
    return {
        "call_sid": "CAjudge0001",
        "clinic_id": "theorem",
        "reason": "booked",
        "booking_confirmed": True,
        "transfer_attempted": False,
        "duration_s": 180,
        "turn_count": 4,
        "transcript": [
            {"role": "assistant", "text": "Hello, this is Susie at the clinic."},
            {"role": "user", "text": "I'd like to book a physio appointment."},
            {"role": "assistant", "text": "Of course — what day suits you?"},
            {"role": "user", "text": "Monday please."},
        ],
    }


@pytest.fixture
def fixture_record() -> dict:
    """A CallLogger-shaped record for seeding the store via capture_call."""
    return {
        "call_sid": "CAjudge0001",
        "clinic_id": "theorem",
        "start_utc": "2026-07-06T10:00:00+00:00",
        "end_utc": "2026-07-06T10:03:00+00:00",
        "duration_s": 180,
        "success": True,
        "reason": "booked",
        "caller_number": "+440000000000",
        "dialled_number": "+441111111111",
        "final_state": "complete",
        "collected": {},
        "booking_confirmed": True,
        "acuity_booking_id": "ACU1",
        "transfer_attempted": False,
        "graceful_exit": False,
        "total_retries": 0,
        "slot_retry_counts": {},
        "turn_count": 4,
        "tone": "neutral",
    }


@pytest.fixture
def fixture_turns() -> list:
    return [
        {"role": "assistant", "text": "Hello, this is Susie at the clinic."},
        {"role": "user", "text": "I'd like to book a physio appointment."},
        {"role": "assistant", "text": "Of course — what day suits you?"},
        {"role": "user", "text": "Monday please."},
    ]
