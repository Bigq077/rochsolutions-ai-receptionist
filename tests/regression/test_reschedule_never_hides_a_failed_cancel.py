"""A reschedule whose cancel failed is HALF-done, and must never read as done.

`_reschedule_appointment_acuity` books the new slot first, then cancels the
original. If that cancel fails the caller is in the diary twice — and the old
code logged one warning and returned `success: True` anyway.

That is not theoretical. On 2026-08-23 a real caller was told "that's you
rescheduled", kept a live appointment he had stopped expecting, and the clinic
was never told. Three separate things went wrong off one failed cancel:

  1. the tool claimed success, so the model narrated a completed move;
  2. the confirmation SMS was skipped in silence, because its `old_time_str`
     guard depends on the cancel having succeeded;
  3. `confirmation_sms_sent` was latched anyway, so the end-of-call follow-up
     router logged "already sent during call" and stood down — over a text
     that had never been sent.

These tests pin all three. The happy path is asserted alongside so that a fix
which simply hard-fails every reschedule cannot pass.
"""

import pytest

import app.tools.receptionist_tools as rt


def _session() -> dict:
    return {
        "clinic_id": "theorem_v3",
        "twilio_to": "+447380841468",
        "selected_location": "alcester",
        "reschedule_appt_id": "1748067711",
        "rc_appointment_confirmed": True,
        "_lookup_patient_name": "Keiran Willis",
        "reschedule_appt_datetime": "2026-08-24T10:00:00+01:00",
    }


def _args() -> dict:
    return {
        "patient_name": "Unknown",
        "phone": "07564202418",
        "location": "alcester",
        "new_slot_iso": "2026-09-08T11:00:00",
    }


@pytest.fixture
def stub_provider(monkeypatch):
    """Stub the book/cancel executors and capture every outbound side effect."""
    calls = {"sms": [], "owner": []}

    async def fake_book(args, session):
        return {
            "success": True,
            "booked_slot": "Tuesday 08 September at 11:00",
            "location": "Alcester",
            "acuity_booking_id": "1758978440",
        }

    async def fake_sms(**kwargs):
        calls["sms"].append(kwargs)
        return True

    async def fake_notify(session, **kwargs):
        calls["owner"].append(kwargs)
        return True

    monkeypatch.setattr(rt, "_book_appointment_acuity", fake_book)
    monkeypatch.setattr(rt, "_resolve_slot_iso", lambda iso, session: __import__(
        "datetime").datetime.fromisoformat("2026-09-08T11:00:00+01:00"))

    import app.notifications.booking_sms as booking_sms
    import app.notifications.owner_alert as owner_alert
    monkeypatch.setattr(booking_sms, "send_reschedule_confirmation", fake_sms)
    monkeypatch.setattr(owner_alert, "notify_owner", fake_notify)

    return calls


@pytest.mark.asyncio
async def test_failed_cancel_is_not_reported_as_success(monkeypatch, stub_provider):
    """The tool must not return success when the original is still in the diary."""

    async def fake_cancel(args, session):
        return {
            "success": False,
            "error": "Cancellation failed. Please ask the caller to call the clinic directly.",
        }

    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)

    session = _session()
    result = await rt._reschedule_appointment_acuity(_args(), session)

    assert result.get("success") is not True, (
        "reschedule reported success while the original appointment was still "
        "live — this is what tells the caller the move is done and leaves them "
        "double-booked"
    )
    assert result.get("original_cancelled") is False
    # The new slot really was taken; the payload must say so, or the model will
    # imply nothing happened and the caller may try to book a third time.
    assert result.get("new_slot_booked")
    assert "reschedule" not in str(session.get("calendar_status", "")).replace(
        "reschedule_partial_duplicate", ""
    ), f"calendar_status must not read as a clean reschedule: {session.get('calendar_status')!r}"


@pytest.mark.asyncio
async def test_failed_cancel_does_not_latch_the_sms_flag(monkeypatch, stub_provider):
    """`confirmation_sms_sent` must never be set for a text that never went out.

    The end-of-call follow-up router reads this flag and stands down when it is
    set. Latching it on a skipped send is how a caller ended up with neither an
    in-call text nor a follow-up.
    """

    async def fake_cancel(args, session):
        return {"success": False, "error": "Cancellation failed."}

    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)

    session = _session()
    await rt._reschedule_appointment_acuity(_args(), session)

    assert not session.get("confirmation_sms_sent"), (
        "confirmation_sms_sent was latched although no reschedule text was sent"
    )
    assert stub_provider["sms"] == [], "no patient text should go out on a half-done move"


@pytest.mark.asyncio
async def test_failed_cancel_escalates_to_a_human(monkeypatch, stub_provider):
    """A duplicate the caller cannot see must reach someone who can fix it."""

    async def fake_cancel(args, session):
        return {"success": False, "error": "Cancellation failed."}

    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)

    session = _session()
    await rt._reschedule_appointment_acuity(_args(), session)

    assert stub_provider["owner"], "nobody at the clinic was told about the duplicate"
    # EXACTLY one. The half-done branch returns before STEP 4's own
    # `event="reschedule"` alert, so the two call sites are mutually
    # exclusive. Two buzzes for one reschedule is the failure mode the
    # Theorem owner-alert suite was written to prevent — Mark reads these,
    # and an alert he has to reconcile against another is worse than one.
    assert len(stub_provider["owner"]) == 1, (
        f"one duplicate is one piece of news: {stub_provider['owner']!r}"
    )
    note = stub_provider["owner"][-1].get("note", "")
    assert "1748067711" in note, (
        f"the orphaned appointment id must be in the alert so it can be found: {note!r}"
    )
    assert session.get("reschedule_orphan_appt_id") == "1748067711"


