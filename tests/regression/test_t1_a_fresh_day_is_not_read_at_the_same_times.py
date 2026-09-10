# tests/regression/test_t1_a_fresh_day_is_not_read_at_the_same_times.py
"""
T1 - CA5e14516b, 9 Sep 2026, northgate, build 8e838f0f. Judge 2, tagged
`dead_end` + `booking_error`.

    caller: "do you have anything wednesday around 12"
    Susie : Wednesday 16th -- eight in the morning / ten past twelve /
            twenty past four
    caller: "what about monday at 12"
    Susie : Monday 14th   -- eight in the morning / ten past twelve /
            twenty past four

The second readout carried no information. He stopped asking.

WHY. B-116's "already heard" is a set of DATED ISO starts, so 2026-09-16T08:00
and 2026-09-14T08:00 are different members of it. On a day the caller has not
heard, every slot is therefore unheard, the pool is the whole day, and `_spread`
picks by POSITION -- and northgate's grid is a uniform 50-minute ladder from
08:00, so the same positions are the same clock times on every day.

PRE-EXISTING. The raw B-116 selection was [0, 10, 11] on both days before D8's
pin touched it; D8 then swapped 12:10 in for 17:10 on both, because he had asked
for twelve on both. Neither is the 9 Sep fixes' bill.

These tests drive `choose_presented_indices` itself. `assert "T1" in source`
would have passed against every one of the three inert changes shipped on 9 Sep.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    REQUESTED_TIMES_KEY,
    choose_presented_indices,
    record_spoken_slots,
)

WED = "2026-09-16"
MON = "2026-09-14"

#: northgate's real grid: 50-minute steps from 08:00. The uniformity is the
#: whole point -- position and clock time are the same fact here.
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
        "day_label": "Wednesday 16th September" if day_iso == WED
        else "Monday 14th September",
        "slot_times": times,
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": _slots(day_iso, times),
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _clocks(day, idx):
    return [day["slot_times"][i] for i in idx]


def _after_hearing(day_iso, heard_clocks, **extra):
    """A session in the state the live call was in: `heard_clocks` spoken on
    `day_iso`, and `available_days` still holding that same day.

    Built through `record_spoken_slots`, the real writer, so this test cannot
    stay green against a change that moves what the reader trusts (B-101/B-102
    are both about exactly that disagreement).
    """
    session = {"available_days": [_day(day_iso)], **extra}
    record_spoken_slots(session, _slots(day_iso, heard_clocks))
    return session


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_a_second_day_is_not_read_at_the_times_the_first_day_was():
    """The exhibit, without D8 in the way: no clock time repeats."""
    heard = ["08:00", "12:10", "16:20"]
    session = _after_hearing(WED, heard)

    monday = _day(MON)
    spoken = _clocks(monday, choose_presented_indices(session, monday, LIMIT))

    assert len(spoken) == LIMIT, spoken
    assert not set(spoken) & set(heard), spoken


def test_the_two_readouts_share_at_most_one_clock_time_with_d8_live():
    """The call as it actually happened. He asked for twelve BOTH times, so D8
    is entitled to pin 12:10 on both -- and only that one may repeat."""
    heard = ["08:00", "12:10", "16:20"]
    session = _after_hearing(WED, heard, **{REQUESTED_TIMES_KEY: ["12:00"]})

    monday = _day(MON)
    spoken = _clocks(monday, choose_presented_indices(session, monday, LIMIT))

    assert "12:10" in spoken, spoken               # D8 still wins
    assert len(set(spoken) & set(heard)) == 1, spoken


def test_the_readout_is_still_limit_real_bookable_slots_in_order():
    """The readout is still `limit` real slots off this day, in order."""
    session = _after_hearing(WED, ["08:00", "12:10", "16:20"])
    monday = _day(MON)
    idx = choose_presented_indices(session, monday, LIMIT)

    assert idx == sorted(set(idx)), idx
    assert all(0 <= i < len(GRID) for i in idx), idx
    assert all(c in GRID for c in _clocks(monday, idx))


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_the_same_day_rule_is_untouched():
    """A caller asking what else there is THAT day gets B-116's answer exactly
    as it was: the unheard times on the day, and nothing already heard."""
    heard = ["08:00", "12:10", "16:20"]
    session = _after_hearing(WED, heard)

    wednesday = _day(WED)
    spoken = _clocks(wednesday, choose_presented_indices(session, wednesday, LIMIT))

    assert not set(spoken) & set(heard), spoken
    assert len(spoken) == LIMIT, spoken


def test_a_first_lookup_is_unchanged():
    """Nothing heard yet -- the commonest case by far. B-116's own answer."""
    monday = _day(MON)
    assert choose_presented_indices({}, monday, LIMIT) == \
        choose_presented_indices({"available_days": [monday]}, monday, LIMIT)


