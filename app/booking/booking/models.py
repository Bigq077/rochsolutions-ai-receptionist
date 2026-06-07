"""
Pydantic models for booking subsystem.
"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, EmailStr


class Location(BaseModel):
    """Represents a clinic location."""
    
    id: str
    name: str
    address: str
    acuity_calendar_id: Optional[str] = None  # Maps to Acuity calendar if using calendar-based routing


class AppointmentType(BaseModel):
    """Represents an appointment type (e.g., 'Physiotherapy Assessment')."""
    
    id: str
    name: str
    duration_minutes: int
    description: Optional[str] = None
    price_gbp: float
    provider_id: str  # Original ID from provider system
    requires_manual_approval: bool = False  # e.g., insurance referrals
    has_surcharge: bool = False  # e.g., Shockwave/Laser
    surcharge_amount_gbp: Optional[float] = None
    category: Optional[str] = None  # e.g., "physiotherapy", "psychotherapy", "rehab"


class Practitioner(BaseModel):
    """Represents a healthcare practitioner."""
    
    id: str
    name: str
    role: Optional[str] = None  # e.g., "Physiotherapist", "Rehab Instructor"
    available_days: Optional[list[str]] = None  # e.g., ["monday", "tuesday", "wednesday"]
    provider_id: Optional[str] = None  # Calendar/resource ID in provider system
    location_ids: Optional[list[str]] = None  # Which locations they work at


class Slot(BaseModel):
    """Represents an available appointment slot."""
    
    start_time: datetime  # Always tz-aware Europe/London
    end_time: datetime
    appointment_type_id: str
    practitioner_id: Optional[str] = None
    location_id: Optional[str] = None
    provider_slot_id: Optional[str] = None  # Provider-specific identifier if available
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class InsuranceInfo(BaseModel):
    """Insurance information for manual approval."""
    
    provider_name: str
    policy_number: Optional[str] = None
    requires_approval: bool = True
    notes: Optional[str] = None


class BookingRequest(BaseModel):
    """Request to create a booking."""
    
    appointment_type_id: str
    slot_start: datetime  # tz-aware Europe/London
    location_id: str
    patient_first_name: str
    patient_last_name: str
    patient_phone: str
    patient_email: Optional[EmailStr] = None
    notes: Optional[str] = None
    practitioner_id: Optional[str] = None
    insurance_info: Optional[InsuranceInfo] = None
    
    # Session context for idempotency
    call_sid: str
    session_id: str


class Booking(BaseModel):
    """Confirmed appointment booking."""
    
    id: str  # Our internal booking ID
    provider_booking_id: str  # Provider's booking reference
    appointment_type_id: str
    appointment_type_name: str
    location_id: str
    location_name: Optional[str] = None
    start_time: datetime  # tz-aware Europe/London
    end_time: datetime
    patient_first_name: str
    patient_last_name: str
    patient_phone: str
    patient_email: Optional[str] = None
    practitioner_id: Optional[str] = None
    practitioner_name: Optional[str] = None
    status: Literal["confirmed", "pending", "cancelled"] = "confirmed"
    provider: str  # "acuity" or "google_calendar"
    created_at: datetime  # UTC
    notes: Optional[str] = None
    insurance_info: Optional[InsuranceInfo] = None
    requires_manual_approval: bool = False
    price_gbp: Optional[float] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
