"""Answering "what's the appointment for?" with a body part must get a head.

Live 2026-09-03 01:28:16, demo line, build 699dfc9f:

    01:28:16  'um just my left ankle nothing serious'
    01:28:20  LAT turn_seq=6 ttfa_ms=3642 content_ttfa_ms=3642   (equal: nothing spoke)
              then 14.8s of speech

`Intent.SYMPTOM` triggers on `_HURT` and corroborates with `_BODY`. That
utterance has "ankle" and nothing in `_HURT`, so no rule fired, and the turn
fell through to `UNKNOWN_SLOW` at 3.5s -- "Still with you --", which apologises
for a wait instead of acknowledging what the caller just said about their body.

THE COMMENT ABOVE `_HURT` ALREADY DIAGNOSED THIS, four lines above the rule:

    Injury is often described with no word for pain at all -- "done my ankle",
    "went over on it", "it gave way". The screening triggers learned the same
    lesson the hard way (a caller saying "my ankle ... I twisted it" armed no
    screen): adding more synonyms is the trap, the SHAPE of the matcher is the
    bug.

Requiring `_HURT` as the TRIGGER is that shape. So the fix is not another
synonym: a body part named IN ANSWER to the reason question is a complaint,
pain word or not.

The question is read from Susie's own previous turn, never inferred from the
answer, so this cannot fire on a body part mentioned anywhere else in the call
-- which is the containment failure that would make it a promised-work defect.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent, question_asks_the_reason

REASON_Q = "What's the appointment for?"
OTHER_Q = "Do you have a preference for when you'd like to come in?"
SCREEN_Q = "Any numbness or tingling around the saddle area?"


@pytest.mark.parametrize("utterance", [
    "um just my left ankle nothing serious",   # the live miss, 01:28:16
    "my ankle",
    "yeah my knee",
    "just my back",
    "it's my shoulder",
    "i went over on my ankle",                 # _HURT would catch this one too
])
def test_a_body_part_answering_the_reason_question_gets_a_head(utterance):
    hits = classify_intent(utterance, REASON_Q)
    assert Intent.SYMPTOM in hits, (
        f"{utterance!r} got {hits} -- a caller who has just named the part of "
        f"their body that is wrong is not waiting for a lookup, and silence "
        f"here hands the turn to UNKNOWN_SLOW, which apologises"
    )


@pytest.mark.parametrize("utterance", [
    "um just my left ankle nothing serious",
    "my ankle",
])
def test_the_same_words_after_a_different_question_get_nothing(utterance):
    """The whole safety of the widening. A body part is only a complaint in
    ANSWER to the reason question; anywhere else it is just a word, and a head
    fired on it would be a guess."""
    assert classify_intent(utterance, OTHER_Q) == []


def test_a_clinical_screen_still_outranks_it():
    """Two ways in, both of which must hold: the previous turn being a screen
    question, and the session's own pending-screen flag. A head in front of an
    unanswered red flag is the promised-work defect at its worst."""
    assert classify_intent("my ankle", SCREEN_Q) == []
    assert classify_intent("my ankle", REASON_Q, screen_pending=True) == []


def test_a_question_about_a_body_part_is_not_a_complaint():
    assert classify_intent("is my ankle something you treat?", REASON_Q) == []


@pytest.mark.parametrize("utterance", [
    "some sports massage please",
    "a check up",
    "just a general appointment",
])
def test_an_answer_naming_no_body_part_is_unchanged(utterance):
    assert classify_intent(utterance, REASON_Q) == []


# ── The matcher has ONE owner ───────────────────────────────────────────────

@pytest.mark.parametrize("asked", [
    "What's the appointment for?",
    "What is it for?",
    "What's the reason for the visit?",
    "What brings you in today?",
    # CAea8abdb, 2 Sep, Vital Edge -- the ask that the literal list missed.
    "Is there a particular area or concern you're looking to address?",
])
def test_the_reason_question_is_recognised_in_every_shape_seen_live(asked):
    assert question_asks_the_reason(asked), asked


@pytest.mark.parametrize("not_asked", [
    "Do you have a preference for when you'd like to come in?",
    "Any of those work?",
    "Could I take your first name and surname?",
    "",
    "What's the appointment for",          # no question mark
])
def test_it_does_not_fire_on_other_questions(not_asked):
    assert not question_asks_the_reason(not_asked)


def test_llm_stream_calls_the_shared_matcher_rather_than_copying_it():
    """Two copies of this matcher would be two answers to "was the reason
    question asked", and it has already been wrong twice for listing the
    literals seen so far instead of the shape (B-36; CAea8abdb).

    Pinned by source, because a second copy would pass every behavioural test
    in this file right up until the two lists drifted apart.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "app" / "media_streams" / "llm_stream.py").read_text(
        encoding="utf-8", errors="replace")
    assert "question_asks_the_reason" in src, (
        "llm_stream no longer calls the shared matcher"
    )
    assert "area or (?:reason|concern|issue|problem)" not in src, (
        "the reason-question pattern list has been copied back into "
        "llm_stream.py -- it belongs to hold_speech.question_asks_the_reason"
    )