def test_soonest_still_leads_with_the_earliest_on_every_day():
    """B-137/B-142: "sooner" and "what else" are opposite questions. A caller
    who asked for the earliest wants 08:00 on the second day too, even though
    they heard 08:00 on the first."""
    session = _after_hearing(
        WED, ["08:00", "12:10", "16:20"], day_preference="as soon as possible",
    )
    monday = _day(MON)
    spoken = _clocks(monday, choose_presented_indices(session, monday, LIMIT))

    assert spoken[0] == "08:00", spoken


def test_a_fresh_day_never_starves():
    """Every clock time on this day was heard on another one. There is nothing
    to prefer, and withholding the day is worse than repeating it."""
    session = _after_hearing(WED, GRID)          # the whole ladder heard
    monday = _day(MON)
    idx = choose_presented_indices(session, monday, LIMIT)

    assert len(idx) == LIMIT, idx


def test_the_preference_stands_down_rather_than_spoil_the_spread():
    """`_spread` outranks this rule, and the owner settled that on 1 Sep 2026:
    two slots fifty minutes apart are not a choice a caller experiences as two
    options.

    The first cut of T1 filled a short unheard pool back up from the rest of
    the day, which put 08:00 and 08:50 in one breath -- the exact pairing that
    decision forbids. `test_slot_presentation_cap.py` caught it in two places.

    So the preference is ALL OR NOTHING. With only two unheard clock times
    against a limit of three it stands down entirely, B-116's selection is
    returned untouched, and a clock time repeats across days -- the lesser
    harm, because the caller can still book any of them.
    """
    # Ten of the twelve clock times heard, so only 08:50 and 17:10 are fresh.
    heard = [t for t in GRID if t not in ("08:50", "17:10")]
    session = _after_hearing(WED, heard)
    monday = _day(MON)

    spoken = _clocks(monday, choose_presented_indices(session, monday, LIMIT))
    untouched = _clocks(monday, choose_presented_indices({}, monday, LIMIT))

    assert len(spoken) == LIMIT, spoken
    assert spoken == untouched, (spoken, untouched)


def test_the_preference_applies_when_it_can_fill_the_readout_alone():
    """The other side of the all-or-nothing rule: three unheard clock times
    against a limit of three, so it applies in full and `_spread` still picks
    among them."""
    heard = [t for t in GRID if t not in ("08:50", "12:10", "17:10")]
    session = _after_hearing(WED, heard)
    monday = _day(MON)

    spoken = _clocks(monday, choose_presented_indices(session, monday, LIMIT))

    assert set(spoken) == {"08:50", "12:10", "17:10"}, spoken


def test_a_desynchronised_day_is_not_reordered():
    """B-116's rule for arrays it cannot prove parallel is a chronological
    readout, not a cleverer one: speaking one slot's label against another's
    time names an appointment the caller cannot book."""
    session = _after_hearing(WED, ["08:00", "12:10", "16:20"])
    broken = _day(MON)
    broken["slot_times_spoken"] = broken["slot_times_spoken"][:-1]

    assert choose_presented_indices(session, broken, LIMIT) == [0, 1, 2]
