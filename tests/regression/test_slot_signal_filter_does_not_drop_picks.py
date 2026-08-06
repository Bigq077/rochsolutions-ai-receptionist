"""
A caller picking a slot must not be silently discarded.

While `v3_awaiting_slot_selection` is live, connection.py:~7739 discards any
transcript that _is_slot_selection_candidate() rejects and re-arms the silence
timer. The caller's turn is gone: they hear nothing, the watchdog eventually
re-asks, and enough of that strands them in the recovery ladder.

The test replaces a plain `word in _SLOT_SIGNALS` membership check that three
ordinary replies defeated:

  * trailing punctuation — AssemblyAI returns formatted text, so the single
    most common reply to a spoken slot list ("Three.") matched nothing at all
  * clock and ordinal forms — "3pm", "2:30", "the 21st". The last was the
    function's OWN docstring example of something that should pass.
  * weekend and relative days — "Saturday", "tomorrow", both already blessed
    as genuine scheduling answers by _SCHEDULING_SINGLES.

Direction matters here and the asymmetry is deliberate: a false negative costs
the caller their turn, a false positive costs one LLM call which then resolves
the utterance against the slots actually offered. Same reasoning the B-37 fix
records at the call site — dropping is the dangerous act.
"""

import pytest

from app.media_streams.connection import _is_slot_selection_candidate


# ── replies that must reach the LLM ────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    # bare picks
    "three", "THREE", "number three", "the second one", "first one",
    # punctuated — the regression that motivated this file
    "Three.", "Sunday.", "Thursday,", "nine o'clock.", "Two!",
    # clock times
    "3pm", "9am", "2:30", "2:30pm", "3 pm", "the 3pm one", "half past two",
    # date ordinals
    "the 21st", "the 3rd",
    # days that were valid answers everywhere except here
    "saturday", "sunday", "today", "tomorrow",
    # already worked — must keep working
    "thursday", "monday", "in the morning", "the afternoon one",
    "yes, three please",
])
def test_a_slot_pick_is_never_discarded(reply):
    assert _is_slot_selection_candidate(reply), (
        f"{reply!r} would be dropped and the caller's turn lost"
    )


# ── replies that legitimately carry no slot signal ─────────────────────────

@pytest.mark.parametrize("reply", [
    "with me", "suits me", "yes please", "sorry what", "can you repeat that",
    "I think so", "hmm", "",
])
def test_non_picks_still_re_arm(reply):
    """Guard against widening this into "everything passes"."""
    assert not _is_slot_selection_candidate(reply)


def test_punctuation_only_token_is_not_a_signal():
    assert not _is_slot_selection_candidate("... , !")


def test_that_one_passes_and_always_did():
    """
    Spec H lists 'that one' as a re-arm, but "one" is a slot signal, so it
    passed before this change too. Pinned so nobody "restores spec compliance"
    and starts dropping a caller who is picking the slot they were offered.
    """
    assert _is_slot_selection_candidate("that one")
