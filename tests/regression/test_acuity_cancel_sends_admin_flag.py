"""A short-notice cancellation must still reach Acuity.

Every Acuity appointment carries a `canClientCancel` flag that the account
flips to False once the booking falls inside the clinic's minimum-cancellation-
notice window.  `PUT /appointments/<id>/cancel` honours that flag unless the
request carries `admin=true`, so without it Acuity answers a flat 400 and the
cancellation silently does not happen.

That is not an edge case.  "I need to cancel tomorrow's appointment" is the
single most common reason a patient rings, and every one of those cancellations
failed the same way — proven live on 2026-08-23, where two separate calls both
400'd on a next-morning appointment while a fortnight-out booking on the same
account cancelled fine.

The reschedule path is book-new-then-cancel-old, so it inherits this too: a
missing `admin` there leaves the patient double-booked.

These tests pin the query parameter, not the happy path.  A cancel that returns
True while sending no `admin` flag is the exact regression to catch.
"""

import pytest

from app.booking.booking.providers.acuity import AcuityAdapter
from app.booking.booking.exceptions import ProviderUnavailable


def _provider() -> AcuityAdapter:
    return AcuityAdapter(user_id="u", api_key="k", clinic_id="test_clinic")


@pytest.mark.asyncio
async def test_cancel_sends_admin_true():
    """The cancel request must carry admin=true or Acuity enforces the window."""
    provider = _provider()
    seen = {}

    async def fake_request(method, endpoint, **kwargs):
        seen["method"] = method
        seen["endpoint"] = endpoint
        seen["params"] = kwargs.get("params")
        return None

    provider._request_with_retry = fake_request

    assert await provider.cancel_booking("1748067711") is True

    assert seen["method"] == "PUT"
    assert seen["endpoint"] == "/appointments/1748067711/cancel"
    assert seen["params"], (
        "cancel_booking sent no query params at all — without admin=true Acuity "
        "rejects any cancellation inside the clinic's notice window with a 400"
    )
    assert str(seen["params"].get("admin")).lower() == "true", (
        f"expected admin=true in the cancel params, got {seen['params']!r}"
    )


@pytest.mark.asyncio
async def test_cancel_reports_failure_rather_than_swallowing_it():
    """A rejected cancel returns False — callers must be able to tell.

    `_reschedule_appointment_acuity` decides whether the patient is
    double-booked purely on this return value.  A cancel that fails and
    reports True is how a caller gets told "that's you rescheduled" while
    the original appointment is still live in the diary.
    """
    provider = _provider()

    async def fake_request(method, endpoint, **kwargs):
        raise ProviderUnavailable(
            "Acuity request error (400): You cannot cancel this appointment.",
            provider="acuity",
        )

    provider._request_with_retry = fake_request

    assert await provider.cancel_booking("1748067711") is False
