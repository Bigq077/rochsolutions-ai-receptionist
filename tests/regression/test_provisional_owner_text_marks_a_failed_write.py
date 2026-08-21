# tests/regression/test_provisional_owner_text_marks_a_failed_write.py
"""
A provisional clinic's owner must be able to tell, from the text alone, whether
the appointment reached the diary.

Vital Edge books provisionally: Jonathan IS the confirmation step, so the owner
SMS is the only channel that reaches him while the caller could still be rung
back. The calendar write in `_exec_book_appointment_provisional` is wrapped in a
try/except that logs and continues, and the whole write is skipped when the
Google token has expired — so `calendar_written` is False on two different real
paths, neither of which raises.

Until 2026-08-21 `build_booking_request_message` had no notion of that flag, so
the text was byte-identical either way. The owner would have gone looking for an
appointment that was never written, and the only other record — the Sheets row
that spells out "calendar write FAILED" — is behind SHEETS_ENABLED, which
defaults to false.

What this pins is narrow and deliberate: the FAILED text must be
distinguishable, must say the write failed, and must still carry the caller's
name and number, because a text that announces a failure without the details
needed to act on it is no better than the one it replaced.
"""

from datetime import datetime

from app.notifications.owner_notify import build_booking_request_message

CLINIC = {"clinic_id": "vital_edge", "sms_name": "Vital Edge Therapy"}
WHEN = datetime(2026, 8, 25, 14, 0)


def _msg(*, calendar_written):
    return build_booking_request_message(
        clinic=CLINIC,
        patient_name="Dara Okonjo",
        phone="07700900456",
        when=WHEN,
        duration_minutes=90,
        service="Deep Tissue Massage",
        calendar_written=calendar_written,
    )


def test_a_failed_write_is_not_worded_like_a_healthy_booking():
    assert _msg(calendar_written=False) != _msg(calendar_written=True)


def test_a_failed_write_says_so_and_says_what_to_do():
    msg = _msg(calendar_written=False)
    assert "NOT IN YOUR CALENDAR" in msg
    assert "FAILED" in msg
    assert "manually" in msg.lower()


def test_the_failure_leads_the_message():
    # Not cosmetic. The owner acts on the first line of an SMS notification
    # preview; a failure buried under four detail lines is a failure missed.
    first = _msg(calendar_written=False).splitlines()[0]
    assert "NOT IN YOUR CALENDAR" in first


def test_a_failed_write_still_carries_the_details_needed_to_act():
    msg = _msg(calendar_written=False)
    assert "Dara Okonjo" in msg
    assert "07700900456" in msg
    assert "90 min" in msg
    assert "Tue 25 Aug at 14:00" in msg


def test_a_successful_write_is_unchanged_and_makes_no_failure_claim():
    msg = _msg(calendar_written=True)
    assert "FAILED" not in msg
    assert "NOT IN YOUR CALENDAR" not in msg
    assert "Dara Okonjo" in msg
    # The provisional caveat is true on both paths and must survive.
    assert "Not yet confirmed" in msg


def test_the_default_is_the_healthy_wording():
    # Every other caller of this builder predates the flag. None of them may
    # start announcing a failure that did not happen.
    assert build_booking_request_message(
        clinic=CLINIC, patient_name="A", phone="07700900456",
        when=WHEN, duration_minutes=60,
    ) == _msg(calendar_written=True).replace("Dara Okonjo", "A").replace("90 min", "60 min").replace(
        "Deep Tissue Massage", "Deep Tissue Massage"
    )
