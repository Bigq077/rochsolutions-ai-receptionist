# app/notifications/owner_alert.py
"""
Real-time owner (practitioner) SMS alerts for important call events.

Sends a concise heads-up to the clinic owner — e.g. Marcus at Joint Venture —
the moment a booking is confirmed (before the patient's confirmation SMS) or a
booking needs manual entry.

Self-gating and data-driven: does NOTHING unless the clinic's config carries an
``operational.owner_alerts`` block (flattened onto the clinic contract as
``clinic["owner_alerts"]``) that enables the given event. Clinics without the
block are unaffected — every call is a no-op for them.

Theorem is no longer one of those clinics: it gained the block on 2026-08-05,
and the Acuity executors gained the call sites to go with it. Enabling an event
in config is only half the wiring — check there is a ``notify_owner`` call on
the executor the clinic actually runs. Theorem short-circuits to the Acuity
executors before the Google-Calendar ones, so for a long time it had neither.

Config shape (in clinic.json → operational.owner_alerts):
    {
      "enabled": true,
      "phone": "+447586605462",        # falls back to clinic transfer_phone
      "events": ["booking", "manual_followup"]
    }
"""

import logging
from typing import Any, Dict, Optional

from app.notifications.sms import send_sms
from app.clinic_config import get_clinic

logger = logging.getLogger(__name__)


def owner_alerts_enabled(clinic: Dict[str, Any], event: str) -> bool:
    """True if this clinic wants an owner alert for `event`. Cheap, side-effect free."""
    cfg = clinic.get("owner_alerts") or {}
    if not cfg.get("enabled"):
        return False
    return event in set(cfg.get("events") or [])


def _resolve_owner_phone(clinic: Dict[str, Any]) -> str:
    cfg = clinic.get("owner_alerts") or {}
    return (cfg.get("phone") or clinic.get("transfer_phone") or "").strip()


def _build_message(
    event: str,
    patient_name: str,
    when_label: str,
    service: str,
    location: str,
    is_new_patient: bool,
    note: str,
) -> str:
    who = patient_name.strip() or "A patient"
    svc = (service or "appointment").strip()
    loc = (location or "").replace("_", " ").strip().title()
    when = when_label.strip()

    parts = []
    if event == "manual_followup":
        head = f"⚠️ Booking needs manual entry: {who}"
    elif event == "cancellation":
        head = f"❌ Cancellation: {who}"
    elif event == "reschedule":
        head = f"🔄 Reschedule (new time): {who}"
    else:  # "booking"
        head = f"🗓️ New booking: {who}"
    parts.append(head)

    detail = svc
    if when:
        detail += f", {when}"
    if loc:
        detail += f" ({loc})"
    parts.append(detail)

    if is_new_patient:
        parts.append("NEW patient")
    if event == "manual_followup":
        parts.append("calendar not connected — please enter manually")
    if note.strip():
        parts.append(f"Note: {note.strip()}")

    return ". ".join(p for p in parts if p) + ". — Susie"


async def notify_owner(
    session: Dict[str, Any],
    *,
    event: str,
    patient_name: str = "",
    when_label: str = "",
    service: str = "",
    location: str = "",
    is_new_patient: bool = False,
    note: str = "",
) -> bool:
    """
    Send a heads-up SMS to the clinic owner. Returns True if an SMS was sent.

    No-op (returns False) when the clinic has no owner_alerts config for `event`
    or no owner number is set. Never raises — a failed alert must never fail the
    booking that triggered it.
    """
    try:
        clinic = get_clinic(session.get("clinic_id"))
        if not owner_alerts_enabled(clinic, event):
            return False

        owner_phone = _resolve_owner_phone(clinic)
        if not owner_phone:
            logger.warning(
                "owner_alert: %s enabled for clinic=%r but no owner phone set — skipping",
                event, session.get("clinic_id"),
            )
            return False

        message = _build_message(
            event, patient_name, when_label, service, location, is_new_patient, note
        )
        sid = await send_sms(to=owner_phone, message=message)
        if sid:
            logger.info(
                "owner_alert sent [%s] → ***%s (clinic=%r)",
                event, owner_phone[-4:], session.get("clinic_id"),
            )
            return True
        logger.warning("owner_alert [%s] send returned no SID — not sent", event)
        return False
    except Exception as e:
        logger.warning("owner_alert [%s] failed (non-fatal): %r", event, e)
        return False
