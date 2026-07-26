"""
app/obs/models.py
-----------------
SQLAlchemy model for the durable call store (spec §5.1).

Schema = every field from CallLogger._build_record() as a typed column, PLUS the
full ordered transcript (session["turns"]) and a `raw` copy of the whole record
for forward-compatibility (so a future call_logger field is never silently lost
before this schema catches up).

Design notes:
- Generic SQLAlchemy `JSON` type (not the Postgres-specific JSONB) is used
  deliberately so the exact same model runs on SQLite in offline tests and on
  Postgres in production. On Postgres this maps to JSON; JSONB is not required
  for the read patterns Phases 3–5 need.
- `call_sid` is unique — capture is idempotent (upsert-by-call_sid), so a retried
  teardown or a replayed call never creates duplicate rows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Call(Base):
    """One durable row per completed call."""

    __tablename__ = "calls"

    # Primary identity
    call_sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    clinic_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # Timing
    start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Outcome (flow-reported; the Phase 3 judge adds a separate quality score)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_state: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Parties (PII / special-category — stored in our EU store only; never copied
    # into committed test fixtures — see spec §7 and the Phase 4 redactor).
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dialled_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Booking / escalation signals
    booking_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    acuity_booking_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transfer_attempted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    graceful_exit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Retry / turn counters
    total_retries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot_retry_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Collected slots (name/phone/reason/day/slot)
    collected: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Clinical screening state, incl. arm_paths = {screen_id: "trigger"|"orphan"}.
    # "orphan" with no "trigger" anywhere is the dormant-Layer-1 signature that
    # took a human reading a full log to spot in the 2026-07-25 sweep.
    screening: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # The transcript — ordered [{"role","text"}] turns. The input to every
    # downstream layer (judge, regression). Never dropped.
    transcript: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Full record as built by CallLogger, verbatim — forward-compat safety net.
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # When this row was written (distinct from call start/end).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # --- Phase 3: LLM-as-judge (spec §5.3) ------------------------------------
    # Populated asynchronously after teardown by app/obs/judge.py. Null until the
    # call has been scored (or if the judge is disabled).
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    intent_resolved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Tiered alerting (v2): "callback" | "review" | "none".
    action_needed: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    judged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_dict(self) -> dict:
        """Plain-dict view used by the replay harness and later phases."""
        return {
            "call_sid": self.call_sid,
            "clinic_id": self.clinic_id,
            "start_utc": self.start_utc.isoformat() if self.start_utc else None,
            "end_utc": self.end_utc.isoformat() if self.end_utc else None,
            "duration_s": self.duration_s,
            "success": self.success,
            "reason": self.reason,
            "final_state": self.final_state,
            "caller_number": self.caller_number,
            "dialled_number": self.dialled_number,
            "booking_confirmed": self.booking_confirmed,
            "acuity_booking_id": self.acuity_booking_id,
            "transfer_attempted": self.transfer_attempted,
            "graceful_exit": self.graceful_exit,
            "total_retries": self.total_retries,
            "slot_retry_counts": self.slot_retry_counts,
            "turn_count": self.turn_count,
            "tone": self.tone,
            "collected": self.collected,
            "screening": self.screening,
            "transcript": self.transcript or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            # Phase 3 judge fields
            "outcome": self.outcome,
            "quality_score": self.quality_score,
            "intent_resolved": self.intent_resolved,
            "failure_tags": self.failure_tags,
            "action_needed": self.action_needed,
            "evidence": self.evidence,
            "rubric_version": self.rubric_version,
            "judged_at": self.judged_at.isoformat() if self.judged_at else None,
        }
