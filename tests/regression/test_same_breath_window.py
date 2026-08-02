# tests/regression/test_same_breath_window.py
"""B-18 (2 Aug 2026) — caller speech during a slow turn is discarded as a straggler.

Incident
--------
    llm_ttft_ms=13868  content_ttfa_ms=16404
    caller says "hello" into ~16s of silence
    [ms_lost] reason=same_breath_straggler

Mechanism
---------
The same-breath guard drops any utterance enqueued before `_last_turn_done_at`,
which is stamped in the `finally:` of the LLM turn — i.e. when *generation*
completes, not when the caller last heard anything. Its premise is stated in its
own comment:

    "A genuine reply is always enqueued AFTER the response audio plays
     (well after _last_turn_done_at), so this never drops a real answer."

That premise holds only while turns are fast. When a turn runs long — an
upstream LLM spike (B-19), a slow tool round-trip — the entire window in which
the caller can hear silence lies *before* `_last_turn_done_at`. So everything
they say during a slow turn satisfies "enqueued before the prior turn completed"
and is discarded as a same-breath straggler.

The failure mode is therefore self-reinforcing, and worst exactly when it hurts
most: the slower we are, the more reliably we throw away the caller's reaction
to our slowness. On the incident call that reaction was "hello" — the caller
checking whether anyone was still there — and dropping it produced more silence.

Fix
---
Bound the guard by the thing it is actually named after. A straggler is the tail
of one breath, so it arrives within a couple of seconds of the FINAL it trails.
The guard now requires both:

    * enqueued before the prior turn completed   (unchanged), AND
    * enqueued within _SAME_BREATH_WINDOW_S of the previous FINAL   (new)

This preserves every case the guard was written for — the 2026-06-12 split
"...that i go down to physiotherapy clinic" arrived ~0.7s after its head, and
the 2026-07-24 surname "rock" 696ms after "yeah so that will be quaint in" —
while releasing speech that arrives many seconds later, which cannot be one
breath no matter how slow the turn was.

Deliberately NOT fixed here: `_last_turn_done_at` measuring generation rather
than audio completion. Changing that timestamp would move every other consumer
of it (the tail-fragment guard, the silence handler) in one commit. The window
is the narrow fix; the timestamp's meaning is a separate question.
"""

import inspect

import pytest

from app.media_streams import connection as conn


# ---------------------------------------------------------------------------
# The guard as it exists in connection.py's transcript loop. Mirrored because it
# is inline inside handle_transcript; test_guard_matches_source below fails if
# the real one is edited without updating this.
# ---------------------------------------------------------------------------
def _is_dropped(
    *,
    enqueue_ts: float,
    prev_final_ts: float,
    last_turn_done_at: float,
    synthetic: bool = False,
    exempt: bool = False,
) -> bool:
    """True when the same-breath guard discards this utterance.

    `exempt` folds together `_in_name_collection and _short_fragment`, which
    test_surname_straggler_exemption.py owns.
    """
    return (
        not synthetic
        and not exempt
        and last_turn_done_at > 0.0
        and enqueue_ts > 0.0
        and enqueue_ts < last_turn_done_at
        and prev_final_ts > 0.0
        and (enqueue_ts - prev_final_ts) <= conn._SAME_BREATH_WINDOW_S
    )


# ---------------------------------------------------------------------------
# The incident.
# ---------------------------------------------------------------------------
# Reconstructed on a monotonic clock with the turn starting at t=0.
_TURN_START = 100.0
_TURN_DONE = _TURN_START + 16.404      # content_ttfa_ms=16404
_PREV_FINAL = _TURN_START              # the caller's turn that opened it
_HELLO_AT = _TURN_START + 16.0         # "hello", 16s into the silence


def test_the_incident_hello_is_no_longer_dropped():
    assert not _is_dropped(
        enqueue_ts=_HELLO_AT,
        prev_final_ts=_PREV_FINAL,
        last_turn_done_at=_TURN_DONE,
    ), (
        "speech 16s after the previous FINAL is still classified as the same "
        "breath — the caller's 'hello' into dead air is discarded again"
    )


def test_the_incident_would_have_been_dropped_before_the_window():
    """Proves the window is what does the work. Without it the old condition is
    satisfied outright: 16.0 < 16.404."""
    old_guard = _HELLO_AT < _TURN_DONE
    assert old_guard, "the reconstruction no longer reproduces the incident"


