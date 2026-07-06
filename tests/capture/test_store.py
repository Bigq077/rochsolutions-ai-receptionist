"""Unit tests for app.obs.store — the durable call store."""
from __future__ import annotations

from sqlalchemy import text

from app import config
from app.obs import store


def _row_count(store_mod) -> int:
    engine = store_mod._get_engine()
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM calls")).scalar_one()


def test_capture_round_trip(sqlite_store, fixture_record, fixture_turns):
    """A captured call round-trips with its transcript and fields intact."""
    assert sqlite_store.capture_call(fixture_record, fixture_turns) is True

    got = sqlite_store.get_call("CAfixture0001")
    assert got is not None
    assert got["call_sid"] == "CAfixture0001"
    assert got["clinic_id"] == "theorem"
    assert got["reason"] == "booked"
    assert got["success"] is True
    assert got["booking_confirmed"] is True
    assert got["final_state"] == "complete"
    assert got["turn_count"] == 4
    assert got["slot_retry_counts"] == {"phone": 1}
    assert got["collected"]["chosen_day"] == "Monday"
    # Transcript preserved verbatim and in order.
    assert got["transcript"] == fixture_turns
    assert got["created_at"] is not None


def test_capture_is_idempotent(sqlite_store, fixture_record, fixture_turns):
    """Re-capturing the same call_sid updates the row, never duplicates it."""
    assert sqlite_store.capture_call(fixture_record, fixture_turns) is True

    updated = dict(fixture_record)
    updated["reason"] = "transferred"
    updated["success"] = False
    assert sqlite_store.capture_call(updated, fixture_turns) is True

    assert _row_count(sqlite_store) == 1
    got = sqlite_store.get_call("CAfixture0001")
    assert got["reason"] == "transferred"
    assert got["success"] is False


def test_capture_without_call_sid_is_skipped(sqlite_store, fixture_turns):
    assert sqlite_store.capture_call({"clinic_id": "theorem"}, fixture_turns) is False
    assert _row_count(sqlite_store) == 0


def test_get_call_missing_returns_none(sqlite_store):
    assert sqlite_store.get_call("CAdoesnotexist") is None


def test_capture_when_no_database_url_is_noop(monkeypatch, fixture_record, fixture_turns):
    """With no DATABASE_URL there is no engine — capture skips, never errors."""
    monkeypatch.setattr(config, "DATABASE_URL", "")
    store.reset_engine()
    try:
        assert store.is_enabled() is False
        assert store.capture_call(fixture_record, fixture_turns) is False
        assert store.get_call("CAfixture0001") is None
    finally:
        store.reset_engine()


async def test_capture_async_writes_when_enabled(sqlite_store, fixture_record, fixture_turns):
    assert await sqlite_store.capture_call_async(fixture_record, fixture_turns) is True
    assert sqlite_store.get_call("CAfixture0001") is not None


async def test_capture_async_noop_when_disabled(monkeypatch, fixture_record, fixture_turns):
    """Flag OFF ⇒ async capture returns immediately and writes nothing."""
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", False)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    store.reset_engine()
    try:
        assert await store.capture_call_async(fixture_record, fixture_turns) is False
    finally:
        store.reset_engine()
