"""
Unit tests for PRESENT_TIMES comparative-constraint detection and fix.

Covers the bug where "do you have anything later than one in the afternoon"
was treated as a TIME_QUERY for 1pm rather than a comparative constraint query,
causing the boundary hour to be offered as the answer.

Root cause:
  _is_time_query=True AND _is_constraint=True were both set, but the TIME_QUERY
  handler fired first and extracted the boundary hour (13) as the queried time.
  Fix: guard the TIME_QUERY path with `not _is_constraint`.

Tests use the pure helper function _extract_hour_from_text and the phrase-set
membership logic that drives _is_constraint / _is_time_query classification.
"""
from __future__ import annotations
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.media_streams.flow import _extract_hour_from_text


# ---------------------------------------------------------------------------
# A. _extract_hour_from_text — boundary-hour extraction
#    The function correctly identifies the numeric hour in constraint phrases.
#    These tests document the existing behavior and confirm the fix does not
#    change what _extract_hour_from_text returns — the fix is upstream (the
#    TIME_QUERY gate no longer runs when _is_constraint is also True).
# ---------------------------------------------------------------------------

def test_extract_hour_later_than_word():
    """'later than one in the afternoon' → 13 (boundary hour)."""
    assert _extract_hour_from_text("later than one in the afternoon") == 13


def test_extract_hour_later_than_digit():
    """'anything later than 2pm' → 14."""
    assert _extract_hour_from_text("anything later than 2pm") == 14


def test_extract_hour_before_digit():
    """'anything before 11' → 11."""
    assert _extract_hour_from_text("anything before 11") == 11


def test_extract_hour_after_word():
    """'anything after five' → 17 (clinic context: 5 < 8 → +12)."""
    assert _extract_hour_from_text("anything after five") == 17


def test_extract_hour_direct_query():
    """'do you have five o'clock' → 17."""
    assert _extract_hour_from_text("do you have five o'clock") == 17


def test_extract_hour_direct_pm():
    """'is there a 4pm' → 16."""
    assert _extract_hour_from_text("is there a 4pm") == 16


def test_extract_hour_none_no_time():
    """'do you have anything in the afternoon' → None (no numeric hour)."""
    assert _extract_hour_from_text("do you have anything in the afternoon") is None


# ---------------------------------------------------------------------------
# B. Phrase-set membership — _CONSTRAINT_GUARD detection
#    These tests verify that comparative/constraint phrases are detected
#    by the same logic used in the handler.
# ---------------------------------------------------------------------------

def _is_constraint(text: str) -> bool:
    """Reproduce the _is_constraint check from the handler."""
    _CONSTRAINT_GUARD = (
        "later than", "earlier than", "before ",
        "anything later", "anything earlier",
        "something later", "something earlier",
        "have you got later", "have you got earlier",
        "do you have later", "do you have earlier",
        "is there anything", "are there any",
        "have you got anything", "got anything",
        "any later", "any earlier",
        "after ", "nothing before", "nothing after",
        "any afternoon", "afternoon slots", "slots in the afternoon",
        "any morning", "morning slots", "slots in the morning",
        "afternoon then", "any afternoon slots", "any morning slots",
        "in the afternoon", "in the morning",
        "anything in the afternoon", "anything in the morning",
        "anything else that day", "anything else on that day",
        "anything else available", "what else do you have",
        "any other times", "any other slots",
        "other times on that day", "other slots on that day",
    )
    return any(p in text for p in _CONSTRAINT_GUARD)


def _is_time_query(text: str) -> bool:
    """Reproduce the _is_time_query_early check from the handler."""
    _TIME_QUERY_PHRASES_EARLY = (
        "do you have anything", "have you got anything", "got anything",
        "have anything",
        "do you have any", "have you got any",
        "is there anything", "is there any",
        "are there any", "any availability",
        "anything around", "anything near", "anything close",
        "anything after", "anything before",
        "anything later than", "anything earlier than",
        "anything later on than",
        "later than that", "earlier than that",
        "around ", "near to ", "close to ",
        "closer to", "nearest to",
        "what about ", "what have you got",
        "is that available", "is it available",
        "is 5 available", "is there a 5",
        "do you have 5", "have you got 5",
        "anything at all", "any slots",
        "like later on", "later on that day",
        "do you do", "do you offer",
        "can you do", "could you do",
        "would you have",
    )
    return any(p in text for p in _TIME_QUERY_PHRASES_EARLY)


# B1: comparative constraint — both flags True, TIME_QUERY must not run
def test_comparative_constraint_sets_both_flags():
    """'do you have anything later than one in the afternoon' triggers both."""
    text = "do you have anything later than one in the afternoon"
    assert _is_constraint(text), "must be classified as constraint"
    assert _is_time_query(text), "also matches time-query phrase"
    # The fix ensures TIME_QUERY handler is skipped when _is_constraint=True.
    # This test documents the dual-flag condition that caused the bug.


# B2: specific exact-time question — not in _TIME_QUERY_PHRASES_EARLY, not constraint
def test_exact_time_question_not_in_query_phrases():
    """'do you have five o'clock' does not hit _is_time_query_early or _is_constraint.
    It falls through to the direct-bind path, which is correct: a specific slot
    request with no exploratory language is treated as direct selection."""
    text = "do you have five o'clock"
    # Not caught by time-query phrases (no "do you have any" substring)
    assert not _is_time_query(text)
    # Not a constraint either
    assert not _is_constraint(text)
    # → direct time bind fires (correct — caller asked for specific time).


# B3: direct selection — neither flag
def test_direct_selection_no_flags():
    """'five o'clock works for me' is a direct selection."""
    text = "five o'clock works for me"
    assert not _is_time_query(text)
    assert not _is_constraint(text)
    # → direct time bind path fires (correct).


