"""
End-of-day booking digest.

Because Carepatron has no public API, Susie books into a Google Calendar that
Carepatron two-way syncs (events land as busy-blocks, not real appointments).
This job closes the loop for the agency: once a day, after the last appointment,
it reads that calendar for the day's events and emails a clean list so the team
can enter each booking into Carepatron as a proper appointment.

Design mirrors the reminder worker (app/notifications/scheduler.py):
  - a background asyncio loop, started at app startup,
  - Redis-optional and SMTP-optional (both degrade to a safe no-op),
  - idempotent: a Redis marker ensures exactly one digest per calendar day even
    though the loop ticks every few minutes.

Config is data-driven per clinic (operational.digest in clinic.json), with env
overrides so each new clinic is just data:
    operational.digest = {
        "enabled": true,
        "email_to": "bookings@clinic.co.uk",   # or list
        "hour": 21, "minute": 30               # London local send time
    }
Env overrides: DIGEST_EMAIL_TO, DIGEST_HOUR, DIGEST_MINUTE, DIGEST_ENABLED.
The deployment's clinic is MEDIA_STREAMS_CLINIC_ID (tokens are global per
deployment, so the digest reads that deployment's connected calendar).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)

LONDON_TZ = pytz.timezone("Europe/London")
_LAST_SENT_KEY = "digest:last_sent:"  # + clinic_id  → "YYYY-MM-DD"


# ---------------------------------------------------------------------------
# Config resolution (clinic.json operational.digest, with env overrides)
# ---------------------------------------------------------------------------

def _digest_config(clinic: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(clinic.get("digest") or {})

    env_enabled = os.getenv("DIGEST_ENABLED")
    enabled = (
        env_enabled.strip().lower() in ("1", "true", "yes")
        if env_enabled is not None
        else bool(cfg.get("enabled", True))
    )

    recipients_raw = os.getenv("DIGEST_EMAIL_TO") or cfg.get("email_to") or ""
    if isinstance(recipients_raw, list):
        recipients = [str(r).strip() for r in recipients_raw if str(r).strip()]
    else:
        recipients = [r.strip() for r in str(recipients_raw).replace(";", ",").split(",") if r.strip()]

    return {
        "enabled": enabled,
        "recipients": recipients,
        "hour": int(os.getenv("DIGEST_HOUR", cfg.get("hour", 21))),
        "minute": int(os.getenv("DIGEST_MINUTE", cfg.get("minute", 30))),
        # Closing instruction line. Defaults to the original Carepatron wording so
        # the JV/Carepatron digest is byte-identical; a clinic overrides it (e.g.
        # Vital Edge, which doesn't use Carepatron).
        "action_line": (
            os.getenv("DIGEST_ACTION_LINE")
            or cfg.get("action_line")
            or "Please enter each of these into Carepatron as an appointment."
        ),
        # only_pending=True lists ONLY booking requests (events whose summary is a
        # PENDING/booking row or that carry a Phone line), not every event on the
        # calendar. Needed for provisional clinics whose availability calendar also
        # holds open, unbooked slots. Off by default → JV unaffected.
        "only_pending": bool(cfg.get("only_pending", False)),
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _extract_field(description: str, label: str) -> str:
    """Pull a 'Label: value' line out of an event description ('' if absent)."""
    for line in (description or "").splitlines():
        if line.strip().lower().startswith(f"{label.lower()}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _event_start_label(event: Dict[str, Any]) -> str:
    start = event.get("start", {})
    iso = start.get("dateTime")
    if not iso:
        return "All day"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(LONDON_TZ)
        return dt.strftime("%H:%M")
    except Exception:
        return iso


def build_digest(
    events: List[Dict[str, Any]],
    clinic_name: str,
    day: datetime,
    action_line: str = "Please enter each of these into Carepatron as an appointment.",
) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the day's events."""
    day_label = day.strftime("%A %-d %B %Y") if os.name != "nt" else day.strftime("%A %d %B %Y")
    count = len(events)
    subject = f"{clinic_name} — {count} booking{'s' if count != 1 else ''} for {day_label}"

    if not events:
        text = (
            f"{clinic_name}\nBookings for {day_label}\n\n"
            "No bookings in the calendar for this day.\n"
        )
        html = (
            f"<h2>{clinic_name}</h2><p><strong>Bookings for {day_label}</strong></p>"
            "<p>No bookings in the calendar for this day.</p>"
        )
        return subject, text, html

    text_rows: List[str] = []
    html_rows: List[str] = []
    for ev in events:
        time_label = _event_start_label(ev)
        summary = (ev.get("summary") or "").strip() or "(no title)"
        desc = ev.get("description") or ""
        phone = _extract_field(desc, "Phone")
        location = _extract_field(desc, "Location")
        extras = " | ".join(p for p in [phone and f"Phone: {phone}", location and location] if p)

        text_rows.append(f"  {time_label}  {summary}" + (f"\n         {extras}" if extras else ""))
        html_rows.append(
            f"<tr><td style='padding:4px 12px;white-space:nowrap;'><strong>{time_label}</strong></td>"
            f"<td style='padding:4px 12px;'>{summary}"
            + (f"<br><span style='color:#666;font-size:13px;'>{extras}</span>" if extras else "")
            + "</td></tr>"
        )

    text = (
        f"{clinic_name}\nBookings for {day_label} ({count})\n\n"
        + "\n".join(text_rows)
        + f"\n\n{action_line}\n"
    )
    html = (
        f"<h2>{clinic_name}</h2>"
        f"<p><strong>Bookings for {day_label} ({count})</strong></p>"
        "<table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;'>"
        + "".join(html_rows)
        + "</table>"
        f"<p style='color:#666;font-size:13px;'>{action_line}</p>"
    )
    return subject, text, html


