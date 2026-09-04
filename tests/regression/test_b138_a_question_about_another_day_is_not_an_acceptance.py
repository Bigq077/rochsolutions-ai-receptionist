"""A QUESTION about Wednesday was resolved as an ACCEPTANCE of Thursday.

B-138 -- CAdd64c466dc13978306e5558817ce147e, northgate, 4 September 2026:

    11:34:44  'um do you have any do you have a 10 past 12 for wednesday
               for example'
    11:34:44  caller ACCEPTED 2026-09-10T12:10:00+01:00      <- a THURSDAY

The caller was ASKING whether another day had that time. The resolver read it
as picking the slot already on the table, on a different day entirely.

── WHERE IT COMES FROM ────────────────────────────────────────────────────────
Step 2 of `slot_accepted_by_caller` ends in a last-resort branch: if the offer
holds exactly ONE date, take it, because "the caller naming only a time has not
left anything ambiguous". Its own comment states the premise --

    "this only fires when the offer holds exactly ONE date, so it cannot guess
     between days"

-- and the premise is FALSE the moment the caller names a day the offer does
not hold. Nothing checked for it.

── WHY IT IS FIXED HERE AND NOT IN B-134 ──────────────────────────────────────
B-134 (reverted, 3b88d9d4) made the branch REACHABLE by recording a stood-down
sentence's single date, and the wrong-day acceptance above is what it produced
on a live call. But the hole is older than B-134 and independent of it: any
offer that legitimately holds one date -- the ordinary shape after Susie
narrows to a day, which is exactly what
`test_a_time_only_pick_after_the_day_is_settled` pins -- exposes it. Fixing it
inside B-134 would have left the branch wrong and merely hard to reach.

Declining is the safe direction and an explicitly cheap one: the resolver's own
docstring says a decline costs one clarifying question, while a wrong resolve
"would pin it into the next readout and read it back as an appointment".
"""
from __future__ import annotations

import inspect

import pytest

from app.tools import slot_followup
from app.tools.slot_followup import (
    _names_a_different_weekday,
    record_spoken_slots,
    slot_accepted_by_caller,
)


def _day(date, label, times, spoken):
    return {
        "date": date, "day_label": label,
        "slot_times": list(times), "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
    }


# Thursday 10 September, as it stood on the table when he asked.
THURSDAY = [
    _day("2026-09-10", "Thursday 10th September", ["12:10", "16:20"],
         ["ten past twelve", "twenty past four in the afternoon"]),
]


def _session(days):
    session = {
        "available_days": days,
        "last_offered_slots": [
            {"start": f"{d['date']}T{t}:00+01:00", "end": ""}
            for d in days for t in d["slot_times"]
        ],
        "slot_labels": [d["day_label"] for d in days],
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in days
        for t, sp in zip(d["slot_times"], d["slot_times_spoken"])
    ])
    return session


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_utterance_resolves_to_nothing():
    """Verbatim, at 11:34:44. Anything but None is a booking on the wrong day."""
    got = slot_accepted_by_caller(
        _session(THURSDAY),
        "um do you have any do you have a 10 past 12 for wednesday for example",
    )
    assert got is None


@pytest.mark.parametrize("utterance", [
    "do you have a 10 past 12 for wednesday",
    "is there anything at twenty past four on friday",
    "what about tuesday, ten past twelve",
])
def test_a_day_the_offer_does_not_hold_never_resolves(utterance):
    """Each of these resolves to the Thursday slot without the fix. Verified by
    neutering it -- a case that declines for some OTHER reason proves nothing
    here, so those live in the test below instead."""
    assert slot_accepted_by_caller(_session(THURSDAY), utterance) is None


