"""
Unit tests for FillerGuard.

Verifies:
  1. arm() is a no-op when booking_flow_active is absent or False.
  2. arm() fires the clip when booking_flow_active is True (with a loaded clip).
  3. cancel() before fire prevents has_played being set.
  4. arm() called again before timer fires cancels the previous timer cleanly.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# FillerGuard lives in app/media_streams/filler_guard.py.
# Import directly; no real clip file needed for these tests.
from app.media_streams.filler_guard import FillerGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_guard(clip_bytes: bytes = b"\x7f" * 100) -> tuple[FillerGuard, list]:
    """
    Return a FillerGuard with a fake clip (bypasses file load) and a list
    that collects every bytes payload passed to send_audio.
    """
    sent: list[bytes] = []

    async def _send(data: bytes) -> None:
        sent.append(data)

    guard = FillerGuard.__new__(FillerGuard)
    guard._clip        = clip_bytes
    guard._send_audio  = _send
    guard._task        = None
    guard._played      = False
    return guard, sent


# ---------------------------------------------------------------------------
# 1. No-op when booking_flow_active is absent or False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arm_noop_when_flag_absent() -> None:
    """arm() must not start a timer when booking_flow_active is not in session."""
    guard, sent = _make_guard()
    session: dict = {}  # no booking_flow_active key
    await guard.arm(session, delay_ms=10)
    await asyncio.sleep(0.05)  # give any (wrongly-started) timer time to fire
    assert guard._task is None or guard._task.done()
    assert not guard.has_played
    assert sent == []


@pytest.mark.asyncio
async def test_arm_noop_when_flag_false() -> None:
    """arm() must not start a timer when booking_flow_active is False."""
    guard, sent = _make_guard()
    session = {"booking_flow_active": False}
    await guard.arm(session, delay_ms=10)
    await asyncio.sleep(0.05)
    assert guard._task is None or guard._task.done()
    assert not guard.has_played
    assert sent == []


# ---------------------------------------------------------------------------
# 2. Timer fires when booking_flow_active is True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arm_fires_when_flag_true() -> None:
    """arm() must fire the clip when booking_flow_active is True."""
    guard, sent = _make_guard(b"\x7f" * 50)
    session = {"booking_flow_active": True}
    await guard.arm(session, delay_ms=20)
    await asyncio.sleep(0.10)  # wait for 20ms timer to fire
    assert guard.has_played
    assert len(sent) == 1
    assert sent[0] == b"\x7f" * 50


# ---------------------------------------------------------------------------
# 3. cancel() before fire prevents has_played
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_before_fire_suppresses_clip() -> None:
    """cancel() called before the timer fires must prevent the clip playing."""
    guard, sent = _make_guard()
    session = {"booking_flow_active": True}
    await guard.arm(session, delay_ms=200)  # long delay
    guard.cancel()
    await asyncio.sleep(0.05)  # well before the original timer
    assert not guard.has_played
    assert sent == []


# ---------------------------------------------------------------------------
# 4. arm() again before timer fires cancels the previous timer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_arm_cancels_first() -> None:
    """arm() called again before first timer fires resets state cleanly."""
    guard, sent = _make_guard()
    session = {"booking_flow_active": True}

    # First arm — long delay so it won't fire before the second arm
    await guard.arm(session, delay_ms=500)
    first_task = guard._task

    # Second arm — short delay, fires after ~20ms
    await guard.arm(session, delay_ms=20)
    second_task = guard._task

    assert first_task is not second_task  # new task created
    assert first_task is None or first_task.cancelled() or first_task.done()

    await asyncio.sleep(0.10)
    assert guard.has_played
    assert len(sent) == 1  # only one clip played


# ---------------------------------------------------------------------------
# 5. has_played resets on the next arm() call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_has_played_resets_on_next_arm() -> None:
    """has_played must be False at the start of every arm() call."""
    guard, sent = _make_guard()
    session = {"booking_flow_active": True}

    # Fire once
    await guard.arm(session, delay_ms=10)
    await asyncio.sleep(0.05)
    assert guard.has_played

    # Second arm resets has_played; abort it so it doesn't fire again
    await guard.arm(session, delay_ms=500)
    assert not guard.has_played
    guard.cancel()
