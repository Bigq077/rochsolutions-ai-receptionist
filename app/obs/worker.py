"""
app/obs/worker.py
-----------------
Scheduler for the daily whole-system call digest (app/obs/digest.py).

WHY THIS EXISTS (divergence from `main`): upstream schedules the digest with a
Render Cron Job, because upstream's blueprint has one. This service doesn't — but it
already runs a daily email on a background worker (app/notifications/digest.py, the
end-of-day booking digest), started from app startup and made idempotent with a Redis
marker. Reusing that proven pattern means no second Render service, no duplicated
env vars, and one way of doing daily email on this deployment (CLAUDE.md rule 7).

Design mirrors app/notifications/digest.py exactly:
  - a background asyncio loop, started at app startup,
  - Redis-optional and SMTP-optional (both degrade to a safe no-op),
  - idempotent: a Redis marker ensures exactly one digest per calendar day even
    though the loop ticks every few minutes,
  - never raises out of the loop; a bad tick is logged and retried.

Gated on OBS_DIGEST_ENABLED (default OFF), so merging this changes nothing until it
is explicitly turned on. Read-only over the `calls` table — it cannot touch the live
call path.

Env:
    OBS_DIGEST_ENABLED   "true" to start the worker at all (default false)
    OBS_DIGEST_HOUR      London local send hour, default 7
    OBS_DIGEST_MINUTE    London local send minute, default 0
    OBS_DIGEST_HOURS     lookback window in hours, default 24
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)

LONDON_TZ = pytz.timezone("Europe/London")
_LAST_SENT_KEY = "obs:digest:last_sent"


def is_enabled() -> bool:
    return (os.getenv("OBS_DIGEST_ENABLED", "false") or "").strip().lower() == "true"


def _send_time() -> tuple:
    try:
        hour = int(os.getenv("OBS_DIGEST_HOUR", "7") or "7")
        minute = int(os.getenv("OBS_DIGEST_MINUTE", "0") or "0")
    except ValueError:
        hour, minute = 7, 0
    return max(0, min(23, hour)), max(0, min(59, minute))


def _lookback_hours() -> int:
    try:
        return max(1, int(os.getenv("OBS_DIGEST_HOURS", "24") or "24"))
    except ValueError:
        return 24


async def _not_yet_sent(today_str: str) -> bool:
    try:
        from app.storage.redis_store import redis_client
        if not redis_client:
            return True  # no Redis → best-effort; may double-send across restarts
        last = await redis_client.get(_LAST_SENT_KEY)
        return last != today_str
    except Exception:
        return True


async def _mark_sent(today_str: str) -> None:
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.setex(_LAST_SENT_KEY, 60 * 60 * 48, today_str)
    except Exception as e:
        logger.warning("[OBS_DIGEST] could not persist last-sent marker: %r", e)


async def send_now(hours: int) -> bool:
    """Build and send one digest. Returns True if a channel accepted it."""
    from app import config
    from app.obs import digest, emailer

    calls = digest._all_calls(hours)
    if emailer.is_configured():
        subject, body = digest.build_email(calls, hours)
        return await asyncio.to_thread(emailer.send_email, subject, body)

    # No email configured → fall back to the operator SMS (review calls only).
    review = [c for c in calls if c.get("action_needed") == "review"]
    summary = digest.build_summary(review, hours)
    if summary is None:
        return False
    from app.obs import alerts
    return await alerts.review_alert(summary)


async def start_obs_digest_worker(interval_seconds: int = 300) -> None:
    """Poll loop: after the configured London send-time each day, send the digest once.

    Idempotent via a Redis marker (obs:digest:last_sent = date). Started from app
    startup; a no-op unless OBS_DIGEST_ENABLED is set.
    """
    from app import config

    if not is_enabled():
        logger.info("[OBS_DIGEST] OBS_DIGEST_ENABLED not set — worker not started")
        return
    if not config.DATABASE_URL:
        logger.info("[OBS_DIGEST] no OBS_DATABASE_URL — worker not started")
        return

    hour, minute = _send_time()
    logger.info(
        "[OBS_DIGEST] worker started — daily at %02d:%02d London → %s",
        hour, minute, config.OBS_DIGEST_EMAIL_TO or "(SMS fallback)",
    )

    while True:
        try:
            now = datetime.now(LONDON_TZ)
            hour, minute = _send_time()
            send_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            today_str = now.strftime("%Y-%m-%d")

            if now >= send_at and await _not_yet_sent(today_str):
                sent = await send_now(_lookback_hours())
                if sent:
                    await _mark_sent(today_str)
                    logger.info("[OBS_DIGEST] sent digest (%s)", today_str)
                # If not sent (e.g. SMTP not configured yet) we DON'T mark — so it
                # retries next tick and self-heals once creds exist.
        except Exception as e:
            logger.error("[OBS_DIGEST] worker error: %r", e, exc_info=True)

        await asyncio.sleep(interval_seconds)
