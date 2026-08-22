"""Regression: the play-duration bound needs a ceiling, not just a slope.

``_clamp_play_secs`` bounded ``play_secs`` at ``len(spoken)/(10*speed) + 4``.
That is proportional, and its slack grows linearly with the text: against
~18.6 c/s of real speech, 10 c/s concedes ~0.046 s of permitted stranding per
character.  So the bound was loosest exactly where it should have been
tightest -- the slot presentation is both the longest chunk in the system and
the turn the whole call turns on.

Measured permissiveness before the cap:

    351-char chunk (corpus max) .... 39.1 s at speed 1.0, 47.9 s at 0.8
    1277-char turn as one chunk .... 131.7 s

WHERE THE CAP COMES FROM
------------------------
Not from taste.  Every assistant turn in the obs corpus (2858 turns across 340
calls) was replayed through the real ``ResponseChunker``, giving 3088 real
chunks: median 97 chars, p95 186, p99 252, p99.9 314, **max 351**.

At the slowest healthy rate measured on Theorem (18.6 c/s), plus the 2.60 s of
filler audio that can ride the same byte counter, the worst real chunk takes
**21.5 s** at speed 1.0.  ``_MAX_CHUNK_PLAY_SECS = 28.0`` clears that by ~1.3x.

WHY IT IS SCALED BY 1/speed
---------------------------
A *flat* 28 s cap would leave only ~1.07x margin at the 0.8 phone speed, where
that same chunk legitimately takes 26.2 s -- and a cap that cuts real audio
short re-prompts over Susie mid-sentence, which is worse than the dead air it
is trying to bound.  Scaling preserves the margin at every speed.

WHAT THIS IS NOT
----------------
A fix for caller-perceived dead air.  The cap only binds above ~240 characters
at speed 1.0; the common case is still governed by the proportional bound, and
~21.5 s for the live 175-char chunk is unchanged.  It is a tail-risk backstop.
See item O's residual in ``docs/plan/OPEN_DEFECTS_2026-08-22.md``.
"""
from __future__ import annotations

from app.media_streams.connection import (
    _clamp_play_secs,
    _MIN_SPEECH_CHARS_PER_SEC,
    _PLAY_SECS_HEADROOM,
    _MAX_CHUNK_PLAY_SECS,
    ELEVENLABS_SPEED,
    ELEVENLABS_PHONE_SPEED,
)

# --- measured, not assumed -------------------------------------------------
CORPUS_MAX_CHUNK_CHARS = 351     # max of 3088 chunks replayed from 2858 turns
CORPUS_P999_CHUNK_CHARS = 314
SLOWEST_REAL_CHARS_PER_SEC = 18.6   # slowest healthy chunk, Theorem 22 Aug
FILLER_HEADROOM_SECS = 2.60         # both clips + the 0.1 s breath gap


def _worst_real_secs(chars: int, speed: float) -> float:
    """How long that many characters can legitimately take to speak."""
    return chars / (SLOWEST_REAL_CHARS_PER_SEC * speed) + FILLER_HEADROOM_SECS


# ---------------------------------------------------------------------------
# The cap exists and binds where the proportional bound goes slack
# ---------------------------------------------------------------------------

def test_a_long_chunk_is_no_longer_permitted_a_proportional_stranding():
    """The whole point: length must stop buying stranding time."""
    text = "x" * CORPUS_MAX_CHUNK_CHARS
    proportional = len(text) / (_MIN_SPEECH_CHARS_PER_SEC * 1.0) + _PLAY_SECS_HEADROOM
    bound = _clamp_play_secs(9_999.0, text)
    assert proportional > 35.0, "precondition: the old bound really was this loose"
    assert bound < proportional, "the cap did not bind on the corpus-max chunk"
    assert bound <= _MAX_CHUNK_PLAY_SECS / min(ELEVENLABS_SPEED, 1.0) + 0.01


def test_an_entire_turn_arriving_as_one_chunk_is_bounded():
    """The 1277-char turn in the corpus would have been permitted ~131 s."""
    text = "x" * 1277
    proportional = len(text) / (_MIN_SPEECH_CHARS_PER_SEC * 1.0) + _PLAY_SECS_HEADROOM
    assert proportional > 120.0
    assert _clamp_play_secs(9_999.0, text) < 40.0


# ---------------------------------------------------------------------------
# ...and does NOT bind on anything real
# ---------------------------------------------------------------------------

def test_the_cap_clears_the_worst_real_chunk_at_every_speed_in_use():
    """A cap that cuts real audio short re-prompts over Susie. Never do that.

    Checked at both configured speeds AND at the 0.7 clamp floor, since the cap
    must hold for any speed the system can select, not just today's config.
    """
    for speed in {1.0, float(ELEVENLABS_SPEED), float(ELEVENLABS_PHONE_SPEED), 0.7}:
        ceiling = _MAX_CHUNK_PLAY_SECS / speed
        worst = _worst_real_secs(CORPUS_MAX_CHUNK_CHARS, speed)
        assert ceiling > worst, (
            "cap %.1fs at speed %.2f would cut off the longest real chunk "
            "(%.1fs of genuine speech)" % (ceiling, speed, worst)
        )
        assert ceiling / worst >= 1.2, (
            "margin at speed %.2f is only %.2fx -- too thin to absorb a chunk "
            "longer than any yet observed" % (speed, ceiling / worst)
        )


