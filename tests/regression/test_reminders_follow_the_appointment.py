# tests/regression/test_reminders_follow_the_appointment.py
"""
The 24hr/2hr reminder must describe an appointment that still exists, at the
time it is actually at, for the clinic that took the booking.

Owner decision, 2026-08-20: reminders on for all three live clinics. Auditing
what that switch would actually turn on found four ways a reminder could be
sent about something untrue, and one way it could silently never be sent at
all. Each is pinned here.

  1. CANCEL — the Acuity path retracted a cancelled appointment's queued
     reminders; the Google-Calendar path never did. A JV or Vital Edge patient
     who cancelled on Monday was still texted "just a reminder — your
     appointment is tomorrow" on Tuesday.

  2. RESCHEDULE — the Google-Calendar path is an in-place event move, not an
     internal book+cancel, so it did neither half: the OLD time kept its
     reminders and the NEW time got none.

  3. PROVISIONAL — Vital Edge bookings are REQUESTS. The caller is texted "not
     yet confirmed — Jonathan will be in touch" and the event stays titled
     "PENDING CONFIRMATION — …" until he confirms. A reminder must not go out
     while that is still true. (Separately: this path queued no reminders at
     all, because _exec_book_appointment early-returns above its reminder
     block — so Vital Edge looked "on" while doing nothing.)

  4. LONG LEAD — the reminder key carried a flat 7-day TTL. An appointment
     booked more than 8 days out had its key expire before it came due, and
     process_due_reminders treats a missing key as an orphan and drops it
     without a word. Nobody was texted and nothing was logged.

  5. FALSE SUCCESS — a suppressed or rejected send was recorded as "sent",
     the same defect the booking confirmation SMS carried until 2026-08-18.

Plus the wording: the body hardcoded "physiotherapy" for every tenant, which
is wrong for two of the three live clinics.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.notifications import scheduler
from app.notifications import templates


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeRedis:
    """
    Honours TTL against a clock the test can wind forward. That matters: the
    long-lead defect IS the expiry, so a fake that ignores `time` would pass
    happily against the very bug this file exists to pin.
    """

    def __init__(self):
        self.store: dict = {}       # name -> (value, expires_at)
        self.zset: dict = {}
        self.setex_calls: list = []
        self.now: float = datetime.now(timezone.utc).timestamp()

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs).total_seconds()

    async def setex(self, name, time, value):
        self.setex_calls.append((name, time))
        self.store[name] = (value, self.now + time)
        return True

    async def get(self, name):
        entry = self.store.get(name)
        if entry is None:
            return None
        value, expires_at = entry
        if self.now >= expires_at:
            del self.store[name]
            return None
        return value

    async def delete(self, name):
        self.store.pop(name, None)
        return 1

    async def zadd(self, key, mapping):
        self.zset.update(mapping)
        return len(mapping)

    async def zrem(self, key, *members):
        for m in members:
            self.zset.pop(m, None)
        return len(members)

    async def zrange(self, key, start, end):
        return list(self.zset)

    async def zrangebyscore(self, key, min=None, max=None):
        return [k for k, score in self.zset.items() if min <= score <= max]

    def payloads(self):
        import json

        return [json.loads(v) for v, _exp in self.store.values()]


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(scheduler, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(scheduler, "redis_client", fake)
    monkeypatch.setenv("APPOINTMENT_REMINDERS_ENABLED", "true")
    return fake


# ---------------------------------------------------------------------------
# 4 — a booking further out than a week must still be reminded
# ---------------------------------------------------------------------------

async def test_a_reminder_key_outlives_the_moment_it_is_due(redis):
    """
    Booked 30 days ahead: the 24hr reminder is due in 29 days. Under the old
    flat 7-day TTL that key was long gone by then, and the due entry was
    silently discarded as an orphan.
    """
    appt = datetime.now(timezone.utc) + timedelta(days=30)

    await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=appt,
        location="Bolton",
    )

    assert len(redis.setex_calls) == 2, "expected a 24hr and a 2hr reminder"
    for name, ttl in redis.setex_calls:
        due = redis.zset[name]
        seconds_until_due = due - datetime.now(timezone.utc).timestamp()
        assert ttl > seconds_until_due, (
            f"{name} expires {seconds_until_due - ttl:.0f}s BEFORE it is due — "
            "it will be dropped without being sent"
        )


async def test_a_long_lead_reminder_actually_fires_when_its_time_comes(redis, monkeypatch):
    """End-to-end on the same defect: queue it far out, wind the clock on, and
    the worker must send rather than find an empty key."""
    appt = datetime.now(timezone.utc) + timedelta(days=30)
    await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=appt,
        location="Bolton",
    )

    # Wind the clock to the day the 24hr reminder actually comes due. Under the
    # old flat 7-day TTL both payloads have evaporated by now.
    redis.advance(days=29)
    redis.zset = {k: 0.0 for k in redis.zset}

    sent: list = []

    async def _fake_send(data):
        sent.append(data["reminder_type"])
        return "sent"

    monkeypatch.setattr(scheduler, "_send_reminder", _fake_send)

    processed = await scheduler.process_due_reminders()

    assert processed == 2
    assert sorted(sent) == ["24hr", "2hr"]


# ---------------------------------------------------------------------------
# 5 — a reminder nobody received must not read as delivered
# ---------------------------------------------------------------------------

async def test_a_suppressed_reminder_is_not_recorded_as_sent(redis, monkeypatch):
    import json

    appt = datetime.now(timezone.utc) + timedelta(days=3)
    await scheduler.schedule_appointment_reminders(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=appt,
        location="Bolton",
    )
    redis.zset = {k: 0.0 for k in redis.zset}

    async def _suppressed(data):
        return "suppressed"

    monkeypatch.setattr(scheduler, "_send_reminder", _suppressed)
    await scheduler.process_due_reminders()

    statuses = {p["status"] for p in redis.payloads()}
    assert statuses == {"suppressed"}, (
        f"a reminder that was never delivered was recorded as {statuses} — "
        "this is the false success the booking confirmation had until 18 Aug"
    )


async def test_booking_sms_reports_a_suppressed_reminder_as_not_sent(monkeypatch):
    """`send_sms` returns None for a suppressed send as well as a rejected one.
    The reminder senders used to discard it and return True regardless."""
    from app.notifications import booking_sms

    async def _suppressed(**kwargs):
        return None

    monkeypatch.setattr(booking_sms, "send_sms", _suppressed)

    ok = await booking_sms.send_24hr_reminder(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=datetime.now(timezone.utc) + timedelta(days=1),
        location="Bolton",
    )
    assert ok is False

    ok2 = await booking_sms.send_same_day_reminder(
        patient_phone="+447700900123",
        patient_name="Alex",
        appointment_time=datetime.now(timezone.utc) + timedelta(hours=2),
        location="Bolton",
    )
    assert ok2 is False


# ---------------------------------------------------------------------------
# 3 — a provisional booking is not an appointment yet
# ---------------------------------------------------------------------------

def _gate():
    return {"event_id": "evt1", "calendar_id": "cal1", "clinic_id": "vital_edge"}


async def _patch_calendar(monkeypatch, summary):
    """Make the gate's calendar read return an event with this title."""
    from app.tools import calendar_google
    from app.storage import redis_store

    async def _tokens(_key):
        return {"token": "x"}

    async def _key(_clinic_id):
        return "google_tokens:vital_edge"

    monkeypatch.setattr(redis_store, "redis_get_json", _tokens)
    monkeypatch.setattr(calendar_google, "resolve_tokens_key", _key)
    monkeypatch.setattr(
        calendar_google,
        "get_event",
        lambda tokens, event_id, calendar_id: (
            None if summary is None else {"id": event_id, "summary": summary}
        ),
    )


