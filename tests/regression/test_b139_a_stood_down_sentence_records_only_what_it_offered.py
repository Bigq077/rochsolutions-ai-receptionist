"""B-134, rebuilt: the two holes that made the first version unsafe to ship.

B-134 recorded the slots a stood-down sentence SPOKE, so a caller's pick could
land. It was right about that and it is restored here. It shipped at 09:12 on
4 September 2026 and was reverted at 3b88d9d4 the same morning, for two
separate reasons:

  1. WRONG DAY. The record it writes holds ONE date, and the resolver's
     last-resort branch reads a one-date offer as "nothing left to be
     ambiguous" -- so a caller ASKING about another day was scored as
     accepting this one:

         11:34:44  'do you have a 10 past 12 for wednesday for example'
         11:34:44  caller ACCEPTED 2026-09-10T12:10   <- a THURSDAY

     Fixed in the resolver, not here: B-138 / 9e4dc3b3. The branch was wrong
     before B-134 existed and B-134 merely made it reachable. This file pins
     the COMBINATION, because that is what actually ships.

  2. A TIME SUSIE SAID SHE DID NOT HAVE. `payload_slots_named_in` read the
     whole sentence at once, so "Wednesday doesn't have ten past twelve"
     recorded 12:10 as a slot the caller had been offered -- and the caller
     could then accept, in the next breath, the exact thing they had just been
     refused. Fixed here, by splitting the sentence into clauses and reading
     only the ones that are offering something.

Both are the shape this codebase keeps producing: a guard whose safety rests on
a premise that is false in one reachable case. Hole 1's premise was "one date
means no ambiguity"; hole 2's was "a sentence that names a time is offering
it".
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams.llm_stream import LLMStream
from app.tools.slot_followup import (
    offer_clauses,
    payload_slots_named_in,
    slot_accepted_by_caller,
)

from tests.regression.test_b134_a_stood_down_sentence_records_what_it_spoke import (
    CALLER_PICK,
    MODEL_SENTENCE,
    MONDAY,
    TUESDAY,
    _session,
)


# ---------------------------------------------------------------------------
# Hole 2 -- a negated time is not an offer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    # The live shape. Note it is ALSO refused by the day requirement -- this
    # session's payload holds no Wednesday -- so it stays green with the clause
    # split neutered and is not what pins B-139. Every other case below is.
    "Wednesday doesn't have ten past twelve",
    "Monday 7th September doesn't have ten past twelve in the afternoon",
    "I'm afraid Monday 7th September has nothing at ten past twelve",
    "Monday 7th September — ten past twelve in the afternoon has gone",
    "Sorry, Monday 7th September's ten past twelve is already booked",
    "Monday 7th September is fully booked at ten past twelve in the afternoon",
    "Monday 7th September no longer has ten past twelve in the afternoon",
])
def test_a_refused_time_is_never_recorded_as_offered(sentence):
    """The caller must not be able to accept what Susie just refused."""
    assert payload_slots_named_in(_session(), sentence) == []


def test_the_offer_on_the_other_side_of_a_but_still_counts():
    """Refusing the whole sentence would be the pre-B-134 bug in a phrasing the
    model uses constantly. Only the negated CLAUSE is dropped."""
    got = payload_slots_named_in(
        _session(),
        "Monday 7th September's twenty past eleven has gone, but I do have "
        "ten past twelve in the afternoon",
    )
    assert [str(s["start"])[:16] for s in got] == ["2026-09-07T12:10"]


def test_the_live_sentence_is_untouched_by_the_clause_split():
    """B-134's own sentence, verbatim. The guard must not cost the fix."""
    got = payload_slots_named_in(_session(), MODEL_SENTENCE)
    assert [str(s["start"])[:16] for s in got] == [
        "2026-09-07T11:20", "2026-09-07T12:10",
    ]


@pytest.mark.parametrize("text,expected", [
    ("Monday 7th September — twenty past eleven in the morning, or ten past "
     "twelve in the afternoon. Either of those work?",
     ["Monday 7th September — twenty past eleven in the morning, or ten past "
      "twelve in the afternoon", "Either of those work"]),
    ("Wednesday doesn't have ten past twelve", []),
    ("Monday's fully booked, but Tuesday has ten past twelve",
     ["Tuesday has ten past twelve"]),
])
def test_the_clause_split(text, expected):
    assert offer_clauses(text) == expected


@pytest.mark.parametrize("junk", ["", None, "   ", 0, [], {}])
def test_the_clause_split_reads_junk_as_offering_nothing(junk):
    assert offer_clauses(junk) == []


# ---------------------------------------------------------------------------
# Hole 1 -- the record B-134 writes holds one date, and that must no longer
# turn a QUESTION about another day into an acceptance
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_wrong_day_acceptance_that_caused_the_revert():
    """End to end: the stand-down writes its one-date record, and then the
    utterance shape that reached a live caller resolves to NOTHING.

    This is the test the first version of B-134 did not have. It is green
    because of B-138; if that guard is removed this goes red, which is the
    point of pinning the combination rather than each half alone.
    """
    session = _session()
    session["_slot_offer_prebuilt"] = {
        "chunks": ["Here's what we've got — Number 1, Thursday 10th September."],
        "slots": [{"start": "2026-09-10T08:00:00+01:00", "end": "",
                   "spoken": "eight in the morning", "date": "2026-09-10"}],
        "dtmf_map": {"1": "Thursday 10th September"},
        "more_times": False,
        "mode": "multi_day",
    }
    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(MODEL_SENTENCE)
    await LLMStream._flush_slot_buf(buf, tts, session)

    # The record now holds ONE date -- exactly the state that made the
    # resolver's last-resort branch reachable.
    dates = {str(s["start"])[:10] for s in session["last_offered_slots"]}
    assert dates == {"2026-09-07"}

    # A question about another day is a question, not a pick.
    assert slot_accepted_by_caller(
        session, "do you have a 10 past 12 for wednesday for example"
    ) is None
    # …and the pick B-134 exists to make land still lands.
    assert slot_accepted_by_caller(session, CALLER_PICK) == (
        "2026-09-07T12:10:00+01:00"
    )


