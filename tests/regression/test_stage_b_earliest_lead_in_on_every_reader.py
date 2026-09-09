"""Stage B — "The earliest I have is ..." reaches every clinic, not just Acuity.

`docs/plan/ONE_PRESENTATION_LAYER.md`, owner decision 2 of 2026-09-09. The warm
lead-in was set only at the end of `_check_availability_acuity`, so Theorem
opened warm and northgate, JV and Vital Edge opened flat on the identical
request. `_cap_presented_slots` now sets it for the three readers that come
through it.

That sentence makes TWO claims and both are pinned here:

  * the caller asked for the soonest  -> `caller_wants_soonest`
  * the day read out IS the soonest   -> `_earliest_available_date`

The within-day half stays where it was, in `earliest_lead_in_is_true`, which
this file asserts is still consulted rather than replaced.

The containment claim is the important one: ACUITY MUST NOT MOVE. Stage A routes
it through `_cap_presented_slots` only on multi_day, so this code cannot run on
a Theorem payload -- asserted structurally at the bottom rather than trusted.
"""

import inspect

import pytest

from app.tools import receptionist_tools
from app.tools.receptionist_tools import (
    _cap_presented_slots,
    _earliest_available_date,
)
from app.tools.slot_offer import build_slot_offer, earliest_lead_in_is_true

_D1, _D2 = "2026-09-14", "2026-09-15"


def _day(date, label, times):
    spoken = ["%s o'clock" % t[:2] for t in times]
    return {
        "date": date, "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [{"start": "%sT%s:00+01:00" % (date, t), "date": date,
                   "day_label": label, "time": t, "spoken": s}
                  for t, s in zip(times, spoken)],
    }


def _result(days):
    return {"available_days": list(days), "total_days": len(days)}


def _soonest(pref="as soon as possible"):
    return {"day_preference": pref}


# ---------------------------------------------------------------------------
# _earliest_available_date -- pure, and it must not trust the list's order
# ---------------------------------------------------------------------------

def test_the_earliest_date_is_the_minimum_not_the_first_entry():
    """Producers sort soonest-first today; none of them promises to."""
    out_of_order = [_day(_D2, "Tuesday", ["09:00"]), _day(_D1, "Monday", ["09:00"])]
    assert _earliest_available_date(out_of_order) == _D1


@pytest.mark.parametrize("junk", [None, [], [1, 2], [{}], "string",
                                  [{"date": None}]])
def test_an_unreadable_day_list_has_no_earliest_date(junk):
    assert _earliest_available_date(junk) is None


# ---------------------------------------------------------------------------
# Condition 1 -- the caller must have asked for the soonest
# ---------------------------------------------------------------------------

def test_a_soonest_request_on_the_only_day_gets_the_lead_in():
    out = _cap_presented_slots(
        _result([_day(_D1, "Monday", ["09:00", "10:00"])]), _soonest())
    assert out["presentation_mode"] == "single_day"
    assert out.get("lead_in") == "earliest"


@pytest.mark.parametrize("pref", [None, "", "next week", "whenever",
                                  "thursday", "in a fortnight"])
def test_no_lead_in_when_the_caller_did_not_ask_for_the_soonest(pref):
    """It answers a question nobody asked -- and when the caller NAMED a day it
    reads as a scarcity note: they wanted Thursday, not a ranking."""
    out = _cap_presented_slots(_result([_day(_D1, "Monday", ["09:00"])]),
                               {"day_preference": pref})
    assert "lead_in" not in out, pref


@pytest.mark.parametrize("session", [None, {}, {"day_preference": 7}])
def test_a_missing_or_junk_session_gets_no_lead_in(session):
    """Deny by default. Silence is safe; a false ranking claim is not."""
    out = _cap_presented_slots(_result([_day(_D1, "Monday", ["09:00"])]), session)
    assert "lead_in" not in out


# ---------------------------------------------------------------------------
# Condition 2 -- the day read out must BE the soonest
# ---------------------------------------------------------------------------

def test_the_lead_in_stands_when_the_presented_day_is_the_earliest():
    days = [_day(_D1, "Monday", ["09:00"]), _day(_D2, "Tuesday", ["09:00"])]
    out = _cap_presented_slots(_result(days), _soonest("tomorrow"),
                               mode="single_day")
    assert out["presentation_mode"] == "single_day"
    assert out["first_day"]["date"] == _D1
    assert out.get("lead_in") == "earliest"


