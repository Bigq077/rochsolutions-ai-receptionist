"""A day-pick must get its head however few words the caller used.

`74ad7c73` added `Intent.SLOT_PICKED` -> "Monday it is -" so a caller who has
just chosen a slot is not apologised to. It was verified on one live call and
shipped. The next night, two calls on the demo line showed it fires on barely
half of real picks:

    01:26:49  'uh yeah monday works'                      -> no head at all
              LAT turn_seq=3 ttfa_ms=2130 content_ttfa_ms=2130   (equal: nothing spoke)
    01:29:14  'yeah monday the 7th at 10 in the morning'   -> 'Monday it is -'
              LAT turn_seq=8 ttfa_ms=746  content_ttfa_ms=1940

Same pick, same intent, opposite behaviour. The discriminator was WORD COUNT.
`_LEADING_DISFLUENCY` strips "uh", leaving "yeah monday works" -- three words,
opening with a bare-answer word -- so the `<= 4` early return fired and the
SLOT_PICKED arm at the end of `classify_intent` was unreachable.

That return's comment asserted "a bare answer names no day, so it cannot reach
SLOT_PICKED either". It can, and a short pick is the ORDINARY way to answer a
readout, so the exemption is not an edge case -- it is most of them.

Second time in twenty-four hours that a guard in this family was safe only
under a premise that was not true; `dc58d3b5` was the first. Both were found by
a call, not by the suite, which is the argument for the replay harness.

WHAT MUST NOT CHANGE. `test_choosing_a_slot_still_gets_silence` (30 Aug) is a
DECISION, not a stale fixture: a band-only or positional pick gets silence,
because a head in front of it would promise a lookup. Every case it pins names
no day, so the exemption here cannot reach them. That is asserted below rather
than left to inspection.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent

READOUT = (
    "Here's what we've got coming up - Number 1, Monday 7th September - "
    "eight in the morning, or ten past five in the evening. Number 2, "
    "Tuesday 8th September - ten to nine in the morning. Any of those work?"
)


@pytest.mark.parametrize("utterance", [
    "uh yeah monday works",        # the live miss, 01:26:49
    "yeah monday works",           # the same without the disfluency
    "monday works",                # what the shipping call happened to say
    "yeah monday please",
    "monday's good",
    "yeah monday the 7th at 10 in the morning",   # the long form that did fire
])
def test_a_pick_that_names_a_day_gets_the_head(utterance):
    hits = classify_intent(utterance, READOUT, slot_selection=True)
    assert Intent.SLOT_PICKED in hits, (
        f"{utterance!r} got {hits} - a caller who has chosen a day is not "
        f"waiting for a lookup, and silence here hands the turn to "
        f"UNKNOWN_SLOW, which apologises"
    )


@pytest.mark.parametrize("picked", [
    # The three cases pinned by test_choosing_a_slot_still_gets_silence.
    "ten in the morning",
    "can I take two in the afternoon please",
    "yeah ten in the morning",
    # And the shapes 74ad7c73 deliberately left out of scope.
    "number two",
    "yeah, that one",
    "the last one",
])
def test_a_pick_that_names_no_day_still_gets_silence(picked):
    """The 30 Aug decision. Reopening it is a choice to make on purpose."""
    assert classify_intent(picked, READOUT, slot_selection=True) == []


def test_the_exemption_needs_the_engines_verdict_too():
    """A day named without a selection verdict is a REQUEST, not a pick.

    "monday works" arriving when the engine says this is not a slot selection
    means the caller is talking about a Monday we have not offered. That is a
    lookup, and it must not be answered with "Monday it is -".
    """
    assert Intent.SLOT_PICKED not in classify_intent(
        "uh yeah monday works", READOUT, slot_selection=False
    )


def test_a_clinical_screen_still_beats_everything():
    """The blocker that must survive any widening of this function.

    A head in front of an unanswered red-flag screen is the promised-work
    defect at its worst, and it outranks a pick.
    """
    assert classify_intent(
        "uh yeah monday works", READOUT, slot_selection=True, screen_pending=True
    ) == []
