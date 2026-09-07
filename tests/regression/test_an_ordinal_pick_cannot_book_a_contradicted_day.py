"""P12's positional branch must not re-open B-138.

The P12 fix (d1189be0, 2 Sep) gave `slot_accepted_by_caller` an early return:
on a SINGLE-DAY offer the positional entries are the slots themselves, so a
named position settles the pick outright. That is correct, and it is what makes
"number two" resolve at all.

But it returns before the day-resolution ladder, and B-138 -- which landed on
4 Sep, two days AFTER the fix was written -- lives at the bottom of that ladder:

    11:34:44  'um do you have any do you have a 10 past 12 for wednesday
               for example'
    11:34:44  caller ACCEPTED 2026-09-10T12:10:00+01:00      <- THURSDAY

A QUESTION about Wednesday resolved as an ACCEPTANCE of Thursday.
`_names_a_different_weekday` was written for exactly that and the positional
return jumped straight over it, so the same wrong-day booking came back through
a new door: "have you got number two on wednesday?" against a Tuesday-only
offer booked the Tuesday.

The second half covers the same shape in the time dimension. Step 3 asks
`_time_contradicts` at all three of its exits; the positional return did not
ask it at all, so a position paired with a time that is not that slot's
resolved to the position and ignored the contradiction. That is the wrong-slot
kind of defect rather than the no-slot kind: it books silently, and every
verbal read-back afterwards is generated FROM the pin, so it sounds correct all
the way to the calendar.

Both guards only ever DECLINE. The cost of a false decline is one more "which
suits?"; the cost of a false resolve is an appointment on the wrong day.
"""

import pytest

from app.tools.slot_followup import slot_accepted_by_caller
from app.tools.slot_offer import apply_offer_to_session

_DAY = "2026-09-08"          # a TUESDAY
_TIMES = [("08:50", "ten to nine in the morning"),
          ("16:20", "twenty past four in the afternoon"),
          ("17:10", "ten past five in the evening")]


def _single_day_session():
    """A one-day, three-option offer, built by the real producer."""
    payload = [{
        "date": _DAY,
        "day_label": "Tuesday 8th September",
        "slot_times": [t for t, _ in _TIMES],
        "slot_times_spoken": [s for _, s in _TIMES],
        "slots": [{"start": "%sT%s:00" % (_DAY, t)} for t, _ in _TIMES],
    }]
    slots = [{"start": "%sT%s:00" % (_DAY, t), "date": _DAY,
              "day_label": "Tuesday 8th September", "time": t, "spoken": s}
             for t, s in _TIMES]
    session = {"available_days": payload}
    apply_offer_to_session(
        session,
        {"slots": slots, "dtmf_map": {i + 1: s for i, (_, s) in enumerate(_TIMES)},
         "mode": "single_day"},
        ["readout"],
    )
    session["available_days"] = payload
    return session


# -- B-138, reached by ordinal ---------------------------------------------

@pytest.mark.parametrize("utterance", [
    "have you got number two on wednesday",
    "do you have the first one on friday",
    "number two on monday please",
])
def test_a_position_naming_another_weekday_resolves_nothing(utterance):
    """The offer covers a Tuesday. A caller naming a different weekday is
    asking about a day this offer does not hold -- pinning it books the wrong
    day, which is the whole of B-138."""
    assert slot_accepted_by_caller(_single_day_session(), utterance) is None, (
        "%r resolved to a slot on %s (a Tuesday) -- B-138 through the "
        "positional branch" % (utterance, _DAY)
    )


@pytest.mark.parametrize("utterance,expected", [
    ("number two on tuesday", "16:20"),
    ("the first one on tuesday please", "08:50"),
])
def test_a_position_naming_the_offers_own_weekday_still_resolves(utterance, expected):
    """The guard must decline a CONTRADICTION, not any mention of a weekday.
    Naming the day the offer is actually on agrees with it."""
    got = slot_accepted_by_caller(_single_day_session(), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), utterance


# -- the time dimension, the same question step 3 asks ---------------------

@pytest.mark.parametrize("utterance", [
    "number two in the morning",      # #2 is 16:20, an afternoon slot
    "the first one in the evening",   # #1 is 08:50, a morning slot
])
def test_a_position_paired_with_a_contradicting_band_resolves_nothing(utterance):
    assert slot_accepted_by_caller(_single_day_session(), utterance) is None, (
        "%r resolved despite naming a part of the day the slot is not in" % utterance
    )


@pytest.mark.parametrize("utterance,expected", [
    ("the first one in the morning", "08:50"),
    ("number two in the afternoon", "16:20"),
    ("the last one in the evening", "17:10"),
])
def test_a_position_agreeing_with_its_band_still_resolves(utterance, expected):
    got = slot_accepted_by_caller(_single_day_session(), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), utterance


# -- and the plain picks P12 exists for are untouched ----------------------

@pytest.mark.parametrize("utterance,expected", [
    ("the first one", "08:50"),
    ("number two", "16:20"),
    ("the last one", "17:10"),
])
def test_a_bare_position_is_unaffected_by_either_guard(utterance, expected):
    got = slot_accepted_by_caller(_single_day_session(), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), utterance
