# app/notifications/booking_sms.py
"""
SMS notifications for booking-related events.
Handles all patient communications via SMS.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from app.notifications.sms import send_sms
from app.notifications.templates import (
    format_booking_confirmation,
    format_24hr_reminder,
    format_same_day_reminder,
    format_insurance_booking_confirmation,
    format_insurance_reminder,
    format_cancellation_confirmation,
    format_reschedule_confirmation,
    format_late_cancellation_warning,
    format_callback_confirmation,
    format_message_received_confirmation,
    format_what_to_bring_reminder,
    format_post_appointment_thankyou,
    format_insurance_receipt_ready,
    get_location_short_name,
)

logger = logging.getLogger(__name__)


# ============================================================================
# BOOKING CONFIRMATION SMS
# ============================================================================

async def send_booking_confirmation(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
    session: Optional[dict] = None,
) -> bool:
    """
    Send immediate booking confirmation SMS.

    When `session` is supplied the body is built by ``build_sms(session)``
    (app/sms_templates.py) which uses env-var clinic details and the slot
    label already stored in the session.  Legacy callers that omit `session`
    continue to use the old template helpers below.

    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Appointment datetime
        location: Location name (e.g., "Alcester" or "Redditch")
        service: Service type (default: "physiotherapy")
        practitioner: Practitioner name (optional)
        is_new_patient: True if this is their first visit
        has_insurance: True if patient has insurance
        insurer: Insurance company name
        clinic_name: Clinic branding name for SMS sign-off
        clinic_phone: Clinic contact number for SMS sign-off
        session: Full session dict — when provided, build_sms() is used

    Returns:
        True only if a text actually went out — NOT if one was merely
        attempted. `send_sms` returns the Twilio SID, or None for a
        SUPPRESSED send (SMS_ENABLED off) as well as a failed one.
    """
    try:
        if session is not None:
            from app.sms_templates import build_sms
            message = build_sms(session)
        else:
            location_short = get_location_short_name(location)
            ck = {"clinic_name": clinic_name, "clinic_phone": clinic_phone}

            # Choose appropriate template
            if has_insurance and insurer:
                message = format_insurance_booking_confirmation(
                    patient_name=patient_name,
                    appointment_time=appointment_time,
                    location=location_short,
                    insurer=insurer,
                    **ck,
                )
            else:
                message = format_booking_confirmation(
                    patient_name=patient_name,
                    appointment_time=appointment_time,
                    location=location_short,
                    service=service,
                    practitioner=practitioner,
                    is_new_patient=is_new_patient,
                    **ck,
                )

        # Log what actually happened, not what was attempted. `send_sms` returns
        # the Twilio SID or None, and returns None for a SUPPRESSED send as well
        # as a failed one — SMS_ENABLED off is the common case on a branch cut
        # from latency-eval. JV call CA38e5603142, 2026-08-18, logged
        # "Booking confirmation SMS sent to ***1207" one millisecond after
        # "[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)".
        # Reading the log top-down, the patient was texted. They were not.
        # owner_alert already checks this return; this path never did.
        _sid = await send_sms(to=patient_phone, message=message)

        logger.info(
            "Booking confirmation SMS %s ***%s",
            "sent to" if _sid else "NOT sent (suppressed or failed) to",
            patient_phone[-4:] if patient_phone else "????",
            extra={
                "patient_name": patient_name,
                "appointment_time": appointment_time.isoformat() if appointment_time else None,
                "location": location,
            }
        )

        # Half of this repair landed on 2026-08-18 and half did not: the LOG
        # above was made honest, the RETURN below was left as a flat True. So
        # the log said "NOT sent" and the caller was told the opposite in the
        # same breath. `_reschedule_appointment_*` and the twilio route latch
        # `session["confirmation_sms_sent"]` on this value, and
        # smart_sms_router (:305) stands down on that latch — so a suppressed
        # send (SMS_ENABLED off) or a failed one cost the caller BOTH the
        # confirmation and the end-of-call follow-up that exists to cover it.
        return bool(_sid)

    except Exception as e:
        logger.error(
            "Failed to send booking confirmation to ***%s: %r",
            patient_phone[-4:] if patient_phone else "????",
            e,
            exc_info=True,
        )
        return False


# ============================================================================
# REMINDER SMS (24 HOURS BEFORE)
# ============================================================================

async def send_24hr_reminder(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
    from_number: Optional[str] = None,
    appointment_noun: Optional[str] = None,
) -> bool:
    """
    Send 24-hour reminder SMS.
    
    This is typically scheduled when the booking is made,
    to be sent 24 hours before the appointment.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Appointment datetime
        location: Location name
        is_new_patient: True if first visit
        has_insurance: True if patient has insurance
        insurer: Insurance company name
    
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        location_short = get_location_short_name(location)
        
        # Main reminder
        message = format_24hr_reminder(
            patient_name=patient_name,
            appointment_time=appointment_time,
            location=location_short,
            what_to_bring=is_new_patient,  # Only remind new patients what to bring
            clinic_name=clinic_name,
            clinic_phone=clinic_phone,
            appointment_noun=appointment_noun,
        )

        # `send_sms` returns the Twilio SID, or None for a SUPPRESSED send
        # (SMS_ENABLED off) as well as a rejected one. This path used to discard
        # it and log "24hr reminder sent" either way — the same false success the
        # booking confirmation carried until 2026-08-18. A reminder nobody
        # received must not read as delivered.
        _sid = await send_sms(to=patient_phone, message=message, from_number=from_number)
        if not _sid:
            logger.warning(
                "24hr reminder NOT sent (suppressed or rejected) to ***%s",
                patient_phone[-4:] if patient_phone else "????",
            )
            return False

        # If new patient, send additional "what to bring" reminder
        if is_new_patient:
            # Wait a moment then send second message
            import asyncio
            await asyncio.sleep(2)

            what_to_bring_msg = format_what_to_bring_reminder(patient_name)
            await send_sms(to=patient_phone, message=what_to_bring_msg, from_number=from_number)

        # If insurance patient, send insurance reminder
        if has_insurance and insurer:
            import asyncio
            await asyncio.sleep(2)

            insurance_msg = format_insurance_reminder(patient_name, insurer)
            await send_sms(to=patient_phone, message=insurance_msg, from_number=from_number)
        
        logger.info(
            f"24hr reminder sent to ***{patient_phone[-4:] if patient_phone else '????'}",
            extra={
                "patient_name": patient_name,
                "appointment_time": appointment_time.isoformat(),
            }
        )
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to send 24hr reminder: {e}", exc_info=True)
        return False


