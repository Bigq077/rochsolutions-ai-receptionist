"""A booking that reached the provider must be recorded as one.

Jules's 2026-07-25 sweep captured 25 calls. The demo calendar held real events.
Every single obs row read:

    reason: 'caller_hung_up'   booking_confirmed: None   acuity_booking_id: None

The bookings happened; nothing recorded that they had. `booking_confirmed = True`
was written only in flow.py (6 sites) and never on the v3 media-streams tool
path, so `connection.py`'s teardown derivation fell through to "caller_hung_up"
on every completed booking.

The giveaway was one record holding `success=True` AND `reason='caller_hung_up'`
simultaneously: `success` reads `confirmation_sms_sent` (which the tool path does
set), `reason` reads `booking_confirmed` (which it did not). `None` rather than
`False` because media_streams/session.py seeds the key as None.

Consequences, all confirmed in the code: session["call_outcome"] never becomes
"booked"; the dropped-call owner alert counts booking_confirmed among the signals
that suppress it; and — worst — the durable record contained NO evidence a
booking existed for any clinic on Google Calendar, because those set
`calendar_event_id`, which was captured nowhere. Booking integrity, bar #1 of
CLAUDE.md §6, was unmeasurable.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from app.call_logger import CallLogger


# ─────────────────────────────────────────────────────────────────────────
# Structural invariant — the bug class, not just the instance
#
# Testing the tool functions end-to-end means mocking an entire calendar write.
# The defect was structural: a booking path that tells the provider "yes" but
# never tells the session. So assert it directly — any function that marks a
# booking as created must also record it. This is what catches the NEXT booking
# path added without the flag.
# ─────────────────────────────────────────────────────────────────────────
def _functions_assigning(module, key: str, value=None) -> set:
    """Names of top-level functions containing `session[key] = value`.

    `value=None` matches any assigned value.
    """
    tree = ast.parse(inspect.getsource(module))
    found = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            for target in sub.targets:
                if not (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "session"):
                    continue
                sl = target.slice
                if not (isinstance(sl, ast.Constant) and sl.value == key):
                    continue
                if value is None:
                    found.add(node.name)
                elif isinstance(sub.value, ast.Constant) and sub.value.value == value:
                    found.add(node.name)
    return found


def test_every_confirmed_booking_path_records_the_booking():
    """`calendar_status = "created"` means the provider accepted it. Any function
    that says so must also set booking_confirmed, or the call is recorded as
    abandoned."""
    from app.tools import receptionist_tools as rt

    creates = _functions_assigning(rt, "calendar_status", "created")
    records = _functions_assigning(rt, "booking_confirmed")

    assert creates, "no confirmed-booking path found — did calendar_status change?"
    missing = creates - records
    assert not missing, (
        f"booking paths that mark a booking created but never set "
        f"booking_confirmed: {sorted(missing)} — every booking they make will be "
        f"recorded as reason='caller_hung_up'"
    )


def test_provisional_path_is_deliberately_excluded():
    """A DECISION, not an oversight — do not 'fix' this without asking.

    _book_appointment_provisional writes a provisional hold (calendar_status =
    "provisional") and suppresses the caller confirmation SMS. Marking it
    booking_confirmed would make its `reason` read "booked", which overstates a
    hold that nobody has confirmed. It already sets `provisional_booking=True`,
    which the dropped-call alert reads. It is also a live Vital Edge path, so
    changing its semantics is Quentin's call, not a side effect of this fix.
    """
    from app.tools import receptionist_tools as rt

    provisional = _functions_assigning(rt, "calendar_status", "provisional")
    records = _functions_assigning(rt, "booking_confirmed")

    assert "_book_appointment_provisional" in provisional
    assert "_book_appointment_provisional" not in records


# ─────────────────────────────────────────────────────────────────────────
# The teardown derivation — mirrors connection.py, and is what actually broke
# ─────────────────────────────────────────────────────────────────────────
def _derive(session):
    """Verbatim mirror of connection.py's teardown derivation (~:12691).

    Duplicated rather than imported because it is inline in a 12k-line file. If
    connection.py changes and this drifts, that is worth knowing — the whole
    defect was these two halves disagreeing.
    """
    success = bool(session.get("booking_confirmed")
                   or session.get("confirmation_sms_sent"))
    if session.get("graceful_exit"):
        reason = "graceful_exit"
    elif session.get("booking_confirmed"):
        reason = "booked"
    elif session.get("transfer_attempted"):
        reason = "transferred"
    else:
        reason = "caller_hung_up"
    return success, reason


def test_a_booked_session_derives_reason_booked():
    success, reason = _derive({
        "booking_confirmed": True, "confirmation_sms_sent": True,
    })
    assert (success, reason) == (True, "booked")


def test_the_observed_broken_state_is_reproduced_by_the_old_behaviour():
    """The exact contradiction from call CA81de6e…: success True, reason
    caller_hung_up. This is what the session looked like before the fix — the
    booking succeeded and only confirmation_sms_sent was set."""
    success, reason = _derive({
        "booking_confirmed": None,        # seeded by session.py, never written
        "confirmation_sms_sent": True,    # set by the tool path
        "calendar_event_id": "abc123",    # the booking that did happen
    })
    assert success is True and reason == "caller_hung_up"


def test_seeded_none_is_not_mistaken_for_a_booking():
    success, reason = _derive({"booking_confirmed": None})
    assert (success, reason) == (False, "caller_hung_up")


# ─────────────────────────────────────────────────────────────────────────
# The durable record
# ─────────────────────────────────────────────────────────────────────────
def _record(session):
    return CallLogger("CAtest", session).build_record()


def test_google_calendar_booking_id_is_captured():
    """Clinics on Google Calendar set calendar_event_id, never
    acuity_booking_id. It was captured nowhere, so those bookings left no id in
    the durable record at all."""
    rec = _record({"calendar_event_id": "evt_abc123"})

    assert rec["calendar_event_id"] == "evt_abc123"
    assert rec["acuity_booking_id"] is None


def test_acuity_booking_id_still_captured():
    rec = _record({"acuity_booking_id": "acu_999"})
    assert rec["acuity_booking_id"] == "acu_999"
    assert rec["calendar_event_id"] is None


def test_empty_calendar_event_id_records_as_null_not_blank():
    """The provisional path writes `event_id or ""`. An empty string in an id
    column reads as 'there is an id' — it must be NULL."""
    assert _record({"calendar_event_id": ""})["calendar_event_id"] is None


def test_seeded_none_is_recorded_as_false_not_null():
    """A NULL booking_confirmed is ambiguous: 'no booking' or 'capture didn't
    populate it'? That ambiguity cost an hour of diagnosis on 2026-07-26."""
    rec = _record({"booking_confirmed": None})
    assert rec["booking_confirmed"] is False


