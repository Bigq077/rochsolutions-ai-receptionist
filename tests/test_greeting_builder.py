# tests/test_greeting_builder.py
"""
Tests for app/greeting_builder.py

Coverage:
  1. Friday 4:45pm, close at 5pm → near-close greeting
  2. Saturday → outside hours (closed day) greeting
  3. Monday 9am → "Happy Monday" prefix
  4. Exception in timezone parsing → safe default returned
  5. get_next_open_day skips closed_days correctly
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.greeting_builder import build_greeting, get_next_open_day


# ---------------------------------------------------------------------------
# Shared config matching clinic_config.json
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = {
    "clinic_name": "Theorem Health",
    "timezone": "Europe/London",
    "opening_hours": {
        "monday":    {"open": "08:00", "close": "18:00"},
        "tuesday":   {"open": "08:00", "close": "18:00"},
        "wednesday": {"open": "08:00", "close": "18:00"},
        "thursday":  {"open": "08:00", "close": "18:00"},
        "friday":    {"open": "08:00", "close": "17:00"},
    },
    "closed_days": ["saturday", "sunday"],
}

_TZ = ZoneInfo("Europe/London")


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return a timezone-aware datetime in Europe/London."""
    return datetime(year, month, day, hour, minute, tzinfo=_TZ)


# ---------------------------------------------------------------------------
# 1. Friday 4:45pm, close at 5pm → near-close greeting
# ---------------------------------------------------------------------------

def test_friday_near_close_gives_closing_soon_greeting():
    # 2026-03-27 is a Friday; friday closes at 17:00; 16:45 is 15 min before close
    fake = _dt(2026, 3, 27, 16, 45)
    with patch("app.greeting_builder.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = build_greeting(SAMPLE_CONFIG)
    assert "just caught us" in result
    assert "closing shortly" in result


# ---------------------------------------------------------------------------
# 2. Saturday → closed day greeting
# ---------------------------------------------------------------------------

def test_saturday_gives_closed_greeting():
    # 2026-03-28 is a Saturday
    fake = _dt(2026, 3, 28, 10, 0)
    with patch("app.greeting_builder.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = build_greeting(SAMPLE_CONFIG)
    assert "closed" in result
    assert "next available" in result.lower()
    # Next open day after Saturday should be Monday
    assert "Monday" in result


# ---------------------------------------------------------------------------
# 3. Monday 9am → "Happy Monday" prefix
# ---------------------------------------------------------------------------

def test_monday_morning_has_happy_monday_prefix():
    # 2026-03-30 is a Monday
    fake = _dt(2026, 3, 30, 9, 0)
    with patch("app.greeting_builder.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = build_greeting(SAMPLE_CONFIG)
    assert result.startswith("Happy Monday")


# ---------------------------------------------------------------------------
# 4. Exception in timezone parsing → safe default returned
# ---------------------------------------------------------------------------

def test_invalid_timezone_returns_safe_default():
    bad_config = {
        "clinic_name": "Test Clinic",
        "timezone": "INVALID/TZ_DOES_NOT_EXIST",
        "opening_hours": {},
        "closed_days": [],
    }
    # No mock needed — ZoneInfo("INVALID/...") raises, triggering the except branch
    result = build_greeting(bad_config)
    assert "I'm Susie" in result
    assert "Test Clinic" in result


# ---------------------------------------------------------------------------
# 5. get_next_open_day skips closed_days correctly
# ---------------------------------------------------------------------------

def test_get_next_open_day_skips_saturday_and_sunday():
    # From Saturday 2026-03-28, skip Sat (closed) and Sun (closed) → Monday
    saturday = _dt(2026, 3, 28, 10, 0)
    result = get_next_open_day(SAMPLE_CONFIG, saturday)
    assert result == "Monday"


def test_get_next_open_day_from_friday_returns_monday():
    # From Friday EOD, next open day is Monday (Saturday + Sunday closed)
    friday = _dt(2026, 3, 27, 23, 0)
    result = get_next_open_day(SAMPLE_CONFIG, friday)
    assert result == "Monday"


def test_get_next_open_day_returns_next_week_when_all_closed():
    # Config with no open days → "next week"
    no_open_config = {
        "clinic_name": "Ghost Clinic",
        "closed_days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "opening_hours": {},
    }
    saturday = _dt(2026, 3, 28, 10, 0)
    result = get_next_open_day(no_open_config, saturday)
    assert result == "next week"


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

def test_after_close_on_friday_gives_closed_greeting():
    # Friday 17:01 — past close time
    fake = _dt(2026, 3, 27, 17, 1)
    with patch("app.greeting_builder.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = build_greeting(SAMPLE_CONFIG)
    assert "closed" in result


def test_monday_afternoon_no_happy_monday():
    # Monday 14:00 — past noon, no "Happy Monday"
    fake = _dt(2026, 3, 30, 14, 0)
    with patch("app.greeting_builder.datetime") as mock_dt:
        mock_dt.now.return_value = fake
        result = build_greeting(SAMPLE_CONFIG)
    assert not result.startswith("Happy Monday")
    assert "I'm Susie" in result
