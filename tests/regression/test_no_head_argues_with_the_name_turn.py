"""Rows N-1 and N-2 of the 13 Sep 2026 hold-head audit (CA9bd192c9, northgate,
build dd3a9ff7). STT heard "Elektra" as "a lecture" three times. The heads:

    turn 6  "did you say Lecture — is that right?"
            caller: "no just a lecture and yeah"
            head:   "Not to worry —"                       ← N-2
    turn 7  "…could you say your first name again?"
            caller: "a lecture"
            head:   "Thanks, got that —"
            model:  "I'm not quite catching that — …"      ← N-1

N-2: a no to a name read-back is a correction, not a refusal. `_CONFIRM_Q`
matches "is that right" and the keypad slot map from an earlier readout made
`offer_refused` true; both roads led to consolation for something nobody
refused. A denied name gets no head.

N-1: after a re-ask the receipt head stands down. It asserts what the model is
about to deny, and the odds the model accepts this attempt are exactly what
they were last time. The 2.75s rung still covers a stall.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent

READBACK = "did you say Lecture — is that right?"
SURNAME_READBACK = "and your surname, is that Rock?"
REASK_FIRST = "I want to make sure I get that right — could you say your first name again?"
REASK_SURNAME = "Sorry, I didn't catch that — what was your surname?"
REASK_ONCE_MORE = "Could you say your name once more for me?"
FIRST_Q = "Lovely — could I take your first name?"
WHOLE_Q = "could I take your first name and surname?"


# ── N-2: a no to a name read-back is a correction ──────────────────────────

@pytest.mark.parametrize("prev", [READBACK, SURNAME_READBACK])
@pytest.mark.parametrize("said", [
    "no just a lecture and yeah",
    "no",
    "no it's roch",
    "no, that's not right",
    "nope, elektra",
])
def test_a_denied_name_gets_no_head(prev, said):
    assert classify_intent(said, prev) == [], (said, prev)
    # ...however the offer-refusal verdict came out. The keypad slot map from
    # an earlier readout was still latched on the live call.
    assert classify_intent(said, prev, offer_refused=True) == [], (said, prev)


def test_a_confirmed_name_keeps_its_head():
    assert classify_intent("yes", READBACK) == [Intent.NAME_GIVEN]
    assert classify_intent("yeah that's right", READBACK) == [Intent.NAME_GIVEN]


def test_a_no_to_a_slot_confirm_is_still_a_refusal():
    # The arm N-2 narrows, still doing its job everywhere else.
    assert classify_intent("no", "So that's Tuesday at ten — is that right?") == [
        Intent.REFUSAL
    ]
    assert classify_intent("no", "Would you like to book in?") == [Intent.REFUSAL]


# ── N-1: after a re-ask the receipt head stands down ───────────────────────

@pytest.mark.parametrize("prev", [REASK_FIRST, REASK_SURNAME, REASK_ONCE_MORE])
@pytest.mark.parametrize("said", ["a lecture", "elektra", "it's roch", "Kowalczyk"])
def test_a_name_after_a_reask_gets_no_head(prev, said):
    assert classify_intent(said, prev) == [], (said, prev)


@pytest.mark.parametrize("prev,said", [
    (FIRST_Q, "elektra"),
    (WHOLE_Q, "um yes that'll be elektra roch"),
    (WHOLE_Q, "elektra roch"),
])
def test_the_first_ask_keeps_its_head(prev, said):
    assert classify_intent(said, prev) == [Intent.NAME_GIVEN], (said, prev)


def test_again_in_a_non_name_question_is_not_a_reask():
    # "again" alone is not the signal; it has to be a NAME question.
    assert classify_intent("tuesday", "Which day works for you again?") != [], (
        "a day pick is still a day pick"
    )


def test_the_whole_call_shape():
    """Turns 4-7 of CA9bd192c9, as the classifier now reads them."""
    turns = [
        (WHOLE_Q, "um yes that'll be a lecture zani", [Intent.NAME_GIVEN]),
        (READBACK, "no just a lecture and yeah", []),
        (REASK_FIRST, "a lecture", []),
    ]
    for prev, said, expected in turns:
        assert classify_intent(said, prev, offer_refused=("no" in said)) == expected, (
            prev, said
        )
