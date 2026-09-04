"""P8 — a day the clinic is CLOSED was reported to the model as "too soon to book".

THE DEFECT

`_check_availability_acuity` captures `raw_slot_count` BEFORE any filtering
(`receptionist_tools.py`, "count BEFORE lead-time filter"), then runs three
filters in sequence: lead time, working hours, bank holidays. The empty-result
branch afterwards asked only

    if raw_slot_count > 0:   ->  error="lead_time_limited"

so it attributed the empty list to the FIRST filter no matter which one had
actually emptied it. A Sunday, a day outside opening hours, or a bank holiday
all came back as

    "There are N slot(s) available at Alcester today but all start within
     2 hours — too soon to book."

which is false on every count: the clinic is shut, the times are not within two
hours, and nothing about waiting would help.

WHY IT IS WORTH A FIX RATHER THAN A COMMENT

`lead_time_limited` is not just wording. It instructs the model to **re-call
check_availability once with the same parameters** (`flow.py:2799`, `:3284`).
For a genuine lead-time squeeze that is reasonable — the two-hour window moves.
For a closed day the second Acuity round-trip is guaranteed to return exactly
the same thing, so the caller waits through a full availability fetch that
cannot change the answer. The misclassification buys a retry that is provably
useless, on the one path where latency is already the open complaint.

THE FIX

Record `after_lead_time = len(slots)` immediately after the lead-time filter and
before the other two, then split the branch: `lead_time_limited` only when the
lead-time filter is what emptied the list, and a new `closed_that_day`
otherwise. Theorem-only in practice — this function is the Acuity path, which
Theorem short-circuits to — but the fix is in the shared classifier, not behind
a clinic check.

Deterministic: no model, no network, no Acuity. The adapter is a fake.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import LONDON_TZ

LOCATION = "alcester"


@dataclass
class _Slot:
    start_time: dt.datetime
    end_time: dt.datetime


class _FakeAdapter:
    """Returns whatever the test hands it, without touching Acuity."""

    def __init__(self, slots):
        self._slots = slots

    async def get_available_slots(self, **_kwargs):
        return list(self._slots)


def _a_weekday_well_past_the_lead_time_window():
    """A date far enough out that the 2h filter cannot be the one that bites.

    Deliberately computed from `today` rather than hardcoded: a fixed date
    drifts past and the test would start passing for the wrong reason.
    """
    d = dt.date.today() + dt.timedelta(days=10)
    while d.weekday() != 2:  # a Wednesday, so no weekend special-casing
        d += dt.timedelta(days=1)
    return d


def _slots_on(day, hour):
    """Timezone-AWARE, because the lead-time filter compares against
    `datetime.now(LONDON_TZ)` and naive datetimes raise there instead of
    reaching the branch under test."""
    start = LONDON_TZ.localize(dt.datetime.combine(day, dt.time(hour, 0)))
    return [_Slot(start, start + dt.timedelta(minutes=60))]


@pytest.fixture
def _no_bank_holidays(monkeypatch):
    """Isolate the working-hours filter from the bank-holiday one.

    Patched at `_closed_dates_for`, which is what this function actually
    calls — `_fetch_uk_bank_holidays` is one layer below it and patching there
    would leave the clinic's open_on_bank_holidays branch in play."""
    async def _none(_clinic_cfg):
        return frozenset()
    monkeypatch.setattr(rt, "_closed_dates_for", _none)


def _install(monkeypatch, slots, working_hours):
    monkeypatch.setattr(rt, "_get_acuity_adapter", lambda: _FakeAdapter(slots))
    import app.clinic_config as cc
    monkeypatch.setattr(
        cc, "get_clinic",
        lambda *_a, **_k: {"location_working_hours": {LOCATION: working_hours}},
    )


OPEN_9_TO_5 = {"mon": (9, 17), "tue": (9, 17), "wed": (9, 17),
               "thu": (9, 17), "fri": (9, 17), "sat": None, "sun": None}


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

async def test_a_day_outside_opening_hours_is_not_called_too_soon(
    monkeypatch, _no_bank_holidays
):
    """The live shape: Acuity returns a 20:00 slot, the clinic shuts at 17:00.

    Ten days out, so the lead-time filter provably removes nothing — which is
    exactly the case the old branch could not distinguish.
    """
    day = _a_weekday_well_past_the_lead_time_window()
    _install(monkeypatch, _slots_on(day, 20), OPEN_9_TO_5)

    result = await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )

    assert result.get("error") != "lead_time_limited", (
        "a day emptied by the working-hours filter is still being reported as "
        "a two-hour lead-time problem — which also tells the model to re-call "
        "check_availability for an answer that cannot change"
    )
    assert result.get("error") == "closed_that_day"


