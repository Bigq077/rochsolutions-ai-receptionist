"""B-86 on the DIARY reader: a named weekday outside a narrow window.

`_exec_check_availability` early-returns into `_check_availability_diary` at the
`availability_mode` branch, so neither of the two widens in the Google body is
ever reached for Vital Edge. A content-based port audit scores that code
"present on VE" because it IS in the file — the dispatch simply never arrives.
The four-branch audit of 27 Aug walked into exactly that and scored VE 98-100%
on this chain.

Why it bites harder since B-105: SPECIFIC DAY tells the model to send a named
day as `after_date` + `day_window: 1`. The diary reader sets
`w_end = w_start + day_window` with nothing behind it, so a weekday that does
not fall inside that single day came back as "there is nothing free in that
window" — said about a day nobody looked at.

No test here touches Google. The calendar is a fixture, so a passing run means
the LOGIC is right, not that the credentials happen to work.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

_WD_NAMES = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


def _clinic(**over):
    """VE's real shape: 09:00-18:00 last start, every day, 5-minute gap."""
    hours = {d: (9.0, 19.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    clinic = {
        "clinic_id": "vital_edge",
        "practitioner": "Jonathan",
        "booking_system": "google_calendar_provisional",
        "availability_mode": "diary",
        "calendar_id": "vitaledgetherapy@gmail.com",
        "working_hours": hours,
        "slot_minutes": 60,
        "slot_break_minutes": 5,
        "slot_increment_minutes": 60,
        "days_ahead": 17,
        "allow_same_day": False,
    }
    clinic.update(over)
    return clinic


def _tomorrow():
    """w_start when allow_same_day is False."""
    return (datetime.now(LONDON_TZ) + timedelta(days=1)).date()


def _timed(day, start_h, end_h, summary="Massage with Roger"):
    base = LONDON_TZ.localize(datetime.combine(day, datetime.min.time()))
    return {"start": base + timedelta(hours=start_h),
            "end": base + timedelta(hours=end_h), "summary": summary}


class _Cal:
    """Fake calendar that COUNTS freebusy reads, so 'bounded' is testable."""

    def __init__(self, busy=(), fail_after=None):
        self.busy = list(busy)
        self.calls = 0
        self.windows = []
        self.fail_after = fail_after

    def freebusy(self, tokens, start, end, calendar_id, *a, **k):
        self.calls += 1
        self.windows.append((start.date(), end.date()))
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("calendar unavailable")
        return [{"start": e["start"].isoformat(), "end": e["end"].isoformat()}
                for e in self.busy]


def _install(monkeypatch, cal):
    async def _tok(*a, **k):
        return {"access_token": "x"}

    async def _save(*a, **k):
        return None

    monkeypatch.setattr(rt, "_get_tokens", _tok)
    monkeypatch.setattr(rt, "_save_gcal_tokens", _save)
    monkeypatch.setattr("app.tools.calendar_google.freebusy", cal.freebusy)
    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events",
                        lambda *a, **k: [])


def _run(args, clinic=None, session=None):
    return asyncio.run(rt._check_availability_diary(
        args, session if session is not None else {}, clinic or _clinic()))


def _dates_offered(out):
    return {d.get("date") for d in (out.get("available_days") or [])}


def _named_day_beyond_the_window():
    """A weekday name that CANNOT fall inside a one-day window at w_start.

    Computed off the real clock rather than hardcoded, so this file does not
    join the wall-clock-dependent tests that turn red as the day advances.
    """
    start = _tomorrow()
    target = start + timedelta(days=3)
    return _WD_NAMES[target.weekday()], target


# -- the defect ------------------------------------------------------------

def test_a_weekday_outside_the_one_day_window_is_still_looked_for(monkeypatch):
    """The defect. day_window=1 + a weekday three days out = 'nothing free'."""
    name, target = _named_day_beyond_the_window()
    cal = _Cal()
    _install(monkeypatch, cal)
    out = _run({"date_hint": name, "after_date": _tomorrow().isoformat(),
                "day_window": 1})
    assert target.isoformat() in _dates_offered(out), (
        f"{name} ({target}) has a free diary and was never offered; "
        f"offered={_dates_offered(out)}"
    )


def test_the_widen_does_not_claim_the_day_is_unavailable(monkeypatch):
    """When the day really is absent, the payload forbids asserting closure."""
    name, target = _named_day_beyond_the_window()
    # Block every hour of the named weekday across the whole horizon.
    busy = []
    d = _tomorrow()
    for _ in range(20):
        if _WD_NAMES[d.weekday()] == name:
            busy.append(_timed(d, 0, 24, "away"))
        d += timedelta(days=1)
    cal = _Cal(busy=busy)
    _install(monkeypatch, cal)
    out = _run({"date_hint": name, "after_date": _tomorrow().isoformat(),
                "day_window": 1})
    assert out.get("day_requested_found") is False
    g = (out.get("guidance") or "").lower()
    assert "do not say the day is unavailable" in g
    assert "fully booked" in g


# -- bounded: the ordinary call must not pay for this -----------------------

def test_no_weekday_named_means_no_second_read(monkeypatch):
    """A request naming no weekday costs exactly one freebusy call."""
    cal = _Cal()
    _install(monkeypatch, cal)
    _run({"date_hint": "any", "after_date": _tomorrow().isoformat(),
          "day_window": 1})
    assert cal.calls == 1


def test_a_weekday_already_in_the_window_is_not_widened(monkeypatch):
    """Found in the narrow read - no second call."""
    start = _tomorrow()
    cal = _Cal()
    _install(monkeypatch, cal)
    _run({"date_hint": _WD_NAMES[start.weekday()],
          "after_date": start.isoformat(), "day_window": 1})
    assert cal.calls == 1


def test_the_widen_is_at_most_one_extra_read(monkeypatch):
    name, _ = _named_day_beyond_the_window()
    cal = _Cal()
    _install(monkeypatch, cal)
    _run({"date_hint": name, "after_date": _tomorrow().isoformat(),
          "day_window": 1})
    assert cal.calls == 2


def test_the_widen_never_reaches_past_the_booking_horizon(monkeypatch):
    """days_ahead is a hard limit; the widen is clamped to it."""
    name, _ = _named_day_beyond_the_window()
    cal = _Cal()
    _install(monkeypatch, cal)
    clinic = _clinic(days_ahead=17)
    _run({"date_hint": name, "after_date": _tomorrow().isoformat(),
          "day_window": 1}, clinic=clinic)
    horizon = (datetime.now(LONDON_TZ) + timedelta(days=17)).date()
    for _s, _e in cal.windows:
        assert _e <= horizon


# -- fail CLOSED: the one thing that must not be copied from the Google body -

def test_a_failed_widen_keeps_the_filtered_result(monkeypatch):
    """The Google body falls back to UNFILTERED candidates. Never here.

    An unfiltered candidate is the bare working-hours grid with nothing
    subtracted - every one a time Jonathan may already have a client in. That
    is the defect `_check_availability_diary` exists to prevent, so a widen
    that cannot read the diary must leave the narrow result standing.
    """
    name, target = _named_day_beyond_the_window()
    start = _tomorrow()
    # The narrow window's own day is busy 11:00-12:00; the widen read fails.
    cal = _Cal(busy=[_timed(start, 11, 12, "Massage with Roger")], fail_after=1)
    _install(monkeypatch, cal)
    out = _run({"date_hint": name, "after_date": start.isoformat(),
                "day_window": 1})
    # No slot from beyond the narrow window leaked in unfiltered.
    assert target.isoformat() not in _dates_offered(out)
    # And the payload still refuses to call the day unavailable.
    assert out.get("day_requested_found") is False
