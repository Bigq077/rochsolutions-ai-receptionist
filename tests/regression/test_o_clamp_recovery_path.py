"""Regression: what the dead-air clamp hands off to must actually re-arm.

``test_o_impossible_play_duration`` proves the arithmetic -- a corrupt
``_tts_bytes_sent`` is clamped down instead of scheduling the finish callback
26.7 s out.  It proves nothing about what happens *next*.

That matters, because on every call observed to date the clamp has stayed
**inert**: ``IMPOSSIBLE play duration`` has never been seen in a clinic log.
The sequence it exists to trigger --

    clamp fires -> _delayed_tts_finished runs early -> a watchdog is armed
    -> the caller gets a re-prompt

-- is therefore an *unexercised code path guarding the worst failure mode in
the system*.  It is also not a straight line: ``_delayed_tts_finished`` puts
six guards between the callback and the watchdog, and the clamp fires the
callback EARLY BY CONSTRUCTION -- while the audio it describes is still playing
out of Twilio's buffer.  Firing early is exactly the condition several of those
guards exist to suppress.

This file drives the real ``SilenceHandler`` (not a mock of it) through that
path and asserts the property the clamp is supposed to buy:

    **after the clamp fires, a real watchdog task is armed.**

WHAT IS DELIBERATELY *NOT* CLAIMED HERE
---------------------------------------
That the outcome is *good*.  The clamped ceiling for the live 175-char chunk is
21.5 s, and the watchdog then waits its own window on top.  The clamp converts
"stranded with no watchdog, forever" into "recovers, slowly".  These tests pin
the former; the latter is item O's residual and is recorded in
``docs/plan/OPEN_DEFECTS_2026-08-22.md``.

WHY THESE ASSERT AN OUTCOME AND NOT A MECHANISM
-----------------------------------------------
Arming is layered and fails OPEN: spec-W direct-arm, then a BACKSTOP arm on an
outstanding question, then T-3.  Disabling any one of them just routes to the
next.  Verified by mutation:

    A  clamp constants -> 6.0/5.0 ............... 2 fail  (bound is live)
    B  _ooo_force_fire task not spawned ......... 1 fail  (backstop is live)
    C  on_tts_finished returns immediately ...... 0 fail  <- redundancy
    D  spec-W direct arm disabled ............... 0 fail  <- redundancy
    E  _restart_timer() a no-op ................. 1 fail  (arming is live)

C and D surviving is a property of the code, not a hole in the tests: asserting
"spec-W armed it" would pin an implementation detail and would have gone red on
a refactor that kept the caller perfectly safe.  E is the mutation that matters
-- kill every arming path and ``test_clamped_callback_arms_a_watchdog`` fails.
Re-run E before trusting any future edit to this file.
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _clamp_play_secs,
)

# The real chunk from CA268397d4, recovered from obs -- 175 characters, three
# sub-chunks of 49/61/63 behind ONE sentinel.
#
# IMPORTED, never re-typed.  Writing this string out by hand produced a
# 149-character paraphrase that ended on a question, where the real one ends on
# a STATEMENT ("...speak to him.").  That single difference decides whether the
# spec-W direct-arm path engages, so a hand-copied chunk tests a call that never
# happened -- the exact failure that made the first version of the clamp inert.
# One source of truth, or this file rots the same way.
from tests.regression.test_o_impossible_play_duration import (  # noqa: E402
    LIVE_CHUNK,
    LIVE_PLAY_SECS,
)

assert len(LIVE_CHUNK) == 175, (
    "the live chunk is no longer 175 characters -- every bound in this file "
    "and its sibling was measured against that length"
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _make_handler():
    """A stub `self` carrying only what _delayed_tts_finished touches.

    The SilenceHandler is the REAL one -- it owns every guard that decides
    whether a watchdog arms, so faking it would test nothing.
    """
    armed = []
    q = asyncio.Queue()

    sh = SilenceHandler(
        tts_text_queue=q,
        trigger_transfer_fn=lambda *a, **k: None,
        on_reask=lambda *a, **k: armed.append(("reask", a, k)),
    )
    # Freshly-asked question: the "late TTS callback" guard in on_tts_finished
    # suppresses arming when the caller spoke >1 s after the question was set,
    # which is not the scenario under test (the caller here is silent).
    sh._last_question_set_at = time.time()
    sh.last_audio_received_at = sh._last_question_set_at - 5.0

    conn = types.SimpleNamespace(
        _tts_gen=1,
        _tts_chunks_completed=set(),
        _silence_handler=sh,
        _flow=types.SimpleNamespace(is_complete=lambda: False),
        _current_chunk_seq=0,
        _tts_expected_final_seq=1,
        _tts_pending_terminal=0,
        _tts_pending_terminal_text="",
        _tts_pending_terminal_chunk_start_ts=0.0,
        _tts_playout_end_mono=0.0,
        _tts_audio_done_at=0.0,
        _last_audio_or_transcript_ts=0.0,
        _clinical_response_active=True,
        tts_text_queue=q,
        session={},
    )
    return conn, sh, armed


async def _fire(conn, delay, text, seq=1, gen=1):
    """Run the real _delayed_tts_finished against the stub, without sleeping.

    Only the callback's OWN ``await asyncio.sleep(delay)`` is collapsed.  The
    patch is lifted before yielding, so a task the method spawns
    (``_ooo_force_fire``) starts and then PARKS on a real sleep rather than
    completing instantly.

    That distinction is the whole point.  Collapsing the backstop's sleep makes
    it force-fire inside the same tick, clearing ``_tts_pending_terminal`` back
    to 0 -- which reads exactly like "the stash never happened" and silently
    destroys the out-of-order tests below.  It cost a debugging cycle; do not
    re-collapse it.
    """
    real_sleep = asyncio.sleep
    slept = []

    async def _no_sleep(d, *a, **k):
        slept.append(d)
        return await real_sleep(0)

    asyncio.sleep = _no_sleep          # type: ignore[assignment]
    try:
        await WebSocketCallHandler._delayed_tts_finished(
            conn, delay, text, gen, 0.0, -1, seq,
        )
    finally:
        asyncio.sleep = real_sleep     # type: ignore[assignment]
    # Now let any spawned task reach its first (real) await and park there.
    await real_sleep(0)
    return slept


async def _drain_tasks():
    """Cancel anything still parked so tasks don't leak between tests."""
    for t in list(asyncio.all_tasks()):
        if t is not asyncio.current_task() and not t.done():
            t.cancel()
    await asyncio.sleep(0)


