# tests/regression/test_reask_variants.py
"""Unit cover for the watchdog re-ask variety helper.

Defect being addressed: on theorem_v3 the FlowEngine state stays "GREETING" for
essentially the whole call (connection.py:3231), so most no-input re-asks land
in the GREETING fallback at connection.py:3300, which replays `last_question`
verbatim.  A caller mis-heard twice hears the same sentence back twice, which
reads as broken software rather than as a receptionist asking again.

`app/media_streams/reask_variants.py` supplies the alternative phrasings and the
already-said suppression.  These tests pin the pure helper; the engine wiring
carries its own behavioural tests.
"""

import pytest

from app.media_streams.reask_variants import (
    ARCHETYPES,
    classify_question,
    normalize_phrase,
    variant_for,
)


# ── normalize_phrase ──────────────────────────────────────────────────────────

def test_normalize_ignores_punctuation_and_case_and_spacing():
    """Two phrasings that differ only cosmetically must compare equal.

    The already-said set keys on this, so if punctuation defeated it the guard
    would let a near-identical repeat through — the exact thing it exists to
    stop.
    """
    a = "Sorry, I didn't catch that. Which day works?"
    b = "sorry i didnt catch that   which day works"
    assert normalize_phrase(a) == normalize_phrase(b)


def test_normalize_handles_empty():
    assert normalize_phrase("") == ""
    assert normalize_phrase(None) == ""


def test_normalize_keeps_distinct_phrases_distinct():
    assert normalize_phrase("morning or afternoon?") != normalize_phrase(
        "which of those would you like?"
    )


# ── classify_question ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "question,expected",
    [
        # Real prompts taken from the live traces / prompt templates.
        ("Did you have a particular day or time in mind?", "timing"),
        ("Would you prefer a morning or an afternoon?", "timing"),
        ("Which of those would you like?", "slot"),
        ("Which option works best?", "slot"),
        ("Could I get your first name?", "name"),
        ("And what's your surname?", "name"),
        ("What's the best number to reach you on?", "phone"),
        ("Could you enter it on your keypad?", "phone"),
        ("What's been going on with your back?", "reason"),
        ("Shall I go ahead and book that in?", "confirm"),
    ],
)
def test_classifies_real_prompts(question, expected):
    assert classify_question(question) == expected


def test_slot_beats_timing_when_both_present():
    """A slot choice that mentions a time is a slot question, not a timing one.

    "which of those" is the operative phrase — the caller is picking from a read
    list, so narrowing to "morning or afternoon" would be a step backwards.
    """
    assert classify_question("Which of those works — the ten or the two?") == "slot"


def test_unknown_question_is_other_not_an_error():
    assert classify_question("Bear with me a moment.") == "other"


def test_empty_question_is_other():
    assert classify_question("") == "other"
    assert classify_question(None) == "other"


# ── variant_for ───────────────────────────────────────────────────────────────

def test_rung_one_has_no_variant():
    """The first re-ask replaying the question is correct — the caller may
    simply not have heard it.  Variety is for the second ask onward."""
    for arch in ARCHETYPES:
        assert variant_for(arch, 1) is None


def test_rung_three_is_not_this_helpers_job():
    """Rung 3 is default-forward, a behavioural decision left to the caller."""
    for arch in ARCHETYPES:
        assert variant_for(arch, 3) is None


def test_every_archetype_has_a_rung_two_variant():
    for arch in ARCHETYPES:
        assert variant_for(arch, 2), f"{arch} has no rung-2 phrasing"


def test_unknown_archetype_falls_back_to_other():
    assert variant_for("no-such-archetype", 2) == variant_for("other", 2)


def test_already_said_variant_is_suppressed():
    """The core invariant: never hand back a sentence already spoken this turn."""
    phrase = variant_for("timing", 2)
    assert phrase is not None
    assert variant_for("timing", 2, already_said=[phrase]) is None


def test_suppression_survives_punctuation_differences():
    """A repeat that differs only in punctuation must still be suppressed."""
    phrase = variant_for("slot", 2)
    mangled = phrase.replace("—", "").replace(",", "").upper()
    assert variant_for("slot", 2, already_said=[mangled]) is None


def test_unrelated_already_said_does_not_suppress():
    assert variant_for("timing", 2, already_said=["something else entirely"])


def test_no_variant_is_a_question_replay():
    """Variants must narrow the ask, never restate the original question.

    A variant containing the original prompt text would reintroduce the parrot
    effect through the back door.
    """
    original = "Did you have a particular day or time in mind?"
    for arch in ARCHETYPES:
        v = variant_for(arch, 2)
        assert original.lower() not in v.lower()
