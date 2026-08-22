"""Regression: a corrupt TTS byte counter must not strand the call in silence.

CA268397d43e00dd2ceaa3e2817334e7dd — 22 Aug 2026, Theorem, build c28669a2aa9e.

Turn 1's first chunk was "No — I'm Susie, Theorem Health's AI receptionist." —
49 characters, about three seconds of speech.  The send loop derived its play
duration from ``_tts_bytes_sent`` and logged::

    [ms_silence] tts_finished in 26.7s: "No — I'm Susie, Theorem Health's AI re"

The playout clock was already in the past at that moment (the preceding filler's
playout-end was 15:48:48.37, the sentinel was dequeued at 15:48:49.04), so the
26.7 s was the byte count alone.

What followed is the defect: the terminal chunk's finish callback was still
pending when the turn ended, so no terminal ``tts_finished`` fired, no
``WATCHDOG_START`` was armed, and the call sat silent from 15:48:49 to 15:49:07
— 19 seconds — recovered only because the caller spoke unprompted.
"""

import pytest

from app.media_streams.connection import (
    _MIN_SPEECH_CHARS_PER_SEC,
    _PLAY_SECS_HEADROOM,
    _clamp_play_secs,
)

# The exact chunk text from the call.
LIVE_CHUNK = "No — I'm Susie, Theorem Health's AI receptionist."
LIVE_PLAY_SECS = 26.7


def test_the_live_call_is_clamped():
    """The 26.7 s that produced 19 s of dead air must not survive."""
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    assert clamped < LIVE_PLAY_SECS
    # 49 chars / 6 + 5 == 13.17 s
    assert clamped == pytest.approx(len(LIVE_CHUNK) / 6.0 + 5.0)


def test_clamped_value_is_survivable_dead_air():
    """Whatever we clamp to must be well inside a caller's patience."""
    assert _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK) < 15.0


@pytest.mark.parametrize(
    "text, play_secs",
    [
        # Every real chunk measured on CA268397d4, at its real duration.
        ("No — I'm Susie, Theorem Health's AI receptionist.", 3.0),
        ("I can get you booked in or answer questions about the clinic", 3.5),
        ("Ha — I'll take that as a yes! How can I help you today?", 3.2),
        ("You're through to reception — I'm Susie, the AI receptionist", 9.2),
        ("Let me just check that…", 1.4),
        ("Hi there, I'm Susie, Theorem Health's AI receptionist —", 6.3),
    ],
)
def test_real_audio_is_never_clamped(text, play_secs):
    """The clamp must be inert on healthy calls.

    Clamping real audio would arm the watchdog while the caller is still
    listening and produce a re-prompt over Susie's own speech — the failure
    the out-of-order guard exists to prevent.
    """
    assert _clamp_play_secs(play_secs, text) == play_secs


def test_bound_sits_far_below_real_speech_rates():
    """6 c/s must stay well under the slowest rate a voice actually speaks."""
    # Measured on the live call: 55 chars in 3.2 s ≈ 17 c/s.
    assert _MIN_SPEECH_CHARS_PER_SEC < 17.0 / 2


def test_headroom_covers_a_filler_clip_on_a_short_chunk():
    """A hold clip riding on the counter must not trip the clamp."""
    short = "Of course."
    hold_clip_secs = 3.0
    speech_secs = len(short) / 15.0
    assert _clamp_play_secs(hold_clip_secs + speech_secs, short) == pytest.approx(
        hold_clip_secs + speech_secs
    )
    assert _PLAY_SECS_HEADROOM >= hold_clip_secs


def test_clamp_only_ever_reduces():
    """Never inflate a duration — that would recreate the dead air."""
    for secs in (0.1, 1.0, 5.0, 12.0, 30.0, 300.0):
        assert _clamp_play_secs(secs, LIVE_CHUNK) <= secs
