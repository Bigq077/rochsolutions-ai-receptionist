"""Provider contract tests using respx."""

import pytest
import respx
import httpx
from datetime import datetime, date
from zoneinfo import ZoneInfo

from app.booking.providers.acuity import AcuityAdapter
from app.booking.models import BookingRequest
from app.booking.exceptions import (
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
)


@pytest.fixture
def acuity_adapter():
    """Create Acuity adapter for testing."""
    return AcuityAdapter(
        user_id="test_user",
        api_key="test_key",
        clinic_id="theorem",
    )


@pytest.mark.asyncio
@respx.mock
async def test_acuity_get_appointment_types(acuity_adapter):
    """Test fetching appointment types from Acuity."""
    mock_response = [
        {
            "id": 123,
            "name": "Physiotherapy Session",
            "duration": 50,
            "description": "50-minute physio session",
        }
    ]
    
    respx.get("https://acuityscheduling.com/api/v1/appointment-types").mock(
        return_value=httpx.Response(200, json=mock_response)
    )
    
    types = await acuity_adapter.get_appointment_types()
    
    assert len(types) == 1
    assert types[0].name == "Physiotherapy Session"
    assert types[0].duration_minutes == 50


@pytest.mark.asyncio
@respx.mock
async def test_acuity_auth_error(acuity_adapter):
    """Test handling of authentication errors."""
    respx.get("https://acuityscheduling.com/api/v1/appointment-types").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    
    with pytest.raises(ProviderAuthError) as exc_info:
        await acuity_adapter.get_appointment_types()
    
    assert exc_info.value.provider == "acuity"


@pytest.mark.asyncio
@respx.mock
async def test_acuity_rate_limit(acuity_adapter):
    """Test handling of rate limits."""
    respx.get("https://acuityscheduling.com/api/v1/appointment-types").mock(
        return_value=httpx.Response(
            429,
            json={"message": "Rate limit exceeded"},
            headers={"Retry-After": "60"},
        )
    )
    
    with pytest.raises(ProviderRateLimited) as exc_info:
        await acuity_adapter.get_appointment_types()
    
    assert exc_info.value.retry_after == 60


@pytest.mark.asyncio
@respx.mock
async def test_acuity_create_booking(acuity_adapter):
    """Test creating a booking in Acuity."""
    london_tz = ZoneInfo("Europe/London")
    
    request = BookingRequest(
        appointment_type_id="acuity_123",
        slot_start=datetime(2024, 3, 15, 14, 0, tzinfo=london_tz),
        location_id="alcester",
        patient_first_name="John",
        patient_last_name="Doe",
        patient_phone="+447870166861",
        patient_email="john@example.com",
        call_sid="CA123",
        session_id="sess_456",
    )
    
    mock_response = {
        "id": 999,
        "datetime": "2024-03-15T14:00:00+00:00",
        "endTime": "2024-03-15T14:50:00+00:00",
        "appointmentType": "Physiotherapy Session",
        "calendar": "Mark",
    }
    
    respx.post("https://acuityscheduling.com/api/v1/appointments").mock(
        return_value=httpx.Response(200, json=mock_response)
    )
    
    booking = await acuity_adapter.create_booking(request)
    
    assert booking.provider_booking_id == "999"
    assert booking.patient_first_name == "John"
    assert booking.appointment_type_name == "Physiotherapy Session"
