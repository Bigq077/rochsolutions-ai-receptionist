# tests/regression/test_appointment_reminders_kill_switch.py
"""
The automatic 24hr/2hr appointment reminder SMS are switched OFF.

Owner decision, 2026-08-07: patients should get the booking confirmation text
and nothing else. The reminders are gated behind APPOINTMENT_REMINDERS_ENABLED,
which defaults OFF on this branch.

WHY BOTH HALVES ARE GATED

Gating only the scheduler would have left every reminder queued before the
switch to fire on schedule — the queue lives in Redis and survives the deploy,
so a booking taken an hour before the change would still text the patient the
next morning. Gating only the worker would keep growing a queue nobody drains.
So schedule_appointment_reminders() must queue nothing and process_due_reminders()
must send nothing, independently of each other.

WHAT MUST NOT REGRESS

  1. A booking still succeeds. schedule_appointment_reminders returns False —
     the same value the Redis-unavailable path has always returned, which every
     call site already treats as non-fatal — and it must never raise.
  2. Nothing is written to the reminder queue while the switch is off.
  3. Nothing is read from the reminder queue while the switch is off.
  4. It is a switch, not a deletion. With APPOINTMENT_REMINDERS_ENABLED=true the
     original behaviour returns intact, both queueing and sending. If someone
     "simplifies" this by ripping the scheduler out, case 4 fails.

NOT covered here, because they are a different mechanism and stay on: the
booking confirmation SMS, the name-confirmation nudge (process_name_confirm_
reminders) and the home-visit address nudge (process_address_reminders).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.notifications import scheduler


class _FakeRedis:
    """Records the calls the reminder pipeline makes, so the test can assert
    on 'nothing was queued' rather than on a log line."""

    def __init__(self, due=None):
        self.setex_calls = []
        self.zadd_calls = []
        self.zrangebyscore_calls = []
        self._due = due or []

    async def setex(self, name, time, value):
        self.setex_calls.append(name)
        return True

    async def zadd(self, key, mapping):
        self.zadd_calls.append((key, mapping))
        return 1

    async def zrangebyscore(self, key, min=None, max=None):
        self.zrangebyscore_calls.append(key)
        return list(self._due)


@pytest.fixture
def fake_redis(monkeypatch):
    """Force the module to believe Redis is up, so an empty queue can only be
    the kill switch and never a missing dependency."""
    fake = _FakeRedis()
    monkeypatch.setattr(scheduler, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(scheduler, "redis_client", fake)
    return fake


def _future_appointment():
    return datetime.now(timezone.utc) + timedelta(days=3)


# ---------------------------------------------------------------------------
# 1 + 2 — the scheduler half
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", [None, "false", "0", "no", "off", ""])
async def test_no_reminder_is_queued_when_the_switch_is_off(monkeypatch, fake_redis, env):
    if env is None:
        monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", env)

    result = await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=_future_appointment(),
        location="Alcester",
    )

    # Non-fatal for the booking, and identical to the long-standing
    # Redis-unavailable return that every call site already handles.
    assert result is False
    assert fake_redis.zadd_calls == [], "a reminder was queued with the switch off"
    assert fake_redis.setex_calls == [], "a reminder payload was written with the switch off"


async def test_default_is_off_with_no_environment_at_all(monkeypatch):
    """The default lives in code, not in Render's env panel. A branch that
    deploys with no APPOINTMENT_REMINDERS_ENABLED set must still be silent."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    assert scheduler._appointment_reminders_enabled() is False


# ---------------------------------------------------------------------------
# 3 — the worker half
# ---------------------------------------------------------------------------

async def test_queued_reminders_are_never_sent_while_the_switch_is_off(
    monkeypatch, fake_redis
):
    """Reminders queued before the switch was thrown must not fire. The worker
    must not even read the pending set."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)

    sent = await scheduler.process_due_reminders()

    assert sent == 0
    assert fake_redis.zrangebyscore_calls == [], "the worker read the reminder queue"


async def test_the_other_nudges_are_not_gated_by_this_switch(monkeypatch):
    """The name-confirm and home-visit address nudges chase missing booking
    details and are deliberately still on. If a future edit folds them behind
    the same flag, this fails and the decision gets re-taken on purpose."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)

    import inspect

    for fn in (
        scheduler.process_name_confirm_reminders,
        scheduler.process_address_reminders,
        scheduler.schedule_name_confirm_reminder,
        scheduler.schedule_address_reminder,
    ):
        src = inspect.getsource(fn)
        assert "_appointment_reminders_enabled" not in src, (
            f"{fn.__name__} was folded behind the appointment-reminder switch"
        )


# ---------------------------------------------------------------------------
# 4 — it is a switch, not a deletion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", ["true", "TRUE", "1", "yes", "on"])
async def test_reminders_still_work_when_switched_back_on(monkeypatch, fake_redis, env):
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", env)

    result = await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=_future_appointment(),
        location="Alcester",
    )

    assert result is True
    # 24hr and 2hr, both still in the future for an appointment 3 days out.
    assert len(fake_redis.zadd_calls) == 2
    queued = {name for _key, mapping in fake_redis.zadd_calls for name in mapping}
    assert any(name.endswith(":24hr") for name in queued)
    assert any(name.endswith(":2hr") for name in queued)


async def test_worker_reads_the_queue_again_when_switched_back_on(monkeypatch, fake_redis):
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", "true")

    sent = await scheduler.process_due_reminders()

    assert sent == 0  # nothing due in the fake queue
    assert fake_redis.zrangebyscore_calls == [scheduler.PENDING_REMINDERS_SET]
