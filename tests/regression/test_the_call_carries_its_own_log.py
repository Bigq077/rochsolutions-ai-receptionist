"""The call carries its own log -- app/obs/call_log.py.

Owner decision 12 Sep 2026: after three demo calls whose defects (a slot
pinned before it was heard, a check-in read as acceptance, a guard retracting
its own offer, 15 s of dead air) could only be diagnosed from a pasted Render
log slice, every log line emitted while a call is open is written to the
`call_logs` table, redacted, on a timer and at teardown.

Pinned here:
  1. two calls running at once never see each other's lines;
  2. lines logged BEFORE the SID is known are attributed once it is;
  3. a task and a worker thread spawned inside the call are captured;
  4. redaction removes phone numbers and the collected name but keeps the
     Twilio SIDs searchable;
  5. periodic and final flushes write real rows (SQLite), and the final one
     marks `complete`;
  6. the byte cap truncates with a marker rather than growing without bound;
  7. a DB failure is swallowed and the buffer survives for the next flush;
  8. with capture off, nothing is buffered at all.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app import config
from app.obs import call_log, store


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'obs.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "OBS_LOG_LINES_ENABLED", True)
    store.reset_engine()
    call_log._purged_this_process = False
    yield url
    store.reset_engine()


@pytest.fixture
def handler():
    h = call_log.install()
    root = logging.getLogger()
    prev = root.level
    root.setLevel(logging.DEBUG)
    yield h
    root.setLevel(prev)
    call_log.uninstall()


def _lines(ctx):
    return ctx.snapshot()[0]


# ── 1-3: attribution ─────────────────────────────────────────────────────────

async def test_two_concurrent_calls_do_not_share_a_buffer(sqlite_store, handler):
    log = logging.getLogger("test.calllog")
    seen = {}

    async def call(sid: str, n: int):
        ctx = call_log.open_context()
        assert ctx is not None
        log.info("before start %s", sid)          # SID not yet known
        await asyncio.sleep(0)
        call_log.bind_call(ctx, sid, clinic_id="northgate")

        async def child():
            log.info("child task %s", sid)

        def worker():
            log.info("worker thread %s", sid)

        await asyncio.gather(
            asyncio.create_task(child()),
            asyncio.to_thread(worker),
        )
        for i in range(n):
            log.info("turn %d %s", i, sid)
            await asyncio.sleep(0)
        seen[sid] = ctx

    await asyncio.gather(call("CA" + "a" * 32, 3), call("CA" + "b" * 32, 5))

    a, b = seen["CA" + "a" * 32], seen["CA" + "b" * 32]
    a_text, b_text = "\n".join(_lines(a)), "\n".join(_lines(b))
    assert "CA" + "b" * 32 not in a_text and "CA" + "a" * 32 not in b_text
    # before-start line, child task line and worker-thread line all attributed
    for ctx, sid in ((a, "CA" + "a" * 32), (b, "CA" + "b" * 32)):
        text = "\n".join(_lines(ctx))
        assert f"before start {sid}" in text
        assert f"child task {sid}" in text
        assert f"worker thread {sid}" in text
    assert sum(1 for l in _lines(b) if "turn " in l) == 5


async def test_a_record_outside_any_call_is_not_buffered(sqlite_store, handler):
    log = logging.getLogger("test.calllog")
    assert call_log.current() is None
    log.info("startup line, no call")     # must not raise, must not go anywhere


# ── 4: redaction ─────────────────────────────────────────────────────────────

def test_redaction_strips_phones_and_names_but_keeps_sids():
    sid = "CA5c69c585325d9004d5f859f76b7d67a6"
    stream = "MZb466e9c6567edc7b256bdbdd9bea9c6d"
    lines = [
        f"[ms_conn] caller number from Twilio: +447502211207 sid={sid}",
        f"[ms_conn v3] stored calling number 07502211207 stream={stream}",
        "[ms_conn v3] name persisted (normal path): 'Quentin Rock'",
        "[ms_conn] drop-off callback ping queued to ***1207 (lead='Quentin Rock')",
    ]
    out = call_log.redact_lines(lines, names=["Quentin Rock"])
    assert sid in out and stream in out            # searchable by SID
    assert "07502211207" not in out and "+447502211207" not in out
    assert "Quentin" not in out and "Rock" not in out
    assert "[PHONE]" in out and "[NAME]" in out


# ── 5: flushes write rows ────────────────────────────────────────────────────

async def test_periodic_then_final_flush_write_the_row(sqlite_store, handler, monkeypatch):
    monkeypatch.setattr(config, "OBS_LOG_FLUSH_SEC", 0.05)
    log = logging.getLogger("test.calllog")
    sid = "CA" + "c" * 32

    ctx = call_log.open_context()
    call_log.bind_call(ctx, sid, clinic_id="northgate", build_sha="deadbeef")
    flusher = asyncio.create_task(call_log.periodic_flush(ctx))
    log.info("first turn for %s ringing 07502211207", sid)
    await asyncio.sleep(0.2)                       # at least one periodic flush

    row = store.get_call_log(sid)
    assert row is not None and row["complete"] is False
    assert "first turn" in row["lines"] and "07502211207" not in row["lines"]
    assert row["clinic_id"] == "northgate" and row["build_sha"] == "deadbeef"

    log.info("teardown line for %s", sid)
    flusher.cancel()
    await asyncio.gather(flusher, return_exceptions=True)
    assert await call_log.flush(ctx, complete=True, names=["Quentin Rock"])

    row = store.get_call_log(sid)
    assert row["complete"] is True
    assert "teardown line" in row["lines"]
    assert row["line_count"] == len(_lines(ctx))
    # closed: a straggler after the final flush is dropped, not written twice
    log.info("straggler for %s", sid)
    assert "straggler" not in "\n".join(_lines(ctx))


# ── 6: the cap ───────────────────────────────────────────────────────────────

async def test_the_byte_cap_truncates_with_a_marker(sqlite_store, handler):
    log = logging.getLogger("test.calllog")
    ctx = call_log.open_context(max_bytes=600)
    call_log.bind_call(ctx, "CA" + "d" * 32)
    for i in range(50):
        log.info("line %02d " + "x" * 40, i)
    lines, byte_count, truncated = ctx.snapshot()
    assert truncated is True
    assert byte_count <= 600
    assert any("TRUNCATED" in l for l in lines)
    assert ctx.dropped > 0
    assert sum(1 for l in lines if "TRUNCATED" in l) == 1


# ── 7: a DB failure never loses the buffer ───────────────────────────────────

async def test_a_failed_flush_keeps_the_buffer(sqlite_store, handler, monkeypatch):
    log = logging.getLogger("test.calllog")
    ctx = call_log.open_context()
    call_log.bind_call(ctx, "CA" + "e" * 32)
    log.info("kept line")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "upsert_call_log", boom)
    assert await call_log.flush(ctx, complete=False) is False
    assert any("kept line" in l for l in _lines(ctx))
    monkeypatch.undo()
    store.reset_engine()
    monkeypatch.setattr(config, "DATABASE_URL", sqlite_store)
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    assert await call_log.flush(ctx, complete=True) is True


# ── 8: off means off ─────────────────────────────────────────────────────────

async def test_capture_off_buffers_nothing(monkeypatch, handler):
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", False)
    store.reset_engine()
    assert call_log.open_context() is None
    assert call_log.current() is None
    logging.getLogger("test.calllog").info("nobody is listening")


def test_retention_purge_removes_only_old_rows(sqlite_store):
    from datetime import datetime, timedelta, timezone
    from app.obs.models import CallLog

    store.upsert_call_log("CA" + "f" * 32, lines="new", line_count=1, byte_count=3,
                          truncated=False, complete=True)
    store.upsert_call_log("CA" + "0" * 32, lines="old", line_count=1, byte_count=3,
                          truncated=False, complete=True)
    engine = store._get_engine()
    with store._Session() as s:
        row = s.get(CallLog, "CA" + "0" * 32)
        row.updated_at = datetime.now(timezone.utc) - timedelta(days=120)
        s.commit()
    assert store.purge_call_logs(90) == 1
    assert store.get_call_log("CA" + "f" * 32) is not None
    assert store.get_call_log("CA" + "0" * 32) is None


def test_a_live_store_that_predates_call_logs_gets_the_table(tmp_path, monkeypatch):
    """The real case, which the fresh-SQLite fixture above never sees: `calls`
    already exists, `call_logs` does not. The first call on 05acf306 wrote
    its `calls` row and every flush failed with "relation call_logs does not
    exist" (demo line, CAb0d38061, 12 Sep 2026), because create_all was only
    run when `calls` was missing."""
    from sqlalchemy import create_engine, inspect
    from app.obs.models import Call

    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = create_engine(url, future=True)
    Call.__table__.create(eng)                       # an old store: calls only
    assert inspect(eng).get_table_names() == ["calls"]
    eng.dispose()

    monkeypatch.setattr(config, "DATABASE_URL", url)
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    try:
        engine = store._get_engine()               # the engine build is the migration
        assert "call_logs" in inspect(engine).get_table_names()
        assert store.upsert_call_log("CA" + "1" * 32, lines="x", line_count=1,
                                     byte_count=1, truncated=False, complete=True)
    finally:
        store.reset_engine()
