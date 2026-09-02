"""
The model may not ask for two booking steps at once on a turn where it
improvises.

Scope, stated up front because the obvious wider rule is a trap: this gate
governs COMBINATION, not order. A lone question for the wrong step is left
alone - see test_a_lone_phone_question_is_deliberately_left_alone.

Vital Edge, 2026-09-02 - a booking that succeeded after four asks:

    turn 10  caller: "the saturday at 6 in the evening works"
             -> misread as time_of_day_preference=evenings, not a slot pick
             -> check_availability called again
             -> BLOCKED: {"status": "already_retrieved"}
             -> no usable tool result, no scripted next step
    turn 11  "Can I get your full name and mobile number, please?"
    turn 12  caller answers both at once; the name extractor sees one blob
    turn 13  the model finally reaches for a CTA. Gate 5g fires HERE, for the
             first time, and asks for the NAME - after the number.

WHY GATE 5g DID NOT CATCH TURN 11. Its trigger is the write-CTA vocabulary
itself ("shall i go ahead and book" | "book that in" | "put that request
through"). Turn 11 carried none of it, so there was nothing to match and the
improvised two-part question went to TTS verbatim. That gate is a backstop on
the LAST step; the steps before it had no sequencer at all.

WHY NOT "REPLACE ANY QUESTION WHILE A BOOKING STEP IS OUTSTANDING". Because
booking_flow_active is armed at the booking ACKNOWLEDGEMENT (connection.py,
"[ms_conn] booking_flow_active = True"), which happens before slots are ever
read out. A gate that broad would replace the slot-selection question - the
very turn this call had already stumbled on - with a request for the caller's
name. Only questions that ask for the NAME or the PHONE are governed here.
"""

import pytest

from app.media_streams.turn_handler import (
    _booking_detail_asked,
    _outstanding_booking_step,
    sanitise_response,
)


# The literal turn 11, and the state the call was in when it was spoken:
# booking acknowledged, a slot under discussion, nothing collected.
TURN_11 = "Can I get your full name and mobile number, please?"


def _booking_session(**over):
    session = {"booking_flow_active": True, "twilio_from": ""}
    session.update(over)
    return session


# -- the reproduction ------------------------------------------------------

def test_the_combined_question_becomes_the_name_question_alone():
    out = sanitise_response(TURN_11, _booking_session())
    assert "first name and surname" in out
    # The phone half is gone. It is step 8 and the name is step 7.
    assert "mobile" not in out.lower()
    assert "keypad" not in out.lower()


def test_the_phone_is_not_asked_until_the_name_is_stored():
    """The second half of the defect: turn 12's answer is one blob because
    turn 11 asked for two things. With the name outstanding, the phone
    question must not appear at all."""
    out = sanitise_response(TURN_11, _booking_session())
    assert out.count("?") == 1

    # Name now in hand (and a fresh turn, so the latch is clear) - NOW the
    # phone question is the correct one, and it is what the gate substitutes.
    out = sanitise_response(TURN_11, _booking_session(patient_name="Quentin Rook"))
    assert "keypad" in out.lower()
    assert "first name and surname" not in out


def test_a_lone_phone_question_is_deliberately_left_alone():
    """The gate governs COMBINATION, not order - and this is the line it
    must not cross.

    A lone "what's your number?" with no name on record IS out of order.
    Rewriting it to the name question reopens O-18: that sentence is almost
    always the model acknowledging the name the caller just gave ("Thanks
    Quentin - is oh seven five... the best number for you?"), and the
    acknowledgement is the only thing _v3_try_persist_name can read a first
    name out of - it scans the assistant reply; the caller's own utterance
    yields a surname only. Delete it and the name is never learned, so the
    same question is asked again next turn, and the turn after. On
    CA041352eb the caller gave his name three times, was asked a fourth,
    and hung up.

    Mis-ordering degrades to Gate 5g catching it one step later at the CTA.
    A deadlock does not degrade at all.
    """
    spoken = "Thanks Quentin - is oh, seven, five, oh, two the best number for you?"
    session = _booking_session()
    assert sanitise_response(spoken, session) == spoken
    assert not session.get("_gate5g_dropped_name_ack")


def test_the_combined_question_is_safe_to_delete_because_it_asks_for_the_name():
    """Why the combined case is exempt from the reasoning above: a sentence
    that ASKS for the name cannot also be speaking one, so replacing it
    destroys no name evidence."""
    assert "quentin" not in TURN_11.lower()
    assert _booking_detail_asked(TURN_11) == {"name", "phone"}


# -- the restraint: what the gate must NOT touch ---------------------------

