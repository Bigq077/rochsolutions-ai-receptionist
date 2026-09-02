"""
Regression: a true scarcity claim about a day the CALLER never named.

B-108. CA1b7b2c58b0a0d43aeb5724abc1b11359 (Theorem, 27 Aug 2026, build
1ec52e26, Alcester). Found while live-verifying B-86.

    22:25:01  caller: "um do you have anything on tuesday"
              day_preference captured: tuesday
    22:25:05  check_availability args={"date_hint": "Tuesday"}
              [ms_tools] week filter bypassed - no week anchor in date_hint
              _check_availability_acuity: 102 total raw slot(s) over 30 days
    22:25:18  kept scarcity sentence (that_is_the_only)
              "That's the only slot on Tuesday 1st September -
               nine in the morning. Would that work for you?"

The same 30-day scan is in the same log, and it found four Tuesdays:

    2026-09-01  1 raw slot     <- the one she read out
    2026-09-08  7 raw slots
    2026-09-15  8 raw slots
    2026-09-22  9 raw slots

24 more Tuesday slots the caller was never told about, and a sentence that
tells them Tuesdays are all but gone at this clinic.

WHY EVERY EXISTING CHECK PASSED
-------------------------------
`_scarcity_claim_is_supported` asks three questions - one day in
available_days, one time in slot_times, times_found_on_day == 1 - and on this
call all three were honestly true. 1 September really did hold exactly one
slot. B-97 had already fixed the counting so the claim is judged against the
DAY rather than the survivors of a band filter, and that fix worked here.

The gap is that every question interrogates the day, and none asks where the
day came from. `_check_availability`'s `_is_specific_day` has two arms:

    (_week_range is not None and _week_range[0] == _week_range[1])   # a DATE
    or (_has_weekday_name and not _has_week_anchor)                  # a WEEKDAY

The first is a date the caller named. The second is this code choosing the next
occurrence on their behalf - deliberate, documented behaviour, and not what is
being changed here. But a scarcity claim about a day the caller never named
answers a question nobody asked, and it is heard as a claim about the weekday
they DID ask about.

SO THE CLAIM IS JUDGED AGAINST THE QUESTION, NOT ONLY THE DAY
-------------------------------------------------------------
The executor now records which arm won, and the guard stands down on the
bare-weekday one. Judged on DATA, in keeping with this guard's existing
doctrine: not on the caller's wording, and not on a literal of Susie's speech.

CA45357d84 - the call B-92 built this guard for - is unaffected. There the
caller had been given a date and was asking about that date, so the flag is
False and the sentence still reaches them. That call is pinned below, because
re-suppressing it would strand a caller who asks four times in forty seconds.
"""
from __future__ import annotations

import inspect
from datetime import datetime

from app.media_streams.turn_handler import _scarcity_claim_is_supported
from app.tools import receptionist_tools
from app.tools.receptionist_tools import LONDON_TZ, _build_days_data
from tests.harness.clinic_dates import open_days

# The incident's real dates, now in the past — same rot as its sibling file.
# See tests/harness/clinic_dates.
_D_ONE, _D_TWO = (d.isoformat() for d in open_days(2, start_offset=4))


def _slots(date: str, *times: str):
    out = []
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        start = LONDON_TZ.localize(
            datetime(*(int(p) for p in date.split("-")), h, m)
        )
        out.append((start, start.replace(hour=h + 1)))
    return out


def _tuesday_1st():
    """Exactly what the live payload held: 1 September, one 09:00 slot."""
    return _build_days_data(_slots(_D_ONE, "09:00"))


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_claim_is_refused_when_the_day_was_picked_from_a_bare_weekday():
    """The one assertion. "anything on tuesday" -> this code chose the 1st."""
    session = {
        "available_days": _tuesday_1st(),
        "day_chosen_from_bare_weekday": True,
    }
    assert _scarcity_claim_is_supported(session) is False, (
        "a scarcity claim about 1 September was approved for a caller who "
        "asked about Tuesdays - the 8th, 15th and 22nd held 24 more slots"
    )


def test_the_day_itself_is_still_honestly_described():
    """The defect is not in the counting - B-97's fix is intact. 1 September
    really did hold one slot, which is why every existing check passed."""
    day = _tuesday_1st()[0]
    assert day["slot_times"] == ["09:00"]
    assert day["times_found_on_day"] == 1
    assert day["times_not_shown"] == 0


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_a_caller_who_named_the_date_still_gets_the_answer():
    """CA45357d84, the call this guard exists for. The caller was working from
    a date, so the day was not chosen for them and the sentence is the answer.
    Re-suppressing it strands them re-offering the same time as a question."""
    session = {
        "available_days": _tuesday_1st(),
        "day_chosen_from_bare_weekday": False,
    }
    assert _scarcity_claim_is_supported(session) is True


def test_an_absent_flag_reads_as_no_change():
    """Only an availability call sets the flag, and only that same call can put
    a scarcity claim on the table. A session without it must behave exactly as
    it did before B-108."""
    assert _scarcity_claim_is_supported(
        {"available_days": _tuesday_1st()}
    ) is True


def test_the_b97_refusal_is_untouched():
    """A band filter hiding a second slot is still refused, flag or no flag."""
    days = _build_days_data(_slots(_D_TWO, "09:00", "14:00"),
                            preference="afternoon")
    for flag in (True, False):
        assert _scarcity_claim_is_supported({
            "available_days": days, "day_chosen_from_bare_weekday": flag,
        }) is False


def test_multiple_days_are_still_refused():
    days = _build_days_data(
        _slots(_D_ONE, "09:00") + _slots("2026-09-08", "10:00")
    )
    assert _scarcity_claim_is_supported({"available_days": days}) is False


# ---------------------------------------------------------------------------
# The coupling. The guard is only as good as the flag reaching it.
# ---------------------------------------------------------------------------
def _availability_source() -> str:
    return inspect.getsource(receptionist_tools)


def test_the_flag_is_written_from_the_bare_weekday_arm():
    src = _availability_source()
    assert (
        "_bare_weekday_pick = bool(_has_weekday_name and not _has_week_anchor)"
        in src
    ), (
        "the flag is no longer derived from the bare-weekday arm, so the guard "
        "cannot tell a caller-named date from one this code chose"
    )
    assert 'session["day_chosen_from_bare_weekday"] = _bare_weekday_pick' in src


def test_the_flag_is_written_unconditionally():
    """Set on EVERY availability call, both arms. Written only when True it
    would latch, and a later named-date call would inherit the suppression."""
    src = _availability_source()
    assign = 'session["day_chosen_from_bare_weekday"] = _bare_weekday_pick'
    indents = [
        len(line) - len(line.lstrip())
        for line in src.splitlines() if assign in line
    ]
    assert indents, "the flag assignment has gone"
    assert indents == [8], (
        f"the assignment sits at indent {indents}, not the executor body - "
        f"nested under a condition it would leave the previous turn's value "
        f"in place on the turns that skip it"
    )