def test_the_claim_is_refused_when_first_day_is_not_the_earliest():
    """The gap Acuity does not have to close and this path does.

    Acuity sets the lead-in only when the caller asked for the soonest AND its
    result is a single day, so the day is earliest by construction. Here
    `single_day` comes from the DATA, and `day_preference` holds "tomorrow" and
    "this week" as well as "as soon as possible" -- so without this check a
    caller could be told Tuesday was the earliest thing available while Monday
    sat in the same payload.
    """
    days = [_day(_D2, "Tuesday", ["09:00"]), _day(_D1, "Monday", ["09:00"])]
    out = _cap_presented_slots(_result(days), _soonest("tomorrow"),
                               mode="single_day")
    assert out["first_day"]["date"] == _D2
    assert "lead_in" not in out


# ---------------------------------------------------------------------------
# A reader that computed its own lead_in keeps it
# ---------------------------------------------------------------------------

def test_a_lead_in_already_on_the_payload_is_never_overwritten():
    r = _result([_day(_D1, "Monday", ["09:00"])])
    r["lead_in"] = "also"
    out = _cap_presented_slots(r, _soonest())
    assert out["lead_in"] == "also"


# ---------------------------------------------------------------------------
# multi_day never carries one (B-125)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [{}, {"mode": "multi_day"}])
def test_multi_day_gets_no_lead_in_however_the_mode_was_reached(kwargs):
    days = [_day(_D1, "Monday", ["09:00"]), _day(_D2, "Tuesday", ["09:00"])]
    out = _cap_presented_slots(_result(days), _soonest(), **kwargs)
    assert out["presentation_mode"] == "multi_day"
    assert "lead_in" not in out


# ---------------------------------------------------------------------------
# The bookable set is still never trimmed by any of this
# ---------------------------------------------------------------------------

def test_setting_a_lead_in_does_not_touch_available_days():
    days = [_day(_D1, "Monday", ["09:00", "10:00", "11:00", "12:00"])]
    out = _cap_presented_slots(_result(days), _soonest())
    assert out.get("lead_in") == "earliest"
    assert [d["date"] for d in out["available_days"]] == [_D1]
    assert out["available_days"][0]["slot_times"] == [
        "09:00", "10:00", "11:00", "12:00"]


# ---------------------------------------------------------------------------
# CONTAINMENT -- Acuity cannot reach this code
# ---------------------------------------------------------------------------

def test_the_acuity_branch_only_caps_multi_day():
    """Structural. Stage A wired `_cap_presented_slots` into the Acuity branch
    behind `presentation_mode == "multi_day"`, and multi_day never carries a
    lead-in -- so Theorem's single_day lead-in stays the one Acuity computes.

    Asserted by reading the dispatcher rather than by trusting the comment: if
    someone widens that condition, Theorem starts taking its lead-in from a
    different rule, and this says so.
    """
    src = inspect.getsource(receptionist_tools._exec_check_availability)
    body = src.split("if uses_acuity(session):", 1)[1]
    body = body[:body.index("return _acuity_result")]
    assert '_acuity_result.get("presentation_mode") == "multi_day"' in body, (
        "the Acuity branch no longer gates _cap_presented_slots on multi_day -- "
        "stage B's containment depended on that")


def test_acuity_keeps_its_own_asap_signals():
    """The other half of the same claim: Acuity still decides its lead-in from
    the tool's date_hint, not from day_preference."""
    src = inspect.getsource(receptionist_tools._check_availability_acuity)
    assert '_result["lead_in"] = "earliest"' in src
    assert "_ASAP_SIGNALS" in src


# ---------------------------------------------------------------------------
# The within-day guard is still the last word
# ---------------------------------------------------------------------------

def test_the_within_day_guard_still_refuses_a_trimmed_first_slot():
    """B-125 is unchanged by stage B: a payload may carry lead_in="earliest"
    and still be refused downstream, because B-116 removed the day's real
    earliest time from what is about to be spoken."""
    full = _day(_D1, "Monday", ["09:00", "10:00", "11:00"])
    presented = _day(_D1, "Monday", ["10:00", "11:00"])
    assert earliest_lead_in_is_true(full, full) is True
    assert earliest_lead_in_is_true(full, presented) is False


def test_llm_stream_still_consults_that_guard():
    """Stage B adds a producer, not a bypass."""
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "earliest_lead_in_is_true(_full, _fd)" in src


def test_the_opener_actually_changes_the_sentence():
    """End of the chain: the flag has to reach words, or none of this matters."""
    day = _day(_D1, "Monday", ["09:00"])
    warm = " ".join(build_slot_offer([day], lead_in="earliest").chunks)
    flat = " ".join(build_slot_offer([day]).chunks)
    assert warm != flat
    assert "earliest" in warm.lower()
    assert "earliest" not in flat.lower()
