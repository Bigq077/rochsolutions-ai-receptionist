"""A promised callback must not be texted as an abandoned booking.

Live call CA5dcd1fed9c2876d815b990a1151def18 (Vital Edge, 2026-08-05 09:54).
The caller rang to reach Jonathan. Susie handled it correctly: she took his
name, called `add_to_waitlist`, pinged Jonathan's mobile, and closed with
"Jonathan will have your details and know you're available at 5pm."

Twenty seconds later the caller received:

    "Hi, you called Vital Edge Therapy earlier and we'd love to help.
     Booking takes less than 2 minutes over the phone — give us a call
     back whenever suits you."

That is `format_abandoned_booking_sms`. It contradicts the promise Susie had
just made — she said the clinic would ring him; the SMS told him to ring the
clinic — and it recasts a handled request as a failed booking attempt.

Root cause: `human_requested` is only ever set by `transfer_to_human`.
`add_to_waitlist` is the tool that actually captures a callback on the live
clinics, and it left no trace the outcome inference could see, so
`infer_call_outcome` fell through every branch to "abandoned".

The correct template already existed and is branch 1 of the router
(`format_callback_confirmation`). Nothing needed writing — only the signal
needed connecting.

The trap: setting `human_requested` alone re-arms the cleanup staff-notify in
connection.py, which texts the *same* practitioner number the waitlist ping
already reached. The fix must leave the practitioner with exactly one SMS.
"""

import inspect

import pytest

from app.media_streams import connection as conn_mod
from app.notifications import smart_sms_router, templates
from app.tools import receptionist_tools
from app.tools.call_summary import infer_call_outcome


# ---------------------------------------------------------------------------
# 1. The tool must leave a trace the outcome inference can see
# ---------------------------------------------------------------------------
@pytest.fixture
def waitlist_session(monkeypatch):
    """Run `add_to_waitlist` with the outbound SMS and Redis stubbed out."""
    sent: list[dict] = []

    async def _fake_send_sms(to, message, **kwargs):
        sent.append({"to": to, "message": message})
        return "SM_fake"

    monkeypatch.setattr("app.notifications.sms.send_sms", _fake_send_sms)
    monkeypatch.setattr(
        "app.clinic_config.get_clinic",
        lambda _cid: {"transfer_phone": "+447545862307", "practitioner": "Jonathan"},
    )
    monkeypatch.setattr("app.storage.redis_store.redis_client", None, raising=False)

    # phone_confirmed is what a caller who reached the waitlist has always had
    # in real life — every route into it runs after the number is settled. It
    # became load-bearing for these tests with B-69 (2026-08-20), which put the
    # A1 phone gate on add_to_waitlist: without it the tool now refuses ONCE,
    # so the assertions below would be measuring the gate rather than the ping.
    session: dict = {"clinic_id": "vital_edge", "phone_confirmed": True}
    return session, sent


async def test_waitlist_marks_the_caller_as_awaiting_a_human(waitlist_session):
    session, _sent = waitlist_session

    result = await receptionist_tools._exec_add_to_waitlist(
        {
            "patient_name": "Raymond Treatall",
            "phone": "07476952176",
            "notes": "Returning Jonathan's call. Available at 5pm today.",
        },
        session,
    )

    assert result.get("success") is True
    assert session.get("human_requested"), (
        "add_to_waitlist promises the caller a human will be in touch, so it "
        "must set the same flag transfer_to_human sets — without it the call "
        "is indistinguishable from someone who hung up mid-booking"
    )


# ---------------------------------------------------------------------------
# 2. That trace must survive into the outcome
# ---------------------------------------------------------------------------
def test_a_promised_callback_is_not_an_abandoned_call():
    session = {"human_requested": True, "_waitlist_pinged": True}

    outcome = infer_call_outcome(session, {})

    assert outcome == "human_requested", (
        f"a caller whose callback was captured and passed to the practitioner "
        f"must not be labelled {outcome!r} — that label drives both the SMS "
        f"the caller receives and the owner's abandonment numbers"
    )


# ---------------------------------------------------------------------------
# 3. The caller must get the message that matches what Susie said
# ---------------------------------------------------------------------------
def test_the_caller_is_told_we_will_ring_them_not_the_reverse():
    body = smart_sms_router._choose_template(
        outcome="human_requested",
        patient_name="Raymond",
        collected={"name": "Raymond Treatall", "phone": "07476952176"},
        insurance_data={},
        handoff_data={},
        faq_data=[],
        session={"clinic_id": "vital_edge"},
        clinic_name="Vital Edge Therapy",
        clinic_phone="+447545862307",
    )

    assert body, "a promised callback must not fall through the router silently"

    abandoned = templates.format_abandoned_booking_sms(
        patient_name="Raymond",
        clinic_name="Vital Edge Therapy",
        clinic_phone="+447545862307",
    )
    assert body != abandoned, (
        "the abandoned-booking copy asks the caller to ring in and book — the "
        "exact opposite of the callback Susie promised on the call"
    )
    assert "call us back" not in body.lower(), (
        "we told this caller we would ring them; the SMS must not reverse that"
    )
    assert "be in touch" in body.lower()


# ---------------------------------------------------------------------------
# 4. The practitioner must still get exactly one SMS
# ---------------------------------------------------------------------------
async def test_the_practitioner_is_pinged_once_not_twice(waitlist_session):
    """The waitlist ping and the cleanup staff-notify hit the same number.

    Setting `human_requested` re-arms the second one. If both fire, Jonathan
    gets two near-identical texts about one caller — which is how a useful
    alert becomes noise he learns to ignore.
    """
    session, sent = waitlist_session

    await receptionist_tools._exec_add_to_waitlist(
        {"patient_name": "Raymond Treatall", "phone": "07476952176"}, session
    )

    # The tool's own ping is fire-and-forget; what matters here is that the
    # cleanup path can tell it already happened.
    assert session.get("_waitlist_pinged") is True

    assert not conn_mod.should_notify_unreached_caller(session), (
        "the practitioner was already pinged by the waitlist tool — the "
        "cleanup notify must stand down rather than send a duplicate"
    )


def test_an_unhandled_human_request_still_reaches_the_practitioner():
    """The suppression must be narrow: no waitlist ping, no suppression."""
    assert conn_mod.should_notify_unreached_caller({"human_requested": True})

    assert not conn_mod.should_notify_unreached_caller(
        {"human_requested": True, "booking_confirmed": True}
    )
    assert not conn_mod.should_notify_unreached_caller(
        {"human_requested": True, "transfer_attempted": True}
    )
    assert not conn_mod.should_notify_unreached_caller({})


def test_cleanup_uses_the_shared_predicate():
    """Guard against the predicate drifting out of the call site it guards."""
    src = inspect.getsource(conn_mod.WebSocketCallHandler._cleanup)
    assert "should_notify_unreached_caller" in src, (
        "cleanup must route its staff-notify decision through the tested "
        "predicate, or this suite stops protecting the real behaviour"
    )
