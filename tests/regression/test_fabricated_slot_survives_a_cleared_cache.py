"""
Regression (CA166de2a99b97322f3d7ead3645b97d86, Theorem, 2026-08-10 15:00): a
slot that existed on no calendar reached Acuity four times.

The model had invented "Wednesday the 12th at four in the afternoon" from a slot
list belonging to Wednesday the 19th. `_resolve_slot_iso` is the backstop for
exactly that — turn_handler's booking-readback gate cites it in a comment as the
reason "a hallucinated slot is rejected and forced back to a real offered slot".

It was not rejected. The verification loop only ran `if s and offered_check`, and
the slot cache is cleared at the top of every turn ("[ms_llm] slot cache cleared
on new turn"), so by the time book_appointment fired there was nothing to verify
against — and the `elif` fell through to *accept the ISO as-is*. The state the
guard treats as "direct calendar booking, nothing to check" is also the state
every ordinary booking is in a turn or two after the lookup.

    15:00:11  book_appointment slot_iso=2026-08-12T16:00:00
    15:00:11  Acuity 400: "The time 2026-08-12T16:00:00+01:00 is not an
              available time slot"
    …×4, each one an owner manual-followup SMS.

Note what is NOT in the log: any `_resolve_slot_iso: ISO ... not in offered
slots` line. It never reached the check.

The fix distinguishes the two states with a call-scoped flag set only when a
check_availability result actually carried slots. A blocked check returns an
error dict and must not arm it — a guard's refusal is not evidence that the
diary was ever read.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _note_availability_seen
from app.tools.receptionist_tools import _resolve_slot_iso

# The 19th's slots — the only real availability in the call.
REAL_SLOTS = [
    {"start": "2026-08-19T14:00:00+01:00"},
    {"start": "2026-08-19T15:00:00+01:00"},
    {"start": "2026-08-19T16:00:00+01:00"},
]

# What the model invented, spoken to the caller and sent to Acuity.
FABRICATED = "2026-08-12T16:00:00"


def test_a_fabricated_slot_is_refused_once_the_cache_has_been_cleared():
    """The defect, exactly: slots were offered earlier in the call, the per-turn
    cache has since been wiped, and the ISO matches nothing."""
    session = {"_slots_offered_this_call": True}
    with pytest.raises(ValueError):
        _resolve_slot_iso(FABRICATED, session)


def test_the_same_slot_is_refused_while_the_real_list_is_still_present():
    """Unchanged behaviour, pinned: with the list in hand the fabrication was
    always caught. The fix must not be the only thing catching it."""
    session = {"last_offered_slots": REAL_SLOTS, "_slots_offered_this_call": True}
    with pytest.raises(ValueError):
        _resolve_slot_iso(FABRICATED, session)


def test_a_real_offered_slot_still_books():
    session = {"last_offered_slots": REAL_SLOTS, "_slots_offered_this_call": True}
    assert _resolve_slot_iso("2026-08-19T16:00:00+01:00", session).hour == 16


def test_a_slot_only_in_available_days_still_books():
    """available_days is checked at 1b but could not OPEN the block, so a session
    holding days-but-no-slots skipped verification entirely — and would now hit
    the fail-closed path and refuse a REAL slot. Both halves matter: this is the
    booking the fix must not cost."""
    session = {
        "_slots_offered_this_call": True,
        "available_days": [{"date": "2026-08-19", "slots": REAL_SLOTS}],
    }
    assert _resolve_slot_iso("2026-08-19T15:00:00+01:00", session).hour == 15


def test_a_direct_booking_with_no_lookup_is_unaffected():
    """No availability lookup has ever run — the ISO is all there is and always
    was. This is the path the old accept-as-is branch was written for, and it
    keeps it."""
    assert _resolve_slot_iso("2026-08-19T16:00:00+01:00", {}).hour == 16


def test_a_real_result_arms_the_flag():
    session = {}
    assert _note_availability_seen(
        session, {"available_days": [{"date": "2026-08-19", "slots": REAL_SLOTS}]}
    ) is True
    assert session["_slots_offered_this_call"] is True


@pytest.mark.parametrize(
    "result,why",
    [
        ({"error": "booking_details_already_complete"}, "the seven blocked checks"),
        ({"status": "slot_already_confirmed"}, "the slot-locked guard"),
        ({"available_days": []}, "a genuine empty day"),
        (None, "a tool that returned nothing"),
        ("not a dict", "a malformed result"),
    ],
)
def test_what_must_not_arm_the_flag(result, why):
    """A guard's refusal is not a reading of the diary. If a blocked result armed
    the flag, a clinic whose lookup never ran would start refusing legitimate
    direct bookings — the fix paying for itself with the defect it prevents."""
    session = {}
    assert _note_availability_seen(session, result) is False, why
    assert session.get("_slots_offered_this_call") is None, why


def test_the_flag_is_never_disarmed_by_a_later_blocked_check():
    """The whole point is that it outlives the per-turn cache. In the call, the
    real lookup came first and seven blocked ones followed it."""
    session = {}
    _note_availability_seen(session, {"available_days": [{"date": "2026-08-19"}]})
    _note_availability_seen(session, {"error": "booking_details_already_complete"})
    assert session["_slots_offered_this_call"] is True


def test_index_selection_still_resolves_after_the_change():
    """The model answers "1" when the caller picks by number. That path reads
    last_offered_slots directly and must be untouched."""
    session = {"last_offered_slots": REAL_SLOTS, "_slots_offered_this_call": True}
    assert _resolve_slot_iso("1", session).hour == 14
