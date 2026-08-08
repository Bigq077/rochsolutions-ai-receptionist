"""
Regression: the clip path's secondary filler must register in the shared clock.

Two independent fixes for the same defect met in `with_filler` during the
Theorem -> Vital Edge port (2026-08-08), and merging them naively opens a hole
that neither original had:

  latency-eval 265d95e  a COOLDOWN CLOCK. Three producers (connection.py,
                        llm_stream.py, filler_phrases.py) queue hold phrases
                        and none knew about the others, so a reschedule played
                        three in 3.4s. Every producer now records itself via
                        note_filler_played() and checks should_play_filler().

  Theorem      8ce4b74  SKIP_PRIMARY. FillerGuard's pre-recorded clip and
                        with_filler's TTS phrase both cover the same gap, so
                        the caller heard four ways of "let me look" in 4.6s.
                        with_filler now suppresses its opening phrase when the
                        clip already spoke, but KEEPS the 4-second secondary.

Theorem's branch had no clock to register with, because 265d95e was never on
that branch. In the merged world it does, and the >4s secondary it speaks is
real audio in the caller's ear. If it does not call note_filler_played, that
audio is invisible to the other two producers — so connection.py or
llm_stream.py can speak straight over it. That is precisely the defect 265d95e
exists to prevent, reintroduced through the one path that bypasses its check.

The ordering is the other half. `skip_primary` is evaluated BEFORE the cooldown
deliberately: FillerGuard does not call note_filler_played, so the clip is
invisible to the clock today — but if it is ever wired in (a real gap, filed
separately), a cooldown-first ordering would make with_filler return with NO
secondary at all, reopening the dead air O-4 closed. Both halves are pinned
here so a later tidy-up of this function fails instead of passing quietly.
"""
from __future__ import annotations

import time

import pytest

from app.filler_phrases import (
    THINKING_FILLERS_PRIMARY,
    THINKING_FILLERS_SECONDARY,
    should_play_filler,
    with_filler,
)


def _slow_api(monkeypatch):
    """Make the 4-second watch expire immediately, without waiting 4 seconds."""
    import app.filler_phrases as fp

    async def _fake_wait(tasks, timeout=None):
        return set(), set(tasks)

    monkeypatch.setattr(fp.asyncio, "wait", _fake_wait)


async def test_the_clip_paths_secondary_records_itself_in_the_cooldown_clock(
    monkeypatch,
):
    """A spoken secondary that no other producer can see is the 265d95e defect."""
    _slow_api(monkeypatch)

    spoken: list[str] = []
    session: dict = {}

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session=session,
        tts_fn=_tts,
        skip_primary=True,
    )

    assert len(spoken) == 1 and spoken[0] in THINKING_FILLERS_SECONDARY

    assert session.get("_last_filler_ts") is not None, (
        "the clip path spoke a secondary filler but did not record it — "
        "connection.py and llm_stream.py can now talk straight over it"
    )
    assert should_play_filler(session) is False, (
        "a filler that just went out must suppress the next producer"
    )


async def test_skip_primary_is_evaluated_before_the_cooldown(monkeypatch):
    """
    Order pin. A session already inside the cooldown window must STILL get the
    clip path's >4s secondary. If the cooldown check ran first it would return
    early having said nothing, and a slow Acuity round-trip after a recorded
    clip would be silence again.
    """
    _slow_api(monkeypatch)

    spoken: list[str] = []
    # Another producer spoke a moment ago — the cooldown is live.
    session: dict = {
        "_last_filler_ts": time.monotonic(),
        "_last_filler_was_write": False,
    }
    assert should_play_filler(session) is False, "precondition: cooldown is active"

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    result = await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session=session,
        tts_fn=_tts,
        skip_primary=True,
    )

    assert result == "slots"
    assert len(spoken) == 1, (
        "the cooldown swallowed the clip path's secondary — skip_primary must "
        "be evaluated first, or a slow lookup after a clip is dead air"
    )
    assert spoken[0] in THINKING_FILLERS_SECONDARY


async def test_the_normal_path_is_still_gated_by_the_cooldown(monkeypatch):
    """
    The order change must not exempt the ordinary path. With skip_primary
    False and the cooldown live, nothing is spoken — 265d95e unchanged.
    """
    spoken: list[str] = []
    session: dict = {
        "_last_filler_ts": time.monotonic(),
        "_last_filler_was_write": False,
    }

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    result = await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session=session,
        tts_fn=_tts,
        skip_primary=False,
    )

    assert result == "slots"
    assert spoken == [], "the cooldown must still gate the ordinary path"
