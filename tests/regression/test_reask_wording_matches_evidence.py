# tests/regression/test_reask_wording_matches_evidence.py
"""P2 — Susie apologised for mishearing callers she had heard perfectly.

Incident (2026-07-24, jv_v1 23:14-23:17)
---------------------------------------
FOUR re-asks opened with "Sorry, I didn't catch that", and on every one the
caller had not spoken at all:

    23:14:19  re-ask after  6.0s   caller first spoke 10.78s after the prompt
    23:15:06  re-ask after 10.0s   caller first spoke 17.08s after the prompt
    23:16:12  re-ask after 10.0s   caller first spoke 30.66s after the prompt
    23:16:27  safety net           (same silence)

Inbound audio was flawless: 9183 frames over 186s, 98.7% of the expected ~20ms
rate, `inbound_audio=flowing`. No partial appeared in any of those windows,
while the caller's engaged replies elsewhere landed in 1.8-3.3s. Real silences,
not slow recognition — so the claim was false every time it was made. To a
listener that is indistinguishable from being deaf, which is why "Susie can't
hear me" was the reported symptom on a call where she heard everything.

Same defect class as the DVT escalation asserting a symptom the caller had
denied (a04fc58): stating as fact something the evidence contradicts.

SECOND incident (2026-07-25, 03:29) — the first fix shipped INERT
-----------------------------------------------------------------
adb2330 deployed at 03:25. At 03:29:41, with audio flowing and the caller
silent, it still said "Sorry, I didn't catch that". Two independent faults:

  1. ARITHMETIC. The decision used a 12.0s "recent voice" window, but the
     watchdog fires 6.0s or 10.0s after the question — so the window always
     covered the entire silence and the "they have not spoken" branch was
     UNREACHABLE. Structural: a smaller constant would only have moved it.

  2. ECHO. `_last_voiced_audio_at` is stamped from raw inbound frame energy,
     and a speakerphone feeds Susie's own voice back into the caller's mic.
     The stamp read "the caller made a sound" when the sound was Susie.

Why the original tests missed it: they fed the probe hand-picked values
(`_REASK_VOICE_RECENCY_SEC + 0.1`) that production can never produce. The
function was verified against inputs chosen by the test, not inputs the system
emits. THE TESTS BELOW THEREFORE DRIVE THE REAL PROBE with timings taken from
the actual calls — that is the part that matters here.

Fix
---
`_reask_audio_probe` answers "did the caller make a sound SINCE we stopped
asking" by comparing two monotonic stamps it already owns. No threshold, so
fault (1) cannot recur; echo during her turn falls on the correct side of the
boundary, so fault (2) cannot either. The recovery ladder — timings, attempt
counts, escalation, transfer — is unchanged.
"""

import types

import pytest

from app.media_streams import connection as conn
from app.media_streams.connection import SilenceHandler, WebSocketCallHandler

_APOLOGY_1 = "Sorry, I didn't catch that"
_APOLOGY_2 = "I'm sorry, I'm still not hearing you clearly. Let's try again"

_NOW = 1000.0


def _handler(probe):
    """A SilenceHandler with only the probe wired — _reask_prefix touches no more."""
    h = SilenceHandler.__new__(SilenceHandler)
    h._audio_probe = probe
    return h


def _real_probe(monkeypatch, *, last_voiced_at, tts_done_at, frames=5000,
                last_frame_at=None):
    """The REAL _reask_audio_probe bound to production-shaped state.

    Hand-written probe stubs are what let the shipped bug through, so every
    behavioural test below goes through this.
    """
    monkeypatch.setattr(conn.time, "monotonic", lambda: _NOW)
    h = types.SimpleNamespace(
        _media_frames_in=frames,
        _last_audio_received_at=_NOW - 0.02 if last_frame_at is None else last_frame_at,
        _last_voiced_audio_at=last_voiced_at,
        _tts_audio_done_at=tts_done_at,
    )
    h._MEDIA_STALL_SEC = WebSocketCallHandler._MEDIA_STALL_SEC
    h._ECHO_TAIL_SEC = WebSocketCallHandler._ECHO_TAIL_SEC
    h._inbound_audio_status = WebSocketCallHandler._inbound_audio_status.__get__(h)
    h._reask_audio_probe = WebSocketCallHandler._reask_audio_probe.__get__(h)
    return h._reask_audio_probe


# ---------------------------------------------------------------------------
# The 2026-07-25 03:29 regression — the reason the first fix was inert.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("watchdog_wait", [6.0, 10.0])
def test_silence_after_the_prompt_is_never_reported_as_a_mishearing(
    monkeypatch, watchdog_wait
):
    """THE ARITHMETIC TRAP, at both real watchdog waits.

    Susie stops speaking, the caller says nothing, the watchdog fires after
    its wait. Echo pins the last voiced frame to the end of her turn — which
    is what actually happened at 03:29:41 — so `voice_gap` equals the wait.
    Any recency window wider than the wait apologises here. This must not.
    """
    tts_done = _NOW - watchdog_wait
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=tts_done,       # echo of Susie, not the caller
        tts_done_at=tts_done,
    )
    assert _handler(probe)._reask_prefix(1) == "Are you still there?"


