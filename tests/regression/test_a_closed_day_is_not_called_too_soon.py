"""P8: a closed day was reported to the model as "too soon to book".

`_check_availability_acuity` captures `raw_slot_count` before the lead-time
filter, then runs THREE filters — lead-time, working-hours, bank-holiday —
before a branch that tested only `raw_slot_count > 0` and asserted lead time
as the cause. Three slots on a Sunday at a Mon-Fri location came back as:

    error       : lead_time_limited
    error_detail: There are 3 slot(s) available at Alcester today but all
                  start within 2 hours — too soon to book.

Three false claims the model is asked to act on: the day ("today", four days
out), the timing ("within 2 hours", 94.6), and the cause (shut, not busy).
The caller hears "no" instead of "we are closed then, how about Monday?".

These tests fail on the unfixed tree with the wrong ERROR CODE, not a crash —
neutering the fix must turn them red, so they assert the cause and the prose
separately.

No test here touches Acuity or the GOV.UK API. Both are fixtures.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

_MON_TO_FRI = {
    "mon": (8.5, 21.0), "tue": (8.5, 21.0), "wed": (8.5, 21.0),
    "thu": (8.5, 21.0), "fri": (8.5, 21.0), "sat": None, "sun": None,
}
_ALWAYS_OPEN = {d: (0.0, 24.0) for d in
                ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


class _Slot:
    def __init__(self, start):
        self.start_time = start
        self.end_time = start + timedelta(hours=1)


class _Adapter:
    """Returns a fixed list of datetimes, whatever window is asked for."""

    def __init__(self, starts):
        self.starts = list(starts)

    async def get_available_slots(self, appointment_type_id, start_date,
                                  end_date, practitioner_id=None, **k):
        return [_Slot(s) for s in self.starts
                if start_date <= s.date() < end_date]


def _install(monkeypatch, starts, hours, bank_holidays=frozenset()):
    monkeypatch.setattr(rt, "_get_acuity_adapter",
                        lambda *a, **k: _Adapter(starts))
    monkeypatch.setattr("app.clinic_config.get_clinic", lambda *a, **k: {
        "clinic_id": "theorem",
        "location_working_hours": {"alcester": hours},
    })

    async def _closed(_clinic):
        return frozenset(bank_holidays)

    monkeypatch.setattr(rt, "_closed_dates_for", _closed)


def _run():
    return asyncio.run(rt._check_availability_acuity(
        {"location": "alcester"}, {"clinic_id": "theorem"}))


def _next_weekday(weekday, at_hour=14):
    """Next date with this weekday, at least 3 days out (clear of lead time)."""
    d = datetime.now(LONDON_TZ).date() + timedelta(days=3)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return LONDON_TZ.localize(
        datetime.combine(d, datetime.min.time()) + timedelta(hours=at_hour))


# -- the defect ------------------------------------------------------------

def test_a_closed_sunday_is_not_reported_as_lead_time(monkeypatch):
    sunday = _next_weekday(6)
    starts = [sunday, sunday + timedelta(hours=1), sunday + timedelta(hours=2)]
    _install(monkeypatch, starts, _MON_TO_FRI)
    out = _run()
    assert out.get("error") != "lead_time_limited", (
        "the clinic is shut on Sunday and the model was told the slots are "
        "too soon to book"
    )
    assert out.get("error") == "closed_on_day", out.get("error")


def test_the_detail_does_not_invent_the_timing_or_the_day(monkeypatch):
    sunday = _next_weekday(6)
    _install(monkeypatch, [sunday], _MON_TO_FRI)
    detail = (_run().get("error_detail") or "").lower()
    assert "too soon" not in detail, detail
    assert "within 2 hours" not in detail, detail
    assert "today" not in detail, detail
    assert "opening hours" in detail, detail


def test_a_bank_holiday_is_named_as_a_bank_holiday(monkeypatch):
    monday = _next_weekday(0)
    _install(monkeypatch, [monday], _ALWAYS_OPEN,
             bank_holidays={monday.date()})
    out = _run()
    assert out.get("error") == "bank_holiday", out.get("error")
    assert "bank holiday" in (out.get("error_detail") or "").lower()


# -- the fix must not over-correct ----------------------------------------

def test_a_genuine_lead_time_case_still_reports_lead_time(monkeypatch):
    soon = datetime.now(LONDON_TZ) + timedelta(minutes=30)
    _install(monkeypatch, [soon, soon + timedelta(minutes=15)], _ALWAYS_OPEN)
    out = _run()
    assert out.get("error") == "lead_time_limited", out.get("error")
    assert "too soon" in (out.get("error_detail") or "").lower()


def test_an_empty_read_is_still_no_availability(monkeypatch):
    _install(monkeypatch, [], _MON_TO_FRI)
    out = _run()
    assert out.get("error") == "no_availability", out.get("error")
