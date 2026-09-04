"""Susie said "wednesday's availability properly for you." out loud.

B-140 — CAdd64c466dc13978306e5558817ce147e, northgate, 4 September 2026. What
the caller heard, in one breath:

    14  "Sorry, still with you —"
    15  "wednesday's availability properly for you."

The model wrote "Let me check Wednesday's availability properly for you."
`_INTERIM_DUPE_RE` removed "Let me check " — correctly, a hold phrase had
already been spoken about a second earlier — and what remained is that verb's
OBJECT with no verb in front of it. Not a sentence. It was synthesised and
played.

── WHY THE EXISTING GUARD MISSED IT ───────────────────────────────────────────
`_ORPHAN_LEAD` is this same defect caught one word earlier: it fires when the
remainder opens with a subordinator or a wh- complement ("While I look that
up.", "What's available for Saturday."). Six of those reached callers on 21–22
August and it was added for them. A possessive noun phrase is neither, so it
walked straight through — the list simply stopped one word short a second time.

── WHY THE TEST IS ON WHAT WAS CONSUMED ───────────────────────────────────────
Because that is where the evidence is. An opener ending in a bare transitive
verb has had its object taken away from it. An opener that closed its own
clause — "Let me check,", "Let me check that for you.", "Let's see —" — leaves
a real sentence behind, and deleting that would delete an offer. The dash is
the case that matters: `[,—-]?` does not consume a dash with a space in front
of it, so it arrives on the remainder instead, and `_SEPARATOR_LEAD` reads it
there.

── SCOPE, MEASURED ────────────────────────────────────────────────────────────
Across the 25 openers the corpus has actually produced, exactly four outputs
change, and every one turns a fragment into nothing. 9,167 stored assistant
turns hold ten that begin lower-case; nine are genuine continuation chunks and
one is this fragment.

Dropping the sentence rather than restoring the opener is `_ORPHAN_LEAD`'s
established choice, kept here so there is one rule and not two. Dead air is
covered by the C8-5 silence guarantee in llm_stream.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _strip_interim_opener


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_fragment_is_never_spoken():
    """Turn 15, verbatim from what the model wrote."""
    assert _strip_interim_opener(
        "Let me check Wednesday's availability properly for you."
    ) == ""


@pytest.mark.parametrize("text", [
    "Let me check Wednesday's availability properly for you.",
    "Let me see Wednesday's availability properly for you.",
    "Let me look at Wednesday's availability properly.",
    "Let me check the diary for Wednesday.",
    "Let me see Thursday's morning times.",
])
def test_an_opener_never_leaves_its_object_stranded(text):
    assert _strip_interim_opener(text) == ""


def test_the_sentence_after_the_fragment_survives():
    """Dropping runs to the end of the fragment's sentence and no further —
    whatever the model said next is still the caller's answer."""
    assert _strip_interim_opener(
        "Let me check Wednesday's availability. "
        "Wednesday 9th September - Number 1, ten to nine in the morning."
    ) == "Wednesday 9th September - Number 1, ten to nine in the morning."


# ---------------------------------------------------------------------------
# An opener that closed its own clause still leaves a real sentence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    # A full stop inside the opener: the remainder is a new sentence.
    ("Let me check that for you. Wednesday 9th September has three times.",
     "Wednesday 9th September has three times."),
    # A comma, consumed by the opener.
    ("Let me check, Wednesday 9th September has three times.",
     "Wednesday 9th September has three times."),
    # A dash, NOT consumed -- `[,—-]?` does not reach past the space, so it
    # arrives at the front of the remainder. Deleting these would delete an
    # offer, which is why _SEPARATOR_LEAD exists. The dash itself is cleaned
    # downstream by _LEADING_JUNK_RE in sanitise_response.
    ("Let's see - Friday the fourteenth works.",
     "- Friday the fourteenth works."),
    ("Let's see — Friday the fourteenth works.",
     "— Friday the fourteenth works."),
    ("Let me check - Wednesday 9th September has three times.",
     "- Wednesday 9th September has three times."),
])
def test_a_closed_opener_leaves_the_sentence_alone(text, expected):
    assert _strip_interim_opener(text) == expected


# ---------------------------------------------------------------------------
# Every other opener is untouched -- the ones that carry their own object, and
# the ones that are complete sentences already
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Let me pull that up for you. Here we go.", "Here we go."),
    ("Let me find that for you. Here we go.", "Here we go."),
    ("Let me look that up for you. Wednesday has three times.",
     "Wednesday has three times."),
    ("Let me get that booked in for you. That's done.", "That's done."),
    ("One moment. Wednesday has three times.", "Wednesday has three times."),
    ("Just a moment... Wednesday has three times.",
     "Wednesday has three times."),
    ("Give me a second. Wednesday has three times.",
     "Wednesday has three times."),
    ("Right with you. Wednesday has three times.",
     "Wednesday has three times."),
    ("Just getting that for you. Wednesday has three times.",
     "Wednesday has three times."),
    # No opener at all: returned whole, untouched.
    ("Wednesday 9th September - Number 1, ten to nine in the morning.",
     "Wednesday 9th September - Number 1, ten to nine in the morning."),
])
def test_the_openers_that_were_already_right_are_unchanged(text, expected):
    assert _strip_interim_opener(text) == expected


# ---------------------------------------------------------------------------
# The guard this one was modelled on still works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Bear with me while I look that up.",
    "Let me check what's available for Saturday.",
    "Let me see what we have.",
])
def test_orphan_lead_is_untouched(text):
    assert _strip_interim_opener(text) == ""


@pytest.mark.parametrize("text", ["", "Let me check.", "Let me check"])
def test_a_bare_opener_still_yields_nothing_to_say(text):
    assert _strip_interim_opener(text) == ""
