"""Call-3 P3 (2026-07-19): one slot, one name — _spoken_slot_time uses natural
UK clock-face phrasing for every on-grid minute, matching what the model says
on repeat reads and readbacks ("five past five", "twenty to six"), so the same
slot is never announced three different ways in one call. Off-grid minutes
stay digital (unambiguous)."""
from __future__ import annotations

import pytest

from app.tools.receptionist_tools import _spoken_slot_time


@pytest.mark.parametrize("hhmm,expected", [
    # Call-3's actual slots — the ones that got three names each.
    ("17:05", "five past five in the evening"),
    ("17:40", "twenty to six in the evening"),
    # Quarter grid (unchanged behaviour).
    ("16:30", "half past four in the afternoon"),
    ("18:45", "quarter to seven in the evening"),
    ("09:15", "quarter past nine in the morning"),
    ("17:00", "five in the evening"),
    # Remaining on-grid minutes — past forms.
    ("10:10", "ten past ten in the morning"),
    ("14:20", "twenty past two in the afternoon"),
    ("19:25", "twenty-five past seven in the evening"),
    # Remaining on-grid minutes — to forms (next hour, same day-part label).
    ("18:35", "twenty-five to seven in the evening"),
    ("20:50", "ten to nine in the evening"),
    ("09:55", "five to ten in the morning"),
    # Specials.
    ("12:00", "midday"),
    ("00:00", "midnight"),
])
def test_spoken_slot_time(hhmm, expected):
    assert _spoken_slot_time(hhmm) == expected


def test_off_grid_minute_falls_back_to_digits():
    # Never on the 5-minute grid — unambiguous digital fallback, not clock-face.
    assert _spoken_slot_time("17:07") == "five 07 in the evening"


def test_garbage_input_returned_unchanged():
    assert _spoken_slot_time("not-a-time") == "not-a-time"
