"""A sender must report what happened, not what it attempted.

`send_sms` returns the Twilio SID, or **None** — and None covers a SUPPRESSED
send (`SMS_ENABLED` off) exactly as much as a failed one. Two senders in
`booking_sms.py` discarded that return and handed back a flat `True`:

    send_booking_confirmation       — the LOG was made honest on 2026-08-18,
                                      the RETURN was not, so the two lines
                                      contradicted each other in one breath
    send_cancellation_confirmation  — neither half was ever fixed

What it costs. `_reschedule_appointment_*`, the twilio route and the booking
executors all latch ``session["confirmation_sms_sent"]`` on these returns, and
`smart_sms_router` (:305) stands down when that latch is set. So on a suppressed
or failed send the caller lost BOTH the confirmation text and the end-of-call
follow-up that exists to cover exactly that case — while the log read healthy.

Live evidence: JV call CA38e5603142 (2026-08-18) and Theorem call CAc9b44a5e
(2026-08-23), each logging a confirmation "sent" one millisecond after
"[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)".

Note the patch target. `booking_sms.py` binds its own reference at import time
(`from app.notifications.sms import send_sms`), so patching the SOURCE module
does not reach it — that is the mistake that put six live cancellation texts on
the owner's phone on 8 Aug. tests/conftest.py blocks every binding globally;
these tests patch over that block deliberately, with a fake that never calls
Twilio.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.notifications.booking_sms import (
    send_booking_confirmation,
    send_cancellation_confirmation,
)

_WHEN = datetime(2026, 9, 1, 10, 30)

_SUPPRESSED = None                  # SMS_ENABLED off, or Twilio rejected it
_DELIVERED = "SM0123456789abcdef"   # a real Twilio SID


async def _booking(sid):
    with patch(
        "app.notifications.booking_sms.send_sms",
        new=AsyncMock(return_value=sid),
    ):
        return await send_booking_confirmation(
            patient_phone="+447700900123",
            patient_name="Alex",
            appointment_time=_WHEN,
            location="Alcester",
        )


async def _cancellation(sid, *, is_late=False):
    with patch(
        "app.notifications.booking_sms.send_sms",
        new=AsyncMock(return_value=sid),
    ):
        return await send_cancellation_confirmation(
            patient_phone="+447700900123",
            patient_name="Alex",
            appointment_time=_WHEN,
            is_late_cancellation=is_late,
        )


async def test_booking_confirmation_reports_a_suppressed_send_as_not_sent():
    assert await _booking(_SUPPRESSED) is False, (
        "a suppressed booking confirmation reported itself as sent — the "
        "caller will be latched as already-texted and the follow-up router "
        "will stand down over a text that never went out"
    )


async def test_booking_confirmation_reports_a_real_send_as_sent():
    assert await _booking(_DELIVERED) is True


async def test_cancellation_confirmation_reports_a_suppressed_send_as_not_sent():
    assert await _cancellation(_SUPPRESSED) is False, (
        "a suppressed cancellation confirmation reported itself as sent — the "
        "exact shape logged on Theorem call CAc9b44a5e"
    )


async def test_cancellation_confirmation_reports_a_real_send_as_sent():
    assert await _cancellation(_DELIVERED) is True


async def test_late_cancellation_takes_the_same_honest_path():
    """The late-cancellation branch builds a different body and must not be
    the one place the old flat `True` survives."""
    assert await _cancellation(_SUPPRESSED, is_late=True) is False
    assert await _cancellation(_DELIVERED, is_late=True) is True


@pytest.mark.parametrize("sender", ["booking", "cancellation"])
async def test_a_raising_send_still_reports_failure(sender):
    """The except arm already returned False. Pin it: a fix to the happy path
    must not turn an exception into a silent success."""
    target = "app.notifications.booking_sms.send_sms"
    with patch(target, new=AsyncMock(side_effect=RuntimeError("twilio down"))):
        if sender == "booking":
            got = await send_booking_confirmation(
                patient_phone="+447700900123",
                patient_name="Alex",
                appointment_time=_WHEN,
                location="Alcester",
            )
        else:
            got = await send_cancellation_confirmation(
                patient_phone="+447700900123",
                patient_name="Alex",
                appointment_time=_WHEN,
            )
    assert got is False
