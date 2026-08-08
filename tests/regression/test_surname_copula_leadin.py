"""
Regression (CAb297555c, Vital Edge, 2026-08-08 09:10:55): the caller said
"yeah that'll be quentin rook" and the record was written as 'Quentin'.

The surname was in the transcript, cleanly transcribed, and dropped. It was not
an STT failure and not a model failure — `extract_surname`'s pattern 3 requires
every token before the first name to be a recognised lead filler, and neither
"that'll" nor "be" was one, so the whole utterance was rejected.

This is the THIRD patch to the same gap and the second phrasing of the same
sentence:

    2026-07-11  "that would be Quentin Rock"  -> added "would be", "that's",
                                                 "that is" to pattern 2
    2026-08-08  "that'll be quentin rook"     -> broke again

Adding the next literal would buy exactly one more phrasing and fail on
"it'll be". So the fix adds the CLASS — demonstrative + copula in every
contraction English offers — to NAME_LEAD_FILLERS, where pattern 3's
all-leading-tokens rule already generalises correctly.

Why this matters more than a mangled log line: the capture contract at the top
of name_capture.py is that the surname is NEVER read back to the caller. So a
missing or wrong surname is never caught by the person best placed to catch it
— it flows silently into the booking, the confirmation SMS and the Sheets row.
On this call the SMS and the summary row both went out as `name=Quentin`.
"""
from __future__ import annotations

import pytest

from app.name_capture import NAME_LEAD_FILLERS, extract_surname


# ── 1. The live failure and its family ──────────────────────────────────────

@pytest.mark.parametrize(
    "utterance,expected",
    [
        # The exact utterance from CAb297555c.
        ("yeah that'll be quentin rook", "Rook"),
        # The same sentence, uncontracted — STT emits either.
        ("yeah that will be quentin rook", "Rook"),
        # The 2026-07-11 phrasing, which must not regress.
        ("that would be quentin rock", "Rock"),
        # The sibling pronoun. This is the one a literal-by-literal fix would
        # still have missed.
        ("it'll be quentin rock", "Rock"),
        ("it would be quentin rock", "Rock"),
        # Already worked; pinned so the widening did not disturb them.
        ("that's quentin rock", "Rock"),
        ("um yeah quentin rock", "Rock"),
        ("quentin rook", "Rook"),
    ],
)
def test_the_copula_leadin_class_yields_the_surname(utterance, expected):
    assert extract_surname(utterance, "Quentin") == expected


def test_particles_still_chain_through_the_new_leadin():
    """The widening must not shortcut the particle walk — "de silva" is one
    surname, not "De"."""
    assert extract_surname("well that would be quentin de silva", "Quentin") == "De Silva"


# ── 2. The safety property the widening must not break ──────────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        "no that's not quentin rook",
        "not quentin rook",
        "no quentin wrong",
        "no thats quentin but wrong",
    ],
)
def test_a_correction_is_still_refused(utterance):
    """
    The documented property of pattern 3 is that EVERY token before the first
    name must be a filler, so a single non-filler word — a negation, a
    correction — rejects the whole utterance.

    That property is what makes a permissive filler list safe. The capture
    contract prefers "" over a guess precisely because a wrong surname is never
    read back and therefore never caught.
    """
    assert extract_surname(utterance, "Quentin") == ""


@pytest.mark.parametrize("word", ["no", "not", "nope", "wrong", "isn't", "wasn't"])
def test_negations_are_absent_from_the_filler_set(word):
    """
    Stated as a property rather than left to a reviewer's eye. Adding any of
    these to NAME_LEAD_FILLERS would silently convert every correction into a
    name capture — and the caller would never hear the result to object to it.
    """
    assert word not in NAME_LEAD_FILLERS


# ── 3. The class, not the literal ───────────────────────────────────────────

@pytest.mark.parametrize(
    "token",
    ["that", "that's", "that'll", "this", "it's", "it'll", "will", "would", "be"],
)
def test_the_whole_copula_class_is_present(token):
    """
    The point of the fix. Two previous rounds added one phrasing each and were
    broken by the next; this asserts the set is closed over the class so the
    fourth variant cannot reopen it.
    """
    assert token in NAME_LEAD_FILLERS
