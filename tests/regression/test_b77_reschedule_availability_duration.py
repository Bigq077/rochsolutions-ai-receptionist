# tests/regression/test_b77_reschedule_availability_duration.py
"""
B-77 — a reschedule verified one duration and wrote another.

JV `CAac066043` (21 Aug). A 60-minute Sports Massage was being moved.

    21:58:36  lookup_patient      -> appointment_type: "Sports Massage"
    21:59:05  check_availability  -> service: "msk_initial_assessment"   (40 min)
    21:59:07  slots offered       -> 16:30, 17:15, 18:00, 18:45, 19:30, 20:15
    21:59:52  reschedule_appointment writes the appointment's TRUE 60 minutes
    diary     -> Thursday 27 August, 17:15 - 18:15

Two independent things were wrong:

  1. `generate_candidate_slots` emits `(start, start + duration)` and
     `filter_free_slots` overlap-tests THAT interval. At 40 minutes only
     `[17:15, 17:55)` was ever checked against the calendar. The event that got
     written runs to 18:15 - twenty minutes never collision-tested against
     anything.
  2. The stride is `increment_min or (duration + break)`. At 40+5 the grid steps
     45 minutes. At the true 60+5 it steps 65, so **17:15 is not an offered
     start time for this appointment at all.**

Second occurrence, worse direction: `CAe84b871b` searched `sports_massage`,
defaulted to the shortest option (30), and wrote 60 into Monday 16:30.

Cause. `f46dd24` ("a 90-minute booking went into the diary as 60") made
`_exec_reschedule_appointment` ignore the model's `duration_minutes` and use the
original event's true length. That is right and stays - but before it, search and
write agreed (both used the argument), and afterwards the search was never
taught to match. Same shape as B-75 and B-75c: fixed for `book_appointment`,
never carried across to `reschedule_appointment`.

Why fixing the OFFER is sufficient. `_resolve_slot_iso` refuses any ISO that
matches no offered slot, once any availability lookup has run this call (added
after a hallucinated slot reached Acuity four times). So the write can only land
on a slot `check_availability` produced. Correct the offer and the write is
correct by construction - no extra call on the write path, and no new way to
refuse a legitimate move.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.tools import receptionist_tools as rt
from app.tools import calendar_google as cg
from app.tools.receptionist_tools import (
    LOOKUP_DURATION_KEY,
    LOOKUP_PURPOSE_KEY,
    _gcal_event_duration_min,
    _reschedule_busy_block,
    _same_interval,
)


DAY = "2026-08-27"
ARGS = {
    "service": "msk_initial_assessment",   # 40 min - what the model actually sent
    "location": "bolton",
    "after_date": DAY,
    "day_window": 1,
}
# The appointment being moved: a 60-minute Sports Massage.
APPT_60 = {"start": {"dateTime": f"{DAY}T16:30:00+01:00"},
           "end": {"dateTime": f"{DAY}T17:30:00+01:00"}}


def _session(**over):
    s = {"clinic_id": "jv_v1"}
    s.update(over)
    return s


def _rescheduling(iso: str = "2026-08-24T16:30:00+01:00", dur: int = 60, **over):
    """A session mid-reschedule, exactly as lookup_patient leaves it."""
    return _session(**{
        LOOKUP_PURPOSE_KEY: "reschedule",
        "_lookup_appointment_datetime": iso,
        LOOKUP_DURATION_KEY: dur,
        **over,
    })


async def _check(session, args=None, busy=None):
    """Drive the REAL executor. Google is mocked; nothing else is."""
    def _fb(tokens, s, e, cal):
        return list(busy or [])
    with patch.object(rt, "_get_tokens",
                      new=lambda *a, **k: asyncio.sleep(0, result={"t": 1})), \
         patch.object(cg, "freebusy", new=_fb), \
         patch.object(rt, "_all_day_blocks_for_window",
                      new=lambda *a, **k: asyncio.sleep(0, result=[])), \
         patch.object(rt, "_save_gcal_tokens",
                      new=lambda *a, **k: asyncio.sleep(0, result=None)):
        return await rt._exec_check_availability(args or dict(ARGS), session)


def _times(result):
    days = result.get("available_days") or []
    return (days[0].get("slot_times") if days else []) or []


# ══════════════════════════════════════════════════════════════════════════
# 1 — the defect
# ══════════════════════════════════════════════════════════════════════════
async def test_a_reschedule_is_sized_by_the_appointment_not_the_argument():
    """The live call, end to end through the real executor."""
    got = _times(await _check(_rescheduling()))
    assert got == ["16:30", "17:35", "18:40", "19:45"], got


async def test_the_slot_that_was_actually_booked_is_no_longer_offered():
    """17:15 cannot hold a 60-minute appointment, so it must not be offered.

    This is the sharpest statement of the defect: the caller was given a start
    time that does not exist in their own service's grid.
    """
    assert "17:15" not in _times(await _check(_rescheduling()))


async def test_the_old_behaviour_is_pinned_so_the_defect_is_unambiguous():
    """Same arguments, NOT a reschedule -> the 40-minute grid, unchanged.

    This is the exact list the live call logged. If this ever changes, the
    comparison above stops meaning anything.
    """
    got = _times(await _check(_session()))
    assert got == ["16:30", "17:15", "18:00", "18:45", "19:30", "20:15"], got


@pytest.mark.parametrize("dur,expected_first_two", [
    (30, ["16:30", "17:05"]),
    (60, ["16:30", "17:35"]),
])
async def test_the_grid_tracks_the_appointment_length(dur, expected_first_two):
    got = _times(await _check(_rescheduling(dur=dur)))
    assert got[:2] == expected_first_two, got


# ══════════════════════════════════════════════════════════════════════════
# 2 — everything this must NOT change
# ══════════════════════════════════════════════════════════════════════════
async def test_a_plain_new_booking_is_untouched():
    assert _times(await _check(_session())) == [
        "16:30", "17:15", "18:00", "18:45", "19:30", "20:15"
    ]


async def test_a_cancel_lookup_sizes_nothing():
    """`purpose` is also set for cancel. Only reschedule may resize the grid."""
    s = _rescheduling()
    s[LOOKUP_PURPOSE_KEY] = "cancel"
    assert _times(await _check(s)) == [
        "16:30", "17:15", "18:00", "18:45", "19:30", "20:15"
    ]


async def test_a_booking_after_the_move_lands_is_sized_by_the_service_again():
    """_note_write_result clears the purpose on any successful write.

    A caller who moves one appointment and then books a NEW one must get the
    new service's grid, not the old appointment's length.
    """
    s = _rescheduling()
    from app.media_streams import llm_stream as ls
    ls._note_write_result(
        s, "reschedule_appointment",
        {"success": True, "rescheduled_to": "x", "attempted_slot_iso": None},
    )
    assert s.get(LOOKUP_PURPOSE_KEY) is None, "the purpose latch was not cleared"
    assert _times(await _check(s)) == [
        "16:30", "17:15", "18:00", "18:45", "19:30", "20:15"
    ]


@pytest.mark.parametrize("bad", [None, 0, -30, "60", 9 * 60])
async def test_an_unusable_stored_duration_falls_back_to_today(bad):
    """Fail-safe: anything odd degrades to the previous behaviour, never to a
    wrong number."""
    s = _rescheduling()
    if bad is None:
        s.pop(LOOKUP_DURATION_KEY)
    else:
        s[LOOKUP_DURATION_KEY] = bad
    assert _times(await _check(s)) == [
        "16:30", "17:15", "18:00", "18:45", "19:30", "20:15"
    ]


# ══════════════════════════════════════════════════════════════════════════
# 3 — the vacated slot. Required, or the fix regresses same-day moves.
# ══════════════════════════════════════════════════════════════════════════
BUSY_1630 = [{"start": f"{DAY}T16:30:00+01:00", "end": f"{DAY}T17:30:00+01:00"}]


async def test_the_slot_being_vacated_is_not_a_conflict_with_its_own_move():
    """At the TRUE 60 minutes the caller's own appointment blocks more
    candidates than it did at the wrong 40. Without this exclusion, sizing the
    grid correctly would REMOVE same-day options that exist today."""
    s = _rescheduling(iso=f"{DAY}T16:30:00+01:00", dur=60)
    assert "16:30" in _times(await _check(s, busy=BUSY_1630))


async def test_someone_elses_appointment_at_that_time_still_blocks_it():
    """The exclusion must be surgical. If it ever drops a block that is NOT the
    caller's own, this fix becomes a double-booking generator."""
    s = _rescheduling(iso="2026-08-28T09:00:00+01:00", dur=60)
    assert "16:30" not in _times(await _check(s, busy=BUSY_1630))


