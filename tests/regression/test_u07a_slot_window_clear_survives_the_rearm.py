"""U-07-a — the write-CTA clear must survive connection.py's post-turn re-arm.

`v3_awaiting_slot_selection` had two writers that disagreed, and the later one
won:

  * connection.py, on the caller's reply — "the slot selection window has
    closed" — pops the FLAG and deliberately keeps `v3_dtmf_slot_map` so a
    keypad press still resolves.
  * connection.py, after `run_turn` — re-derives the flag from the map: a map
    present sets it True again.

`_clear_slot_window_after_write_cta` (llm_stream, B1.2) ran between the two and
keyed off the flag. On the one turn it exists for — the caller has just picked a
day, so the flag is already popped, and Susie is now speaking the move CTA — it
early-returned, left the map in place, and the re-arm put the flag straight
back. The clear was dead code that read as live protection.

Observed on `CA3eccc7c153bb92cc8142f625dfcc5414` (jv_v2, build 66dd7a1a12bd):
the watchdog logged `slot_selection_grace (v3_awaiting_slot_selection)` eight
seconds after the CTA turn had supposedly cleared it. That call still passed,
on `36a7e5b`'s family-aware re-ask wording — a separate mechanism, still
load-bearing, deliberately not touched here.

The fix: the window is the MAP. Close it by dropping the map, which is what the
one owner reads.
"""
from __future__ import annotations

from app.media_streams import llm_stream as ls
from app.media_streams.config import F_LAST_BOT_PROMPT, F_LAST_QUESTION


_MOVE_CTA = (
    "Just to confirm — I'm moving your appointment to Friday the 21st of "
    "August at 6:45pm. Shall I go ahead and move it for you?"
)
_SLOT_OFFER = "I've got Thursday or Friday — which of those suits you?"


def _rearm(session: dict) -> None:
    """connection.py's post-turn block, reduced to the rule it enforces.

    Mirrored rather than imported: the real block sits ~200 lines into
    `_run_v3_loop` behind a live websocket. What is asserted below is only that
    the clear survives THIS rule, so the rule is all that needs standing in —
    and it is stated in one line at the re-arm site.
    """
    if session.get("v3_dtmf_slot_map"):
        session["v3_awaiting_slot_selection"] = True
    else:
        session.pop("v3_awaiting_slot_selection", None)
        session.pop("v3_slot_dtmf_active", None)


def _session_after_caller_picked_a_day(**extra) -> dict:
    """The A9b state: flag already popped by the caller-is-responding branch,
    map still live, stamp from the turn that presented the slots."""
    s = {
        # "v3_awaiting_slot_selection" absent — popped on the caller's reply
        "v3_dtmf_slot_map": {"1": "Thursday 20th", "2": "Friday 21st"},
        "v3_slot_dtmf_active": True,
        "v3_slot_map_armed_turn": 9,
        "turn_count": 10,
    }
    s.update(extra)
    return s


def test_write_cta_clear_is_not_undone_by_the_rearm():
    session = _session_after_caller_picked_a_day(
        **{F_LAST_BOT_PROMPT: _MOVE_CTA, F_LAST_QUESTION: _MOVE_CTA}
    )

    assert ls._clear_slot_window_after_write_cta(session) is True
    assert session.get("v3_dtmf_slot_map") is None

    _rearm(session)

    # The whole point: still closed on the other side of the re-arm.
    assert session.get("v3_awaiting_slot_selection") is None, (
        "the re-arm resurrected the slot window — silence will ask for a day "
        "again instead of re-asking the move"
    )


def test_ordinary_slot_offer_still_survives_the_rearm():
    """The inverse must hold, or the fix would close every window."""
    session = _session_after_caller_picked_a_day(
        **{F_LAST_BOT_PROMPT: _SLOT_OFFER, F_LAST_QUESTION: _SLOT_OFFER}
    )

    assert ls._clear_slot_window_after_write_cta(session) is False
    assert session["v3_dtmf_slot_map"] == {"1": "Thursday 20th", "2": "Friday 21st"}

    _rearm(session)

    assert session["v3_awaiting_slot_selection"] is True


def test_window_armed_by_this_same_reply_is_still_left_open():
    """The guard the clear already had must not be widened away.

    One reply can list the options AND ask the CTA. Dropping the map there
    leaves the caller unable to pick by voice or keypad.
    """
    session = _session_after_caller_picked_a_day(
        v3_slot_map_armed_turn=10,  # == turn_count: armed by the reply being spoken
        **{F_LAST_BOT_PROMPT: _MOVE_CTA, F_LAST_QUESTION: _MOVE_CTA},
    )

    assert ls._clear_slot_window_after_write_cta(session) is False
    assert session["v3_dtmf_slot_map"] == {"1": "Thursday 20th", "2": "Friday 21st"}

    _rearm(session)

    assert session["v3_awaiting_slot_selection"] is True


def test_no_window_at_all_is_still_a_no_op():
    session = {F_LAST_BOT_PROMPT: _MOVE_CTA, F_LAST_QUESTION: _MOVE_CTA}
    assert ls._clear_slot_window_after_write_cta(session) is False
