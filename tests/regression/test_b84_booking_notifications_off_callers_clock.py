"""B-84 — the caller waited 7.3s in silence while notifications were sent.

CA98557584dc, 24 Aug 2026 10:23 GMT, Theorem Alcester:

    10:23:26.7  filler "Just locking that in now…" ENDS
    10:23:27.9  Acuity booking created            <- 1.0s, the only necessary part
    10:23:28.8  owner alert SMS                    0.64s  Twilio round trip
    10:23:29.5  patient confirmation SMS           0.65s  Twilio round trip
    10:23:31.2  tool result returned              <- 3.3s after the booking
    10:23:34.0  audio resumes

7.3 seconds of silence against a 3s bar, and 3.3s of it was notifications the
caller had no reason to wait for.

Two things are pinned here:

  1. book_appointment returns as soon as the DIARY write is done. The
     notifications run detached.
  2. A notification failure can no longer speak for the booking. notify_owner
     was awaited bare, with no try/except, inside the outer `except Exception`
     that returns {"success": False} — so a Twilio hiccup on the OWNER alert
     reported a committed Acuity booking as failed, and the caller was told
     their appointment had not been made. That is the worst failure mode this
     system has (CLAUDE.md §6.1).
"""

import asyncio

import pytest

from app.notifications import background


@pytest.fixture(autouse=True)
def _clean_pending():
    """No leakage between tests, and none out of this module."""
    background._PENDING.clear()
    yield
    for t in list(background._PENDING):
        t.cancel()
    background._PENDING.clear()


# ───────────────────────────────────────────────────────────────────────────
# The helper
# ───────────────────────────────────────────────────────────────────────────

async def test_task_is_retained_while_it_runs():
    """asyncio holds only a WEAK reference to a running task.

    Without the module-level set a detached confirmation SMS can be garbage
    collected mid-flight — intermittently, under load, which is the worst way
    to lose a message a patient was promised out loud.
    """
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    background.run_detached(slow(), label="test", call_sid="CAtest")

    assert background.pending_count() == 1
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert background.pending_count() == 0
    # ...and the done-callback removed it, so the set cannot grow unbounded.
    assert len(background._PENDING) == 0


async def test_failure_is_logged_not_swallowed(caplog):
    """A detached failure that logs nothing is invisible.

    The patient is expecting a text; if it did not go, that has to be findable
    in the call record.
    """
    async def boom():
        raise RuntimeError("twilio said no")

    with caplog.at_level("WARNING"):
        task = background.run_detached(boom(), label="confirm SMS", call_sid="CAxyz")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert any(
        "confirm SMS" in r.message and "FAILED" in r.message
        for r in caplog.records
    ), caplog.text


async def test_drain_waits_for_outstanding_work():
    """A deploy must not drop an SMS that was one round trip from sending."""
    sent = []

    async def slow():
        await asyncio.sleep(0.05)
        sent.append(True)

    background.run_detached(slow(), label="test", call_sid="")
    unfinished = await background.drain(timeout=5.0)

    assert unfinished == 0
    assert sent == [True]


async def test_drain_reports_what_it_could_not_finish():
    async def never():
        await asyncio.Event().wait()

    background.run_detached(never(), label="test", call_sid="")
    assert await background.drain(timeout=0.05) == 1


def test_no_event_loop_closes_the_coroutine_loudly():
    """Never leave a coroutine un-awaited — that is a silent dropped message."""
    async def noop():
        return None

    coro = noop()
    assert background.run_detached(coro, label="test") is None
    with pytest.raises(RuntimeError):
        coro.send(None)  # already closed


# ───────────────────────────────────────────────────────────────────────────
# The booking path
# ───────────────────────────────────────────────────────────────────────────

class _FakeBooking:
    provider_booking_id = "1759393370"
    practitioner_name = "Theorem Wellness Clinics Alcester."

    def __init__(self, start):
        self.start_time = start


