"""Latency survives the call — the [LAT] figures reach the durable call row.

Until this change the per-turn timings existed only as `[LAT]` log lines. The obs
store held hundreds of calls and not one latency figure, so the only way to get a
baseline was to export a Render log window — and at roughly a dozen calls a day
against a retention measured in hours, that export can never hold more than the
handful of calls inside the window. Two sessions produced a largest sample of 29
turns across 3 calls: directional, and permanently un-growable.

What these tests pin, in the order the data moves:

  1. OFF is still OFF. LATENCY_TIMING defaults false; with it off nothing is
     allocated, nothing is buffered, and the record's latency field is None. The
     obs column stays NULL rather than filling with zeros.
  2. emit() files the turn under its call_sid, and only under its call_sid.
  3. The drain happens ONCE and is cached. _build_record runs three times per
     teardown (flush's JSONL write, then connection.py's obs capture, then alert
     routing). A drain per call would give the JSONL the turns and leave the obs
     row — the row this whole change exists to populate — empty.
  4. The row round-trips through the store with the turns intact.
  5. -1 never enters a percentile. A superseded or abandoned turn reports -1 for
     every stage it never reached; averaging that in is how a latency number
     silently becomes a lie, and it would bias every figure DOWNWARD, i.e.
     towards saying the system is fine.
  6. Both buffer caps hold, because this runs in a live-call process and a call
     that dies before teardown never drains.
"""
from __future__ import annotations

import importlib
import logging

import pytest

from app import config
from app.call_logger import CallLogger
from app.media_streams import latency_timing as lat
from app.obs import store


@pytest.fixture(autouse=True)
def _clean_buffer():
    """No test may inherit another's buffered turns."""
    lat._pending.clear()
    yield
    lat._pending.clear()


@pytest.fixture
def timing_on(monkeypatch):
    """LATENCY_TIMING on. Patched on the module, which is where new_turn reads it."""
    monkeypatch.setattr(lat, "LATENCY_TIMING", True)
    return lat


@pytest.fixture
def timing_off(monkeypatch):
    """LATENCY_TIMING off — the shipped default, asserted separately below.

    Patched explicitly rather than relied upon, because conftest.py calls
    load_dotenv(override=True) and a developer's .env could turn it on.
    """
    monkeypatch.setattr(lat, "LATENCY_TIMING", False)
    return lat


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    """A migrated, capture-enabled store on a throwaway SQLite file.

    Mirrors tests/capture/conftest.py; duplicated because that conftest does not
    reach tests/regression, and this test belongs with the behaviour it pins.
    """
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'calls.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    assert store.init_db() is True
    yield store
    store.reset_engine()


def _finish(t, *, ttfa_ms: int) -> None:
    """Drive one turn to a completed state with a chosen perceived TTFA.

    Stamps are anchored at t0 = 0.0 rather than at a realistic monotonic clock
    value on purpose: emit() truncates with int(), so a large t0 lets binary
    float error turn 1.8 s into 1799 ms and the test asserts against noise
    instead of against the code.
    """
    t.t0 = 0.0
    t.t_dispatch = 0.010
    t.t1 = 0.100
    t.t2 = 0.200
    t.content_t3 = 0.300
    t.t4 = ttfa_ms / 1000.0
    t.content_t4 = t.t4
    t.emit()


# ---------------------------------------------------------------------------
# 1 · OFF stays off
# ---------------------------------------------------------------------------

def test_latency_timing_ships_off(monkeypatch):
    """With the env var absent the flag parses False.

    Every stamp site in connection.py relies on new_turn returning None for its
    no-op, so this default is what keeps the change byte-behaviour-identical for
    a clinic that has not opted in.
    """
    monkeypatch.delenv("LATENCY_TIMING", raising=False)
    assert importlib.reload(lat).LATENCY_TIMING is False


def test_off_buffers_nothing(timing_off):
    """Off: no record, no buffer, no latency on the call row."""
    assert timing_off.new_turn(t0=0.0, call_sid="CAoff") is None
    assert lat._pending == {}
    assert lat.drain_call("CAoff") == []

    logger = CallLogger("CAoff", {})
    logger.complete(success=False, reason="caller_hung_up")
    assert logger.build_record()["latency"] is None


