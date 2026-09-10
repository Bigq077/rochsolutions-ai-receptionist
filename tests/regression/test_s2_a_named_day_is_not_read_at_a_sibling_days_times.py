# tests/regression/test_s2_a_named_day_is_not_read_at_a_sibling_days_times.py
"""
S-2 - CA8214b75c, 10 Sep 2026, northgate. "Twenty to ten" was offered for
Monday and again for Tuesday, seventeen seconds apart (09:35:46 -> 09:36:03).

WHY T1 DID NOT COVER IT. T1's rule stands down on a day the caller has already
heard, on the ground that B-116 owns that case:

    if not today or today in {str(s)[:10] for s in spoken}:
        return chosen

That reads as "the caller asked what else there is that day", and for a single
day it is right. But a multi-day readout makes EVERY day it named a heard day.
From the first readout onwards, every day the caller can then ask about takes
this early return, so the cross-day preference never fires again for the rest
of the call -- which is exactly when a caller is comparing days and a repeat is
most audible.

And B-116 cannot cover it either, by construction: its "already heard" is a set
of DATED ISO starts, so it subtracts what was heard ON THIS DAY and has no
notion of a clock time heard on another one.

THE REFINEMENT. B-116's pool is kept exactly as it is -- never offer a time
already heard on that day -- and the preference now operates WITHIN that pool,
choosing clock times unheard on ANY day. Same all-or-nothing guard as T1: it
applies only when the fresh-anywhere subset can fill the readout on its own,
because `_spread` outranks it (owner, 1 Sep 2026).

These tests drive `choose_presented_indices`, the real entry point, through
`record_spoken_slots`, the real writer.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    _choose_presented_times,
    choose_presented_indices,
    record_spoken_slots,
)

MON = "2026-09-14"
TUE = "2026-09-15"

#: northgate's real grid: 50-minute steps from 08:00.
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
LIMIT = 3


def _slots(day_iso, times):
    return [
        {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
        for t in times
    ]


def _day(day_iso, times=None):
    times = list(GRID if times is None else times)
    return {
        "date": day_iso,
        "day_label": "Monday 14th September" if day_iso == MON
        else "Tuesday 15th September",
        "slot_times": times,
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": _slots(day_iso, times),
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _clocks(day, idx):
    return [day["slot_times"][i] for i in idx]


def _after_a_multi_day_readout(**extra):
    """The state the live call was in: an opening readout that named BOTH days,
    at a different clock time on each (T1b already working)."""
    session = {"available_days": [_day(MON), _day(TUE)], **extra}
    record_spoken_slots(session, _slots(MON, ["08:00"]))
    record_spoken_slots(session, _slots(TUE, ["10:30"]))
    return session


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_a_named_day_is_not_read_at_the_times_a_sibling_day_was():
    """The exhibit. Ask about Monday, then about Tuesday: no clock time may
    appear in both answers.

    Before the fix this returned 08:50/16:20/17:10 then 08:00/16:20/17:10 --
    two of three repeated, because both days were "heard" and the preference
    stood down on both.
    """
    session = _after_a_multi_day_readout()

    monday = _clocks(_day(MON), choose_presented_indices(session, _day(MON), LIMIT))
    record_spoken_slots(session, _slots(MON, monday))
    tuesday = _clocks(_day(TUE), choose_presented_indices(session, _day(TUE), LIMIT))

    assert len(monday) == LIMIT, monday
    assert len(tuesday) == LIMIT, tuesday
    assert not set(monday) & set(tuesday), (monday, tuesday)


def test_the_opening_readouts_own_times_are_not_repeated_either():
    """The clock times the opening multi-day readout spoke are heard too, so a
    follow-up on either day may not hand them back."""
    session = _after_a_multi_day_readout()

    monday = _clocks(_day(MON), choose_presented_indices(session, _day(MON), LIMIT))
    record_spoken_slots(session, _slots(MON, monday))
    tuesday = _clocks(_day(TUE), choose_presented_indices(session, _day(TUE), LIMIT))

    assert "08:00" not in monday, monday      # heard on Monday, B-116's own job
    assert "10:30" not in tuesday, tuesday    # heard on Tuesday, likewise
    assert "10:30" not in monday, monday      # heard on TUESDAY -- S-2's job
    assert "08:00" not in tuesday, tuesday


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_b116s_own_pool_is_still_respected():
    """The refinement chooses WITHIN B-116's pool and never outside it: a time
    already heard on the day being read may not come back, whatever its clock
    time did on another day."""
    session = {"available_days": [_day(MON)]}
    heard = ["08:00", "12:10", "16:20"]
    record_spoken_slots(session, _slots(MON, heard))

    spoken = _clocks(_day(MON), choose_presented_indices(session, _day(MON), LIMIT))

    assert not set(spoken) & set(heard), spoken
    assert len(spoken) == LIMIT, spoken


def test_a_single_heard_day_is_byte_identical_to_b116():
    """With only ONE day heard there is no sibling day to be fresh against, so
    the preference must find nothing to do and hand B-116's answer straight
    back. This is `test_the_same_day_rule_is_untouched`'s case, and it must
    stay exactly as it was."""
    session = {"available_days": [_day(MON)]}
    record_spoken_slots(session, _slots(MON, ["08:00", "12:10", "16:20"]))

    assert choose_presented_indices(session, _day(MON), LIMIT) == \
        _choose_presented_times(session, _day(MON), LIMIT)


def test_it_stands_down_rather_than_spoil_the_spread():
    """`_spread` outranks this rule (owner, 1 Sep 2026): two slots fifty
    minutes apart are not a choice. When the fresh-anywhere subset cannot fill
    the readout on its own the preference stands down entirely and a clock time
    repeats across days -- the lesser harm."""
    session = {"available_days": [_day(MON), _day(TUE)]}
    # Everything but 08:50 and 17:10 heard on Tuesday, and 08:00 on Monday, so
    # Monday's B-116 pool is wide but only two clock times are fresh anywhere.
    record_spoken_slots(
        session, _slots(TUE, [t for t in GRID if t not in ("08:50", "17:10")])
    )
    record_spoken_slots(session, _slots(MON, ["08:00"]))

    spoken = choose_presented_indices(session, _day(MON), LIMIT)

    assert spoken == _choose_presented_times(session, _day(MON), LIMIT), spoken
    assert len(spoken) == LIMIT, spoken


def test_soonest_still_leads_with_the_earliest():
    """B-137/B-142: "sooner" and "what else" are opposite questions, and this
    preference stands down for that caller on a heard day exactly as it does on
    a fresh one."""
    session = _after_a_multi_day_readout(day_preference="as soon as possible")

    spoken = _clocks(_day(TUE), choose_presented_indices(session, _day(TUE), LIMIT))

    assert spoken[0] == "08:00", spoken


def test_a_desynchronised_day_is_not_reordered():
    """A day that cannot prove its arrays are parallel gets a chronological
    readout, not a cleverer one -- unchanged on the heard-day path too."""
    session = _after_a_multi_day_readout()
    broken = _day(TUE)
    broken["slot_times_spoken"] = broken["slot_times_spoken"][:-1]

    assert choose_presented_indices(session, broken, LIMIT) == [0, 1, 2]
