"""
Owner notification for provisional bookings.

When a clinic runs the `google_calendar_provisional` booking model (e.g.
Vital Edge Therapy), the AI does NOT confirm appointments. The moment a caller
requests a slot, the owner is pinged with the request details so they can
confirm directly with the client.

Channel-agnostic by design: today it sends via Twilio SMS (the only channel
wired in this codebase). Swapping to Twilio WhatsApp later is a one-function
change inside `_send` — callers of notify_owner() do not change.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _owner_number(clinic: Dict[str, Any]) -> Optional[str]:
    """Resolve the owner notification number from the clinic contract."""
    num = (
        clinic.get("owner_notification_sms")
        or (clinic.get("operational") or {}).get("owner_notification_sms")
        or ""
    )
    return num.strip() or None


async def _send(to: str, message: str) -> Optional[str]:
    """The single channel seam. SMS now; WhatsApp is a drop-in replacement here."""
    from app.notifications.sms import send_sms
    return await send_sms(to=to, message=message)


def build_booking_request_message(
    *,
    clinic: Dict[str, Any],
    patient_name: str,
    phone: str,
    when: datetime,
    duration_minutes: int,
    service: str = "Deep Tissue Massage",
    notes: str = "",
    action: str = "REQUEST",
    calendar_written: bool = True,
) -> str:
    """
    Owner-facing text for a provisional booking request / change.

    `calendar_written=False` is the case this message exists to make visible.
    On a provisional clinic the owner IS the confirmation step, so this text is
    the only thing that reaches them in time to ring the caller back. Until
    2026-08-21 it was byte-identical whether the calendar write had landed or
    thrown — and the write is wrapped in a try/except that logs and continues,
    so a failure (or an expired Google token, which skips the write entirely)
    produced a text reading exactly like a healthy booking. The owner would
    have gone looking for an appointment that was not there.
    """
    when_str = when.strftime("%a %d %b at %H:%M")
    name = (clinic.get("sms_name") or clinic.get("display_name") or "Clinic")
    if not calendar_written:
        # Lead with the failure. It is the one line that changes what the owner
        # has to DO, so it goes first rather than into a footnote they may not
        # read to the end of.
        lines = [
            f"\u26A0\uFE0F {name} — booking {action.lower()}, NOT IN YOUR CALENDAR",
            "The calendar write FAILED — please add this one manually.",
        ]
    else:
        lines = [
            f"\U0001F4C5 {name} — booking {action.lower()} (please confirm with the client)",
        ]
    lines += [
        f"Name: {patient_name or '—'}",
        f"Phone: {phone or '—'}",
        f"Service: {service} ({duration_minutes} min)",
        f"Requested: {when_str}",
    ]
    if notes:
        lines.append(f"Notes: {notes}")
    lines.append("Not yet confirmed — Susie told the caller you'll confirm directly.")
    return "\n".join(lines)


async def notify_owner(clinic: Dict[str, Any], message: str) -> bool:
    """
    Send a notification to the clinic owner. Never raises — returns True/False.
    No-op (returns False) if no owner number is configured.
    """
    to = _owner_number(clinic)
    if not to:
        logger.warning(
            "notify_owner: no owner_notification_sms configured for clinic %r — skipping",
            clinic.get("clinic_id"),
        )
        return False
    try:
        sid = await _send(to, message)
        if sid:
            logger.info("notify_owner: sent to %s (sid=%s)", to, sid)
            return True
        logger.warning("notify_owner: send returned no SID (to=%s)", to)
        return False
    except Exception as e:  # pragma: no cover — must never break the call
        logger.warning("notify_owner failed (non-fatal): %r", e)
        return False
