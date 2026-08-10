# tests/regression/test_month_first_date_is_honoured.py
""""August 19th" was not read as a date — and its month was replaced (10 Aug 2026).

Incident
--------
Call 4. The caller asked for Wednesday the 19th of August. Susie replied
"Wednesday the 19th of August is fully booked, I'm afraid". It was not. Eight
lines earlier the same log has::

    2026-08-19 — 6 raw slot(s)

Only the caller's own 3pm from Call 1 was gone; 2pm and 4pm were still free.
She offered Tuesday the 11th instead — a different week.

Mechanism
---------
The hint was ``'August 19th at 3 pm'`` and the log says::

    week filter bypassed — no week anchor in date_hint: 'August 19th at 3 pm'

So nothing filtered. The tool swept the full 30-day window, the spoken list was
capped to the soonest three days (11th/12th/13th), and the 19th was simply not
in the payload. The model read that absence as clinic state.

Three separate faults stacked up, and all three had to be fixed:

1. **The anchor gate was day-first only.** Its ordinal-date row matched
   ``19th August`` but not ``August 19th``, so a named date bypassed the week
   filter entirely.

2. **The parser was day-first only, and silently substituted the month.**
   ``_extract_week_range`` searches from the digits, so on ``august 19th`` the
   month word to their LEFT was never seen. It fell through to the
   nearest-future-day-of-month branch and returned a date in whatever month
   came next. This is the worse half of the bug and it was invisible in the
   incident only because August *was* the current month::

       'September 19th'  ->  19 AUGUST 2026     (a month early)
       'December 1st'    ->   1 SEPTEMBER 2026  (three months early)
       'January 6th'     ->   6 SEPTEMBER 2026

   Fixing the gate alone would therefore have been worse than the bug: the
   week filter would have applied, confidently, to the wrong date. The bypass
   was accidentally protecting against it.

3. **Any trailing word killed the ordinal fallback.** Pattern 4 captured the
   word after the ordinal as a month candidate; when it was not a month it
   still blocked the fallback via ``not word``. So ``'the 19th at 3 pm'`` — a
   date and a time in one hint, which is how people actually speak — parsed as
   no date at all.

Fix
---
* New Pattern 3.9 for the month-first form, before Pattern 4.
* Pattern 4's fallback keyed on ``not month_n`` rather than ``not word``.
* The anchor gate gained a month-first row (and ``of`` in the day-first row).

The ordinal suffix remains what distinguishes a date from a time, so ``9pm``
and ``10:30`` still parse as no date and still bypass the filter — which is
what the bypass is for.

Family
------
Same family as the guard-refusal fix (#5): something that is not clinic state
being spoken as clinic state. #5 was a refusal; this is real data with a gap in
it. For a real caller this one is worse — they asked for a specific day, were
told it was full when it was not, and were sent to a different week.

Not covered here: when a hint legitimately bypasses the filter ("evening
slots"), the payload still reports ``total_days`` as the number of days
*presented*, not the number found, so the model still cannot tell that days
were withheld. That is a separate decision about the tool contract.
"""

import datetime as _dt

import pytest

from app.tools.receptionist_tools import _extract_week_range


TODAY = _dt.date.today()


def _single_day(hint):
    """The hint's resolved date, asserting it is a single-day range."""
    rng = _extract_week_range(hint)
    assert rng is not None, f"{hint!r} parsed as no date at all"
    start, end = rng
    assert start == end, f"{hint!r} gave a multi-day range {start}..{end}"
    return start


# ---------------------------------------------------------------------------
# The incident.
# ---------------------------------------------------------------------------
def test_the_incident_hint_resolves_to_the_nineteenth_of_august():
    """'August 19th at 3 pm' — a date and a time, as the caller said it."""
    got = _single_day("August 19th at 3 pm")
    assert (got.month, got.day) == (8, 19), (
        f"resolved to {got}; the week filter would again be applied to the "
        f"wrong day, or bypassed, and the 19th dropped from the payload"
    )


