"""
B-151 — northgate, 2026-09-10 20:57, CAc9a7976f516be8d816c367500a0fd130,
build 2658f7272216.  103 s, outcome=abandoned, judge 2.

NUMBERING.  This defect was called "B-149" in
docs/plan/SLOT_PRESENTATION_FINISH_2026-09-10.md rev. 6.  B-149 was already
taken, on 6 Sep, by the promised-work defect in
test_b149_b150_a_refused_day_after_narrowing.py — a head that promised a Tuesday
lookup before Susie talked about Monday.  Two live defects under one id is how a
fix gets reported closed against the wrong exhibit, so the dead-air defect is
B-151 from here.  B-150 was the highest number in use.

Reported as "about 8 seconds of silence, which is the watchdog firing".  It was
13.7 seconds, and the watchdog was not firing: it was never armed.  Measured on
that call, nothing was armed for 24.4 s — from the WATCHDOG_CANCEL at
20:58:12.370 to the next WATCHDOG_START at 20:58:36.773.

    20:58:15.024   +0.00s  'Apologies for that -' ENDS.  LAST AUDIO.
    20:58:15.270   +0.25s  barge-in partial "nothing's there" -> tts_inhibit set
    20:58:16.571   +1.55s  FINAL 'nothing serious though'    -> queued, not dequeued
    20:58:19.769   +4.74s  tts_inhibit: DISCARDING 'ankles can be tricky...'
    20:58:19.794   +4.77s  every chunk dropped before TTS
    20:58:22.357   +7.33s  LAT turn_seq=2 outcome=no_content content_ttfa=-1
    20:58:24.569   +9.54s  barge-in 'have'                   -> tts_inhibit AGAIN
    20:58:25.569  +10.54s  FINAL 'hello'  <- the caller thinks the line dropped
    20:58:25.938  +10.91s  tts_inhibit: DISCARDING 'Ankles can be a bit...'
    20:58:28.741  +13.72s  'Sorry, still with you -'  FIRST AUDIO SINCE 20:58:15

The loop is self-sustaining and the caller's own rescue attempt is what kills
the next reply: barge-in sets tts_inhibit, the reply is discarded, the silence
makes the caller speak, that speech is a new barge-in, the next reply is
discarded.  'hello' at +10.54s is what killed turn 2's answer.

WHY NOTHING BROKE THE LOOP.  on_speech_started(stt_source=True) cancels the
watchdog on the PARTIAL — speculatively, before anyone knows whether the
barge-in yields a turn at all.  Arming is then handed to on_tts_finished() by
    WATCHDOG_DEFERRED_CLEAR reason=tts_still_playing
and on_tts_finished() never runs, because on_tts_started() sits BELOW the
_tts_loop's tts_inhibit check and every chunk of that turn took the `continue`.
The safety net is armed BY SPEECH, so it is missing exactly on the turns that
produce none.

This is B-67 recurring through a different door.  B-67 closed the door where the
final is GARBAGE and gets dropped at the socket boundary, and its repair hangs
on _is_garbage_fc.  This call's final was real — 'nothing serious though' — and
WAS enqueued; it simply arrived while the loop was mid-turn, so
_resolve_barge_in() did not run for another 3.3 s and the in-flight reply died
in that window.  The garbage gate cannot see this case.  The codebase already
describes the mechanism verbatim in B-67's own comment, and the exhibit beside
it (CAa0f76e2c, Vital Edge, 20 Aug) is a caller who also said "hello" into the
same silence.

THE REPAIR is _rearm_no_input_watchdog(), not _restart_timer().  The watchdog
here was cancelled SPECULATIVELY, which is the exact case that function's
docstring was written for (_handle_dtmf "cancelled the watchdog before knowing
whether it needed to"), and it keeps the ORIGINAL q_gen and deadline rather than
handing the caller a fresh window on a question they were already answering.
_no_input_watchdog computes its deadline as
    max(armed_at, last_engagement_at, _watchdog_grace_until) + wait
so a caller who has just spoken still gets a full window from their last word —
test_the_caller_is_not_re_asked_the_instant_they_stop_talking pins that down.

_restart_timer() also arms on this state (measured, both candidates were run
against the reproduction below).  It was not chosen: it cancels and recreates
the W1/W2/W3 task, resets currently_reasking and re-runs every Spec Z gate.
connection.py is 18k lines and frozen; the smaller diff wins.

THE DISCRIMINATOR IS session["tts_inhibit"].  It must NOT be "arm whenever no
watchdog is live at on_llm_finished" — that would arm on every ordinary turn
whose audio is still playing and take the deliberate DEFERRED_CLEAR hand-off
away.  tts_inhibit is the flag whose entire purpose is "discard this turn's
chunks", so an LLM turn that completes while it is still set is precisely a turn
whose speech went nowhere.
test_an_ordinary_turn_still_hands_arming_to_the_tts_callback is the guard.

NOT CAUSED BY THE 10 SEP WORK.  Nothing shipped that day touches barge-in,
tts_inhibit or the watchdog; the pacing commit adds one `speed` kwarg.  The
mechanism is documented from 20 Aug.
"""

