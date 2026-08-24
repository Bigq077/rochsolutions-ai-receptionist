"""Post-write side effects, off the caller's clock.

B-84. On CA98557584dc the caller heard 7.3 seconds of silence after "Just
locking that in now…". The booking itself took 1.0s; the remaining 3.3s was
four side effects the caller was made to wait for even though none of them
changes what Susie says next:

    10:23:27.96  Acuity booking created          <- the only part that matters
    10:23:28.80  owner alert SMS      (0.64s, Twilio round trip)
    10:23:29.55  patient confirm SMS  (0.65s, Twilio round trip)
    10:23:30.20  reminders + Sheets append
    10:23:31.27  tool result finally returned

Everything after the Acuity write is a notification. It belongs here.

Why a module-level set and not the connection's task list
---------------------------------------------------------
`MediaStreamConnection.handle()` cancels every task in its local `tasks` list
at teardown. A confirmation SMS is a promise made to a patient out loud — it
must NOT be cancellable by the caller hanging up two seconds later. These tasks
are therefore deliberately owned here, by the process, and outlive any one call.

`_PENDING` is not bookkeeping: asyncio holds only a WEAK reference to a running
task, so a task nobody references can be garbage-collected mid-flight. Dropping
the set would reintroduce the exact failure this module exists to prevent, at
random, under load. The done-callback removes the entry.

The only remaining loss window is process shutdown, which `drain()` closes —
wired into main.py's shutdown hook.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

# Strong references to in-flight side effects. See module docstring — removing
# this set makes tasks eligible for garbage collection while they are running.
_PENDING: Set[asyncio.Task] = set()

# How long shutdown waits for outstanding notifications before giving up. Long
# enough for a Twilio round trip (~0.7s observed) with headroom; short enough
# that a wedged send cannot hold a deploy open.
DRAIN_TIMEOUT_S = 10.0


def _report(task: asyncio.Task, label: str, call_sid: str) -> None:
    """Log the outcome. A detached failure that logs nothing is invisible."""
    if task.cancelled():
        logger.warning(
            "[bg] %s CANCELLED call_sid=%s — the side effect did not complete",
            label, call_sid,
        )
        return
    exc = task.exception()
    if exc is not None:
        # WARNING, not ERROR: the booking itself is already committed and the
        # caller has been told. This is a failed notification, not a failed
        # write — but it must never pass silently, because the patient is
        # expecting a text.
        logger.warning(
            "[bg] %s FAILED call_sid=%s: %r", label, call_sid, exc, exc_info=exc,
        )
    else:
        logger.info("[bg] %s ok call_sid=%s", label, call_sid)


def run_detached(
    coro: Coroutine[Any, Any, Any],
    *,
    label: str,
    call_sid: str = "",
) -> Optional[asyncio.Task]:
    """Run `coro` without making the caller wait for it.

    Returns the task, or None when there is no running loop — in which case the
    coroutine is closed rather than left un-awaited (a "never awaited" warning
    is how a silently-dropped confirmation SMS would look in the logs).
    """
    try:
        task = asyncio.create_task(coro, name=f"bg:{label}")
    except RuntimeError:
        # No running event loop. Nothing can be scheduled; close the coroutine
        # so it fails loudly here rather than as a warning at interpreter exit.
        coro.close()
        logger.error(
            "[bg] %s NOT SCHEDULED call_sid=%s — no running event loop",
            label, call_sid,
        )
        return None

    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    task.add_done_callback(lambda t: _report(t, label, call_sid))
    return task


async def drain(timeout: float = DRAIN_TIMEOUT_S) -> int:
    """Wait for outstanding side effects. Returns the number still unfinished.

    Called at application shutdown so a deploy does not drop a confirmation SMS
    that was one Twilio round trip from being sent.
    """
    pending = [t for t in _PENDING if not t.done()]
    if not pending:
        return 0
    logger.info("[bg] draining %d outstanding side effect(s)", len(pending))
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        logger.warning(
            "[bg] %d side effect(s) did NOT finish within %.1fs — "
            "these notifications are lost", len(still_pending), timeout,
        )
    return len(still_pending)


def pending_count() -> int:
    """In-flight side effects. For tests and diagnostics."""
    return len([t for t in _PENDING if not t.done()])
