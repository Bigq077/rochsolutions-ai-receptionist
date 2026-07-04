"""
tests/test_location_deictic_clinic.py
-------------------------------------
Clinic-resolver v2-3 / F16 — deictic "this clinic" resolution (sign-off sweep
Call 5).

A caller who answers "Awlstuh or Redditch?" with "this clinic please" / "this
one" / "the one I called" means the site they dialled — but that names no clinic
alias, so it missed the fast path, went to the biased-confirm rung, then dead
air, then the keypad, and only resolved when the caller pressed DTMF '1'. ~30s
of friction (F16).

Like v2-1 indifference, a deictic self-reference to "this" clinic should resolve
DIRECTLY to the default clinic (Alcester, the primary Mon-Fri site) instead of
climbing the ladder.

_is_deictic_current_clinic() is the deterministic predicate. It runs only in the
location-resolution intercept at the OPEN rung (v3_awaiting_use_this_clinic is
False there); at the biased-confirm rung "this clinic" still means "yes, use the
biased one" and is handled by the confirm gate, never this predicate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import _is_deictic_current_clinic


@pytest.mark.parametrize(
    "utterance",
    [
        "this clinic",
        "this clinic please",
        "this one",
        "this one please",
        "this place",
        "this site",
        "this branch",
        "this here clinic",
        "the one I called",
        "the one I rang",
        "the clinic I called",
        "the one I dialled",
        "the one I phoned",
        "whichever one I called",
    ],
)
def test_deictic_phrases_detected(utterance):
    assert _is_deictic_current_clinic(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "alcester",
        "redditch",
        "the redditch one",   # names a specific clinic — not deictic-vague
        "the alcester clinic",
        "I'd like to book",
        "Tuesday please",
        "my name is John",
        "",
    ],
)
def test_non_deictic_not_detected(utterance):
    # The predicate only DETECTS the deictic phrase. Question-routing (e.g.
    # "what's the address of this clinic?") is handled upstream by the guard's
    # `not _transcript_is_question(...)` check, not by this predicate.
    assert _is_deictic_current_clinic(utterance) is False
