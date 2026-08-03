"""
Week anchors for the date context handed to the model — ONE implementation.

B-09. Three separate copies of this calculation existed
(`clinic_template_prompt._date_context`, `config._v3_state_block`,
`llm_stream._date_line`) and **all three were wrong the same way**, while a
fourth in `receptionist_tools._extract_week_range` was correct. On Sundays the
two halves of the system therefore disagreed by exactly seven days.

The bug, in all three:

    days_until_sunday = (6 - weekday) % 7          # == 0 on a Sunday
    this_sunday = now + (days_until_sunday if days_until_sunday > 0 else 7)
    next_monday = this_sunday + 1

On a Sunday the ``else 7`` fired, so "this Sunday" became *next* Sunday and
``next_monday`` — literally tomorrow — was reported as **eight days away**. The
model then counted "next Friday" from that anchor and landed on **+12 days**,
which is the symptom `B-09` was filed under. The model's arithmetic was never
the problem; the anchor we handed it was seven days late.

Wrong on Sundays only, which is one day in seven and why it survived two months.

This module is deliberately dependency-free so every caller — prompt builders,
the media-stream config block and the tool layer — can share it without an
import cycle. Same reasoning as `app/name_capture.py`.

**Timezone is explicit.** `llm_stream`'s copy used a bare `date.today()`, i.e.
server-local time. On a UTC container under BST that is a day behind between
23:00 and midnight London. Passing the zone explicitly is correct whether or not
`TZ` happens to be set on the host, so it needs no deployment check to be safe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta

__all__ = ["CLINIC_TZ", "WeekAnchors", "week_anchors", "clinic_now", "clinic_today"]

CLINIC_TZ = "Europe/London"


def _zone(timezone: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(timezone)
    except Exception:                       # pragma: no cover - py<3.9 / no tzdata
        import pytz
        return pytz.timezone(timezone)


def clinic_now(timezone: str = CLINIC_TZ) -> _datetime:
    """Timezone-aware 'now' in the clinic's local time."""
    return _datetime.now(_zone(timezone))


def clinic_today(timezone: str = CLINIC_TZ) -> _date:
    """Today's date in the clinic's local time — never the server's."""
    return clinic_now(timezone).date()


@dataclass(frozen=True)
class WeekAnchors:
    """The four dates every relative-date phrase is counted from.

    Invariants, true on **all seven weekdays** — asserted in
    tests/regression/test_b09_date_anchors.py:

        this_sunday  == today + (6 - weekday)     # Sunday OF THIS WEEK
        next_monday  == this_sunday + 1 day       # therefore always tomorrow
                                                  # when today is a Sunday
        next_sunday  == next_monday + 6 days
    """

    today: _date
    this_sunday: _date
    next_monday: _date
    next_sunday: _date


def week_anchors(today: _date | None = None, timezone: str = CLINIC_TZ) -> WeekAnchors:
    """Return this week's Sunday and next week's Monday/Sunday.

    `today` is injectable so the seven-weekday tests do not have to travel in
    time; production callers leave it None and get clinic-local today.

    The arithmetic has no special case, and that is the point — the special case
    (`else 7`) *was* the bug. `weekday()` is Mon=0 … Sun=6, so:

        Monday  (0): this_sunday = +6, next_monday = +7
        Sunday  (6): this_sunday = +0 (today), next_monday = +1 (tomorrow)

    Matches `receptionist_tools._extract_week_range`'s `_next_monday`, which was
    already correct (`7 - today.weekday()`) — pinned by test.
    """
    if today is None:
        today = clinic_today(timezone)
    weekday = today.weekday()
    this_sunday = today + _timedelta(days=6 - weekday)
    next_monday = today + _timedelta(days=7 - weekday)
    return WeekAnchors(
        today=today,
        this_sunday=this_sunday,
        next_monday=next_monday,
        next_sunday=next_monday + _timedelta(days=6),
    )
