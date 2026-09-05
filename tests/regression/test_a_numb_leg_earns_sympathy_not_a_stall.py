"""
A caller reporting numbness heard a stall phrase, not sympathy.

northgate, 5 Sep 2026 — CAcb51bc27 and CA6b241e20, two calls a half hour apart,
same sentence:

    caller: "my lower back's been really bad and my leg's gone numb"
    Susie:  "Sorry, still with you —"

`classify_intent` returned `[]`. The SYMPTOM rule is trigger `_HURT`,
corroborator `_BODY`, blocker "ends with a question mark". `_BODY` matched
(back, leg). `_HURT` did not match anything in that sentence, so no intent was
found, `_hs_situational` stayed empty, and the arbiter's contentless head was
spoken instead of `Sorry to hear that —`.

THE GAP. `_HURT` carried the mechanical vocabulary — pain, sprain, strain,
ache, stiff, sore, twist, roll, swollen, gave way — and NOT ONE sensory or
neurological term. Numbness, tingling and pins-and-needles are the signs that
make a presentation clinically urgent, and they were the ones with no word in
the list.

This is a missing CLASS, not a missing phrase. The repeated lesson in this
codebase is that lengthening a trigger list is usually the trap and the matcher
SHAPE is the bug; here the shape is right — a symptom word corroborated by a
body part — and one whole category of symptom word was absent.

Kept safe in the direction that matters: every new term still needs a body part
to corroborate, and is still blocked by a trailing "?", so "is numbness normal
after surgery?" does not arm a sympathy head in front of an FAQ answer.
"""

import pytest

from app.hold_speech import Intent, classify_intent


LIVE = "yeah my lower back's been really bad and my leg's gone numb"


def _intents(text):
    return {i.value for i in classify_intent(text)}


def test_the_live_utterance_is_a_symptom():
    assert Intent.SYMPTOM in classify_intent(LIVE), (
        "classify_intent still returns nothing for a numb leg, so the caller "
        "gets 'Sorry, still with you —' instead of 'Sorry to hear that —'"
    )


@pytest.mark.parametrize("utterance", [
    LIVE,
    "um yeah my lower back's been really bad and my leg has gone numb",
    "my hand keeps tingling",
    "i get pins and needles in my foot",
    "my knee keeps giving way",
    "i've got shooting down my leg",
])
def test_the_neurological_class_is_covered(utterance):
    assert Intent.SYMPTOM in classify_intent(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "my knee is really sore",
    "i've twisted my ankle",
    "my shoulder is stiff",
    "i pulled my hamstring",
])
def test_the_mechanical_class_still_works(utterance):
    """The terms that were already there must not be disturbed."""
    assert Intent.SYMPTOM in classify_intent(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "is numbness normal after surgery?",
    "do you treat numbness?",
    "can tingling be treated?",
])
def test_a_question_about_a_symptom_is_not_a_symptom(utterance):
    """The trailing-'?' blocker is what stops a sympathy head landing in front
    of an FAQ answer, and it must keep applying to the new terms."""
    assert Intent.SYMPTOM not in classify_intent(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "the signal has gone a bit numb",   # no body part to corroborate
    "how much does it cost",
    "can i book for next week",
])
def test_a_symptom_word_alone_is_not_a_symptom(utterance):
    """Corroboration by a body part is the guard that keeps this narrow."""
    assert Intent.SYMPTOM not in classify_intent(utterance), utterance


def test_the_trigger_still_requires_a_body_part():
    """Stated as the rule, not as an example, because widening the trigger
    list without the corroborator is how this matcher would start firing on
    ordinary conversation."""
    from app import hold_speech as H

    rule = [r for r in H._INTENT_RULES if r[0] is Intent.SYMPTOM]
    assert len(rule) == 1, "the SYMPTOM rule has been duplicated or removed"
    _, trigger, corroborator, blocker = rule[0]
    assert corroborator is not None, (
        "the SYMPTOM rule no longer requires a body part -- a bare 'numb' or "
        "'sore' anywhere in an utterance would now arm a sympathy head"
    )
    assert blocker is not None, (
        "the SYMPTOM rule no longer blocks question forms"
    )
    assert trigger.search("my leg has gone numb")
    assert corroborator.search("my leg has gone numb")
