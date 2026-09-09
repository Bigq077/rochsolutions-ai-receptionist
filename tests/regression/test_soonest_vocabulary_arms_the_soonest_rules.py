"""A caller who says "soonest" gets the same treatment as one who says "asap".

9 Sep 2026, northgate, build b5f5c9975949. The caller opened with "um what's
the soonest available slot you have". The model understood it perfectly -- it
said "Let me find the soonest I've got" and sent
date_hint="as soon as possible" -- but `_extract_day_preference` knew only the
literal phrases "as soon as possible" and "asap", so `day_preference` was never
written and `caller_wants_soonest` stayed False.

THE STAGE B LEAD-IN IS THE LEAST OF IT. `caller_wants_soonest` also gates the
two ordering fixes from 4 Sep:

    choose_presented_days       B-137 -- lead with the EARLIEST days, not the
                                unheard ones
    choose_presented_indices    B-142 -- read each day from its earliest time

So this vocabulary gap left B-137 armed only for callers who used its one
phrase. CA5685a2ab -- the call B-137 was written for -- says "that's not soon
enough", which the matcher could not see.

The assertions go through `caller_wants_soonest` rather than stopping at the
matcher's return value, because the return value is not the behaviour: the
consumers read a session field, and a test that checks only the string cannot
see the membership rule in `_SOONEST_DAY_PREFERENCES` drift out from under it.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _extract_day_preference
from app.tools.slot_followup import caller_wants_soonest


def _armed(utterance: str) -> bool:
    """The real chain: capture in connection.py, read in slot_followup."""
    return caller_wants_soonest(
        {"day_preference": _extract_day_preference(utterance)}
    )


@pytest.mark.parametrize("utterance", [
    "um what's the soonest available slot you have",   # the live call, verbatim
    "what is the earliest you have",
    "can you give me something sooner",
    "anything sooner than that",
    "whats the soonest",
    "earliest appointment please",
    "when's your next available",
    "first available please",
])
def test_soonest_phrasings_arm_the_soonest_rules(utterance):
    assert _armed(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "as soon as possible",
    "i need to be seen as soon as possible",
    "asap",
    "today",
    "tomorrow",
    "what have you got this week",
])
def test_the_phrasings_that_already_worked_still_do(utterance):
    assert _armed(utterance), utterance


# ── The guard ────────────────────────────────────────────────────────────────
# "What's the earliest on Thursday" is a request for THURSDAY. Banking
# "as soon as possible" would make `choose_presented_days` lead with the
# globally earliest days and drop the only day the caller asked about -- the
# B-138 family, where a vague phrase banks a filter that deletes the real
# request. Bare "asap" could reach this too, but nobody says "asap on
# Thursday"; the vocabulary above is what makes it reachable.

@pytest.mark.parametrize("utterance,expected", [
    ("what's the earliest on thursday", "thursday"),
    ("soonest you have on friday", "friday"),
    ("earliest next week", "next week"),
    ("is thursday soon enough", "thursday"),   # ACCEPTING Thursday, not rejecting it
])
def test_a_named_day_beats_a_vague_earliest(utterance, expected):
    assert _extract_day_preference(utterance) == expected
    assert not _armed(utterance), utterance


# ── The rejection frame ──────────────────────────────────────────────────────
# "Saturday is not soon enough" is a REJECTION of Saturday, and the guard above
# would otherwise read it as a request for one. b5f5c997 returned "saturday"
# here too, via the bare-weekday arm -- pre-existing, and fixed with this change
# because it is the same call: CA5685a2ab is a caller rejecting the day he was
# just offered. Banking it pins every later readout to the day he turned down.

@pytest.mark.parametrize("utterance", [
    "that's not soon enough",
    "no saturday is not soon enough",
    "thursday isn't soon enough",
    "no saturday is not soon enough i need to be seen as soon as possible",
])
def test_rejecting_a_day_does_not_bank_that_day(utterance):
    assert _extract_day_preference(utterance) == "as soon as possible"
    assert _armed(utterance), utterance


# ── What must NOT arm ────────────────────────────────────────────────────────
# Bare "soon" is deliberately not in the vocabulary. This capture persists for
# the whole call, so a false positive re-orders every later readout.

@pytest.mark.parametrize("utterance", [
    "see you soon",
    "i'll get there as soon as i can",
    "my back has been bad for weeks",
    "good morning",
    "hiya, can i book something",
])
def test_non_requests_do_not_arm_the_soonest_rules(utterance):
    assert not _armed(utterance), utterance
