# app/notifications/templates.py
"""
COMPLETE SMS templates for Theorem Health - ALL FUNCTIONS.
Includes booking templates, smart router templates, and helper functions.
"""

from datetime import datetime
from typing import Optional


# ============================================================================
# BOOKING CONFIRMATION TEMPLATES
# ============================================================================

def format_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    insurer: Optional[str] = None,
) -> str:
    """Standard booking confirmation SMS."""
    day_name = appointment_time.strftime("%A")
    day_num = appointment_time.strftime("%d").lstrip("0")
    month = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    message = f"Hi {patient_name}, your {service} appointment is confirmed for {day_name} {day_num} {month} at {time_str} at our {location} clinic"
    
    if practitioner:
        message += f" with {practitioner}"
    
    if is_new_patient:
        message += ". Please arrive 5 minutes early"
    
    message += ". We look forward to seeing you! Theorem Health - 07870 166861"
    
    return message


def format_insurance_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    insurer: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
) -> str:
    """Booking confirmation for insurance patients."""
    day_name = appointment_time.strftime("%A")
    day_num = appointment_time.strftime("%d").lstrip("0")
    month = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    message = (
        f"Hi {patient_name}, your {service} appointment is confirmed for "
        f"{day_name} {day_num} {month} at {time_str} at our {location} clinic"
    )
    
    if practitioner:
        message += f" with {practitioner}"
    
    message += (
        f". We'll provide all documentation for your {insurer} claim. "
        f"See you then! Theorem Health - 07870 166861"
    )
    
    return message


def format_first_visit_welcome(
    patient_name: str,
    appointment_time: datetime,
    location: str,
) -> str:
    """Welcome message for first-time patients."""
    day_name = appointment_time.strftime("%A")
    day_num = appointment_time.strftime("%d").lstrip("0")
    month = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    return (
        f"Hi {patient_name}, welcome to Theorem Health! Your first appointment is "
        f"{day_name} {day_num} {month} at {time_str} at our {location} clinic. "
        f"Please arrive 5 minutes early. We look forward to meeting you! "
        f"Call 07870 166861 with any questions."
    )


# ============================================================================
# REMINDER TEMPLATES
# ============================================================================

def format_24hr_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    what_to_bring: bool = False,  # ← CORRECT parameter name for booking_sms.py
    is_new_patient: bool = False,
    has_insurance: bool = False,
) -> str:
    """24-hour reminder SMS."""
    day_name = appointment_time.strftime("%A")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    message = f"Hi {patient_name}, reminder: your physiotherapy appointment is tomorrow ({day_name}) at {time_str} at our {location} clinic."
    
    if what_to_bring or is_new_patient:
        message += " Please arrive 5 minutes early to complete paperwork."
    
    message += " Call 07870 166861 if you need to reschedule. Theorem Health"
    
    return message


def format_same_day_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
) -> str:
    """Same-day reminder (2 hours before)."""
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    return (
        f"Hi {patient_name}, reminder: your physiotherapy appointment is today at {time_str} "
        f"at our {location} clinic. See you soon! Theorem Health - 07870 166861"
    )


def format_insurance_reminder(patient_name: str, insurer: str) -> str:
    """Insurance reminder (sent with 24hr reminder)."""
    return (
        f"Hi {patient_name}, reminder: we'll provide all documentation needed "
        f"for your {insurer} claim. Bring your membership number if you have it. "
        f"Theorem Health"
    )


def format_what_to_bring_reminder(patient_name: str) -> str:
    """What to bring reminder for new patients."""
    return (
        f"Hi {patient_name}, for your first visit please bring: photo ID, "
        f"insurance details (if claiming), and any relevant medical reports. "
        f"Theorem Health"
    )


# ============================================================================
# CANCELLATION & RESCHEDULE TEMPLATES
# ============================================================================

def format_cancellation_confirmation(
    patient_name: str,
    appointment_time: datetime,
    is_late_cancellation: bool = False,
) -> str:
    """Cancellation confirmation."""
    day_name = appointment_time.strftime("%A")
    day_num = appointment_time.strftime("%d").lstrip("0")
    month = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()
    
    message = f"Hi {patient_name}, your appointment on {day_name} {day_num} {month} at {time_str} has been cancelled."
    
    if is_late_cancellation:
        message += " As this was within 24 hours, our cancellation fee may apply."
    
    message += " Call 07870 166861 to rebook. Theorem Health"
    
    return message