async def test_an_unconfirmed_provisional_booking_is_not_reminded(monkeypatch):
    await _patch_calendar(monkeypatch, "PENDING CONFIRMATION — Alex — Sports Massage")
    assert await scheduler._confirmed_enough_to_remind(_gate()) is False


async def test_a_confirmed_provisional_booking_is_reminded(monkeypatch):
    """Jonathan renames the event when he confirms. That is the signal."""
    await _patch_calendar(monkeypatch, "Alex — Sports Massage")
    assert await scheduler._confirmed_enough_to_remind(_gate()) is True


async def test_a_deleted_provisional_booking_is_not_reminded(monkeypatch):
    await _patch_calendar(monkeypatch, None)
    assert await scheduler._confirmed_enough_to_remind(_gate()) is False


async def test_an_unreadable_calendar_still_sends(monkeypatch):
    """
    Fails OPEN on purpose. A clinic whose Google auth has lapsed must not go
    quietly reminder-less — that looks identical to the feature being off, and
    the whole point of this change is that confirmed patients get reminded.
    """
    from app.tools import calendar_google
    from app.storage import redis_store

    async def _tokens(_key):
        return {"token": "x"}

    async def _key(_clinic_id):
        return "google_tokens:vital_edge"

    def _boom(*a, **k):
        raise RuntimeError("calendar API down")

    monkeypatch.setattr(redis_store, "redis_get_json", _tokens)
    monkeypatch.setattr(calendar_google, "resolve_tokens_key", _key)
    monkeypatch.setattr(calendar_google, "get_event", _boom)

    assert await scheduler._confirmed_enough_to_remind(_gate()) is True


