"""
Unit tests for CONFIRM_PHONE semantic YES broadening.

Covers the bug where natural affirmations like "that's the best number"
were treated as ambiguous instead of YES, causing unnecessary re-asks
during live calls.

Fix:
  Added _SEMANTIC_YES_PHRASES to both CONFIRM_PHONE handlers.
  Ambiguous fallback now uses a tight re-ask instead of replaying the
  full phone readback.
  Added "not the best number" to _HG_NO_PHRASES.
"""
from __future__ import annotations
import re


# ---------------------------------------------------------------------------
# Reproduce the classification logic from both handlers
# ---------------------------------------------------------------------------

_HG_YES = (
    "yes", "yeah", "yep", "yup", "yeh", "ya",
    "that's right", "thats right",
    "that's correct", "thats correct",
    "that's fine", "thats fine", "that's ok", "thats ok",
    "use this number", "yes use this number",
    "use my number", "yes use my number",
    "same number", "use my current number",
)

_HG_NO_PHRASES = (
    "nope", "nah",
    "no use a different number", "different number",
    "use a different number", "another number",
    "no different number", "give you another number",
    "no i'll give you another",
    "wrong number", "not the right number",
    "that's not the right number", "that's the wrong number",
    "thats not the right number", "thats the wrong number",
    "not the best number",
)

_SEMANTIC_YES_PHRASES = (
    "that's the best number", "thats the best number",
    "that's the right number", "thats the right number",
    "this is the best number", "this is the right number",
    "that's the one", "thats the one",
    "you can use this one", "you can use this number",
    "this number is fine", "that number is fine",
    "best number to reach me", "best number for me",
    "that one is fine", "this one is fine",
)

_SEMANTIC_YES_NEGATION = ("not", "different", "another", "wrong")


def _classify(text: str):
    """Return ('yes'|'no'|'ambiguous') using primary-handler logic."""
    hg_yes = any(p in text for p in _HG_YES)
    hg_no = bool(re.search(r'\bno\b', text)) or any(p in text for p in _HG_NO_PHRASES)
    semantic_yes = (
        any(p in text for p in _SEMANTIC_YES_PHRASES)
        and not re.search(r'\bno\b', text)
        and not any(n in text for n in _SEMANTIC_YES_NEGATION)
    )
    if (hg_yes or semantic_yes) and not hg_no:
        return "yes"
    elif hg_no and not hg_yes and not semantic_yes:
        return "no"
    else:
        return "ambiguous"


# ---------------------------------------------------------------------------
# A. Explicit YES phrases still work
# ---------------------------------------------------------------------------

def test_explicit_yes():
    assert _classify("yes") == "yes"

def test_explicit_yeah():
    assert _classify("yeah that's fine") == "yes"

def test_explicit_that_is_correct():
    assert _classify("that's correct") == "yes"

def test_explicit_use_this_number():
    assert _classify("use this number") == "yes"

def test_explicit_same_number():
    assert _classify("same number") == "yes"


# ---------------------------------------------------------------------------
# B. Semantic YES phrases now resolve to YES
# ---------------------------------------------------------------------------

def test_semantic_thats_the_best_number():
    assert _classify("that's the best number") == "yes"

def test_semantic_thats_the_right_number():
    assert _classify("that's the right number") == "yes"

def test_semantic_this_is_the_best_number():
    assert _classify("this is the best number") == "yes"

def test_semantic_you_can_use_this_one():
    assert _classify("you can use this one") == "yes"

def test_semantic_you_can_use_this_number():
    assert _classify("you can use this number") == "yes"

def test_semantic_this_number_is_fine():
    assert _classify("this number is fine") == "yes"

def test_semantic_that_number_is_fine():
    assert _classify("that number is fine") == "yes"

def test_semantic_best_number_to_reach_me():
    assert _classify("best number to reach me") == "yes"

def test_semantic_that_one_is_fine():
    assert _classify("that one is fine") == "yes"

def test_semantic_this_one_is_fine():
    assert _classify("this one is fine") == "yes"

def test_semantic_thats_the_one():
    assert _classify("thats the one") == "yes"


# ---------------------------------------------------------------------------
# C. NO phrases still resolve to NO
# ---------------------------------------------------------------------------

def test_explicit_no():
    assert _classify("no") == "no"

def test_explicit_nope():
    assert _classify("nope") == "no"

def test_explicit_wrong_number():
    assert _classify("wrong number") == "no"

def test_explicit_not_the_right_number():
    assert _classify("not the right number") == "no"

def test_explicit_different_number():
    assert _classify("different number") == "no"

def test_explicit_not_the_best_number():
    """Newly added: 'not the best number' must be NO."""
    assert _classify("not the best number") == "no"

def test_explicit_another_number():
    assert _classify("another number") == "no"


# ---------------------------------------------------------------------------
# D. Negated semantic phrases do NOT fire as YES
# ---------------------------------------------------------------------------

def test_negated_no_thats_not_the_right_number():
    """'no that's not the right number' — has 'no' AND 'not' → NO."""
    assert _classify("no that's not the right number") == "no"

def test_negated_not_the_best_number_phrase():
    """'not the best number' — has 'not' negation → should not be semantic YES."""
    result = _classify("not the best number")
    assert result == "no"  # Caught by _HG_NO_PHRASES

def test_negated_different_in_semantic():
    """A phrase that contains 'different' negation word → not semantic YES."""
    # Manufacture a phrase unlikely to be in _HG_NO_PHRASES but contains 'different'
    assert _classify("that's a different number") == "no"  # "different number" hits _HG_NO_PHRASES


# ---------------------------------------------------------------------------
# E. Ambiguous utterances remain ambiguous
# ---------------------------------------------------------------------------

def test_ambiguous_hmm():
    assert _classify("hmm") == "ambiguous"

def test_ambiguous_ok():
    assert _classify("ok") == "ambiguous"

def test_ambiguous_right():
    """Bare 'right' was intentionally removed from _HG_YES to prevent false positives."""
    assert _classify("right") == "ambiguous"

def test_ambiguous_i_think_so():
    assert _classify("i think so") == "ambiguous"
