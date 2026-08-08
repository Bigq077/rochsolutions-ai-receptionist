"""No test may send a real text.

8 Aug 2026: a cancel-path regression test put six live cancellation texts on the
repo owner's phone. It had patched `app.notifications.sms.send_sms`, but
`booking_sms.py` binds its own reference at import time, so that copy still
pointed at Twilio and `send_cancellation_confirmation` went straight out.

This asserts the autouse block in tests/conftest.py covers the binding that
actually leaked — not just the one everybody remembers to patch.
"""

import inspect
from datetime import datetime, timedelta

import pytest


def test_every_module_level_binding_is_blocked():
    """Each module that imported send_sms must hold the blocked callable."""
    import app.notifications.sms as sms_mod
    import app.notifications.booking_sms as booking_sms
    import app.notifications.owner_alert as owner_alert
    import app.notifications.smart_sms_router as smart_router

    for mod in (sms_mod, booking_sms, owner_alert, smart_router):
        fn = getattr(mod, "send_sms", None)
        assert fn is not None, f"{mod.__name__} has no send_sms to block"
        assert not inspect.iscoroutinefunction(fn) or "blocked" in repr(fn).lower() or hasattr(fn, "side_effect"), (
            f"{mod.__name__}.send_sms is not the blocked mock — a test using this "
            f"path would text a real phone"
        )


async def test_the_exact_call_that_leaked_sends_nothing(block_outbound_sms):
    """`send_cancellation_confirmation` is what reached the owner's phone.

    Drive it directly. If the block ever regresses, this test texts whatever
    number is below — so it uses an unroutable test number, never a real one.
    """
    from app.notifications.booking_sms import send_cancellation_confirmation

    await send_cancellation_confirmation(
        patient_phone="+447700900000",  # Ofcom reserved drama/test range
        patient_name="Test Patient",
        appointment_time=datetime.now() + timedelta(days=2),
        clinic_name="Test Clinic",
        clinic_phone="+447700900001",
    )
    assert block_outbound_sms, "send_cancellation_confirmation bypassed the block"
    assert block_outbound_sms[0]["to"] == "+447700900000"


@pytest.mark.parametrize("mod_name", [
    "app.notifications.booking_sms",
    "app.notifications.owner_alert",
    "app.notifications.smart_sms_router",
])
def test_new_import_style_bindings_are_caught(mod_name):
    """A module that adds `from app.notifications.sms import send_sms` gets its
    own copy, invisible to a patch of the source module. If a new one appears,
    it must be added to the conftest target list — this test names the failure."""
    import importlib

    mod = importlib.import_module(mod_name)
    assert hasattr(mod, "send_sms"), (
        f"{mod_name} no longer binds send_sms — remove it from the conftest "
        f"target list, or the block is silently guarding nothing"
    )