@pytest.mark.parametrize("spoken", [
    # Slot selection. booking_flow_active is TRUE here and no name is on
    # record - this is the exact state the gate runs in, and eating this
    # question strands the caller mid-pick.
    "Number 1, Saturday the 5th at six in the evening. Number 2, Monday the "
    "7th at ten past nine. Which number would you like?",
    "Would you like the Saturday or the Monday?",
    # Screening.
    "Have you had any numbness or pins and needles in the saddle area?",
    # An FAQ answer's follow-up.
    "We've got eighty parking spaces. Was there anything else?",
    # Timing.
    "Are mornings or afternoons better for you?",
])
def test_questions_that_are_not_booking_steps_are_untouched(spoken):
    assert sanitise_response(spoken, _booking_session()) == spoken


def test_the_correct_question_is_left_exactly_as_the_model_wrote_it():
    """A model that asks the outstanding step, on its own, is doing the right
    thing. Rewriting it would throw away better phrasing for no gain."""
    spoken = "Lovely. Could I take your name, please?"
    assert sanitise_response(spoken, _booking_session()) == spoken


def test_a_statement_about_the_number_is_not_a_question():
    spoken = "I've got your mobile number on the booking already."
    assert sanitise_response(spoken, _booking_session()) == spoken


def test_the_gate_is_dormant_once_both_steps_are_in():
    """Nothing outstanding, so a later confirmation question about the number
    is legitimate and must survive."""
    spoken = "And your mobile number is the best one to reach you on?"
    session = _booking_session(patient_name="Quentin Rook", phone_confirmed=True)
    assert sanitise_response(spoken, session) == spoken


def test_the_gate_is_dormant_before_the_booking_flow_opens():
    """Pre-booking FAQ turns are not sequenced. Same scoping as Gate 5c."""
    assert sanitise_response(TURN_11, {"twilio_from": ""}) == TURN_11


# -- mechanics -------------------------------------------------------------

def test_only_one_booking_question_survives_a_chunk():
    out = sanitise_response(
        "Could I take your full name? And what's your mobile number?",
        _booking_session(),
    )
    assert out.count("?") == 1
    assert "mobile" not in out.lower()


def test_the_substitution_does_not_run_into_the_previous_sentence():
    """The replacement carries the sentence's leading whitespace, or the text
    that becomes last_bot_prompt reads '...evening.Before I' and the
    sentence-splitting matchers downstream mis-parse it."""
    out = sanitise_response(
        "That Saturday at six is free. Can I get your full name and mobile "
        "number, please?",
        _booking_session(),
    )
    assert ". Before I do that" in out
    assert ".Before" not in out


def test_the_substitution_happens_once_across_a_two_chunk_turn():
    """sanitise_response runs per streamed chunk. Without the turn-scoped
    latch, a turn split across two chunks asks for the name twice."""
    session = _booking_session()
    first = sanitise_response(TURN_11, session)
    assert "first name and surname" in first
    second = sanitise_response("And your mobile number?", session)
    assert "first name and surname" not in second


def test_the_replacement_is_stable_under_a_second_pass():
    """The substituted question is itself a name question. Re-sanitising it
    must not rewrite or delete it - the same self-deletion trap
    _phone_question_for documents."""
    session = _booking_session()
    once = sanitise_response(TURN_11, session)
    session.pop("_gate5g_step_substituted")
    assert sanitise_response(once, session) == once


def test_the_dropped_name_ack_flag_is_set_when_the_name_is_missing():
    """Belt and braces for O-18.

    The combined question should not be carrying a name - it is asking for
    one - but a model that writes "Thanks Quentin, and can I get your full
    name and mobile number?" would have its only name evidence deleted here.
    Setting the flag lets _v3_try_persist_name fall back to the raw
    generation, exactly as the Gate 5g hold-back does.
    """
    session = _booking_session()
    sanitise_response(TURN_11, session)
    assert session.get("_gate5g_dropped_name_ack") is True

    # ...and not when the name is already known: only the NAME case can lose
    # evidence, so a phone-step substitution must leave the flag clear.
    session = _booking_session(patient_name="Quentin Rook")
    sanitise_response(TURN_11, session)
    assert not session.get("_gate5g_dropped_name_ack")


@pytest.mark.parametrize("sentence,expected", [
    ("Can I get your full name and mobile number, please?", {"name", "phone"}),
    ("Could I take your name?", {"name"}),
    ("What's your mobile number?", {"phone"}),
    ("Which number would you like?", set()),
    ("Number 1 or number 2?", set()),
    ("I have your name on the booking.", set()),
    ("Can I get the name of your GP?", set()),
])
def test_booking_detail_asked(sentence, expected):
    assert _booking_detail_asked(sentence) == expected


@pytest.mark.parametrize("session,expected", [
    ({}, "name"),
    ({"patient_name": "Quentin Rook"}, "phone"),
    ({"patient_name": "Quentin Rook", "phone_confirmed": True}, None),
    ({"phone_confirmed": True}, "name"),
])
def test_outstanding_step_is_name_before_phone(session, expected):
    assert _outstanding_booking_step(session) == expected
