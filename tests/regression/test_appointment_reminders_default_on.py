# tests/regression/test_appointment_reminders_default_on.py
"""
This is a LIVE clinic branch: appointment reminders default ON.

The APPOINTMENT_REMINDERS_ENABLED switch was born on latency-eval on
2026-08-07, where it defaults OFF so an isolated timing-eval service cannot
text real patients. This branch inherits engine fixes from latency-eval by
cherry-pick, and that default must NOT come with them.

Owner decision, 2026-08-20: reminders on for all three live clinics. So the
switch exists here — a named off-switch is better than none — but a Render
service that never sets the variable must still send. If a future port drags
latency-eval's "false" default across, this test is what catches it, because
the symptom otherwise is total silence that looks exactly like the feature
never having been built.

The switch itself (both halves gated, nothing queued and nothing sent when it
is off) is covered on latency-eval by test_appointment_reminders_kill_switch.
This file pins only the DEFAULT, which is the half that differs per branch.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.notifications import scheduler


def test_the_default_is_on_with_no_environment_at_all(monkeypatch):
    """The default lives in code, not in Render's env panel."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    assert scheduler._appointment_reminders_enabled() is True, (
        "reminders default OFF on a live clinic branch — a Render service "
        "with no APPOINTMENT_REMINDERS_ENABLED set would text nobody"
    )


@pytest.mark.parametrize("env", ["false", "0", "no", "off"])
def test_it_can_still_be_switched_off_deliberately(monkeypatch, env):
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", env)
    assert scheduler._appointment_reminders_enabled() is False


async def test_a_booking_queues_reminders_with_no_environment_set(monkeypatch):
    """End-to-end on the default: the queue must actually receive something."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)

    queued: dict = {}

    class _Redis:
        async def setex(self, name, time, value):
            return True

        async def zadd(self, key, mapping):
            queued.update(mapping)
            return len(mapping)

    monkeypatch.setattr(scheduler, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(scheduler, "redis_client", _Redis())

    result = await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=datetime.now(timezone.utc) + timedelta(days=3),
        location="Redditch",
    )

    assert result is True
    assert any(name.endswith(":24hr") for name in queued)
    assert any(name.endswith(":2hr") for name in queued)
