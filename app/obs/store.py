"""
app/obs/store.py
----------------
Durable call store (spec §5.1).

Reuses the existing `sqlalchemy` + `psycopg2-binary` dependencies — no new infra
stack (CLAUDE.md rule 7). The write is a synchronous SQLAlchemy `session.merge`
(insert-or-update by primary key `call_sid`, so it is idempotent) executed off
the event loop via `asyncio.to_thread`, so it never blocks the live pipeline.

Everything is gated on config.OBS_CAPTURE_ENABLED + a non-empty DATABASE_URL:
with either missing, `capture_call_async` returns immediately and no engine is
ever created. Default production behaviour is therefore unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.obs.models import Base, Call

_log = logging.getLogger(__name__)

# Lazily-created engine/session factory, keyed on the URL they were built for so
# a config change (mainly in tests) rebuilds them cleanly.
_engine: Optional[Engine] = None
_engine_url: Optional[str] = None
_Session: Optional[sessionmaker] = None


# ---------------------------------------------------------------------------
# Engine / schema management
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Capture is active only when explicitly flagged AND a store URL is set."""
    return bool(config.OBS_CAPTURE_ENABLED and config.DATABASE_URL)


def _get_engine() -> Optional[Engine]:
    """Return a cached engine for config.DATABASE_URL, or None if unset."""
    global _engine, _engine_url, _Session
    url = config.DATABASE_URL
    if not url:
        return None
    if _engine is None or _engine_url != url:
        # pool_pre_ping keeps long-lived connections healthy across Render/DB
        # idle timeouts; future=True for SQLAlchemy 2.0 semantics.
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        _engine_url = url
        _Session = sessionmaker(bind=_engine, future=True)
        _ensure_schema(_engine)
    return _engine


def reset_engine() -> None:
    """Dispose and forget the cached engine (used by tests between URLs)."""
    global _engine, _engine_url, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _Session = None
    # CPython reuses freed ids, so a new engine could land on the address of a
    # disposed one and inherit its "already checked" mark. Clear it.
    _schema_checked.clear()


def init_db() -> bool:
    """
    Create the `calls` table if it does not exist (idempotent migration).

    Returns True if the schema was ensured, False if no DATABASE_URL is set.
    """
    engine = _get_engine()
    if engine is None:
        _log.warning("[obs.store] init_db skipped — OBS_DATABASE_URL/DATABASE_URL not set")
        return False
    # _get_engine already ran _ensure_schema when it built the engine; run it
    # again rather than depend on that, so `python -m app.obs.migrate` against an
    # engine this process built earlier is still a real migration.
    _ensure_schema(engine, force=True)
    _log.info("[obs.store] schema ensured (calls table)")
    return True


# Engines whose schema has already been checked this process (by id, since an
# Engine is not hashable by URL alone and reset_engine may build several).
_schema_checked: set = set()


def _ensure_schema(engine: Engine, force: bool = False) -> None:
    """Create the table and add any missing columns. Idempotent, never raises.

    Run when the engine is BUILT, not only from `python -m app.obs.migrate`.

    This is a deploy-safety fix, and the failure it prevents is severe. session.merge
    SELECTs every mapped column, so a single column present in the model and absent
    in the database fails the whole write — not just that column. capture_call
    raises, capture_call_async swallows it and returns False, and obs capture stops
    for that service: no transcript, no screening, no guard counters, nothing for
    the judge to score. Silently, because the only trace is one log line per call.

    Measured, not assumed: against a store missing one column, capture_call raised
    OperationalError and the table ended with zero rows.

    That hazard was latent for every column added since the Phase 1 schema
    (build_sha, calendar_event_id, the judge columns), and depended entirely on an
    operator remembering to run the migration before the code that needs it
    deploys. Render's autoDeploy does not wait for that. Checking at engine build
    costs one inspect() per process and removes the ordering requirement.

    Never raises: a store that cannot be migrated (no DDL rights) must fail at the
    write, where it is logged with the call_sid, rather than at engine creation
    where it would take down every read path too.
    """
    if not force and id(engine) in _schema_checked:
        return
    _schema_checked.add(id(engine))
    try:
        from sqlalchemy import inspect as _inspect

        if not _inspect(engine).has_table("calls"):
            Base.metadata.create_all(engine, checkfirst=True)
            _log.info("[obs.store] created calls table")
            return  # freshly created from the model: no columns can be missing
        _ensure_new_columns(engine)
    except Exception as exc:  # pragma: no cover - defensive; must not break reads
        _log.error("[obs.store] schema check failed: %r", exc)


# Columns added after the initial Phase 1 schema. create_all(checkfirst=True) only
# creates missing *tables*, not missing columns on an existing table, so we add
# them here idempotently. Each ALTER is best-effort: if the column already exists
# the dialect raises, which we swallow — so re-running migrate is always safe on
# both SQLite (tests) and Postgres (prod).
_ADDED_COLUMNS = {
    "screening": "JSON",
    # 2026-07-31 — the running commit, recorded by the process instead of
    # reconstructed from deploy timestamps. NULL on every earlier call.
    "build_sha": "VARCHAR(16)",
    "calendar_event_id": "VARCHAR(128)",
    # Per-turn latency. Until this column existed the [LAT] lines were logged and
    # nothing else, so no historical latency can be back-filled: every row before
    # the migration is NULL by absence, not by measurement.
    "latency": "JSON",
    "outcome": "VARCHAR(32)",
    "quality_score": "INTEGER",
    "intent_resolved": "BOOLEAN",
    "failure_tags": "JSON",
    "action_needed": "VARCHAR(16)",
    "evidence": "TEXT",
    "rubric_version": "VARCHAR(16)",
    "judged_at": "TIMESTAMP",
}


