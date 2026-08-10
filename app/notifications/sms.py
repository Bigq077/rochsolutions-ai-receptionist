"""
SMS sending service using Twilio.
"""

import logging
import os
import re
from typing import Optional
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Eval-only staff SMS redirect
# ---------------------------------------------------------------------------
# latency-eval maps its test line (+447366263180) to clinic_id "jv_v1", so an
# eval call loads Marcus's real config. With SMS_ENABLED=true every owner alert,
# waitlist ping and staff notify would text his actual mobile — dozens of times
# during a latency run.
#
# Set EVAL_STAFF_SMS_TO=<your number> and any SMS addressed to a *configured
# staff number* is redirected there instead. Patient-facing SMS (booking
# confirmations, reminders, follow-ups) are untouched: they go to the caller's
# number, which is not in the staff set.
#
# Destination-based rather than call-site-based on purpose — there are a dozen
# staff-directed send sites and more will be added; matching on the number
# cannot miss one, and a new site inherits the protection for free.
#
# Unset (production default) this is a no-op. Do NOT set it on a live service.
_STAFF_NUMBERS_CACHE: Optional[frozenset] = None


def _staff_numbers() -> frozenset:
    """Every number a clinic config would send staff SMS to, in E.164.

    Built once per process from the clinic registry. Failure is non-fatal and
    yields an empty set, which makes the redirect a no-op rather than
    misrouting a message.
    """
    global _STAFF_NUMBERS_CACHE
    if _STAFF_NUMBERS_CACHE is not None:
        return _STAFF_NUMBERS_CACHE

    found: set = set()
    try:
        from app.clinic_config import CLINICS, TWILIO_TO_CLINIC, get_clinic
        from app.utils import normalise_to_e164

        clinic_ids = set(TWILIO_TO_CLINIC.values()) | set(CLINICS.keys())
        for cid in clinic_ids:
            try:
                clinic = get_clinic(cid) or {}
            except Exception:
                continue
            operational = clinic.get("operational") or {}
            owner_alerts = clinic.get("owner_alerts") or {}
            for raw in (
                clinic.get("transfer_phone"),
                clinic.get("owner_notification_sms"),
                operational.get("transfer_phone"),
                operational.get("owner_notification_sms"),
                owner_alerts.get("phone"),
            ):
                if not raw:
                    continue
                found.add(normalise_to_e164(str(raw)) or str(raw).strip())

        legacy = os.getenv("THEOREM_NOTIFICATION_SMS", "").strip()
        if legacy:
            found.add(normalise_to_e164(legacy) or legacy)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[sms] staff-number scan failed, redirect disabled: %r", exc)
        found = set()

    _STAFF_NUMBERS_CACHE = frozenset(n for n in found if n)
    return _STAFF_NUMBERS_CACHE


def redirect_staff_sms(to: str) -> str:
    """Return the eval override when `to` is a staff number, else `to` unchanged."""
    override = os.getenv("EVAL_STAFF_SMS_TO", "").strip()
    if not override:
        return to

    from app.utils import normalise_to_e164
    normalised = normalise_to_e164(to) or (to or "").strip()
    if normalised not in _staff_numbers():
        return to

    logger.warning(
        "[sms] EVAL_STAFF_SMS_TO active — staff SMS redirected ***%s → ***%s "
        "(this must never be set on a live service)",
        normalised[-4:], override[-4:],
    )
    return override


