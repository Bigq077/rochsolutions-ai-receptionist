"""B1.2 narrowing — a slot window armed by THIS reply must survive its own CTA.

3c40057 made the write-CTA turn authoritative for closing the slot-selection
window, which is right for the defect it was written for (A9b, `CAba5b1629`):
options on one turn, the caller picks, and the CTA turn afterwards leaves
`v3_awaiting_slot_selection` standing so silence re-asks for a day.

But one turn can do both. Susie composes offers and CTAs in a single reply:

    "I can move that for you. Number 1 - Monday the 24th at 9am,
     Number 2 - Tuesday the 25th at 2pm. Shall I go ahead and move it?"

`_flush_slot_buf` arms the DTMF map and the selection flag off that text while
it is still streaming; a few lines later in the SAME `run_turn`,
`_clear_slot_window_after_write_cta` matched the move CTA in the same sentence
and popped both. The caller heard two options and could select neither — not
by voice (the flag was gone) and not by keypad (the map the "keypad" fallback
arms from was gone with it).

These tests drive the two real functions over one string, in the order
`run_turn` calls them, rather than asserting on a hand-built session — the
arm-then-clear ordering IS the defect, so a test that skips the arming step
cannot see it.
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams.config import F_LAST_BOT_PROMPT, F_LAST_QUESTION

_COMBINED_MOVE = (
    "I can move that for you. Number 1 - Monday the 24th at 9am, "
    "Number 2 - Tuesday the 25th at 2pm. Shall I go ahead and move it?"
)
_COMBINED_BOOKING = (
    "Number 1 - Monday the 24th at 9am, Number 2 - Tuesday the 25th at 2pm. "
    "Shall I go ahead and book that in?"
)
_OFFER_ONLY = (
    "Number 1 - Monday the 24th at 9am, Number 2 - Tuesday the 25th at 2pm. "
    "Which of those suits you?"
)
_CTA_ONLY = (
    "That's Monday the 24th at 9am with Jonathan. Shall I go ahead and move it?"
)


async def _speak(session: dict, reply: str) -> bool:
    """Run one turn's worth of `run_turn` state updates over `reply`.

    Mirrors the real order: `_flush_slot_buf` arms the window mid-stream, then
    the prompt fields are written, then the write-CTA cleanup runs. Returns
    whether the cleanup cleared the window.
    """
    buf: asyncio.Queue = asyncio.Queue()
    buf.put_nowait(reply)
    await ls.LLMStream._flush_slot_buf(buf, asyncio.Queue(), session)
    session[F_LAST_BOT_PROMPT] = reply[:200]
    session[F_LAST_QUESTION] = ls._question_from_response(reply)
    return ls._clear_slot_window_after_write_cta(session)


@pytest.mark.parametrize("reply", [_COMBINED_MOVE, _COMBINED_BOOKING])
async def test_window_armed_by_this_reply_survives_its_own_cta(reply):
    session = {"turn_count": 4}
    cleared = await _speak(session, reply)

    assert cleared is False
    assert session.get("v3_awaiting_slot_selection") is True, (
        "the caller just heard two options — they must still be able to say one"
    )
    assert session.get("v3_dtmf_slot_map"), (
        "the keypad fallback arms from this map on a later turn; dropping it "
        "here strands a caller whose speech the ASR cannot make out"
    )


@pytest.mark.parametrize("reply", [_COMBINED_MOVE, _COMBINED_BOOKING])
async def test_the_premise_holds_arming_and_the_cta_are_the_same_sentence(reply):
    """Pin the premise, or the tests above pass for the wrong reason.

    If the slot map ever stops being extracted from these strings, or the CTA
    predicates stop matching them, the assertions above would hold trivially on
    a session where nothing was ever armed. Then the real defect could come
    back under a green suite.
    """
    session = {"turn_count": 4}
    buf: asyncio.Queue = asyncio.Queue()
    buf.put_nowait(reply)
    await ls.LLMStream._flush_slot_buf(buf, asyncio.Queue(), session)

    assert len(session.get("v3_dtmf_slot_map") or {}) >= 2, (
        "premise: this reply must arm a real slot map"
    )
    assert session.get("v3_awaiting_slot_selection") is True

    session[F_LAST_BOT_PROMPT] = reply[:200]
    session[F_LAST_QUESTION] = ls._question_from_response(reply)
    assert (
        ls._cta_asked(session, ls._move_confirmation_asked)
        or ls._cta_asked(session, ls._booking_confirmation_asked)
    ), "premise: the same reply must also read as an outstanding write CTA"


async def test_a9b_still_clears_on_the_turn_after_the_offer():
    """The defect 3c40057 fixed must stay fixed — this is the whole point.

    Options on turn 4, CTA on turn 5. The window is a leftover by then, so it
    is cleared exactly as before and silence re-asks the confirmation rather
    than "which of those days suits you?".
    """
    session = {"turn_count": 4}
    assert await _speak(session, _OFFER_ONLY) is False
    assert session["v3_awaiting_slot_selection"] is True

    session["turn_count"] = 5
    session[F_LAST_BOT_PROMPT] = _CTA_ONLY[:200]
    session[F_LAST_QUESTION] = ls._question_from_response(_CTA_ONLY)

    assert ls._clear_slot_window_after_write_cta(session) is True
    assert session.get("v3_awaiting_slot_selection") is None
    assert session.get("v3_dtmf_slot_map") is None
    assert session.get("v3_slot_map_armed_turn") is None, (
        "the stamp must not outlive the window it describes"
    )


async def test_stamp_records_the_turn_that_armed_the_window():
    session = {"turn_count": 7}
    await _speak(session, _OFFER_ONLY)
    assert session.get("v3_slot_map_armed_turn") == 7


async def test_a_turn_with_no_numbered_options_drops_the_stamp():
    """`_flush_slot_buf` clears a stale map itself; the stamp must go with it."""
    session = {"turn_count": 4}
    await _speak(session, _OFFER_ONLY)
    assert session.get("v3_slot_map_armed_turn") == 4

    session["turn_count"] = 5
    await _speak(session, "Righto, that's booked in for you.")
    assert session.get("v3_dtmf_slot_map") is None
    assert session.get("v3_slot_map_armed_turn") is None
