"""Three utterances that named no position and resolved to a bookable slot.

All three were found by scripts/replay_slot_decisions.py over the stored
corpus, not on a call, and none of them is reachable before P12: until a
position on a single-day offer settled the pick outright, a named position
selected a DAY, step 3 then failed to find a time under it, and the turn
resolved to nothing BY ACCIDENT. P12 removed the accident, so the looseness in
`_position_named` became bookable.

That makes these the wrong-slot kind rather than the no-slot kind. Nothing
sounds wrong on the call: the read-back is generated FROM the pin, so the
caller hears a confirmation of a slot they never chose.

  1. `"um yeah quentin roch um the last name is spelled r-o-c-h"`
     A caller SPELLING THEIR SURNAME. "last" was read as the end of the list
     and booked the last slot in the offer.

  2. `"actually what's the soonest you've got"`   (7 stored turns)
     `_FIRST_POSITION_RE` carries "soonest", so a question about what the
     DIARY holds resolved to the first slot in the OFFER -- the opposite of
     what the caller asked for. `classify_intent` already reads these as
     Intent.EARLIEST; only this resolver took them for a pick.

  3. `"i didn't catch that last bit can you repeat yourself"`
     A caller who did not hear, booked into the last slot.

The fixes are shape rules, never longer word lists -- the vocabulary is not
the discriminator and every one of these words is a legitimate way to pick a
slot in some other sentence.

The last block is the one that keeps the cure honest. `Intent.REPEAT_ASK`
covers case 3 exactly and reusing it was the first attempt, but it also
matches "i said", which is how a caller RE-ASSERTS a pick Susie missed.
Replay measured that: four real picks stopped resolving, every one from an
audibly impatient caller who would then have been read the list a second
time -- the P6 symptom, landing on the callers who had already hit it once.
"""

import pytest

from app.tools.slot_followup import (
    _position_named,
    utterance_is_a_request_not_a_pick,
    slot_accepted_by_caller,
)
from app.tools.slot_offer import apply_offer_to_session

_DAY = "2026-09-08"
_TIMES = [("08:50", "ten to nine in the morning"),
          ("16:20", "twenty past four in the afternoon"),
          ("17:10", "ten past five in the evening")]


def _single_day_session():
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


# -- the three shapes, at the resolver's front door -------------------------

@pytest.mark.parametrize("utterance", [
    "um yeah quentin roch um the last name is spelled r-o-c-h",
    "my first name is sarah",
    "actually what's the soonest you've got",
    "what could you tell me what's the soonest you have",
    "that's not soon enough what's the soonest you have",
    "um what about um the soonest available slot you have",
    "i didn't catch that last bit can you repeat yourself",
])
def test_a_position_word_used_as_ordinary_english_books_nothing(utterance):
    assert slot_accepted_by_caller(_single_day_session(), utterance) is None, (
        "%r resolved to a bookable slot -- the caller named no position and "
        "would be read back a confirmation of a time they never chose" % utterance
    )


@pytest.mark.parametrize("utterance", [
    "um yeah quentin roch um the last name is spelled r-o-c-h",
    "my first name is sarah",
    "actually what's the soonest you've got",
    "sorry what's the soonest you have",
])
def test_position_named_declines_the_shapes_that_are_not_positions(utterance):
    assert _position_named(utterance, 3) is None, utterance


# -- and every real way to name one still works -----------------------------

@pytest.mark.parametrize("utterance,expected", [
    ("the first one", 1),
    ("First one", 1),
    ("number two", 2),
    ("the second one", 2),
    ("the last one", 3),
    ("Last one", 3),
    ("the last one please", 3),
    ("i'll take the first one", 1),
    ("the earliest one", 1),
    # the two utterances that opened P6 on live calls
    ("the last day at 6 in the evening works", 3),
    ("the last day in the afternoon works", 3),
])
def test_a_real_position_is_still_named(utterance, expected):
    assert _position_named(utterance, 3) == expected, utterance


# -- restating a pick is not asking for a repeat ---------------------------

@pytest.mark.parametrize("utterance", [
    "uh yeah i said 5 in the evening works for me",
    "yeah i said 9 in the morning works",
    "i said half past six in the evening works",
    "i said quarter past six please",
])
def test_a_caller_repeating_their_own_pick_is_still_picking(utterance):
    """`Intent.REPEAT_ASK` matches "i said" and would deny all four.

    A caller who did not HEAR needs the list again; a caller who SAID
    something is re-asserting it, and reading the list back to them is the
    defect this resolver was written to remove.
    """
    assert not utterance_is_a_request_not_a_pick(utterance), utterance