def format_late_cancellation_warning(patient_name: str) -> str:
    """Late cancellation warning (within 24 hours)."""
    return (
        f"Hi {patient_name}, your appointment has been cancelled. "
        f"As this was within 24 hours of your appointment, our £25 cancellation fee applies. "
        f"Call 07870 166861 to rebook. Theorem Health"
    )


def format_reschedule_confirmation(
    patient_name: str,
    old_time: datetime,
    new_time: datetime,
    location: str,
) -> str:
    """Reschedule confirmation."""
    new_day = new_time.strftime("%A")
    new_day_num = new_time.strftime("%d").lstrip("0")
    new_month = new_time.strftime("%b")
    new_time_str = new_time.strftime("%I:%M%p").lstrip("0").lower()
    
    return (
        f"Hi {patient_name}, your appointment has been rescheduled to "
        f"{new_day} {new_day_num} {new_month} at {new_time_str} at our {location} clinic. "
        f"See you then! Theorem Health - 07870 166861"
    )


# ============================================================================
# COMMUNICATION TEMPLATES
# ============================================================================

def format_callback_confirmation(patient_name: str) -> str:
    """Callback request confirmation."""
    if patient_name:
        return f"Hi {patient_name}, thanks for your message. One of our team will call you back shortly. Theorem Health"
    return "Thanks for your message. We'll call you back shortly. Theorem Health"


def format_message_received_confirmation(patient_name: str) -> str:
    """Message received confirmation."""
    if patient_name:
        return (
            f"Hi {patient_name}, we've received your message and will respond shortly. "
            f"Theorem Health - 07870 166861"
        )
    return "Message received. We'll respond shortly. Theorem Health - 07870 166861"


# ============================================================================
# POST-APPOINTMENT TEMPLATES
# ============================================================================

def format_post_appointment_thankyou(
    patient_name: str,
    practitioner: Optional[str] = None,
) -> str:
    """Thank you after appointment."""
    message = f"Hi {patient_name}, thank you for visiting Theorem Health today"
    
    if practitioner:
        message += f" with {practitioner}"
    
    message += (
        ". Remember to do your prescribed exercises! "
        f"Call 07870 166861 if you have any questions."
    )
    
    return message


def format_insurance_receipt_ready(patient_name: str, insurer: str) -> str:
    """Insurance receipt ready notification."""
    return (
        f"Hi {patient_name}, your receipt and clinical notes for {insurer} "
        f"have been emailed. Call 07870 166861 if you need anything else. Theorem Health"
    )


def format_insurance_receipt_notification(
    patient_name: str,
    insurer: str,
) -> str:
    """Alias for format_insurance_receipt_ready (backward compatibility)."""
    return format_insurance_receipt_ready(patient_name, insurer)


# ============================================================================
# SMART SMS ROUTER TEMPLATES (for different call outcomes)
# ============================================================================