# A chunk that the TTS layer classifies as a phone read-back, and so speaks at
# ELEVENLABS_PHONE_SPEED.  Long enough that the ABSOLUTE cap, not the
# proportional bound, is the operative limit -- which is the only condition
# under which flat-vs-scaled is observable at all.
PHONE_LONG_CHUNK = (
    "Let me read that back so we definitely have it right, and do stop me if "
    "any of it is wrong, because it is the number we will call you on: "
    "oh seven five oh two, two one one, two oh seven. "
    "I will send the confirmation there as soon as we are done, and if "
    "anything changes you can call us back on that same line at any time."
)


def test_the_cap_is_scaled_by_speed_and_not_flat():
    """Pins the reason the cap is not a flat number -- through the function.

    An earlier version of this test computed both margins from the constants
    and asserted on those.  It passed whether or not the shipped code divided
    by `speed`, because with ordinary text `speed` is 1.0 and the two forms are
    identical -- a mutation making the cap flat went undetected.

    So drive a chunk the TTS layer actually speaks at 0.8 and require the bound
    to exceed the flat value.  A flat 28 s would leave only ~1.07x margin over
    the 26.2 s a corpus-max chunk legitimately takes at that speed, and a cap
    that cuts real audio short re-prompts over Susie mid-sentence.
    """
    from app.media_streams.tts_stream import (
        _apply_tts_substitutions_elevenlabs as _subs,
        _is_spoken_phone_number as _is_phone,
    )
    spoken = _subs(PHONE_LONG_CHUNK)
    assert _is_phone(spoken), (
        "fixture no longer classifies as a phone read-back -- this test is "
        "not exercising the 0.8 path and proves nothing"
    )
    assert ELEVENLABS_PHONE_SPEED < 1.0, "premise: the phone speed is slower"

    proportional = len(spoken) / (_MIN_SPEECH_CHARS_PER_SEC * ELEVENLABS_PHONE_SPEED) \
        + _PLAY_SECS_HEADROOM
    bound = _clamp_play_secs(9_999.0, PHONE_LONG_CHUNK)

    assert bound < proportional, (
        "precondition: the absolute cap must be the operative bound here, "
        "otherwise flat-vs-scaled is invisible"
    )
    assert bound > _MAX_CHUNK_PLAY_SECS + 0.01, (
        "the cap is being applied FLAT (%.1fs) instead of scaled by 1/speed "
        "(%.1fs) -- margin collapses to ~1.07x on a phone read-back"
        % (bound, _MAX_CHUNK_PLAY_SECS / ELEVENLABS_PHONE_SPEED)
    )
    assert bound == _MAX_CHUNK_PLAY_SECS / ELEVENLABS_PHONE_SPEED


def test_ordinary_chunks_are_untouched_by_the_cap():
    """The cap must not become the operative bound for normal speech.

    Below ~240 chars the proportional bound is tighter and must stay in charge;
    if the cap started binding here it would be *loosening* the clamp.
    """
    for chars in (50, 97, 175, 186, 240):
        text = "x" * chars
        proportional = len(text) / (_MIN_SPEECH_CHARS_PER_SEC * 1.0) + _PLAY_SECS_HEADROOM
        assert _clamp_play_secs(9_999.0, text) == proportional, (
            "the cap displaced the proportional bound at %d chars -- that "
            "RELAXES the clamp for ordinary speech" % chars
        )


def test_the_cap_never_relaxes_the_existing_bound():
    """min(), not max().  A cap must only ever tighten."""
    for chars in (10, 97, 175, 314, 351, 800, 1277):
        text = "x" * chars
        proportional = len(text) / (_MIN_SPEECH_CHARS_PER_SEC * 1.0) + _PLAY_SECS_HEADROOM
        assert _clamp_play_secs(9_999.0, text) <= proportional + 0.01


def test_the_live_call_is_unchanged_by_the_cap():
    """The 175-char chunk that started all this still clamps to 21.5 s."""
    from tests.regression.test_o_impossible_play_duration import (
        LIVE_CHUNK, LIVE_PLAY_SECS,
    )
    assert _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK) == 21.5


def test_healthy_durations_still_pass_through_untouched():
    """Regression guard: the cap must not clamp audio that is fine."""
    text = "x" * CORPUS_MAX_CHUNK_CHARS
    healthy = _worst_real_secs(CORPUS_MAX_CHUNK_CHARS, 1.0)   # 21.5 s
    assert _clamp_play_secs(healthy, text) == healthy
