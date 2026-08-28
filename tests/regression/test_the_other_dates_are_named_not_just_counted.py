"""
Regression: Susie was told to offer the other Tuesdays and forbidden to name one.

B-109. Owner decision 28 Aug 2026, taken off CA1b7b2c58 (Theorem, 27 Aug) --
the same call as B-108.

    caller: "um do you have anything on tuesday"
    Susie:  one slot, Tuesday 1st September, nine in the morning
    outcome: abandoned

    the same 30-day scan, in the same log:
        2026-09-01   1 slot   <- the one she read out
        2026-09-08   7 slots
        2026-09-15   8 slots
        2026-09-22   9 slots

B-108 stopped her claiming that was all there was. It did not let her OFFER
the rest. The guidance said "offer to look at the next one" while the same
sentence said "never name a later date you have not been given slots for", so
the only way the caller reached 24 further slots was to think to ask again.

That shape is the norm rather than bad luck: the soonest matching date is the
nearest, so it is the most booked, and single_day mode shows it. The thinnest
member of the set is the one spoken almost every time.

THE DECISION, AND WHAT WAS REJECTED
-----------------------------------
Presenting the next three occurrences of the weekday was rejected: it is the
wall of times the presentation caps exist to prevent (58319e89, "she read out
five times in one breath"), and it would put dates the caller never heard back
into the ordinal map that B-108b had just cleared.

Reusing the ASAP "fill-forward" was rejected because it does not exist. The
comment above the mode decision described it for months after owner decision
2026-06-15 removed it; the code says NO fill-forward. That stale comment is
corrected in this commit, because it was read as a live mechanism.

So: hand over the DATES, never the times. Dates alone let her say "I have also
got times on the 8th, 15th and 22nd" and stop.
"""
from __future__ import annotations

import datetime as _dt
from unittest.mock import patch

import pytz

import app.tools.receptionist_tools as rt

_TZ = pytz.timezone("Europe/London")
_HOURS = (9, 10, 11, 12, 14, 15, 16, 17, 18)


class _Slot:
    def __init__(self, start, end):
        self.start_time, self.end_time = start, end


def _build(plan):
    out = []
    for d, n in plan.items():
        for hour in _HOURS[:n]:
            out.append(_Slot(
                _dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=_TZ),
                _dt.datetime(d.year, d.month, d.day, hour, 50, tzinfo=_TZ),
            ))
    return out


def _the_live_shape():
    """Four occurrences of one weekday holding 1, 7, 8 and 9 slots.

    Anchored on the real today, not a fixed date: a pin that names a weekday
    dies at midnight (b55).
    """
    base = _dt.date.today() + _dt.timedelta(days=4)
    return base, {
        base: 1,
        base + _dt.timedelta(days=7): 7,
        base + _dt.timedelta(days=14): 8,
        base + _dt.timedelta(days=21): 9,
    }


async def _availability(plan, date_hint):
    class _Stub:
        async def get_available_slots(self, **_kw):
            return _build(plan)

    session = {
        "clinic_id": "theorem",
        "selected_location": "alcester",
        "call_sid": "TEST",
    }
    with patch.object(rt, "_get_acuity_adapter",
                      lambda *a, **k: _Stub(), create=True):
        return await rt._check_availability_acuity(
            {
                "service":   "msk_initial_assessment",
                "location":  "alcester",
                "date_hint": date_hint,
            },
            session,
        )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
async def test_the_other_dates_are_named():
    base, plan = _the_live_shape()
    result = await _availability(plan, base.strftime("%A"))
    others = result.get("other_dates_for_requested_day")
    assert others, (
        "the later dates are still only counted, so Susie can offer to look "
        "but cannot say which date she would be looking at"
    )
    assert [o["date"] for o in others] == [
        (base + _dt.timedelta(days=7)).isoformat(),
        (base + _dt.timedelta(days=14)).isoformat(),
        (base + _dt.timedelta(days=21)).isoformat(),
    ]


async def test_each_date_carries_a_spoken_label_and_a_count():
    base, plan = _the_live_shape()
    others = (await _availability(plan, base.strftime("%A")))["other_dates_for_requested_day"]
    assert [o["times_available"] for o in others] == [7, 8, 9]
    for o in others:
        assert o["spoken"] == rt._spoken_day_label(o["date"])
        assert o["spoken"], "the model would have to render the date itself"


async def test_no_time_is_handed_over_for_those_dates():
    """The half that keeps B-108b shut. Times on an unspoken date must not
    reach the payload, or they are back in the ordinal map."""
    base, plan = _the_live_shape()
    others = (await _availability(plan, base.strftime("%A")))["other_dates_for_requested_day"]
    for o in others:
        assert set(o) == {"date", "spoken", "times_available"}


async def test_the_guidance_tells_her_to_name_them_and_forbids_times():
    base, plan = _the_live_shape()
    g = (await _availability(plan, base.strftime("%A")))["guidance"]
    assert "NAME" in g and "other_dates_for_requested_day" in g
    assert "never state a time on those dates" in g
    assert "never name a date that is not in other_dates_for_requested_day" in g
    # Still carries the B-94 rule this grew out of.
    assert "Do NOT say or imply it is all there is on that weekday" in g


def test_the_guidance_carries_no_em_dash():
    """TTS pause punctuation is chunker input and model-facing strings have
    been echoed into speech before."""
    import inspect
    src = inspect.getsource(rt._check_availability_acuity)
    block = src[src.find("other_dates_for_requested_day"):]
    assert "—" not in block[:3000]


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
async def test_the_spoken_day_is_untouched():
    """The caller still hears one date. This commit adds an offer, not a
    second readout."""
    base, plan = _the_live_shape()
    result = await _availability(plan, base.strftime("%A"))
    assert result["presentation_mode"] == "single_day"
    assert result["first_day"]["date"] == base.isoformat()
    assert result["first_day"]["slot_times"] == ["09:00"]


async def test_a_single_matching_date_names_nothing():
    """Only one occurrence in the window: there is no other date to offer, and
    the payload must not grow an empty promise."""
    base = _dt.date.today() + _dt.timedelta(days=4)
    result = await _availability({base: 3}, base.strftime("%A"))
    assert "other_dates_for_requested_day" not in result


async def test_only_the_requested_weekday_is_offered():
    """_filter_tuples_by_preference silently DROPS a day filter that matches
    nothing, so days_data can hold unrelated days. Naming one of those as
    another Tuesday is the false confidence this family exists to stop."""
    base = _dt.date.today() + _dt.timedelta(days=4)
    plan = {
        base: 1,
        base + _dt.timedelta(days=1): 5,    # the day after, NOT the weekday
        base + _dt.timedelta(days=7): 7,    # the next real occurrence
    }
    result = await _availability(plan, base.strftime("%A"))
    for o in result.get("other_dates_for_requested_day", []):
        assert _dt.date.fromisoformat(o["date"]).weekday() == base.weekday()


async def test_at_most_three_dates_are_offered():
    base = _dt.date.today() + _dt.timedelta(days=4)
    plan = {base + _dt.timedelta(days=7 * i): 3 for i in range(6)}
    plan[base] = 1
    result = await _availability(plan, base.strftime("%A"))
    assert len(result["other_dates_for_requested_day"]) <= 3
