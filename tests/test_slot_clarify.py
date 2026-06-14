"""
Regression tests for the slot-selection clarify builder and the dead-air
re-ask filler stripper.

Bug B (2026-06-14): when the caller gave a non-specific affirmation ("works for
me") during a multi-slot offer, the clarify prompt listed only the first THREE
options — a caller wanting the 4th or 5th time could not pick it.  The builder
must echo EVERY offered slot.

Bug C (2026-06-14): the dead-air re-ask replays the stored question prefixed
with "Sorry — I can't quite hear you —"; when the stored question already opened
with its own filler ("No problem — …") the two stacked.  The stripper removes a
single known leading filler clause.
"""
from __future__ import annotations

from app.media_streams.connection import _build_slot_clarify, _strip_leading_filler


# ---------------------------------------------------------------------------
# Bug B — _build_slot_clarify lists every option
# ---------------------------------------------------------------------------

def test_clarify_lists_all_five_options():
    labels = [
        "nine in the morning", "ten in the morning", "eleven in the morning",
        "midday", "one in the afternoon",
    ]
    out = _build_slot_clarify(labels)
    for lbl in labels:
        assert lbl in out, f"clarify dropped {lbl!r}: {out!r}"
    # Natural grammar: commas between, 'or' before the last.
    assert ", or one in the afternoon?" in out


def test_clarify_two_options_uses_or():
    out = _build_slot_clarify(["one in the afternoon", "two in the afternoon"])
    assert out == "Which works best — one in the afternoon or two in the afternoon?"


def test_clarify_three_options_all_present():
    out = _build_slot_clarify(["nine in the morning", "midday", "one in the afternoon"])
    assert out == (
        "Which works best — nine in the morning, midday, or one in the afternoon?"
    )


def test_clarify_single_option():
    assert _build_slot_clarify(["midday"]) == "Did you mean midday?"


def test_clarify_empty_is_safe():
    assert _build_slot_clarify([]) == "Which option works best for you?"
    assert _build_slot_clarify(None) == "Which option works best for you?"


def test_clarify_ignores_blank_labels():
    # The blank is dropped, leaving two real labels → two-option grammar.
    out = _build_slot_clarify(["nine in the morning", "  ", "midday"])
    assert out == "Which works best — nine in the morning or midday?"


# ---------------------------------------------------------------------------
# Bug C — _strip_leading_filler removes one known leading filler clause
# ---------------------------------------------------------------------------

def test_strip_removes_known_filler():
    assert _strip_leading_filler(
        "No problem — do you prefer mornings or afternoons?"
    ) == "do you prefer mornings or afternoons?"


def test_strip_various_fillers():
    assert _strip_leading_filler("Sure — what time suits you?") == "what time suits you?"
    assert _strip_leading_filler("Not to worry — which day?") == "which day?"


def test_strip_leaves_real_content_untouched():
    # No leading filler → unchanged.
    q = "Is there a particular day or time that works best for you?"
    assert _strip_leading_filler(q) == q


def test_strip_does_not_truncate_dash_in_real_content():
    # A dash that is NOT preceded by a known filler must be left alone.
    q = "Thursday the 18th — what time works?"
    assert _strip_leading_filler(q) == q


def test_strip_is_idempotent():
    once = _strip_leading_filler("No problem — which day?")
    assert _strip_leading_filler(once) == once


def test_strip_handles_empty():
    assert _strip_leading_filler("") == ""
