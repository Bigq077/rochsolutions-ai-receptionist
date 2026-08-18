"""
Fix B — a reschedule must not speculatively arm phone collection.

`_handle_dtmf`'s auto-activation has two ways in:

    _is_phone_keypad_prompt(last_bot_prompt)   or   _phone_outstanding

The second exists for a good reason (CA9758ceab, 7 Aug): the prompt test
survives silence but not a REPLY, because any turn Susie takes overwrites
`last_bot_prompt`. She said "No rush at all." nine seconds before the caller
typed, and that one sentence removed the only net under eleven digits.

But `_phone_outstanding` is

    booking_flow_active AND NOT phone_confirmed AND NOT phone_entered_by_keypad

and a RESCHEDULE never collects a phone number at all — the patient is looked up
by the number they are calling from. So `phone_confirmed` is never set and the
condition is true for the entire call. On JV
CA29d50a41db9234a16037a5c3f04c836d one stray keypress against "Shall I go ahead
and move it for you?" therefore armed phone collection, buffered the digit, and
(before the stray-buffer hatch) deafened the rest of the call.

The hatch nets that. This closes the cause: while the call is in a
reschedule/cancel lookup, "no number on record" carries no information, so it
must not arm anything. `LOOKUP_PURPOSE_KEY` ("cancel" | "reschedule") is set by
`lookup_patient` and popped the moment any write succeeds, so:

  * booking flows never set it     -> the CA9758ceab net is untouched;
  * reschedule-then-book works     -> the key is gone once the move lands;
  * an explicit keypad ask still arms during a reschedule, because that is the
    FIRST arm and this changes only the second.
"""

import asyncio
import time

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    SlotMapStage,
    WebSocketCallHandler,
)
from app.tools.receptionist_tools import LOOKUP_PURPOSE_KEY


THE_MOVE_CTA = (
    "Just to confirm - I'm moving your appointment to Monday the 31st of "
    "August at half past four. Shall I go ahead and move it for you?"
)
THE_KEYPAD_ASK = (
    "I can't find you on this number - could you type your number on your "
    "keypad? You can press the star key to reset at any time."
)


def _session(**over) -> dict:
    """Mid-reschedule, at the move confirmation, exactly as on the live call."""
    s = {
        "clinic_id": "jv_v1",
        "booking_flow_active": True,
        "phone_confirmed": False,        # a reschedule never sets this
        "v3_phone_dtmf_active": False,
        "last_bot_prompt": THE_MOVE_CTA,
        LOOKUP_PURPOSE_KEY: "reschedule",
    }
    s.update(over)
    return s


def _handler(session: dict) -> WebSocketCallHandler:
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAfixb00000000000000000000000000"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h.booking_flow_active = True
    h.slot_map_stage = SlotMapStage.NONE
    h._tts_task = None
    h._dtmf_idle_task = None
    h._dtmf_near_complete_task = None
    h._last_audio_or_transcript_ts = 0.0
    h._silence_handler = SilenceHandler(
        tts_text_queue=h.tts_text_queue,
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    h._silence_handler._tts_playing = False
    return h


async def _arm(h: WebSocketCallHandler, q_gen: int = 9) -> float:
    sh = h._silence_handler
    at = time.time()
    sh._watchdog_q_gen = q_gen
    sh._watchdog_armed_at = at
    sh._q_gen = q_gen
    sh._no_input_watchdog_task = asyncio.create_task(
        sh._no_input_watchdog(at, q_gen), name="ms_silence_no_input_watchdog"
    )
    await asyncio.sleep(0)
    return at


async def _drain(h: WebSocketCallHandler) -> None:
    t = h._silence_handler._no_input_watchdog_task
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


# -- the fix ---------------------------------------------------------------

@pytest.mark.parametrize("purpose", ["reschedule", "cancel"])
async def test_a_stray_digit_mid_reschedule_does_not_arm_phone_collection(purpose):
    session = _session(**{LOOKUP_PURPOSE_KEY: purpose})
    h = _handler(session)
    await _arm(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "2"}})
        assert not session.get("v3_phone_dtmf_active"), (
            f"a stray digit during a {purpose} armed phone collection - "
            "'no number on record' is true for the whole call there and means "
            "nothing"
        )
        assert not session.get("phone_dtmf_buffer"), (
            "the digit was buffered as a phone digit"
        )
        assert session.get("utterances_lost", {}).get("dtmf_digit_discarded") == 1, (
            "the digit should fall through to the discard path and be counted"
        )
    finally:
        await _drain(h)


async def test_the_discarded_digit_still_keeps_the_watchdog():
    """Fix B must land on the discard path, which fdc9a8b made safe."""
    h = _handler(_session())
    await _arm(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "2"}})
        t = h._silence_handler._no_input_watchdog_task
        assert t is not None and not t.cancelled(), (
            "the digit now reaches the discard path, so it must rearm the "
            "watchdog there"
        )
    finally:
        await _drain(h)


# -- what must NOT change --------------------------------------------------

async def test_an_explicit_keypad_ask_still_arms_during_a_reschedule():
    """
    If Susie cannot find the caller and asks them to type their number, that is
    a real request and must still arm - it is the FIRST arm, untouched here.
    """
    session = _session(last_bot_prompt=THE_KEYPAD_ASK)
    h = _handler(session)
    await _arm(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "0"}})
        assert session.get("v3_phone_dtmf_active"), (
            "an explicit keypad ask during a reschedule must still arm"
        )
        assert session.get("phone_dtmf_buffer") == "0"
    finally:
        await _drain(h)


async def test_an_ordinary_booking_still_arms_speculatively():
    """
    The CA9758ceab net. A booking never sets the lookup purpose, so nothing
    about this path changes: a digit with a number outstanding still arms even
    though last_bot_prompt is no longer the keypad ask.
    """
    session = _session(last_bot_prompt="No rush at all.")
    session.pop(LOOKUP_PURPOSE_KEY)          # a booking, not a reschedule
    h = _handler(session)
    await _arm(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "0"}})
        assert session.get("v3_phone_dtmf_active"), (
            "eleven digits were lost on CA9758ceab exactly here - a booking "
            "with a number outstanding must still arm off state, not prompt"
        )
        assert session.get("phone_dtmf_buffer") == "0"
    finally:
        await _drain(h)


async def test_booking_after_a_completed_reschedule_still_arms():
    """
    reschedule-then-book: LOOKUP_PURPOSE_KEY is popped the moment a write
    succeeds, so the later booking behaves like any other booking.
    """
    session = _session(last_bot_prompt="No rush at all.")
    session.pop(LOOKUP_PURPOSE_KEY)          # popped by the successful move
    h = _handler(session)
    await _arm(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "0"}})
        assert session.get("v3_phone_dtmf_active")
    finally:
        await _drain(h)
