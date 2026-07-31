# tests/regression/test_detectors_allow_a_change_of_mind.py
"""
A caller changing their mind is correct behaviour, and the scorer must not report
it as a defect.

CA78e8b9b7 (31 Jul 2026, build 2a1be7ac6f10) is the first clean day-change
booking this system produced: agreed Tuesday, asked for Wednesday instead,
confirmed Wednesday, booked 2026-08-05T17:30, real calendar event, 145 s. The
write-guard never fired because there was nothing left to catch.

The scorer called it C1 and A4 — its two worst findings — on the call that proved
the fix worked. Both for the same reason: they treated the abandoned Tuesday as
evidence.

  C1  scanned every confirmation turn and returned on the FIRST mismatch. The
      caller's first confirmation names the day they moved off; the last one is
      the one they acted on. Now compares the LAST confirmation only — the same
      newest-wins rule the engine's write-guard and readback are built on.
  A4  counted confirmations. A change of mind legitimately produces two, one per
      day. A real loop always re-asks about the SAME day, so the signature is now
      the date named in the ask and a repeat is the defect.

A false C1 is worse than no C1. It is the register's most serious class, so
mis-firing it on a correct booking sends someone hunting a defect that is not
there — and, next time, teaches them to discount the one that is.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
import detect_defects as dd  # noqa: E402


def _call(turns, selected_slot=None, event="evt123"):
    return {
        "call_sid": "CAtest",
        "calendar_event_id": event,
        "collected": {"selected_slot": selected_slot},
        "transcript": [{"role": "assistant", "text": t} for t in turns],
    }


# The real spoken turns from CA78e8b9b7, in order.
CLEAN_DAY_CHANGE = [
    "Tuesday 4th August — Number 1, quarter to six in the evening. Number 2, "
    "half past six in the evening. Any of those work?",
    "So that's Tuesday the 4th of August at half past six in the evening — "
    "could I take your first name and surname?",
    "So that's Quinton, Tuesday the 4th of August at half past six in the "
    "evening — shall I go ahead and book that in?",
    "Wednesday 5th of August — Number 1, half past five in the evening. "
    "Number 2, quarter past six in the evening. Any of those work?",
    "So that's Quinton, Wednesday the 5th of August at half past five in the "
    "evening — shall I go ahead and book that in?",
    "All booked — you're in for Wednesday the 5th of August at half past five "
    "in the evening. We'll see you then — take care.",
]


class TestTheFirstCleanBooking:
    def test_c1_does_not_fire(self):
        call = _call(CLEAN_DAY_CHANGE, "2026-08-05T17:30:00")
        assert dd.d_spoken_slot_not_booked_slot(call) is None, (
            "the caller agreed Wednesday last and was booked on Wednesday — "
            "reporting the abandoned Tuesday as a wrong-day booking is a false "
            "positive on the register's most serious class"
        )

    def test_a4_does_not_fire(self):
        call = _call(CLEAN_DAY_CHANGE, "2026-08-05T17:30:00")
        assert dd.d_confirmation_loop(call) is None, (
            "two confirmations for two different days is a change of mind, "
            "not a loop"
        )


class TestRealDefectsStillFire:
    def test_c1_still_fires_when_the_last_agreed_day_is_wrong(self):
        """CA5c4fb14f: agreed Tuesday 4 Aug, booked Wednesday 5 Aug, no change
        of mind anywhere. This is the defect C1 exists for."""
        call = _call([
            "So that's Tom, Tuesday the 4th of August at seven in the evening "
            "— shall I go ahead and book that in?",
            "All booked — you're in for Tuesday the 4th of August at seven in "
            "the evening.",
        ], "2026-08-05T19:00:00")
        out = dd.d_spoken_slot_not_booked_slot(call)
        assert out and "2026-08-05" in out

    def test_c1_fires_even_after_a_change_of_mind_if_the_booking_is_wrong(self):
        """The caller moves Tuesday -> Wednesday, and the booking lands on
        neither. Changing your mind must not buy immunity."""
        call = _call(CLEAN_DAY_CHANGE, "2026-08-11T17:30:00")
        out = dd.d_spoken_slot_not_booked_slot(call)
        assert out and "2026-08-11" in out

    def test_a4_still_fires_on_the_same_day_re_asked(self):
        """CA6dce36c8: Tuesday the 4th confirmed twice, caller hung up."""
        call = _call([
            "So that's Sara, Tuesday the 4th of August at quarter past six in "
            "the evening — shall I go ahead and book that in?",
            "That's Tuesday the 4th of August — not Tuesday, you're right. "
            "Shall I go ahead and book that in?",
        ])
        out = dd.d_confirmation_loop(call)
        assert out and "same day" in out

    def test_a4_fires_on_repeated_bare_re_asks(self):
        """A re-ask that names no day at all is still a re-ask."""
        call = _call([
            "Shall I go ahead and book that in?",
            "Shall I go ahead and book that in?",
        ])
        assert dd.d_confirmation_loop(call) is not None


class TestQuiet:
    @pytest.mark.parametrize("turns", [
        [],
        ["Right — what's the appointment for?"],
        ["So that's Quinton, Wednesday the 5th of August at half past five in "
         "the evening — shall I go ahead and book that in?"],
    ])
    def test_a4_silent_on_a_single_confirmation(self, turns):
        assert dd.d_confirmation_loop(_call(turns)) is None

    def test_c1_silent_without_a_booked_slot(self):
        """No slot recorded — nothing to compare, and guessing would fire on
        every abandoned call."""
        assert dd.d_spoken_slot_not_booked_slot(_call(CLEAN_DAY_CHANGE, None)) is None