def _install_fake_booking(monkeypatch, *, owner_raises=False, gate=None, calls=None):
    """Drive the real _book_appointment_acuity with a fake diary + notifiers."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.tools import receptionist_tools as rt

    start = datetime(2026, 8, 25, 15, 0, tzinfo=ZoneInfo("Europe/London"))

    class _Adapter:
        async def create_booking(self, request):
            return _FakeBooking(start)

    monkeypatch.setattr(rt, "_get_acuity_adapter", lambda: _Adapter())
    monkeypatch.setattr(rt, "_resolve_slot_iso", lambda *a, **k: start)

    async def fake_notify_owner(*a, **k):
        if gate is not None:
            await gate.wait()
        if owner_raises:
            raise RuntimeError("twilio 500 on the owner alert")
        (calls if calls is not None else []).append("owner")

    async def fake_confirm(*a, **k):
        if gate is not None:
            await gate.wait()
        (calls if calls is not None else []).append("patient_sms")

    async def fake_reminders(*a, **k):
        (calls if calls is not None else []).append("reminders")

    import app.notifications.owner_alert as owner_alert
    import app.notifications.booking_sms as booking_sms
    import app.notifications.scheduler as scheduler

    # Each module binds its own copy — patching one is not enough here.
    monkeypatch.setattr(owner_alert, "notify_owner", fake_notify_owner)
    monkeypatch.setattr(booking_sms, "send_booking_confirmation", fake_confirm)
    monkeypatch.setattr(scheduler, "schedule_appointment_reminders", fake_reminders)
    monkeypatch.setattr(
        "app.tools.handoff.send_to_sheet", lambda *a, **k: None, raising=False,
    )
    return start


def _args_and_session():
    args = {
        "patient_name": "Lesley Marton",
        "phone": "07974734502",
        "location": "alcester",
        "service": "physiotherapy assessment",
        "slot_iso": "2026-08-25T15:00:00",
    }
    session = {"clinic_id": "theorem_v3", "call_sid": "CA98557584dc"}
    return args, session


async def test_booking_returns_before_the_notifications_finish(monkeypatch):
    """The defect itself: the caller waited for two Twilio round trips."""
    from app.tools import receptionist_tools as rt

    gate = asyncio.Event()
    calls = []
    _install_fake_booking(monkeypatch, gate=gate, calls=calls)
    args, session = _args_and_session()

    result = await rt._book_appointment_acuity(args, session)

    # Diary write is done and the caller can be told, immediately.
    assert result["success"] is True
    assert result["acuity_booking_id"] == "1759393370"
    # ...while the notifications are still in flight.
    assert background.pending_count() == 1
    assert calls == [], "the caller was made to wait for a notification"

    # And they DO complete once released — detached is not dropped.
    gate.set()
    assert await background.drain(timeout=5.0) == 0
    assert "patient_sms" in calls


async def test_owner_alert_failure_no_longer_reports_the_booking_as_failed(monkeypatch):
    """A committed booking must never be announced as a failure.

    Pre-fix, notify_owner was awaited bare inside the outer `except Exception`,
    so this returned {"success": False, "error": "twilio 500 ..."} for an
    appointment that exists in Acuity.
    """
    from app.tools import receptionist_tools as rt

    calls = []
    _install_fake_booking(monkeypatch, owner_raises=True, calls=calls)
    args, session = _args_and_session()

    result = await rt._book_appointment_acuity(args, session)

    assert result["success"] is True, result
    assert result["acuity_booking_id"] == "1759393370"

    # The patient's own confirmation still goes, despite the owner alert dying.
    assert await background.drain(timeout=5.0) == 0
    assert "patient_sms" in calls


async def test_notifications_are_not_registered_with_the_call(monkeypatch):
    """The connection cancels its own task list at teardown.

    A caller hanging up two seconds after "All booked" must not cancel their
    own confirmation text, so these tasks are owned by the process.
    """
    from app.tools import receptionist_tools as rt

    calls = []
    _install_fake_booking(monkeypatch, calls=calls)
    args, session = _args_and_session()

    await rt._book_appointment_acuity(args, session)

    tasks = [t for t in background._PENDING]
    assert len(tasks) == 1
    assert tasks[0].get_name().startswith("bg:"), tasks[0].get_name()
    assert await background.drain(timeout=5.0) == 0