@pytest.mark.parametrize("utterance,already_caught_by", [
    # "instead" is a request for other slots, refused at the top of the
    # resolver before the day ladder is ever reached.
    ("have you got ten past twelve on the monday instead",
     "utterance_requests_more_slots"),
    # No time named, and the day holds two spoken slots, so the time step
    # declines for ambiguity even when the day step has guessed wrong.
    ("wednesday please", "the time step finding two candidates"),
])
def test_the_wrong_day_is_refused_more_than_once(utterance, already_caught_by):
    """Defence in depth, recorded rather than assumed. These stay green with
    B-138 neutered -- they are NOT what pins it -- but they are the second
    lock on the same door, and a future change that removes one should have to
    notice this file."""
    assert slot_accepted_by_caller(_session(THURSDAY), utterance) is None


# ---------------------------------------------------------------------------
# What the branch is FOR still works -- the whole reason it was added
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("utterance,expected", [
    ("ten past twelve works",              "2026-09-10T12:10:00+01:00"),
    ("um 10 past 12 works",                "2026-09-10T12:10:00+01:00"),
    ("twenty past four in the afternoon",  "2026-09-10T16:20:00+01:00"),
    # The offer's OWN day, named. Not a contradiction, so not a decline.
    ("thursday at ten past twelve",        "2026-09-10T12:10:00+01:00"),
    ("yeah thursday ten past twelve",      "2026-09-10T12:10:00+01:00"),
])
def test_a_time_only_pick_is_untouched(utterance, expected):
    assert slot_accepted_by_caller(_session(THURSDAY), utterance) == expected


# ---------------------------------------------------------------------------
# The predicate, on its own
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,date,expected", [
    ("do you have a 10 past 12 for wednesday", "2026-09-10", True),
    ("do you have 10 past 12 on thursday",     "2026-09-10", False),
    ("yeah 12:10 works",                       "2026-09-10", False),
    ("Wednesday!",                             "2026-09-10", True),
    # Several days named, one of them the offer's: not a contradiction.
    ("is it saturday or thursday",             "2026-09-10", False),
    ("is it saturday or monday",               "2026-09-10", True),
    # An ISO start rather than a bare date. The call site passes [:10] of one,
    # but the predicate must not depend on which it is handed.
    ("thursday", "2026-09-10T12:10:00+01:00", False),
])
def test_the_predicate(text, date, expected):
    assert _names_a_different_weekday(text, date) is expected


@pytest.mark.parametrize("junk", ["", None, "   ", 0, [], {}])
def test_junk_text_declines_nothing(junk):
    """It must never turn an ordinary acceptance into a decline by accident."""
    assert _names_a_different_weekday(junk, "2026-09-10") is False


@pytest.mark.parametrize("junk", ["", None, "not-a-date", 0, [], {}, "2026-13-45"])
def test_an_unreadable_date_declines_nothing(junk):
    """A check that cannot read its input must not be the thing that pins an
    appointment -- it fails towards the behaviour that existed before it."""
    assert _names_a_different_weekday("wednesday please", junk) is False


# ---------------------------------------------------------------------------
# Wired, not merely callable. B-134 shipped tests that stayed green when the
# fix was neutered, because they exercised the helper and not the call site.
# ---------------------------------------------------------------------------
def test_the_fallback_consults_the_predicate():
    src = inspect.getsource(slot_followup.slot_accepted_by_caller)
    assert "_names_a_different_weekday(text, _only)" in src
    at = src.index("_names_a_different_weekday(text, _only)")
    assert "return None" in src[at:at + 120]


def test_it_guards_the_one_date_branch_and_not_something_else():
    """The predicate belongs to the last-resort branch only. Applied higher up
    it would start declining picks the three steps above resolve correctly --
    a bare weekday against a multi-day offer is exactly what
    `_offered_day_by_weekday` is for."""
    src = inspect.getsource(slot_followup.slot_accepted_by_caller)
    assert src.count("_names_a_different_weekday") == 1
    at = src.index("_names_a_different_weekday")
    assert "if len(_dates) == 1:" in src[at - 400:at]
