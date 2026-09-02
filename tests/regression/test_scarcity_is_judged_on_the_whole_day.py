"""
Regression: "that's the only one we have that day" about a day with two slots.

B-97. `CA6fa4b4339c567e19e3fb2b47b2847dde` (26 Aug 2026, theorem_v3, build
`d6887451`, Alcester). Found by the call that was verifying B-95.

    21:42:13  caller: "uh afternoons are best for me"
              time_of_day_preference captured: afternoons (tier=hard)
    ...
    21:42:50  check_availability -> Wednesday 2 September, slot_times ["14:00"]
              _check_availability_acuity: 2026-09-02 - 2 raw slot(s) from Acuity
    21:42:51  kept scarcity sentence (that_is_the_only)
              "The available slot for Wednesday 2nd September is two in the
               afternoon, that's the only one we have that day.
               Does that work for you?"
    21:43:06  caller: "um no unfortunately it doesn't"

Wednesday 2 September holds TWO bookable slots. The caller's hard "afternoons"
kept one. They said the 2pm did not suit and were told it was the only one that
day -- so the other slot was never offered, and they hung up at the name request
a minute later. outcome=abandoned, and nobody was alerted.

TWO FACES, and fixing either alone leaves the defect reachable:

1. `_scarcity_claim_is_supported` counted `len(slot_times)` -- the SURVIVORS of
   the preference filter -- and so approved a false claim about the DAY.

2. `susie_system_prompt.py` tells the formatter it may use a completeness opener
   ("The available slot for [day_label] is [time]") when `first_day.more_times`
   is false. That flag was set ONLY by the 3-time presentation cap, never by the
   band filter, so a day cut from four slots to one read as complete. Same claim
   as face 1, in wording no banned-phrase table matches -- the recurring
   `write-gates-match-one-literal` shape.

THE FIX is neither guard nor prompt: it is the DATA. `_build_days_data` is the
one chokepoint both executors pass through, and it now counts each day before
the preference filter and publishes `times_found_on_day` / `times_not_shown`.
The guard and both `more_times` sites read that instead of counting survivors.
This mirrors what B-94 did on the DAYS axis with days_found_in_window; the times
axis simply had no equivalent.

The guard FAILS CLOSED on a missing count, which strips the sentence -- its own
docstring already argues that is the safe direction.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.media_streams.turn_handler import _scarcity_claim_is_supported
from app.tools.receptionist_tools import LONDON_TZ, _build_days_data
from tests.harness.clinic_dates import open_days

# Future days the clinic is OPEN, standing in for the literal dates this file
# was written with. It pinned 2026-09-02 and 2026-08-28, and those were the
# live incident's real dates -- accurate on the day, and by 2 Sep 2026 they were
# today and last Friday, so `_build_days_data` filtered the past slots out and
# five assertions about "nothing was withheld" started failing on the calendar
# rather than on the code. The SHAPE is what these tests are about: a day
# holding two slots, a day holding one. See tests/harness/clinic_dates.
_D_TWO, _D_NEXT, _D_ONE = (d.isoformat() for d in open_days(3, start_offset=4))


def _slots(date: str, *times: str):
    out = []
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        start = LONDON_TZ.localize(datetime(*(int(p) for p in date.split("-")), h, m))
        out.append((start, start.replace(hour=h + 1)))
    return out


# ---------------------------------------------------------------------------
# The data now describes the day, not the survivors
# ---------------------------------------------------------------------------
def test_the_live_day_reports_both_of_its_slots():
    """Wednesday 2 September, exactly as Acuity returned it."""
    day = _build_days_data(_slots(_D_TWO, "09:00", "14:00"),
                           preference="afternoon")[0]
    assert day["slot_times"] == ["14:00"], "the band filter still applies"
    assert day["times_found_on_day"] == 2
    assert day["times_not_shown"] == 1


def test_an_unfiltered_day_hides_nothing():
    day = _build_days_data(_slots(_D_TWO, "09:00", "14:00"))[0]
    assert day["times_found_on_day"] == 2
    assert day["times_not_shown"] == 0


def test_a_day_with_one_slot_is_reported_as_one():
    """Friday 28 August — 1 raw slot. The count must not inflate."""
    day = _build_days_data(_slots(_D_ONE, "14:00"), preference="afternoon")[0]
    assert day["times_found_on_day"] == 1
    assert day["times_not_shown"] == 0


def test_the_count_is_per_day_not_per_payload():
    days = _build_days_data(
        _slots(_D_TWO, "09:00", "14:00") + _slots(_D_NEXT, "13:00"),
        preference="afternoon",
    )
    by_date = {d["date"]: d for d in days}
    assert by_date[_D_TWO]["times_found_on_day"] == 2
    assert by_date[_D_NEXT]["times_found_on_day"] == 1


def test_a_preference_that_matches_nothing_hides_nothing():
    """_filter_tuples_by_preference falls back to the full set when the band
    matches no slot. Nothing was withheld, so nothing may be reported as
    withheld."""
    day = _build_days_data(_slots(_D_TWO, "09:00", "10:00"),
                           preference="evening")[0]
    assert day["times_not_shown"] == 0
    assert day["times_found_on_day"] == 2


# ---------------------------------------------------------------------------
# Face 1 — the explicit claim
# ---------------------------------------------------------------------------
def test_the_live_defect_the_claim_is_refused():
    days = _build_days_data(_slots(_D_TWO, "09:00", "14:00"),
                            preference="afternoon")
    assert _scarcity_claim_is_supported({"available_days": days}) is False


def test_b92_still_works_when_the_claim_is_true():
    """The whole point of B-92 (CA45357d84): when the day really does hold one
    slot, the sentence is the answer and stripping it strands the caller."""
    days = _build_days_data(_slots(_D_ONE, "14:00"), preference="afternoon")
    assert _scarcity_claim_is_supported({"available_days": days}) is True


def test_a_missing_count_fails_closed():
    """An older or hand-built payload cannot support the claim."""
    days = [{"date": _D_TWO, "day_label": "a hand-built day",
             "slot_times": ["14:00"]}]
    assert _scarcity_claim_is_supported({"available_days": days}) is False


@pytest.mark.parametrize(
    "session",
    [{}, {"available_days": []}, {"available_days": None},
     {"available_days": [{"times_found_on_day": 1}, {"times_found_on_day": 1}]}],
    ids=["empty", "no-days", "null-days", "two-days"],
)
def test_the_claim_needs_exactly_one_day_on_the_table(session):
    assert _scarcity_claim_is_supported(session) is False


# ---------------------------------------------------------------------------
# Face 2 — the completeness opener
# ---------------------------------------------------------------------------
def test_hidden_times_set_more_times_on_the_google_path():
    """_cap_presented_slots gates the opener for VE and JV."""
    from app.tools.receptionist_tools import _cap_presented_slots

    days = _build_days_data(_slots(_D_TWO, "09:00", "14:00"),
                            preference="afternoon")
    out = _cap_presented_slots({"available_days": days})
    assert out["presentation_mode"] == "single_day"
    assert out["first_day"].get("more_times") is True, (
        "a day showing 1 of its 2 slots is not complete, so the formatter must "
        "not be told it may use a completeness opener"
    )


def test_a_genuinely_complete_day_keeps_more_times_off():
    from app.tools.receptionist_tools import _cap_presented_slots

    days = _build_days_data(_slots(_D_ONE, "14:00"), preference="afternoon")
    out = _cap_presented_slots({"available_days": days})
    assert not out["first_day"].get("more_times")


def test_the_acuity_branch_sets_more_times_from_the_same_field():
    """The Acuity executor does not call _cap_presented_slots (Theorem short-
    circuits to it), so the flag is set inline and must use the same field."""
    import inspect

    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools)
    i = src.index('_result["first_day"] = _fd')
    window = src[i - 1200:i]
    assert "times_not_shown" in window, (
        "the Acuity single_day branch still sets more_times from the "
        "presentation cap alone — a band-filtered day reads as complete"
    )


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------
def test_the_count_is_taken_before_the_preference_filter():
    """Both executors reach _build_days_data, so the count belongs there and
    nowhere else. If the count moved after the filter it would always equal
    len(slot_times) and every test above would pass vacuously."""
    import inspect

    from app.tools.receptionist_tools import _build_days_data as fn

    src = inspect.getsource(fn)
    assert src.index("_found_per_day[") < src.index(
        "_filter_tuples_by_preference"
    ), "the day is being counted AFTER the filter that hides part of it"