async def test_a_new_booking_is_never_granted_the_exclusion():
    s = _session()
    assert "16:30" not in _times(await _check(s, busy=BUSY_1630))


def test_the_vacated_block_is_only_built_mid_reschedule():
    base = {LOOKUP_PURPOSE_KEY: "reschedule",
            "_lookup_appointment_datetime": f"{DAY}T17:15:00+01:00",
            LOOKUP_DURATION_KEY: 60}
    assert _reschedule_busy_block(dict(base)) is not None
    for broken in (
        {**base, LOOKUP_PURPOSE_KEY: "cancel"},
        {k: v for k, v in base.items() if k != LOOKUP_PURPOSE_KEY},
        {k: v for k, v in base.items() if k != LOOKUP_DURATION_KEY},
        {**base, LOOKUP_DURATION_KEY: 0},
        {**base, "_lookup_appointment_datetime": "not-a-date"},
        {},
    ):
        assert _reschedule_busy_block(broken) is None


def test_interval_matching_tolerates_drift_but_not_a_different_block():
    a = (datetime.fromisoformat(f"{DAY}T17:15:00+01:00"),
         datetime.fromisoformat(f"{DAY}T18:15:00+01:00"))
    near = (a[0] + timedelta(seconds=30), a[1])
    other = (a[0] + timedelta(hours=1), a[1] + timedelta(hours=1))
    assert _same_interval(a, near)
    assert not _same_interval(a, other)
    assert not _same_interval(a, None)


