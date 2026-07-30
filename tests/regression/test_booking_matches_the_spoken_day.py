# tests/regression/test_booking_matches_the_spoken_day.py
"""
C1 write-guard — never write a booking on a day the caller was not told.

CA5c4fb14f (30 Jul 2026): she said "So that's John, Tuesday the 4th of August at
seven in the evening — shall I go ahead and book that in?", he said yes, she said
"All booked — you're in for Tuesday the 4th of August", and the calendar event
was created for 2026-08-05T19:00 — a Wednesday. He would have arrived to nothing.

WHY THIS HAS TO BE AT THE WRITE
Every downstream check is consistent, because the booking matches the slot. Only
the SPEECH disagrees, and by the time book_appointment fires the words have
already been said. The readback date enforcement in turn_handler cannot help: it
copies v3_confirmed_slot_phrase forward, so it propagates the wrong day rather
than correcting it. And steering the model will not hold — it free-texts the
spoken phrase from a multi-day available_days still in its context, which is how
7c140f4 (confining the deterministic batch to one day) failed to close this: that
path is unreachable once name and phone are collected.

So the guard compares the two facts at the only moment both exist: the ISO about
to be written, against the date last spoken in a commitment sentence.

THE OVER-FIRE RISK IS THE REAL DESIGN CONSTRAINT
Blocking a legitimate booking is worse than the defect — this codebase has
abandoned a completed booking that way before (Gate 5c, 2026-06-12). So:

  * only COMMITMENT sentences set the reference date. An availability list names
    several dates the caller never agreed to; latching onto one would block
    correct bookings.
  * the reference date is overwritten, never accumulated, so a change of mind
    cannot leave a stale date behind that blocks the new booking.
  * every uncertain case books: nothing spoken yet, missing slot_iso, unparseable
    slot_iso. The guard only fires on a mismatch it is sure of.
  * the re-steer is a QUESTION, so the call continues; the model states the real
    day, the caller confirms, and the booking proceeds on the next turn.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import (
    _note_spoken_slot_date,
    _slot_date_disagrees_with_speech,
    _spoken_slot_date,
)

READBACK = ("So that's John, Tuesday the 4th of August at quarter to six in the "
            "evening — shall I go ahead and book that in?")
ALL_BOOKED = ("All booked — you're in for Tuesday the 4th of August at seven in "
              "the evening. We'll see you then — take care.")
NAME_READBACK = ("So that's Tuesday the 4th of August at five in the evening — "
                 "could I take your first name and surname?")
AVAILABILITY = ("Tuesday 4th August — Number 1, five in the evening. Number 2, "
                "quarter to six in the evening. Any of those work?")
NO_SLOTS = "Wednesday the 5th of August doesn't have any morning slots, I'm afraid."


# ── what counts as "the date the caller was told" ──────────────────────────
@pytest.mark.parametrize("text,expected", [
    (READBACK, "2026-08-04"),
    (ALL_BOOKED, "2026-08-04"),
    (NAME_READBACK, "2026-08-04"),
    (AVAILABILITY, None),   # offered, not agreed
    (NO_SLOTS, None),       # discussed, not agreed
])
def test_only_commitment_sentences_set_the_reference_date(text, expected):
    assert _spoken_slot_date(text, 2026) == expected


def test_availability_list_does_not_overwrite_the_agreed_date():
    """The list names dates the caller never acted on. If one of those became the
    reference, a correct booking would be blocked."""
    session = {}
    _note_spoken_slot_date(session, READBACK)
    _note_spoken_slot_date(session, AVAILABILITY)
    assert session["last_spoken_slot_date"] == "2026-08-04"


# ── the real defect ────────────────────────────────────────────────────────
def test_blocks_the_real_ca5c4fb14f_booking():
    session = {}
    _note_spoken_slot_date(session, READBACK)
    assert _slot_date_disagrees_with_speech(
        {"slot_iso": "2026-08-05T19:00:00"}, session) is True


def test_allows_the_booking_the_caller_actually_agreed_to():
    session = {}
    _note_spoken_slot_date(session, READBACK)
    assert _slot_date_disagrees_with_speech(
        {"slot_iso": "2026-08-04T17:45:00"}, session) is False


def test_time_may_differ_only_the_day_is_guarded():
    """Scope is the DAY. A same-day time difference is not this defect, and
    blocking it would fire on every legitimate slot change within a day."""
    session = {}
    _note_spoken_slot_date(session, READBACK)
    assert _slot_date_disagrees_with_speech(
        {"slot_iso": "2026-08-04T09:15:00"}, session) is False


# ── the over-fire cases: these MUST book ───────────────────────────────────
def test_change_of_mind_does_not_leave_a_stale_blocking_date():
    """The caller moves Tuesday -> Wednesday. The Wednesday booking must go
    through; a stale Tuesday reference here would abandon a real booking."""
    session = {}
    _note_spoken_slot_date(session, READBACK)
    _note_spoken_slot_date(session, "So that's John, Wednesday the 5th of August "
                                    "at seven in the evening — shall I go ahead "
                                    "and book that in?")
    assert session["last_spoken_slot_date"] == "2026-08-05"
    assert _slot_date_disagrees_with_speech(
        {"slot_iso": "2026-08-05T19:00:00"}, session) is False


@pytest.mark.parametrize("session,args,why", [
    ({}, {"slot_iso": "2026-08-05T19:00:00"}, "no commitment spoken yet"),
    ({"last_spoken_slot_date": "2026-08-04"}, {}, "no slot_iso"),
    ({"last_spoken_slot_date": "2026-08-04"}, {"slot_iso": ""}, "empty slot_iso"),
    ({"last_spoken_slot_date": "2026-08-04"}, {"slot_iso": "soon"}, "unparseable"),
    ({"last_spoken_slot_date": "2026-08-04"}, {"slot_iso": None}, "None slot_iso"),
    ({"last_spoken_slot_date": "2026-08-04"}, {"slot_iso": "next tuesday"}, "prose"),
])
def test_uncertain_cases_book_rather_than_block(session, args, why):
    assert _slot_date_disagrees_with_speech(args, session) is False, (
        f"guard fired on an uncertain case ({why}); blocking a real booking is "
        "worse than the defect this guard exists to prevent"
    )


def test_guard_never_raises_on_junk_session_state():
    """This runs on the live write path — an exception here loses the booking."""
    for bad in [None, "", 0, [], {"last_spoken_slot_date": None}]:
        session = bad if isinstance(bad, dict) else {"last_spoken_slot_date": bad}
        assert _slot_date_disagrees_with_speech({"slot_iso": "2026-08-05T19:00"},
                                                session) in (True, False)


def test_note_spoken_slot_date_tolerates_empty_speech():
    session = {}
    for text in ["", None, "   ", "Right — what's the appointment for?"]:
        _note_spoken_slot_date(session, text)
    assert "last_spoken_slot_date" not in session
