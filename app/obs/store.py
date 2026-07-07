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
    return _engine


def reset_engine() -> None:
    """Dispose and forget the cached engine (used by tests between URLs)."""
    global _engine, _engine_url, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _Session = None


def init_db() -> bool:
    """
    Create the `calls` table if it does not exist (idempotent migration).

    Returns True if the schema was ensured, False if no DATABASE_URL is set.
    """
    engine = _get_engine()
    if engine is None:
        _log.warning("[obs.store] init_db skipped — DATABASE_URL not set")
        return False
    Base.metadata.create_all(engine, checkfirst=True)
    _log.info("[obs.store] schema ensured (calls table)")
    return True


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
        transfer_attempted=record.get("transfer_attempted"),
        graceful_exit=record.get("graceful_exit"),
        total_retries=record.get("total_retries"),
        slot_retry_counts=record.get("slot_retry_counts"),
        turn_count=record.get("turn_count"),
        tone=record.get("tone"),
        collected=record.get("collected"),
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
