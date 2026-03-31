# tests/test_tone_detector.py
"""
Tests for app/tone_detector.py

Coverage:
  1.  Two utterances of 2 words each → "brief"
  2.  Two utterances of 15 words each → "warm"
  3.  Two utterances of 6 words each → "standard"
  4.  One utterance only → "standard" (not yet locked)
  5.  Third utterance after lock → tone unchanged
  6.  get_instruction returns correct string for each tone
  7.  to_dict / from_dict round-trip preserves state
  8.  get_tone_instruction_from_session — live instance path
  9.  get_tone_instruction_from_session — rebuild from _tone_state path
  10. get_tone_instruction_from_session — never raises on bad input
"""
from __future__ import annotations

import pytest
from app.tone_detector import ToneDetector, get_tone_instruction_from_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _words(n: int) -> str:
    """Return a string of exactly n space-separated words."""
    return " ".join("word" for _ in range(n))


# ---------------------------------------------------------------------------
# 1. Two utterances of 2 words → "brief"
# ---------------------------------------------------------------------------

def test_two_short_utterances_give_brief():
    td = ToneDetector()
    td.record_utterance(_words(2))
    td.record_utterance(_words(2))
    assert td.get_tone() == "brief"


# ---------------------------------------------------------------------------
# 2. Two utterances of 15 words → "warm"
# ---------------------------------------------------------------------------

def test_two_long_utterances_give_warm():
    td = ToneDetector()
    td.record_utterance(_words(15))
    td.record_utterance(_words(15))
    assert td.get_tone() == "warm"


# ---------------------------------------------------------------------------
# 3. Two utterances of 6 words → "standard"
# ---------------------------------------------------------------------------

def test_two_medium_utterances_give_standard():
    td = ToneDetector()
    td.record_utterance(_words(6))
    td.record_utterance(_words(6))
    assert td.get_tone() == "standard"


# ---------------------------------------------------------------------------
# 4. One utterance only → "standard" (not yet locked)
# ---------------------------------------------------------------------------

def test_single_utterance_is_standard():
    td = ToneDetector()
    td.record_utterance(_words(2))     # would be brief, but not enough turns yet
    assert td.get_tone() == "standard"


def test_single_utterance_not_locked():
    td = ToneDetector()
    td.record_utterance(_words(2))
    assert td._locked is False


# ---------------------------------------------------------------------------
# 5. Third utterance after lock → tone unchanged
# ---------------------------------------------------------------------------

def test_third_utterance_does_not_change_tone():
    td = ToneDetector()
    td.record_utterance(_words(2))     # brief
    td.record_utterance(_words(2))     # brief → locks
    assert td.get_tone() == "brief"
    td.record_utterance(_words(20))    # warm — but locked, should be ignored
    assert td.get_tone() == "brief"


def test_locked_flag_set_after_two_utterances():
    td = ToneDetector()
    td.record_utterance(_words(6))
    td.record_utterance(_words(6))
    assert td._locked is True


# ---------------------------------------------------------------------------
# 6. get_instruction returns correct string for each tone
# ---------------------------------------------------------------------------

def test_get_instruction_brief():
    td = ToneDetector()
    td.record_utterance(_words(2))
    td.record_utterance(_words(2))
    instr = td.get_instruction()
    assert "TONE:" in instr
    assert "concise" in instr.lower() or "short" in instr.lower() or "point" in instr.lower()


def test_get_instruction_warm():
    td = ToneDetector()
    td.record_utterance(_words(15))
    td.record_utterance(_words(15))
    instr = td.get_instruction()
    assert "TONE:" in instr
    assert "warm" in instr.lower() or "conversational" in instr.lower()


def test_get_instruction_standard():
    td = ToneDetector()
    instr = td.get_instruction()
    assert "TONE:" in instr
    assert "natural" in instr.lower() or "standard" not in instr.lower() or "warm" in instr.lower()


def test_all_three_instructions_are_distinct():
    instructions = set()
    for words_per_turn, _ in [(2, "brief"), (6, "standard"), (15, "warm")]:
        td = ToneDetector()
        td.record_utterance(_words(words_per_turn))
        td.record_utterance(_words(words_per_turn))
        instructions.add(td.get_instruction())
    assert len(instructions) == 3


# ---------------------------------------------------------------------------
# 7. to_dict / from_dict round-trip
# ---------------------------------------------------------------------------

def test_to_dict_from_dict_round_trip():
    td = ToneDetector()
    td.record_utterance(_words(2))
    td.record_utterance(_words(2))
    d = td.to_dict()
    td2 = ToneDetector.from_dict(d)
    assert td2.get_tone() == "brief"
    assert td2._locked is True


def test_from_dict_empty_dict_gives_defaults():
    td = ToneDetector.from_dict({})
    assert td.get_tone() == "standard"
    assert td._locked is False


# ---------------------------------------------------------------------------
# 8. get_tone_instruction_from_session — live instance path
# ---------------------------------------------------------------------------

def test_session_live_instance_path():
    td = ToneDetector()
    td.record_utterance(_words(2))
    td.record_utterance(_words(2))
    session = {"tone_detector": td, "_tone_state": td.to_dict()}
    instr = get_tone_instruction_from_session(session)
    assert "TONE:" in instr
    # brief tone
    assert "concise" in instr.lower() or "point" in instr.lower()


# ---------------------------------------------------------------------------
# 9. get_tone_instruction_from_session — rebuild from _tone_state (no live instance)
# ---------------------------------------------------------------------------

def test_session_rebuild_from_tone_state():
    td = ToneDetector()
    td.record_utterance(_words(15))
    td.record_utterance(_words(15))
    # Simulate Redis round-trip: live instance absent, state preserved as dict
    session = {"tone_detector": None, "_tone_state": td.to_dict()}
    instr = get_tone_instruction_from_session(session)
    assert "TONE:" in instr
    assert "warm" in instr.lower() or "conversational" in instr.lower()


# ---------------------------------------------------------------------------
# 10. get_tone_instruction_from_session — never raises on bad input
# ---------------------------------------------------------------------------

def test_session_helper_does_not_raise_on_empty_session():
    instr = get_tone_instruction_from_session({})
    assert isinstance(instr, str)
    assert "TONE:" in instr


def test_session_helper_does_not_raise_on_none():
    try:
        instr = get_tone_instruction_from_session(None)  # type: ignore[arg-type]
    except Exception as exc:
        pytest.fail(f"get_tone_instruction_from_session raised: {exc}")
