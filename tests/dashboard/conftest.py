"""Fixtures for the dashboard/weekly CLI tests — a seeded throwaway SQLite store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.obs import store


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def seeded_store(tmp_path, monkeypatch):
    db_path = tmp_path / "calls.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    assert store.init_db() is True

    now = datetime.now(timezone.utc)

    def seed(sid, clinic, days_ago, booked, score, tags):
        rec = {
            "call_sid": sid, "clinic_id": clinic,
            "start_utc": _iso(now - timedelta(days=days_ago)),
            "end_utc": _iso(now - timedelta(days=days_ago)),
            "duration_s": 120, "success": booked, "reason": "booked" if booked else "caller_hung_up",
            "caller_number": "+440000000000", "dialled_number": "+441111111111",
            "final_state": "complete", "collected": {}, "booking_confirmed": booked,
            "acuity_booking_id": None, "transfer_attempted": False, "graceful_exit": False,
            "total_retries": 0, "slot_retry_counts": {}, "turn_count": 4, "tone": "neutral",
        }
        store.capture_call(rec, [{"role": "user", "text": "hi"}])
        if score is not None:
            store.save_judgement(sid, {
                "outcome": "booked" if booked else "abandoned",
                "quality_score": score, "intent_resolved": booked,
                "failure_tags": tags or [], "evidence": "x", "rubric_version": "v1",
            })

    # This week
    seed("CA1", "theorem", 1, True, 5, [])
    seed("CA2", "theorem", 2, False, 1, ["dead_end", "wrong_info"])
    seed("CA3", "jv", 3, True, 3, ["loop"])
    seed("CA4", "theorem", 4, False, None, None)   # unscored
    # Older (outside a 7-day window, inside 8-week window)
    seed("CA5", "theorem", 20, True, 4, [])

    yield store
    store.reset_engine()
