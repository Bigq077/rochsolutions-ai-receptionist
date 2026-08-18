"""A trailing slash on GOOGLE_SHEETS_ID silently killed a clinic's call log.

JV, 2026-08-18, call `CA38e560314294de4e5671168fc6975db5`: the booking landed,
but both Sheets appends returned **HTTP 404 "Requested entity was not found"**:

    .../spreadsheets/1eM26iIE2-hUaDk6GQX5l018eELizxVzw5vbuwP2qRis%2F/values/...
                                                                  ^^^ a slash

`.strip()` removes whitespace, not slashes. The id is copied out of a URL, so
the paste is routinely a fragment of one — and a 404 looks identical to a
deleted sheet or a permissions fault, so it reads as anything but a typo.
"""
from __future__ import annotations

import pytest

from app.tools.handoff import _normalise_sheet_id

_ID = "1eM26iIE2-hUaDk6GQX5l018eELizxVzw5vbuwP2qRis"


@pytest.mark.parametrize(
    "pasted",
    [
        _ID,                                              # already clean
        _ID + "/",                                        # the live JV defect
        "  " + _ID + "  ",                                # stray whitespace
        f"https://docs.google.com/spreadsheets/d/{_ID}/edit#gid=0",   # full URL
        f"https://docs.google.com/spreadsheets/d/{_ID}",              # URL, no suffix
        f"'{_ID}'",                                       # quoted paste
    ],
)
def test_every_realistic_paste_reduces_to_the_bare_id(pasted):
    assert _normalise_sheet_id(pasted) == _ID


def test_the_exact_live_value_no_longer_carries_a_slash():
    """Pin the defect itself: this value 404'd every append on JV."""
    assert "/" not in _normalise_sheet_id(_ID + "/")


def test_missing_id_stays_empty_so_the_configured_check_still_fires():
    """_get_service() treats a falsy SHEET_ID as 'not configured' — keep that."""
    assert _normalise_sheet_id("") == ""
    assert _normalise_sheet_id(None) == ""