# ---------------------------------------------------------------------------
# 2 · a turn is filed under its own call
# ---------------------------------------------------------------------------

def test_emitted_turns_are_filed_under_their_call_sid(timing_on):
    for sid, ttfa in (("CAone", 1200), ("CAtwo", 900), ("CAone", 1800)):
        _finish(timing_on.new_turn(t0=0.0, call_sid=sid), ttfa_ms=ttfa)

    assert [t["ttfa_ms"] for t in timing_on._pending["CAone"]] == [1200, 1800]
    assert [t["ttfa_ms"] for t in timing_on._pending["CAtwo"]] == [900]

    # Drain is a pop: the second read of the same call is empty, which is why
    # CallLogger must cache rather than re-drain.
    assert len(timing_on.drain_call("CAone")) == 2
    assert timing_on.drain_call("CAone") == []


def test_a_turn_without_a_call_sid_is_logged_but_not_buffered(timing_on, caplog):
    """The log line is unconditional; only the durable filing needs the sid."""
    with caplog.at_level(logging.INFO, logger="susie.latency"):
        _finish(timing_on.new_turn(t0=0.0, call_sid=None), ttfa_ms=1100)
    assert any("[LAT] turn_seq=" in r.getMessage() for r in caplog.records)
    assert timing_on._pending == {}


def test_the_lat_line_still_carries_every_field(timing_on, caplog):
    """emit() formats FROM as_record() now; the line itself must not have moved.

    lat_parse.py parses these lines by key, and two sessions of baseline work are
    expressed in that parser. A renamed or dropped key breaks the offline tool
    silently — it would just report fewer turns.
    """
    with caplog.at_level(logging.INFO, logger="susie.latency"):
        _finish(timing_on.new_turn(t0=0.0, call_sid="CAfields"), ttfa_ms=1400)
    line = next(r.getMessage() for r in caplog.records if "[LAT] " in r.getMessage())
    for key in (
        "turn_seq", "path", "outcome", "ttfa_ms", "content_ttfa_ms",
        "ep_dispatch_ms", "llm_ttft_ms", "chunk_gate_ms", "tts_first_byte_ms",
        "audio_wire_ms", "flags", "model", "stt_model", "eot_confident",
        "capture_phase", "endpoint_wait_ms",
    ):
        assert f"{key}=" in line, f"[LAT] line lost the {key} field"


# ---------------------------------------------------------------------------
# 3 · drained once, cached — the trap
# ---------------------------------------------------------------------------

def test_every_build_record_call_sees_the_same_latency(timing_on):
    """Three consumers per teardown; the drain is a pop. Cache or lose the row.

    Order at teardown (connection.py): flush() builds the record for the JSONL,
    then build_record() again for obs capture, then a third time for alert
    routing. Without the cache, obs — the store that made this work necessary —
    would be the one consumer that got nothing.
    """
    for ttfa in (1000, 2000, 3000):
        _finish(timing_on.new_turn(t0=0.0, call_sid="CAcache"), ttfa_ms=ttfa)

    logger = CallLogger("CAcache", {})
    logger.complete(success=True, reason="booked")

    first = logger.build_record()["latency"]
    second = logger.build_record()["latency"]
    third = logger.build_record()["latency"]

    assert first is not None
    assert first == second == third
    assert [t["ttfa_ms"] for t in first["turns"]] == [1000, 2000, 3000]
    assert first["summary"]["turns_measured"] == 3


