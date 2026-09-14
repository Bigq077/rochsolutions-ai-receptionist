"""Owner, 14 Sep 2026: no name nudge and no SMS reminders for demo-line calls.

Every demo call that exited the name step queued a 2 h name nudge. The
reminder queues are global keys on shared Redis, so a clinic service with SMS
on could send a nudge the demo service queued -- credits spent on test calls.

`operational.sms_reminders_enabled: false` in northgate's clinic.json stops
them being QUEUED. The live clinics do not set it and keep every reminder.

Redis is a mock throughout: nothing here can reach a real queue or a phone.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clinic_config import get_clinic
from app.notifications import scheduler


def _redis():
    r = MagicMock()
    r.zadd = AsyncMock()
    return r


def test_only_the_demo_tenant_has_reminders_off():
    assert get_clinic("northgate")["sms_reminders_enabled"] is False
    # Theorem's config comes from canonical.py and lacks the key; the gate reads
    # it with a True default, which is what this asserts.
    for cid in ("jv_v1", "vital_edge", "theorem", "theorem_v3"):
        assert get_clinic(cid).get("sms_reminders_enabled", True) is True, cid
        assert scheduler._clinic_sends_reminders(cid, "test") is True, cid


@pytest.mark.parametrize("clinic_id, queued", [("northgate", False), ("jv_v1", True), ("", True)])
async def test_the_name_nudge(clinic_id, queued):
    r = _redis()
    with patch("app.storage.redis_store.redis_client", r):
        await scheduler.schedule_name_confirm_reminder(
            phone="+447000000000", first_name="Test", clinic_id=clinic_id)
    assert r.zadd.await_count == (1 if queued else 0)


@pytest.mark.parametrize("clinic_id, queued", [("northgate", False), ("jv_v1", True)])
async def test_the_address_nudge(clinic_id, queued):
    r = _redis()
    with patch("app.storage.redis_store.redis_client", r):
        await scheduler.schedule_address_reminder(
            phone="+447000000000", first_name="Test", clinic_id=clinic_id)
    assert r.zadd.await_count == (1 if queued else 0)


async def test_appointment_reminders_are_refused_for_the_demo_even_with_the_env_on(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", "true")
    with patch.object(scheduler, "REDIS_AVAILABLE", False):
        ok = await scheduler.schedule_appointment_reminders(
            patient_phone="+447000000000", patient_name="Test",
            appointment_time=datetime.now() + timedelta(days=2), location="Didsbury",
            clinic_id="northgate",
        )
    assert ok is False


async def test_the_name_chase_passes_the_clinic_through():
    from app.notifications import name_chase
    seen = {}

    async def _fake_nudge(**kw):
        seen.update(kw)

    with patch("app.storage.redis_store.create_pending_name_confirmation", new=AsyncMock()), \
         patch("app.notifications.scheduler.schedule_name_confirm_reminder",
               new=AsyncMock(side_effect=_fake_nudge)):
        await name_chase.start(
            session={"clinic_id": "northgate"}, clinic={"clinic_id": "northgate"},
            phone="07502211207", patient_name="Bowel Gardner", provider="google_calendar",
            appointment_id="evt1", when_label="Tuesday", location="Didsbury",
        )
    assert seen["clinic_id"] == "northgate"