import asyncio
import os
import time

import pytest

from app.media_streams.connection import SilenceHandler


WAIT = 4.5   # NO_INPUT_WATCHDOG_SEC default.  Asserted at the bottom of this
             # file so a config drift fails loudly here rather than quietly
             # changing what the deadline assertions are measuring.


@pytest.fixture(autouse=True)
def _watchdog_enabled(monkeypatch):
    """The harness may set NO_INPUT_WATCHDOG_SEC=0 to keep the suite quiet.
    This file is about whether the watchdog arms, so it has to be on."""
    monkeypatch.setenv("NO_INPUT_WATCHDOG_SEC", str(WAIT))


def _handler(state: str = "PRESENT_TIMES") -> SilenceHandler:
    session = {
        "clinic_id": "northgate",
        "state": state,
        "flow_step": 4,
        "tts_inhibit": False,
    }
    sh = SilenceHandler(
        tts_text_queue=asyncio.Queue(),
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    sh._test_session = session          # tests reach the dict through the handler
    return sh


def _live(sh: SilenceHandler) -> bool:
    return (
        sh._no_input_watchdog_task is not None
        and not sh._no_input_watchdog_task.done()
    )


def _cancel_all(sh: SilenceHandler) -> None:
    for task in (sh._no_input_watchdog_task, sh._task, sh._recovery_task):
        if task is not None and not task.done():
            task.cancel()


async def _ask_a_question(sh: SilenceHandler) -> None:
    """Susie asks, the audio finishes, a watchdog is armed against it."""
    sh.on_question_asked("Which of those would you like?")
    sh.on_tts_started()
    sh.on_tts_finished("Which of those would you like?")
    await asyncio.sleep(0)
    assert _live(sh), "precondition: a question with finished audio arms a watchdog"


async def _the_silent_turn(sh: SilenceHandler) -> None:
    """20:58:15.270 onwards — the caller talks over Susie, and the reply she
    generates for them is discarded chunk by chunk, so on_tts_started() (which
    sits below the _tts_loop inhibit check) is never reached and its paired
    on_tts_finished() never fires."""
    sh.on_tts_started()                       # 'Apologies for that —'
    await asyncio.sleep(0)
    sh.on_speech_started(stt_source=True)     # the barge-in PARTIAL
    await asyncio.sleep(0)
    sh._test_session["tts_inhibit"] = True    # set at barge-in start
    sh.on_llm_started()
    await asyncio.sleep(0)
    sh.on_llm_finished()                      # the reply exists; nobody hears it
    await asyncio.sleep(0)


# ── the regression ──────────────────────────────────────────────────────────

async def test_a_turn_whose_every_chunk_was_discarded_leaves_a_safety_net():
    """The defect in one assertion.  Before the fix this call has no timer, no
    watchdog and no speech: the only exit is the caller speaking again, which is
    what the caller did at +10.54s — and doing so killed the next reply too."""
    sh = _handler()
    await _ask_a_question(sh)
    await _the_silent_turn(sh)

    assert _live(sh), (
        "the turn generated a reply, every chunk of it was discarded by "
        "tts_inhibit, and nothing armed a watchdog — the caller heard 13.7 s "
        "of silence and abandoned the call"
    )
    _cancel_all(sh)


async def test_the_barge_in_cancels_the_watchdog_before_the_turn_is_known():
    """Anchors WHY the net is missing: the cancel is on the PARTIAL, so it has
    already happened before there is any turn to hand arming to.  If this stops
    being true, the repair below is aimed at the wrong seam."""
    sh = _handler()
    await _ask_a_question(sh)
    sh.on_tts_started()
    await asyncio.sleep(0)

    sh.on_speech_started(stt_source=True)
    await asyncio.sleep(0)

    assert not _live(sh), (
        "on_speech_started(stt_source=True) during TTS is expected to cancel "
        "the watchdog speculatively — that cancellation is the thing the "
        "silent turn must undo"
    )
    _cancel_all(sh)


async def test_the_original_question_generation_is_kept():
    """_rearm_no_input_watchdog, not _restart_timer: the caller is still being
    asked the SAME question, so the re-ask budget for that q_gen must carry over
    rather than resetting and letting Susie prompt twice."""
    sh = _handler()
    await _ask_a_question(sh)
    q_gen_before = sh._watchdog_q_gen
    armed_at_before = sh._watchdog_armed_at

    await _the_silent_turn(sh)

    assert _live(sh)
    assert sh._watchdog_q_gen == q_gen_before, (
        f"the question did not change, so neither should the generation the "
        f"watchdog is bound to ({sh._watchdog_q_gen} != {q_gen_before})"
    )
    assert sh._watchdog_armed_at == pytest.approx(armed_at_before), (
        "the ORIGINAL deadline must be restored — a fresh window here would "
        "hand the caller a brand new wait on a question they already answered"
    )
    _cancel_all(sh)


async def test_the_caller_is_not_re_asked_the_instant_they_stop_talking():
    """Keeping the original armed_at is only safe because _no_input_watchdog
    takes max(armed_at, last_engagement_at, grace).  The caller HAS just spoken,
    so the effective deadline must still be a full window from their last word —
    otherwise this fix trades 13.7 s of silence for Susie talking over them."""
    sh = _handler()
    await _ask_a_question(sh)
    await _the_silent_turn(sh)

    assert _live(sh)
    effective = max(
        sh._watchdog_armed_at, sh.last_engagement_at, sh._watchdog_grace_until
    ) + WAIT
    assert effective - time.time() > 1.0, (
        f"the watchdog would speak {effective - time.time():.2f}s from now — "
        f"the caller only just finished a sentence"
    )
    _cancel_all(sh)


# ── guards: the repair must not fire anywhere else ──────────────────────────

async def test_an_ordinary_turn_still_hands_arming_to_the_tts_callback():
    """The load-bearing negative.  A normal turn reaches on_llm_finished() with
    audio still playing and no watchdog live, and that is CORRECT — arming
    belongs to on_tts_finished() so the window starts when the caller has
    actually heard the question.  Arm here and every ordinary turn gets a
    watchdog measured from before its own audio."""
    sh = _handler()
    await _ask_a_question(sh)
    sh.on_tts_started()
    await asyncio.sleep(0)
    sh.on_speech_started(stt_source=True)
    await asyncio.sleep(0)
    assert not _live(sh)

    sh.on_llm_started()
    await asyncio.sleep(0)
    sh.on_llm_finished()                  # tts_inhibit stays False: chunks WILL play
    await asyncio.sleep(0)

    assert not _live(sh), (
        "nothing was discarded on this turn, so its chunks are about to play "
        "and on_tts_finished() owns the arming — the backstop must be gated on "
        "tts_inhibit, not on 'no watchdog is live'"
    )
    _cancel_all(sh)


async def test_the_llm_turn_itself_is_what_leaves_the_call_unguarded():
    """Measured while writing this file, and it decides what the backstop can
    be gated on: on_llm_started() calls _cancel_timer(), which kills the
    watchdog on EVERY turn — not only barged-in ones.  So by the time
    on_llm_finished() runs there is never a live watchdog to preserve, and the
    whole weight of the gate falls on tts_inhibit.  A backstop that also
    checked "no watchdog is live" would look careful and be a no-op check."""
    sh = _handler()
    await _ask_a_question(sh)
    assert _live(sh)

    sh.on_llm_started()
    await asyncio.sleep(0)

    assert not _live(sh), (
        "on_llm_started -> _cancel_timer cancels the watchdog for the duration "
        "of every LLM turn; the turn is expected to re-arm it on the way out"
    )
    _cancel_all(sh)


async def test_a_live_watchdog_is_never_replaced():
    """Idempotence, for the same reason _restart_timer has it: re-arming a live
    watchdog resets its deadline, and something that keeps doing so on slow
    multi-chunk TTS means it never fires at all.  Asserted against
    _rearm_no_input_watchdog directly because — see the test above — the
    on_llm_finished path can never reach it with one live."""
    sh = _handler()
    await _ask_a_question(sh)
    original = sh._no_input_watchdog_task

    sh._rearm_no_input_watchdog(sh._watchdog_armed_at, sh._watchdog_q_gen)
    await asyncio.sleep(0)

    assert sh._no_input_watchdog_task is original, (
        "a watchdog was already counting down for this question; replacing it "
        "restarts its clock"
    )
    _cancel_all(sh)


async def test_nothing_is_armed_after_teardown():
    """Bug 3's rule, which every arming path in this class honours: a late
    callback must not raise a task on a call that has already gone."""
    sh = _handler()
    await _ask_a_question(sh)
    _cancel_all(sh)
    sh._no_input_watchdog_task = None
    sh._cancelled = True
    sh._test_session["tts_inhibit"] = True

    sh.on_llm_started()
    await asyncio.sleep(0)
    sh.on_llm_finished()
    await asyncio.sleep(0)

    assert not _live(sh), "the call is tearing down; nothing may be armed"


async def test_a_question_that_already_had_its_audible_re_ask_is_not_re_armed():
    """The spam cap.  _restart_timer retires a q_gen after one audible re-ask
    (WATCHDOG_RETIRED_FOR_QGEN) and _rearm_no_input_watchdog carries the same
    guard; the backstop must not become a way around it, or a caller on a bad
    line gets 'still with you' on a loop."""
    sh = _handler()
    await _ask_a_question(sh)
    _cancel_all(sh)
    sh._no_input_watchdog_task = None
    sh._no_input_reask_count = 1          # its one audible re-ask already played
    sh._test_session["tts_inhibit"] = True

    sh.on_llm_started()
    await asyncio.sleep(0)
    sh.on_llm_finished()
    await asyncio.sleep(0)

    assert not _live(sh), (
        "this question generation has spent its audible re-ask; the next "
        "recovery must wait for a new question to advance q_gen"
    )


def test_the_watchdog_wait_is_still_the_value_this_file_asserts_against():
    """If NO_INPUT_WATCHDOG_SEC's default moves, the deadline arithmetic above
    is measuring something else.  Fail here rather than there."""
    assert os.getenv("NO_INPUT_WATCHDOG_SEC") == str(WAIT)