def test_tool_round_trips_are_stored_beside_the_turns(timing_on):
    """How long the provider actually took, on the same row as the timings.

    Nothing measured this before: a tool round-trip is absorbed silently into
    llm_ttft_ms, so "was 1800ms the right moment to start speaking?" could not be
    answered from data — which is how a hold phrase came to fire on 175 turns
    that never ran a tool at all. The durations live on the session because a
    tool belongs to a turn but is not one of the turn's audio stages.
    """
    session = {"lat_tools": [
        {"tool": "check_availability", "ms": 1840},
        {"tool": "book_appointment",   "ms": 620},
    ]}
    _finish(timing_on.new_turn(t0=0.0, call_sid="CAtools"), ttfa_ms=900)

    logger = CallLogger("CAtools", session)
    logger.complete(success=True, reason="booked")
    block = logger.build_record()["latency"]

    assert [t["tool"] for t in block["tools"]] == [
        "check_availability", "book_appointment",
    ]
    assert block["tools"][0]["ms"] == 1840
    # Caching is load-bearing here too: build_record runs three times a teardown.
    assert logger.build_record()["latency"] == block


def test_a_call_that_ran_a_tool_but_measured_no_turn_still_records(timing_on):
    """Tools alone are enough to keep the row.

    A turn that dead-ends produces no measured turn, and that is exactly the
    call worth keeping: it ran a tool, spoke a filler, and delivered nothing.
    Requiring turns before storing would drop the evidence.
    """
    session = {"lat_tools": [{"tool": "check_availability", "ms": 2100}]}
    logger = CallLogger("CAtoolsonly", session)
    logger.complete(success=False, reason="caller_hung_up")
    block = logger.build_record()["latency"]
    assert block is not None
    assert block["tools"][0]["ms"] == 2100
    assert block["turns"] == []


def test_a_call_with_no_measured_turns_records_null(timing_on):
    """Nothing measured is NULL, not an empty structure — same as `screening`."""
    logger = CallLogger("CAquiet", {})
    logger.complete(success=False, reason="caller_hung_up")
    assert logger.build_record()["latency"] is None


def test_the_drain_never_breaks_teardown(timing_on, monkeypatch):
    """An observability layer must not be able to fail a call's teardown."""
    def _boom(_sid):
        raise RuntimeError("buffer exploded")

    monkeypatch.setattr(lat, "drain_call", _boom)
    logger = CallLogger("CAboom", {})
    logger.complete(success=True, reason="booked")
    assert logger.build_record()["latency"] is None


# ---------------------------------------------------------------------------
# 4 · it reaches the durable row
# ---------------------------------------------------------------------------

def test_latency_round_trips_through_the_store(sqlite_store, timing_on):
    for ttfa in (1100, 2400):
        _finish(timing_on.new_turn(t0=0.0, call_sid="CAstore01"), ttfa_ms=ttfa)

    logger = CallLogger("CAstore01", {})
    logger.complete(success=True, reason="booked")
    record = logger.build_record()

    assert sqlite_store.capture_call(record, []) is True
    got = sqlite_store.get_call("CAstore01")

    assert got["latency"] is not None
    assert [t["ttfa_ms"] for t in got["latency"]["turns"]] == [1100, 2400]
    # 1100 is inside the 1.5 s bar, 2400 is outside it.
    assert got["latency"]["summary"]["over_bar"] == 1


def test_migrate_adds_the_column_to_an_existing_table(sqlite_store):
    """Re-running the migration on a live table adds `latency` idempotently.

    create_all(checkfirst=True) creates missing TABLES, not missing columns —
    the existing store has a `calls` table already, so the column arrives only
    through _ADDED_COLUMNS.
    """
    from sqlalchemy import inspect

    assert "latency" in sqlite_store._ADDED_COLUMNS
    assert sqlite_store.init_db() is True  # second run must not raise
    cols = {c["name"] for c in inspect(sqlite_store._get_engine()).get_columns("calls")}
    assert "latency" in cols


# ---------------------------------------------------------------------------
# 5 · -1 never enters a percentile
# ---------------------------------------------------------------------------