def _ensure_new_columns(engine: Engine) -> None:
    from sqlalchemy import inspect as _inspect, text as _text
    existing = {c["name"] for c in _inspect(engine).get_columns("calls")}
    for name, ddl_type in _ADDED_COLUMNS.items():
        if name in existing:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(f"ALTER TABLE calls ADD COLUMN {name} {ddl_type}"))
            _log.info("[obs.store] added column calls.%s", name)
        except Exception as exc:  # pragma: no cover - defensive (racing migrate)
            _log.warning("[obs.store] could not add column %s: %r", name, exc)


# ---------------------------------------------------------------------------
# Record → row mapping
# ---------------------------------------------------------------------------

def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _row_from_record(record: Dict[str, Any], turns: List[Dict[str, str]]) -> Call:
    """Build a Call row from a CallLogger record + the ordered transcript turns."""
    return Call(
        call_sid=record.get("call_sid"),
        clinic_id=record.get("clinic_id"),
        build_sha=record.get("build_sha"),
        start_utc=_parse_dt(record.get("start_utc")),
        end_utc=_parse_dt(record.get("end_utc")),
        duration_s=record.get("duration_s"),
        success=record.get("success"),
        reason=record.get("reason"),
        final_state=record.get("final_state"),
        caller_number=record.get("caller_number"),
        dialled_number=record.get("dialled_number"),
        booking_confirmed=record.get("booking_confirmed"),
        acuity_booking_id=record.get("acuity_booking_id"),
        calendar_event_id=record.get("calendar_event_id"),
        transfer_attempted=record.get("transfer_attempted"),
        graceful_exit=record.get("graceful_exit"),
        total_retries=record.get("total_retries"),
        slot_retry_counts=record.get("slot_retry_counts"),
        turn_count=record.get("turn_count"),
        tone=record.get("tone"),
        collected=record.get("collected"),
        screening=record.get("screening") or None,
        latency=record.get("latency") or None,
        transcript=list(turns or []),
        raw=record,
    )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def capture_call(record: Dict[str, Any], turns: List[Dict[str, str]]) -> bool:
    """
    Persist one call (synchronous). Idempotent upsert keyed on call_sid.

    Returns True on write, False if skipped (no engine / no call_sid).
    Raises on a genuine DB error — callers on the live path must guard this.
    """
    call_sid = record.get("call_sid")
    if not call_sid:
        _log.warning("[obs.store] capture skipped — no call_sid in record")
        return False

    engine = _get_engine()
    if engine is None or _Session is None:
        return False

    row = _row_from_record(record, turns)
    session: Session = _Session()
    try:
        session.merge(row)  # insert-or-update by primary key (idempotent)
        session.commit()
        _log.info("[obs.store] captured call_sid=%s turns=%d", call_sid, len(turns or []))
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def capture_call_async(record: Dict[str, Any], turns: List[Dict[str, str]]) -> bool:
    """
    Async, non-blocking capture for the teardown path.

    No-ops instantly when disabled. Runs the sync write in a worker thread so a
    slow DB never stalls the event loop. Never raises: any error is logged and
    swallowed so call teardown is never broken by the observability layer.
    """
    if not is_enabled():
        return False
    try:
        return await asyncio.to_thread(capture_call, record, turns)
    except Exception as exc:  # pragma: no cover - defensive; unit-tested via capture_call
        _log.error("[obs.store] async capture failed call_sid=%s: %r",
                   record.get("call_sid"), exc)
        return False


# ---------------------------------------------------------------------------
# Read path (used by the replay harness and later phases)
# ---------------------------------------------------------------------------

def get_call(call_sid: str) -> Optional[Dict[str, Any]]:
    """Load one stored call as a plain dict, or None if not found / no store."""
    engine = _get_engine()
    if engine is None or _Session is None:
        return None
    session: Session = _Session()
    try:
        row = session.get(Call, call_sid)
        return row.to_dict() if row is not None else None
    finally:
        session.close()


def list_calls(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    clinic_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return stored calls as dicts, filtered by start time / clinic (for reporting).

    Ordered oldest-first by start_utc. Empty list if no store is configured.
    """
    engine = _get_engine()
    if engine is None or _Session is None:
        return []
    from sqlalchemy import select
    session: Session = _Session()
    try:
        stmt = select(Call)
        if since is not None:
            stmt = stmt.where(Call.start_utc >= since)
        if until is not None:
            stmt = stmt.where(Call.start_utc < until)
        if clinic_id is not None:
            stmt = stmt.where(Call.clinic_id == clinic_id)
        stmt = stmt.order_by(Call.start_utc)
        return [row.to_dict() for row in session.scalars(stmt)]
    finally:
        session.close()


def save_judgement(call_sid: str, judgement: Dict[str, Any]) -> bool:
    """Write a Phase 3 judge result onto an existing call row.

    Returns True on update, False if no store or the call row does not exist.
    Raises on a genuine DB error — async callers must guard.
    """
    from datetime import datetime, timezone

    engine = _get_engine()
    if engine is None or _Session is None:
        return False
    session: Session = _Session()
    try:
        row = session.get(Call, call_sid)
        if row is None:
            _log.warning("[obs.store] save_judgement: no row for call_sid=%s", call_sid)
            return False
        row.outcome = judgement.get("outcome")
        row.quality_score = judgement.get("quality_score")
        row.intent_resolved = judgement.get("intent_resolved")
        row.failure_tags = judgement.get("failure_tags")
        row.action_needed = judgement.get("action_needed")
        row.evidence = judgement.get("evidence")
        row.rubric_version = judgement.get("rubric_version")
        row.judged_at = datetime.now(timezone.utc)
        session.commit()
        _log.info("[obs.store] judged call_sid=%s score=%s", call_sid,
                  judgement.get("quality_score"))
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
