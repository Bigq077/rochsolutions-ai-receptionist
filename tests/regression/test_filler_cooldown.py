"""
Hold phrases must not stack (CA8cf0aaea, 2026-08-05).

On a reschedule the caller heard three of them in 3.4 seconds:

    15:13:33.278  "Let me just check that…"        connection.py, phone confirm
    15:13:35.094  "One moment…"                    llm_stream.py, background ack
    15:13:36.727  "Let me bring that up for you…"  filler_phrases.py, tool call

and at the write, two saying the same thing 1.1 seconds apart:

    15:14:42.691  "Just moving that for you now…"
    15:14:43.810  "No problem at all — I'm moving that for you now…"

Three producers, none aware of the others. A cancellation path existed
(`_ack_filler_cancelled`) but only works while the ack filler is still queued;
by the time a tool call is detected it has usually reached ElevenLabs, so the
cancel is a no-op and both play.

The fix is a shared cooldown. The risk it must NOT introduce is silence — the
calendar round-trip after "yes, go ahead" measured 11.1 s of dead air once
(B-40), so a write filler is exempt from the first suppression and only a
SECOND write filler is dropped.
"""

import time

import pytest

from app.filler_phrases import (
    FILLER_COOLDOWN_S,
    is_write_filler,
    note_filler_played,
    should_play_filler,
)


def _session_with_filler_at(seconds_ago: float, *, was_write: bool = False) -> dict:
    return {
        "_last_filler_ts": time.monotonic() - seconds_ago,
        "_last_filler_was_write": was_write,
    }


# ── the failing sequence ────────────────────────────────────────────────────

def test_the_three_phrase_stack_is_broken_up():
    """Replays the real gaps: A at 0.0, B at +1.8, C at +3.4."""
    session = {}

    assert should_play_filler(session) is True, "A must play — first of the turn"
    note_filler_played(session)

    session["_last_filler_ts"] = time.monotonic() - 1.816
    assert should_play_filler(session) is False, (
        "B played 1.8s after A — this is the stack the caller complained about"
    )

    session["_last_filler_ts"] = time.monotonic() - 3.449
    assert should_play_filler(session) is True, (
        "C is 3.4s after A, past the cooldown — suppressing it would leave the "
        "lookup uncovered"
    )


def test_the_duplicate_write_pair_is_broken_up():
    """15:14:42.691 then 15:14:43.810 — 1.1s apart, same meaning."""
    session = {}

    first = "Just moving that for you now…"
    assert is_write_filler(first), "write-filler detection lost 'moving that for you'"
    assert should_play_filler(session, is_write=True) is True
    note_filler_played(session, is_write=True)

    session["_last_filler_ts"] = time.monotonic() - 1.119
    second = "No problem at all — I'm moving that for you now…"
    assert is_write_filler(second)
    assert should_play_filler(session, is_write=True) is False, (
        "a second write filler still stacks on the first"
    )


# ── the risk: silence on a write must stay impossible ───────────────────────

def test_a_write_filler_survives_a_recent_generic_filler():
    """The one that matters. A caller who has just said 'yes, go ahead' waits
    on a calendar round-trip — B-40 measured 11.1 s of it. A generic filler
    moments earlier must never silence the write filler."""
    session = _session_with_filler_at(0.2, was_write=False)
    assert should_play_filler(session, is_write=True) is True


def test_a_write_filler_survives_even_immediately_after_a_generic_one():
    session = _session_with_filler_at(0.0, was_write=False)
    assert should_play_filler(session, is_write=True) is True


def test_the_first_filler_of_a_call_always_plays():
    assert should_play_filler({}) is True
    assert should_play_filler({}, is_write=True) is True


def test_a_filler_plays_again_once_the_cooldown_expires():
    """A genuinely slow turn must still be able to speak twice."""
    session = _session_with_filler_at(FILLER_COOLDOWN_S + 0.1)
    assert should_play_filler(session) is True


# ── write-filler detection agrees with the barge-in guard ───────────────────

@pytest.mark.parametrize("phrase", [
    "Just locking that in now…",
    "No problem at all — I'm moving that for you now…",
    "Getting that booked in for you now…",
    "Popping that in the diary…",
])
def test_write_phrases_are_recognised(phrase):
    assert is_write_filler(phrase) is True


@pytest.mark.parametrize("phrase", [
    "One moment…",
    "Let me just check that…",
    "Right with you…",
    "I'll take a look at the schedule for you…",
])
def test_generic_phrases_are_not_write_fillers(phrase):
    assert is_write_filler(phrase) is False


def test_detection_matches_the_barge_in_guard_phrases():
    """connection.py arms `_clinical_response_active` on the same four phrases.
    If one list moves without the other, a write filler stops being protected
    from suppression while still suppressing barge-in, or vice versa."""
    import inspect

    from app.media_streams import connection as conn
    from app.filler_phrases import _WRITE_FILLER_MARKERS

    src = inspect.getsource(conn.WebSocketCallHandler)
    guard = src[src.index("write filler — barge-in guard armed") - 900:
                src.index("write filler — barge-in guard armed")]
    for marker in _WRITE_FILLER_MARKERS:
        assert marker in guard, (
            f"{marker!r} is in filler_phrases but not in connection.py's "
            "barge-in guard — the two lists have drifted"
        )
