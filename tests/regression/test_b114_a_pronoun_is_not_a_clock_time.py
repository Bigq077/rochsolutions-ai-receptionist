# tests/regression/test_b114_a_pronoun_is_not_a_clock_time.py
"""
B-114 - CA0f8ffe7b5f0fbb576fdc956df31c545b, 28 Aug 2026, theorem_v3, Alcester,
build fa2e51743452.

  10:41:34  the offer on the table spans THREE days:
            Number 1, Tuesday 1st September - nine in the morning.
            Number 2, Wednesday 2nd September - ten in the morning.
            Number 3, Friday 4th September - nine in the morning. Or ten...
  10:41:50  caller: "um no could you tell me what you have next monday
                     the one after that not the one coming up the one after"
  10:41:58  "Yes - one in the afternoon on Friday 4th September is free.
             Shall I book that in for you?"
  10:42:03  caller: "that's not what i asked i asked for next monday the one after"

A booking prompt, on a day the caller had never mentioned, one second after
they said "no". Three of their words were "one" and every one was a pronoun.

TWO independent paths inside resolve_requested_time produced it, and either
alone reproduces the call:

  soft-core   matched "one" by SUBSTRING. The same test matches "none of those
              work", "could someone call me back" and "phone me on my mobile"
              - and "none of those work" is the single most common thing a
              caller says to a list of times.

  hhmm        emitted 01:00/13:00 for a bare hour word anywhere in the
              utterance. Its stated safety was uniqueness in `remaining`;
              exactly one slot sat at 13:00, so the wrong answer was the
              confident one.

And TWO call sites reach it, which is why the fix is in the shared function:

  slot_followup.try_unspoken_followup_speech  - the deterministic speech path
                                                that spoke on this call
  llm_stream (~4891)                          - builds a tool RESULT telling
                                                the model "Confirm it and ask
                                                whether to book"

The second is the louder one: it does not merely say the wrong thing, it
instructs the model to.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.tools import slot_followup
from app.tools.slot_followup import resolve_requested_time

TUE, WED, FRI = "2026-09-01", "2026-09-02", "2026-09-04"

# The offer that was on the table at 10:41:50, from the payload.
REMAINING = [
    {"time": "09:00", "spoken": "nine in the morning", "start": f"{TUE}T09:00:00+01:00"},
    {"time": "10:00", "spoken": "ten in the morning", "start": f"{WED}T10:00:00+01:00"},
    {"time": "13:00", "spoken": "one in the afternoon", "start": f"{FRI}T13:00:00+01:00"},
    {"time": "14:00", "spoken": "two in the afternoon", "start": f"{FRI}T14:00:00+01:00"},
]
DAYS = [
    {"date": TUE, "day_label": "Tuesday 1st September",
     "slots": [{"start": f"{TUE}T09:00:00+01:00"}]},
    {"date": WED, "day_label": "Wednesday 2nd September",
     "slots": [{"start": f"{WED}T10:00:00+01:00"}]},
    {"date": FRI, "day_label": "Friday 4th September",
     "slots": [{"start": f"{FRI}T13:00:00+01:00"}, {"start": f"{FRI}T14:00:00+01:00"}]},
]

LIVE_UTTERANCE = (
    "um no could you tell me what you have next monday the one after that "
    "not the one coming up the one after"
)


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------

def test_the_live_utterance_resolves_to_nothing():
    assert resolve_requested_time(LIVE_UTTERANCE, REMAINING, DAYS) is None


def test_the_live_utterance_resolves_to_nothing_without_the_day_payload():
    """The word fix alone has to carry it.

    "next monday" names no day of a payload holding Tuesday, Wednesday and
    Friday, so the day guard returns None here and cannot be what saves this
    call.
    """
    assert resolve_requested_time(LIVE_UTTERANCE, REMAINING) is None


# ---------------------------------------------------------------------------
# The substring door - ordinary phrases that resolved to a 1pm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "no none of those work",
    "none of them work for me",
    "could someone call me back please",
    "can you phone me on my mobile",
    "the first one",
    "which one is soonest",
    "i'll take one",                      # an OPTION number, not one o'clock
    "have you got anything in two weeks",
])
def test_phrases_that_are_not_times_resolve_to_nothing(utterance):
    assert resolve_requested_time(utterance, REMAINING, DAYS) is None


# ---------------------------------------------------------------------------
# A real time still resolves - the fix must not deafen her
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,want", [
    ("i'll take one in the afternoon", f"{FRI}T13:00:00+01:00"),
    ("two in the afternoon please", f"{FRI}T14:00:00+01:00"),
    ("can you do one o'clock", f"{FRI}T13:00:00+01:00"),
    ("at one", f"{FRI}T13:00:00+01:00"),
    ("nine in the morning works", f"{TUE}T09:00:00+01:00"),
    ("13:00 please", f"{FRI}T13:00:00+01:00"),
    ("not the one after, the one o'clock", f"{FRI}T13:00:00+01:00"),
    # Asking whether a time EXISTS carries no marker of its own, and it is the
    # commonest way a caller reaches for a slot they have not been offered.
    # The first cut of this fix rejected all four; the suite diff caught it as
    # test_unspoken_slot_followup::test_resolve_six_in_remaining, which has
    # pinned "do you have six" since long before B-114.
    ("do you have two in the afternoon", f"{FRI}T14:00:00+01:00"),
    ("have you got one", f"{FRI}T13:00:00+01:00"),
    ("does one work", f"{FRI}T13:00:00+01:00"),
    ("is one free", f"{FRI}T13:00:00+01:00"),
])
def test_a_real_time_still_resolves(utterance, want):
    hit = resolve_requested_time(utterance, REMAINING, DAYS)
    assert hit is not None, f"{utterance!r} named a time and reached nothing"
    assert hit["start"] == want


# ---------------------------------------------------------------------------
# The day guard - the sibling shape
# ---------------------------------------------------------------------------

def test_a_time_on_another_day_is_refused_when_the_caller_named_one():
    """Wednesday is on the table and 1pm is not on it."""
    assert resolve_requested_time(
        "what about wednesday the 2nd of september at one", REMAINING, DAYS,
    ) is None


def test_the_named_day_still_resolves_its_own_time():
    hit = resolve_requested_time(
        "friday the 4th of september at one", REMAINING, DAYS,
    )
    assert hit is not None and hit["start"] == f"{FRI}T13:00:00+01:00"


def test_naming_no_day_leaves_the_whole_sweep_reachable():
    """The whole-sweep scope is deliberate and must survive."""
    hit = resolve_requested_time("two in the afternoon", REMAINING, DAYS)
    assert hit is not None and hit["start"] == f"{FRI}T14:00:00+01:00"


def test_an_unreadable_day_payload_never_fails_the_lookup():
    for days in (None, "nonsense", [{"date": None}], [None]):
        assert resolve_requested_time(
            "two in the afternoon", REMAINING, days,
        ) is not None


# ---------------------------------------------------------------------------
# The two branches must not drift apart again
# ---------------------------------------------------------------------------

_CALL_RE = re.compile(r"resolve_requested_time\(\s*([^)]*?)\)", re.S)


def _call_sites():
    from app.media_streams import llm_stream

    out = []
    for mod in (slot_followup, llm_stream):
        src = inspect.getsource(mod)
        for m in _CALL_RE.finditer(src):
            args = m.group(1)
            if "text: str" in args:
                continue                      # the definition itself
            out.append((mod.__name__, args))
    return out


def test_every_call_site_passes_the_day_payload():
    """Two branches resolve a caller time phrase and they share one function.

    A third - or a call that drops the day payload - reopens B-114 on the half
    nobody tested. `available_days` defaults to None ONLY for back-compat, so a
    silent omission is exactly what this catches.
    """
    sites = _call_sites()
    assert len(sites) == 2, f"call-site count changed: {sites}"
    for mod, args in sites:
        assert args.count(",") >= 2, (
            f"{mod} calls resolve_requested_time without the day payload: {args!r}"
        )
