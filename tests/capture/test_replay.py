"""Unit tests for app.obs.replay — the offline replay harness."""
from __future__ import annotations

from app import config
from app.obs import replay, store


def test_load_call_returns_stored_call(sqlite_store, fixture_record, fixture_turns):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    call = replay.load_call("CAfixture0001")
    assert call is not None
    assert call["transcript"] == fixture_turns


def test_format_trace_renders_header_and_ordered_turns(sqlite_store, fixture_record, fixture_turns):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    call = replay.load_call("CAfixture0001")
    trace = replay.format_trace(call)

    # Header facts present.
    assert "CAfixture0001" in trace
    assert "reason=booked" in trace
    assert "theorem" in trace

    # Every turn rendered, in stored order.
    positions = [trace.index(t["text"]) for t in fixture_turns]
    assert positions == sorted(positions)
    assert "ASSISTANT" in trace and "USER" in trace


def test_main_prints_trace_for_stored_call(sqlite_store, fixture_record, fixture_turns, capsys):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    rc = replay.main(["CAfixture0001"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Monday please." in out


def test_main_json_mode(sqlite_store, fixture_record, fixture_turns, capsys):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    rc = replay.main(["CAfixture0001", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"call_sid": "CAfixture0001"' in out


def test_main_missing_call_returns_1(sqlite_store, capsys):
    rc = replay.main(["CAnope"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no stored call" in err


def test_main_without_database_url_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    store.reset_engine()
    try:
        rc = replay.main(["CAfixture0001"])
        err = capsys.readouterr().err
        assert rc == 2
        assert "not set" in err and "DATABASE_URL" in err
    finally:
        store.reset_engine()