@pytest.mark.asyncio
async def test_a_refusal_records_nothing_and_leaves_the_standing_offer():
    """The other half, end to end. A stand-down whose sentence OFFERS nothing
    must leave the record exactly as it found it -- not write an empty one,
    which would strand the caller with no offer at all."""
    session = _session()
    before = list(session["last_offered_slots"])
    session["_slot_offer_prebuilt"] = {
        "chunks": ["Here's what we've got — Number 1, Thursday 10th September."],
        "slots": [{"start": "2026-09-10T08:00:00+01:00", "end": "",
                   "spoken": "eight in the morning", "date": "2026-09-10"}],
        "dtmf_map": {"1": "Thursday 10th September"},
        "more_times": False,
        "mode": "multi_day",
    }
    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(
        "Monday 7th September doesn't have ten past twelve in the afternoon"
    )
    await LLMStream._flush_slot_buf(buf, tts, session)

    assert session["last_offered_slots"] == before
    assert slot_accepted_by_caller(session, CALLER_PICK) is None


# ---------------------------------------------------------------------------
# The live call, both halves in one sentence
# ---------------------------------------------------------------------------
# CAdd64c466, turn 12, verbatim. It REFUSES one time and OFFERS three, which is
# the phrasing the whole clause split exists for -- and the caller's answer at
# turn 13 died:
#
#   12  "I'm afraid Wednesday 9th September doesn't have ten past twelve
#        available — but it does have ten to nine in the morning, twenty to ten
#        in the morning, or half past three in the afternoon."
#   13  'oh yeah 20 to 10 will work'
#   14  "Sorry, still with you —"
#   16  "Wednesday 9th September — Number 1, twenty to ten in the morning.
#        Number 2, half past ten in the morning. …"
#
# She read the day again, with a different set of times, because nothing had
# resolved. He had already chosen.
WED_TIMES = [
    "08:00", "08:50", "09:40", "10:30", "11:20",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
WED_SPOKEN = [
    "eight in the morning", "ten to nine in the morning",
    "twenty to ten in the morning", "half past ten in the morning",
    "twenty past eleven in the morning", "one in the afternoon",
    "ten to two in the afternoon", "twenty to three in the afternoon",
    "half past three in the afternoon", "twenty past four in the afternoon",
    "ten past five in the evening",
]
WEDNESDAY = {
    "date": "2026-09-09",
    "day_label": "Wednesday 9th September",
    "slot_times": list(WED_TIMES),
    "slot_times_spoken": list(WED_SPOKEN),
    "slots": [
        {"start": "2026-09-09T%s:00+01:00" % t, "end": ""} for t in WED_TIMES
    ],
}
TURN_12 = (
    "I'm afraid Wednesday 9th September doesn't have ten past twelve available "
    "— but it does have ten to nine in the morning, twenty to ten in the "
    "morning, or half past three in the afternoon. Would any of those work "
    "for you?"
)


def _wednesday_session():
    return {
        "available_days": [WEDNESDAY],
        "last_offered_slots": [
            {"start": "2026-09-09T08:00:00+01:00", "end": ""},
            {"start": "2026-09-09T16:20:00+01:00", "end": ""},
        ],
        "slot_labels": [
            "eight in the morning", "twenty past four in the afternoon",
        ],
        "slot_starts_spoken": [
            "2026-09-09T08:00:00", "2026-09-09T16:20:00",
        ],
    }


def test_the_live_sentence_records_the_three_it_offered():
    got = payload_slots_named_in(_wednesday_session(), TURN_12)
    assert [str(s["start"])[:16] for s in got] == [
        "2026-09-09T08:50", "2026-09-09T09:40", "2026-09-09T15:30",
    ]


def test_turn_13_now_lands():
    """The caller's actual words. None is what he got on the call."""
    from app.tools.slot_offer import apply_offer_to_session

    session = _wednesday_session()
    assert slot_accepted_by_caller(session, "oh yeah 20 to 10 will work") is None

    apply_offer_to_session(
        session,
        {"slots": payload_slots_named_in(session, TURN_12), "dtmf_map": {},
         "more_times": False, "mode": "single_day"},
        [TURN_12],
    )
    assert slot_accepted_by_caller(session, "oh yeah 20 to 10 will work") == (
        "2026-09-09T09:40:00+01:00"
    )


def test_the_refused_time_is_not_among_them():
    """"ten past twelve" is in the same sentence, and Wednesday's payload does
    not hold it either -- so this passes for two independent reasons and is
    here to say so, not to pin the clause split."""
    got = payload_slots_named_in(_wednesday_session(), TURN_12)
    assert not any(str(s["start"])[11:16] == "12:10" for s in got)


# ---------------------------------------------------------------------------
# Wired, not merely callable
# ---------------------------------------------------------------------------
def test_the_matcher_reads_clauses_and_not_the_whole_sentence():
    import inspect

    from app.tools import slot_followup

    src = inspect.getsource(slot_followup.payload_slots_named_in)
    assert "offer_clauses(text)" in src
    assert "_time_norm(text)" not in src
