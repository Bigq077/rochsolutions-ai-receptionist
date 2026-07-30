# tests/regression/test_detector_agreed_date_vs_booked.py
"""
C1 — the caller agreed one date and a DIFFERENT date exists in the calendar.

Found 2026-07-30 on a verification call made against the previous night's build.
Two real instances, both with real calendar events:

  CAc64a05f1  agreed "Tuesday the 4th of August"   booked 2026-07-29T17:30  (6 days out)
  CA5c4fb14f  agreed "Tuesday the 4th of August"   booked 2026-08-05T19:00  (1 day out)

The caller says yes, she says "All booked", and the appointment is on another
day. Nothing in the call sounds wrong, which is why this is the worst class in
the register and why it went unnoticed: it is inaudible.

WHY THIS TEST EXISTS SEPARATELY FROM A2
---------------------------------------
C1 was initially filed as A2 in a hand-back. It is not, and the difference
changes which code you go and fix:

  A2  the spoken phrase is internally INCONSISTENT — "Friday the 1st of August"
      when the 1st is a Saturday. The date is right, the weekday label is wrong.
      Fix lives in how the weekday is derived.
  C1  the spoken phrase is perfectly consistent and names the WRONG DAY —
      "Tuesday the 4th of August" (4 Aug 2026 genuinely IS a Tuesday) against a
      booking on the 5th. Fix lives in why the spoken slot and the booked slot
      diverged.

A2's detector returns 0 on every C1 instance, correctly. Had the misfiling
stood, we would have rewritten weekday derivation and left a wrong-day booking
in place. The two cases below are the real calls that pin the distinction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
import detect_defects as dd  # noqa: E402


def _call(spoken_turns, selected_slot, event="evt123"):
    return {
        "call_sid": "CAtest",
        "calendar_event_id": event,
        "collected": {"selected_slot": selected_slot},
        "transcript": [{"role": "assistant", "text": t} for t in spoken_turns],
    }


# ── fires when the agreed date is not the booked date ──────────────────────
def test_fires_on_the_real_v2_call():
    """CA5c4fb14f: agreed Tuesday 4 Aug, booked Wednesday 5 Aug."""
    call = _call(
        ["So that's John, Tuesday the 4th of August at seven in the evening "
         "— shall I go ahead and book that in?",
         "All booked — you're in for Tuesday the 4th of August at seven in the "
         "evening. We'll see you then — take care."],
        "2026-08-05T19:00:00",
    )
    detail = dd.d_spoken_slot_not_booked_slot(call)
    assert detail and "2026-08-05" in detail


def test_fires_when_the_booking_is_days_out():
    """CAc64a05f1: agreed Tuesday 4 Aug, booked 29 Jul."""
    call = _call(
        ["So that's Tom, Tuesday the 4th of August at half past six in the "
         "evening — shall I go ahead and book that in?"],
        "2026-07-29T17:30:00",
    )
    assert dd.d_spoken_slot_not_booked_slot(call)


def test_reports_when_no_calendar_event_exists():
    """A wrong date AND no event is worse, not better — say so in the detail."""
    call = _call(
        ["All booked — you're in for Tuesday the 4th of August at five."],
        "2026-08-05T17:00:00", event=None,
    )
    detail = dd.d_spoken_slot_not_booked_slot(call)
    assert detail and "NO EVENT" in detail


# ── silent when the agreed date IS the booked date ─────────────────────────
def test_silent_on_the_real_v1_call():
    """CA3264ed4b: agreed Tuesday 4 Aug, booked Tuesday 4 Aug. Correct call."""
    call = _call(
        ["So that's Tom, Tuesday the 4th of August at five in the evening "
         "— shall I go ahead and book that in?",
         "All booked — you're in for Tuesday the 4th of August at five in the "
         "evening. We'll see you then — take care."],
        "2026-08-04T17:00:00",
    )
    assert dd.d_spoken_slot_not_booked_slot(call) is None


def test_silent_on_an_a2_call_the_date_is_right():
    """CAfe6a4162: "Friday the 1st of August" booked 2026-08-01.

    1 Aug 2026 is a Saturday, so the WEEKDAY is wrong — that is A2, and A2's
    detector catches it. The DATE the caller agreed to is the date that was
    booked, so C1 must stay silent. If this test ever goes red, C1 has started
    double-counting A2 and the two classes can no longer be told apart.
    """
    call = _call(
        ["All booked — you're in for Friday the 1st of August at six in the "
         "evening. We'll see you then — take care."],
        "2026-08-01T18:00:00",
    )
    assert dd.d_spoken_slot_not_booked_slot(call) is None
    assert dd.d_day_date_mismatch(
        {"transcript": call["transcript"], "collected": {}}
    ) is not None, "A2 should own this call"


def test_silent_on_dates_mentioned_while_browsing():
    """A date offered but never agreed to is not a wrong booking.

    Availability turns name plenty of dates the caller never acted on. Counting
    them would bury the real hits under noise, which is how a register loses its
    credibility.
    """
    call = _call(
        ["Tuesday 4th August — Number 1, five in the evening. Number 2, quarter "
         "to six in the evening. Any of those work?",
         "Wednesday the 5th of August doesn't have any morning slots, I'm afraid.",
         "So that's Tom, Wednesday the 5th of August at seven — shall I go ahead "
         "and book that in?"],
        "2026-08-05T19:00:00",
    )
    assert dd.d_spoken_slot_not_booked_slot(call) is None


def test_silent_when_no_slot_was_ever_selected():
    """No selected_slot means nothing to compare — never guess."""
    call = _call(["All booked — you're in for Tuesday the 4th of August."], None)
    assert dd.d_spoken_slot_not_booked_slot(call) is None


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-45T99:00", None])
def test_malformed_slot_is_ignored_not_raised(bad):
    """A detector must never crash — that hides every other defect on the call."""
    call = _call(["All booked — you're in for Tuesday the 4th of August."], bad)
    assert dd.d_spoken_slot_not_booked_slot(call) is None
