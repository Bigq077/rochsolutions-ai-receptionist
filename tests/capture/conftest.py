"""
Fixtures for the durable-capture tests.

Every test runs against a throwaway SQLite database (generic SQLAlchemy JSON
columns mean the same model works on SQLite here and Postgres in production).
The transcript fixture is fully synthetic — no real names or phone numbers ever
enter these committed tests (CLAUDE.md rule 6 / spec §7).
"""
from __future__ import annotations

import pytest

from app import config
from app.obs import store


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    """A migrated, capture-enabled store backed by a temp SQLite file."""
    db_path = tmp_path / "calls.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    assert store.init_db() is True
    yield store
    store.reset_engine()


@pytest.fixture
def fixture_record() -> dict:
    """A CallLogger-shaped record with synthetic (non-PII) values."""
    return {
        "call_sid": "CAfixture0001",
        "clinic_id": "theorem",
        "start_utc": "2026-07-06T10:00:00+00:00",
        "end_utc": "2026-07-06T10:03:20+00:00",
        "duration_s": 200,
        "success": True,
        "reason": "booked",
        "caller_number": "+440000000000",   # synthetic
        "dialled_number": "+441111111111",  # synthetic
        "final_state": "complete",
        "collected": {
            "name": "Test Caller",
            "phone": "+440000000000",
            "reason": "back pain",
            "chosen_day": "Monday",
            "selected_slot": "10:00",
        },
        "booking_confirmed": True,
        "acuity_booking_id": "ACU999",
        "transfer_attempted": False,
        "graceful_exit": False,
        "total_retries": 1,
        "slot_retry_counts": {"phone": 1},
        "turn_count": 4,
        "tone": "neutral",
    }


@pytest.fixture
def fixture_turns() -> list:
    """Ordered synthetic transcript."""
    return [
        {"role": "assistant", "text": "Hello, this is Susie at the clinic."},
        {"role": "user", "text": "I'd like to book an appointment."},
        {"role": "assistant", "text": "Of course — what day works for you?"},
        {"role": "user", "text": "Monday please."},
    ]
