"""
Regression: Susie asked what brings the caller in.

Owner decision 2026-08-07, restated 2026-08-08: the reason for the visit is
recorded ONLY when the caller volunteers it, unprompted, in their own words.
There is no turn, step or slot in the flow where asking is correct. An empty
reason is a correct outcome, not a gap to fill — `first_turn_extractor`
captures it deterministically when the caller names a body part themselves.

The prompt has said so since susie_system_prompt.py:901. The model asks anyway,
and asks it at the worst point — between the slot and the phone step. Two
earlier fixes were drafted and withdrawn because they RE-ORDERED the question
rather than removing it (see O-5). Suppression is the fix.

Observed on CA041352eb04a40fcb5ebd13ee37379722 (2026-08-08 00:01:04), where the
ENTIRE turn was the reason question, in two phrasings back to back:

    'Before I go ahead and check that day, could I ask what brings you in?'
    "what's the appointment for?"

and on CA1e7552819091949f02c08a39f5203d36 (2026-08-07 23:43:07), where it was
folded into a reply that also carried the slot readback and the name request:

    "Before I get that booked, could I ask what's bringing you in? So that's
     Monday the 10th of August at five in the evening — could I take your first
     name and surname?"

Those two shapes are why the rule is not in the flat _BANNED_SENTENCE_RE list.
The folded case must lose one sentence and keep the rest. The whole-turn case
strips to nothing, and an empty turn is NOT safe: it falls through to the
deferred Gate-5 fallback, which speaks "Sorry, I didn't quite catch that" —
a non-sequitur answering a question the caller was never meant to hear.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _REASON_QUESTION_RE,
    _next_booking_question_for,
    sanitise_response,
)


# ── the phrasings, verbatim from the calls ──────────────────────────────────

@pytest.mark.parametrize(
    "sentence",
    [
        "Before I go ahead and check that day, could I ask what brings you in?",
        "what's the appointment for?",
        "What is the appointment for?",
        "Could I ask what's bringing you in?",
        "So what's going on with it?",
        "What's going on with that?",
        "What's been troubling you?",
        "What's the issue?",
    ],
)
def test_reason_question_is_stripped(sentence):
    assert _REASON_QUESTION_RE.sub("", sentence).strip() == "", sentence


# ── things that merely look similar and must survive ────────────────────────

@pytest.mark.parametrize(
    "sentence",
    [
        "Is there a particular day or time that works best for you?",
        "Could I take your first name and surname?",
        "Here's what we've got coming up — Number 1, Monday 10th August.",
        "Is this for our Alcester or Redditch clinic?",
        "So that's Quentin Rock, Monday the 10th of August at five in the "
        "evening — shall I go ahead and book that in?",
        "What's the best number to reach you on?",
    ],
)
def test_ordinary_questions_are_untouched(sentence):
    assert _REASON_QUESTION_RE.sub("", sentence) == sentence, sentence


# ── the folded case: lose one sentence, keep the rest ───────────────────────

def test_only_the_reason_sentence_is_removed_from_a_longer_reply():
    """CA1e755281 23:43:07 — the readback and the name request must survive."""
    reply = (
        "Before I get that booked, could I ask what's bringing you in? "
        "So that's Monday the 10th of August at five in the evening — "
        "could I take your first name and surname?"
    )
    out = _REASON_QUESTION_RE.sub("", reply).strip()
    assert "bringing you in" not in out
    assert "Monday the 10th of August" in out
    assert "first name and surname" in out


# ── the whole-turn case: must not hand back an empty turn ───────────────────

def test_a_turn_that_was_only_the_reason_question_asks_the_real_step_instead():
    """
    CA041352eb 00:01:04 — the whole turn was the reason question. Stripping to
    "" drops into the deferred Gate-5 fallback ("Sorry, I didn't quite catch
    that"), which is a non-sequitur. The outstanding booking step is asked
    instead.
    """
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "Before I go ahead and check that day, could I ask what brings you in?",
        session,
    )
    assert out.strip(), "the turn was emptied — caller gets the fallback re-ask"
    assert "brings you in" not in out.lower()
    assert out.strip() == _next_booking_question_for(session).strip()


def test_both_reason_phrasings_in_one_turn_still_leave_a_question():
    """The same call asked it twice in a row, as two separate sentences."""
    session = {"booking_flow_active": True}
    out = sanitise_response(
        "Before I go ahead and check that day, could I ask what brings you in? "
        "what's the appointment for?",
        session,
    )
    assert out.strip()
    assert "brings you in" not in out.lower()
    assert "appointment for" not in out.lower()


# ── the substitution must not be a reason question itself ───────────────────

def test_the_substituted_question_is_not_itself_stripped():
    """
    A replacement that the same gate deletes would empty the turn on the next
    pass. This already happened once in this file's sibling gate, where a
    replacement carrying CTA vocabulary deleted itself.
    """
    for session in (
        {"booking_flow_active": True},
        {"booking_flow_active": True, "patient_name": "Quentin Rock"},
    ):
        q = _next_booking_question_for(session)
        assert _REASON_QUESTION_RE.sub("", q) == q, q
