"""Saying no to a day, while naming it, resolved to a slot on that day.

`utterance_is_a_request_not_a_pick` already claims this ground. Its docstring
says, in terms:

    a REJECTION is a negator sitting in front of the position

but `_NEGATED_POSITION_RE` only covers POSITIONS -- "not the first one". A
negated DAY or time walked straight through, because naming a day IS a
selection on a multi-day offer and nothing asked whether the caller had said no
to it:

    "no saturday is not soon enough i need to be seen as soon as possible"
    "uh yeah monday doesn't work"
    "no no saturday doesn't work i need one now"

All three resolved to a slot on the day being refused.

-- HOW IT WAS FOUND, WHICH IS THE POINT --------------------------------------
Not by a phone call. `197e0bae` widened the re-query guard to consult
`slot_accepted_by_caller`, and that guard BLOCKS check_availability. Measuring
the change before shipping it -- 307 turns newly blocked across 921 calls --
and reading all of them turned up these five.

A resolver that only PINS a choice can afford to be slightly generous. The same
resolver feeding a blocking guard cannot: refusing the lookup for a caller who
has just said "that doesn't work" is worse than the defect being fixed. The
widening is what made this a defect, and it was found in the same hour.

-- THE BIAS IS DELIBERATE ----------------------------------------------------
A leading "no" is a rejection only when it is not "no problem" / "no worries" /
"no rush", which are agreement. Everything else needs an explicit negation
attached to WORKING or to being soon enough. A miss costs the old behaviour; a
false positive costs a caller the pick they just made. So the doubt goes to
not-a-rejection, and `test_a_softener_is_not_a_rejection` is the test that
holds that line.

-- MEASURED -----------------------------------------------------------------
`scripts/replay_slot_decisions.py` over the stored corpus: **exactly 5 turns
changed of 1789**, every one of them a rejection, `accepted` going from a slot
to None. No pick anywhere in the corpus stopped resolving.

The intent classification was wrong on the same turns and is corrected by the
same change -- `Intent.SLOT_PICKED` -> `Intent.EARLIEST` / `Intent.NAMED_DAY` --
so the hold speech was choosing its wording as though the caller had chosen.
"""
from __future__ import annotations

import pytest

from app.tools.slot_offer import apply_offer_to_session
from app.tools.slot_followup import (
    slot_accepted_by_caller,
    utterance_is_a_request_not_a_pick,
)

_DAY = "2026-09-07"
_TIMES = [("08:00", "eight in the morning"),
          ("13:00", "one in the afternoon"),
          ("15:00", "three in the afternoon")]


def _session():
    day = {
        "date": _DAY, "day_label": "Monday 7th September",
        "slot_times": [t for t, _ in _TIMES],
        "slot_times_spoken": [s for _, s in _TIMES],
        "slots": [{"start": "%sT%s:00+01:00" % (_DAY, t), "date": _DAY,
                   "day_label": "Monday 7th September", "time": t,
                   "spoken": s} for t, s in _TIMES],
    }
    s = {"available_days": [day]}
    apply_offer_to_session(
        s,
        {"slots": day["slots"],
         "dtmf_map": {"1": _TIMES[0][1], "2": _TIMES[1][1], "3": _TIMES[2][1]},
         "mode": "single_day"},
        ["readout"],
    )
    s["available_days"] = [day]
    return s


# ---------------------------------------------------------------------------
# The five live turns, verbatim from the corpus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "no saturday is not soon enough i need to be seen as soon as possible",
    "no what do you what's the soonest you have on saturday then",
    "uh yeah monday doesn't work",
    "monday doesn't work",
    "no no saturday doesn't work i need one now",
])
def test_the_live_rejections_resolve_to_nothing(said):
    assert slot_accepted_by_caller(_session(), said) is None, said
    assert utterance_is_a_request_not_a_pick(said) is True, said


@pytest.mark.parametrize("said", [
    "three in the afternoon doesn't work",
    "no that's no good",
    "that doesn't really work for me",
    "one in the afternoon won't work",
    "no, none of those",
])
def test_the_family_not_only_the_five(said):
    """The five are what the corpus happened to hold. The rule is about the
    shape, not those wordings."""
    assert slot_accepted_by_caller(_session(), said) is None, said


# ---------------------------------------------------------------------------
# The guard. A false positive here costs a caller the pick they just made.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,iso", [
    ("yeah three works", "2026-09-07T15:00:00+01:00"),
    ("three works", "2026-09-07T15:00:00+01:00"),
    ("one in the afternoon works", "2026-09-07T13:00:00+01:00"),
    ("the third one", "2026-09-07T15:00:00+01:00"),
    ("yeah 3 works", "2026-09-07T15:00:00+01:00"),
    ("three in the afternoon works", "2026-09-07T15:00:00+01:00"),
    ("First one", "2026-09-07T08:00:00+01:00"),
    ("Second one", "2026-09-07T13:00:00+01:00"),
])
def test_a_pick_still_resolves(said, iso):
    assert slot_accepted_by_caller(_session(), said) == iso, said


@pytest.mark.parametrize("said,iso", [
    ("no problem three works", "2026-09-07T15:00:00+01:00"),
    ("no worries, one in the afternoon works", "2026-09-07T13:00:00+01:00"),
    ("no rush - three in the afternoon works", "2026-09-07T15:00:00+01:00"),
])
def test_a_softener_is_not_a_rejection(said, iso):
    """THE guard, and the reason the leading-"no" arm carries an exception list.
    "No problem" is agreement. Reading it as a refusal would throw away the
    clearest acceptances a caller makes."""
    assert slot_accepted_by_caller(_session(), said) == iso, said


@pytest.mark.parametrize("said", ["no bother, the third one",
                                  "no bother three works"])
def test_a_softener_this_arm_allows_but_the_resolver_does_not(said):
    """A PRE-EXISTING gap, asserted so it is not mistaken for this change.

    `_REJECTS_THE_OFFER_RE` lets "no bother" through -- its exception list holds
    `bother` and `trouble` as well as problem/worries/rush -- and the pick still
    does not resolve, because something earlier in `slot_accepted_by_caller`
    recognises "no problem" and "no worries" as discourse markers and does not
    recognise these. Verified against the commit before this one: "no bother,
    the third one" returned None there too.

    Left alone deliberately. Widening the other list is a change to what
    resolves a pick, which needs its own measurement and its own call; this
    commit only stops rejections resolving. Pinned here so the next person sees
    a known gap rather than a regression."""
    assert slot_accepted_by_caller(_session(), said) is None, said


def test_a_negated_position_still_works():
    """The arm that already existed must not be disturbed by the one added
    beside it."""
    assert utterance_is_a_request_not_a_pick("the first one, not the second") is True


@pytest.mark.parametrize("junk", [None, "", "   "])
def test_it_never_raises(junk):
    assert utterance_is_a_request_not_a_pick(junk) is False
    assert slot_accepted_by_caller(_session(), junk) is None
