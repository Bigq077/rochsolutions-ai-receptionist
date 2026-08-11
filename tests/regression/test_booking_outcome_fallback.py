"""
Regression: a booking that was WRITTEN must never end in "could you say that
again?".

CAd8868396 (Vital Edge, 2026-08-11). `book_appointment` returned

    {"success": true, "provisional": true,
     "booked_slot": "Tuesday 18 August at 12:00",
     "note": "PROVISIONAL request logged and owner notified.
              Tell the caller it is NOT confirmed …"}

The next tool-loop iteration stalled 21 seconds on the provider, retried, and
emitted nothing. The caller therefore heard the deferred Gate-5 fallback —
"Sorry, I didn't quite catch that — could you say that again?" — said "okay
thank you bye-bye", and hung up. The request was sitting in the practitioner's
diary and they were never told.

The call was then recorded as `outcome=abandoned`, because nothing had been
spoken for the summariser to see. One stall, two wrong records: the caller's
understanding and the clinic's.

The outcome is known DETERMINISTICALLY at that point — it is in the tool result.
So the fallback speaks it.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams.llm_stream import _booking_outcome_line, _note_write_result

CONFIRMED = {"success": True, "booked_slot": "Friday 14 August at 11:00"}
PROVISIONAL = {
    "success": True,
    "provisional": True,
    "booked_slot": "Tuesday 18 August at 12:00",
}


# ---------------------------------------------------------------------------
# The sentence
# ---------------------------------------------------------------------------
def test_a_confirmed_booking_is_stated_as_booked():
    line = _booking_outcome_line(CONFIRMED)
    assert "Friday 14 August at 11:00" in line
    assert line.strip().endswith(".")


def test_a_provisional_booking_is_NOT_stated_as_confirmed():
    """The distinction this whole helper exists for.

    Telling a caller they are "booked in" when the practitioner has not
    accepted is a false confirmation — the failure the write-guard family
    exists to prevent. It must say the request is in AND that it is not
    confirmed yet.
    """
    line = _booking_outcome_line(PROVISIONAL)
    assert "Tuesday 18 August at 12:00" in line
    assert "not confirmed" in line.lower()
    for claim in ("that's booked in", "you're booked", "you're down for"):
        assert claim not in line.lower(), (
            f"a provisional request was announced as a confirmed booking: {line!r}"
        )


def test_neither_sentence_promises_a_text():
    """SMS is env-gated per service. A promise made here cannot check it, and
    a promised text that never arrives is its own defect."""
    for r in (CONFIRMED, PROVISIONAL):
        low = _booking_outcome_line(r).lower()
        for promise in ("text", "sms", "message"):
            assert promise not in low, f"fallback promised a {promise}"


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"success": False, "booked_slot": "Friday 14 August at 11:00"},
        {"success": True},                       # no slot to speak
        {"success": True, "booked_slot": "   "},  # blank slot
        {"status": "duration_choice_required"},   # a gate refusal, no success key
        None,
        "not a dict",
    ],
)
def test_no_sentence_without_a_real_written_booking(result):
    """Fails to the ORDINARY fallback rather than a sentence with a hole in it.

    The `success is not True` test is deliberate, not `success is False`: every
    gate refusal returns no success key at all.
    """
    assert _booking_outcome_line(result) == ""


# ---------------------------------------------------------------------------
# The latch
# ---------------------------------------------------------------------------
def test_a_successful_booking_arms_the_fallback():
    session: dict = {}
    _note_write_result(session, "book_appointment", dict(PROVISIONAL))
    assert "Tuesday 18 August at 12:00" in session.get("_booking_outcome_unspoken", "")


def test_a_refused_booking_does_not_arm_it():
    session: dict = {}
    _note_write_result(
        session, "book_appointment",
        {"status": "duration_choice_required", "message": "…"},
    )
    assert not session.get("_booking_outcome_unspoken")


def test_it_is_consumed_at_most_once():
    """pop(), not get() — a second empty turn must not re-announce it."""
    src = inspect.getsource(ls)
    assert 'session.pop("_booking_outcome_unspoken"' in src


def test_it_is_cleared_every_turn():
    """Turn-scoped. Without the reset a farewell turn the model fumbles would
    re-announce a booking the caller was told about minutes ago."""
    src = inspect.getsource(ls)
    assert src.count('session.pop("_booking_outcome_unspoken"') >= 2, (
        "expected both the consume site and the per-turn reset"
    )


# ---------------------------------------------------------------------------
# The wiring — the helper existing is not the fix
# ---------------------------------------------------------------------------
def test_the_fallback_prefers_the_outcome_over_the_re_ask():
    src = inspect.getsource(ls)
    i_fallback = src.index("Sorry, I didn't quite catch that")
    i_outcome = src.index('session.pop("_booking_outcome_unspoken"')
    assert i_outcome > i_fallback, (
        "the outcome must OVERRIDE the re-ask, so it has to be read after the "
        "default is constructed"
    )
    assert "_gate5_fallback = _outcome" in src, (
        "the outcome is computed but never substituted — the caller would still "
        "be asked to repeat themselves after a successful booking"
    )
