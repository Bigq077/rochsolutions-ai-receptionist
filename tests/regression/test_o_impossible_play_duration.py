"""Regression: a corrupt TTS byte counter must not strand the call in silence.

CA268397d43e00dd2ceaa3e2817334e7dd — 22 Aug 2026, Theorem, build c28669a2aa9e.

Turn 1's reply was scheduled to finish 26.7 s out.  The send loop derived that
from ``_tts_bytes_sent`` and logged::

    [ms_silence] tts_finished in 26.7s: "No — I'm Susie, Theorem Health's AI re"

The terminal chunk's finish callback was still pending when the turn ended, so
no terminal ``tts_finished`` fired, no ``WATCHDOG_START`` was armed, and the
call sat silent from 15:48:49 to 15:49:07 — 19 seconds — recovered only because
the caller happened to speak unprompted.  A real caller hangs up.

THE TRAP THIS FILE EXISTS TO HOLD DOWN
--------------------------------------
The first version of this test hard-coded a 49-character string, because the
log line truncates ``text`` at 60 characters and 49 characters is where the
first sentence ends.  It is the wrong unit.  One ``_TTS_DONE_SENTINEL`` is
placed per ``chunk_text``, after *every* sub-chunk of it has been synthesised,
so the ``text`` behind a sentinel is the WHOLE reply.  The real chunk — read
back out of the obs store for this exact call — is 175 characters, and
``split_tts_text`` cut it into three sub-chunks of 49, 61 and 63.

At 49 characters the bound was 33.2 s, the 26.7 s failure sailed under it, and
the fix was inert against the very call it was written for while the test went
on passing.  Hence ``test_the_live_chunk_is_the_whole_reply``: if the constant
below ever shrinks back to one sentence, that test fails first and says why.
"""

import pytest

from app.media_streams.chunker import split_tts_text
from app.media_streams.config import ELEVENLABS_PHONE_SPEED, ELEVENLABS_SPEED
from app.media_streams.connection import (
    _MIN_SPEECH_CHARS_PER_SEC,
    _PLAY_SECS_HEADROOM,
    _clamp_play_secs,
)

# The exact chunk, recovered from obs (calls.transcript, turn 3 of the call).
LIVE_CHUNK = (
    "No — I'm Susie, Theorem Health's AI receptionist. I can get you booked "
    "in or answer questions about the clinic, and I can put you through to "
    "Mark if you'd rather speak to him."
)
LIVE_PLAY_SECS = 26.7          # the corrupt count that produced the dead air
REPRO_PLAY_SECS = 8.8          # the same chunk, healthy, on the 22 Aug repro call

# The slowest rate any chunk has actually been measured speaking at.  Theorem's
# 22 Aug chunks clustered at 18.6-21.0 c/s and the corrupt one implied 6.55.
# The 22 Aug 20:21 verification call on the demo line came in slower still --
# the greeting at 17.9 c/s -- so this was revised DOWN from 18.6.  Used as the
# floor a real chunk is asserted against, so it must only ever move down.
SLOWEST_REAL_CHARS_PER_SEC = 17.9   # 93-char greeting in 5.2s, CA59c015de
                                    # (22 Aug 20:21). Was 18.6; remeasured.
CORPUS_MAX_CHUNK_CHARS = 351        # max of 3088 chunks replayed from obs

# Every assistant chunk spoken on the three Theorem calls of 22 Aug 2026,
# verbatim from obs.  This is the audio the clamp must never touch.
REAL_CHUNKS = [
    "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
    "directly press 1, otherwise how can I help you today?",
    "Let me just check that…",
    LIVE_CHUNK,
    "Ha — I'll take that as a yes! How can I help you today?",
    "You're through to reception — I'm Susie, the AI receptionist — I can get "
    "you booked in or answer questions about the clinic, or put you through to "
    "Mark if you'd rather.",
    "Right with you…",
    "Hi — how can I help you today?",
    "It's £85 for a new patient assessment, which is fifty minutes with Mark, "
    "and follow-ups are £55 for thirty minutes.",
    "Just getting that for you…",
    "A new patient assessment is £85, and that's a 50-minute session with Mark. "
    "Follow-up appointments are £55 for 30 minutes.",
    "Sorry, I didn't catch that. Would you like to book one?",
]


