"""A time preference stated before any offer does not get a pick head.

CA7de22277, Theorem line, 14 Sep 2026 09:27, build 67ab481f -- the first
patient-line call after the promotion:

    Susie : "Is there a particular day or time that works best for you?"
    caller: "um anytime in 2 weeks' time"
    head  : situational head (slot_picked): "That one works —"

Nothing was on the table. The classifier's pick-question list carries "works
best for you" (for "which of those works best for you?"), the preference
question happens to end in the same words, and the "2" in "2 weeks" read as a
clock time. A preference question is excluded by shape; the engine's
`slot_selection` verdict is untouched.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent

PREF_THEOREM = "Is there a particular day or time that works best for you?"
PREF_DEMO = "Do you have a preference for when you'd like to come in?"


@pytest.mark.parametrize("prev", [PREF_THEOREM, PREF_DEMO])
@pytest.mark.parametrize("said", [
    "um anytime in 2 weeks' time", "anytime next week", "2 o'clock",
    "sometime around 10", "any day at 3",
])
def test_a_preference_answer_is_never_a_slot_pick(prev, said):
    assert Intent.SLOT_PICKED not in classify_intent(said, prev, slot_selection=False), (prev, said)


def test_a_real_pick_question_still_is_one():
    prev = ("Number 1, eleven in the morning. Number 2, three in the afternoon. "
            "Which of those works best for you?")
    assert classify_intent("eleven in the morning", prev, slot_selection=True) == [Intent.SLOT_PICKED]
    assert classify_intent("eleven in the morning", prev, slot_selection=False) == [Intent.SLOT_PICKED]


# ── CA3aee2959 (Theorem cancel call, 14 Sep 2026) ─────────────────────────

def test_confirming_a_found_appointment_is_not_a_slot_pick():
    prev = ("I can see an appointment on Wednesday the 30th of September at eleven "
            "in the morning at our Alcester clinic — is that the right one?")
    assert Intent.SLOT_PICKED not in classify_intent("yes that's the right one", prev, slot_selection=False)


def test_a_cancel_after_the_appointment_is_found_is_the_decision():
    from app.hold_speech import render_intent_head

    prev = "Would you like to reschedule this appointment, or cancel it altogether?"
    hits = classify_intent("i'd like to cancel it altogether", prev, slot_selection=False)
    assert hits == [Intent.CANCEL_CONFIRMED], hits
    assert render_intent_head(Intent.CANCEL_CONFIRMED, index=0).startswith("Cancelling that for you")


def test_a_cancel_at_the_top_of_the_call_is_still_a_request():
    prev = "to speak to Mark directly press 1, otherwise how can I help you today?"
    assert classify_intent("um yeah i'd like to cancel my appointment", prev, slot_selection=False) == [Intent.CANCEL_REQ]
