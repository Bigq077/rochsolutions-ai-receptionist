# tests/regression/test_appointment_reminders_kill_switch.py
"""
The automatic 24hr/2hr appointment reminder SMS are switched ON — this is a
LIVE clinic branch.

APPOINTMENT_REMINDERS_ENABLED still exists and still gates both halves, but on
jv_v2 it defaults ON, which is the opposite of latency-eval (this branch's
parent) and matches the two other live clinics, theorem-onboarding and
vitaledge-onboarding, which carry no switch at all and send unconditionally by
owner decision (confirmed 2026-08-10).

The switch was introduced on latency-eval on 2026-08-07 so an isolated
timing-eval service could not text real patients. Keeping the switch but
inverting its default gives a live clinic a named off-ramp without inheriting
eval silence — the failure that cost theorem-onboarding days of unsent
confirmations (3b2f195).

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

# `None` (unset) is deliberately NOT in this list on jv_v2. On latency-eval an
# unset variable means OFF; on a live clinic branch it means ON, and that
# inversion is the whole point of the branch. Unset is covered by
# test_default_is_on_with_no_environment_at_all below.
@pytest.mark.parametrize("env", ["false", "0", "no", "off", ""])
async def test_no_reminder_is_queued_when_the_switch_is_off(monkeypatch, fake_redis, env):
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


async def test_default_is_on_with_no_environment_at_all(monkeypatch):
    """The default lives in code, not in Render's env panel — and on a LIVE
    clinic branch it must default ON.

    This is the assertion that was inverted when jv_v2 was cut from
    latency-eval, and it is the one that matters. theorem-onboarding was cut the
    same way and silently inherited latency-eval's OFF default for SMS, so
    Mark's line sent nothing for days (3b2f195). A forgotten Render variable
    must fail towards sending here, matching the two live clinics that carry no
    switch at all."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    assert scheduler._appointment_reminders_enabled() is True


# ---------------------------------------------------------------------------
# 3 — the worker half
# ---------------------------------------------------------------------------

async def test_queued_reminders_are_never_sent_while_the_switch_is_off(
    monkeypatch, fake_redis
):
    """Reminders queued before the switch was thrown must not fire. The worker
    must not even read the pending set.

    Set explicitly rather than unset: on this branch unset means ON."""
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", "false")

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
