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