def format_abandoned_booking_sms(
    patient_name: str,
    reason: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    """SMS for abandoned bookings."""
    if patient_name and reason:
        return (
            f"Hi {patient_name}, thanks for calling about {reason}. "
            f"We'd love to help - we're open Mon-Fri 8:30am-9pm. "
            f"Call 07870 166861 to book or reply YES for a callback. Theorem Health"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, thanks for calling Theorem Health. "
            f"We're here Mon-Fri 8:30am-9pm if you'd like to complete your booking. "
            f"Call 07870 166861 or reply YES for a callback."
        )
    else:
        return (
            f"Thanks for calling Theorem Health. We're here Mon-Fri 8:30am-9pm. "
            f"Call 07870 166861 to book your appointment."
        )


def format_info_only_sms(patient_name: str, topics: Optional[str] = None) -> str:
    """SMS for FAQ-only calls."""
    if patient_name and topics:
        return (
            f"Hi {patient_name}, glad we could help with your questions about {topics}. "
            f"Ready to book? We're here Mon-Fri 8:30am-9pm. Call 07870 166861. Theorem Health"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, thanks for your call. If you have more questions or want to book, "
            f"we're here Mon-Fri 8:30am-9pm. Call 07870 166861. Theorem Health"
        )
    else:
        return (
            f"Thanks for calling Theorem Health. Questions or bookings? "
            f"Mon-Fri 8:30am-9pm. Call 07870 166861."
        )


def format_price_inquiry_sms(patient_name: str, service: Optional[str] = None) -> str:
    """SMS for price inquiries."""
    if patient_name and service:
        return (
            f"Hi {patient_name}, {service} sessions are £75 for 50 minutes. "
            f"First session includes full assessment and treatment. "
            f"Ready to book? Call 07870 166861. Theorem Health"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, physiotherapy sessions are £75 for 50 minutes "
            f"(full assessment + treatment included). "
            f"Questions or booking? Call 07870 166861. Theorem Health"
        )
    else:
        return (
            f"Physiotherapy £75/50min (assessment + treatment). "
            f"Call 07870 166861 to book. Theorem Health"
        )


def format_insurance_inquiry_sms(
    patient_name: str,
    insurer: Optional[str] = None,
    bupa_mentioned: bool = False,
) -> str:
    """SMS for insurance inquiries."""
    if bupa_mentioned:
        if patient_name:
            return (
                f"Hi {patient_name}, unfortunately we don't accept Bupa directly. "
                f"You're welcome to book as a private patient (£75/session). "
                f"Call 07870 166861 for more info. Theorem Health"
            )
        else:
            return (
                f"We don't accept Bupa directly. Private pay £75/session. "
                f"Call 07870 166861 for details. Theorem Health"
            )
    
    elif insurer:
        if patient_name:
            return (
                f"Hi {patient_name}, we're self-pay (£75/session) then you claim back from {insurer}. "
                f"We provide all documentation. "
                f"Ready to book? Call 07870 166861. Theorem Health"
            )
        else:
            return (
                f"Self-pay £75/session, claim back from {insurer}. "
                f"We provide receipts. Call 07870 166861. Theorem Health"
            )
    
    else:
        if patient_name:
            return (
                f"Hi {patient_name}, we're self-pay (£75/session) then you claim back from your insurer. "
                f"We provide all documentation needed. "
                f"Call 07870 166861 to book. Theorem Health"
            )
        else:
            return (
                f"Self-pay £75/session, claim from your insurer. "
                f"We provide receipts. Call 07870 166861. Theorem Health"
            )


def format_no_suitable_time_sms(patient_name: str, reason: Optional[str] = None) -> str:
    """SMS when no suitable time found."""
    if patient_name and reason:
        return (
            f"Hi {patient_name}, sorry we couldn't find a suitable time for {reason}. "
            f"We'd like to help - reply YES and we'll call you back to arrange something. "
            f"Or call 07870 166861. Theorem Health"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, sorry we couldn't find a suitable appointment time. "
            f"Reply YES for a callback or call us on 07870 166861. "
            f"We'll find something that works! Theorem Health"
        )
    else:
        return (
            f"Sorry we couldn't find a suitable time. "
            f"Reply YES for callback or call 07870 166861. Theorem Health"
        )


def format_technical_issue_sms(patient_name: str) -> str:
    """SMS for technical issues."""
    if patient_name:
        return (
            f"Hi {patient_name}, sorry - we had a technical issue with your call. "
            f"Please call us back on 07870 166861 or reply YES and we'll call you. "
            f"Apologies! Theorem Health"
        )
    else:
        return (
            f"Sorry - technical issue with your call. "
            f"Please call 07870 166861 or reply YES for callback. Theorem Health"
        )


def format_reschedule_request_sms(patient_name: str) -> str:
    """SMS for reschedule requests."""
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about rescheduling. "
            f"We'll help you find a new time - call 07870 166861 "
            f"or reply YES and we'll call you. Theorem Health"
        )
    else:
        return (
            f"Thanks for calling about rescheduling. "
            f"Call 07870 166861 or reply YES for callback. Theorem Health"
        )


def format_cancellation_request_sms(patient_name: str) -> str:
    """SMS for cancellation requests."""
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about cancelling. "
            f"Please call 07870 166861 to confirm your cancellation "
            f"and we'll sort it out for you. Theorem Health"
        )
    else:
        return (
            f"Thanks for calling about cancellation. "
            f"Call 07870 166861 to confirm. Theorem Health"
        )


def format_general_thankyou_sms(patient_name: str) -> str:
    """Generic thank you SMS."""
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling Theorem Health. "
            f"If you'd like to book or have questions, we're here Mon-Fri 8:30am-9pm. "
            f"Call 07870 166861."
        )
    else:
        return (
            f"Thanks for calling Theorem Health. Mon-Fri 8:30am-9pm. "
            f"Call 07870 166861 to book or ask questions."
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_location_short_name(location: str) -> str:
    """Convert location to short name."""
    location_lower = location.lower()
    if "alcester" in location_lower:
        return "Alcester"
    elif "redditch" in location_lower:
        return "Redditch"
    return location
