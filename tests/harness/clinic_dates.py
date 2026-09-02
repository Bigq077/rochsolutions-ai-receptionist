"""A future date the clinic is actually OPEN on. One definition.

Why this exists, measured 2026-09-02. Five regression files anchor their
synthetic slots on `date.today() + timedelta(days=N)`, deliberately — a test
that pins a literal weekday dies at midnight, which is b55 and is already
paid for. But a relative anchor lands on a DIFFERENT WEEKDAY every day the
suite runs, and Alcester is closed on Saturday and Sunday. On 2 Sep,
`today + 4` was Sunday the 6th: every synthetic slot was removed by the
working-hours filter, and 22 slot-layer regression tests went red without a
line of code changing.

They had been red for some time, which is the part that matters — those files
carry B-93, B-99, B-103, B-108, B-109 and B-116, so the invariants this layer
keeps re-breaking were unenforced while we changed it.

`open_weekday_base` moves forward to a day the clinic's own config says it is
open, so the same test runs the same way whatever day it is run on. Read from
`location_working_hours` rather than hardcoded, because a clinic that opens on
Saturday should not need this file edited.

The +7/+14/+21 offsets those tests use keep the same weekday by construction,
so one check covers every date in the set.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

_WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def clinic_is_open_on(
    day: _dt.date, clinic_id: str = "theorem", location: str = "alcester"
) -> bool:
    """True when `location`'s configured hours have that weekday open."""
    from app.clinic_config import get_clinic

    hours = (get_clinic(clinic_id) or {}).get("location_working_hours") or {}
    per_day = hours.get(location)
    if not isinstance(per_day, dict):
        return True          # no opinion configured — do not filter
    return bool(per_day.get(_WEEKDAY_KEYS[day.weekday()]))


def open_weekday_base(
    min_days_ahead: int = 4,
    clinic_id: str = "theorem",
    location: str = "alcester",
    span_weeks: int = 0,
) -> _dt.date:
    """The first date >= `min_days_ahead` out that the clinic is open on.

    `span_weeks` asks for a date whose +7/+14/... repeats are ALSO open. They
    share a weekday so this is normally free; it is a parameter because a
    clinic could close a single date, and a test that then silently lost one
    of its four dates would be worse than one that fails loudly.
    """
    day = _dt.date.today() + _dt.timedelta(days=max(0, min_days_ahead))
    for _ in range(14):      # a fortnight is more than enough to find one
        if all(
            clinic_is_open_on(day + _dt.timedelta(days=7 * w), clinic_id, location)
            for w in range(span_weeks + 1)
        ):
            return day
        day += _dt.timedelta(days=1)
    raise AssertionError(
        "no open day for {}/{} within a fortnight of {} — check "
        "location_working_hours".format(clinic_id, location, day)
    )


def london(day: _dt.date, hour: int, minute: int = 0):
    """An aware Europe/London datetime, localised the way pytz requires.

    `datetime(..., tzinfo=pytz.timezone("Europe/London"))` silently yields LMT
    — a one-minute offset — instead of GMT/BST. Harmless in these tests today,
    and exactly the kind of thing that becomes a two-hour bug at a DST boundary,
    so it is done properly in one place.
    """
    import pytz

    return pytz.timezone("Europe/London").localize(
        _dt.datetime(day.year, day.month, day.day, hour, minute)
    )


def open_days(
    count: int,
    start_offset: int = 1,
    clinic_id: str = "theorem",
    location: str = "alcester",
) -> list:
    """The next `count` dates the clinic is open, from `start_offset` days out.

    For tests that need a RUN of open days rather than one. Fixed offsets like
    `(1, 2, 3, 4, 9, 10)` were the other half of the 2 Sep rot: they encode a
    six-day sweep, and on any day of the week where two of them land on a
    weekend the sweep silently finds four.
    """
    out: list = []
    day = _dt.date.today() + _dt.timedelta(days=max(0, start_offset))
    for _ in range(count * 3 + 14):
        if len(out) >= count:
            break
        if clinic_is_open_on(day, clinic_id, location):
            out.append(day)
        day += _dt.timedelta(days=1)
    if len(out) < count:
        raise AssertionError(
            "only found {} open day(s) of {} for {}/{}".format(
                len(out), count, clinic_id, location
            )
        )
    return out
