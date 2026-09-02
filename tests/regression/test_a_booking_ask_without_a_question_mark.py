"""
A booking question does not have to carry a question mark.

D3, 2026-09-02. Gate 5g-b sequences the two booking steps by finding which
sentence asks for the name and which asks for the phone. `_booking_detail_asked`
returned an empty set for any sentence without "?", so

    "I'll need your full name and mobile number"

asked for both steps in one breath and passed through untouched. It reached a
caller and was caught only incidentally, because that turn also carried a write
CTA that Gate 5g matched one step later - the backstop, not the sequencer.

WHY THE "?" TEST IS NOT REMOVED. Its reason has been in the docstring since the
gate was written: a statement naming these nouns is usually a REPORT.

    "I've got your mobile on the booking"      <- reports, asks nothing
    "I'll need your full name and mobile"      <- asks for both

Same nouns, opposite meaning. So the statement path is the narrowest thing that
closes D3, and it is narrower than the question path in three ways. Each has a
test below, because each is load-bearing:

  1. It requires an explicit REQUEST STEM. A report has none.
  2. It returns the COMBINED pair or NOTHING - never a single step. Gate 5g-b
     rewrites only a combined ask; a single-step ask is deliberately kept (the
     O-18 note at the gate). So a false positive cannot reach the rewrite, and
     - because an empty set reads as "not a booking question" - it cannot
     consume the one-question-per-turn budget and delete a real question later
     in the same chunk. That last failure is the one this shape rules out by
     construction, and `test_a_report_does_not_eat_the_question_after_it` is
     the test that would catch it coming back.
  3. It stands down on callback vocabulary. The gate's own comment records why:
     "I can take your name and number and have someone call you back" asks for
     both CORRECTLY, and rewriting it breaks the capture. The booking anchor
     normally keeps a callback out of this gate, but `slots_presented` is never
     reset, so a callback offered AFTER a failed booking still has it set.

SCOPE. The question path is byte-for-byte unchanged, including its own exposure
to the callback phrasing. Widening the statement path is not the place to change
how questions are handled; that is a separate decision with its own evidence.
"""

import pytest

from app.media_streams.turn_handler import (
    _booking_detail_asked,
    sanitise_response,
)


# The D3 sentence, and the state the call is in when it is spoken: a slot has
# been read out, nothing collected yet.
D3 = "I'll need your full name and mobile number."


def _booking_session(**over):
    session = {"slots_presented": True, "twilio_from": ""}
    session.update(over)
    return session


# -- the reproduction ------------------------------------------------------

def test_the_statement_form_is_resequenced_to_the_name_alone():
    out = sanitise_response(D3, _booking_session())
    assert "first name and surname" in out
    assert "mobile" not in out.lower()


def test_it_asks_exactly_one_thing():
    out = sanitise_response(D3, _booking_session())
    assert out.count("?") == 1


@pytest.mark.parametrize(
    "sentence",
    [
        "I'll need your full name and mobile number.",
        "I will need your full name and your mobile number.",
        "I just need your name and a contact number.",
        "Let me take your first name and surname and your mobile number.",
        "I'll take your full name and mobile number.",
    ],
)
def test_every_request_stem_reaches_the_gate(sentence):
    assert _booking_detail_asked(sentence) == {"name", "phone"}


# -- 1. a report is not a request ------------------------------------------

@pytest.mark.parametrize(
    "sentence",
    [
        "I've got your mobile on the booking.",
        "I have your name and number already.",
        "That's your name and mobile number saved.",
    ],
)
def test_a_report_asks_for_nothing(sentence):
    """The reason the "?" test exists. These name the same nouns as D3."""
    assert _booking_detail_asked(sentence) == set()


def test_a_report_is_not_rewritten():
    out = sanitise_response("I've got your mobile on the booking.", _booking_session())
    assert out == "I've got your mobile on the booking."


# -- 2. never a single step, and never the budget --------------------------

@pytest.mark.parametrize(
    "sentence",
    [
        "I'll need your mobile number.",
        "I'll need your full name.",
        "Let me take your first name.",
    ],
)
def test_a_single_step_statement_returns_nothing(sentence):
    """Not {"phone"} or {"name"} - NOTHING. A single-step ask is kept by the
    gate anyway, but returning it here would consume the one-question budget
    and silently drop a real question later in the chunk."""
    assert _booking_detail_asked(sentence) == set()


def test_a_report_does_not_eat_the_question_after_it():
    """The failure mode the combined-only rule exists to prevent.

    If a statement were allowed to report a single step, it would set the
    gate's `_seen_detail_q` latch, and the genuine question behind it would be
    dropped entirely - leaving the caller a statement, no question, and dead
    air until the watchdog. Here the report must pass through AND the combined
    question behind it must still be resequenced.
    """
    text = (
        "I have your name and number already. "
        "Can I get your full name and mobile number, please?"
    )
    out = sanitise_response(text, _booking_session())
    assert "first name and surname" in out, "the real question was dropped"
    assert out.count("?") == 1


# -- 3. a callback asks for both correctly ---------------------------------

@pytest.mark.parametrize(
    "sentence",
    [
        "I can take your name and number and have someone call you back.",
        "I'll take your name and mobile number and someone will ring you back.",
        "Let me take your name and number and I'll get back to you.",
    ],
)
def test_a_callback_offer_is_left_alone(sentence):
    """Asking for both at once is CORRECT here. Rewriting it to "could I take
    your first name?" breaks the capture - the gate's own comment says so."""
    assert _booking_detail_asked(sentence) == set()
    assert sanitise_response(sentence, _booking_session()) == sentence


# -- the question path is untouched ----------------------------------------

@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Could I take your first name and surname?", {"name"}),
        ("What's your mobile number?", {"phone"}),
        ("Can I take your full name and mobile number?", {"name", "phone"}),
        ("Which number would you like?", set()),
    ],
)
def test_questions_are_classified_exactly_as_before(sentence, expected):
    assert _booking_detail_asked(sentence) == expected


def test_a_slot_question_is_still_none_of_this_gates_business():
    """The numbered readout ends in "which number would you like?". A gate that
    ate that would strand the pick - the defect the slot work has spent two
    days on."""
    text = "Number 1, Saturday 12th September. Which number would you like?"
    assert sanitise_response(text, _booking_session()) == text


def test_the_gate_does_not_run_once_both_steps_are_in():
    session = _booking_session(phone_confirmed=True)
    session["collected"] = {"name": "Quentin Rock"}
    assert sanitise_response(D3, session) == D3
