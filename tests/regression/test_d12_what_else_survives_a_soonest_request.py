""""What else have you got" must not be disabled by an earlier soonest request.

D12, 9 Sep 2026, northgate, build 909a90ad3752 — found on the call that verified
D11, and caused by the commit two before it.

    caller: uh what's the soonest you've got
    Susie : Starting with the soonest — Number 1, Wednesday 9th September …
            Number 2, Thursday 10th … Number 3, Friday 11th …
    caller: uh that's not soon enough
    Susie : I wish I had something sooner — Today at twenty past four … (D11 ✓)
    caller: okay then what else have you got this week
    [slot_followup] every day in the sweep has been offered -- falling through
    Susie : this week I've also got Thursday at eight in the morning, or …
            Friday at eight in the morning, or half past three …

Thursday and Friday read straight back — they were Numbers 2 and 3 ninety
seconds earlier. The going-in-circles shape B-137 and D11 both exist to end.

THE CAUSE. `more_days_speech` selected its candidates with
`choose_presented_days`, which answers a DIFFERENT question — which days should
LEAD a fresh readout — and short-circuits on `caller_wants_soonest` to
`days[:max_days]`, the three EARLIEST. For a caller who asked for the soonest
those are exactly the three just heard, so the unheard filter emptied the list
and the producer declined, handing the turn to the model.

LATENT UNTIL 38709d5f. `day_preference` was previously set only by the literal
"as soon as possible"/"asap", so `caller_wants_soonest` was almost always False
here and the helper fell through to its unheard branch. Teaching the capture the
words people actually use ("soonest", "earliest", "sooner") made the
short-circuit reachable — so this is that commit's bill.

Paid by selecting the unheard days directly rather than by narrowing the
capture: the soonest ordering is RIGHT on a fresh readout, and wrong only in
this producer, where the question is "what have I NOT heard".
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.tools.slot_followup import more_days_speech, record_spoken_slots

TODAY = dt.date.today()

TIMES = [("08:00", "eight in the morning"),
         ("15:30", "half past three in the afternoon")]


def _day(i: int):
    iso = (TODAY + dt.timedelta(days=i)).isoformat()
    return {
        "date": iso, "day_label": "Day %d" % i, "times_not_shown": 0,
        "slot_times": [t for t, _ in TIMES],
        "slot_times_spoken": [s for _, s in TIMES],
        "slots": [{"start": "%sT%s:00+01:00" % (iso, t), "end": ""}
                  for t, _ in TIMES],
    }


def _session(n_days: int, heard: list, soonest: bool):
    days = [_day(i) for i in range(n_days)]
    s = {"available_days": days, "_slot_presentation_mode": "multi_day"}
    if soonest:
        s["day_preference"] = "as soon as possible"
    offered = [{"start": days[i]["slots"][0]["start"]} for i in heard]
    s["last_offered_slots"] = offered
    record_spoken_slots(s, offered)
    return s


def test_the_live_case_is_answered_not_declined():
    """7 days in the sweep, the 3 earliest heard, caller asks what else."""
    said = more_days_speech(_session(7, [0, 1, 2], soonest=True))
    assert said, "the producer declined again — the turn goes back to the model"
    assert "Day 3" in said


@pytest.mark.parametrize("soonest", [True, False])
def test_the_answer_is_the_same_either_way(soonest):
    """`caller_wants_soonest` is a standing preference about ORDERING a fresh
    readout. It must not change what "what else have you got" means."""
    said = more_days_speech(_session(7, [0, 1, 2], soonest=soonest))
    assert said and "Day 3" in said


def test_no_day_the_caller_has_already_heard_is_read_back():
    said = more_days_speech(_session(7, [0, 1, 2], soonest=True))
    for heard in ("Day 0", "Day 1", "Day 2"):
        assert heard not in said, "%s was read back to a caller who heard it" % heard


def test_the_real_end_of_the_sweep_still_declines():
    """Every day offered is the honest end of the week, and the exhaustion
    sentence — not a repeat — is the right answer to it."""
    assert more_days_speech(_session(3, [0, 1, 2], soonest=True)) is None


def test_it_hedges_when_more_unheard_days_remain_than_it_names():
    """D10's twin inside this producer: 7 days, 3 heard, 4 unheard, 3 named."""
    said = more_days_speech(_session(7, [0, 1, 2], soonest=True))
    assert said.startswith("I've got a few days —")


def test_it_claims_the_diary_when_it_names_every_unheard_day():
    """6 days, 3 heard, exactly 3 unheard — nothing is being held back."""
    said = more_days_speech(_session(6, [0, 1, 2], soonest=True))
    assert said.startswith("Here's what we've got coming up —")


def test_it_never_names_more_than_the_cap():
    said = more_days_speech(_session(20, [0], soonest=True))
    assert said.count("Number ") == 3