# ---------------------------------------------------------------------------
# Send (one calendar day)
# ---------------------------------------------------------------------------

async def send_daily_digest(clinic_id: str, day: Optional[datetime] = None) -> bool:
    """
    Build and email the booking digest for `clinic_id` for `day` (default: today
    in London). Returns True if an email was sent. Safe: returns False (no raise)
    if disabled, unconfigured, or the calendar/email call fails.
    """
    from app.clinic_config import get_clinic
    from app.tools.calendar_google import list_events_for_day
    from app.tools.receptionist_tools import _get_tokens, _save_gcal_tokens
    from app.notifications.email import send_email

    clinic = get_clinic(clinic_id)
    cfg = _digest_config(clinic)
    if not cfg["enabled"]:
        logger.info("[DIGEST] disabled for %s — skipping", clinic_id)
        return False
    if not cfg["recipients"]:
        logger.warning(
            "[DIGEST] no recipient configured for %s (set operational.digest.email_to "
            "or DIGEST_EMAIL_TO) — skipping", clinic_id,
        )
        return False

    calendar_id = clinic.get("calendar_id")
    if not calendar_id:
        logger.warning("[DIGEST] clinic %s has no calendar_id — skipping", clinic_id)
        return False

    tokens = await _get_tokens()
    if not tokens:
        logger.warning(
            "[DIGEST] Google not connected (no tokens) for %s — cannot read calendar, "
            "skipping", clinic_id,
        )
        return False

    day = day or datetime.now(LONDON_TZ)
    try:
        events = await asyncio.to_thread(list_events_for_day, tokens, day, calendar_id)
        await _save_gcal_tokens(tokens)  # persist any token refresh
    except Exception as e:
        logger.error("[DIGEST] failed to read calendar for %s: %r", clinic_id, e, exc_info=True)
        return False

    # Provisional/availability calendars also hold open, unbooked slots — only
    # list actual booking requests when the clinic asks for it.
    if cfg["only_pending"]:
        events = [
            ev for ev in events
            if str(ev.get("summary") or "").strip().upper().startswith("PENDING")
            or _extract_field(ev.get("description") or "", "Phone")
        ]

    subject, text, html = build_digest(
        events, clinic.get("sms_name") or clinic_id, day, action_line=cfg["action_line"]
    )
    return await send_email(cfg["recipients"], subject, text, html)


# ---------------------------------------------------------------------------
# Background worker (once-per-day, idempotent)
# ---------------------------------------------------------------------------

async def start_digest_worker(interval_seconds: int = 300) -> None:
    """
    Poll loop: after the configured London send-time each day, send the digest
    once. Idempotent via a Redis marker (digest:last_sent:<clinic_id> = date).
    Started from app startup; no-op if no deployment clinic / digest disabled.
    """
    clinic_id = (os.getenv("MEDIA_STREAMS_CLINIC_ID") or "").strip().lower()
    if not clinic_id:
        logger.info("[DIGEST] MEDIA_STREAMS_CLINIC_ID unset — digest worker not started")
        return

    from app.clinic_config import get_clinic
    cfg = _digest_config(get_clinic(clinic_id))
    if not cfg["enabled"]:
        logger.info("[DIGEST] disabled for %s — worker not started", clinic_id)
        return

    logger.info(
        "[DIGEST] worker started for %s — daily at %02d:%02d London → %s",
        clinic_id, cfg["hour"], cfg["minute"], cfg["recipients"] or "(no recipient yet)",
    )

    while True:
        try:
            # Re-read config each tick so clinic.json edits take effect without a restart.
            cfg = _digest_config(get_clinic(clinic_id))
            now = datetime.now(LONDON_TZ)
            send_at = now.replace(hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0)
            today_str = now.strftime("%Y-%m-%d")

            if now >= send_at and await _not_yet_sent(clinic_id, today_str):
                sent = await send_daily_digest(clinic_id, now)
                if sent:
                    await _mark_sent(clinic_id, today_str)
                    logger.info("[DIGEST] sent digest for %s (%s)", clinic_id, today_str)
                # If not sent (e.g. Google not connected yet), we DON'T mark —
                # so it retries next tick and self-heals once tokens exist.
        except Exception as e:
            logger.error("[DIGEST] worker error: %r", e, exc_info=True)

        await asyncio.sleep(interval_seconds)


async def _not_yet_sent(clinic_id: str, today_str: str) -> bool:
    try:
        from app.storage.redis_store import redis_client
        if not redis_client:
            return True  # no Redis → best-effort; may double-send across restarts
        last = await redis_client.get(_LAST_SENT_KEY + clinic_id)
        return last != today_str
    except Exception:
        return True


async def _mark_sent(clinic_id: str, today_str: str) -> None:
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.setex(_LAST_SENT_KEY + clinic_id, 60 * 60 * 48, today_str)
    except Exception as e:
        logger.warning("[DIGEST] could not persist last-sent marker: %r", e)
