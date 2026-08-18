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

`_rearm` here is `connection.py._derive_slot_window` itself, imported, not a
copy. An earlier version of this file mirrored that branch by hand — the same
mistake the B1.2 test file calls out in its own docstring, and it would have let
someone change the ownership rule with these tests still green.
"""
from __future__ import annotations

from app.media_streams import llm_stream as ls
from app.media_streams.connection import _derive_slot_window as _rearm
from app.media_streams.config import F_LAST_BOT_PROMPT, F_LAST_QUESTION


_MOVE_CTA = (
    "Just to confirm — I'm moving your appointment to Friday the 21st of "
    "August at 6:45pm. Shall I go ahead and move it for you?"
)
_SLOT_OFFER = "I've got Thursday or Friday — which of those suits you?"


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


def test_the_owner_decides_the_flag_in_both_directions():
    """The ownership rule itself — the reason `_rearm` is imported, not copied.

    Added after a revert-probe: sabotaging `_derive_slot_window` so a closed
    window kept its flag left every test above green, because they all start
    from a session whose flag is ALREADY absent (popped on the caller's reply).
    They pin the composition — clear, then re-arm — and not the rule. Without
    this case the import buys nothing that a hand-copy would not.
    """
    # Map present → window open.
    open_window = {"v3_dtmf_slot_map": {"1": "Thursday", "2": "Friday"}}
    assert _rearm(open_window) is True
    assert open_window["v3_awaiting_slot_selection"] is True

    # Map gone → window closed, and the flag must not outlive it. This is the
    # direction that makes dropping the map an effective way to close a window.
    closed_window = {
        "v3_awaiting_slot_selection": True,
        "v3_slot_dtmf_active": True,
        "v3_dtmf_slot_context": "day",
    }
    assert _rearm(closed_window) is False
    assert closed_window.get("v3_awaiting_slot_selection") is None
    assert closed_window.get("v3_slot_dtmf_active") is None
    assert closed_window.get("v3_dtmf_slot_context") is None
