"""
SMS message templates for Theorem Health.
"""

from datetime import datetime
from typing import Optional


def booking_confirmation_sms(
    patient_first_name: str,
    appointment_date: datetime,
    location_name: str,
    practitioner_name: Optional[str] = None,
) -> str:
    """
    SMS sent immediately after booking is confirmed.
    
    Example:
    "Hi John, your physiotherapy appointment is confirmed for Monday, March 15 
    at 2:00 PM at our Alcester clinic with Mark. See you then! - Theorem Health"
    """
    day_date = appointment_date.strftime("%A, %B %d")
    time = appointment_date.strftime("%I:%M %p").lstrip("0")
    
    message_parts = [
        f"Hi {patient_first_name},",
        f"your physiotherapy appointment is confirmed for {day_date} at {time}",
        f"at our {location_name} clinic",
    ]
    
    if practitioner_name:
        message_parts.append(f"with {practitioner_name}")
    
    message_parts.append("See you then! - Theorem Health")
    
    return " ".join(message_parts)


def appointment_reminder_sms(
    patient_first_name: str,
    appointment_date: datetime,
    location_name: str,
) -> str:
    """
    SMS sent 24 hours before appointment.
    
    Example:
    "Hi John, reminder: your appointment at Theorem Health is tomorrow at 2:00 PM 
    at our Alcester clinic. Reply CANCEL if you need to reschedule. 07870 166861"
    """
    time = appointment_date.strftime("%I:%M %p").lstrip("0")
    
    return (
        f"Hi {patient_first_name}, reminder: your appointment at Theorem Health "
        f"is tomorrow at {time} at our {location_name} clinic. "
        f"Reply CANCEL if you need to reschedule. 07870 166861"
    )


def insurance_followup_sms(
    patient_first_name: str,
    insurance_provider: str,
) -> str:
    """
    SMS sent when patient mentions insurance.
    
    Example:
    "Hi John, we've noted your Aviva insurance details. You'll pay £75 for your 
    session and we'll provide documentation for your claim. Any questions, 
    call 07870 166861 - Theorem Health"
    """
    return (
        f"Hi {patient_first_name}, we've noted your {insurance_provider} insurance details. "
        f"You'll pay £75 for your session and we'll provide documentation for your claim. "
        f"Any questions, call 07870 166861 - Theorem Health"
    )


def callback_request_sms(
    patient_first_name: str,
    callback_reason: Optional[str] = None,
) -> str:
    """
    SMS confirming callback request.
    
    Example:
    "Hi John, we've received your callback request about shockwave therapy. 
    Mark or the team will call you back shortly. - Theorem Health"
    """
    reason_text = f"about {callback_reason}" if callback_reason else ""
    
    return (
        f"Hi {patient_first_name}, we've received your callback request {reason_text}. "
        f"Mark or the team will call you back shortly. - Theorem Health"
    )


def cancellation_confirmation_sms(
    patient_first_name: str,
    appointment_date: datetime,
) -> str:
    """
    SMS confirming appointment cancellation.
    
    Example:
    "Hi John, your appointment on Monday, March 15 at 2:00 PM has been cancelled. 
    To rebook, call 07870 166861 - Theorem Health"
    """
    day_date = appointment_date.strftime("%A, %B %d")
    time = appointment_date.strftime("%I:%M %p").lstrip("0")
    
    return (
        f"Hi {patient_first_name}, your appointment on {day_date} at {time} "
        f"has been cancelled. To rebook, call 07870 166861 - Theorem Health"
    )


def equipment_info_sms(
    patient_first_name: str,
    equipment_type: str,
) -> str:
    """
    SMS with equipment info link.
    
    Example:
    "Hi John, here's more info about shockwave therapy: [link]. 
    We can discuss during your assessment or call 07870 166861 - Theorem Health"
    """
    equipment_names = {
        "shockwave": "shockwave therapy",
        "laser": "Class IV laser therapy",
    }
    
    equipment_name = equipment_names.get(equipment_type, equipment_type)
    
    return (
        f"Hi {patient_first_name}, we can discuss {equipment_name} during your "
        f"assessment. It's £45 extra if appropriate for your condition. "
        f"Questions? Call 07870 166861 - Theorem Health"
    )


def welcome_new_patient_sms(patient_first_name: str) -> str:
    """
    Welcome SMS for new patients.
    
    Example:
    "Hi John, welcome to Theorem Health! We're looking forward to seeing you. 
    Please wear comfortable clothing and bring any scans or reports. - Mark & Team"
    """
    return (
        f"Hi {patient_first_name}, welcome to Theorem Health! We're looking forward to seeing you. "
        f"Please wear comfortable clothing and bring any scans or reports if you have them. - Mark & Team"
    )
