"""
tests/test_name_usage_tracker.py
---------------------------------
Tests for NameUsageTracker — governs personalised name use in Susie's replies.

Covers:
  1. set_name("John Davies") → stores "John"
  2. set_name("A") → not stored (too short)
  3. set_name("John2") → not stored (contains digit)
  4. set_name("JOHN") → stored as "John" (title-cased)
  5. get_name_if_available called 3 times → third returns None
  6. peek() after exhaustion → returns None
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.name_usage_tracker import NameUsageTracker


class TestSetName:
    def test_stores_first_name_only(self):
        """set_name('John Davies') stores only the first name 'John'."""
        tracker = NameUsageTracker()
        tracker.set_name("John Davies")
        assert tracker._name == "John"

    def test_too_short_not_stored(self):
        """set_name('A') is rejected — under 2 characters."""
        tracker = NameUsageTracker()
        tracker.set_name("A")
        assert tracker._name is None

    def test_digit_in_name_rejected(self):
        """set_name('John2') is rejected — contains digit."""
        tracker = NameUsageTracker()
        tracker.set_name("John2")
        assert tracker._name is None

    def test_uppercased_name_title_cased(self):
        """set_name('JOHN') stores 'John' (title-cased)."""
        tracker = NameUsageTracker()
        tracker.set_name("JOHN")
        assert tracker._name == "John"

    def test_empty_string_not_stored(self):
        """set_name('') is a no-op — name stays None."""
        tracker = NameUsageTracker()
        tracker.set_name("")
        assert tracker._name is None

    def test_none_not_stored(self):
        """set_name(None) is a safe no-op."""
        tracker = NameUsageTracker()
        tracker.set_name(None)  # type: ignore[arg-type]
        assert tracker._name is None

    def test_special_char_rejected(self):
        """Names with special characters are rejected."""
        tracker = NameUsageTracker()
        tracker.set_name("Jo@n")
        assert tracker._name is None


class TestGetNameIfAvailable:
    def test_first_call_returns_name(self):
        """First call to get_name_if_available returns the stored name."""
        tracker = NameUsageTracker()
        tracker.set_name("Sarah")
        assert tracker.get_name_if_available() == "Sarah"

    def test_second_call_returns_name(self):
        """Second call still returns the name (MAX_USES=2)."""
        tracker = NameUsageTracker()
        tracker.set_name("Sarah")
        tracker.get_name_if_available()
        assert tracker.get_name_if_available() == "Sarah"

    def test_third_call_returns_none(self):
        """Third call returns None — counter exhausted at MAX_USES=2."""
        tracker = NameUsageTracker()
        tracker.set_name("Sarah")
        tracker.get_name_if_available()  # use 1
        tracker.get_name_if_available()  # use 2
        assert tracker.get_name_if_available() is None

    def test_no_name_set_returns_none(self):
        """get_name_if_available returns None when no name has been set."""
        tracker = NameUsageTracker()
        assert tracker.get_name_if_available() is None

    def test_decrements_counter(self):
        """Each call decrements _uses_remaining by 1."""
        tracker = NameUsageTracker()
        tracker.set_name("Ali")
        assert tracker._uses_remaining == NameUsageTracker.MAX_USES
        tracker.get_name_if_available()
        assert tracker._uses_remaining == NameUsageTracker.MAX_USES - 1


class TestPeek:
    def test_peek_does_not_decrement(self):
        """peek() checks availability without decrementing the counter."""
        tracker = NameUsageTracker()
        tracker.set_name("Tom")
        _ = tracker.peek()
        assert tracker._uses_remaining == NameUsageTracker.MAX_USES

    def test_peek_returns_name_when_available(self):
        """peek() returns the name while uses remain."""
        tracker = NameUsageTracker()
        tracker.set_name("Tom")
        assert tracker.peek() == "Tom"

    def test_peek_after_exhaustion_returns_none(self):
        """peek() after exhaustion returns None — counter at 0."""
        tracker = NameUsageTracker()
        tracker.set_name("Tom")
        tracker.get_name_if_available()  # use 1
        tracker.get_name_if_available()  # use 2
        assert tracker.peek() is None

    def test_peek_with_no_name_returns_none(self):
        """peek() returns None if no name has been set."""
        tracker = NameUsageTracker()
        assert tracker.peek() is None


class TestMaxUses:
    def test_max_uses_constant_is_two(self):
        """MAX_USES class constant must be 2."""
        assert NameUsageTracker.MAX_USES == 2

    def test_fresh_tracker_starts_at_max_uses(self):
        """A fresh tracker starts with _uses_remaining == MAX_USES."""
        tracker = NameUsageTracker()
        assert tracker._uses_remaining == NameUsageTracker.MAX_USES
