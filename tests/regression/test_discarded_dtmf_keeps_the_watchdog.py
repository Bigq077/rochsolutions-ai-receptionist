"""
JV Bolton live call (2026-08-18, CA9a405cd2d6249fd3e3748630d1f2cec2) — the
caller pressed a key to confirm a booking and got 7.5 s of silence, then a
reset to the top of the call.

Live-call trace:
    13:08:05,593  WATCHDOG_START q_gen=13 wait=10.0s
                  prompt="So that's Quentin Rock, Monday the 24th of August
                          at half pa[st four] ... shall I go ahead and book
                          that in?"
    13:08:09,384  DTMF raw digit='2' v3_phone_dtmf_active=False
    13:08:09,384  DTMF digit received - cancelling speech watchdog
    13:08:09,393  WATCHDOG_CANCEL caller=_handle_dtmf
    13:08:09,393  WATCHDOG_CANCEL q_gen=13
                  ... 7.5 s of nothing ...
    13:08:16,856  [ms_safety_net] 10s dead-air (since=11.3s)
                  "Sorry, I can't quite hear you - how can I help today?"

Root cause: `_handle_dtmf` cancels the speech watchdog on ANY digit, before it
knows whether the digit is wanted.  That is the right thing to do when the
caller has genuinely switched to the keypad channel - a speech re-ask must not
fire on top of keypad entry.  But when the digit reaches no handler it hits the
`dtmf_digit_discarded` early return, and the restart lives further down the
function, past that return.  So the watchdog is cancelled and never rearmed.

The question the caller was answering is left with no dead-air cover at all.
Only the 10 s safety-net backstop recovered the call, and it is not
context-aware: it reset a caller who was mid-booking to "how can I help today?".

Had the watchdog simply been left alone it would have fired at 13:08:15.6 -
1.2 s EARLIER than the backstop did, and with the caller's actual question
replayed instead of a greeting reset.

That last point is why this fix restores the ORIGINAL deadline rather than
arming a fresh window.  A fresh 10 s window from the keypress would have
expired at 13:08:19.4 - later than the backstop that already fired, so the
caller would have heard exactly the same greeting reset and nothing would have
been fixed.  test_the_deadline_is_preserved_not_restarted pins that down.

The deadline is max(armed_at, last_engagement_at, _watchdog_grace_until)
(see `_no_input_watchdog`), and the discard path returns before the handler's
`last_engagement_at` bump - so restoring armed_at restores it exactly.
"""

import asyncio
import time

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    SlotMapStage,
    WebSocketCallHandler,
)


def _live_call_session() -> dict:
    """The session as it stood at 13:08:09 on the live call.

    phone_confirmed=True is the load-bearing part: it was set at 13:07:57
    ("verbal phone confirm - stored calling number ... + phone_confirmed=True"),
    which made `_phone_outstanding` False and so kept the auto-activation block
    above the discard from firing.  That is why the digit was discarded rather
    than appended to the phone number.
    """
    return {
        "clinic_id": "jv_v1",
        "state": "GREETING",
        "flow_step": 0,
        "booking_flow_active": True,
        "phone_confirmed": True,
        "v3_phone_dtmf_active": False,
        "last_bot_prompt": (
            "So that's Quentin Rock, Monday the 24th of August at half past "
            "four - shall I go ahead and book that in?"
        ),
    }


