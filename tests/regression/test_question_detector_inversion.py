"""
A yes/no question is still a question (T-13).

Observed live, call 5 of the Theorem acceptance sweep, 2026-08-04 21:35:49.
With the clinic question pending, the caller asked:

    "should I take ibuprofen, ice or heat in the meantime"

and was answered:

    "No worries — did you say the Awlstuh clinic? If so, just say…"

`_transcript_is_question` returned False, so the utterance took the
Haiku-unknown NON-question branch and climbed the location ladder instead of
being answered. The caller had to repeat themselves — "no I said should I take
ibuprofen" — to be heard at all.

Two things combined to make this silent:

1. `_QUESTION_SIGNALS` held only wh-words. English forms yes/no questions by
   inverting an auxiliary, with no question word anywhere in the sentence.
2. There is no "?" to fall back on. The AssemblyAI handshake sets
   `format_turns=false`, so transcripts arrive unpunctuated and the word list
   is the ONLY signal available.

The blast radius is narrow — it only misroutes while the location question is
pending — but that is exactly when a caller is most likely to interrupt with a
clarifying question, and here the question was about medication.
"""

import pytest

from app.media_streams.connection import _transcript_is_question


# The utterance from the live call, exactly as STT delivered it.
LIVE_CALL_5 = "um should i take ibuprofen ice or heat in the meantime"


def test_the_live_regression():
    assert _transcript_is_question(LIVE_CALL_5), (
        "the call-5 medication question is classified a non-question again"
    )


@pytest.mark.parametrize("utterance", [
    # auxiliary inversion — no wh-word anywhere
    "should i take ibuprofen",
    "can i come in today",
    "could i see mark instead",
    "shall i bring anything",
    "may i bring my daughter",
    "do i need a gp referral",
    "did i need to pay upfront",
    "have i got time to park",
    "will i need more than one session",
    "am i able to claim it back",
    "is it going to hurt",
    "are you open on saturdays",
    "are there stairs",
    "have you got parking",
    "would you recommend shockwave",
    "does he do home visits",
    "can we book two together",
])
def test_inverted_questions_are_questions(utterance):
    assert _transcript_is_question(utterance), f"{utterance!r} read as a statement"


@pytest.mark.parametrize("utterance", [
    # Location answers. These MUST stay false, or the ladder can never
    # resolve a clinic through this path and every answer goes to the LLM.
    "alcester",
    "redditch",
    "the awlstuh clinic",
    "your alcester clinic",
    "alcester please",
    "the first one",
    "redditch one",
    "use this clinic",
    "yeah alcester",
])
def test_location_answers_are_not_questions(utterance):
    assert not _transcript_is_question(utterance), (
        f"{utterance!r} now reads as a question — the location ladder can no "
        "longer resolve a spoken clinic answer"
    )


def test_trailing_question_mark_still_wins():
    """Punctuation remains a valid signal for any path that does supply it."""
    assert _transcript_is_question("alcester?")


def test_unpunctuated_input_is_the_realistic_case():
    """Guard the assumption this fix rests on: STT gives us no punctuation, so
    a wordlist gap is a silent gap. If format_turns is ever turned on, this
    test is the note explaining why the list is as long as it is."""
    from app.media_streams import config

    assert "format_turns=false" in config.ASSEMBLYAI_WS_URL, (
        "format_turns is no longer false — transcripts may now carry '?', "
        "which changes the reasoning behind _QUESTION_SIGNALS"
    )