async def test_the_detail_the_model_reads_does_not_claim_a_lead_time(
    monkeypatch, _no_bank_holidays
):
    """`error_detail` is what actually reaches the model, so it is asserted.

    A right error code with the old sentence attached would still put the
    falsehood in front of the caller.
    """
    day = _a_weekday_well_past_the_lead_time_window()
    _install(monkeypatch, _slots_on(day, 20), OPEN_9_TO_5)

    detail = (await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )).get("error_detail", "")

    assert "within 2 hours" not in detail
    assert "too soon" not in detail
    assert "closed" in detail.lower()


async def test_a_closed_weekday_is_reported_the_same_way(
    monkeypatch, _no_bank_holidays
):
    """`None` for the weekday — the other half of the working-hours filter."""
    day = _a_weekday_well_past_the_lead_time_window()
    shut_on_wednesday = dict(OPEN_9_TO_5, wed=None)
    _install(monkeypatch, _slots_on(day, 10), shut_on_wednesday)

    result = await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )
    assert result.get("error") == "closed_that_day"


async def test_a_bank_holiday_is_reported_the_same_way(monkeypatch):
    """The third filter. Same misattribution, and the one that produced the
    original bank-holiday report."""
    day = _a_weekday_well_past_the_lead_time_window()
    _install(monkeypatch, _slots_on(day, 10), OPEN_9_TO_5)

    async def _that_day(_clinic_cfg): return frozenset({day})
    monkeypatch.setattr(rt, "_closed_dates_for", _that_day)

    result = await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )
    assert result.get("error") == "closed_that_day"


# ---------------------------------------------------------------------------
# What must NOT change — the case the old code was right about
# ---------------------------------------------------------------------------

async def test_a_real_lead_time_squeeze_still_says_so(
    monkeypatch, _no_bank_holidays
):
    """The retry instruction is CORRECT here and must survive the fix.

    Everything Acuity returned is inside the two-hour window, so the lead-time
    filter really is what emptied the list. Losing this would be the fix
    overshooting into the case it was meant to leave alone.
    """
    soon = dt.datetime.now(LONDON_TZ) + dt.timedelta(minutes=30)
    # Opening hours wide enough that only the lead-time filter can bite.
    always_open = {d: (0, 24) for d in
                   ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    _install(monkeypatch, [_Slot(soon, soon + dt.timedelta(minutes=60))],
             always_open)

    result = await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )
    assert result.get("error") == "lead_time_limited"
    assert "too soon" in result.get("error_detail", "")


async def test_no_slots_at_all_is_still_no_availability(
    monkeypatch, _no_bank_holidays
):
    """Acuity returned nothing, so no filter emptied anything. `raw_slot_count`
    is 0 and neither new branch may claim the day."""
    _install(monkeypatch, [], OPEN_9_TO_5)

    result = await rt._check_availability_acuity(
        {"location": LOCATION}, {"clinic_id": "theorem"}
    )
    assert result.get("error") == "no_availability"


# ---------------------------------------------------------------------------
# The structural pin
# ---------------------------------------------------------------------------

def test_the_lead_time_count_is_taken_before_the_other_two_filters():
    """The whole fix is WHERE the count is taken.

    `after_lead_time` measured after the working-hours or bank-holiday filter
    would be back to conflating them, and every behavioural test above would
    still pass on some inputs. Pinned by source because the ordering is the
    invariant, and nothing else expresses it.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "app" / "tools" / "receptionist_tools.py").read_text(
        encoding="utf-8", errors="replace")

    i_count = src.find("after_lead_time = len(slots)")
    i_hours = src.find("_filter_slots_by_working_hours(slots, location")
    i_bh = src.find("bank_holidays = await _closed_dates_for(clinic_cfg)")

    assert i_count != -1, "the lead-time count is gone; P8 is back"
    assert i_hours != -1 and i_bh != -1, "filter call sites moved — re-aim this"
    assert i_count < i_hours < i_bh, (
        "after_lead_time must be measured BEFORE the working-hours and "
        "bank-holiday filters, or it cannot tell which filter emptied the list"
    )
