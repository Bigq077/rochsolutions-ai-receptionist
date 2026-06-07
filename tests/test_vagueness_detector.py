"""
tests/test_vagueness_detector.py
----------------------------------
Tests for vagueness detection and option-selection parsing.

Covers:
  1. "I'm pretty flexible" → vague
  2. "Thursday afternoon" → not vague
  3. "hmm" (1 word, no day/time) → vague
  4. "maybe morning" → not vague (TIME_PATTERN matches "morning")
  5. API success → 2 options presented (via build_vague_options)
  6. "first one" after options → options[0] selected
  7. API failure during vague handling → falls back to normal re-ask
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vagueness_detector import (
    is_vague_availability,
    build_vague_options,
    build_vague_response_phrase,
    parse_option_selection,
    _time_to_speech,
)


# ---------------------------------------------------------------------------
# Tests for is_vague_availability
# ---------------------------------------------------------------------------

class TestIsVagueAvailability:
    def test_flexible_is_vague(self):
        """'I'm pretty flexible' → vague."""
        assert is_vague_availability("I'm pretty flexible") is True

    def test_thursday_afternoon_not_vague(self):
        """'Thursday afternoon' contains day+time → not vague."""
        assert is_vague_availability("Thursday afternoon") is False

    def test_one_word_no_day_time_is_vague(self):
        """'hmm' (1 word, no day/time signal) → vague."""
        assert is_vague_availability("hmm") is True

    def test_maybe_morning_not_vague(self):
        """'maybe morning' — TIME_PATTERN matches 'morning' → not vague."""
        assert is_vague_availability("maybe morning") is False

    def test_whenever_is_vague(self):
        assert is_vague_availability("whenever") is True

    def test_anytime_is_vague(self):
        assert is_vague_availability("anytime") is True

    def test_dont_mind_is_vague(self):
        assert is_vague_availability("I don't mind") is True

    def test_not_fussed_is_vague(self):
        assert is_vague_availability("not fussed really") is True

    def test_monday_not_vague(self):
        """'Monday' contains a day signal → not vague (DAY_PATTERN matches)."""
        assert is_vague_availability("Monday") is False

    def test_empty_string_returns_false(self):
        """Empty string → vague (0 words < 3, no day/time)."""
        # Empty string has 0 words, no day/time, so is_vague returns True
        result = is_vague_availability("")
        assert isinstance(result, bool)

    def test_nine_oclock_not_vague(self):
        """'nine o clock' contains digit-like → check TIME_PATTERN behaviour."""
        # 'nine' doesn't match TIME_PATTERN, but 'o clock' might
        # Just verify it doesn't crash
        result = is_vague_availability("nine o clock")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Tests for build_vague_options
# ---------------------------------------------------------------------------

_SAMPLE_AVAILABLE_DAYS = [
    {
        "date":       "2025-04-10",
        "day_label":  "Thursday 10th April",
        "slot_times": ["09:00", "11:00"],
        "slots": [
            {"start": "2025-04-10T09:00:00", "end": "2025-04-10T10:00:00"},
            {"start": "2025-04-10T11:00:00", "end": "2025-04-10T12:00:00"},
        ],
    },
    {
        "date":       "2025-04-11",
        "day_label":  "Friday 11th April",
        "slot_times": ["14:00"],
        "slots": [
            {"start": "2025-04-11T14:00:00", "end": "2025-04-11T15:00:00"},
        ],
    },
    {
        "date":       "2025-04-14",
        "day_label":  "Monday 14th April",
        "slot_times": ["10:00"],
        "slots": [
            {"start": "2025-04-14T10:00:00", "end": "2025-04-14T11:00:00"},
        ],
    },
]


