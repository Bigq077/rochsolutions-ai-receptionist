"""
"Press 1 to speak to Mark" must expire, or it eats the caller's slot choice.

The theorem_v3 greeting is:

    "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark
     directly press 1, otherwise how can I help you today?"

`v3_intro_dtmf_active` is set True once, when that greeting is delivered. It
was cleared in exactly one place: the intro branch of the digit handler, i.e.
only when a digit actually arrived. Nothing else ended the window, so the flag
survived the entire call.

That branch also sits ABOVE the slot handler in `_handle_dtmf`, which is a
chain of early returns. So for the rest of the call:

  * a caller pressing 1 to choose the first slot off a numbered list was
    transferred out of their own booking;
  * a 2 or a 3 hit `digit == "1"` → False, fell through to `return`, and
    vanished with no reply at all.

Two independent closes, because either alone leaves a hole:

  1. the window closes on the caller's first words — answering the offer with
     speech declines it, and that is the common case;
  2. the branch will not fire while a slot map or the location keypad is live —
     if a numbered question is on the table, the digit belongs to it.

Companion to test_transfer_promise_requires_target.py, which covers what
happens when the transfer IS attempted but no leg can be placed. This file
covers when it should not be attempted at all.
"""

import inspect

import pytest

from app.media_streams import connection as c


def _dtmf_handler_source() -> str:
    return inspect.getsource(c.WebSocketCallHandler._handle_dtmf)


def _intro_branch() -> str:
    """The intro-DTMF branch, up to the slot handler that follows it."""
    src = _dtmf_handler_source()
    start = src.index('if (\n                self.session.get("v3_intro_dtmf_active")')
    end = src.index("theorem_v3 slot / time selection", start)
    return src[start:end]


# ── close 1: the caller's first words end the window ───────────────────────

def test_speaking_closes_the_intro_window():
    """
    Without this the flag is set once at the greeting and never cleared except
    by a digit — so it outlives every question that follows.
    """
    src = inspect.getsource(c)
    assert 'self.session.pop("v3_intro_dtmf_active", None)' in src, (
        "nothing closes the intro DTMF window on caller speech"
    )


def test_the_window_closes_on_the_transcript_path_not_somewhere_incidental():
    """
    It must sit on the path EVERY transcript takes — not inside a branch only
    some utterances reach — or the window stays open for the callers who never
    happen to hit that branch.

    Anchored on the `transcript:` log line, which every caller utterance
    produces, rather than on the slot-window close: there are five
    `v3_awaiting_slot_selection` pop sites in this module and `.index()` finds
    the wrong one.
    """
    src = inspect.getsource(c)
    intro_close = src.index('self.session.pop("v3_intro_dtmf_active", None)')
    transcript_log = src.index('"[ms_conn v3] transcript: %r"')

    # Just before the transcript is logged, alongside the other end-of-turn
    # window closes — not hundreds of lines away in some unrelated branch.
    assert intro_close < transcript_log, (
        "the intro window closes after the transcript is dispatched"
    )
    assert transcript_log - intro_close < 800, (
        "the intro close has drifted away from the common transcript path"
    )

    # And it is on the same path as the slot-window close it accompanies.
    slot_pops = [
        i for i in range(len(src))
        if src.startswith('self.session.pop("v3_awaiting_slot_selection", None)', i)
    ]
    nearest = max(p for p in slot_pops if p < intro_close)
    assert intro_close - nearest < 800


# ── close 2: a live numbered question outranks the intro offer ─────────────

def test_intro_branch_yields_to_a_live_slot_map():
    branch = _intro_branch()
    assert 'not self.session.get("v3_dtmf_slot_map")' in branch, (
        "pressing 1 to pick slot 1 would still transfer the caller to Mark"
    )


def test_intro_branch_yields_to_the_location_keypad():
    branch = _intro_branch()
    assert 'not self.session.get("v3_awaiting_location_dtmf")' in branch


def test_intro_branch_still_transfers_when_it_genuinely_is_the_intro():
    """Guard against a fix that disables press-1 altogether."""
    branch = _intro_branch()
    assert 'digit == "1"' in branch
    assert "_on_transfer_request" in branch
    assert "transfer_requested_by_caller" in branch


# ── ordering: the branch order that made this reachable ───────────────────

def test_intro_branch_still_precedes_the_slot_handler():
    """
    Documents WHY the guard is a condition rather than a reordering: the
    branch genuinely does come first, and this handler is a chain of early
    returns where moving a block is riskier than gating it. If someone later
    reorders them, the guard is harmless — but this test records the shape the
    guard was written against.
    """
    src = _dtmf_handler_source()
    assert src.index("v3_intro_dtmf_active") < src.index("v3_dtmf_slot_map")


def test_the_flag_is_armed_only_at_the_greeting():
    """One arming site. More than one means a second lifetime to reason about."""
    src = inspect.getsource(c)
    armings = src.count('self.session["v3_intro_dtmf_active"] = True')
    assert armings == 1, f"expected one arming site, found {armings}"
