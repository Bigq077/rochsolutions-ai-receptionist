"""CA66bd0930 (14 Sep 2026, JV, live), 09:51:40. OPEN_DEFECTS_2026-09-14.md #4.

    Susie   "Number 1, seven in the evening … Number 3 … Any of those work?"
    Caller  "no"
    Susie   "would the week of the 30th of September suit better?"
    Caller  "no"
    Susie   "is there another time that might suit you, or would you like me
             to look further ahead for Wednesdays?"
    (silence)
    Watchdog "Still with you — which of those would you like?"   <-- of WHAT?

`v3_awaiting_slot_selection` was still set, and the slot-selection re-ask
reused the pick-question shape without checking whether the last question
pointed at an offer. Now it does: a question that refers to the offered set
("any of those", "number two", "does that work") is re-asked as a pick; any
other question is the one the caller heard, repeated.

(The caller's answer "any day as soon as possible" was ALSO discarded that
turn -- `open_availability_suppressed`, row #5 -- which is why the watchdog
fired at all. That is a slot-spec question and is not touched here.)
"""
import pytest

from app.media_streams.connection import (
    _SLOT_PREFERENCE_REASK,
    _slot_selection_reask_phrase,
)

PICK = "Still with you — which of those would you like?"


def test_the_preference_question_from_CA66bd0930_is_repeated_not_replaced():
    s = {"v3_awaiting_slot_selection": True,
         "last_question": ("is there another time that might suit you, or would "
                           "you like me to look further ahead for Wednesdays?")}
    out = _slot_selection_reask_phrase(s)
    assert out == ("Still with you — is there another time that might suit you, "
                   "or would you like me to look further ahead for Wednesdays?")
    assert "of those" not in out


@pytest.mark.parametrize("last_q", [
    "Number 3, half past eight in the evening. Any of those work?",
    "Would either of those suit you?",
    "Does that work for you?",
    "Which one would you like?",
    "Number one or number two?",
])
def test_a_question_that_points_at_an_offer_is_reasked_as_a_pick(last_q):
    assert _slot_selection_reask_phrase({"last_question": last_q}) == PICK


def test_no_last_question_keeps_todays_wording():
    assert _slot_selection_reask_phrase({}) == PICK


def test_a_non_question_falls_back_to_the_preference_ask():
    s = {"last_question": "Let me check a bit further ahead for you."}
    assert _slot_selection_reask_phrase(s) == _SLOT_PREFERENCE_REASK


def test_would_the_week_of_the_30th_suit_better_is_not_a_pick():
    s = {"last_question": "would the week of the 30th of September suit better?"}
    out = _slot_selection_reask_phrase(s)
    assert out == "Still with you — would the week of the 30th of September suit better?"
