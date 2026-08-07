"""
Regression (O-1): a caller who got most of the way to a booking and then dropped
must still get the abandoned-booking text.

CA6e1024db, theorem_v3, 2026-08-07 10:14–10:16. He gave his name, picked a slot,
typed eleven digits, and hung up before the "shall I go ahead?" turn. Because
availability had been offered, `session["slots_offered"]` was True, a suppression
branch in `_choose_template` returned None, and the one caller on that call who
most needed a text was the only outcome that got none.

The ladder as it stood:

    reached the CTA, dropped before "yes"   → reached_confirmation SMS   ✅
    slot picked, stuck before the CTA       → suppressed                 ❌
    nothing much happened                   → abandoned SMS              ✅

The suppression existed to stop a duplicate going out alongside a confirmation.
Nothing that sends a confirmation can reach `_choose_template`: `booked`,
`cancelled`, `reschedule_failed` and the `confirmation_sms_sent` latch all
return False from `send_smart_followup_sms` first. `test_no_confirmed_outcome_
reaches_the_template_chooser` below pins that, because it is the whole reason
the deletion is safe — if a confirming outcome ever starts reaching the chooser,
this file should go red rather than the duplicate reaching a patient.
"""
from __future__ import annotations

import inspect

import pytest

from app.notifications import smart_sms_router
from app.notifications.smart_sms_router import _choose_template


def _choose(session, collected, outcome="abandoned"):
    return _choose_template(
        outcome        = outcome,
        patient_name   = collected.get("name", "") or "",
        collected      = collected,
        insurance_data = {},
        handoff_data   = {},
        faq_data       = [],
        session        = session,
        clinic_name    = "Theorem Health",
        clinic_phone   = "01234 567890",
    )


# The CA6e1024db session, reduced to the fields the router reads.
CA6E1024DB_SESSION = {
    "clinic_id": "theorem_v3",
    "intent": "booking",
    "slots_offered": True,          # availability was fetched and read out
    "phone_confirmed": True,
    "conversation_history": [
        {"role": "user", "content": "i'd like to book an appointment please"},
        {"role": "user", "content": "john"},
        {"role": "user", "content": "yeah the tuesday one"},
    ],
}
CA6E1024DB_COLLECTED = {
    "name": "John",
    "phone": "+447502211207",
    "patient_type": "new",
}


def test_the_call_that_lost_the_text_now_sends_one():
    msg = _choose(dict(CA6E1024DB_SESSION), dict(CA6E1024DB_COLLECTED))
    assert msg, (
        "CA6e1024db picked a slot and typed a number, then dropped — and got no "
        "SMS at all. Progress toward a booking is the argument FOR the text."
    )
    assert "call" in msg.lower()


@pytest.mark.parametrize(
    "progress_signal",
    [
        "slots_offered",
        "slot_pending_confirmation",
        "slot_confirmed",
        "rc_appointment_confirmed",
        "reschedule_confirmed",
    ],
)
def test_no_single_progress_signal_silences_the_text(progress_signal):
    """
    Each of these alone was enough to suppress. `reschedule_confirmed` is the
    worst of the five: the caller verbally agreed a new time that was never
    written, so they believe they are moved and they are not.
    """
    session = {"clinic_id": "theorem_v3", "intent": "booking", progress_signal: True}
    msg = _choose(session, {"name": "John", "phone": "+447502211207"})
    assert msg, f"{progress_signal}=True suppressed the abandoned SMS"


def test_a_shallow_call_still_gets_the_general_text():
    """The floor case — no progress at all — was already sending, and must keep."""
    msg = _choose({"clinic_id": "theorem_v3"}, {})
    assert msg


def test_a_condition_label_still_reaches_the_labelled_template():
    """
    Deleting the branch above branch 12b must not shadow it: a caller whose
    reason was captured still routes to the condition-label template.
    """
    msg = _choose(
        {"clinic_id": "theorem_v3", "slots_offered": True},
        {"name": "John", "reason": "my knee has been aching for a couple of weeks"},
    )
    assert msg


def test_no_confirmed_outcome_reaches_the_template_chooser():
    """
    The deletion is safe only while every outcome that already sent a text
    returns before `_choose_template` is called. Pin that here.
    """
    src = inspect.getsource(smart_sms_router.send_smart_followup_sms)
    chooser_at = src.index("_choose_template(")
    guard_region = src[:chooser_at]
    for guard in ('"booked"', '"cancelled"', '"reschedule_failed"',
                  'confirmation_sms_sent'):
        assert guard in guard_region, (
            f"{guard} no longer returns before _choose_template — a confirmation "
            f"and an abandoned text can now both be sent for the same call"
        )


def test_the_suppression_helper_is_gone():
    assert not hasattr(smart_sms_router, "_booking_has_progressed"), (
        "the suppression was reintroduced; see the note in _choose_template"
    )