def test_the_incident_hint_is_not_bypassed_as_anchorless():
    """The parse is only reached if the gate admits it. A None here is exactly
    the 'week filter bypassed — no week anchor' log line from the incident."""
    assert _extract_week_range("August 19th at 3 pm") is not None


# ---------------------------------------------------------------------------
# The silent month substitution — the half that was not in the report.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hint,month,day",
    [
        ("September 19th", 9, 19),
        ("December 1st",  12, 1),
        ("October 5th",   10, 5),
        ("Aug 19",         8, 19),
        ("sept 3rd",       9, 3),
        ("August the 19th", 8, 19),
        ("August 19th 2026", 8, 19),
    ],
)
def test_month_first_dates_keep_their_month(hint, month, day):
    got = _single_day(hint)
    assert (got.month, got.day) == (month, day), (
        f"{hint!r} resolved to {got} — the month word was discarded and the "
        f"nearest future {day}th returned instead"
    )


def test_a_month_first_date_is_never_earlier_than_the_month_named():
    """The precise shape of the old failure: the answer landed BEFORE the month
    the caller said. Asserted as an ordering property so it also catches a
    future off-by-one that a fixed date list would miss."""
    for name, n in [("September", 9), ("October", 10), ("November", 11),
                    ("December", 12)]:
        got = _single_day(f"{name} 15th")
        assert (got.year, got.month) >= (TODAY.year, n) or got.year > TODAY.year, (
            f"'{name} 15th' resolved to {got}, before {name}"
        )


# ---------------------------------------------------------------------------
# A date and a time in one hint.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hint", ["the 19th at 3 pm", "the 19th around 3pm", "19th in the afternoon"]
)
def test_a_trailing_word_no_longer_kills_the_ordinal(hint):
    got = _single_day(hint)
    assert got.day == 19


# ---------------------------------------------------------------------------
# Everything the bypass exists for must still bypass.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hint",
    ["9pm", "10:30", "evening slots", "as soon as possible", "any Monday",
     "mornings", "after 5", "May 2026", ""],
)
def test_timey_and_vague_hints_still_parse_as_no_date(hint):
    assert _extract_week_range(hint) is None, (
        f"{hint!r} now narrows the search to a single day — this is the "
        f"failure the bypass was written to prevent ('9pm' -> the 9th)"
    )


# ---------------------------------------------------------------------------
# Day-first forms are unchanged. The month-first pattern runs first, so these
# are the ones a bad new pattern would quietly capture.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hint,expected",
    [
        ("21st May 2026",              _dt.date(2026, 5, 21)),
        ("Thursday 21st May mornings", _dt.date(2027, 5, 21)),  # rolls forward
        ("2026-06-23",                 _dt.date(2026, 6, 23)),
    ],
)
def test_day_first_and_iso_forms_are_unchanged(hint, expected):
    assert _extract_week_range(hint) == (expected, expected)


def test_a_bare_year_is_not_eaten_as_the_day():
    """'21st May 2026' — the month-first pattern must not match 'May 20' out of
    'May 2026'. It did on the first cut of this fix, giving 20 May 2027."""
    got = _single_day("21st May 2026")
    assert got == _dt.date(2026, 5, 21), (
        f"resolved to {got} — the day group ate the head of the year"
    )


@pytest.mark.parametrize(
    "hint,start,end",
    [
        ("next week",       None, None),
        ("from 18 May 2026", _dt.date(2026, 5, 18), _dt.date(2026, 5, 24)),
        ("8th or 9th",      _dt.date(2026, 9, 8),  _dt.date(2026, 9, 9)),
    ],
)
def test_week_and_multidate_patterns_still_win(hint, start, end):
    """Patterns 1-3.7 run before the new one and must keep their multi-day
    ranges — a single-day answer here means the month-first pattern reached a
    hint it should never have seen."""
    rng = _extract_week_range(hint)
    assert rng is not None
    if start is not None:
        assert rng == (start, end)
    else:
        assert rng[0] != rng[1], f"{hint!r} collapsed to a single day"
