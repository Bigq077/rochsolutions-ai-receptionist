"""`confirmation_sms_sent` must record what HAPPENED, not what was attempted.

The end-of-call follow-up router stands down on this latch. Setting it
unconditionally is how a caller ends up with NO confirmation text AND no
follow-up, over a log line that reads "sent".

Three ways the send does not happen, all of which used to latch anyway:

  * SMS_ENABLED is off, so `send_sms` returns None and the helper returns False
  * the send raised, and the surrounding `except` swallowed it as non-fatal
  * the guard around the call was false, so nothing was attempted at all

`_reschedule_appointment_acuity` already latched only on success. The two
Google-Calendar executors did not, and `_exec_cancel_appointment` carried the
identical shape — fixing only the reschedule one would have left its twin live.

Two halves, and BOTH are needed. A guarded latch is meaningless if the helper
returns a flat True (which is what jv_v2 and vitaledge-onboarding still do),
and a truthful helper is meaningless if the call site throws the value away.
So the executors are asserted at the source contract and the helpers are
driven for real.
"""

import asyncio
import inspect
import re
from datetime import datetime, timedelta

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

_UNCONDITIONAL = re.compile(r'\n    session\["confirmation_sms_sent"\] = True')


def _when(h=11):
    return (datetime.now(LONDON_TZ) + timedelta(days=3)).replace(
        hour=h, minute=0, second=0, microsecond=0)


# -- half one: the call sites keep the value and guard the latch -------------

def test_reschedule_latch_is_guarded_not_unconditional():
    s = inspect.getsource(rt._exec_reschedule_appointment)
    assert "if _resched_sms_sent:" in s, "reschedule latch is not guarded"
    assert not _UNCONDITIONAL.search(s), (
        "an unconditional latch is still present in the reschedule executor"
    )


def test_cancel_latch_is_guarded_not_unconditional():
    s = inspect.getsource(rt._exec_cancel_appointment)
    assert "if _cancel_sms_sent:" in s, "cancel latch is not guarded"
    assert not _UNCONDITIONAL.search(s), (
        "an unconditional latch is still present in the cancel executor"
    )


def test_both_executors_capture_the_helper_return():
    r = inspect.getsource(rt._exec_reschedule_appointment)
    c = inspect.getsource(rt._exec_cancel_appointment)
    assert "_resched_sms_sent = bool(await send_reschedule_confirmation(" in r
    # the provisional branch texts through send_sms directly
    assert "_send_sms_caller(" in r and "_resched_sms_sent = bool(" in r
    assert "_cancel_sms_sent = bool(await send_cancellation_confirmation(" in c


def test_the_skipped_branch_logs_instead_of_latching():
    """No appointment time on record = nothing sent. Say so, do not latch."""
    r = inspect.getsource(rt._exec_reschedule_appointment)
    c = inspect.getsource(rt._exec_cancel_appointment)
    assert "reschedule confirmation SMS SKIPPED" in r
    assert "cancellation confirmation SMS SKIPPED" in c


# -- half two: the helpers report the truth ---------------------------------

def test_a_suppressed_send_reports_false(monkeypatch):
    """SMS_ENABLED off: send_sms returns None, so the helper returns False."""
    from app.notifications import booking_sms

    async def _no_sid(*a, **k):
        return None

    monkeypatch.setattr(booking_sms, "send_sms", _no_sid)
    assert asyncio.run(booking_sms.send_reschedule_confirmation(
        patient_phone="+447700900001", patient_name="Ana",
        old_time=_when(), new_time=_when(14), location="Bolton",
    )) is False


def test_a_real_send_reports_true(monkeypatch):
    from app.notifications import booking_sms

    async def _sid(*a, **k):
        return "SM123"

    monkeypatch.setattr(booking_sms, "send_sms", _sid)
    assert asyncio.run(booking_sms.send_reschedule_confirmation(
        patient_phone="+447700900001", patient_name="Ana",
        old_time=_when(), new_time=_when(14), location="Bolton",
    )) is True


def test_cancellation_helper_reports_the_truth_too(monkeypatch):
    from app.notifications import booking_sms

    async def _no_sid(*a, **k):
        return None

    monkeypatch.setattr(booking_sms, "send_sms", _no_sid)
    assert asyncio.run(booking_sms.send_cancellation_confirmation(
        patient_phone="+447700900001", patient_name="Ana",
        appointment_time=_when(),
    )) is False