def _watchdog_live(sh):
    t = getattr(sh, "_no_input_watchdog_task", None)
    return t is not None and not t.done()




# ---------------------------------------------------------------------------
# 1. The clamp fires, and the callback it schedules actually arms something
# ---------------------------------------------------------------------------

async def test_clamped_callback_arms_a_watchdog():
    """The whole point of clamping: a watchdog exists afterwards.

    Without the clamp this callback is scheduled 26.7 s out, is still pending
    when the turn ends, and NOTHING arms -- that is the 19 s of dead air.
    """
    conn, sh, _ = _make_handler()
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    assert clamped < LIVE_PLAY_SECS, "precondition: the clamp must have fired"
    assert not _watchdog_live(sh), "precondition: nothing armed yet"

    await _fire(conn, clamped, LIVE_CHUNK, seq=1)

    # Assert the watchdog TASK specifically, not merely that some field became
    # truthy -- an OR over `last_question` would pass vacuously if seeding moved
    # elsewhere, and this is the one assertion the whole fix rests on.
    assert _watchdog_live(sh), (
        "the clamped callback ran to completion but armed no watchdog -- the "
        "clamp would then be cosmetic: it shortens a timer that leads nowhere"
    )
    assert sh.last_question, "armed with no prompt to re-ask"


async def test_the_callback_sleeps_the_clamped_duration_not_the_corrupt_one():
    """The clamp must reach the scheduler, not just the log line."""
    conn, _, _ = _make_handler()
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    slept = await _fire(conn, clamped, LIVE_CHUNK, seq=1)
    assert slept and slept[0] == pytest.approx(clamped, abs=0.01)
    assert slept[0] < LIVE_PLAY_SECS


