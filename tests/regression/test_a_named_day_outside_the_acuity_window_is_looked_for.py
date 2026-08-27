"""B-86 on the ACUITY reader: day_window=1 replaces a 30-day scan.

This path was safe for as long as the model sent no window — it scans
`_ACUITY_FULL_WINDOW_DAYS` (30) by default, which contains the next occurrence
of every weekday. B-105 changed that. Its SPECIFIC DAY rule tells the model to
send a named day as `after_date` + `day_window: 1`, and

    used_window = int(explicit_window) if explicit_window else 30

takes it literally: one day is scanned, the named weekday is not in it, and the
caller is told the day is unavailable — about a day nobody looked at.

Same defect as the Google and diary readers, reached differently. There the
window is BUILT from day_window; here it REPLACES a scan that would have found
the day anyway.

No test here touches Acuity. The adapter is a fixture.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

# Imported defensively so this file still COLLECTS on a tree without the fix —
# a collection error would hide these behind an ERROR instead of showing them
# as the assertion failures they are. 30 is the window this path has always
# scanned when the model sends no day_window.
_ACUITY_FULL_WINDOW_DAYS = getattr(rt, "_ACUITY_FULL_WINDOW_DAYS", 30)

_WD_NAMES = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


class _Slot:
    def __init__(self, start):
        self.start_time = start
        self.end_time = start + timedelta(hours=1)


class _Adapter:
    """Counts scans and reports the window each one asked for."""

    def __init__(self, slot_days):
        # slot_days: offsets from today that have a 14:00 slot
        self.slot_days = list(slot_days)
        self.calls = 0
        self.windows = []

    async def get_available_slots(self, appointment_type_id, start_date,
                                  end_date, practitioner_id=None, **k):
        self.calls += 1
        self.windows.append((start_date, end_date))
        today = datetime.now(LONDON_TZ).date()
        out = []
        for off in self.slot_days:
            d = today + timedelta(days=off)
            if start_date <= d < end_date:
                out.append(_Slot(LONDON_TZ.localize(
                    datetime.combine(d, datetime.min.time()) + timedelta(hours=14))))
        return out


def _install(monkeypatch, adapter):
    monkeypatch.setattr(rt, "_get_acuity_adapter", lambda *a, **k: adapter)
    # Keep the post-fetch filters out of the way: this file is about the WINDOW.
    monkeypatch.setattr(rt, "_filter_slots_by_working_hours",
                        lambda slots, *a, **k: slots)
    monkeypatch.setattr("app.clinic_config.get_clinic",
                        lambda *a, **k: {"clinic_id": "theorem"})


def _run(args, session=None):
    return asyncio.run(rt._check_availability_acuity(
        args, session if session is not None else {"clinic_id": "theorem"}))


def _target_offset_and_name(days_out=6):
    """A weekday whose next occurrence is well outside a one-day window."""
    d = datetime.now(LONDON_TZ).date() + timedelta(days=days_out)
    return days_out, _WD_NAMES[d.weekday()]


# -- the defect ------------------------------------------------------------

def test_a_named_weekday_is_not_refused_on_a_one_day_scan(monkeypatch):
    off, name = _target_offset_and_name()
    adapter = _Adapter(slot_days=[off])
    _install(monkeypatch, adapter)
    out = _run({"date_hint": name, "day_window": 1, "location": "alcester"})
    assert adapter.calls == 2, "the widen did not happen"
    assert out.get("day_requested_found") is True, (
        f"{name} has a free slot and was still reported as not found"
    )


def test_the_widen_asks_for_the_full_window(monkeypatch):
    off, name = _target_offset_and_name()
    adapter = _Adapter(slot_days=[off])
    _install(monkeypatch, adapter)
    _run({"date_hint": name, "day_window": 1, "location": "alcester"})
    first, second = adapter.windows
    assert (first[1] - first[0]).days == 1
    assert (second[1] - second[0]).days == _ACUITY_FULL_WINDOW_DAYS


def test_a_day_that_really_is_empty_forbids_claiming_it_is_shut(monkeypatch):
    """Widened, still nothing on that weekday: say so honestly."""
    off, name = _target_offset_and_name()
    # slots exist, but never on the named weekday
    others = [o for o in range(1, 25)
              if _WD_NAMES[(datetime.now(LONDON_TZ).date()
                            + timedelta(days=o)).weekday()] != name]
    adapter = _Adapter(slot_days=others)
    _install(monkeypatch, adapter)
    out = _run({"date_hint": name, "day_window": 1, "location": "alcester"})
    assert out.get("day_requested_found") is False
    g = (out.get("guidance") or "").lower()
    assert "do not say the day is unavailable" in g
    assert "fully booked" in g


# -- bounded ---------------------------------------------------------------

def test_no_weekday_named_means_one_scan(monkeypatch):
    adapter = _Adapter(slot_days=[3])
    _install(monkeypatch, adapter)
    _run({"date_hint": "afternoon", "day_window": 1, "location": "alcester"})
    assert adapter.calls == 1


def test_a_weekday_already_in_the_window_is_not_widened(monkeypatch):
    """Found in the narrow scan — no second Acuity call."""
    today = datetime.now(LONDON_TZ).date()
    adapter = _Adapter(slot_days=[0])
    _install(monkeypatch, adapter)
    _run({"date_hint": _WD_NAMES[today.weekday()], "day_window": 1,
          "location": "alcester"})
    assert adapter.calls == 1


def test_a_full_window_request_is_not_re_read(monkeypatch):
    """A model that already asked for 30+ days has been given it."""
    off, name = _target_offset_and_name()
    adapter = _Adapter(slot_days=[])
    _install(monkeypatch, adapter)
    _run({"date_hint": name, "day_window": _ACUITY_FULL_WINDOW_DAYS,
          "location": "alcester"})
    assert adapter.calls == 1


def test_a_failed_widen_keeps_the_narrow_result(monkeypatch):
    """The turn survives an Acuity error, and still refuses to claim closure.

    A 2-day window with a slot TOMORROW, so the narrow result is non-empty
    whatever the clock says — a slot later today would be eaten by the 2-hour
    lead-time filter after midday and make this test wall-clock dependent.
    """
    off, name = _target_offset_and_name()

    class _Boom(_Adapter):
        async def get_available_slots(self, *a, **k):
            # NB: the base class does its own counting — do not double-count.
            if self.calls >= 1:
                self.calls += 1
                raise RuntimeError("acuity down")
            return await super().get_available_slots(*a, **k)

    adapter = _Boom(slot_days=[1, off])
    _install(monkeypatch, adapter)
    out = _run({"date_hint": name, "day_window": 2, "location": "alcester"})
    assert adapter.calls == 2, "the widen was not attempted"
    # The widen failed, so the named day is still unfound and unclaimable...
    assert out.get("day_requested_found") is False
    # ...and nothing from beyond the narrow window leaked into the offer.
    _dates = {d.get("date") for d in (out.get("available_days") or [])}
    _target = (datetime.now(LONDON_TZ).date() + timedelta(days=off)).isoformat()
    assert _target not in _dates