class SMSService:
    """
    Service for sending SMS messages via Twilio.
    
    Handles errors gracefully to prevent app crashes.
    """
    
    def __init__(
        self,
        account_sid: str = None,
        auth_token: str = None,
        from_number: str = None,
    ):
        """
        Initialize SMS service.
        
        Args:
            account_sid: Twilio Account SID (or from env)
            auth_token: Twilio Auth Token (or from env)
            from_number: Twilio phone number (or from env)
        """
        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = from_number or os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([self.account_sid, self.auth_token, self.from_number]):
            logger.error("Missing Twilio credentials in environment")
            raise ValueError("Missing required Twilio credentials")
        
        self.client = Client(self.account_sid, self.auth_token)
        
        logger.info(f"SMS service initialized with number: {self.from_number}")
    
    async def send_sms(
        self,
        to: str,
        message: str,
        max_length: int = 1600,
    ) -> Optional[str]:
        """
        Send an SMS message.
        
        Args:
            to: Recipient phone number (E.164 format: +447...)
            message: Message content
            max_length: Maximum message length (Twilio limit is 1600)
        
        Returns:
            Message SID if successful, None if failed
        """
        # Global SMS kill switch (env-gated). Every surface — smart follow-up,
        # owner alerts, booking SMS — funnels through this method, so this one
        # gate covers them all.
        #
        # ⚑ LIVE branch (jv_v2): default is ON. Deliberately the opposite of
        # latency-eval, which this branch was cut from and which defaults OFF
        # because it is an isolated timing-eval service that must never text a
        # real caller.
        #
        # The flip back is not optional and it is not cosmetic. theorem-onboarding
        # was cut from latency-eval the same way and inherited the OFF default
        # straight past the comment telling it not to (3b2f195). Mark's line then
        # sent nothing — no booking confirmation, no staff transfer notice, no
        # reminder — and it read as healthy, because "[sms] SMS_ENABLED is off"
        # is exactly what a correct eval branch prints. The prompt closes on
        # "Confirmation text on its way" unconditionally, so every caller was
        # promised a text that was never coming.
        #
        # Defaulting ON means a forgotten Render env var fails in the safe
        # direction for a LIVE clinic, which is sending. Set SMS_ENABLED=false to
        # deliberately silence this service.
        if os.getenv("SMS_ENABLED", "true").strip().lower() not in ("true", "1", "yes", "on"):
            logger.info("[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)")
            return None

        # Normalise to E.164 and validate before sending (#14)
        # Import is lazy (inside the method) to avoid module-load import errors
        # if app.utils is not yet initialised when sms.py is first imported.
        from app.utils import normalise_to_e164, is_valid_e164
        if not is_valid_e164(to):
            normalised = normalise_to_e164(to)
            if normalised:
                logger.info("Phone normalised: %s → %s", to, normalised)
                to = normalised
            else:
                logger.error(
                    "Invalid phone number — SMS aborted: %r", to,
                    extra={"raw_number": to},
                )
                return None

        # Eval-only: divert staff-directed SMS away from the real practitioner.
        # After normalisation so the comparison is E.164 on both sides.
        to = redirect_staff_sms(to)

        # Truncate message if too long
        if len(message) > max_length:
            logger.warning(f"Message truncated from {len(message)} to {max_length} chars")
            message = message[:max_length - 3] + "..."
        
        try:
            # Send message via Twilio
            sms = self.client.messages.create(
                body=message,
                from_=self.from_number,
                to=to,
            )
            
            logger.info(
                "SMS sent successfully",
                extra={
                    "to": to,
                    "message_sid": sms.sid,
                    "status": sms.status,
                    "length": len(message),
                },
            )
            
            return sms.sid
        
        except TwilioRestException as e:
            logger.error(
                f"Twilio error sending SMS: {e.msg}",
                extra={
                    "to": to,
                    "error_code": e.code,
                    "status": e.status,
                },
            )
            return None
        
        except Exception as e:
            logger.error(
                f"Unexpected error sending SMS: {e}",
                extra={"to": to},
                exc_info=True,
            )
            return None
    
    async def send_bulk_sms(
        self,
        recipients: list[str],
        message: str,
    ) -> dict:
        """
        Send SMS to multiple recipients.
        
        Args:
            recipients: List of phone numbers
            message: Message content (same for all)
        
        Returns:
            Dict with success/failure counts
        """
        results = {
            "sent": 0,
            "failed": 0,
            "message_sids": [],
        }
        
        for recipient in recipients:
            message_sid = await self.send_sms(recipient, message)
            if message_sid:
                results["sent"] += 1
                results["message_sids"].append(message_sid)
            else:
                results["failed"] += 1
        
        logger.info(
            "Bulk SMS completed",
            extra={
                "total": len(recipients),
                "sent": results["sent"],
                "failed": results["failed"],
            },
        )
        
        return results


# Convenience function for quick SMS sending
async def send_sms(to: str, message: str, from_number: Optional[str] = None) -> Optional[str]:
    """
    Quick helper to send SMS without creating service instance.

    Uses environment variables for credentials. Pass from_number to override the
    Twilio sender (e.g. send a queued reminder from the booking clinic's own
    line instead of the worker's ambient TWILIO_PHONE_NUMBER); when None it
    falls back to the env number exactly as before.
    """
    sms_service = SMSService(from_number=from_number)
    return await sms_service.send_sms(to, message)