# ============================================================================
# SAME-DAY REMINDER (2 HOURS BEFORE)
# ============================================================================

async def send_same_day_reminder(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
    from_number: Optional[str] = None,
    appointment_noun: Optional[str] = None,
) -> bool:
    """
    Send same-day reminder (2 hours before appointment).
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Appointment datetime
        location: Location name
    
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        location_short = get_location_short_name(location)
        
        message = format_same_day_reminder(
            patient_name=patient_name,
            appointment_time=appointment_time,
            location=location_short,
            clinic_name=clinic_name,
            clinic_phone=clinic_phone,
            appointment_noun=appointment_noun,
        )

        # Same honest-result rule as the 24hr reminder above.
        _sid = await send_sms(to=patient_phone, message=message, from_number=from_number)
        _tail = patient_phone[-4:] if patient_phone else "????"
        if not _sid:
            logger.warning(
                "Same-day reminder NOT sent (suppressed or rejected) to ***%s", _tail,
            )
            return False

        logger.info("Same-day reminder sent to ***%s", _tail)

        return True
    
    except Exception as e:
        logger.error(f"Failed to send same-day reminder: {e}", exc_info=True)
        return False


# ============================================================================
# CANCELLATION & RESCHEDULING
# ============================================================================

async def send_cancellation_confirmation(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    is_late_cancellation: bool = False,
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> bool:
    """
    Send cancellation confirmation SMS.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Original appointment datetime
        is_late_cancellation: True if cancelled within 24 hours
    
    Returns:
        True only if a text actually went out — NOT if one was merely
        attempted. `send_sms` returns the Twilio SID, or None for a
        SUPPRESSED send (SMS_ENABLED off) as well as a failed one.
    """
    try:
        if is_late_cancellation:
            message = format_late_cancellation_warning(
                patient_name, clinic_name=clinic_name, clinic_phone=clinic_phone,
            )
        else:
            message = format_cancellation_confirmation(
                patient_name, appointment_time,
                clinic_name=clinic_name, clinic_phone=clinic_phone,
            )
        
        _sid = await send_sms(to=patient_phone, message=message)

        # Log what happened, not what was attempted — and mask the number, as
        # every other send in this module does. Theorem call CAc9b44a5e,
        # 2026-08-23, logged "[sms] SMS_ENABLED is off — outbound SMS
        # suppressed (not sent)" and then "Cancellation confirmation sent to
        # 07564202418" one millisecond later, in full, to a caller who was
        # never texted.
        logger.info(
            "Cancellation confirmation %s ***%s",
            "sent to" if _sid else "NOT sent (suppressed or failed) to",
            patient_phone[-4:] if patient_phone else "????",
            extra={"is_late": is_late_cancellation}
        )

        # See send_booking_confirmation: callers latch
        # session["confirmation_sms_sent"] on this return and the follow-up
        # router stands down on the latch, so a flat True costs the caller both
        # the confirmation and the follow-up.
        return bool(_sid)

    except Exception as e:
        logger.error(f"Failed to send cancellation confirmation: {e}", exc_info=True)
        return False


async def send_reschedule_confirmation(
    patient_phone: str,
    patient_name: str,
    old_time: datetime,
    new_time: datetime,
    location: str,
    clinic_name: Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> bool:
    """
    Send reschedule confirmation SMS.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        old_time: Original appointment datetime
        new_time: New appointment datetime
        location: Location name
    
    Returns:
        True if sent successfully
    """
    try:
        location_short = get_location_short_name(location)
        
        message = format_reschedule_confirmation(
            patient_name=patient_name,
            old_time=old_time,
            new_time=new_time,
            location=location_short,
            clinic_name=clinic_name,
            clinic_phone=clinic_phone,
        )
        
        await send_sms(to=patient_phone, message=message)
        
        logger.info(f"Reschedule confirmation sent to {patient_phone}")
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to send reschedule confirmation: {e}", exc_info=True)
        return False


# ============================================================================
# CALLBACK & MESSAGE CONFIRMATIONS
# ============================================================================

async def send_callback_confirmation(
    patient_phone: str,
    patient_name: str,
) -> bool:
    """
    Send callback request confirmation.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
    
    Returns:
        True if sent successfully
    """
    try:
        message = format_callback_confirmation(patient_name)
        await send_sms(to=patient_phone, message=message)
        
        logger.info(f"Callback confirmation sent to {patient_phone}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send callback confirmation: {e}", exc_info=True)
        return False


async def send_message_received_confirmation(
    patient_phone: str,
    patient_name: str,
) -> bool:
    """
    Send message received confirmation.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
    
    Returns:
        True if sent successfully
    """
    try:
        message = format_message_received_confirmation(patient_name)
        await send_sms(to=patient_phone, message=message)
        
        logger.info(f"Message confirmation sent to {patient_phone}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send message confirmation: {e}", exc_info=True)
        return False


# ============================================================================
# POST-APPOINTMENT
# ============================================================================

async def send_post_appointment_thankyou(
    patient_phone: str,
    patient_name: str,
    practitioner: Optional[str] = None,
) -> bool:
    """
    Send thank you message after appointment.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        practitioner: Practitioner name (optional)
    
    Returns:
        True if sent successfully
    """
    try:
        message = format_post_appointment_thankyou(patient_name, practitioner)
        await send_sms(to=patient_phone, message=message)
        
        logger.info(f"Thank you message sent to {patient_phone}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send thank you message: {e}", exc_info=True)
        return False


async def send_insurance_receipt_notification(
    patient_phone: str,
    patient_name: str,
    insurer: str,
) -> bool:
    """
    Notify patient that insurance receipt is ready.
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        insurer: Insurance company name
    
    Returns:
        True if sent successfully
    """
    try:
        message = format_insurance_receipt_ready(patient_name, insurer)
        await send_sms(to=patient_phone, message=message)
        
        logger.info(f"Insurance receipt notification sent to {patient_phone}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send insurance receipt notification: {e}", exc_info=True)
        return False


# ============================================================================
# BATCH OPERATIONS
# ============================================================================

async def send_booking_complete_workflow(
    patient_phone: str,
    patient_name: str,
    appointment_time: datetime,
    location: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
    schedule_reminders: bool = True,
) -> Dict[str, bool]:
    """
    Send complete booking workflow:
    1. Immediate confirmation
    2. Schedule 24hr reminder (if enabled)
    3. Schedule same-day reminder (if enabled)
    
    Args:
        patient_phone: Patient's mobile number
        patient_name: Patient's first name
        appointment_time: Appointment datetime
        location: Location name
        service: Service type
        practitioner: Practitioner name
        is_new_patient: True if first visit
        has_insurance: True if patient has insurance
        insurer: Insurance company name
        schedule_reminders: True to schedule future reminders
    
    Returns:
        Dict with status of each message sent
    """
    results = {}
    
    # 1. Send immediate confirmation
    results["confirmation"] = await send_booking_confirmation(
        patient_phone=patient_phone,
        patient_name=patient_name,
        appointment_time=appointment_time,
        location=location,
        service=service,
        practitioner=practitioner,
        is_new_patient=is_new_patient,
        has_insurance=has_insurance,
        insurer=insurer,
    )
    
    # 2. Schedule reminders if enabled
    if schedule_reminders:
        from app.notifications.scheduler import schedule_appointment_reminders
        
        results["reminders_scheduled"] = await schedule_appointment_reminders(
            patient_phone=patient_phone,
            patient_name=patient_name,
            appointment_time=appointment_time,
            location=location,
            is_new_patient=is_new_patient,
            has_insurance=has_insurance,
            insurer=insurer,
        )
    
    return results