async def test_a_clinic_with_no_gate_is_never_calendar_checked(monkeypatch):
    """Theorem and JV carry no gate — they must not pay a calendar read."""
    from app.tools import calendar_google

    def _must_not_be_called(*a, **k):
        raise AssertionError("a non-provisional reminder read the calendar")

    monkeypatch.setattr(calendar_google, "get_event", _must_not_be_called)
    assert await scheduler._confirmed_enough_to_remind(None) is True


async def test_the_gate_suppresses_the_send_not_just_the_check(redis, monkeypatch):
    """_send_reminder must honour the gate, not merely expose it."""
    await _patch_calendar(monkeypatch, "PENDING CONFIRMATION — Alex — Sports Massage")

    from app.notifications import booking_sms

    async def _must_not_send(**kwargs):
        raise AssertionError("an unconfirmed provisional booking was texted")

    monkeypatch.setattr(booking_sms, "send_sms", _must_not_send)

    outcome = await scheduler._send_reminder(
        {
            "reminder_type": "24hr",
            "patient_phone": "+447700900123",
            "patient_name": "Alex",
            "appointment_time": (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat(),
            "location": "Kingston",
            "confirm_gate": _gate(),
        }
    )
    assert outcome == "suppressed"


# ---------------------------------------------------------------------------
# Wording — the reminder must name what the clinic actually sells
# ---------------------------------------------------------------------------

def test_the_reminder_does_not_call_a_massage_physiotherapy():
    body = templates.format_24hr_reminder(
        patient_name="Alex",
        appointment_time=datetime(2026, 8, 25, 15, 0),
        location="Kingston",
        clinic_name="Vital Edge Therapy",
        clinic_phone="+44 7426 779875",
        appointment_noun="massage",
    )
    assert "massage appointment" in body
    assert "physiotherapy" not in body.lower()


def test_a_clinic_that_names_no_speciality_gets_neutral_wording():
    """Theorem's book runs from acupuncture to psychotherapy. Guessing a
    speciality there is worse than omitting one."""
    body = templates.format_same_day_reminder(
        patient_name="Alex",
        appointment_time=datetime(2026, 8, 25, 15, 0),
        location="Redditch",
        clinic_name="Theorem Health and Wellness",
        clinic_phone="07870 166861",
    )
    assert "your appointment is today" in body
    assert "physiotherapy" not in body.lower()


def test_the_noun_comes_from_clinic_config_not_engine_code():
    from app.clinic_config import get_clinic

    assert get_clinic("vital_edge").get("sms_appointment_noun") == "massage"
    assert get_clinic("jv_v1").get("sms_appointment_noun") == "physiotherapy"


def test_a_status_marker_never_becomes_the_greeting():
    """Calendar titles read "PENDING CONFIRMATION — Name — Service". The
    reminder templates took patient_name raw where the cancel/reschedule ones
    normalise it, so the same "Hi PENDING" that shipped on 2026-08-19 could
    have reached a reminder."""
    body = templates.format_24hr_reminder(
        patient_name="PENDING",
        appointment_time=datetime(2026, 8, 25, 15, 0),
        location="Kingston",
    )
    assert "Hi PENDING" not in body


# ---------------------------------------------------------------------------
# 1 + 2 — the Google-Calendar cancel and reschedule paths
# ---------------------------------------------------------------------------

APPT_ID = "evt-jv-001"
OLD_START = "2026-09-10T14:00:00+01:00"
OLD_END = "2026-09-10T14:40:00+01:00"
NEW_START = "2026-09-12T16:00:00+01:00"
CALLER = "+447700900123"


def _jv_event():
    return {
        "id": APPT_ID,
        "summary": "MSK Treatment Session — Alex Rowe",
        "description": f"Patient: Alex Rowe\nPhone: {CALLER}\nService: MSK Treatment Session",
        "start": {"dateTime": OLD_START},
        "end": {"dateTime": OLD_END},
    }


@pytest.fixture
def gcal(monkeypatch):
    """
    Stand the Google-Calendar executors up on fakes and record every reminder
    call they make. Returns the recorder.
    """
    from app.tools import receptionist_tools as rt
    from app.tools import calendar_google
    from app.notifications import scheduler as sched
    from app.notifications import booking_sms, owner_alert, sms

    calls = {"retracted": [], "scheduled": []}

    async def _retract(patient_phone, appointment_time):
        calls["retracted"].append((patient_phone, appointment_time))
        return 1

    async def _schedule(**kwargs):
        calls["scheduled"].append(kwargs)
        return True

    monkeypatch.setattr(sched, "cancel_reminders_for_appointment", _retract)
    monkeypatch.setattr(sched, "schedule_appointment_reminders", _schedule)

    async def _tokens(_clinic_id=None):
        return {"token": "x"}

    monkeypatch.setattr(rt, "_get_tokens", _tokens)
    monkeypatch.setattr(rt, "_save_gcal_tokens", lambda *a, **k: None)
    monkeypatch.setattr(
        calendar_google, "list_upcoming_events", lambda *a, **k: [_jv_event()]
    )
    monkeypatch.setattr(calendar_google, "delete_event", lambda *a, **k: True)
    monkeypatch.setattr(calendar_google, "patch_event_time", lambda *a, **k: True)
    monkeypatch.setattr(calendar_google, "update_event", lambda *a, **k: {"id": APPT_ID})

    # Nothing in this test may reach Twilio. Patching app.notifications.sms is
    # NOT enough on its own — booking_sms and owner_alert each bind their own
    # copy of send_sms at import time.
    async def _no_sms(*a, **k):
        return None

    for mod in (sms, booking_sms, owner_alert):
        if hasattr(mod, "send_sms"):
            monkeypatch.setattr(mod, "send_sms", _no_sms)
    monkeypatch.setattr(booking_sms, "send_cancellation_confirmation", _no_sms)
    monkeypatch.setattr(booking_sms, "send_reschedule_confirmation", _no_sms)
    monkeypatch.setattr(owner_alert, "notify_owner", _no_sms)

    return calls


def _jv_session():
    return {"clinic_id": "jv_v1", "_lookup_appointment_id": APPT_ID}


async def test_cancelling_retracts_the_reminders_for_that_appointment(gcal):
    """
    Defect 1. The Acuity path has retracted since reminders existed; this one
    never did, so a cancelled JV or Vital Edge patient was still reminded.
    """
    from app.tools.receptionist_tools import _exec_cancel_appointment

    result = await _exec_cancel_appointment(
        {"phone": CALLER, "appointment_id": APPT_ID, "patient_name": "Alex Rowe"},
        _jv_session(),
    )

    assert result.get("success") is True
    assert gcal["retracted"], (
        "the appointment was deleted from the calendar but its 24hr/2hr "
        "reminders were left queued — the patient gets reminded of an "
        "appointment that no longer exists"
    )
    phone, when = gcal["retracted"][0]
    assert when == datetime.fromisoformat(OLD_START), (
        "retracted the wrong instant — matching is by exact appointment time"
    )


async def test_cancelling_does_not_queue_a_new_reminder(gcal):
    from app.tools.receptionist_tools import _exec_cancel_appointment

    await _exec_cancel_appointment(
        {"phone": CALLER, "appointment_id": APPT_ID, "patient_name": "Alex Rowe"},
        _jv_session(),
    )
    assert gcal["scheduled"] == []


async def test_rescheduling_moves_the_reminders_to_the_new_time(gcal):
    """
    Defect 2. An in-place event move did neither half of the bookkeeping: the
    old time kept its reminders, the new time got none.
    """
    from app.tools.receptionist_tools import _exec_reschedule_appointment

    result = await _exec_reschedule_appointment(
        {
            "phone": CALLER,
            "appointment_id": APPT_ID,
            "patient_name": "Alex Rowe",
            "new_slot_iso": NEW_START,
            "duration_minutes": 40,
        },
        _jv_session(),
    )

    assert result.get("success") is True

    assert gcal["retracted"], "the OLD time's reminders were left queued"
    _phone, when = gcal["retracted"][0]
    assert when == datetime.fromisoformat(OLD_START)

    assert gcal["scheduled"], "the NEW time got no reminders at all"
    queued = gcal["scheduled"][0]
    assert queued["appointment_time"] == datetime.fromisoformat(NEW_START)


async def test_a_rescheduled_jv_booking_carries_no_provisional_gate(gcal):
    """The gate belongs to provisional clinics only. Applying it to JV would
    make every reminder pay a calendar read it cannot satisfy."""
    from app.tools.receptionist_tools import _exec_reschedule_appointment

    await _exec_reschedule_appointment(
        {
            "phone": CALLER,
            "appointment_id": APPT_ID,
            "patient_name": "Alex Rowe",
            "new_slot_iso": NEW_START,
            "duration_minutes": 40,
        },
        _jv_session(),
    )
    assert gcal["scheduled"][0].get("confirm_gate") is None


async def test_the_reschedule_reminder_is_pinned_to_the_booking_clinics_line(gcal):
    """`pending_reminders` is a global Redis key. An unpinned sender means
    another tenant's worker texts from ITS number and the reply is lost."""
    from app.tools.receptionist_tools import _exec_reschedule_appointment

    await _exec_reschedule_appointment(
        {
            "phone": CALLER,
            "appointment_id": APPT_ID,
            "patient_name": "Alex Rowe",
            "new_slot_iso": NEW_START,
            "duration_minutes": 40,
        },
        _jv_session(),
    )
    assert "from_number" in gcal["scheduled"][0]