# ---------------------------------------------------------------------------
# 2. Firing early must not corrupt the turn it lands in
# ---------------------------------------------------------------------------

async def test_clamped_fire_does_not_replay_a_reask_phrase():
    """A re-ask must never become the prompt the watchdog re-arms on.

    The clamped callback fires while audio is still playing, which is precisely
    when a stale re-ask could still be in flight.  on_tts_finished refuses to
    restart the timer for a re-ask phrase; assert the clamp did not find a way
    around that.
    """
    conn, sh, _ = _make_handler()
    reask = "Sorry, I didn't quite catch that. Would you like to book one?"
    await _fire(conn, _clamp_play_secs(30.0, reask), reask, seq=1)
    assert sh.last_question != reask, (
        "a re-ask phrase became the armed question -- the caller would be "
        "re-asked with the re-ask itself"
    )


async def test_stale_generation_callback_is_still_ignored_after_clamping():
    """Clamping must not resurrect a barge-in'd chunk.

    Barge-in advances _tts_gen.  Because the clamp makes the callback fire
    EARLIER, it lands closer to the barge-in it should be ignored for -- so this
    guard matters more after the clamp than before it.
    """
    conn, sh, _ = _make_handler()
    conn._tts_gen = 7                      # caller barged in since scheduling
    await _fire(conn, _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK),
                LIVE_CHUNK, seq=1, gen=1)  # stale gen
    assert not _watchdog_live(sh), (
        "a stale (barged-in) chunk armed a watchdog -- Susie would re-ask a "
        "question the caller has already answered"
    )


async def test_retired_watchdog_is_not_restarted_by_a_clamped_callback():
    """A retired watchdog must stay retired -- else the call loops re-asking."""
    conn, sh, _ = _make_handler()
    sh._watchdog_has_retired = True
    await _fire(conn, _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK),
                LIVE_CHUNK, seq=1)
    assert not _watchdog_live(sh)


# ---------------------------------------------------------------------------
# 3. The multi-chunk case -- where the clamp can be silently defeated
# ---------------------------------------------------------------------------

async def test_terminal_clamped_early_past_a_pending_chunk_is_not_dropped():
    """The failure mode the clamp is most likely to hit in production.

    The live corrupt turn had THREE chunks.  Clamping the terminal makes it
    fire before earlier chunks have completed, which is case 2 of the
    out-of-order guard: the terminal is stashed in _tts_pending_terminal and
    the method RETURNS WITHOUT ARMING.

    That is the original defect's shape -- terminal fired, nothing armed -- so
    the clamp must not simply relocate it.  Recovery then rests entirely on
    _ooo_force_fire, so assert a backstop was actually scheduled.
    """
    conn, sh, _ = _make_handler()
    conn._tts_expected_final_seq = 3
    conn._tts_chunks_completed = {1}          # chunk 2 still outstanding
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    # Mirror the send loop: the playout clock is advanced by the CLAMPED value.
    conn._tts_playout_end_mono = time.monotonic() + clamped

    before = len(asyncio.all_tasks())
    await _fire(conn, clamped, LIVE_CHUNK, seq=3)
    after = len(asyncio.all_tasks())

    try:
        if conn._tts_pending_terminal == 3:
            # Stashed: nothing has armed yet, so a backstop MUST exist.
            assert after > before, (
                "terminal was stashed with no backstop task -- nothing will "
                "ever arm and the call is stranded exactly as in CA268397d4"
            )
            assert not _watchdog_live(sh), (
                "sanity: the stash path must not also have armed directly"
            )
        else:
            assert _watchdog_live(sh)
    finally:
        await _drain_tasks()


