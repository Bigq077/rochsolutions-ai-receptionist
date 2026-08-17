"""B1.2 — write CTA must close the slot-selection window.

CAba5b162932ff73cc9c5b847f8e86aeeb (A9b withheld reschedule): Susie asked the
move CTA, the caller said yep, move likely landed, then silence re-asked
*"Still with you — which of those days suits you?"* because
`v3_awaiting_slot_selection` was still true.

Reschedule never asks for a name after the slot pick, so Spec J never clears
the flag. Speaking the write CTA must.
"""
from __future__ import annotations

import pytest

from app.media_streams import connection as c
from app.media_streams import llm_stream as ls
from app.media_streams.config import F_LAST_BOT_PROMPT, F_LAST_QUESTION


_MOVE_CTA = (
    "Just to confirm — I'm moving your appointment to Monday the 10th of August "
    "at half past four. Shall I go ahead and move it for you?"
)
_BOOKING_CTA = "So that's Tom, Tuesday at ten — shall I go ahead and book that in?"
_SLOT_OFFER = "I've got Thursday or Friday — which of those suits you?"


def _session_with_slot_window(**extra):
    s = {
        "v3_awaiting_slot_selection": True,
        "v3_dtmf_slot_map": {"1": "Thursday", "2": "Friday"},
        "v3_slot_dtmf_active": True,
    }
    s.update(extra)
    return s


@pytest.mark.parametrize("cta", [_MOVE_CTA, _BOOKING_CTA])
def test_write_cta_clears_slot_selection_flag(cta):
    session = _session_with_slot_window(
        **{F_LAST_BOT_PROMPT: cta, F_LAST_QUESTION: cta}
    )
    assert ls._clear_slot_window_after_write_cta(session) is True
    assert session.get("v3_awaiting_slot_selection") is None
    assert session.get("v3_dtmf_slot_map") is None
    assert session.get("v3_slot_dtmf_active") is None


def test_ordinary_slot_offer_leaves_the_window_alone():
    session = _session_with_slot_window(
        **{F_LAST_BOT_PROMPT: _SLOT_OFFER, F_LAST_QUESTION: _SLOT_OFFER}
    )
    assert ls._clear_slot_window_after_write_cta(session) is False
    assert session["v3_awaiting_slot_selection"] is True
    assert session["v3_dtmf_slot_map"] == {"1": "Thursday", "2": "Friday"}


def test_no_flag_is_a_no_op():
    session = {F_LAST_BOT_PROMPT: _MOVE_CTA, F_LAST_QUESTION: _MOVE_CTA}
    assert ls._clear_slot_window_after_write_cta(session) is False


def test_silence_prefers_write_cta_over_stale_slot_flag():
    """Defence in depth: even if the flag leaked, silence must not ask for a day.

    Calls `_write_cta_reask_phrase` rather than re-typing the branch. An earlier
    version of this test mirrored the `if` by hand and asserted the literal
    "Still with you — shall I go ahead?" — a string 36a7e5b then deleted for
    being booking-shaped on a reschedule. A hand-mirrored branch keeps passing
    while the code it claims to cover says something else entirely.
    """
    session = _session_with_slot_window(
        **{F_LAST_BOT_PROMPT: _MOVE_CTA, F_LAST_QUESTION: _MOVE_CTA}
    )
    assert c._write_cta_outstanding(session) is True
    phrase = c._write_cta_reask_phrase(session)
    assert phrase, "a write CTA is outstanding — silence must re-ask it"
    assert "days" not in phrase
    # And it must be the MOVE re-ask, since a move CTA is what is outstanding.
    assert ls._move_confirmation_asked(phrase) is True
