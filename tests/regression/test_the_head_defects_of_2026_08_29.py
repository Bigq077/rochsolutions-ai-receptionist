"""
Three head defects from the demo call of 2026-08-29, CA7454c983a10dd3db7caee7dba3b06238.

The call was 297 seconds, fifty turns, and it booked. Eight heads fired with the
right wording and the right rotation. These are what it also produced, and each
was sized against the 737-call obs corpus before being acted on rather than
after — the numbers in each section are from
`scripts/replay_situational_heads` and the consultation-promise split beside it.
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    WorkKind,
    _NAMES_THE_WORK,
    classify_intent,
    decide_hold,
    render_intent_head,
    subject_for,
)


def _head(utterance: str, prev: str = "") -> str:
    hits = classify_intent(utterance, prev)
    if not hits:
        return ""
    return render_intent_head(hits[0], subject=subject_for(utterance))


# ── 1. Two hold phrases, 0.8 seconds apart ──────────────────────────────────
#
#   23:02:25.558  "Got it. Let me check what's available for you as soon as
#                  possible"                          <- the model's own
#   23:02:26.370  "Okay, one sec —"                   <- ours
#
# `keep_pre_slot_speech` preserves the model's pre-tool sentence, and the
# tool-time producer asks `decide_hold` with
# head_already_spoken=session["_hold_head_spoken"] — which nothing was setting
# there. B-121, the detector's own defect, live on the demo line.
#
# The latch is now set when the preserved line IS a hold phrase, and the
# discriminator is the module's own `_NAMES_THE_WORK`, because that same branch
# also preserves empathy, and latching on empathy would suppress a hold phrase
# the caller genuinely needs.

def test_the_model_own_hold_sentence_is_recognised_as_one():
    """The exact sentence from the call."""
    assert _NAMES_THE_WORK.search(
        "Got it. Let me check what's available for you as soon as possible"
    )


@pytest.mark.parametrize("empathy", [
    "I'm sorry to hear that — shoulder pain can be really limiting.",
    "That's reassuring.",
    "Right — and before I check availability, I just want to make sure.",
])
def test_empathy_is_not_a_hold_phrase(empathy):
    """The same branch preserves these. Latching on one would take away a hold
    phrase the caller needs, so the discriminator has to tell them apart.

    The third is deliberately included and deliberately DOES name the work — it
    says "before I check availability", which is a hold phrase with empathy in
    front of it. Pinned as claiming work, so the latch fires on it too.
    """
    claims = bool(_NAMES_THE_WORK.search(empathy))
    assert claims is ("check" in empathy or "look" in empathy)


def test_the_latch_is_what_makes_the_producer_stand_down():
    """The coupling the fix relies on. Setting the latch is one line; this is
    the behaviour that line buys, and it must not be weakened."""
    spoken = decide_hold(
        legacy=False, session={}, kind=WorkKind.DIARY_READ,
        head_already_spoken=True, practitioner="Priya", heads_used=0,
    )
    assert spoken.speak is False
    assert spoken.head == ""

    unlatched = decide_hold(
        legacy=False, session={}, kind=WorkKind.DIARY_READ,
        head_already_spoken=False, practitioner="Priya", heads_used=0,
    )
    assert unlatched.speak is True, (
        "the latch now suppresses every hold phrase, not just the second one"
    )


# ── 2. A head promised a session length the clinic does not sell ────────────
#
# Caller: "uh quick question first do you do 90-minute sessions"
# Head:   "Let me see where a ninety-minute session fits —"
# Reply:  "the sessions are either thirty minutes … or sixty"
#
# SESSION_LENGTH fired on the bare duration pattern with no corroborator and no
# blocker, so a question about whether a thing EXISTS read as a request to
# schedule it. Corpus: ten of its twenty heads were followed by a question
# rather than by times — every one of them a caller stating a duration, which
# supplies a parameter rather than asking anyone to open a diary.

def test_a_capability_question_gets_no_head():
    """The live turn. A head must never assert that something exists."""
    assert _head("uh quick question first do you do 90-minute sessions") == ""


@pytest.mark.parametrize("stated", [
    "60 minute session please",
    "i think a 60 minute please",
    "uh the 60-minute session",
    "to book that 60 minute sports massage please",
])
def test_stating_a_duration_is_not_asking_to_look(stated):
    """All four are from the corpus, and all four were answered with "do you
    have a preference for when you'd like to come in?" — the diary was never
    opened. Silence here is the pre-arbiter behaviour, which is the correct
    failure direction for a rule that was confident and wrong."""
    assert _head(stated) == ""


def test_asking_where_a_length_fits_still_gets_its_head():
    """The fix must not delete the intent — only narrow it to the case its
    wording actually describes."""
    assert _head("when could i get a 60 minute") == (
        "Let me see where a sixty-minute session fits —"
    )


def test_a_real_availability_question_is_not_blocked():
    """"do you HAVE any 90 minute slots" is an availability question, not a
    capability one, and it must keep a head. The blocker names `do you do` and
    `do you offer` for exactly this reason."""
    assert _head("do you have any 90 minute slots") != ""


def test_a_named_day_still_wins_over_the_duration():
    """SESSION_LENGTH is last in the rules so a better-fitting intent takes
    hits[0]: this caller wants their DAY named back, not their duration."""
    assert _head("can i get a 60 minute on thursday") == (
        "Let me see what Thursday looks like —"
    )


# ── 3. "uh" made a request look like a bare answer ──────────────────────────
#
# `classify_intent` silenced anything matching _BARE_ANSWER in four words or
# fewer. "uh what about mornings" is four words BECAUSE of the "uh", and it
# matched BECAUSE of the "uh". Two turns on the live call got silence where the
# caller had asked for something. Corpus: 28 turns newly armed, every one a
# genuine request.

@pytest.mark.parametrize("utterance, expected", [
    ("uh what about mornings", "Let me see what I've got in the morning —"),
    ("um afternoons",          "Let me see what I've got in the afternoon —"),
    ("um next tuesday",        "Let me see what Tuesday looks like —"),
    ("um anytime next week",   "Let me look at next week for you —"),
    ("uh cancel it",           "No problem at all —"),
])
def test_a_disfluency_does_not_silence_a_request(utterance, expected):
    assert _head(utterance) == expected


@pytest.mark.parametrize("answer", [
    "yeah",
    "uh yeah go for it",
    "uh no not really nothing like that",
    "um",
    "no",
    "not really",
])
def test_an_answer_is_still_an_answer(answer):
    """The guard's real job. Stripping the disfluency must not turn a reply
    into a request — there is nothing for a head to stand in front of."""
    assert _head(answer) == ""


def test_only_true_disfluencies_are_stripped():
    """"oh", "yeah", "no", "well" and "so" stay in _BARE_ANSWER's own list,
    because each of them CAN be the whole answer. "um" and "uh" never can, and
    that asymmetry is the whole rule."""
    from app.hold_speech import _LEADING_DISFLUENCY

    assert _LEADING_DISFLUENCY.sub("", "uh what about mornings") == "what about mornings"
    assert _LEADING_DISFLUENCY.sub("", "um, next tuesday") == "next tuesday"
    for kept in ("oh no", "yeah fine", "well actually", "so tuesday then"):
        assert _LEADING_DISFLUENCY.sub("", kept) == kept


# ── What none of the three may break ────────────────────────────────────────

def test_the_screen_still_silences_everything():
    """The worst moment in a call to guess. Unchanged by all three fixes, and
    checked here because two of them touch the same function's early returns."""
    assert classify_intent("just book me in for tuesday", "", screen_pending=True) == []
    assert classify_intent(
        "uh what about mornings",
        "do you have any numbness around the saddle area?",
    ) == []


def test_the_call_that_worked_still_works():
    """Eight heads fired correctly on the same call. They are the control
    group: if a fix for the four defects moved any of these, it went too far."""
    assert _head("um how much is a sports massage") == "In terms of pricing —"
    assert _head("and do you take bupa") == "In regards to insurance —"
    assert _head("uh what exactly are your opening hours") == (
        "In terms of our opening hours —"
    )
    assert _head("and uh is there parking at your clinic") == "In regards to parking —"
    assert _head("um have you got anything on saturday") == (
        "Let me see what Saturday looks like —"
    )
    assert _head("and what's available the week after") == (
        "Let me look at the week after for you —"
    )
    assert _head("okay uh well i'd like to know what's the earliest appointment you've got") == (
        "Let me find the soonest I've got —"
    )
