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
# The SMS kill switch — ONE definition, read by the sender AND by the prompt
# ---------------------------------------------------------------------------
# SMS_ENABLED gates two things that must never disagree: whether a text is
# actually sent, and whether Susie TELLS the caller a text has been sent. Both
# used to read os.getenv("SMS_ENABLED", ...) with their own copy of the default
# — the sender here, the promise in prompts/clinic_template_prompt.py.
#
# That duplication is the bug. A live branch flips the default here to "true"
# and the prompt keeps "false", so with the env var unset in Render the text IS
# sent while Susie says it will not be. It was live in exactly that state on
# theorem-onboarding, vitaledge-onboarding and jv_v2 on 2026-08-25.
#
# There is now one function and one default. To flip a branch, change
# _DEFAULT below and nothing else; the promise follows the send by construction.
#
# ⚑ latency-eval: OFF. This branch is an isolated timing-eval service that must
# NEVER text a real caller, so it stays silent even if the Render env var is
# forgotten. Live branches (theorem-onboarding, vitaledge-onboarding, jv_v2)
# set this to "true" — a forgotten env var there should fail in the direction
# the clinic already runs in. Do NOT port this branch's default to them.
_SMS_ENABLED_DEFAULT = "false"

_TRUTHY = ("true", "1", "yes", "on")


def sms_enabled() -> bool:
    """True when outbound SMS is switched on for this service.

    Read this rather than os.getenv: the default is part of the switch, and a
    second reader that supplies its own default is how the send and the promise
    came apart.
    """
    return os.getenv("SMS_ENABLED", _SMS_ENABLED_DEFAULT).strip().lower() in _TRUTHY


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
        # Global SMS kill switch. Every surface — smart follow-up, owner alerts,
        # booking SMS — funnels through this method, so this one gate covers
        # them all. The switch itself lives in sms_enabled() at module scope;
        # see the note there before changing the default.
        if not sms_enabled():
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
        #
        # Keep what was ASKED FOR. From here on `to` is the destination that
        # will actually be dialled, and the two can differ — which is precisely
        # what no call site can see, because the redirect happens in here.
        _requested = to
        to = redirect_staff_sms(to)

        # --- SMS cost guard ------------------------------------------
        # AFTER redirect_staff_sms: `to` must already be the E.164
        # destination that will actually be dialled when it is matched
        # against SMS_TEST_NUMBERS, or the match silently misses.
        # BEFORE the truncation below: max_length must count the
        # sanitised text, not the pre-sanitised text.
        from app.notifications.sms_guard import (
            to_gsm7, check_budget, is_test_number, record_fake,
        )
        message = to_gsm7(message)
        segments = check_budget(message, to)

        if is_test_number(to):
            # record_fake returns a SID string, never None. Call sites in
            # this codebase read a SID as success and None as failure, so
            # returning None here would make the fake path change the
            # conversation flow rather than just skip a send.
            return record_fake(to, self.from_number, message, segments)
        # ---------------------------------------------------------------

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
            
            # Name the destination in the MESSAGE, not only in `extra`: the
            # deployed log format renders the message and drops extra, so
            # "SMS sent successfully" was unfalsifiable in production.
            #
            # Call CA6711a434 (22 Aug) logged "transfer-miss: clinic notified
            # at +447586605462" for a text EVAL_STAFF_SMS_TO had diverted to a
            # different number. A delivery claim nobody can check is worse than
            # no claim at all: it survives an audit.
            _redirect_note = (
                ""
                if to == _requested
                else (
                    " (REDIRECTED from ***%s — EVAL_STAFF_SMS_TO is set, so the "
                    "intended recipient got NOTHING)" % ((_requested or "")[-4:],)
                )
            )
            logger.info(
                "SMS sent successfully → ***%s%s",
                (to or "")[-4:],
                _redirect_note,
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
