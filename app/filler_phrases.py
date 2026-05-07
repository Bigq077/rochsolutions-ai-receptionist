# app/filler_phrases.py
"""
Filler phrases played concurrently during API latency (availability check,
booking write) so dead air doesn't sound like a crash.

Two lists:
  - THINKING_FILLERS_PRIMARY   — played when check_availability is called
  - THINKING_FILLERS_SECONDARY — played if the API takes > 4 seconds
  - BOOKING_WRITE_FILLERS      — played when book_appointment is called

Usage:
    from app.filler_phrases import with_filler, THINKING_FILLERS_PRIMARY, BOOKING_WRITE_FILLERS
    result = await with_filler(executor(args, session), THINKING_FILLERS_PRIMARY, session, tts_fn)
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, List

logger = logging.getLogger(__name__)

_MAX_FILLER_CHARS = 400

THINKING_FILLERS_PRIMARY: List[str] = [
    "Let me have a look at what we've got…",
]

THINKING_FILLERS_SECONDARY: List[str] = [
    "Nearly there…",
    "Just a moment longer…",
    "Almost got it…",
]

BOOKING_WRITE_FILLERS: List[str] = [
    "Getting that all booked in for you…",
    "Just locking that in now…",
    "Popping that in the diary…",
]


def pick_filler(filler_list: List[str], used: list) -> str:
    """
    Pick an unused filler phrase from filler_list.

    If all phrases have been used, resets the used pool and picks again
    so fillers never run dry.

    Args:
        filler_list: Pool of candidate phrases.
        used:        List of already-used phrases this call (mutable — modified
                     in place).  Stored as a JSON-serialisable list in session.

    Returns:
        A phrase no longer than _MAX_FILLER_CHARS characters.
    """
    available = [f for f in filler_list if f not in used]
    if not available:
        used.clear()
        available = list(filler_list)
    choice = random.choice(available)
    used.append(choice)
    # ElevenLabs 400-char safety gate
    if len(choice) > _MAX_FILLER_CHARS:
        choice = choice[:_MAX_FILLER_CHARS]
    return choice


async def with_filler(
    api_coro: Coroutine,
    filler_list: List[str],
    session: dict,
    tts_fn: Callable[[str], Coroutine],
) -> Any:
    """
    Run api_coro concurrently with a filler phrase on the TTS queue.

    - Picks a primary filler and puts it on the TTS queue immediately.
    - Awaits the API coroutine with a 4-second timeout.
    - If the API hasn't finished in 4s, picks a secondary filler and queues it.
    - Waits for the API to complete, then returns its result.
    - If the API raises an exception, re-raises it after the filler completes.

    Args:
        api_coro:    The slow API coroutine (e.g. check_availability(args, session)).
        filler_list: Primary filler list to draw from.
        session:     Call session dict — must contain "used_fillers" (list).
        tts_fn:      Async callable (text: str) -> coroutine — queues TTS text.

    Returns:
        Whatever api_coro returns.
    """
    used = session.setdefault("used_fillers", [])
    filler_text = pick_filler(filler_list, used)

    # Queue primary filler immediately (non-blocking — returns once enqueued)
    filler_task = asyncio.create_task(tts_fn(filler_text))

    # Run the API coroutine with a 4-second "slow API" watch
    api_task = asyncio.create_task(api_coro)
    done, pending = await asyncio.wait([api_task], timeout=4.0)

    if not done:
        # API still running after 4s — play a secondary filler
        secondary = pick_filler(THINKING_FILLERS_SECONDARY, used)
        logger.info("[filler] API slow (>4s) — secondary filler: %r", secondary)
        await tts_fn(secondary)

    # Ensure primary filler task is done before we return
    try:
        await asyncio.wait_for(asyncio.shield(filler_task), timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    # Collect API result — re-raise any exception
    if done:
        result = done.pop()
        if result.exception():
            raise result.exception()
        return result.result()
    else:
        # API was still running — await pending task now
        return await list(pending)[0]