# ---------------------------------------------------------------------------
# The cases the guard exists for must still be caught.
# ---------------------------------------------------------------------------
def test_the_2026_06_12_split_utterance_is_still_dropped():
    """The stress-test case in the guard's own comment: one long sentence split
    by STT, the tail arriving a beat later, firing a redundant second turn."""
    head = 200.0
    tail = head + 0.7
    assert _is_dropped(
        enqueue_ts=tail,
        prev_final_ts=head,
        last_turn_done_at=head + 2.4,   # normal turn
    )


def test_the_2026_07_24_surname_timing_is_still_inside_the_window():
    """'yeah so that will be quaint in' 23:16:34.635 -> 'rock' 23:16:35.331.

    696ms apart. This one is KEPT, but by the name-collection exemption, not by
    the window — the window must not be what rescues it, or the exemption would
    be silently dead and surnames outside a booking would start dispatching.
    """
    head = 300.0
    surname = head + 0.696
    assert _is_dropped(
        enqueue_ts=surname,
        prev_final_ts=head,
        last_turn_done_at=head + 2.04,
        exempt=False,
    ), "the window now rescues the surname, which would bypass the exemption"
    assert not _is_dropped(
        enqueue_ts=surname,
        prev_final_ts=head,
        last_turn_done_at=head + 2.04,
        exempt=True,
    )


@pytest.mark.parametrize("gap", [0.1, 0.5, 1.0, 1.9])
def test_fragments_within_the_window_are_still_dropped(gap):
    head = 400.0
    assert _is_dropped(
        enqueue_ts=head + gap,
        prev_final_ts=head,
        last_turn_done_at=head + 5.0,
    )


@pytest.mark.parametrize("gap", [2.5, 4.0, 10.0, 16.0])
def test_speech_beyond_the_window_is_released(gap):
    head = 500.0
    assert not _is_dropped(
        enqueue_ts=head + gap,
        prev_final_ts=head,
        last_turn_done_at=head + 20.0,
    )


# ---------------------------------------------------------------------------
# Boundaries and degenerate state.
# ---------------------------------------------------------------------------
def test_the_first_final_of_a_call_is_never_a_straggler():
    """No previous FINAL exists, so nothing can be trailing it. Before this fix
    the caller's first answer after the greeting was eligible for the guard on
    timing alone."""
    assert not _is_dropped(
        enqueue_ts=600.0,
        prev_final_ts=0.0,
        last_turn_done_at=601.0,
    )


def test_a_reply_after_the_turn_completed_is_untouched():
    """The ordinary case: the caller answers once they have heard the response."""
    assert not _is_dropped(
        enqueue_ts=705.0,
        prev_final_ts=700.0,
        last_turn_done_at=702.0,
    )


def test_synthetic_utterances_are_never_dropped():
    assert not _is_dropped(
        enqueue_ts=800.5,
        prev_final_ts=800.0,
        last_turn_done_at=802.0,
        synthetic=True,
    )


def test_the_window_is_a_breath_not_a_turn():
    """If this is ever raised past a few seconds it stops being a claim about
    human speech and starts silently re-creating B-18."""
    assert 1.0 <= conn._SAME_BREATH_WINDOW_S <= 3.0


# ---------------------------------------------------------------------------
# Keep the mirror honest.
# ---------------------------------------------------------------------------
def test_guard_matches_source():
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert "_SAME_BREATH_WINDOW_S" in src, (
        "the window is no longer applied in the transcript loop"
    )
    assert "_prev_final_ts" in src
    assert "self._last_final_enqueue_ts" in src


def test_the_anchor_is_advanced_for_every_final():
    """Including ones the guard drops. Three fragments of one breath: if a
    dropped fragment did not advance the anchor, the third would be measured
    against the first and could fall outside the window."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    i = src.index("_prev_final_ts")
    window = src[i:i + 400]
    assert "self._last_final_enqueue_ts = _enqueue_ts" in window, (
        "the anchor is not advanced before the guard runs"
    )


def test_both_branches_share_one_condition():
    """The 'KEPT (name collection…)' log must only fire when the guard would
    actually have dropped the fragment, or it credits the name exemption with
    rescues the window made.

    Asserted on the shared `_same_breath` name rather than on the constant:
    the two branches must apply the *same* condition, which is what a single
    name buys and what two inlined copies would eventually lose.
    """
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert src.count("and _same_breath") == 2, (
        "the drop branch and the KEPT branch no longer share one condition"
    )
    i = src.index("same-breath straggler KEPT")
    assert "and _same_breath" in src[i - 800:i]