def test_the_live_chunk_is_the_whole_reply():
    """Guard the unit itself: `text` is the chunk, not its first sentence.

    Bounding on the 49-character first sentence is what made the first fix
    inert.  The log truncates at 60 characters; the chunk does not.
    """
    assert len(LIVE_CHUNK) == 175
    # Exactly what the 22 Aug repro call's log line rendered, truncation and all.
    assert LIVE_CHUNK[:60] == (
        "No — I'm Susie, Theorem Health's AI receptionist. I can get "
    )
    # Three sub-chunks, one sentinel — so one play_secs covers all three.
    assert [len(s) for s in split_tts_text(LIVE_CHUNK)] == [49, 61, 63]


def test_the_live_call_is_clamped():
    """The 26.7 s that produced 19 s of dead air must not survive."""
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    assert clamped < LIVE_PLAY_SECS
    # 175 chars / (10.0 * 1.0) + 4.0 == 21.5 s
    expected = len(LIVE_CHUNK) / (_MIN_SPEECH_CHARS_PER_SEC * ELEVENLABS_SPEED)
    assert clamped == pytest.approx(expected + _PLAY_SECS_HEADROOM)


def test_the_failure_clears_the_bound_by_a_real_margin():
    """The failure must sit inside the bound by a margin, not by a hair.

    6 c/s + 5 s gave 34.2 s for this chunk — above 26.7, which is precisely why
    the first fix never fired.  Any future loosening that reopens that gap
    fails here.
    """
    bound = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    assert LIVE_PLAY_SECS - bound >= 5.0


def test_the_same_chunk_when_healthy_is_untouched():
    """The 22 Aug reproduction: this exact reply, spoken normally, took 8.8 s.

    The pair (26.7 clamped, 8.8 not) is the whole test.  A bound that fails
    either half is the wrong bound.
    """
    assert len(LIVE_CHUNK) / REPRO_PLAY_SECS == pytest.approx(19.9, abs=0.2)
    assert _clamp_play_secs(REPRO_PLAY_SECS, LIVE_CHUNK) == REPRO_PLAY_SECS


@pytest.mark.parametrize("text", REAL_CHUNKS, ids=lambda t: t[:28])
def test_real_audio_is_never_clamped(text):
    """The clamp must be inert on healthy calls.

    Clamping real audio arms the watchdog while the caller is still listening
    and produces a re-prompt over Susie's own speech.
    """
    real_secs = len(text) / SLOWEST_REAL_CHARS_PER_SEC
    assert _clamp_play_secs(real_secs, text) == real_secs


@pytest.mark.parametrize("text", REAL_CHUNKS, ids=lambda t: t[:28])
def test_real_audio_keeps_a_two_times_margin(text):
    """Not merely inert — inert with room.

    A real chunk that only just clears the bound is one prompt tweak away from
    being cut short, and the failure mode is silent.
    """
    real_secs = len(text) / SLOWEST_REAL_CHARS_PER_SEC
    assert _clamp_play_secs(999.0, text) >= 2.0 * real_secs


def test_a_phone_readback_is_never_clamped():
    """The one utterance that is legitimately slow per written character.

    ElevenLabs is handed the expanded form — "07502211207" becomes "oh seven
    five oh two, two one one, two oh seven" — and speaks it at
    ELEVENLABS_PHONE_SPEED.  A bound taken on the written text would cut short
    the turn where the caller is checking eleven digits against the number in
    their hand.
    """
    written = "Just to confirm — that's 07502211207. Is that right?"
    expanded_extra = 47 - 11        # digits -> words, measured on _spell_phone
    spoken_chars = len(written) + expanded_extra
    real_secs = spoken_chars / (SLOWEST_REAL_CHARS_PER_SEC * ELEVENLABS_PHONE_SPEED)
    assert _clamp_play_secs(real_secs, written) == real_secs


def test_a_slow_tuned_voice_is_never_clamped():
    """ELEVENLABS_SPEED is env-tunable down to 0.7 from the Render dashboard.

    The bound scales with it, so slowing the voice cannot silently turn the
    clamp on real audio.
    """
    real_secs = len(LIVE_CHUNK) / (SLOWEST_REAL_CHARS_PER_SEC * 0.7)
    bound_at_0_7 = (
        len(LIVE_CHUNK) / (_MIN_SPEECH_CHARS_PER_SEC * 0.7) + _PLAY_SECS_HEADROOM
    )
    assert bound_at_0_7 > real_secs


