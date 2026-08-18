"""
The second door into the dead-air defect fixed in fdc9a8b.

`_handle_dtmf` cancels the speech watchdog on every digit before it knows
whether any handler wants it (see
test_discarded_dtmf_keeps_the_watchdog.py for the full mechanism and the live
call that exposed it).  fdc9a8b rearmed it on the `dtmf_digit_discarded` exit.

That was one of eight exits sitting after the cancel.  Seven of the others are
fine - they speak, queue a synthetic transcript, or call on_question_asked, and
each of those rearms the watchdog on its own.  One is not:

    [ms_conn] theorem_v3: slot DTMF digit=%r - no mapping, ignored

A digit pressed against a live slot map that maps to nothing is logged and
returned.  Nothing is spoken, nothing is queued, the watchdog stays cancelled,
and - unlike the discard exit - the lost keypress was not even counted.

Concretely: the caller is read three slots, presses 7, and gets the same
silence-then-greeting-reset as on CA9a405cd2d6249fd3e3748630d1f2cec2.

Note what this branch has ALREADY done by the time it decides there is no
mapping: it popped v3_slot_dtmf_active, v3_dtmf_slot_map and
v3_awaiting_slot_selection ("Disarm regardless - one press = one selection").
So the caller has also lost the map.  That is deliberate and is left alone
here; this fix is about the silence, not about whether an unmapped press
should consume the map.  Once the watchdog is rearmed the caller hears their
question again and can answer it verbally, which works.

The loss is recorded under its own reason - `dtmf_slot_no_mapping`, not
`dtmf_digit_discarded` - so the two doors stay countable apart in the
[ms_lost] CALL SUMMARY row.
"""

import asyncio
import time

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    SlotMapStage,
    WebSocketCallHandler,
)


SLOT_MAP = {"1": "Monday at 9am", "2": "Monday at 2pm", "3": "Tuesday at 10am"}


def _slot_session() -> dict:
    """A caller who has just been read a numbered list of slots."""
    return {
        "clinic_id": "jv_v1",
        "state": "PRESENT_TIMES",
        "v3_phone_dtmf_active": False,
        "v3_slot_dtmf_active": True,
        "v3_dtmf_slot_map": dict(SLOT_MAP),
        "v3_awaiting_slot_selection": True,
        "phone_confirmed": True,
        "last_bot_prompt": (
            "I can do Monday at 9am, Monday at 2pm, or Tuesday at 10am - "
            "which of those suits you?"
        ),
    }


def _handler(session: dict) -> WebSocketCallHandler:
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAslotmapdoor0000000000000000000"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h.booking_flow_active = True
    h.slot_map_stage = SlotMapStage.TIME_SELECTION
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


async def _arm_watchdog(h: WebSocketCallHandler, q_gen: int = 21) -> float:
    sh = h._silence_handler
    armed_at = time.time()
    sh._watchdog_q_gen = q_gen
    sh._watchdog_armed_at = armed_at
    sh._q_gen = q_gen
    sh._no_input_watchdog_task = asyncio.create_task(
        sh._no_input_watchdog(armed_at, q_gen),
        name="ms_silence_no_input_watchdog",
    )
    await asyncio.sleep(0)
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

async def test_an_unmapped_slot_digit_leaves_the_watchdog_running():
    """Press 7 against a 1-3 map: silence must not be the outcome."""
    h = _handler(_slot_session())
    await _arm_watchdog(h)

    await h._handle_dtmf({"dtmf": {"digit": "7"}})

    try:
        task = h._silence_handler._no_input_watchdog_task
        assert task is not None and not task.cancelled(), (
            "an unmapped slot digit cancelled the speech watchdog and nothing "
            "rearmed it - the caller is left in silence until the 10s backstop, "
            "which resets them to the greeting"
        )
    finally:
        await _drain(h)


async def test_the_unmapped_digit_is_counted_under_its_own_reason():
    """
    It was not counted at all.  A separate reason keeps this door countable
    apart from the dtmf_digit_discarded one in the CALL SUMMARY row.
    """
    session = _slot_session()
    h = _handler(session)
    await _arm_watchdog(h)
    try:
        await h._handle_dtmf({"dtmf": {"digit": "7"}})
        lost = session.get("utterances_lost", {})
        assert lost.get("dtmf_slot_no_mapping") == 1, (
            f"unmapped slot digit not tallied under its own reason: {lost!r}"
        )
        assert "dtmf_digit_discarded" not in lost, (
            "the two doors must stay countable apart"
        )
    finally:
        await _drain(h)


async def test_the_deadline_is_preserved_not_restarted():
    """Same reasoning as the discard exit: a fresh window lands after the
    backstop and changes nothing the caller hears."""
    h = _handler(_slot_session())
    armed_at = await _arm_watchdog(h)

    await asyncio.sleep(0.05)
    await h._handle_dtmf({"dtmf": {"digit": "7"}})

    try:
        sh = h._silence_handler
        assert (
            sh._no_input_watchdog_task is not None
            and not sh._no_input_watchdog_task.cancelled()
        ), "no watchdog is live, so there is no deadline to preserve"
        assert sh._watchdog_armed_at == pytest.approx(armed_at, abs=1e-6), (
            "the watchdog was rearmed with a fresh deadline instead of the "
            "original one"
        )
    finally:
        await _drain(h)


# -- the behaviour that must NOT change ------------------------------------

async def test_a_mapped_slot_digit_still_selects_the_slot():
    """
    The mapped case is the whole point of the branch and must be untouched:
    the label goes to the transcript queue and the map is consumed.
    """
    session = _slot_session()
    h = _handler(session)
    await _arm_watchdog(h)

    await h._handle_dtmf({"dtmf": {"digit": "2"}})

    try:
        assert not h.transcript_queue.empty(), (
            "a mapped slot digit must still inject its label as a transcript"
        )
        _ts, label = h.transcript_queue.get_nowait()
        assert label == SLOT_MAP["2"], f"wrong slot injected: {label!r}"
        assert session.get("utterances_lost", {}) == {}, (
            "a mapped digit is not a lost utterance"
        )
        assert "v3_dtmf_slot_map" not in session, (
            "one press = one selection: the map should have been consumed"
        )
    finally:
        await _drain(h)
