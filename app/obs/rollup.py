"""
app/obs/rollup.py
-----------------
Send the daily alert roll-up (spec §5.2 — benign short calls / retry storms are
batched, not alerted per-call).

    python -m app.obs.rollup

Sends one summary of the buffered low-severity alerts to the operator channels
and clears the buffer. Intended to be run once a day (e.g. via cron / the existing
daily digest). No-op when OBS_ALERTS_ENABLED is off or the buffer is empty.

Note: the buffer is in-process, so this command is only meaningful in a long-lived
worker that accumulated the day's alerts. The durable source of truth for these
events is the Phase 1 `calls` table; a future dashboard (Phase 5) reads from there.
"""
from __future__ import annotations

import asyncio

from app.obs import alerts


def main() -> int:
    summary = asyncio.run(alerts.flush_daily_rollup())
    if summary is None:
        print("No roll-up to send (disabled or buffer empty).")
    else:
        print("Roll-up sent:\n" + summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
