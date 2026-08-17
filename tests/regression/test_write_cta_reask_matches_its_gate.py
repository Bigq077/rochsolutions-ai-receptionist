"""
Regression: the silence re-ask for an outstanding write CTA must satisfy the
gate for ITS OWN family.

B1.2 (`3c40057`) replaced the day question with a generic
*"Still with you — shall I go ahead?"* when a write CTA was outstanding. That
phrase satisfies `_booking_confirmation_asked` but **not**
`_move_confirmation_asked` — so on the RESCHEDULE flow the fix was written for
(A9b, `CAba5b1629`) it overwrote a valid move CTA with one the move gate cannot
recognise:

    move CTA spoken -> caller silent -> re-ask "shall I go ahead?"
      -> caller says "yes"
        -> _move_confirmation_asked False: consent dropped, move never happens
        -> _booking_confirmation_asked True: the BOOKING gate armed mid-reschedule

Third instance of B-36 cause 1 / `CA23199d08`: a single-phrasing gate meeting a
sentence composed somewhere else.

These tests assert the property that actually matters — **the re-ask satisfies
the predicate for the outstanding family** — not merely that it stopped being
the day question. A test for "not the day question" passes on the broken
version.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _write_cta_reask_phrase
from app.media_streams.llm_stream import (
    _booking_confirmation_asked,
    _cancel_retention_asked,
    _move_confirmation_asked,
)

MOVE_CTA = "shall I go ahead and move it?"
CANCEL_CTA = "would you like to reschedule this appointment, or cancel it altogether?"
BOOK_CTA = "shall I go ahead and book that in?"


def _sess(last_bot_prompt: str) -> dict:
    return {"last_bot_prompt": last_bot_prompt, "last_question": last_bot_prompt}


# ---------------------------------------------------------------------------
# The property: the re-ask is recognised by its own family's gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cta,predicate,family",
    [
        (MOVE_CTA, _move_confirmation_asked, "move"),
        (CANCEL_CTA, _cancel_retention_asked, "cancel"),
        (BOOK_CTA, _booking_confirmation_asked, "booking"),
    ],
)
def test_reask_satisfies_the_outstanding_families_gate(cta, predicate, family):
    reask = _write_cta_reask_phrase(_sess(cta))
    assert reask, f"no re-ask produced for an outstanding {family} CTA"
    assert predicate(reask), (
        f"the {family} re-ask {reask!r} is not recognised by its own gate. The "
        f"caller's 'yes' after this prompt would be dropped, and the write "
        f"would never happen."
    )


def test_the_move_case_is_the_regression():
    """The specific failure. A generic 'shall I go ahead?' passes the booking
    gate and fails the move gate — this pins that we no longer emit it for a
    reschedule."""
    reask = _write_cta_reask_phrase(_sess(MOVE_CTA))
    assert _move_confirmation_asked(reask)
    assert reask != "Still with you — shall I go ahead?"


def test_a_generic_go_ahead_would_have_failed_the_move_gate():
    """Guards the premise. If this ever starts passing, the move gate has been
    widened and these tests have gone vacuous — delete them or re-aim them."""
    assert _booking_confirmation_asked("Still with you — shall I go ahead?")
    assert not _move_confirmation_asked("Still with you — shall I go ahead?")


# ---------------------------------------------------------------------------
# Ordering — move/cancel are tested before booking on purpose
# ---------------------------------------------------------------------------
def test_move_wins_over_booking_when_both_match():
    """The move and cancel phrasings also satisfy the BOOKING predicate, so a
    booking-first ordering would hand back the booking wording for every family
    and reintroduce the bug."""
    reask = _write_cta_reask_phrase(_sess(MOVE_CTA))
    assert "move it" in reask.lower()


def test_cancel_wins_over_booking_when_both_match():
    reask = _write_cta_reask_phrase(_sess(CANCEL_CTA))
    assert _cancel_retention_asked(reask)


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "last_bot_prompt",
    ["what day suits you?", "could I take your first name and surname?", "", None],
)
def test_no_reask_when_no_write_cta_is_outstanding(last_bot_prompt):
    """Returns empty so the caller keeps whatever prompt it would otherwise use
    — the slot question must still be asked when a slot really is awaited."""
    assert _write_cta_reask_phrase(_sess(last_bot_prompt or "")) == ""


def test_never_raises_on_a_junk_session():
    for s in ({}, {"last_bot_prompt": None}, None):
        assert _write_cta_reask_phrase(s) == ""


def test_both_silence_paths_use_the_helper():
    """The helper existing is not the fix. Both re-ask sites — SilenceHandler
    and the watchdog in WebSocketCallHandler — must route through it."""
    import inspect

    from app.media_streams import connection as conn

    src = inspect.getsource(conn)
    assert src.count("_write_cta_reask_phrase(") >= 3, (
        "expected the definition plus BOTH call sites"
    )
    assert "Still with you — shall I go ahead?\"" not in src, (
        "the generic booking-shaped re-ask is still emitted somewhere"
    )
