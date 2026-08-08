"""A first name is only captured when the caller actually introduced themselves.

2026-08-08, 22:13, Theorem live call. The caller said:

    "uh mate anytime next week to be fair i'm free all week i don't have a job"

and the log recorded:

    [ms_conn v3] first-turn name extracted: Free

`i'm free` matched `i[']?m\\s+([A-Za-z][a-z]{1,20})` and "Free" was not in the
inline denylist, so it was written to soft_context["name"]. That is not a log
curiosity: rendering theorem_v3's live prompt with it produces

    CALLER CONTEXT: caller's name: Free (use ≤2× total)

so Susie was instructed to address the caller as "Free", twice.

Third instance of the same defect. T-7 (2026-08-04) was "a shockwave on its own"
-> "Own" and "is it worth its cost" -> "Cost". Each was answered by extending a
denylist, and the code's own comment conceded the approach "can only ever hold
junk somebody already thought of".

Replaying the shipped patterns over ordinary booking speech produced a false
name in 15 of 16 utterances. This file pins the rule that replaced them: an
introduction ENDS ITS CLAUSE ("I'm Quentin."), a predicate continues into one
("I'm free ALL WEEK").

The two directions are not symmetric, and the function is biased accordingly:
a missed introduction costs one extra "and your name?"; an invented one is
spoken back to the caller and can reach the booking.
"""

import pytest

from app.name_capture import first_name_from_self_introduction


# ---------------------------------------------------------------------------
# Ordinary speech must never produce a name
# ---------------------------------------------------------------------------
NOT_INTRODUCTIONS = [
    # The live call, verbatim.
    "uh mate anytime next week to be fair i'm free all week i don't have a job",
    # The rest of the replay corpus — each produced a false name before the fix.
    "i'm free all week",
    "i'm flexible",
    "i'm easy either way",
    "i'm not sure",
    "i'm good thanks",
    "i'm afraid i can't do mornings",
    "i'm calling about my knee",
    "i'm looking to book an appointment",
    "i'm happy with that",
    "i'm fine with tuesday",
    "it's urgent",
    "it's for my back",
    "it's fine",
    "this is regarding a cancellation",
    # T-7, the two that are already supposed to be fixed. Kept so a future
    # rewrite cannot quietly reopen them.
    "a shockwave on its own",
    "is it worth its cost",
    # Neighbours of the above that a boundary rule must also refuse.
    "i'm ready to book",
    "i'm keen to get in soon",
    "it's about my shoulder",
    "i'm in a lot of pain",
]


@pytest.mark.parametrize("utterance", NOT_INTRODUCTIONS)
def test_ordinary_speech_produces_no_name(utterance):
    got = first_name_from_self_introduction(utterance)
    assert got == "", (
        f"invented the name {got!r} from {utterance!r} — this reaches the "
        f"prompt as \"caller's name: {got}\" and Susie says it to the caller"
    )


# ---------------------------------------------------------------------------
# Real introductions must still be captured
# ---------------------------------------------------------------------------
# If these regress the fix has overshot: the caller gets asked their name again,
# which is the cost this whole path exists to avoid.
INTRODUCTIONS = [
    ("i'm quentin", "Quentin"),
    ("im quentin", "Quentin"),
    ("i'm quentin.", "Quentin"),
    ("it's quentin", "Quentin"),
    ("my name is quentin", "Quentin"),
    ("hello it's quentin", "Quentin"),
    ("quentin here", "Quentin"),
    ("it's quentin here", "Quentin"),
    # A name followed by a genuine continuation.
    ("i'm quentin, calling about my knee", "Quentin"),
    ("i'm quentin and i'd like to book", "Quentin"),
    ("hi it's sarah speaking", "Sarah"),
]


@pytest.mark.parametrize("utterance,expected", INTRODUCTIONS)
def test_real_introductions_are_still_captured(utterance, expected):
    assert first_name_from_self_introduction(utterance) == expected, (
        f"lost a real introduction in {utterance!r} — the caller will now be "
        f"asked their name again"
    )


# ---------------------------------------------------------------------------
# The specific mechanics, so a rewrite cannot lose them silently
# ---------------------------------------------------------------------------
def test_the_possessive_its_never_matches_the_contraction():
    """T-7's root cause: an OPTIONAL apostrophe let "its" match "it's"."""
    assert first_name_from_self_introduction("a shockwave on its own") == ""
    assert first_name_from_self_introduction("it's quentin") == "Quentin"


def test_a_predicate_continuation_is_refused_even_when_the_word_is_unknown():
    """The boundary rule must not depend on the denylist knowing the word.

    This is the whole point: "zorbing" is not in any list, and must still be
    refused because "all week" follows it.
    """
    assert first_name_from_self_introduction("i'm zorbing all week") == ""
    assert first_name_from_self_introduction("i'm zorbing") == "Zorbing"


def test_empty_and_none_are_safe():
    assert first_name_from_self_introduction("") == ""
    assert first_name_from_self_introduction(None) == ""
