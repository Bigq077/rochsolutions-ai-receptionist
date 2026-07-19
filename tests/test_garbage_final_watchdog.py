"""Regression: a garbage / noise-only STT final must NOT cancel the no-input
watchdog.

RC-2 (2026-07-19, jv_v1 name-capture dead-air)
----------------------------------------------
On a real booking call the caller's name was dropped by STT to a single
garbage final ``'n'``.  ``_on_final_transcript_clear`` marked it as prompt
speech and cancelled the per-question no-input watchdog, so the fast re-ask
never fired and the caller sat in ~13s of dead air until the slow global
safety net kicked in with a flow-resetting "how can I help today?".

The fix gates the ``_mark_prompt_speech_detected("final", …)`` call in
``_on_final_transcript_clear`` with the SAME ``_is_garbage_transcript``
predicate ``on_transcript_received`` already uses, so a garbage final leaves
the watchdog running.  These tests drive the REAL method against a minimal
stub + a REAL ``SilenceHandler`` holding a REAL watchdog task, and assert the
end-to-end invariant: garbage preserves the watchdog, a real answer cancels it.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.media_streams import connection as conn


def _make_handler_stub():
    """Minimal `self` exposing exactly what `_on_final_transcript_clear` touches,
    wired to a real SilenceHandler in the production pre-speech state."""
    q: asyncio.Queue = asyncio.Queue()
    sh = conn.SilenceHandler(tts_text_queue=q, trigger_transfer_fn=lambda: None)
    # Mirrors _reset_prompt_speech_guard(): the just-emitted prompt reset the
    # per-prompt guard before the caller spoke.  (Not set in __init__.)
    sh.prompt_speech_detected = False
    sh.prompt_last_speech_ts = None

    stub = types.SimpleNamespace(
        _barge_in_pending=False,
        _barge_in_ts=0.0,
        _barge_in_duration=0.0,
        _clearing=True,
        _silence_handler=sh,
        _tts_task=None,
        _tts_text_queue=q,
        _last_turn_done_at=0.0,   # >0 would arm the tail-fragment gate; keep off
        _tts_last_start_ts=0.0,
        _tts_audio_done_at=0.0,
        session={},
    )
    return stub, sh


async def _live_watchdog() -> None:
    """Stand-in no-input watchdog: cancelling it surfaces as task.cancelled()."""
    await asyncio.sleep(3600)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.parametrize("garbage", ["n", "a", "mm", "uh"])
async def test_garbage_final_does_not_cancel_watchdog(garbage: str):
    stub, sh = _make_handler_stub()
    wd = asyncio.create_task(_live_watchdog())
    sh._no_input_watchdog_task = wd
    await asyncio.sleep(0)  # let the watchdog task start running
    try:
        await conn.WebSocketCallHandler._on_final_transcript_clear(stub, garbage)
        await asyncio.sleep(0)  # deliver any (erroneous) cancellation

        assert not wd.cancelled(), (
            f"garbage final {garbage!r} cancelled the no-input watchdog "
            "(RC-2 regression)"
        )
        assert not wd.done()
        assert sh.prompt_speech_detected is False, (
            f"garbage final {garbage!r} was marked as prompt speech"
        )
    finally:
        await _cancel(wd)


async def test_real_final_cancels_watchdog():
    """Positive control: a genuine answer MUST still cancel the watchdog so an
    in-flight re-ask cannot talk over the caller."""
    stub, sh = _make_handler_stub()
    wd = asyncio.create_task(_live_watchdog())
    sh._no_input_watchdog_task = wd
    await asyncio.sleep(0)
    try:
        await conn.WebSocketCallHandler._on_final_transcript_clear(
            stub, "Quentin Rock"
        )
        await asyncio.sleep(0)

        assert wd.cancelled(), (
            "a real name final did not cancel the watchdog — the fix over-reached"
        )
        assert sh.prompt_speech_detected is True
    finally:
        if not wd.done():
            await _cancel(wd)
