# app/media_streams/filler_guard.py
"""
FillerGuard — plays a short pre-synthesised µ-law clip on Acuity
availability turns only.

Lifecycle per turn
──────────────────
  1. arm(session)          — called immediately after STT delivers a
                             transcript and before the LLM task starts.
                             No-op unless session["booking_flow_active"] is True.
  2. cancel()              — called when the first audio chunk from the LLM
                             is about to reach the caller. Stops the timer if
                             it hasn't fired yet; if it already fired, does
                             nothing (interrupting short audio sounds worse).
  3. has_played            — True if the clip actually sent this turn. Caller
                             injects 100 ms of silence after the clip so the
                             LLM response doesn't start abruptly.

has_played and the internal task are reset at the top of every arm() call so
a fresh turn always starts clean.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class FillerGuard:
    """
    Single-clip filler guard gated on booking_flow_active.

    arm()    — start (or restart) the delay timer.
    cancel() — stop the timer; no-op if clip already fired.
    has_played — True if the clip was sent this turn.
    """

    def __init__(
        self,
        clip_path: Path,
        send_audio: Callable[[bytes], Awaitable[None]],
    ) -> None:
        if clip_path.exists():
            self._clip: bytes = clip_path.read_bytes()
            logger.info(
                "[filler_guard] loaded %d bytes from %s",
                len(self._clip), clip_path,
            )
        else:
            self._clip = b""
            logger.warning(
                "[filler_guard] clip not found: %s — filler disabled until clip is generated",
                clip_path,
            )

        self._send_audio = send_audio
        self._task: "asyncio.Task | None" = None
        self._played: bool = False

    async def arm(self, session: dict, delay_ms: int = 350) -> None:
        """
        Start the filler timer.

        Guards:
          - session["booking_flow_active"] must be True; otherwise no-op.
          - clip must be loaded; otherwise no-op.

        Cancels any in-flight timer from a previous arm() call and resets
        has_played so every turn starts with a clean slate.
        """
        # Always cancel any previous timer and reset state.
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        self._played = False

        # Gate 1: only fire on booking_flow_active turns.
        if not session.get("booking_flow_active"):
            return

        # Gate 2: clip must be present.
        if not self._clip:
            return

        _delay_s = delay_ms / 1000.0
        _clip    = self._clip
        _send    = self._send_audio

        async def _fire() -> None:
            await asyncio.sleep(_delay_s)
            self._played = True
            logger.info("[ms_filler] clip firing (delay=%dms)", delay_ms)
            await _send(_clip)

        self._task = asyncio.create_task(_fire(), name="ms_filler_guard")

    def cancel(self) -> None:
        """
        Cancel the pending timer.

        If the clip has already started playing, do nothing — interrupting
        a short clip sounds worse than letting it finish naturally.
        """
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    @property
    def has_played(self) -> bool:
        """True if the clip was actually sent this turn."""
        return self._played
