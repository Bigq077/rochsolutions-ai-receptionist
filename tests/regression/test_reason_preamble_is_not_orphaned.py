"""Deleting the reason question must not leave its run-up behind.

CA32440a92, 2026-08-08 22:14, Theorem live. Twice in one call:

    22:14:10  [ms_gate5] removed banned phrase (reason_question)
    22:14:10  synthesise_chunk: "Just so we've got a reason on the booking."

    22:14:27  [ms_gate5] removed banned phrase (reason_question)
    22:14:27  synthesise_chunk: "I need a reason on the booking."

Gate 5b-r took the question and left the justification for it. The caller heard
a preamble to nothing, then silence — the T-3 nudge fired both times ("turn
answered but asked nothing and none was outstanding") — and came back with
"hello you got cut off".

_REASON_QUESTION_RE matches a WHOLE sentence, which is correct and deliberate.
The gap is that the model does not always put the whole intent in one sentence.
Sentence boundaries are the entire difference between the two behaviours, and
the same call shows both: four minutes earlier the single-sentence form
("could I ask what brings you in?") emptied the turn, and the fallback asked
the outstanding step exactly as designed.

Same family as B-36 and the Gate-5g name deadlock: a matcher keyed to one
literal of model speech, defeated by the model saying the same thing a
different way.
"""

import re

import pytest

from app.media_streams.turn_handler import (
    _REASON_QUESTION_RE,
    _REASON_PREAMBLE_RE,
)


def _gate(text: str) -> str:
    """The Gate 5b-r strip, minus the session-dependent fallback.

    Mirrors the call site: the preamble strip runs ONLY when a reason question
    was actually removed. Returns "" where production substitutes the next
    outstanding booking question.
    """
    cleaned = _REASON_QUESTION_RE.sub("", text)
    if cleaned == text:
        return text
    cleaned = _REASON_PREAMBLE_RE.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# The live call
# ---------------------------------------------------------------------------
SPLIT_ACROSS_SENTENCES = [
    "Just so we've got a reason on the booking. What brings you in?",
    "I need a reason on the booking. What's the appointment for?",
    "I just need a reason for the appointment. What brings you in?",
    "Just for our records. What's the appointment for?",
    "I need a reason on the booking! What brings you in?",
]


@pytest.mark.parametrize("text", SPLIT_ACROSS_SENTENCES)
def test_the_preamble_does_not_survive_its_question(text):
    assert _gate(text) == "", (
        f"left {_gate(text)!r} in the caller's ear — a justification for a "
        f"question that was just deleted. Production then has nothing to say "
        f"and the T-3 nudge fires."
    )


def test_the_single_sentence_form_still_empties():
    """The shape that already worked must keep working."""
    assert _gate("Before I check that day, could I ask what brings you in?") == ""


# ---------------------------------------------------------------------------
# What must survive
# ---------------------------------------------------------------------------
def test_legitimate_content_in_the_same_turn_is_kept():
    """The strip takes the reason question and its run-up — nothing else."""
    out = _gate(
        "That's Monday at ten. Just so we've got a reason on the booking. "
        "What brings you in?"
    )
    assert out == "That's Monday at ten.", out


UNTOUCHED = [
    # No reason question present, so the preamble strip must never run at all.
    "I've noted the reason on the booking.",
    "That's booked in for you.",
    "Could I take your phone number?",
    # Vital Edge's MANDATED wording. VE is exempted upstream by
    # _clinic_asks_its_own_reason_question on branches that have it, but Gate
    # 5b-r is unconditional here, so the pattern itself must not reach it.
    # It is a question, and the preamble pattern only ever ends in "." or "!".
    "Is there a particular area or reason for the massage today?",
]


@pytest.mark.parametrize("text", UNTOUCHED)
def test_ordinary_turns_are_untouched(text):
    assert _gate(text) == text


def test_the_preamble_pattern_never_takes_a_question():
    """Load-bearing: the pattern ends in [.!], never [?].

    A sentence that IS a question about the reason belongs to whatever gate
    owns that decision, not to an orphan-cleanup rule.
    """
    assert _REASON_PREAMBLE_RE.search(
        "Just so we've got a reason on the booking."
    )
    assert not _REASON_PREAMBLE_RE.search(
        "Shall I put a reason on the booking?"
    )


def test_the_bare_word_reason_is_not_enough():
    """Requires the admin framing, so ordinary speech is out of range."""
    assert not _REASON_PREAMBLE_RE.search("That's the reason I asked.")
    assert not _REASON_PREAMBLE_RE.search("There's no reason to worry.")