def test_headroom_covers_the_clips_that_actually_ship():
    """A hold clip riding on the counter must not trip the clamp.

    FillerGuard injects through _send_ulaw, which places no sentinel, so its
    bytes are charged to the next chunk that does.  Worst case is one clip from
    each pool in the same turn, plus the 100 ms breath gap.  Measured off the
    committed µ-law files, so adding a longer clip fails here rather than on a
    call.

    The figure moved 2.60 -> 2.93 on 2026-08-30, when filler_checking was
    regenerated from one recording into a pool of five: the longest of the
    five is 1.72s against the original 1.39s.  That is the pool doing its job
    -- a single recording replayed for the life of the service is what made
    the hold sound like a machine -- and _PLAY_SECS_HEADROOM (4.0s) still
    covers it with a second to spare.
    """
    from app.media_streams.connection import _AUDIO_CLIPS_DIR, _SILENCE_100MS
    from app.media_streams.filler_guard import discover_clip_pool

    def longest(name: str) -> float:
        pool = discover_clip_pool(_AUDIO_CLIPS_DIR / name)
        assert pool, f"{name} missing — FillerGuard would disable itself"
        return max(p.stat().st_size for p in pool) / 8000.0

    worst = (
        longest("filler_checking.ulaw")
        + longest("filler_moment.ulaw")
        + len(_SILENCE_100MS) / 8000.0
    )
    assert worst == pytest.approx(2.93, abs=0.05)
    assert _PLAY_SECS_HEADROOM > worst


def test_a_short_chunk_carrying_both_clips_is_never_clamped():
    """The shortest real chunk, with the whole filler budget charged to it."""
    short = "Right with you…"
    real_secs = len(short) / SLOWEST_REAL_CHARS_PER_SEC + 2.93
    assert _clamp_play_secs(real_secs, short) == real_secs


def test_clamp_only_ever_reduces():
    """Never inflate a duration — that would recreate the dead air."""
    for secs in (0.1, 1.0, 5.0, 12.0, 30.0, 300.0):
        assert _clamp_play_secs(secs, LIVE_CHUNK) <= secs


def test_the_bound_sits_between_the_failure_and_the_slowest_real_speech():
    """The single number the whole clamp rests on.

    10 c/s must stay well above the rate the corrupt count implied (6.55 c/s)
    and must never fall below what real speech needs.

    The second half used to be a proxy -- ``_MIN_SPEECH_CHARS_PER_SEC <=
    SLOWEST_REAL_CHARS_PER_SEC / 1.8`` -- a ratio between two *rates*.  That
    proxy broke when the slowest measured rate was revised 18.6 -> 17.9 c/s
    (17.9/1.8 = 9.94 < 10.0), while the property it stood for held with room to
    spare, because ``_PLAY_SECS_HEADROOM`` dominates the rate difference at
    every length.  Loosening 1.8 to 1.7 would have been fitting the test to the
    number.  Assert the real invariant instead, directly:

        no real chunk, at any length or speed, may exceed its own bound.
    """
    corrupt_rate = len(LIVE_CHUNK) / LIVE_PLAY_SECS      # 6.55 c/s
    assert _MIN_SPEECH_CHARS_PER_SEC >= corrupt_rate * 1.4

    from app.media_streams.connection import _MAX_CHUNK_PLAY_SECS
    for speed in (1.0, 0.8, 0.7, 1.2):
        for chars in range(5, CORPUS_MAX_CHUNK_CHARS + 1, 5):
            real = chars / (SLOWEST_REAL_CHARS_PER_SEC * speed) + 2.93
            bound = min(
                chars / (_MIN_SPEECH_CHARS_PER_SEC * speed) + _PLAY_SECS_HEADROOM,
                _MAX_CHUNK_PLAY_SECS / speed,
            )
            assert real <= bound, (
                "a %d-char chunk at speed %.1f legitimately takes %.1fs but is "
                "bounded at %.1fs -- the clamp would cut real speech short and "
                "re-prompt over Susie" % (chars, speed, real, bound)
            )