def test_the_exact_0329_numbers(monkeypatch):
    """Reconstructed from the live call that proved adb2330 inert.

        03:29:31.019  Susie stops speaking
        03:29:41.039  WATCHDOG_FIRE  (10.02s later)  -> said "I didn't catch that"
    """
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=_NOW - 10.02,   # echo, pinned to end of her turn
        tts_done_at=_NOW - 10.02,
    )
    status, voice_gap, voiced_since = probe()
    assert status == "flowing"
    assert voice_gap == pytest.approx(10.02)
    assert voiced_since is False, (
        "echo of Susie's own voice during her turn was credited to the caller"
    )
    assert _handler(probe)._reask_prefix(1) == "Are you still there?"


def test_echo_during_the_prompt_does_not_count_as_the_caller(monkeypatch):
    """A voiced frame BEFORE she finished is hers, not theirs."""
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=_NOW - 12.0,    # mid-way through her turn
        tts_done_at=_NOW - 11.0,       # ...which ended after it
    )
    assert probe()[2] is False
    assert _handler(probe)._reask_prefix(1) == "Are you still there?"


def test_echo_tail_just_after_playout_still_does_not_count(monkeypatch):
    """Line delay and reverb outlive the last audio packet."""
    tts_done = _NOW - 10.0
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=tts_done + (WebSocketCallHandler._ECHO_TAIL_SEC / 2),
        tts_done_at=tts_done,
    )
    assert probe()[2] is False


# ---------------------------------------------------------------------------
# The apology must survive for the case it was written for.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("attempt,expected", [(1, _APOLOGY_1), (2, _APOLOGY_2)])
def test_caller_spoke_after_the_prompt_but_no_transcript_apologises(
    monkeypatch, attempt, expected
):
    """A genuine mishearing: they made a sound, we produced nothing."""
    tts_done = _NOW - 10.0
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=tts_done + 2.0,   # well clear of the echo tail
        tts_done_at=tts_done,
    )
    assert probe()[2] is True
    assert _handler(probe)._reask_prefix(attempt) == expected


def test_caller_never_audible_all_call_is_silence_not_a_miss(monkeypatch):
    probe = _real_probe(monkeypatch, last_voiced_at=0.0, tts_done_at=_NOW - 10.0)
    status, gap, voiced = probe()
    assert (gap, voiced) == (float("inf"), False)
    assert _handler(probe)._reask_prefix(1) == "Are you still there?"


# ---------------------------------------------------------------------------
# A dead leg says so.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("attempt,expected", [
    (1, "I'm having trouble hearing you — you might be breaking up"),
    (2, "I still can't hear anything your end"),
])
def test_stalled_inbound_leg_says_it_cannot_hear(monkeypatch, attempt, expected):
    probe = _real_probe(
        monkeypatch,
        last_voiced_at=_NOW - 50.0,
        tts_done_at=_NOW - 10.0,
        last_frame_at=_NOW - 48.0,       # media stopped: 'stalled'
    )
    assert probe()[0] == "stalled"
    assert _handler(probe)._reask_prefix(attempt) == expected


def test_no_frames_at_all_says_it_cannot_hear(monkeypatch):
    probe = _real_probe(
        monkeypatch, last_voiced_at=0.0, tts_done_at=_NOW - 10.0, frames=0,
    )
    assert probe()[0] == "never"
    assert "trouble hearing you" in _handler(probe)._reask_prefix(1)


# ---------------------------------------------------------------------------
# A diagnostic must never reshape recovery.
# ---------------------------------------------------------------------------
def test_no_probe_falls_back_to_original_wording():
    """Every pre-existing test double constructs SilenceHandler without one."""
    assert _handler(None)._reask_prefix(1) == _APOLOGY_1
    assert _handler(None)._reask_prefix(2) == _APOLOGY_2


def test_probe_raising_falls_back_to_original_wording():
    def boom():
        raise RuntimeError("probe exploded")
    assert _handler(boom)._reask_prefix(1) == _APOLOGY_1


def test_probe_returning_the_old_2_tuple_falls_back_safely():
    """Defence against a half-deployed rollback: arity change must not crash."""
    assert _handler(lambda: ("flowing", 3.0))._reask_prefix(1) == _APOLOGY_1


def test_probe_is_optional_on_the_constructor():
    import inspect
    sig = inspect.signature(SilenceHandler.__init__)
    assert sig.parameters["audio_probe"].default is None


# ---------------------------------------------------------------------------
# The trap itself, locked.
# ---------------------------------------------------------------------------
def test_no_recency_window_constant_survives():
    """The decision must not depend on a duration compared against the wait.

    A window wider than the watchdog wait (6.0s / 10.0s) makes the silence
    branch unreachable, which is precisely how the first fix shipped doing
    nothing. Reintroducing one should fail here rather than on a live call.
    """
    assert not hasattr(SilenceHandler, "_REASK_VOICE_RECENCY_SEC"), (
        "a recency window is back — see the 2026-07-25 03:29 incident in this "
        "module's docstring before reinstating it"
    )
