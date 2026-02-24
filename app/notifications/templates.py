# app/notifications/templates.py
"""
Clinic-aware SMS templates.
Every function accepts optional clinic_name and clinic_phone kwargs so the
correct branding appears in every message regardless of which clinic is active.
Defaults retain Theorem Health values for backward compatibility.
"""

from datetime import datetime
from typing import Optional


# ============================================================================
# DEFAULTS (Theorem values — override per clinic via kwargs)
# ============================================================================

_DEFAULT_CLINIC_NAME  = "Theorem Health"
_DEFAULT_CLINIC_PHONE = "07870 166861"


def _cn(clinic_name: Optional[str]) -> str:
    return clinic_name  or _DEFAULT_CLINIC_NAME


def _cp(clinic_phone: Optional[str]) -> str:
    return clinic_phone or _DEFAULT_CLINIC_PHONE


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
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Standard booking confirmation SMS."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    message = (
        f"Hi {patient_name}, your {service} appointment is confirmed for "
        f"{day_name} {day_num} {month} at {time_str} at our {location} clinic"
    )
    if practitioner:
        message += f" with {practitioner}"
    if is_new_patient:
        message += ". Please arrive 5 minutes early"
    message += f". We look forward to seeing you! {_cn(clinic_name)} - {_cp(clinic_phone)}"
    return message


def format_insurance_booking_confirmation(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    insurer: str,
    service: str = "physiotherapy",
    practitioner: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Booking confirmation for insurance patients."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    message = (
        f"Hi {patient_name}, your {service} appointment is confirmed for "
        f"{day_name} {day_num} {month} at {time_str} at our {location} clinic"
    )
    if practitioner:
        message += f" with {practitioner}"
    message += (
        f". We'll provide all documentation for your {insurer} claim. "
        f"See you then! {_cn(clinic_name)} - {_cp(clinic_phone)}"
    )
    return message


def format_first_visit_welcome(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Welcome message for first-time patients."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    return (
        f"Hi {patient_name}, welcome to {_cn(clinic_name)}! Your first appointment is "
        f"{day_name} {day_num} {month} at {time_str} at our {location} clinic. "
        f"Please arrive 5 minutes early. We look forward to meeting you! "
        f"Call {_cp(clinic_phone)} with any questions."
    )


# ============================================================================
# REMINDER TEMPLATES
# ============================================================================

def format_24hr_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    what_to_bring: bool = False,
    is_new_patient: bool = False,
    has_insurance: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """24-hour reminder SMS."""
    day_name = appointment_time.strftime("%A")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    message = (
        f"Hi {patient_name}, reminder: your physiotherapy appointment is tomorrow "
        f"({day_name}) at {time_str} at our {location} clinic."
    )
    if what_to_bring or is_new_patient:
        message += " Please arrive 5 minutes early to complete paperwork."
    message += f" Call {_cp(clinic_phone)} if you need to reschedule. {_cn(clinic_name)}"
    return message


