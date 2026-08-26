"""
Regression: a bare weekday showed one date and reported nothing withheld.

B-94. `CA390f03d2c91c59d85691618bb7e55f1d` (26 Aug 2026, theorem_v3, build
`c00e4a4c`, Alcester).

    19:39:21  caller: "um have you got anything on a friday"
    19:39:26  [ms_tools] week filter bypassed — no week anchor in date_hint: 'Friday'
              _check_availability_acuity: 95 total raw slot(s) over 30 days
                2026-09-04 — 4 raw slot(s)      <- Friday
                2026-09-11 — 5 raw slot(s)      <- Friday
    19:39:27  "The available slot for Friday 28th August is two in the afternoon."
    19:39:35  caller: "uh do you have any other slots on that day"
    19:39:45  "That's the only slot we have on Friday 28th August."
    19:39:50  outcome=abandoned

Both sentences are TRUE about the 28th, which is why this is invisible in a
transcript. The caller asked about FRIDAYS and was answered about one date.

CAUSE, reproduced by driving the real executor with Mark's Acuity shape:

    presentation_mode    single_day
    first_day            Friday 28th August, one slot
    available_days       28 Aug, 4 Sep, 11 Sep, 18 Sep
    days_found_in_window 4
    days_not_shown       0        <- three Fridays with 4, 5 and 2 free slots

A bare weekday with no week phrase is a SPECIFIC-DAY request, so the mode goes
single_day and `days_data` keeps every matching date. `days_not_shown` was
`_days_found - len(_present_days)`, and in single_day mode `_present_days` is
all of days_data — so the field read 0 on the one presentation that hides the
most. Only `days_data[0]` ever becomes `first_day`.

Susie's answer was a faithful reading of a payload that said nothing was held
back. The payload is what was wrong.

THE FIX has two halves, and the second is the one the caller hears. The
denominator becomes the number of days actually SPOKEN (one, in single_day).
Then, because a number does not stop a sentence, the date/weekday rule is
stated outright the way the Google path already states it (28245401): all the
times on that DATE is sayable; all there is on that WEEKDAY is not.

DELIBERATELY NOT COVERED, both asserted below: a specific DATE resolves to one
day so nothing fires, and an ASAP request asked for the soonest day and is
answered by it (owner decision, 2026-06-15).
"""
from __future__ import annotations

import datetime as _dt
from unittest.mock import patch

import pytest
import pytz

import app.tools.receptionist_tools as rt

_TZ = pytz.timezone("Europe/London")
_TODAY = _dt.date(2026, 8, 26)          # the Wednesday of the live call


class _Slot:
    def __init__(self, start, end):
        self.start_time, self.end_time = start, end


def _slots(plan):
    """plan: {day offset from 26 Aug 2026: how many slots that day}."""
    out = []
    for off, n in plan.items():
        d = _TODAY + _dt.timedelta(days=off)
        for hour in (14, 10, 11, 15, 16)[:n]:
            out.append(_Slot(
                _dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=_TZ),
                _dt.datetime(d.year, d.month, d.day, hour, 50, tzinfo=_TZ),
            ))
    return out


# Mark's diary as Acuity actually returned it: Fri 28 Aug has ONE slot, and the
# later Fridays have more. That asymmetry is the point — the thinnest matching
# date is the one that gets spoken.
_FRIDAYS = {2: 1, 9: 4, 16: 5, 23: 2}


def _adapter(plan):
    class _Stub:
        async def get_available_slots(self, **_kw):
            return _slots(plan)
    return _Stub()


async def _availability(date_hint, plan=None):
    session = {
        "clinic_id": "theorem",
        "selected_location": "alcester",
        "call_sid": "TEST",
    }
    with patch.object(rt, "_get_acuity_adapter",
                      lambda *a, **k: _adapter(plan or _FRIDAYS), create=True):
        return await rt._check_availability_acuity(
            {
                "service":   "msk_initial_assessment",
                "location":  "alcester",
                "date_hint": date_hint,
            },
            session,
        )


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
async def test_the_live_payload_shape_is_reproduced():
    """Guard the fixture: if this stops matching the call, the rest proves
    nothing about what happened on CA390f03d2."""
    r = await _availability("Friday")
    assert r["presentation_mode"] == "single_day"
    assert r["first_day"]["date"] == "2026-08-28"
    assert r["first_day"]["slot_times"] == ["14:00"]
    assert [d["date"] for d in r["available_days"]] == [
        "2026-08-28", "2026-09-04", "2026-09-11", "2026-09-18",
    ]


async def test_the_hidden_fridays_are_counted():
    """The field itself. This read 0 on the live call."""
    r = await _availability("Friday")
    assert r["days_found_in_window"] == 4
    assert r["days_not_shown"] == 3, (
        "three Fridays with free slots were not shown; a payload that reports "
        "0 licenses 'that's the only slot we have'"
    )


async def test_the_date_weekday_rule_travels_with_the_result():
    """A count does not stop a sentence. The rule has to be stated."""
    g = (await _availability("Friday")).get("guidance") or ""
    assert g, "no guidance on a weekday request that hid three dates"
    low = g.lower()
    assert "date" in low and "weekday" in low, (
        "the guidance must separate the DATE claim from the WEEKDAY claim — "
        "that split is what keeps 'the only slot on the 28th' sayable"
    )
    assert "never invent times" in low


def test_the_guidance_carries_no_em_dash():
    """TTS pause punctuation is chunker input and model-facing strings have
    been echoed into speech before."""
    import inspect
    src = inspect.getsource(rt._check_availability_acuity)
    i = src.index("further date(s) matching the requested day")
    assert "—" not in src[i - 200:i + 700]


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
async def test_a_specific_date_still_says_nothing_was_hidden():
    """"the 28th" resolves to one day, so there is nothing to disclose and no
    rule to state. This is the sentence 28245401 fought to keep sayable."""
    r = await _availability("28 August 2026")
    assert r["days_not_shown"] == 0
    assert not r.get("guidance")


async def test_an_asap_request_is_left_alone():
    """Owner decision 2026-06-15: ASAP shows the ONE soonest day as-is. It
    names no weekday, so the guidance must not fire."""
    r = await _availability("as soon as possible")
    assert r["presentation_mode"] == "single_day"
    assert not r.get("guidance")


async def test_a_multi_day_readout_keeps_its_own_count():
    """The denominator change must not touch multi_day, where _present_days is
    already the spoken set."""
    plan = {2: 2, 3: 2, 4: 2, 5: 2, 6: 2}
    r = await _availability("next week", plan=plan)
    if r["presentation_mode"] == "multi_day":
        assert r["days_not_shown"] == max(
            0, r["days_found_in_window"] - len(r["available_days"])
        )


async def test_a_weekday_with_exactly_one_matching_date_stays_quiet():
    """Nothing hidden, nothing to say."""
    r = await _availability("Friday", plan={2: 3})
    assert r["days_found_in_window"] == 1
    assert r["days_not_shown"] == 0
    assert not r.get("guidance")
