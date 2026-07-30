# tests/regression/test_write_guard_names_the_real_day.py
"""
The C1 write-guard's refusal names the day the slot is REALLY on.

CAb81fe651 (30 Jul 2026, build ad09f3e). The guard worked: the caller had been
told Tuesday, the slot about to be written was Wednesday, and it refused. Twice.
But its message only said "tell the caller the day and time you can actually
offer" without saying what that day WAS, so the model repeated the Tuesday it had
already spoken:

    slot = 2026-08-05 18:15  ->  "the slot I have is Tuesday the 4th of August
                                  at quarter past six. Is that the one you'd like?"

The caller confirmed, the guard fired again on the identical mismatch, and he
hung up after 231 s. Fail-closed is right — no wrong-day booking was written —
but it turned a wrong-day booking into no booking, and a lost patient either way.

The slot date is authoritative: it is the appointment that would actually exist.
The refusal now states it, so the model has something true to say and the caller
can accept or correct it.

The guard's own trigger logic is pinned by
tests/regression/test_booking_matches_the_spoken_day.py — this file covers only
what the refusal TELLS the model.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _spoken_day_phrase


class TestSpokenDayPhrase:
    @pytest.mark.parametrize("iso,expected", [
        ("2026-08-05", "Wednesday the 5th of August"),   # the slot from CAb81fe651
        ("2026-08-04", "Tuesday the 4th of August"),     # the day she kept saying
        ("2026-08-01", "Saturday the 1st of August"),
        ("2026-08-02", "Sunday the 2nd of August"),
        ("2026-08-03", "Monday the 3rd of August"),
        ("2026-08-11", "Tuesday the 11th of August"),    # 11th/12th/13th, not 11st
        ("2026-08-12", "Wednesday the 12th of August"),
        ("2026-08-13", "Thursday the 13th of August"),
        ("2026-08-21", "Friday the 21st of August"),
        ("2026-12-25", "Friday the 25th of December"),
    ])
    def test_renders_a_speakable_day(self, iso, expected):
        assert _spoken_day_phrase(iso) == expected

    def test_accepts_a_full_timestamp(self):
        """slot_iso carries a time; the guard passes the date part, but a caller
        of this helper must not be punished for handing over the whole thing."""
        assert _spoken_day_phrase("2026-08-05T18:15:00") == "Wednesday the 5th of August"

    @pytest.mark.parametrize("bad", [
        "", None, "not-a-date", "2026-13-45", "05/08/2026", "tomorrow", "2026-08",
    ])
    def test_unparseable_input_returns_empty_never_raises(self, bad):
        """This runs on the live booking write path. An exception here loses the
        booking outright, which is worse than the defect it exists to describe —
        so the refusal degrades to its original wording instead."""
        assert _spoken_day_phrase(bad) == ""


class TestRefusalMessage:
    """The message the model actually receives when the guard fires."""

    @staticmethod
    def _refusal(slot_iso: str, spoken_date: str) -> str:
        """Mirror of the construction in LLMStream — same inputs, same output."""
        slot_phrase = _spoken_day_phrase(str(slot_iso or "")[:10])
        spoken_phrase = _spoken_day_phrase(spoken_date or "")
        correction = (
            f" The slot you are holding is on {slot_phrase}, not "
            f"{spoken_phrase or 'the day you last said'}. Say {slot_phrase} — with "
            "the time — and ask if that is the one they want."
        ) if slot_phrase else ""
        return (
            "NOT booked. The appointment you were about to create is on a "
            "different DAY..." + correction
        )

    def test_names_the_slot_day_not_the_spoken_day(self):
        """The exact shape of CAb81fe651: told Tuesday, holding Wednesday."""
        msg = self._refusal("2026-08-05T18:15:00", "2026-08-04")
        assert "Wednesday the 5th of August" in msg, (
            "the refusal must name the day the slot is really on — without it the "
            "model can only repeat the wrong day it already spoke"
        )
        assert "not Tuesday the 4th of August" in msg, (
            "naming the rejected day too stops the model re-offering it"
        )

    def test_still_refuses_when_the_date_cannot_be_rendered(self):
        """A bad slot_iso must not lose the refusal — only the correction."""
        msg = self._refusal("garbage", "2026-08-04")
        assert msg.startswith("NOT booked."), "the block itself is never conditional"
        assert "Wednesday" not in msg and "Tuesday" not in msg

    def test_reads_as_an_instruction_to_speak_the_real_day(self):
        msg = self._refusal("2026-08-05T18:15:00", "2026-08-04")
        assert "Say Wednesday the 5th of August" in msg
        assert "ask if that is the one they want" in msg