def test_unreached_stages_are_excluded_from_the_summary(timing_on):
    """A superseded turn reports -1; counting it would bias every figure down."""
    _finish(timing_on.new_turn(t0=0.0, call_sid="CAmix"), ttfa_ms=2000)

    superseded = timing_on.new_turn(t0=0.0, call_sid="CAmix")
    superseded.t0 = 1000.0
    superseded.outcome = "superseded"
    superseded.emit()  # never reached t4 -> every delta is -1

    turns = timing_on.drain_call("CAmix")
    assert [t["ttfa_ms"] for t in turns] == [2000, -1]

    summary = timing_on.summarise(turns)
    assert summary["turns_logged"] == 2      # the superseded turn is kept…
    assert summary["turns_measured"] == 1    # …but not measured
    assert summary["ttfa_p50_ms"] == 2000    # not 999.5
    assert summary["over_bar"] == 1


def test_percentiles_match_the_offline_parser():
    """Same method as lat_parse.py, so DB and log export give the same number."""
    values = [100, 200, 300, 400]
    assert lat._percentile(values, 50) == 250.0
    assert lat._percentile([42], 95) == 42.0
    assert lat._percentile([], 50) is None


# ---------------------------------------------------------------------------
# 6 · the buffer is bounded on both axes
# ---------------------------------------------------------------------------

def test_a_runaway_call_cannot_grow_the_buffer_without_limit(timing_on, monkeypatch):
    monkeypatch.setattr(timing_on, "_MAX_TURNS_PER_CALL", 3)
    for ttfa in (1000, 1100, 1200, 1300, 1400):
        _finish(timing_on.new_turn(t0=0.0, call_sid="CAlong"), ttfa_ms=ttfa)

    kept = timing_on.drain_call("CAlong")
    # FIRST turns kept: the opening turns carry the greeting and the capture
    # steps, which is where the latency questions actually are.
    assert [t["ttfa_ms"] for t in kept] == [1000, 1100, 1200]


def test_an_undrained_call_is_eventually_evicted(timing_on, monkeypatch):
    """A call that dies before teardown never drains; the dict must not leak."""
    monkeypatch.setattr(timing_on, "_MAX_PENDING_CALLS", 2)
    for sid in ("CAa", "CAb", "CAc"):
        _finish(timing_on.new_turn(t0=0.0, call_sid=sid), ttfa_ms=1000)

    assert set(timing_on._pending) == {"CAb", "CAc"}
    assert timing_on.drain_call("CAa") == []


# ---------------------------------------------------------------------------
# 7 · the stored turns read back through the offline parser
# ---------------------------------------------------------------------------

def test_stored_turns_round_trip_through_lat_parse(timing_on):
    """DB -> [LAT] line -> lat_parse must be lossless.

    scripts/lat_baseline.py renders stored turns back into [LAT] lines and hands
    them to lat_parse.py, so the table comes from the same parser and the same
    percentile method as every earlier baseline. If the two ever disagree about a
    field name, the parser does not error — it just silently reports fewer turns,
    which is the failure mode this asserts against.
    """
    import lat_parse
    from scripts.lat_baseline import _lat_lines

    _finish(timing_on.new_turn(t0=0.0, call_sid="CAparse"), ttfa_ms=1700)
    turns = timing_on.drain_call("CAparse")

    rows = [{"latency": {"turns": turns}, "clinic_id": "vital_edge"}]
    lines, calls, skipped = _lat_lines(rows)
    assert (calls, skipped) == (1, 0)

    parsed = [lat_parse.parse_line(line) for line in lines]
    assert all(p is not None for p in parsed), "lat_parse could not read our line"
    assert parsed[0]["ttfa_ms"] == 1700
    assert parsed[0]["path"] == "llm"        # lat_parse's llm pool must accept it
    assert parsed[0]["outcome"] == "completed"
    # Every field the parser types as an int must have survived as an int.
    for field in lat_parse.INT_FIELDS:
        assert isinstance(parsed[0][field], int), f"{field} did not parse as int"


def test_a_call_with_no_stored_latency_is_skipped_not_counted_as_fast():
    """NULL means 'not measured'. Counting it as a fast call would flatter every
    figure — the column is NULL for every call predating it and for every service
    running with LATENCY_TIMING off."""
    from scripts.lat_baseline import _lat_lines

    lines, calls, skipped = _lat_lines([{"latency": None}, {"latency": {"turns": []}}])
    assert (lines, calls, skipped) == ([], 0, 2)
