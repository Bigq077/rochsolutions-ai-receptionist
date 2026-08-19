"""Unit tests for app.obs.store — the durable call store."""
from __future__ import annotations

import importlib

from sqlalchemy import text

from app import config
from app.obs import store


def test_obs_database_url_takes_precedence(monkeypatch):
    """config.DATABASE_URL prefers OBS_DATABASE_URL over a pre-existing DATABASE_URL."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://existing/other")
    monkeypatch.setenv("OBS_DATABASE_URL", "postgresql+psycopg2://obs/store")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.DATABASE_URL == "postgresql+psycopg2://obs/store"
    finally:
        # Restore the module to normal env-free state for other tests.
        monkeypatch.delenv("OBS_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(config)


def test_falls_back_to_database_url_when_obs_unset(monkeypatch):
    monkeypatch.delenv("OBS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://legacy/db")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.DATABASE_URL == "postgresql+psycopg2://legacy/db"
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(config)


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


# ---------------------------------------------------------------------------
# Deploy safety: a store missing a column must heal, not silently stop capturing
# ---------------------------------------------------------------------------

def test_an_unmigrated_store_heals_itself_on_first_use(tmp_path, monkeypatch,
                                                       fixture_record, fixture_turns):
    """A missing column used to cost the WHOLE row, not just that column.

    session.merge SELECTs every mapped column, so one column present in the model
    and absent in the database fails the entire write. capture_call raised,
    capture_call_async swallowed it and returned False, and obs capture stopped
    for that service — no transcript, no screening, no guard counters, nothing for
    the judge to score — with one log line per call as the only trace.

    Reproduced before the fix: capture_call raised OperationalError and the table
    ended with zero rows.

    It could happen to any column added since the Phase 1 schema, and it depended
    on an operator running the migration before the code that needs it deploys.
    Render's autoDeploy does not wait for that. The schema is now checked when the
    engine is built, so the ordering no longer matters.
    """
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'calls.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    try:
        assert store.init_db() is True

        # Roll the store back to a schema that predates the `latency` column.
        with store._get_engine().begin() as conn:
            conn.execute(text("ALTER TABLE calls DROP COLUMN latency"))
        store.reset_engine()  # a fresh process, engine rebuilt on first use

        record = dict(fixture_record)
        record["latency"] = {"summary": {"turns_measured": 1},
                             "turns": [{"turn_seq": 1, "ttfa_ms": 1200}]}

        assert store.capture_call(record, fixture_turns) is True
        got = store.get_call("CAfixture0001")
        # The whole row, not just the new column — that is what was being lost.
        assert got is not None
        assert got["transcript"] == fixture_turns
        assert got["latency"]["turns"][0]["ttfa_ms"] == 1200
    finally:
        store.reset_engine()


def test_the_schema_check_never_raises(tmp_path, monkeypatch, caplog):
    """A store that cannot be migrated must fail at the write, not at engine build.

    Failing at engine creation would take down every READ path too — get_call,
    list_calls, the dashboard — for what is at worst one unwritable column.
    """
    def _boom(_engine):
        raise RuntimeError("no DDL rights")

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'calls.db'}")
    monkeypatch.setattr(store, "_ensure_new_columns", _boom)
    store.reset_engine()
    try:
        assert store.init_db() is True
        with caplog.at_level("ERROR"):
            store.reset_engine()
            assert store._get_engine() is not None   # must not raise
    finally:
        monkeypatch.undo()
        store.reset_engine()


def test_a_brand_new_store_is_created_without_migrate(tmp_path, monkeypatch,
                                                      fixture_record, fixture_turns):
    """First use against an empty database creates the table from the model."""
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'fresh.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    try:
        # No init_db() call at all — the engine build is the only schema step.
        assert store.capture_call(fixture_record, fixture_turns) is True
        assert store.get_call("CAfixture0001") is not None
    finally:
        store.reset_engine()
