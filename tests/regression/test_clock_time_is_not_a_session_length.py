"""
Regression: the time a caller wants to COME IN is not how long they want to stay.

CAce1457d1 (jv_v1, 2026-08-11). The caller said

    "on the 24th of August at around 5 30 to 9 pm"

— a date and a time window — and the engine logged

    [ms_conn v3] session length captured: 30 minutes

taking the "30" out of half past five. `duration_choice_from_utterance` scanned
for each option value with the unit suffix OPTIONAL, so a bare "30" anywhere in
the utterance was a 30-minute session.

Two things make it worse than a stray parse:

  * the captured length drives the slot GRID as well as the booked event, so
    every time offered is on the wrong grid too;
  * the capture deliberately NEVER OVERWRITES, so one misread minute-hand fixes
    the wrong length for the remainder of the call.

jv_v1 sells a 30-minute Sports Massage, so "Monday at 4 30" was enough to write
a 40-minute assessment into the diary as 30 — the practitioner ten minutes
short, and nothing said to anyone.

The ordinal strip already existed for "the 30th of August". This is the same
bug through a different door.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.tools.receptionist_tools import duration_choice_from_utterance

JV = "jv_v1"
VE = "vital_edge"


def _dur(clinic_id: str, utterance: str):
    return duration_choice_from_utterance(get_clinic(clinic_id) or {}, utterance)


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        # The live utterance, verbatim.
        "on one second um on the 24th of august at around 5 30 to 9 pm",
        "around 5 30 to 9 pm",
        "monday at 4 30",
        "can i come at 9 30",
        "anytime after 5 30",
        # Punctuation is flattened to spaces upstream, so these arrive the same
        # way — pinned so a change to that normalisation is caught here.
        "monday at 4:30",
        "monday at 4.30",
        # Other clock forms that carry a bare number.
        "anything after 5 pm",
        "4 o clock",
        "around 6pm",
    ],
)
def test_a_clock_time_is_never_a_session_length(utterance):
    assert _dur(JV, utterance) is None, (
        f"{utterance!r} names WHEN, not HOW LONG — capturing a length here sets "
        "the slot grid and the booked event for the rest of the call, and the "
        "capture never overwrites"
    )


def test_the_original_ordinal_case_still_holds():
    """The strip this one was modelled on. Both must survive together —
    'the 24th at 5 30' needs each of them."""
    assert _dur(JV, "the 30th of august") is None
    assert _dur(JV, "the 30th of august at 5 30") is None


# ---------------------------------------------------------------------------
# Containment — the fix must not eat a real answer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("the 30 minute session", 30),
        ("30 minutes please", 30),
        ("id like the 60", 60),
        ("half an hour", 30),
    ],
)
def test_a_real_session_length_is_still_captured(utterance, expected):
    """A bare option value with no clock separator is untouched: the hour half
    of the pattern requires 00-23 AND a separator AND a 00-59 minute half, so a
    lone '30' or '90' can never match it."""
    assert _dur(JV, utterance) == expected


def test_a_length_survives_alongside_a_time():
    """The common real sentence: both a when and a how-long. Only the clock
    should be removed."""
    assert _dur(JV, "monday at 4 30 for the 60 minute one") == 60


@pytest.mark.parametrize("utterance,expected", [
    ("the 90 minute session", 90),
    ("90 minutes", 90),
    ("the 60", 60),
])
def test_vital_edge_60_90_is_unaffected(utterance, expected):
    """VE is the clinic where session length carries a price difference, so a
    regression here is a wrong charge as well as a wrong booking."""
    assert _dur(VE, utterance) == expected


def test_vital_edge_does_not_pick_up_a_clock_either():
    assert _dur(VE, "monday at 4 30") is None
