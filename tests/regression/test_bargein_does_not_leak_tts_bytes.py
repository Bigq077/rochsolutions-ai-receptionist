"""
Barge-in must not charge the interrupted audio to the next utterance.

`play_secs = self._tts_bytes_sent / 8000.0` decides when a TTS chunk's finish
callback fires, which is when the silence timer and the no-input watchdog arm.
The counter was a LOCAL inside `_tts_loop`, zeroed only when a done sentinel was
consumed — and barge-in calls `_drain_queue(self.audio_out_queue)`, which takes
the sentinel with it. The interrupted utterance's bytes stayed on the counter
and were added to the next completed utterance.

Both barge-in paths reset `_tts_playout_end_mono` and neither could reset the
counter, because a coroutine's local is not reachable from the handler. So the
clock reset was undone on the very next chunk.

Measured live, CA1747c2d9 on 2026-08-06, after six barge-ins:

    20:52:36  [ms_silence] tts_finished in 29.1s: 'No problem at all — on your
              keypad, just press 1 for Awlstuh'

That phrase takes about five seconds to say. The caller then heard nothing for
25 seconds — 20:52:36.9 to 20:53:01.8 — and the `[ms_safety_net] _tts_playing
stale ... force-clearing (Bug A backstop)` at 20:53:31 is the same drift being
mopped up eleven seconds later. The same call scheduled the identical 80-char
phrase at 4.2s earlier and 7.3s later: same text, different answer, the
difference being how much the caller had interrupted.

The property that makes this worth fixing over the cosmetic ones: the more the
caller talks over Susie, the more dead air they get. It punishes exactly the
callers who are trying hardest to be understood.
"""

import inspect

import pytest

from app.media_streams import connection as c


BYTES_PER_SECOND = 8000  # μ-law, 8 kHz


# ── the arithmetic the defect produced ─────────────────────────────────────

def test_the_leak_arithmetic_is_what_we_saw_in_the_log():
    """
    Not a test of the fix — a statement of the mechanism, so the numbers in the
    commit message can be checked rather than taken on trust.
    """
    interrupted = 5 * BYTES_PER_SECOND      # ~5s of audio, cut off by barge-in
    next_phrase = 5 * BYTES_PER_SECOND      # the keypad prompt, ~5s

    leaked = (interrupted + next_phrase) / BYTES_PER_SECOND
    correct = next_phrase / BYTES_PER_SECOND

    assert correct == 5.0
    assert leaked == 10.0          # one barge-in doubles it
    # Six barge-ins, as on the real call, is how 5s becomes ~29s.
    assert (6 * interrupted + next_phrase) / BYTES_PER_SECOND == 35.0


# ── the counter must be reachable from the barge-in handlers ──────────────

def test_the_counter_is_instance_state_not_a_loop_local():
    """
    The root cause in one assertion. A local in _tts_loop cannot be reset by
    the barge-in handler, however correct that handler looks.
    """
    src = inspect.getsource(c.WebSocketCallHandler.__init__)
    assert "self._tts_bytes_sent" in src, (
        "_tts_bytes_sent is not instance state — barge-in cannot clear it"
    )


def test_no_local_shadow_remains():
    """A re-declared local would silently restore the bug."""
    src = inspect.getsource(c)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("_tts_bytes_sent"):
            pytest.fail(f"local shadow of the counter reintroduced: {stripped!r}")


# ── the counter must die wherever the clock dies ───────────────────────────

def _reset_sites(src: str) -> list:
    """Every place the cumulative playout clock is zeroed."""
    return [
        i for i in range(len(src))
        if src.startswith("self._tts_playout_end_mono = 0.0", i)
    ]


def test_every_clock_reset_also_resets_the_counter():
    """
    These two are one operation. Clearing the clock alone is what the code did
    before, and the next chunk's bytes put the delay straight back.
    """
    src = inspect.getsource(c)
    sites = _reset_sites(src)
    assert len(sites) >= 2, "expected the cancel path and the barge-in path"

    for site in sites:
        window = src[site:site + 900]
        assert "self._tts_bytes_sent = 0" in window, (
            "a playout-clock reset does not clear the byte counter — the "
            "interrupted audio will be charged to the next utterance"
        )


def test_the_sentinel_still_zeroes_it_on_the_normal_path():
    """Ordinary completion must keep working: measure, then reset."""
    src = inspect.getsource(c)
    measure = src.index("play_secs = self._tts_bytes_sent / 8000.0")
    window = src[measure:measure + 200]
    assert "self._tts_bytes_sent = 0" in window


def test_the_counter_is_only_incremented_in_one_place():
    """More than one writer means another lifetime to reason about."""
    src = inspect.getsource(c)
    assert src.count("self._tts_bytes_sent +=") == 1
