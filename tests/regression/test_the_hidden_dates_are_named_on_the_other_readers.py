"""
Regression: the diary and generic readers hid dates and said nothing about it.

B-110. Found 28 Aug 2026 by re-auditing the B-109 port claim, not by a call.

B-109 gave the Acuity reader a third case: when N further dates matching the
requested weekday DO have times and were cut by the presentation cap, name
them so Susie can offer them. Asked whether that needed porting, the first
answer was "the other readers already carry this contract". That was scoped
from a keyword count and was wrong. They carry two of the three cases:

    weekday not found at all               acuity yes | diary/generic yes
    exactly ONE occurrence in the window   acuity yes | diary/generic yes
    N further dates DO have times          acuity yes | diary/generic NO

`grep -c "further date(s) matching the requested day"` was 1 on the Acuity body
and 0 everywhere else. So a Vital Edge caller asking about Tuesdays heard two
of four and was never told the other two existed.

MILDER THAN THE ACUITY VERSION, and the difference is worth keeping straight.
`_MAX_PRESENTED_DAYS` is 2 and `_cap_presented_slots` only declares single_day
when ONE day survives, so these readers show TWO real dates rather than one,
and single_day here means there genuinely is only one matching day. The
scarcity guard also already declines while `available_days` holds more than one
day. So this is the missing OFFER, not a false claim - which is why it is
B-110 and not a P1.

THE FIX runs AFTER `_cap_presented_slots`, reading what was actually presented
rather than assuming the cap: `_filter_same_day_slots` runs in between and can
drop a day, which would put a guess off by one. Dates only, never times - the
same rule as B-109, because times for an unspoken date go back into the
ordinal map that B-108b cleared.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

_WD_NAMES = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


def _clinic(**over):
    """VE's real shape, with a window wide enough to hold four Tuesdays."""
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
        "days_ahead": 30,
        "allow_same_day": False,
    }
    clinic.update(over)
    return clinic


class _FreeDiary:
    """An empty diary: every working hour is free. No Google is touched."""

    def freebusy(self, tokens, start, end, calendar_id, *a, **k):
        return []


def _install(monkeypatch):
    async def _tok(*a, **k):
        return {"access_token": "x"}

    async def _save(*a, **k):
        return None

    monkeypatch.setattr(rt, "_get_tokens", _tok)
    monkeypatch.setattr(rt, "_save_gcal_tokens", _save)
    monkeypatch.setattr("app.tools.calendar_google.freebusy", _FreeDiary().freebusy)
    monkeypatch.setattr("app.tools.calendar_google.list_upcoming_events",
                        lambda *a, **k: [])


def _run(args, clinic=None):
    return asyncio.run(rt._check_availability_diary(args, {}, clinic or _clinic()))


def _a_weekday_with_several_occurrences():
    """A weekday name several days out, computed off the real clock.

    A pin that hardcodes a weekday dies at midnight (b55).
    """
    target = (datetime.now(LONDON_TZ) + timedelta(days=4)).date()
    return _WD_NAMES[target.weekday()], target


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_hidden_dates_are_named(monkeypatch):
    _install(monkeypatch)
    name, first = _a_weekday_with_several_occurrences()
    out = _run({"date_hint": name})

    others = out.get("other_dates_for_requested_day")
    assert others, (
        f"the diary reader offered {out.get('total_days')} {name}s, spoke two, "
        f"and told the model nothing about the rest"
    )
    spoken = {d["date"] for d in (out.get("presented_days") or [])}
    if out.get("first_day"):
        spoken.add(out["first_day"]["date"])
    for o in others:
        assert o["date"] not in spoken, "a date already read out was re-offered"
        assert datetime.fromisoformat(o["date"]).weekday() == first.weekday()


def test_each_named_date_carries_a_label_and_a_count(monkeypatch):
    _install(monkeypatch)
    name, _ = _a_weekday_with_several_occurrences()
    others = _run({"date_hint": name})["other_dates_for_requested_day"]
    for o in others:
        assert o["spoken"] == rt._spoken_day_label(o["date"])
        assert o["times_available"] > 0


def test_no_time_is_handed_over_for_a_date_nobody_heard(monkeypatch):
    """The rule that keeps B-108b shut."""
    _install(monkeypatch)
    name, _ = _a_weekday_with_several_occurrences()
    for o in _run({"date_hint": name})["other_dates_for_requested_day"]:
        assert set(o) == {"date", "spoken", "times_available"}


def test_at_most_three_are_named(monkeypatch):
    _install(monkeypatch)
    name, _ = _a_weekday_with_several_occurrences()
    assert len(_run({"date_hint": name})["other_dates_for_requested_day"]) <= 3


def test_the_guidance_states_the_whole_contract(monkeypatch):
    _install(monkeypatch)
    name, _ = _a_weekday_with_several_occurrences()
    g = _run({"date_hint": name})["guidance"]
    assert "NAME" in g and "other_dates_for_requested_day" in g
    assert "never state a time on those dates" in g
    # The phrase the B-94 family pins. Do not reword it.
    assert "never invent times" in g
    assert "all there is on that weekday" in g


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_the_one_occurrence_case_still_wins(monkeypatch):
    """The existing, more specific guidance is set first and describes a diary
    this one does not apply to. The new case must stay silent under it."""
    _install(monkeypatch)
    target = (datetime.now(LONDON_TZ) + timedelta(days=3)).date()
    out = _run({"date_hint": _WD_NAMES[target.weekday()]}, _clinic(days_ahead=5))
    assert "Only ONE" in (out.get("guidance") or "")
    assert "other_dates_for_requested_day" not in out


def test_a_request_naming_no_weekday_is_untouched(monkeypatch):
    _install(monkeypatch)
    out = _run({"date_hint": "next week"})
    assert "other_dates_for_requested_day" not in out


def test_the_spoken_days_are_unchanged(monkeypatch):
    """This commit adds an offer, not a second readout. The cap still holds."""
    _install(monkeypatch)
    name, _ = _a_weekday_with_several_occurrences()
    out = _run({"date_hint": name})
    spoken = out.get("presented_days") or ([out["first_day"]] if out.get("first_day") else [])
    assert len(spoken) <= rt._MAX_PRESENTED_DAYS


# ---------------------------------------------------------------------------
# The coupling: both live readers must call it
# ---------------------------------------------------------------------------
def test_both_non_acuity_readers_call_it():
    """VE runs the diary reader; jv_v1 runs the generic path in
    _exec_check_availability. A fix on one is half a fix."""
    call = "_name_the_other_matching_dates(_out, _pref_weekdays)"
    assert call in inspect.getsource(rt._check_availability_diary), (
        "the diary reader (Vital Edge) no longer names the dates it hides"
    )
    assert call in inspect.getsource(rt._exec_check_availability), (
        "the generic reader (jv_v1) no longer names the dates it hides"
    )


def test_it_runs_after_the_cap_not_before():
    """Reading the presented set before _cap_presented_slots would name a date
    that is about to be spoken, and _filter_same_day_slots runs in between."""
    src = inspect.getsource(rt._check_availability_diary)
    cap = src.find("_cap_presented_slots(")
    name = src.find("_name_the_other_matching_dates(")
    assert -1 not in (cap, name) and cap < name