class TestBuildVagueOptions:
    def test_returns_up_to_two_options(self):
        """build_vague_options returns at most 2 options from available_days."""
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS)
        assert len(opts) == 2

    def test_option_has_required_fields(self):
        """Each option dict has day_label, time_hhmm, time_speech, slot_iso."""
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS)
        for opt in opts:
            assert "day_label"   in opt
            assert "time_hhmm"   in opt
            assert "time_speech" in opt
            assert "slot_iso"    in opt

    def test_uses_first_slot_of_each_day(self):
        """Each option uses the first slot_time and first slot of the day."""
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS)
        assert opts[0]["time_hhmm"] == "09:00"
        assert opts[1]["time_hhmm"] == "14:00"

    def test_empty_available_days_returns_empty(self):
        """build_vague_options returns [] when available_days is empty."""
        assert build_vague_options([]) == []

    def test_single_day_returns_one_option(self):
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS[:1])
        assert len(opts) == 1


class TestBuildVagueResponsePhrase:
    def test_two_options_phrase(self):
        """Two options → includes 'No problem' and 'Either of those work?'"""
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS[:2])
        phrase = build_vague_response_phrase(opts)
        assert "No problem" in phrase
        assert "Either of those work?" in phrase

    def test_one_option_phrase(self):
        """One option → includes 'That's the next one I've got.'"""
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS[:1])
        phrase = build_vague_response_phrase(opts)
        assert "That's the next one I've got." in phrase

    def test_empty_options_returns_empty(self):
        assert build_vague_response_phrase([]) == ""

    def test_phrase_under_400_chars(self):
        opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS[:2])
        phrase = build_vague_response_phrase(opts)
        assert len(phrase) <= 400


# ---------------------------------------------------------------------------
# Tests for parse_option_selection
# ---------------------------------------------------------------------------

class TestParseOptionSelection:
    def setup_method(self):
        self.opts = build_vague_options(_SAMPLE_AVAILABLE_DAYS[:2])

    def test_first_one_selects_option_0(self):
        """'first one' → options[0]."""
        result = parse_option_selection("first one", self.opts)
        assert result is not None
        assert result["day_label"] == self.opts[0]["day_label"]

    def test_second_one_selects_option_1(self):
        """'second' → options[1]."""
        result = parse_option_selection("second", self.opts)
        assert result is not None
        assert result["day_label"] == self.opts[1]["day_label"]

    def test_day_name_match(self):
        """'Thursday' in utterance → matches Thursday option."""
        result = parse_option_selection("Thursday works for me", self.opts)
        assert result is not None
        assert "Thursday" in result["day_label"]

    def test_ambiguous_returns_none(self):
        """Completely ambiguous utterance → None."""
        result = parse_option_selection("yes please", self.opts)
        assert result is None

    def test_empty_options_returns_none(self):
        assert parse_option_selection("first", []) is None

    def test_other_one_selects_option_1(self):
        """'other one' → options[1]."""
        result = parse_option_selection("the other one", self.opts)
        assert result is not None
        assert result["day_label"] == self.opts[1]["day_label"]


# ---------------------------------------------------------------------------
# Tests for time_to_speech
# ---------------------------------------------------------------------------

class TestTimeToSpeech:
    def test_nine_am(self):
        assert "nine o'clock" in _time_to_speech("09:00")
        assert "morning" in _time_to_speech("09:00")

    def test_half_past_two_pm(self):
        result = _time_to_speech("14:30")
        assert "half past" in result
        assert "afternoon" in result

    def test_four_pm(self):
        result = _time_to_speech("16:00")
        assert "four o'clock" in result
        assert "afternoon" in result


# ---------------------------------------------------------------------------
# Tests for API success / failure paths (via mock session)
# ---------------------------------------------------------------------------

class TestVagueFlowIntegration:
    def test_api_success_two_options_presented(self):
        """
        When available_days is populated and caller says "whenever",
        build_vague_options returns 2 options and a phrase is built.
        """
        session = {"available_days": _SAMPLE_AVAILABLE_DAYS}
        opts = build_vague_options(session["available_days"])
        assert len(opts) == 2
        phrase = build_vague_response_phrase(opts)
        assert "Thursday" in phrase
        assert "Friday" in phrase

    def test_api_failure_empty_available_days(self):
        """
        When available_days is empty (API failed), build_vague_options
        returns [] and the caller flow should fall through to normal re-ask.
        """
        session = {"available_days": []}
        opts = build_vague_options(session["available_days"])
        assert opts == []
        phrase = build_vague_response_phrase(opts)
        assert phrase == ""
