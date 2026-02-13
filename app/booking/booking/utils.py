"""
Utility functions for booking subsystem.
"""

import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

# Import from main clinic config
from app.clinic_config import (
    UK_BANK_HOLIDAYS,
    THEOREM_PRACTITIONERS,
    get_clinic,
)

# UK timezone
LONDON_TZ = ZoneInfo("Europe/London")
UTC_TZ = ZoneInfo("UTC")


def generate_idempotency_key(
    clinic_id: str,
    call_sid: str,
    session_id: str,
    appointment_type_id: str,
    slot_start_iso: str,
    location_id: str = None,
    practitioner_id: str = None,
) -> str:
    """
    Generate deterministic idempotency key for booking requests.
    
    Same inputs always produce same key, preventing duplicate bookings.
    """
    components = [
        clinic_id,
        call_sid,
        session_id,
        appointment_type_id,
        slot_start_iso,
        location_id or "none",
        practitioner_id or "none",
    ]
    raw_key = ":".join(components)
    return f"booking:idempotency:{hashlib.sha256(raw_key.encode()).hexdigest()}"


def to_utc(dt: datetime) -> datetime:
    """Convert timezone-aware datetime to UTC."""
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return dt.astimezone(UTC_TZ)


def to_london(dt: datetime) -> datetime:
    """Convert timezone-aware datetime to Europe/London."""
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return dt.astimezone(LONDON_TZ)


def ensure_london_tz(dt: datetime) -> datetime:
    """
    Ensure datetime is timezone-aware in Europe/London.
    If naive, assume it's already London time and add timezone.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LONDON_TZ)
    return dt.astimezone(LONDON_TZ)


def is_bank_holiday(check_date: date) -> bool:
    """Check if date is a UK bank holiday."""
    date_str = check_date.isoformat()
    return date_str in UK_BANK_HOLIDAYS


def get_day_abbrev(dt: datetime) -> str:
    """
    Get 3-letter day abbreviation matching clinic_config format.
    
    Returns: "mon", "tue", "wed", "thu", "fri", "sat", "sun"
    """
    day_map = {
        0: "mon",
        1: "tue", 
        2: "wed",
        3: "thu",
        4: "fri",
        5: "sat",
        6: "sun",
    }
    return day_map[dt.weekday()]


def is_within_working_hours(dt: datetime, clinic_id: str = "theorem") -> bool:
    """
    Check if datetime falls within clinic working hours.
    
    Uses the working_hours from clinic_config.py CLINICS dictionary.
    """
    clinic = get_clinic(clinic_id)
    working_hours = clinic.get("working_hours", {})
    
    day_abbrev = get_day_abbrev(dt)
    hours = working_hours.get(day_abbrev)
    
    if hours is None:
        return False  # Clinic closed this day
    
    start_hour, end_hour = hours
    
    # Convert time to decimal hour (e.g., 14:30 -> 14.5)
    current_hour = dt.hour + (dt.minute / 60.0)
    
    return start_hour <= current_hour < end_hour


def filter_slots_by_practitioner_availability(
    slots: list,
    practitioner_id: str,
) -> list:
    """
    Filter slots to only those on days when practitioner is available.
    
    Uses practitioner_days from clinic_config.py CLINICS["theorem"]["practitioner_days"].
    """
    prac_data = THEOREM_PRACTITIONERS.get(practitioner_id)
    if not prac_data:
        return slots
    
    available_days = prac_data.get("available_days", [])
    
    filtered = []
    for slot in slots:
        day_abbrev = get_day_abbrev(slot.start_time)
        if day_abbrev in available_days:
            filtered.append(slot)
    
    return filtered


def validate_booking_rules(
    slot_start: datetime,
    clinic_id: str = "theorem",
) -> tuple[bool, str]:
    """
    Validate booking against clinic rules from clinic_config.py.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    clinic = get_clinic(clinic_id)
    max_days_ahead = clinic.get("days_ahead", 180)
    
    slot_date = slot_start.date()
    today = date.today()
    
    # Check if in the past
    if slot_date < today:
        return False, "Cannot book appointments in the past"
    
    # Check if too far in advance
    days_ahead = (slot_date - today).days
    if days_ahead > max_days_ahead:
        return False, f"Cannot book more than {max_days_ahead} days in advance"
    
    # Check if bank holiday
    if is_bank_holiday(slot_date):
        return False, "Clinic is closed on UK bank holidays"
    
    # Check if within working hours
    if not is_within_working_hours(slot_start, clinic_id):
        day_name = slot_start.strftime("%A")
        return False, f"Clinic is not open at this time on {day_name}"
    
    return True, ""


def format_price_gbp(amount: float) -> str:
    """Format price in GBP."""
    return f"£{amount:.2f}"


def format_appointment_confirmation(
    appointment_type_name: str,
    start_time: datetime,
    location_name: str,
    practitioner_name: str = None,
    price: float = None,
) -> str:
    """
    Format a friendly appointment confirmation message.
    
    Returns:
        Formatted confirmation string
    """
    parts = [
        f"Perfect! Your {appointment_type_name} is confirmed for",
        f"{start_time.strftime('%A, %B %d at %I:%M %p')}",
        f"at our {location_name} clinic.",
    ]
    
    if practitioner_name:
        parts.append(f"You'll be seeing {practitioner_name}.")
    
    if price:
        parts.append(f"The cost is {format_price_gbp(price)}.")
    
    # Add cancellation policy
    from app.clinic_config import get_cancellation_policy
    policy = get_cancellation_policy()
    if policy:
        parts.append(policy)
    
    return " ".join(parts)