def _handler(session: dict) -> WebSocketCallHandler:
    """A skeletal handler - only what the DTMF discard path actually touches.

    WebSocketCallHandler's real constructor wants a live WebSocket; the discard
    path needs none of it.
    """
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CA9a405cd2d6249fd3e3748630d1f2cec2"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h.booking_flow_active = True
    h.slot_map_stage = SlotMapStage.NONE
    h._tts_task = None                  # no synthesis in flight
    h._dtmf_idle_task = None            # no idle-finalize timer pending
    h._dtmf_near_complete_task = None   # …nor a near-complete one
    h._last_audio_or_transcript_ts = 0.0
    h._silence_handler = SilenceHandler(
        tts_text_queue=h.tts_text_queue,
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    h._silence_handler._tts_playing = False   # …and nothing playing out
    return h


async def _arm_watchdog(h: WebSocketCallHandler, q_gen: int = 13) -> float:
    """Arm a real no-input watchdog the way _restart_timer does, and return the
    armed_at it was given."""
    sh = h._silence_handler
    # time.time(), not the loop clock: the deadline is compared against
    # last_engagement_at, which _handle_dtmf sets from time.time().
    armed_at = time.time()
    sh._watchdog_q_gen = q_gen
    sh._watchdog_armed_at = armed_at    # as _restart_timer records it
    sh._q_gen = q_gen
    sh._no_input_watchdog_task = asyncio.create_task(
        sh._no_input_watchdog(armed_at, q_gen),
        name="ms_silence_no_input_watchdog",
    )
    await asyncio.sleep(0)          # let it start
    return armed_at


async def _drain(h: WebSocketCallHandler) -> None:
    task = h._silence_handler._no_input_watchdog_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# -- the regression --------------------------------------------------------

async def test_a_discarded_digit_leaves_the_watchdog_running():
    """
    The whole defect in one assertion.  Before the fix the watchdog is gone and
    nothing rearms it, so the question the caller was answering has no dead-air
    cover for the rest of its life.
    """
    h = _handler(_live_call_session())
    await _arm_watchdog(h)

    await h._handle_dtmf({"dtmf": {"digit": "2"}})

    task = h._silence_handler._no_input_watchdog_task
    try:
        assert task is not None, (
            "the discarded digit cancelled the speech watchdog and nothing "
            "rearmed it - the caller's question is left with no dead-air "
            "cover, exactly as on CA9a405cd2d6249fd3e3748630d1f2cec2"
        )
        assert not task.cancelled(), "watchdog task was left in a cancelled state"
    finally:
        await _drain(h)


async def test_the_digit_is_still_counted_as_lost():
    """The fix must not quietly swallow the loss metric along with the bug."""
    session = _live_call_session()
    h = _handler(session)
    await _arm_watchdog(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "2"}})
        assert session.get("utterances_lost", {}).get("dtmf_digit_discarded") == 1, (
            "the discarded digit must still be tallied - the [ms_lost] CALL "
            "SUMMARY row is the only thing that made this defect visible"
        )
    finally:
        await _drain(h)


async def test_the_deadline_is_preserved_not_restarted():
    """
    A fresh window is NOT a fix.  On the live call a fresh 10 s window from the
    keypress would have expired at 13:08:19.4 - after the 13:08:16.9 backstop
    that already fired - so the caller would have heard the same greeting reset.
    The rearm has to carry the ORIGINAL armed_at.
    """
    h = _handler(_live_call_session())
    armed_at = await _arm_watchdog(h)

    await asyncio.sleep(0.05)       # keypress lands measurably after arming
    await h._handle_dtmf({"dtmf": {"digit": "2"}})

    try:
        sh = h._silence_handler
        # Liveness first.  _watchdog_armed_at alone proves nothing — this
        # harness sets it when arming, exactly as _restart_timer does, so it
        # survives the cancel as a stale value.  It only describes a real
        # deadline if a watchdog is actually running.
        assert (
            sh._no_input_watchdog_task is not None
            and not sh._no_input_watchdog_task.cancelled()
        ), "no watchdog is live, so there is no deadline to preserve"

        restored = getattr(sh, "_watchdog_armed_at", None)
        assert restored is not None, (
            "the handler does not record armed_at, so the deadline cannot be "
            "restored - see _no_input_watchdog's max(armed_at, ...) deadline"
        )
        assert restored == pytest.approx(armed_at, abs=1e-6), (
            f"the watchdog was rearmed with a FRESH deadline ({restored}) "
            f"instead of the original ({armed_at}). That pushes the re-ask "
            "later than the safety-net backstop and fixes nothing."
        )
    finally:
        await _drain(h)


# -- the behaviour that must NOT change ------------------------------------

async def test_a_wanted_digit_still_cancels_the_watchdog():
    """
    When the caller really has switched to the keypad, the speech watchdog must
    still be cancelled - a "Sorry, I didn't catch that" re-ask on top of live
    keypad entry is the thing the original cancel exists to prevent.
    """
    session = _live_call_session()
    session["v3_phone_dtmf_active"] = True      # keypad entry genuinely live
    h = _handler(session)
    await _arm_watchdog(h)

    await h._handle_dtmf({"dtmf": {"digit": "2"}})

    try:
        assert h._silence_handler._no_input_watchdog_task is None, (
            "a digit that reached the phone buffer must still cancel the "
            "speech watchdog; rearming there would re-ask over keypad entry"
        )
        assert session.get("phone_dtmf_buffer") == "2", (
            "the wanted digit should have reached the phone buffer"
        )
    finally:
        await _drain(h)
