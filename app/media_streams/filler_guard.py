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
    Filler guard gated on booking_flow_active.

    Plays a primary clip after `delay_ms` if the LLM hasn't responded.
    If the LLM still hasn't responded `second_delay_ms` after the primary
    clip finishes, plays a second clip (falls back to the primary clip if
    no dedicated second clip is configured).

    arm()      — start (or restart) the delay timer.
    cancel()   — stop the timer; no-op if clip already fired.
    has_played — True if the primary clip was sent this turn.
    """

    def __init__(
        self,
        clip_path: Path,
        send_audio: Callable[[bytes], Awaitable[None]],
        clip_path_2: "Path | None" = None,
        second_delay_ms: int = 2500,
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

        # Second clip — falls back to primary if not provided or missing
        if clip_path_2 and clip_path_2.exists():
            self._clip_2: bytes = clip_path_2.read_bytes()
            logger.info(
                "[filler_guard] second clip loaded %d bytes from %s",
                len(self._clip_2), clip_path_2,
            )
        else:
            # Reuse primary clip; a repeated "one moment" beats silence
            self._clip_2 = self._clip
            if clip_path_2:
                logger.info(
                    "[filler_guard] second clip not found (%s) — will reuse primary",
                    clip_path_2,
                )

        self._second_delay_s = second_delay_ms / 1000.0
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

        # Every turn starts with the clip unspoken. `with_filler` reads this to
        # decide whether its own opening phrase would just be a second way of
        # saying what the caller already heard — see
        # `filler_phrases.with_filler(skip_primary=...)`. Reset here rather than
        # in _fire() so a turn where the clip never fires clears the previous
        # turn's flag.
        session["_filler_clip_spoke_this_turn"] = False

        # Gate 1: only fire on booking_flow_active turns.
        if not session.get("booking_flow_active"):
            return

        # Gate 2: clip must be present.
        if not self._clip:
            return

        _delay_s        = delay_ms / 1000.0
        _second_delay_s = self._second_delay_s
        _clip           = self._clip
        _clip_2         = self._clip_2
        _send           = self._send_audio
        _session        = session

        async def _fire() -> None:
            await asyncio.sleep(_delay_s)
            self._played = True
            # Set at the moment audio actually goes out, not at arm() time: a
            # turn whose LLM answers inside 350ms cancels this task before the
            # sleep returns, and nothing was spoken, so with_filler must still
            # be allowed its own phrase.
            _session["_filler_clip_spoke_this_turn"] = True
            logger.info("[ms_filler] clip firing (delay=%dms)", delay_ms)
            await _send(_clip)
            # Wait to see if the LLM responds; if not, play second clip.
            # cancel() will interrupt this sleep when the first TTS chunk arrives.
            await asyncio.sleep(_second_delay_s)
            logger.info(
                "[ms_filler] second clip firing (still no LLM response after %.1fs)",
                _second_delay_s,
            )
            await _send(_clip_2)

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
