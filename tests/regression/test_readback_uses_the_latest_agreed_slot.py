# tests/regression/test_readback_uses_the_latest_agreed_slot.py
"""
The booking readback states the slot the caller last agreed to — it never asks
the model to pick one out of the conversation.

CA42486ff4 (31 Jul 2026, build 6f63057). The caller took Tuesday 6:30, gave his
name and number, then changed to Wednesday 6:15 and confirmed it. Susie then read
back "Tuesday the 4th of August at quarter past six" — Tuesday's DATE with
Wednesday's TIME, an appointment that existed on no calendar (Tuesday's offers
were 5:45 and 6:30; quarter past six was Wednesday-only). The write-guard caught
it and refused, so nothing wrong was booked, but the wrong day had already been
spoken and he hung up at 162 s.

Two causes, both here:

1. Once name and phone were in, the readback instruction said to fill the day
   "from the slot the caller already agreed to earlier in this conversation".
   That call had TWO agreements earlier in the conversation. The instruction was
   ambiguous by construction and the model composed the two together.

2. v3_confirmed_slot_phrase, which is injected verbatim when present, is captured
   on exactly one transition — the name request at the end of the slot flow. A
   caller who changes day AFTER giving their name never refreshes it, so it names
   the abandoned day and is treated as authoritative.

Newest agreement wins, in both. That is the same rule the write-guard is built
on, and it is what lets a caller change their mind and still get booked.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import (
    _note_spoken_slot_date,
    _phrase_date,
    _spoken_slot_phrase,
)

TUESDAY_READBACK = (
    "So that's Tuesday the 4th of August at half past six in the evening "
    "— could I take your first name and surname?"
)
WEDNESDAY_READBACK = (
    "So that's Sarah, Wednesday the 5th of August at quarter past six in the "
    "evening — shall I go ahead and book that in?"
)
# What she actually said on the call — the hybrid the guard refused.
THE_HYBRID = (
    "So that's Sarah Jenkins, Tuesday the 4th of August at quarter past six in "
    "the evening — shall I go ahead and book that in?"
)


class TestExtractingTheSpokenSlot:
    @pytest.mark.parametrize("spoken,expected", [
        (WEDNESDAY_READBACK, "Wednesday the 5th of August at quarter past six in the evening"),
        (TUESDAY_READBACK, "Tuesday the 4th of August at half past six in the evening"),
        (THE_HYBRID, "Tuesday the 4th of August at quarter past six in the evening"),
        ("So that's Sarah, Tuesday the 4th of August at seven in the evening "
         "— shall I go ahead and book that in?",
         "Tuesday the 4th of August at seven in the evening"),
    ])
    def test_captures_the_slot_and_stops_at_the_dash(self, spoken, expected):
        """It must not swallow "shall I go ahead and book that in?" — that would
        be read back to the caller verbatim."""
        assert _spoken_slot_phrase(spoken) == expected

    @pytest.mark.parametrize("spoken", [
        # An availability list names days the caller has NOT agreed to. Latching
        # one would make the readback state a slot nobody chose.
        "Tuesday 4th August — Number 1, quarter to six in the evening. "
        "Number 2, half past six in the evening. Any of those work?",
        "Wednesday 5th August — Number 1, half past five in the evening.",
        "Thanks Sarah — and your surname?",
        "Right — what's the appointment for?",
        "I've got you on 07502 211 207, is that the best number for the booking?",
        "",
    ])
    def test_only_commitment_sentences_are_captured(self, spoken):
        assert _spoken_slot_phrase(spoken) is None


class TestNewestAgreementWins:
    def test_changing_day_overwrites_the_earlier_agreement(self):
        """The exact sequence of CA42486ff4: Tuesday agreed, then Wednesday."""
        session: dict = {}
        _note_spoken_slot_date(session, TUESDAY_READBACK)
        assert session["last_spoken_slot_phrase"].startswith("Tuesday")

        _note_spoken_slot_date(session, WEDNESDAY_READBACK)
        assert session["last_spoken_slot_phrase"] == (
            "Wednesday the 5th of August at quarter past six in the evening"
        ), "the newest agreement must win — a stale one is how the wrong day is read back"
        assert session["last_spoken_slot_date"].endswith("-08-05")

    def test_a_non_commitment_turn_does_not_clear_the_agreement(self):
        """Name and phone turns sit between the agreement and the readback."""
        session: dict = {}
        _note_spoken_slot_date(session, WEDNESDAY_READBACK)
        for chatter in ("And your surname?",
                        "I've got you on 07502 211 207, is that the best number?",
                        "Thanks Sarah."):
            _note_spoken_slot_date(session, chatter)
        assert session["last_spoken_slot_phrase"].startswith("Wednesday the 5th")

    def test_phrase_and_date_always_describe_the_same_turn(self):
        session: dict = {}
        _note_spoken_slot_date(session, WEDNESDAY_READBACK)
        assert _phrase_date(session["last_spoken_slot_phrase"]) == \
            session["last_spoken_slot_date"]


class TestStaleConfirmedSlotIsRejected:
    """v3_confirmed_slot_phrase is injected verbatim, so a stale one is spoken as
    fact. It is only refreshed at the name request, which a later day change
    never revisits."""

    @staticmethod
    def _resolve(confirmed: str, last_spoken_date: str) -> str:
        """Mirror of the staleness test in LLMStream."""
        rb = (confirmed or "").strip()
        if rb and last_spoken_date:
            d = _phrase_date(rb)
            if d and d != last_spoken_date:
                return ""
        return rb

    def test_stale_tuesday_is_dropped_after_the_caller_moves_to_wednesday(self):
        assert self._resolve(
            "Tuesday the 4th of August at half past six in the evening",
            "2026-08-05",
        ) == "", "a confirmed phrase naming an abandoned day must not be injected"

    def test_current_phrase_is_kept(self):
        phrase = "Wednesday the 5th of August at quarter past six in the evening"
        assert self._resolve(phrase, "2026-08-05") == phrase

    def test_no_commitment_yet_leaves_the_confirmed_phrase_alone(self):
        """Nothing to compare against — the old behaviour must be untouched, or
        this becomes a regression on every call that never changes day."""
        phrase = "Tuesday the 4th of August at half past six in the evening"
        assert self._resolve(phrase, "") == phrase

    def test_an_unparseable_confirmed_phrase_is_kept(self):
        """Fail toward the existing behaviour, never toward dropping a slot we
        cannot prove is stale."""
        assert self._resolve("your usual time", "2026-08-05") == "your usual time"
