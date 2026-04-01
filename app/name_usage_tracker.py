# app/name_usage_tracker.py
"""
NameUsageTracker — governs how often Susie uses the caller's first name.

Real receptionists use the caller's name once or twice per call for warmth;
overuse sounds scripted.  This tracker allows at most MAX_USES (2) uses and
is exhausted after that, ensuring the name never appears excessively.

Usage:
    tracker = NameUsageTracker()
    tracker.set_name("John Davies")         # stores "John"
    name = tracker.get_name_if_available()  # returns "John", decrements counter
    name = tracker.get_name_if_available()  # returns "John", decrements counter
    name = tracker.get_name_if_available()  # returns None (exhausted)
    tracker.peek()                          # returns None (still exhausted)
"""
from __future__ import annotations

import re


class NameUsageTracker:
    MAX_USES = 2

    def __init__(self) -> None:
        self._uses_remaining: int = self.MAX_USES
        self._name: str | None = None

    def set_name(self, raw_name: str) -> None:
        """
        Validates and stores the first name only.

        Rejects:
          - Names under 2 characters
          - Names containing digits or special characters
        Stores the name title-cased so "JOHN" becomes "John".
        """
        if not raw_name or not isinstance(raw_name, str):
            return
        first = raw_name.strip().split()[0]
        if len(first) < 2:
            return
        if re.search(r'[0-9!@#$%^&*()_+=\[\]{};\':"\\|,.<>?/]', first):
            return
        self._name = first.strip().title()

    def get_name_if_available(self) -> str | None:
        """
        Returns the first name and decrements the usage counter.
        Returns None if the counter is exhausted or no name has been set.
        """
        if self._name and self._uses_remaining > 0:
            self._uses_remaining -= 1
            return self._name
        return None

    def peek(self) -> str | None:
        """
        Check whether the name is still available without decrementing.
        Returns the name if uses remain, None if exhausted or not set.
        """
        return self._name if self._uses_remaining > 0 else None