async def test_the_backstop_is_anchored_to_the_same_clock_the_corruption_inflated():
    """Records why the clamp cannot be relied on to recover *promptly*.

    ``_ooo_force_fire`` waits until ``_tts_playout_end_mono + 3.0``.  That clock
    is advanced from the (clamped) corrupt duration -- so the backstop inherits
    the inflation rather than correcting it.  The clamp caps how bad that is;
    it does not make recovery quick.
    """
    conn, sh, _ = _make_handler()
    conn._tts_expected_final_seq = 3
    conn._tts_chunks_completed = {1}
    clamped = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    conn._tts_playout_end_mono = time.monotonic() + clamped

    await _fire(conn, clamped, LIVE_CHUNK, seq=3)
    try:
        if conn._tts_pending_terminal != 3:
            pytest.skip("terminal armed in-order; backstop not exercised")
        # The backstop's own wait, recomputed the way the code computes it.
        backstop_wait = max(
            2.0, (conn._tts_playout_end_mono + 3.0) - time.monotonic()
        )
        assert backstop_wait > 3.0, (
            "if the backstop now recovers within the dead-air bar, item O's "
            "residual is closed -- update OPEN_DEFECTS and replace this test"
        )
    finally:
        await _drain_tasks()


async def test_a_late_chunk_resolves_the_stashed_terminal():
    """The stash must be redeemable -- the resolver is the primary recovery.

    This is the path that should normally fire: the straggler lands well before
    the backstop's timer, and releases the terminal.
    """
    conn, sh, _ = _make_handler()
    conn._tts_expected_final_seq = 3
    conn._tts_chunks_completed = {1}
    conn._tts_playout_end_mono = time.monotonic() + 20.0

    await _fire(conn, 0.1, LIVE_CHUNK, seq=3)          # terminal, out of order
    try:
        assert conn._tts_pending_terminal == 3, "precondition: terminal stashed"

        await _fire(conn, 0.1, "middle chunk", seq=2)  # the straggler lands

        assert conn._tts_pending_terminal == 0, (
            "the straggler did not release the stashed terminal -- the "
            "watchdog never arms and the caller sits in silence"
        )
    finally:
        await _drain_tasks()


# ---------------------------------------------------------------------------
# 4. The residual: the clamp bounds damage, it does not meet the dead-air bar
# ---------------------------------------------------------------------------

def test_the_clamped_ceiling_is_recorded_not_assumed():
    """Pins the residual so it cannot be quietly forgotten.

    CLAUDE.md section 6.2 sets the bar at no dead air over 3 s.  The clamped
    ceiling for the live chunk is ~21.5 s BEFORE the watchdog's own window is
    added.  This test does not demand the bar be met -- it fails if anyone
    comes to believe it already is.
    """
    ceiling = _clamp_play_secs(LIVE_PLAY_SECS, LIVE_CHUNK)
    assert ceiling > 3.0, (
        "the clamped ceiling now meets the 3 s dead-air bar -- if this is "
        "deliberate, update item O's residual in OPEN_DEFECTS and delete this "
        "test; if it is accidental, real speech is being cut off"
    )
    # ~9 s of real speech; the rest is permitted dead air.
    assert ceiling - (len(LIVE_CHUNK) / 19.5) > 5.0


def test_the_bound_is_proportional_so_long_chunks_get_the_loosest_ceiling():
    """Recorded because it is backwards, and non-obvious.

    max_plausible scales with len(text), so the slot presentation -- the longest
    chunk and the turn the whole call turns on -- is granted the largest
    permissible dead air.  There is no absolute cap anywhere in the path.
    """
    short = _clamp_play_secs(999.0, "Okay.")
    long_ = _clamp_play_secs(999.0, "x" * 298)     # a real slot-buffer chunk
    assert long_ > short
    assert long_ > 30.0, (
        "if an absolute cap has been added, this test should be replaced by "
        "one that pins the cap"
    )