def format_same_day_reminder(
    patient_name: str,
    appointment_time: datetime,
    location: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Same-day reminder (2 hours before)."""
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    return (
        f"Hi {patient_name}, reminder: your physiotherapy appointment is today at {time_str} "
        f"at our {location} clinic. See you soon! {_cn(clinic_name)} - {_cp(clinic_phone)}"
    )


def format_insurance_reminder(
    patient_name: str,
    insurer: str,
    clinic_name: Optional[str] = None,
) -> str:
    """Insurance reminder (sent with 24hr reminder)."""
    return (
        f"Hi {patient_name}, reminder: we'll provide all documentation needed "
        f"for your {insurer} claim. Bring your membership number if you have it. "
        f"{_cn(clinic_name)}"
    )


def format_what_to_bring_reminder(
    patient_name: str,
    clinic_name: Optional[str] = None,
) -> str:
    """What to bring reminder for new patients."""
    return (
        f"Hi {patient_name}, for your first visit please bring: photo ID, "
        f"insurance details (if claiming), and any relevant medical reports. "
        f"{_cn(clinic_name)}"
    )


# ============================================================================
# CANCELLATION & RESCHEDULE TEMPLATES
# ============================================================================

def format_cancellation_confirmation(
    patient_name: str,
    appointment_time: datetime,
    is_late_cancellation: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Cancellation confirmation."""
    day_name = appointment_time.strftime("%A")
    day_num  = appointment_time.strftime("%d").lstrip("0")
    month    = appointment_time.strftime("%b")
    time_str = appointment_time.strftime("%I:%M%p").lstrip("0").lower()

    message = (
        f"Hi {patient_name}, your appointment on {day_name} {day_num} {month} "
        f"at {time_str} has been cancelled."
    )
    if is_late_cancellation:
        message += " As this was within 24 hours, our cancellation fee may apply."
    message += f" Call {_cp(clinic_phone)} to rebook. {_cn(clinic_name)}"
    return message


def format_late_cancellation_warning(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Late cancellation warning (within 24 hours)."""
    return (
        f"Hi {patient_name}, your appointment has been cancelled. "
        f"As this was within 24 hours of your appointment, our £25 cancellation fee applies. "
        f"Call {_cp(clinic_phone)} to rebook. {_cn(clinic_name)}"
    )


def format_reschedule_confirmation(
    patient_name: str,
    old_time: datetime,
    new_time: datetime,
    location: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Reschedule confirmation."""
    new_day      = new_time.strftime("%A")
    new_day_num  = new_time.strftime("%d").lstrip("0")
    new_month    = new_time.strftime("%b")
    new_time_str = new_time.strftime("%I:%M%p").lstrip("0").lower()

    return (
        f"Hi {patient_name}, your appointment has been rescheduled to "
        f"{new_day} {new_day_num} {new_month} at {new_time_str} at our {location} clinic. "
        f"See you then! {_cn(clinic_name)} - {_cp(clinic_phone)}"
    )


# ============================================================================
# COMMUNICATION TEMPLATES
# ============================================================================

def format_callback_confirmation(
    patient_name: str,
    clinic_name: Optional[str] = None,
) -> str:
    """Callback request confirmation."""
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for your message. "
            f"One of our team will call you back shortly. {_cn(clinic_name)}"
        )
    return f"Thanks for your message. We'll call you back shortly. {_cn(clinic_name)}"


def format_message_received_confirmation(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Message received confirmation."""
    if patient_name:
        return (
            f"Hi {patient_name}, we've received your message and will respond shortly. "
            f"{_cn(clinic_name)} - {_cp(clinic_phone)}"
        )
    return f"Message received. We'll respond shortly. {_cn(clinic_name)} - {_cp(clinic_phone)}"


# ============================================================================
# POST-APPOINTMENT TEMPLATES
# ============================================================================

def format_post_appointment_thankyou(
    patient_name: str,
    practitioner: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Thank you after appointment."""
    message = f"Hi {patient_name}, thank you for visiting {_cn(clinic_name)} today"
    if practitioner:
        message += f" with {practitioner}"
    message += (
        ". Remember to do your prescribed exercises! "
        f"Call {_cp(clinic_phone)} if you have any questions."
    )
    return message


def format_insurance_receipt_ready(
    patient_name: str,
    insurer: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Insurance receipt ready notification."""
    return (
        f"Hi {patient_name}, your receipt and clinical notes for {insurer} "
        f"have been emailed. Call {_cp(clinic_phone)} if you need anything else. "
        f"{_cn(clinic_name)}"
    )


def format_insurance_receipt_notification(
    patient_name: str,
    insurer: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Alias for format_insurance_receipt_ready (backward compatibility)."""
    return format_insurance_receipt_ready(
        patient_name, insurer,
        clinic_name=clinic_name, clinic_phone=clinic_phone,
    )


# ============================================================================
# SMART SMS ROUTER TEMPLATES (for different call outcomes)
# ============================================================================

def format_abandoned_booking_sms(
    patient_name: str,
    reason: Optional[str] = None,
    location: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for abandoned bookings."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name and reason:
        return (
            f"Hi {patient_name}, thanks for calling about {reason}. "
            f"We'd love to help — call {phone} to book or reply YES for a callback. {name}"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, thanks for calling {name}. "
            f"We're here if you'd like to complete your booking. "
            f"Call {phone} or reply YES for a callback."
        )
    else:
        return f"Thanks for calling {name}. Call {phone} to book your appointment."


def format_info_only_sms(
    patient_name: str,
    topics: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for FAQ-only calls."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name and topics:
        return (
            f"Hi {patient_name}, glad we could help with your questions about {topics}. "
            f"Ready to book? Call {phone}. {name}"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, thanks for your call. If you have more questions or want to book, "
            f"call {phone}. {name}"
        )
    else:
        return f"Thanks for calling {name}. Questions or bookings? Call {phone}."


def format_price_inquiry_sms(
    patient_name: str,
    service: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for price inquiries."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name and service:
        return (
            f"Hi {patient_name}, {service} sessions are £75 for 50 minutes. "
            f"First session includes full assessment and treatment. "
            f"Ready to book? Call {phone}. {name}"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, physiotherapy sessions are £75 for 50 minutes "
            f"(full assessment + treatment included). "
            f"Questions or booking? Call {phone}. {name}"
        )
    else:
        return f"Physiotherapy £75/50min (assessment + treatment). Call {phone} to book. {name}"


def format_insurance_inquiry_sms(
    patient_name: str,
    insurer: Optional[str] = None,
    bupa_mentioned: bool = False,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for insurance inquiries."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if bupa_mentioned:
        if patient_name:
            return (
                f"Hi {patient_name}, unfortunately we don't accept Bupa directly. "
                f"You're welcome to book as a private patient. "
                f"Call {phone} for more info. {name}"
            )
        else:
            return f"We don't accept Bupa directly. Call {phone} for details. {name}"
    elif insurer:
        if patient_name:
            return (
                f"Hi {patient_name}, we're self-pay then you claim back from {insurer}. "
                f"We provide all documentation. Ready to book? Call {phone}. {name}"
            )
        else:
            return f"Self-pay, claim back from {insurer}. We provide receipts. Call {phone}. {name}"
    else:
        if patient_name:
            return (
                f"Hi {patient_name}, we're self-pay then you claim back from your insurer. "
                f"We provide all documentation needed. Call {phone} to book. {name}"
            )
        else:
            return f"Self-pay, claim from your insurer. We provide receipts. Call {phone}. {name}"


def format_no_suitable_time_sms(
    patient_name: str,
    reason: Optional[str] = None,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS when no suitable time found."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name and reason:
        return (
            f"Hi {patient_name}, sorry we couldn't find a suitable time for {reason}. "
            f"Reply YES and we'll call you back to arrange something. Or call {phone}. {name}"
        )
    elif patient_name:
        return (
            f"Hi {patient_name}, sorry we couldn't find a suitable appointment time. "
            f"Reply YES for a callback or call us on {phone}. We'll find something that works! {name}"
        )
    else:
        return f"Sorry we couldn't find a suitable time. Reply YES for callback or call {phone}. {name}"


def format_technical_issue_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for technical issues."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, sorry — we had a technical issue with your call. "
            f"Please call us back on {phone} or reply YES and we'll call you. Apologies! {name}"
        )
    else:
        return f"Sorry — technical issue with your call. Please call {phone} or reply YES for callback. {name}"


def format_reschedule_request_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for reschedule requests."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about rescheduling. "
            f"We'll help you find a new time — call {phone} or reply YES and we'll call you. {name}"
        )
    else:
        return f"Thanks for calling about rescheduling. Call {phone} or reply YES for callback. {name}"


def format_cancellation_request_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """SMS for cancellation requests."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling about cancelling. "
            f"Please call {phone} to confirm your cancellation and we'll sort it out. {name}"
        )
    else:
        return f"Thanks for calling about cancellation. Call {phone} to confirm. {name}"


def format_general_thankyou_sms(
    patient_name: str,
    clinic_name:  Optional[str] = None,
    clinic_phone: Optional[str] = None,
) -> str:
    """Generic thank you SMS."""
    name  = _cn(clinic_name)
    phone = _cp(clinic_phone)
    if patient_name:
        return (
            f"Hi {patient_name}, thanks for calling {name}. "
            f"If you'd like to book or have questions, call {phone}."
        )
    else:
        return f"Thanks for calling {name}. Call {phone} to book or ask questions."


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
