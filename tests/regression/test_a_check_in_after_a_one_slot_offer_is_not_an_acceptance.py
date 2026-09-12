"""Defect A: a non-affirmative turn after a ONE-slot offer is not an acceptance.

`CA5c69c585`, northgate demo line, 12 Sep 2026 17:36, build 35f06eb6:

    17:36:16  [ms_gate5] the caller named a time (['16:00']) -- ONE slot
    17:36:16  offer built: "The nearest I've got to four in the afternoon is
              twenty past four … Shall I book that in for you?"
    17:36:16  [slot_guard] REPLACED a slot fact -> 'Sorry — let me just
              double-check that one for you.'          (caller never heard it)
    17:36:34  caller: 'hello you still there'
    17:36:35  [ms_conn v3] caller ACCEPTED 2026-09-15T16:20:00+01:00
              ('hello you still there') — pinned into any readout this turn
    17:36:35  [slot_followup] accepted slot pinned for the CALL
    ...
    read-back: "so that's Quentin Rock, Tuesday the 15th of September at
               twenty past four in the afternoon — shall I go ahead and book
               that in?"

The caller was read back a slot they had never heard and never chosen.

WHY. `slot_accepted_by_caller` is deny-by-default on WHICH slot: every step
declines on ambiguity. After a one-slot offer (D-r, 12 Sep) there is none --
one day, one time -- so its own shortcuts (the sole-date step from 3 Sep and
the single-`heard` return) resolve the slot for ANY utterance that is not a
request. Nothing asked whether the utterance was a yes. This test pins the
gate that now asks first (`utterance_can_accept_a_slot`), on the exact
payload obs stored for the call (`calls.slot_offers[0].payload`, Tue 15 Sep).

The positive shapes are pinned in the same file so the gate cannot drift
closed: a yes to a one-slot offer is the commonest acceptance there is.

NOT here: "um so the thing is um last tuesday" -- a PAST day. It carries a
weekday, so the gate lets it through and the resolver pins Tuesday. That is
defect F (past-tense day banked as a preference) and gets its own predicate
and its own commit; folding it into this gate would hide it.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    apply_resolved_time_to_session,
    slot_accepted_by_caller,
    utterance_can_accept_a_slot,
)

# calls.slot_offers[0].payload for CA5c69c585, as stored -- Tuesday only.
TUESDAY = {
    "date": "2026-09-15",
    "day_label": "Tuesday 15th September",
    "slot_times": ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
                   "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"],
    "slot_times_spoken": [
        "eight in the morning", "ten to nine in the morning",
        "twenty to ten in the morning", "half past ten in the morning",
        "twenty past eleven in the morning", "ten past twelve in the afternoon",
        "one in the afternoon", "ten to two in the afternoon",
        "twenty to three in the afternoon", "half past three in the afternoon",
        "twenty past four in the afternoon", "ten past five in the evening",
    ],
    "times_not_shown": 0,
}
SLOT_1620 = {
    "start": "2026-09-15T16:20:00+01:00",
    "end": "2026-09-15T17:05:00+01:00",
    "date": "2026-09-15",
    "time": "16:20",
    "spoken": "twenty past four in the afternoon",
    "day_label": "Tuesday 15th September",
}


def _one_slot_offer_on_the_table():
    """The session exactly as the one-slot producer leaves it (the D-r arm
    on the tool path, `_named_time_offer_for_tool_path`)."""
    session = {"available_days": [TUESDAY]}
    speech = apply_resolved_time_to_session(session, SLOT_1620, asked=["16:00"])
    assert "twenty past four" in speech
    assert session["last_offered_slots"] == [
        {"start": SLOT_1620["start"], "end": SLOT_1620["end"]}
    ]
    return session


# Every caller turn of call 3 that is NOT an acceptance of that slot.
NOT_AN_ACCEPTANCE = [
    "hello you still there",
    "hello",
    "you still there",
    "are you there",
    "the accident was",
    "quentin rock",
    "07502211207",
    "i've got a knee thing and a shoulder thing",
]


@pytest.mark.parametrize("utterance", NOT_AN_ACCEPTANCE)
def test_a_turn_with_no_accepting_signal_does_not_resolve(utterance):
    session = _one_slot_offer_on_the_table()
    got = slot_accepted_by_caller(session, utterance)
    assert got is None, (
        f"{utterance!r} resolved to {got!r} on a one-slot offer -- the caller "
        "said nothing that accepts a slot, and this is the read-back of a time "
        "they never chose (CA5c69c585, 12 Sep)"
    )


@pytest.mark.parametrize("utterance", NOT_AN_ACCEPTANCE)
def test_the_gate_declines_the_same_turns(utterance):
    assert not utterance_can_accept_a_slot(utterance), utterance


# ...and the yes that the same offer is FOR. The one-slot offer ends "Shall I
# book that in for you?"; these are how callers answer it.
ACCEPTS_1620 = [
    "yes go for it",
    "uh yes go for it",
    "yes please",
    "yeah",
    "um yeah that works",
    "go ahead",
    "that one",
    "that's fine",
    "twenty past four",
    "twenty past four works",
    "4:20",
    "tuesday",
    "tuesday's fine",
    "the tuesday",
    "afternoon works",
]


@pytest.mark.parametrize("utterance", ACCEPTS_1620)
def test_a_yes_to_the_one_slot_offer_still_pins_it(utterance):
    session = _one_slot_offer_on_the_table()
    assert utterance_can_accept_a_slot(utterance), utterance
    got = slot_accepted_by_caller(session, utterance)
    # The resolver answers from `available_days`, which carries no offset.
    assert (got or "")[:19] == SLOT_1620["start"][:19], (
        f"{utterance!r} did not resolve to 16:20 -- the caller accepted the "
        "one slot on the table in words and the gate must not swallow it"
    )


def test_a_question_about_the_slot_is_still_not_a_pick():
    """The gate is additive: D-s's existence question and B-138's other-day
    question decline exactly as before, one step later."""
    session = _one_slot_offer_on_the_table()
    assert slot_accepted_by_caller(session, "do you have anything at four") is None
    assert slot_accepted_by_caller(session, "is twenty past four on wednesday") is None
