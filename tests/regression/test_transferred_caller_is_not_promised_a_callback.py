"""
A caller put through to a person must not be texted a callback promise.

infer_call_outcome() labels two different endings "human_requested": a live
transfer, and a waitlist / callback entry taken on the caller's behalf. Only
the second is owed "someone will be in touch". Call CA82ec06 (2026-08-21) was
transferred live to Mark and, 0.3s before the redirect, texted "you requested
a callback from our team ... someone will be in touch shortly" — a promise of
future contact delivered while the caller was being connected. If Mark
answered, they read it mid-conversation with him.

`transfer_attempted` is the discriminator. It is written at the single point a
leg is actually placed (realtime.py `_do_transfer`, after the REST redirect
succeeds), so a refused, suppressed or targetless transfer leaves it False and
keeps the callback copy — which is right, because in those cases the caller
really is waiting to hear from someone.

should_notify_unreached_caller() already excludes the same flag for the OWNER
alert. This is the caller-facing half of the same rule.
"""

import pytest

from app.notifications import smart_sms_router


def _choose(session, *, name="Alex", outcome="human_requested"):
    return smart_sms_router._choose_template(
        outcome=outcome,
        patient_name=name,
        collected={},
        insurance_data={},
        handoff_data={},
        faq_data=[],
        session=session,
        clinic_name="Theorem Health",
        clinic_phone="01527 123456",
    )


def test_a_transferred_caller_is_not_promised_a_callback():
    """THE regression."""
    msg = _choose({"transfer_attempted": True})
    assert "callback" not in msg.lower(), msg
    assert "be in touch" not in msg.lower(), msg
    assert "put you through" in msg.lower(), msg


def test_a_callback_request_still_promises_contact():
    """The other half must not be swapped — this is not a replacement."""
    msg = _choose({})
    assert "callback" in msg.lower(), msg


def test_a_refused_transfer_keeps_the_callback_promise():
    """
    TRANSFER_DISABLED, no dial target, or a call no longer in-progress all
    return from _do_transfer BEFORE transfer_attempted is written. Nobody was
    dialled, so the caller genuinely is waiting to hear from someone.
    """
    msg = _choose({"request_transfer": True, "human_requested": True})
    assert "callback" in msg.lower(), msg


def test_the_transfer_text_is_true_whether_or_not_the_leg_answered():
    """
    This SMS is sent at the redirect, before the dial outcome is known — the
    log for CA82ec06 shows the text at 16:28:32.621 and the leg ending three
    seconds later. So it may claim nothing about having spoken to anyone, and
    must leave a way back for a caller whose transfer failed.
    """
    msg = _choose({"transfer_attempted": True})
    for promise in ("spoke", "you're now", "connected you to"):
        assert promise not in msg.lower(), msg
    assert "01527 123456" in msg, "no route back for a failed transfer: " + msg


def test_the_unnamed_variant_says_the_same_thing():
    """Most transfers happen before a name is collected — CA82ec06 had none."""
    msg = _choose({"transfer_attempted": True}, name="")
    assert "callback" not in msg.lower(), msg
    assert "be in touch" not in msg.lower(), msg
    assert "01527 123456" in msg


@pytest.mark.parametrize("outcome", ["booked", "cancelled", "out_of_hours"])
def test_the_flag_only_speaks_for_human_requested(outcome):
    """
    transfer_attempted must not leak into other outcomes: a caller who booked
    and was then put through has a booking to be told about.
    """
    msg = _choose({"transfer_attempted": True}, outcome=outcome)
    if msg:
        assert "put you through" not in msg.lower(), (outcome, msg)
