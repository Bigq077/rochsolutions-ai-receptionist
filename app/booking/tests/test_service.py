"""Unit tests for BookingService."""

import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from app.booking.booking.service import BookingService
from app.booking.booking.models import Booking, BookingRequest, Slot
from app.booking.booking.exceptions import SlotUnavailable


@pytest.fixture
def mock_provider():
    """Mock booking provider."""
    provider = AsyncMock()
    return provider


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.setex = AsyncMock()
    redis_mock.delete = AsyncMock()
    redis_mock.keys = AsyncMock(return_value=[])
    return redis_mock


@pytest.fixture
def booking_service(mock_provider, mock_redis):
    """Create BookingService with mocks."""
    return BookingService(
        provider=mock_provider,
        redis_client=mock_redis,
        clinic_id="theorem",
    )


@pytest.mark.asyncio
async def test_create_booking_success(booking_service, mock_provider, mock_redis):
    """Test successful booking creation."""
    london_tz = ZoneInfo("Europe/London")
    
    request = BookingRequest(
        appointment_type_id="physio_session",
        slot_start=datetime(2024, 3, 15, 14, 0, tzinfo=london_tz),
        location_id="alcester",
        patient_first_name="Jane",
        patient_last_name="Smith",
        patient_phone="+447870166861",
        call_sid="CA123",
        session_id="sess_456",
    )
    
    expected_booking = Booking(
        id="acuity_999",
        provider_booking_id="999",
        appointment_type_id="physio_session",
        appointment_type_name="Physiotherapy Session",
        location_id="alcester",
        start_time=request.slot_start,
        end_time=datetime(2024, 3, 15, 14, 50, tzinfo=london_tz),
        patient_first_name="Jane",
        patient_last_name="Smith",
        patient_phone="+447870166861",
        provider="acuity",
        created_at=datetime.now(ZoneInfo("UTC")),
        status="confirmed",
    )
    
    mock_provider.create_booking.return_value = expected_booking
    
    # Mock Redis lock acquisition
    mock_redis.set.return_value = True
    
    booking = await booking_service.create_booking(request)
    
    assert booking.id == "acuity_999"
    assert booking.patient_first_name == "Jane"
    mock_provider.create_booking.assert_called_once()


@pytest.mark.asyncio
async def test_create_booking_idempotency(booking_service, mock_provider, mock_redis):
    """Test idempotent booking creation."""
    london_tz = ZoneInfo("Europe/London")
    
    request = BookingRequest(
        appointment_type_id="physio_session",
        slot_start=datetime(2024, 3, 15, 14, 0, tzinfo=london_tz),
        location_id="alcester",
        patient_first_name="Jane",
        patient_last_name="Smith",
        patient_phone="+447870166861",
        call_sid="CA123",
        session_id="sess_456",
    )
    
    # Mock existing booking in cache
    existing_booking = {
        "id": "acuity_999",
        "provider_booking_id": "999",
        "appointment_type_id": "physio_session",
        "appointment_type_name": "Physiotherapy Session",
        "location_id": "alcester",
        "start_time": request.slot_start.isoformat(),
        "end_time": datetime(2024, 3, 15, 14, 50, tzinfo=london_tz).isoformat(),
        "patient_first_name": "Jane",
        "patient_last_name": "Smith",
        "patient_phone": "+447870166861",
        "provider": "acuity",
        "created_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "status": "confirmed",
    }
    
    import json
    mock_redis.get.return_value = json.dumps(existing_booking)
    
    booking = await booking_service.create_booking(request)
    
    # Should return cached booking without calling provider
    assert booking.id == "acuity_999"
    mock_provider.create_booking.assert_not_called()