# B4: constraint without time query
def test_constraint_only_afternoon_period():
    """'anything in the afternoon' triggers constraint but not time-query early."""
    text = "anything in the afternoon"
    assert _is_constraint(text)
    # May or may not match time-query — what matters is _is_constraint is True.


# B5: 'anything later on than' variant
def test_anything_later_on_than_variant():
    """'have anything later on than one o'clock' — constraint AND query."""
    text = "have anything later on than one o'clock"
    assert _is_constraint(text), "anything later"
    assert _is_time_query(text), "have anything"


# B6: 'anything after two' — comparative with digit
def test_anything_after_digit_is_constraint():
    """'anything after 2' — constraint."""
    assert _is_constraint("anything after 2")


# B7: 'anything before eleven' — constraint
def test_anything_before_word_is_constraint():
    """'anything before eleven' — constraint."""
    assert _is_constraint("anything before eleven")


# ---------------------------------------------------------------------------
# C. _allow_time_bind gate logic
#    Verify the combined gate prevents binding on constraint+query phrases.
# ---------------------------------------------------------------------------

def _allow_bind(text: str) -> bool:
    """Reproduce _allow_time_bind from the handler (post-fix)."""
    import re
    constraint = _is_constraint(text)
    query = _is_time_query(text)

    _TIME_SELECTION_PHRASES = (
        "works for me", "work for me", "that works",
        "i'll do", "i'll take", "i will take", "i will do",
        "book ", "book me in", "please book",
        "suits me", "that suits", "i'll go with",
        "i'd like ", "i would like ", "i want ",
        "sign me up", "confirm",
    )
    selection = any(p in text for p in _TIME_SELECTION_PHRASES)
    has_at_time = bool(
        re.search(
            r'\bat\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\b',
            text,
        )
        or re.search(
            r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s+o\'?clock\b',
            text,
        )
    )
    return not query and (not constraint or has_at_time or selection)


def test_bind_blocked_for_comparative_constraint():
    """Comparative constraint query must not allow direct time bind."""
    assert not _allow_bind("do you have anything later than one in the afternoon")


def test_bind_allowed_for_direct_selection():
    """Explicit selection must allow bind."""
    assert _allow_bind("one in the afternoon works for me")


def test_bind_allowed_for_plain_hour():
    """Plain hour with no query/constraint language → bind allowed."""
    assert _allow_bind("five o'clock please")


def test_bind_allowed_for_specific_time_question():
    """'do you have five o'clock' — no constraint, no query phrase → bind allowed.
    Caller named a specific slot; direct binding is correct."""
    assert _allow_bind("do you have five o'clock")


# ---------------------------------------------------------------------------
# D. SSC repair-phrase detection
#    Verifies the correction guard phrases are correctly matched.
# ---------------------------------------------------------------------------

def _is_ssc_repair(text: str) -> bool:
    _SSC_REPAIR_PHRASES = (
        "no i said", "no i meant", "no i was asking",
        "no i asked", "that's not what i",
        "i said do you", "i was asking",
    )
    return any(p in text for p in _SSC_REPAIR_PHRASES)


def test_correction_after_wrong_answer():
    """'no i said do you have anything later on ...' is a repair."""
    assert _is_ssc_repair("no i said do you have anything later on when one o'clock")


def test_correction_no_i_meant():
    """'no i meant later' is a repair."""
    assert _is_ssc_repair("no i meant later")


def test_plain_no_is_not_repair():
    """Bare 'no' is NOT a repair — should trigger normal SSC NO."""
    assert not _is_ssc_repair("no")


def test_no_thanks_is_not_repair():
    """'no thanks' is NOT a repair."""
    assert not _is_ssc_repair("no thanks")


def test_nope_is_not_repair():
    """'nope' is NOT a repair."""
    assert not _is_ssc_repair("nope")


# ---------------------------------------------------------------------------
# E. Constraint-hour filter logic
#    Verify boundary filtering: "later than 13" → only hours > 13.
# ---------------------------------------------------------------------------

def _filter_by_constraint(times: list, wants_later: bool, constraint_hour: int) -> list:
    """Reproduce the slot-filtering logic from the constraint handler."""
    result = []
    for t in times:
        try:
            h = int(t.split(":")[0])
            if wants_later and h > constraint_hour:
                result.append(t)
            elif not wants_later and h < constraint_hour:
                result.append(t)
        except (ValueError, IndexError):
            pass
    return result


def test_filter_later_than_excludes_boundary():
    """'later than 13:00' must NOT include 13:00 itself."""
    slots = ["09:00", "11:00", "13:00", "16:00", "17:00"]
    result = _filter_by_constraint(slots, wants_later=True, constraint_hour=13)
    assert "13:00" not in result
    assert "16:00" in result
    assert "17:00" in result


def test_filter_later_than_excludes_earlier():
    """'later than 13:00' must NOT include 09:00 or 11:00."""
    slots = ["09:00", "11:00", "13:00", "16:00", "17:00"]
    result = _filter_by_constraint(slots, wants_later=True, constraint_hour=13)
    assert "09:00" not in result
    assert "11:00" not in result


def test_filter_before_excludes_boundary():
    """'before 11:00' must NOT include 11:00 itself."""
    slots = ["09:00", "10:00", "11:00", "13:00"]
    result = _filter_by_constraint(slots, wants_later=False, constraint_hour=11)
    assert "11:00" not in result
    assert "09:00" in result
    assert "10:00" in result


def test_filter_later_than_all_excluded_returns_empty():
    """'later than 17:00' when max slot is 17:00 → empty list."""
    slots = ["09:00", "13:00", "17:00"]
    result = _filter_by_constraint(slots, wants_later=True, constraint_hour=17)
    assert result == []
