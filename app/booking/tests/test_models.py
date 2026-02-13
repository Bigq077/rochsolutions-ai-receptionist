"""Unit tests for booking models."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.booking.models import Slot, Booking, BookingRequest, AppointmentType


def test_slot_model():
    """Test Slot model creation and serialization."""
    london_tz = ZoneInfo("Europe/London")
    start = datetime(2024, 3, 15, 14, 0, tzinfo=london_tz)
    end = datetime(2024, 3, 15, 14, 50, tzinfo=london_tz)
    
    slot = Slot(
        start_time=start,
        end_time=end,
        appointment_type_id="physio_session",
    )
    
    assert slot.start_time == start
    assert slot.end_time == end
    assert slot.appointment_type_id == "physio_session"
    
    # Test serialization
    data = slot.dict()
    assert "start_time" in data
    assert "end_time" in data


def test_booking_request_validation():
    """Test BookingRequest validation."""
    london_tz = ZoneInfo("Europe/London")
    
    request = BookingRequest(
        appointment_type_id="physio_session",
        slot_start=datetime(2024, 3, 15, 14, 0, tzinfo=london_tz),
        location_id="alcester",
        patient_first_name="John",
        patient_last_name="Doe",
        patient_phone="+447870166861",
        patient_email="john@example.com",
        call_sid="CA123",
        session_id="sess_456",
    )
    
    assert request.patient_first_name == "John"
    assert request.patient_email == "john@example.com"
    assert request.location_id == "alcester"


def test_appointment_type_model():
    """Test AppointmentType model."""
    apt_type = AppointmentType(
        id="physio_session",
        name="Physiotherapy Session",
        duration_minutes=50,
        provider_id="101",
        price_gbp=75.00,
    )
    
    assert apt_type.name == "Physiotherapy Session"
    assert apt_type.duration_minutes == 50
    assert apt_type.price_gbp == 75.00