# ══════════════════════════════════════════════════════════════════════════
# 4 — one definition of "how long is this event"
# ══════════════════════════════════════════════════════════════════════════
def test_the_duration_helper_is_exact_and_fails_safe():
    assert _gcal_event_duration_min(APPT_60) == 60
    for bad in (
        {"start": {"dateTime": f"{DAY}T17:15:00+01:00"}},        # no end
        {"start": {"date": DAY}, "end": {"date": "2026-08-28"}},  # all-day
        {"start": {"dateTime": "x"}, "end": {"dateTime": "y"}},   # unparseable
        {"start": {"dateTime": f"{DAY}T18:15:00+01:00"},
         "end": {"dateTime": f"{DAY}T17:15:00+01:00"}},           # reversed
        {},
    ):
        assert _gcal_event_duration_min(bad) is None


def test_the_write_path_uses_the_same_helper_as_the_search():
    """Two derivations of the appointment's length that must agree is exactly
    the defect. Both callers now share one function."""
    src = inspect.getsource(rt._exec_reschedule_appointment)
    assert "_gcal_event_duration_min(found)" in src
    assert "_oe - _os" not in src, "the inline copy is back - it will drift"


def test_the_lookup_stores_the_duration_and_never_a_stale_one():
    src = inspect.getsource(rt._lookup_patient_gcal)
    assert "LOOKUP_DURATION_KEY" in src, "lookup no longer records the length"
    assert "session.pop(LOOKUP_DURATION_KEY, None)" in src, (
        "an unusable event must CLEAR the stored duration, not inherit the "
        "previous lookup's"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5 — the other two clinics reach none of this
# ══════════════════════════════════════════════════════════════════════════
def test_acuity_and_provisional_return_before_the_grid():
    """Theorem goes to Acuity (which validates server-side) and Vital Edge to the
    published-slot reader. Both return before generate_candidate_slots, so this
    change cannot reach them."""
    src = inspect.getsource(rt._exec_check_availability)
    i_acuity = src.index("_filter_same_day_slots(_acuity_result, session)")
    i_prov = src.index("_check_availability_published(")
    i_dur = src.index("duration_min = _resolve_duration_minutes(")
    i_grid = src.index("generate_candidate_slots(")
    assert i_acuity < i_dur < i_grid, "the Acuity branch no longer returns early"
    assert i_prov < i_dur < i_grid, "the provisional branch no longer returns early"
