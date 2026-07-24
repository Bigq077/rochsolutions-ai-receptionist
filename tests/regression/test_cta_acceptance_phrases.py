# tests/regression/test_cta_acceptance_phrases.py
"""
S-09 (2026-07-24) — the caller says yes to a booking offer and Susie misses it.

Background
----------
When the previous bot reply carried a booking CTA ("Would you like to book an
appointment?"), `connection.py` decides whether the caller's reply was a yes.
Before this fix that decision ran on nine tokens plus one phrase:

    yes yeah yep sure okay ok yup absolutely definitely | "go ahead"

Anything else was not an acceptance.  The reported incident was a caller who
answered "go on then" — an ordinary British yes — and got no booking flow.
This is the same shape of defect as the single-word allowlist
(test_single_word_dispatch_default.py): a hand-maintained vocabulary standing
between the caller and the thing they asked for, failing one phrase at a time.

Unlike that one, the default here CANNOT be inverted: "no thanks" and "what
does it cost?" are also replies to a CTA and must not start a booking.  So the
fix is a widened vocabulary plus an explicit decline guard.

Asymmetry that governs this whole module
----------------------------------------
A MISS costs one LLM turn — the model usually handles the yes anyway.
A FALSE POSITIVE sets booking_flow_active, which makes the LLM tack a booking
push onto every later FAQ answer (BUG-7) and, historically, pivoted a call back
to booking seconds after a 999/A&E escalation (Call 6, 2026-06-18).

So the decline tests below are the load-bearing half of this file. If a change
has to trade one against the other, it trades away recall.
"""

import pytest

from app.media_streams.connection import _utterance_affirms_cta


# ---------------------------------------------------------------------------
# The regression: natural acceptances the nine-token vocabulary missed.
# ---------------------------------------------------------------------------
MISSED_ACCEPTANCES = [
    "go on then",           # the reported S-09 incident
    "go on then please",
    "please do",
    "please book",
    "sounds good",
    "sounds great",
    "that works",
    "that would work",
    "that would be great",
    "that'd be great",
    "that's great",
    "that's fine",
    "let's do it",
    "lets do it",
    "i'd like that",
    "book me in",
    "if you could",
    "if you would",
    "carry on",
    "perfect",
    "brilliant",
    "lovely",
    "no worries",           # British acceptance -- must survive the "no" guard
    "no problem",
]


# Already worked before the fix; must keep working.
ALREADY_WORKING = [
    "yes",
    "yes please",
    "yeah",
    "yep",
    "sure",
    "okay",
    "ok",
    "absolutely",
    "definitely",
    "go ahead",
    "yes, when can I come in?",   # strong token inside a wh-question
]


# ---------------------------------------------------------------------------
# Declines. A false positive here is the expensive direction -- see docstring.
# ---------------------------------------------------------------------------
DECLINES = [
    "no",
    "no thanks",
    "no thank you",
    "nope",
    "nah",
    "not right now",
    "not at the moment",
    "not yet",
    "not today",
    "maybe later",
    "another time",
    "definitely not",
    "absolutely not",
    "i'd rather not",
    # The pre-fix regex scored all of these as affirmations, because
    # "ok"/"okay"/"sure" matched regardless of the negation in front of them.
    "no I'm fine",
    "no I'm okay",
    "no thanks I'm good",
    "I'm fine thanks",
    "I'm not sure",
    "not sure",
    "not really",
    "I don't think so",
]


# Hesitation. Not a no, but not a yes either -- the LLM should ask again rather
# than the caller finding themselves mid-booking.
HESITATIONS = [
    "let me think about it",
    "can I call you back",
    "I need to check my diary",
]


# Questions asked in reply to a CTA. The caller has not agreed to anything yet;
# the LLM answers the question and may re-offer.
NON_ANSWERS = [
    "what does it cost?",
    "how long does it take?",
    "how long does it go on for?",   # contains weak "go on"
    "what do I do?",                 # Call 6 emergency phrasing -- weak "i do"
    "what do I do, what do I do",
    "where are you based?",
    "which clinic is that?",
]


@pytest.mark.parametrize("utterance", MISSED_ACCEPTANCES)
def test_natural_acceptances_are_recognised(utterance):
    """The regression: an ordinary yes must register as a yes."""
    assert _utterance_affirms_cta(utterance), (
        f"{utterance!r} is an acceptance of a booking offer and was not "
        "recognised — the caller said yes and Susie carried on as if they "
        "had not answered."
    )


@pytest.mark.parametrize("utterance", ALREADY_WORKING)
def test_previously_recognised_acceptances_still_work(utterance):
    """No recall lost on the phrases the old vocabulary did handle."""
    assert _utterance_affirms_cta(utterance), f"{utterance!r} regressed"


@pytest.mark.parametrize("utterance", DECLINES)
def test_declines_never_affirm(utterance):
    """A no must never start a booking flow.

    This is the half of the contract that protects the caller. Widening the
    acceptance vocabulary is only safe while this keeps passing.
    """
    assert not _utterance_affirms_cta(utterance), (
        f"{utterance!r} is a DECLINE and was scored as acceptance — this "
        "starts a booking flow the caller refused."
    )


@pytest.mark.parametrize("utterance", HESITATIONS)
def test_hesitations_never_affirm(utterance):
    """A caller who has not decided yet has not agreed."""
    assert not _utterance_affirms_cta(utterance), (
        f"{utterance!r} is hesitation, not acceptance"
    )


@pytest.mark.parametrize("utterance", NON_ANSWERS)
def test_questions_are_not_acceptances(utterance):
    """A question in reply to a CTA is not agreement.

    The wh-question guard exists because an emergency caller's "what do I do,
    what do I do" matched the weak token "i do" and pivoted the call back to
    booking right after the 999/A&E message (Call 6, 2026-06-18).
    """
    assert not _utterance_affirms_cta(utterance), (
        f"{utterance!r} is a question, not an acceptance"
    )


def test_empty_and_none_are_not_acceptances():
    """Defensive: the call site passes a raw transcript."""
    assert not _utterance_affirms_cta("")
    assert not _utterance_affirms_cta(None)