@pytest.mark.asyncio
async def test_successful_reschedule_still_reports_success(monkeypatch, stub_provider):
    """Guard against a fix that simply fails every reschedule."""

    async def fake_cancel(args, session):
        return {"success": True, "was_at": "2026-08-24T10:00:00+01:00"}

    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)

    session = _session()
    result = await rt._reschedule_appointment_acuity(_args(), session)

    assert result.get("success") is True
    assert result.get("acuity_booking_id") == "1758978440"
    assert session.get("calendar_status") == "rescheduled"
    assert session.get("confirmation_sms_sent") is True
    assert stub_provider["sms"], "the patient should be texted on a completed move"
    # NOT a count assertion. Theorem carries a STEP 4 owner heads-up that
    # this branch does not, so the number of alerts on a completed move is
    # legitimately branch-dependent (1 on Theorem, 0 here). What must hold
    # everywhere is that the half-done escalation did not fire: a completed
    # move must never tell the clinic to go and delete something by hand.
    assert not any(
        "ACTION NEEDED" in (a.get("note") or "") for a in stub_provider["owner"]
    ), f"a successful move raised a duplicate alert: {stub_provider['owner']!r}"


# ── The latch must record the SEND, not the attempt ──────────────────────────
# These do NOT stub send_reschedule_confirmation. The bug lived inside it: it
# discarded send_sms's return and handed back a flat True, so the conditional
# latch above could never actually be false and "records what happened, not what
# was attempted" was not achieved.
#
# NOTE: patch booking_sms.send_sms, NOT sms.send_sms. booking_sms does
# `from app.notifications.sms import send_sms` at module level and holds its own
# binding — patching the source module leaves this one live and TEXTS A REAL
# PHONE.

@pytest.mark.asyncio
async def test_suppressed_text_does_not_latch_the_flag(monkeypatch):
    """SMS_ENABLED off is the live state on Theorem. A suppressed text must not
    tell the end-of-call router the caller has already been contacted."""
    import app.notifications.booking_sms as booking_sms

    async def suppressed(**kwargs):
        return None          # what send_sms returns when SMS_ENABLED is off

    monkeypatch.setattr(booking_sms, "send_sms", suppressed)

    async def fake_book(args, session):
        return {"success": True, "booked_slot": "Tuesday 08 September at 11:00",
                "location": "Alcester", "acuity_booking_id": "1758978440"}

    async def fake_cancel(args, session):
        return {"success": True, "was_at": "2026-08-24T10:00:00+01:00"}

    monkeypatch.setattr(rt, "_book_appointment_acuity", fake_book)
    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)
    monkeypatch.setattr(rt, "_resolve_slot_iso", lambda iso, session: __import__(
        "datetime").datetime.fromisoformat("2026-09-08T11:00:00+01:00"))

    import app.notifications.owner_alert as owner_alert

    async def fake_notify(session, **kwargs):
        return True

    monkeypatch.setattr(owner_alert, "notify_owner", fake_notify)

    session = _session()
    result = await rt._reschedule_appointment_acuity(_args(), session)

    # The move itself genuinely happened — a suppressed text must not fail it.
    assert result.get("success") is True
    assert session.get("confirmation_sms_sent") is not True, (
        "a suppressed text latched the flag — the follow-up router will now "
        "stand down over a message the caller never received"
    )


@pytest.mark.asyncio
async def test_a_real_send_does_latch_the_flag(monkeypatch):
    """The mirror image: guard against a fix that simply never latches."""
    import app.notifications.booking_sms as booking_sms

    async def delivered(**kwargs):
        return "SM_fake_sid"

    monkeypatch.setattr(booking_sms, "send_sms", delivered)

    async def fake_book(args, session):
        return {"success": True, "booked_slot": "Tuesday 08 September at 11:00",
                "location": "Alcester", "acuity_booking_id": "1758978440"}

    async def fake_cancel(args, session):
        return {"success": True, "was_at": "2026-08-24T10:00:00+01:00"}

    monkeypatch.setattr(rt, "_book_appointment_acuity", fake_book)
    monkeypatch.setattr(rt, "_cancel_appointment_acuity", fake_cancel)
    monkeypatch.setattr(rt, "_resolve_slot_iso", lambda iso, session: __import__(
        "datetime").datetime.fromisoformat("2026-09-08T11:00:00+01:00"))

    import app.notifications.owner_alert as owner_alert

    async def fake_notify(session, **kwargs):
        return True

    monkeypatch.setattr(owner_alert, "notify_owner", fake_notify)

    session = _session()
    await rt._reschedule_appointment_acuity(_args(), session)

    assert session.get("confirmation_sms_sent") is True, (
        "a delivered text must latch the flag, or the caller is texted twice"
    )