def test_booked_session_records_confirmed():
    assert _record({"booking_confirmed": True})["booking_confirmed"] is True


# ─────────────────────────────────────────────────────────────────────────
# The obs row
# ─────────────────────────────────────────────────────────────────────────
def test_calendar_event_id_reaches_the_obs_row():
    from app.obs.store import _row_from_record

    row = _row_from_record(_record({
        "booking_confirmed": True, "calendar_event_id": "evt_abc123",
    }), [])

    assert row.calendar_event_id == "evt_abc123"
    assert row.booking_confirmed is True


def test_column_is_registered_for_additive_migration():
    from app.obs.store import _ADDED_COLUMNS
    assert _ADDED_COLUMNS.get("calendar_event_id") == "VARCHAR(128)"


def test_to_dict_exposes_calendar_event_id():
    """replay / to_scenario / reports all read through to_dict."""
    from app.obs.models import Call
    c = Call(call_sid="CAx", calendar_event_id="evt_1")
    assert c.to_dict()["calendar_event_id"] == "evt_1"


def test_a_booking_is_evidenced_by_at_least_one_id():
    """The property that makes booking integrity measurable at all: a confirmed
    booking must carry an id from whichever provider took it."""
    for session in ({"booking_confirmed": True, "calendar_event_id": "evt_1"},
                    {"booking_confirmed": True, "acuity_booking_id": "acu_1"}):
        rec = _record(session)
        assert rec["booking_confirmed"] is True
        assert rec["calendar_event_id"] or rec["acuity_booking_id"]
